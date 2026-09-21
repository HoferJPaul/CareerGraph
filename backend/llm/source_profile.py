"""Source CV parsing behind one interface.

    LLM_PROVIDER=groq  ->  GroqSourceCvParser   one structured-output call through the EXISTING
                                                GroqStructuredClient (same timeouts, bounded retries,
                                                error taxonomy and content-free logging as every other
                                                Groq operation -- there is no second Groq client)
    LLM_PROVIDER=dev   ->  DevSourceCvParser    an explicit, heuristic development/test parser

Both return the same draft type. Neither is trusted: source_cv/grounding.py validates the draft against
the document text and converts it into the real SourceProfile. A failed Groq call is an error, never a
silent switch to the heuristic parser.
"""
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from llm.config import LLMSettings
from llm.groq_client import StructuredClient
from llm.schemas import parse_draft
from llm.source_prompts import SOURCE_CV_PARSER_SYSTEM_PROMPT
from llm.source_schemas import (
    SOURCE_PROFILE_SCHEMA,
    DraftCertification,
    DraftContact,
    DraftEducation,
    DraftEmployment,
    DraftFact,
    DraftLanguage,
    DraftProject,
    SourceProfileDraft,
)
from llm_provider import TokenUsage

PARSER_MAX_COMPLETION_TOKENS = 16384  # gpt-oss counts reasoning tokens against this budget
PARSER_TEMPERATURE = 0.0

MODE_LLM = "llm_structured"
MODE_DEV = "heuristic_dev"


@dataclass(frozen=True)
class ParseOutput:
    draft: SourceProfileDraft
    provider: str
    model: Optional[str]
    mode: str
    usage: Optional[TokenUsage] = None
    attempts: int = 1


class SourceCvParser(ABC):
    @abstractmethod
    def parse(self, text: str) -> ParseOutput:
        """Text extracted from the uploaded CV -> an UNTRUSTED draft profile."""


class GroqSourceCvParser(SourceCvParser):
    def __init__(self, settings: LLMSettings, client: StructuredClient):
        self._settings = settings
        self._client = client

    def parse(self, text: str) -> ParseOutput:
        completion = self._client.complete(
            operation="parse_source_cv",
            model=self._settings.extraction_model,
            system_prompt=SOURCE_CV_PARSER_SYSTEM_PROMPT,
            user_content=f"<cv_document>\n{text}\n</cv_document>",
            schema_name="source_profile",
            schema=SOURCE_PROFILE_SCHEMA,
            max_completion_tokens=PARSER_MAX_COMPLETION_TOKENS,
            temperature=PARSER_TEMPERATURE,
        )
        return ParseOutput(
            draft=parse_draft(SourceProfileDraft, completion.data),
            provider="groq",
            model=completion.model,
            mode=MODE_LLM,
            usage=completion.usage,
            attempts=completion.attempts,
        )


# ---- development parser ---------------------------------------------------------------------------------
#
# A plain rule-based reader for well-formed CVs with conventional headings. It exists so the feature can
# be developed and tested offline (LLM_PROVIDER=dev) and is labelled as such everywhere. It understands
# far less than a language model: unusual layouts will simply parse poorly, and it never guesses -- every
# value it returns is a substring of the document, so the same grounding checks apply to it.

_SECTION_WORDS = {
    "summary": {"summary", "profile", "about me", "about", "profil", "kurzprofil"},
    "experience": {"experience", "work experience", "professional experience", "employment", "employment history",
                   "work history", "berufserfahrung"},
    "education": {"education", "academic background", "ausbildung", "bildung"},
    "projects": {"projects", "personal projects", "projekte"},
    "certifications": {"certifications", "certificates", "licenses", "zertifikate", "zertifizierungen"},
    "languages": {"languages", "sprachen"},
    "skills": {"skills", "technical skills", "kenntnisse"},
}
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?\d[\d ().\-]{7,}\d")
_URL = re.compile(r"(?:https?://)?(?:www\.)?[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}(?:/[^\s|,;]*)?")
_DATE = r"(?:[A-Za-zäöüÄÖÜ]{3,9}\.?\s+)?\d{4}|\d{1,2}[/.]\d{4}"
_RANGE = re.compile(rf"(?P<start>{_DATE})\s*(?:-|–|—|to|bis)\s*(?P<end>{_DATE}|Present|Current|Heute|Now)", re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[•\-\*–·▪●]|\d+[.)])\s+")
_SEPARATORS = [" — ", " – ", " | ", " - ", " at "]
LINKS_MARKER = "Links found in the document:"


