"""The parse boundary: an UNTRUSTED model draft -> a validated SourceProfile.

Nothing the model returns is stored on its say-so. Every value that must be verbatim (names, employers,
titles, institutions, contact details, dates as written) and every excerpt is looked up in the text that
was actually extracted from the uploaded file. What does not check out is handled by how much it matters:

  REJECT the whole parse (nothing is stored; the previous profile stays)
      an employer, title or institution that is not in the document; an entry whose excerpt is not in the
      document; too many entries. These would put invented identity facts into someone's CV.
  DROP the value or statement, and say so in a visible warning
      a contact value, location, qualification, date, project, certification, language or statement the
      document does not contain -- or a statement whose numbers/wording its own excerpt does not support.

The comparison ignores case, whitespace and punctuation (so line wraps, hyphenation and odd dashes in a
PDF do not cause false alarms) but nothing else: it is a containment check, not fuzzy matching.

Normalized dates are derived here by dates.parse_date_text -- the model never provides them.
"""
import re
import unicodedata
from collections import Counter
from datetime import datetime
from typing import Optional

from llm.source_schemas import DraftFact, SourceProfileDraft
from source_cv.errors import NoCvContentError, ProfileRejectedError
from source_cv.schema import (
    MAX_CERTIFICATIONS,
    MAX_EDUCATION,
    MAX_EMPLOYMENT,
    MAX_EXCERPT_CHARS,
    MAX_FACT_CHARS,
    MAX_FACTS_PER_ENTRY,
    MAX_FIELD_CHARS,
    MAX_LANGUAGES,
    MAX_OTHER_SECTIONS,
    MAX_OTHER_URLS,
    MAX_PROJECTS,
    CertificationEntry,
    ContactInfo,
    EducationEntry,
    EmploymentEntry,
    IdAllocator,
    LabeledUrl,
    LanguageEntry,
    OtherSection,
    ParseWarning,
    ParserInfo,
    ProfessionalSummary,
    ProjectEntry,
    SourceFact,
    SourceProfile,
    UploadInfo,
    clean_text,
    derive,
    safe_url,
    valid_email,
)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_WORD = re.compile(r"[^\W\d_]{4,}", re.UNICODE)
_SCHEME = re.compile(r"^https?://(?:www\.)?", re.IGNORECASE)
MIN_WORD_COVERAGE = 0.75
MAX_WARNINGS = 25