def _heading_of(line: str) -> Optional[str]:
    word = line.strip().rstrip(":").lower()
    return next((name for name, words in _SECTION_WORDS.items() if word in words), None)


def _split_header(header: str) -> tuple[str, str, str]:
    """'Backend Engineer — Acme Analytics, Remote' -> (title, employer, location)."""
    for separator in _SEPARATORS:
        if separator in header:
            first, _, rest = header.partition(separator)
            employer, _, location = rest.partition(", ")
            return first.strip(), employer.strip(), location.strip()
    return header.strip(), "", ""


class DevSourceCvParser(SourceCvParser):
    def parse(self, text: str) -> ParseOutput:
        body, _, links_block = text.partition(LINKS_MARKER)  # links the extractor appended (PDF annotations)
        lines = [line.rstrip() for line in body.splitlines()]
        sections: dict[str, list[str]] = {"header": []}
        current = "header"
        for line in lines:
            heading = _heading_of(line) if line.strip() and not _BULLET.match(line) else None
            if heading:
                current = heading
                sections.setdefault(current, [])
            else:
                sections.setdefault(current, []).append(line)

        draft = SourceProfileDraft(
            contact=self._contact(sections["header"] + links_block.splitlines()),
            summary=self._summary(sections.get("summary", [])),
            employment=self._entries(sections.get("experience", []), DraftEmployment),
            education=self._education(sections.get("education", [])),
            projects=self._entries(sections.get("projects", []), DraftProject),
            certifications=self._certifications(sections.get("certifications", [])),
            languages=self._languages(sections.get("languages", [])),
            otherSections=[],
            warnings=[],
        )
        return ParseOutput(draft=draft, provider="dev", model=None, mode=MODE_DEV)

    # -- pieces -------------------------------------------------------------------------------------------

    @staticmethod
    def _contact(header: list[str]) -> DraftContact:
        block = "\n".join(header)
        emails = _EMAIL.findall(block)
        phones = [m.group(0).strip() for m in _PHONE.finditer(block) if not _RANGE.search(m.group(0))]
        without_emails = _EMAIL.sub(" ", block)
        urls = [u.rstrip(".,;") for u in _URL.findall(without_emails) if "/" in u or u.lower().startswith("http")]
        linkedin = next((u for u in urls if "linkedin.com" in u.lower()), "")
        github = next((u for u in urls if "github.com" in u.lower()), "")
        rest = [u for u in urls if u not in (linkedin, github)]
        first = next((ln.strip() for ln in header if ln.strip()), "")
        name = first if re.fullmatch(r"[^\d@|,]{3,60}", first) and 2 <= len(first.split()) <= 4 else ""
        city = country = ""
        for segment in re.split(r"[|\n]", block):
            match = re.fullmatch(r"\s*([A-ZÄÖÜ][\w .'-]+),\s*([A-ZÄÖÜ][\w .'-]+)\s*", segment)
            if match and "@" not in segment and not any(ch.isdigit() for ch in segment):
                city, country = match.group(1).strip(), match.group(2).strip()
                break
        return DraftContact(
            fullName=name, email=emails[0] if emails else "", telephone=phones[0] if phones else "",
            city=city, country=country, linkedinUrl=linkedin, githubUrl=github,
            portfolioUrl=rest[0] if rest else "", otherUrls=rest[1:],
        )

    @staticmethod
    def _summary(lines: list[str]) -> DraftFact:
        body = [ln.strip() for ln in lines if ln.strip()]
        text = " ".join(body)
        return DraftFact(text=text, excerpt="\n".join(body)) if text else DraftFact(text="", excerpt="")

    @staticmethod
    def _entry_blocks(lines: list[str]) -> list[tuple[str, str, list[str]]]:
        """(header line, date range text or "", body lines) per entry. An entry's dates are either on
        its header line or alone on the line after it."""
        blocks: list[list] = []
        pending: Optional[str] = None
        content = [ln.strip() for ln in lines if ln.strip()]
        for position, line in enumerate(content):
            if _BULLET.match(line):
                if blocks:
                    blocks[-1][2].append(_BULLET.sub("", line).strip())
                continue
            dates_only = _RANGE.fullmatch(line)
            if dates_only:
                if pending is not None:
                    blocks.append([pending, dates_only.group(0), []])
                    pending = None
                elif blocks and not blocks[-1][1]:
                    blocks[-1][1] = dates_only.group(0)
                continue
            inline = _RANGE.search(line)
            if inline:
                blocks.append([line[: inline.start()].strip(" -–—|,()"), inline.group(0), []])
                continue
            following = content[position + 1] if position + 1 < len(content) else ""
            if _RANGE.fullmatch(following):
                pending = line
            elif blocks:
                blocks[-1][2].append(line)
            else:
                blocks.append([line, "", []])
        return [(header, dates, body) for header, dates, body in blocks]

    def _entries(self, lines: list[str], kind):
        entries = []
        for header, dates, body in self._entry_blocks(lines):
            start = end = ""
            if dates and (match := _RANGE.fullmatch(dates)):
                start, end = match.group("start"), match.group("end")
            current = end.lower() in ("present", "current", "heute", "now")
            facts = [DraftFact(text=b, excerpt=b) for b in body]
            if kind is DraftProject:
                entries.append(DraftProject(
                    name=header, role="", url="", startText=start, endText=end, current=current,
                    excerpt=header, facts=facts,
                ))
            else:
                title, employer, location = _split_header(header)
                entries.append(DraftEmployment(
                    employer=employer, title=title, location=location, startText=start, endText=end,
                    current=current, excerpt=header, facts=facts,
                ))
        return entries

    def _education(self, lines: list[str]) -> list[DraftEducation]:
        entries = []
        for header, dates, body in self._entry_blocks(lines):
            start = end = ""
            if dates and (match := _RANGE.fullmatch(dates)):
                start, end = match.group("start"), match.group("end")
            institution, qualification, _ = _split_header(header)
            entries.append(DraftEducation(
                institution=institution, qualification=qualification, fieldOfStudy="", location="",
                startText=start, endText=end, current=end.lower() in ("present", "current", "heute", "now"),
                excerpt=header, details=[DraftFact(text=b, excerpt=b) for b in body],
            ))
        return entries

    @staticmethod
    def _certifications(lines: list[str]) -> list[DraftCertification]:
        out = []
        for raw in lines:
            line = _BULLET.sub("", raw).strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",")]
            date = parts[-1] if len(parts) > 1 and re.fullmatch(r"\d{4}", parts[-1]) else ""
            core = parts[:-1] if date else parts
            out.append(DraftCertification(
                name=core[0], issuer=core[1] if len(core) > 1 else "", dateText=date, excerpt=line,
            ))
        return out

    @staticmethod
    def _languages(lines: list[str]) -> list[DraftLanguage]:
        out = []
        for raw in lines:
            for part in re.split(r"[,;]", _BULLET.sub("", raw)):
                part = part.strip()
                if not part:
                    continue
                match = re.fullmatch(r"([^()\-–—:]+?)\s*(?:[(\-–—:]\s*([^)]+)\)?)?", part)
                language = match.group(1).strip() if match else part
                proficiency = (match.group(2) or "").strip() if match else ""
                out.append(DraftLanguage(language=language, proficiency=proficiency, excerpt=part))
        return out