def squash(text: str) -> str:
    """Case-, whitespace- and punctuation-insensitive form used for containment checks."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _number_set(text: str) -> set[str]:
    return {re.sub(r"[.,]", "", n) for n in _NUMBER.findall(text)}


def _norm_words(text: str) -> set[str]:
    return {w.casefold() for w in _WORD.findall(text)}


class _Builder:
    def __init__(self, source_text: str):
        self.text = source_text
        self.haystack = squash(source_text)
        self.digits = _digits(source_text)
        self.allocator = IdAllocator()
        self.warnings: list[ParseWarning] = []
        self.violations: Counter = Counter()

    # -- checks -------------------------------------------------------------------------------------

    def grounded(self, value: Optional[str]) -> bool:
        if not value:
            return False
        squashed = squash(value)
        return bool(squashed) and squashed in self.haystack

    def url_grounded(self, url: str) -> bool:
        bare = _SCHEME.sub("", url.strip()).rstrip("/")
        return self.grounded(bare)

    def phone_grounded(self, phone: str) -> bool:
        digits = _digits(phone)
        return len(digits) >= 6 and digits in self.digits

    def warn(self, code: str, message: str, entry_id: Optional[str] = None) -> None:
        if len(self.warnings) < MAX_WARNINGS:
            self.warnings.append(ParseWarning(code=code, message=message, entryId=entry_id, origin="parser"))

    def reject(self, code: str) -> None:
        self.violations[code] += 1

    # -- optional descriptive value: kept only if it is in the document ------------------------------

    def value(self, raw: str, max_chars: int, label: str, entry_id: Optional[str] = None) -> Optional[str]:
        text = clean_text(raw, max_chars)
        if not text:
            return None
        if not self.grounded(text):
            self.warn("value_not_in_document", f"The {label} the model returned was not found in the document and was left blank.", entry_id)
            return None
        return text

    def dates(self, entry_id: str, start: str, end: str, current: bool, label: str) -> tuple[Optional[str], Optional[str], bool]:
        start_text = self.value(start, 60, f"start date of a {label}", entry_id)
        end_text = self.value(end, 60, f"end date of a {label}", entry_id)
        return start_text, end_text, bool(current)

    # -- statements ---------------------------------------------------------------------------------

    @staticmethod
    def supported_by_excerpt(text: str, excerpt: str) -> bool:
        """The statement may only restate its excerpt: no number that the excerpt lacks, and (almost)
        every substantive word must come from it."""
        if not _number_set(text) <= _number_set(excerpt):
            return False
        words = _norm_words(text)
        if not words:
            return True
        return len(words & _norm_words(excerpt)) / len(words) >= MIN_WORD_COVERAGE

    def facts(self, items: list[DraftFact], owner_id: str, label: str) -> list[SourceFact]:
        out: list[SourceFact] = []
        seen: set[str] = set()
        if len(items) > MAX_FACTS_PER_ENTRY:
            self.warn("statements_truncated", f"A {label} had more than {MAX_FACTS_PER_ENTRY} statements; the rest were ignored.", owner_id)
        for item in items[:MAX_FACTS_PER_ENTRY]:
            text = clean_text(item.text, MAX_FACT_CHARS)
            if not text:
                continue
            excerpt = clean_text(item.excerpt, MAX_EXCERPT_CHARS)
            if not excerpt or not self.grounded(excerpt):
                self.warn("statement_unverified", f"A statement in a {label} was not found in the document and was left out.", owner_id)
                continue
            if not self.supported_by_excerpt(text, excerpt):
                self.warn("statement_unsupported", f"A statement in a {label} said more than the document does and was left out.", owner_id)
                continue
            key = squash(text)
            if key in seen:
                continue
            seen.add(key)
            out.append(SourceFact(id=self.allocator.allocate("f", owner_id, text), text=text, excerpt=excerpt))
        return out

    def entry_excerpt(self, raw: str) -> Optional[str]:
        excerpt = clean_text(raw, MAX_EXCERPT_CHARS)
        if not excerpt or not self.grounded(excerpt):
            self.reject("entry_excerpt_not_in_document")
            return None
        return excerpt

    def identity(self, raw: str, code: str) -> Optional[str]:
        text = clean_text(raw, MAX_FIELD_CHARS)
        if not text:
            return None
        if not self.grounded(text):
            self.reject(code)
            return None
        return text


# ---- contact ------------------------------------------------------------------------------------------


def _contact(draft, b: _Builder) -> ContactInfo:
    c = draft.contact
    email = clean_text(c.email, MAX_FIELD_CHARS)
    if email and (valid_email(email) is None or not b.grounded(email)):
        b.warn("value_not_in_document", "The e-mail address the model returned is not valid or not in the document and was left blank.")
        email = None
    phone = clean_text(c.telephone, 60)
    if phone and not b.phone_grounded(phone):
        b.warn("value_not_in_document", "The telephone number the model returned was not found in the document and was left blank.")
        phone = None

    # Links are classified by host, not by which field the model chose to put them in.
    candidates = [c.linkedinUrl, c.githubUrl, c.portfolioUrl, *c.otherUrls]
    linkedin = github = portfolio = None
    others: list[str] = []
    for raw in candidates:
        if not raw or not raw.strip():
            continue
        url = safe_url(raw)
        if url is None or not b.url_grounded(url):
            b.warn("value_not_in_document", "A link the model returned is not a web address in the document and was left out.")
            continue
        host = re.sub(r"^https?://(?:www\.)?", "", url, flags=re.IGNORECASE).split("/")[0].lower()
        if host.endswith("linkedin.com") and linkedin is None:
            linkedin = url
        elif host.endswith("github.com") and github is None:
            github = url
        elif portfolio is None and raw.strip() == (c.portfolioUrl or "").strip():
            portfolio = url
        elif url not in others and url not in (linkedin, github, portfolio):
            others.append(url)
    if portfolio is None and others:
        portfolio = others.pop(0)
    return ContactInfo(
        fullName=b.value(c.fullName, MAX_FIELD_CHARS, "name"),
        email=email,
        telephone=phone,
        city=b.value(c.city, MAX_FIELD_CHARS, "city"),
        country=b.value(c.country, MAX_FIELD_CHARS, "country"),
        linkedinUrl=linkedin,
        githubUrl=github,
        portfolioUrl=portfolio,
        otherUrls=[LabeledUrl(url=u) for u in others[:MAX_OTHER_URLS]],
    )


# ---- the profile --------------------------------------------------------------------------------------


def build_profile(
    draft: SourceProfileDraft,
    source_text: str,
    *,
    upload: UploadInfo,
    parser: ParserInfo,
    revision: int,
    now: datetime,
    uploaded_at: Optional[datetime] = None,
) -> SourceProfile:
    b = _Builder(source_text)
    contact = _contact(draft, b)

    summary = None
    s_text, s_excerpt = clean_text(draft.summary.text, MAX_FACT_CHARS), clean_text(draft.summary.excerpt, MAX_EXCERPT_CHARS)
    if s_text and s_excerpt and b.grounded(s_excerpt) and b.supported_by_excerpt(s_text, s_excerpt):
        summary = ProfessionalSummary(id=b.allocator.allocate("sum", s_text), text=s_text, excerpt=s_excerpt)
    elif s_text:
        b.warn("statement_unverified", "The profile summary the model returned was not found in the document and was left out.")

    if len(draft.employment) > MAX_EMPLOYMENT:
        b.reject("too_many_positions")
    employment: list[EmploymentEntry] = []
    seen_positions: set[str] = set()
    for index, d in enumerate(draft.employment[:MAX_EMPLOYMENT]):
        employer = b.identity(d.employer, "employer_not_in_document")
        title = b.identity(d.title, "title_not_in_document")
        excerpt = b.entry_excerpt(d.excerpt)
        if not (employer or title):
            if not (clean_text(d.employer, 5) or clean_text(d.title, 5)):
                b.warn("entry_without_identity", "A position without an employer or a title was ignored.")
            continue
        key = "|".join(squash(x or "") for x in (employer, title, d.startText, d.endText))
        if key in seen_positions:
            b.warn("duplicate_position_removed", "The same position was listed twice and was kept once.")
            continue
        seen_positions.add(key)
        entry_id = b.allocator.allocate("emp", employer or "", title or "", d.startText, str(index))
        start_text, end_text, current = b.dates(entry_id, d.startText, d.endText, d.current, "position")
        employment.append(
            EmploymentEntry(
                id=entry_id, employer=employer, title=title,
                location=b.value(d.location, MAX_FIELD_CHARS, "location of a position", entry_id),
                startText=start_text, endText=end_text, current=current,
                facts=b.facts(d.facts, entry_id, "position"), excerpt=excerpt,
            )
        )

    if len(draft.education) > MAX_EDUCATION:
        b.reject("too_many_education_entries")
    education: list[EducationEntry] = []
    for index, d in enumerate(draft.education[:MAX_EDUCATION]):
        institution = b.identity(d.institution, "institution_not_in_document")
        excerpt = b.entry_excerpt(d.excerpt)
        if not institution:
            if not clean_text(d.institution, 5):
                b.warn("entry_without_identity", "An education entry without an institution was ignored.")
            continue
        entry_id = b.allocator.allocate("edu", institution, d.qualification, d.startText, str(index))
        start_text, end_text, current = b.dates(entry_id, d.startText, d.endText, d.current, "education entry")
        education.append(
            EducationEntry(
                id=entry_id, institution=institution,
                qualification=b.value(d.qualification, MAX_FIELD_CHARS, "qualification", entry_id),
                field=b.value(d.fieldOfStudy, MAX_FIELD_CHARS, "field of study", entry_id),
                location=b.value(d.location, MAX_FIELD_CHARS, "location of an education entry", entry_id),
                startText=start_text, endText=end_text, current=current,
                facts=b.facts(d.details, entry_id, "education entry"), excerpt=excerpt,
            )
        )

    projects: list[ProjectEntry] = []
    for index, d in enumerate(draft.projects[:MAX_PROJECTS]):
        name = clean_text(d.name, MAX_FIELD_CHARS)
        excerpt = clean_text(d.excerpt, MAX_EXCERPT_CHARS)
        if not name or not b.grounded(name) or not excerpt or not b.grounded(excerpt):
            b.warn("entry_unverified", "A project the model returned was not found in the document and was left out.")
            continue
        entry_id = b.allocator.allocate("prj", name, d.startText, str(index))
        start_text, end_text, current = b.dates(entry_id, d.startText, d.endText, d.current, "project")
        url = safe_url(d.url) if d.url and d.url.strip() else None
        projects.append(
            ProjectEntry(
                id=entry_id, name=name, role=b.value(d.role, MAX_FIELD_CHARS, "role in a project", entry_id),
                url=url if url and b.url_grounded(url) else None,
                startText=start_text, endText=end_text, current=current,
                facts=b.facts(d.facts, entry_id, "project"), excerpt=excerpt,
            )
        )

    certifications: list[CertificationEntry] = []
    for index, d in enumerate(draft.certifications[:MAX_CERTIFICATIONS]):
        name = clean_text(d.name, MAX_FIELD_CHARS)
        excerpt = clean_text(d.excerpt, MAX_EXCERPT_CHARS)
        if not name or not b.grounded(name) or not excerpt or not b.grounded(excerpt):
            b.warn("entry_unverified", "A certification the model returned was not found in the document and was left out.")
            continue
        entry_id = b.allocator.allocate("crt", name, str(index))
        certifications.append(
            CertificationEntry(
                id=entry_id, name=name, issuer=b.value(d.issuer, MAX_FIELD_CHARS, "issuer", entry_id),
                dateText=b.value(d.dateText, 60, "date of a certification", entry_id), excerpt=excerpt,
            )
        )

    languages: list[LanguageEntry] = []
    seen_languages: set[str] = set()
    for index, d in enumerate(draft.languages[:MAX_LANGUAGES]):
        language = clean_text(d.language, MAX_FIELD_CHARS)
        excerpt = clean_text(d.excerpt, MAX_EXCERPT_CHARS)
        if not language or not b.grounded(language) or not excerpt or not b.grounded(excerpt):
            b.warn("entry_unverified", "A language the model returned was not found in the document and was left out.")
            continue
        if language.casefold() in seen_languages:
            continue
        seen_languages.add(language.casefold())
        entry_id = b.allocator.allocate("lng", language)
        languages.append(
            LanguageEntry(
                id=entry_id, language=language,
                proficiency=b.value(d.proficiency, 80, "language level", entry_id), excerpt=excerpt,
            )
        )

    sections: list[OtherSection] = []
    for index, d in enumerate(draft.otherSections[:MAX_OTHER_SECTIONS]):
        heading = clean_text(d.heading, MAX_FIELD_CHARS)
        if not heading or not b.grounded(heading):
            b.warn("entry_unverified", "A section the model returned was not found in the document and was left out.")
            continue
        section_id = b.allocator.allocate("oth", heading, str(index))
        items = b.facts(d.items, section_id, "section")
        if items:
            sections.append(OtherSection(id=section_id, heading=heading, items=items))

    for w in draft.warnings[:10]:
        message = clean_text(w.message, 300) or "The model flagged something in the document."
        b.warn(w.code, message)

    if b.violations:
        raise ProfileRejectedError(dict(b.violations))

    has_contact = any((contact.fullName, contact.email, contact.telephone))
    if not (has_contact or employment or education or projects or certifications or languages):
        raise NoCvContentError()

    profile = SourceProfile(
        revision=revision, upload=upload, parser=parser,
        uploadedAt=uploaded_at or now, parsedAt=now, updatedAt=now,
        contact=contact, summary=summary, employment=employment, education=education, projects=projects,
        certifications=certifications, languages=languages, otherSections=sections, warnings=b.warnings,
    )
    return derive(profile)
