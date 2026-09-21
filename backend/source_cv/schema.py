"""Strict, source-backed profile models for the persistent Source CV.

Rules baked into the shapes:
  * Nothing is required that a CV might not contain. An absent value is `None` / an empty list --
    never a placeholder such as "N/A" or "Unknown".
  * Every entity and every fact has a stable generated id, unique within the profile. Facts are the
    unit a generated CV may cite (`source:<fact id>`). Ids are content-derived at parse time (so
    re-parsing the same document yields the same ids) and never change when the user edits a value.
  * Dates keep the text as written (`startText`/`endText`) next to a server-derived normalized form
    (`start`/`end`, "YYYY" or "YYYY-MM"). The model never supplies the normalized form.
  * `excerpt`s are the exact source text a value came from. They are stored for provenance and are NOT
    returned by the review API (see `profile_view`).

Models forbid unknown fields (`extra="forbid"`), so a stored profile that does not match this schema is
rejected on load rather than half-trusted.
"""
import hashlib
import re
import secrets
from datetime import datetime, timezone
from typing import Iterable, Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from source_cv.dates import is_normalized, is_present_word, month_index, parse_date_text
from source_cv.errors import InvalidProfileEditError

SCHEMA_VERSION = 1

# ---- limits (applied to parsed profiles and to user edits alike) --------------------------------------
MAX_FIELD_CHARS = 300
MAX_URL_CHARS = 500
MAX_FACT_CHARS = 1200
MAX_EXCERPT_CHARS = 2000
MAX_EMPLOYMENT = 40
MAX_EDUCATION = 15
MAX_PROJECTS = 30
MAX_CERTIFICATIONS = 30
MAX_LANGUAGES = 20
MAX_OTHER_SECTIONS = 15
MAX_FACTS_PER_ENTRY = 30
MAX_OTHER_URLS = 6

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
# C0 controls except tab/newline/carriage return, DEL, and zero-width characters
_CONTROL_CHARS = "".join(chr(c) for c in [*range(0, 9), 11, 12, *range(14, 32), 127, 0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF])
_CONTROL_RE = re.compile("[" + re.escape(_CONTROL_CHARS) + "]")

WarningOrigin = Literal["parser", "derived"]
ConflictOrigin = Literal["document", "graph"]
ConflictKind = Literal["title", "dates", "location", "education", "duplicate", "date_order"]
Resolution = Literal["use_source", "use_graph"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def clean_text(value: Optional[str], max_chars: int) -> Optional[str]:
    """Trim, drop control/zero-width characters, collapse inner whitespace runs on ONE line. Empty -> None."""
    if value is None:
        return None
    text = _CONTROL_RE.sub("", value)
    text = " ".join(text.split())
    if not text:
        return None
    return text[:max_chars] if len(text) <= max_chars else None  # over-long values are rejected by callers


def safe_url(value: Optional[str]) -> Optional[str]:
    """http(s) URLs only (no javascript:, data:, file:, mailto: ...). A bare 'github.com/x' gets https://."""
    text = clean_text(value, MAX_URL_CHARS)
    if not text or " " in text:
        return None
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or "." not in parsed.netloc:
        return None
    return text


def valid_email(value: Optional[str]) -> Optional[str]:
    text = clean_text(value, MAX_FIELD_CHARS)
    return text if text and _EMAIL_RE.match(text) else None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---- ids ----------------------------------------------------------------------------------------------


class IdAllocator:
    """Unique ids within one profile. Content-derived by default (stable across re-parses); random for
    entities the user adds by hand."""

    def __init__(self, used: Iterable[str] = ()):
        self.used: set[str] = set(used)

    def allocate(self, prefix: str, *parts: str) -> str:
        digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
        return self._unique(prefix, digest)

    def random(self, prefix: str) -> str:
        return self._unique(prefix, secrets.token_hex(8))

    def _unique(self, prefix: str, digest: str) -> str:
        width = 8
        while True:
            candidate = f"{prefix}_{digest[:width]}"
            if candidate not in self.used:
                self.used.add(candidate)
                return candidate
            width += 2
            if width > len(digest):  # identical content twice: fall back to a fresh random suffix
                digest = digest + secrets.token_hex(4)


# ---- the profile --------------------------------------------------------------------------------------


class LabeledUrl(_Strict):
    label: Optional[str] = None
    url: str


class ContactInfo(_Strict):
    fullName: Optional[str] = None
    email: Optional[str] = None
    telephone: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    linkedinUrl: Optional[str] = None
    githubUrl: Optional[str] = None
    portfolioUrl: Optional[str] = None
    otherUrls: list[LabeledUrl] = Field(default_factory=list)


class SourceFact(_Strict):
    """One statement from the CV (a responsibility, achievement, detail). The unit of citation."""

    id: str
    text: str
    excerpt: Optional[str] = None  # exact source text; absent for facts the user typed in by hand
    userEdited: bool = False


class _Dated(_Strict):
    startText: Optional[str] = None
    endText: Optional[str] = None
    start: Optional[str] = None  # derived: "YYYY" | "YYYY-MM"
    end: Optional[str] = None
    current: bool = False

    @field_validator("start", "end")
    @classmethod
    def _normalized_only(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not is_normalized(value):
            raise ValueError("dates are normalized to YYYY or YYYY-MM")
        return value


class EmploymentEntry(_Dated):
    id: str
    employer: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    facts: list[SourceFact] = Field(default_factory=list)
    excerpt: Optional[str] = None

    @model_validator(mode="after")
    def _has_identity(self) -> "EmploymentEntry":
        if not (self.employer or self.title):
            raise ValueError("an employment entry needs an employer or a title")
        return self


class EducationEntry(_Dated):
    id: str
    institution: str
    qualification: Optional[str] = None
    field: Optional[str] = None
    location: Optional[str] = None
    facts: list[SourceFact] = Field(default_factory=list)
    excerpt: Optional[str] = None


class ProjectEntry(_Dated):
    id: str
    name: str
    role: Optional[str] = None
    url: Optional[str] = None
    facts: list[SourceFact] = Field(default_factory=list)
    excerpt: Optional[str] = None


class CertificationEntry(_Strict):
    id: str
    name: str
    issuer: Optional[str] = None
    dateText: Optional[str] = None
    date: Optional[str] = None
    excerpt: Optional[str] = None


class LanguageEntry(_Strict):
    id: str
    language: str
    proficiency: Optional[str] = None
    excerpt: Optional[str] = None


class OtherSection(_Strict):
    id: str
    heading: str
    items: list[SourceFact] = Field(default_factory=list)


class ParseWarning(_Strict):
    code: str
    message: str
    entryId: Optional[str] = None
    origin: WarningOrigin = "parser"


class Conflict(_Strict):
    """Two values that disagree. Neither is silently chosen: an unresolved conflict keeps the field out
    of the generated CV. `graph` conflicts can be resolved by the user (use_source / use_graph); a
    `document` conflict (the CV disagreeing with itself) is resolved by correcting the profile."""

    id: str
    origin: ConflictOrigin
    kind: ConflictKind
    entryId: Optional[str] = None
    field: str
    description: str
    sourceValue: Optional[str] = None
    graphValue: Optional[str] = None
    resolution: Optional[Resolution] = None


class ParserInfo(_Strict):
    provider: str  # "groq" | "dev"
    model: Optional[str] = None
    mode: str  # "llm_structured" | "heuristic_dev"
    attempts: int = 1


class UploadInfo(_Strict):
    originalFilename: str  # sanitized; metadata only -- never used as a path
    detectedType: Literal["pdf", "docx"]
    sha256: str
    sizeBytes: int


class SourceProfile(_Strict):
    schemaVersion: int = SCHEMA_VERSION
    revision: int
    upload: UploadInfo
    parser: ParserInfo
    uploadedAt: datetime
    parsedAt: datetime
    updatedAt: datetime

    contact: ContactInfo = Field(default_factory=ContactInfo)
    summary: Optional[SourceFact] = None
    employment: list[EmploymentEntry] = Field(default_factory=list)
    education: list[EducationEntry] = Field(default_factory=list)
    projects: list[ProjectEntry] = Field(default_factory=list)
    certifications: list[CertificationEntry] = Field(default_factory=list)
    languages: list[LanguageEntry] = Field(default_factory=list)
    otherSections: list[OtherSection] = Field(default_factory=list)
    warnings: list[ParseWarning] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)

    def fact_index(self) -> dict[str, tuple[SourceFact, str, str]]:
        """fact id -> (fact, owning entity id, kind). The set of things a generated CV may cite."""
        index: dict[str, tuple[SourceFact, str, str]] = {}
        if self.summary:
            index[self.summary.id] = (self.summary, self.summary.id, "summary")
        for group, kind in ((self.employment, "employment"), (self.education, "education"), (self.projects, "project")):
            for entry in group:
                for fact in entry.facts:
                    index[fact.id] = (fact, entry.id, kind)
        for section in self.otherSections:
            for fact in section.items:
                index[fact.id] = (fact, section.id, "other")
        return index

    def all_ids(self) -> set[str]:
        ids = {self.summary.id} if self.summary else set()
        for entry in [*self.employment, *self.education, *self.projects]:
            ids.add(entry.id)
            ids.update(f.id for f in entry.facts)
        ids.update(c.id for c in self.certifications)
        ids.update(lang.id for lang in self.languages)
        for section in self.otherSections:
            ids.add(section.id)
            ids.update(f.id for f in section.items)
        ids.update(c.id for c in self.conflicts)
        return ids


# ---- derived data: normalized dates, document conflicts, derived warnings --------------------------------


def _derive_dates(entry: _Dated, label: str, warnings: list[ParseWarning], entry_id: str) -> None:
    entry.start = parse_date_text(entry.startText)
    end_text = entry.endText
    if end_text and is_present_word(end_text):
        entry.current = True
        entry.end = None
    else:
        entry.end = parse_date_text(end_text)
    if entry.current:
        entry.end = None
    for text, value, which in ((entry.startText, entry.start, "start"), (end_text, entry.end, "end")):
        if text and value is None and not (which == "end" and entry.current):
            warnings.append(
                ParseWarning(
                    code="date_unparsed",
                    message=f"The {which} date of a {label} entry could not be read reliably, so its original text is used as written.",
                    entryId=entry_id,
                    origin="derived",
                )
            )


def _norm_key(*parts: Optional[str]) -> str:
    return "|".join(" ".join((p or "").lower().split()) for p in parts)


def _overlap(a: EmploymentEntry, b: EmploymentEntry) -> bool:
    if not (a.start and b.start):
        return False
    a_start, b_start = month_index(a.start, end=False), month_index(b.start, end=False)
    a_end = 10**9 if a.current or not a.end else month_index(a.end, end=True)
    b_end = 10**9 if b.current or not b.end else month_index(b.end, end=True)
    return a_start <= b_end and b_start <= a_end


def derive(profile: SourceProfile) -> SourceProfile:
    """Recompute everything that is a pure function of the stored values: normalized dates, the
    derived warnings and the document-internal conflicts. Parser warnings and graph conflicts (and any
    resolutions on them) are preserved. Idempotent."""
    derived_warnings: list[ParseWarning] = []
    for entry in profile.employment:
        _derive_dates(entry, "position", derived_warnings, entry.id)
        if not (entry.startText or entry.endText or entry.current):
            derived_warnings.append(
                ParseWarning(
                    code="missing_dates",
                    message="A position has no dates, so it cannot be placed on the timeline.",
                    entryId=entry.id,
                    origin="derived",
                )
            )
    for entry in profile.education:
        _derive_dates(entry, "education", derived_warnings, entry.id)
    for project in profile.projects:
        _derive_dates(project, "project", derived_warnings, project.id)
    for cert in profile.certifications:
        cert.date = parse_date_text(cert.dateText)

    document_conflicts: list[Conflict] = []
    for entry in profile.employment:
        if entry.start and entry.end and not entry.current:
            if month_index(entry.start, end=False) > month_index(entry.end, end=True):
                document_conflicts.append(
                    Conflict(
                        id=_conflict_id("document", "date_order", entry.id, "dates"),
                        origin="document",
                        kind="date_order",
                        entryId=entry.id,
                        field="dates",
                        description="A position ends before it starts. Correct the dates.",
                        sourceValue=f"{entry.startText} – {entry.endText}",
                    )
                )
    seen: dict[str, EmploymentEntry] = {}
    for entry in profile.employment:
        key = _norm_key(entry.employer, entry.title)
        earlier = seen.get(key)
        if earlier is not None and _overlap(earlier, entry):
            document_conflicts.append(
                Conflict(
                    id=_conflict_id("document", "duplicate", entry.id, "entry"),
                    origin="document",
                    kind="duplicate",
                    entryId=entry.id,
                    field="entry",
                    description="Two positions have the same employer and title and overlapping dates. Remove one if it is a duplicate.",
                    sourceValue=" — ".join(p for p in (entry.title, entry.employer) if p),
                )
            )
        seen.setdefault(key, entry)

    kept_warnings = [w for w in profile.warnings if w.origin != "derived"]
    graph_conflicts = [c for c in profile.conflicts if c.origin == "graph"]
    profile.warnings = kept_warnings + derived_warnings
    profile.conflicts = document_conflicts + graph_conflicts
    return profile


def _conflict_id(origin: str, kind: str, entry_id: str, field: str, *values: Optional[str]) -> str:
    raw = "\x1f".join([origin, kind, entry_id, field, *[" ".join((v or "").lower().split()) for v in values]])
    return "cnf_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def graph_conflict_id(kind: str, entry_id: str, field: str, source_value: Optional[str], graph_value: Optional[str]) -> str:
    """Deterministic: the same disagreement gets the same id on every analysis, so a resolution the user
    recorded keeps applying -- and stops applying the moment either underlying value changes."""
    return _conflict_id("graph", kind, entry_id, field, source_value, graph_value)


# ---- what the review API returns ---------------------------------------------------------------------------


_FACT_EXCLUDE = {"excerpt": True}
_ENTRY_EXCLUDE = {"excerpt": True, "facts": {"__all__": _FACT_EXCLUDE}}
_VIEW_EXCLUDE = {
    "summary": _FACT_EXCLUDE,
    "employment": {"__all__": _ENTRY_EXCLUDE},
    "education": {"__all__": _ENTRY_EXCLUDE},
    "projects": {"__all__": _ENTRY_EXCLUDE},
    "certifications": {"__all__": {"excerpt": True}},
    "languages": {"__all__": {"excerpt": True}},
    "otherSections": {"__all__": {"items": {"__all__": _FACT_EXCLUDE}}},
    "upload": {"sha256": True},
}


def profile_view(profile: SourceProfile) -> dict:
    """The safe, JSON-ready view for the review screen: no source excerpts, no hash, no paths."""
    return profile.model_dump(mode="json", exclude=_VIEW_EXCLUDE)


def completeness(profile: SourceProfile) -> dict:
    contact = profile.contact
    missing = [
        label
        for label, value in (
            ("name", contact.fullName),
            ("email", contact.email),
            ("telephone", contact.telephone),
            ("location", contact.city or contact.country),
            ("employment", profile.employment),
            ("education", profile.education),
        )
        if not value
    ]
    return {
        "employmentCount": len(profile.employment),
        "educationCount": len(profile.education),
        "languageCount": len(profile.languages),
        "certificationCount": len(profile.certifications),
        "projectCount": len(profile.projects),
        "missing": missing,
        "isComplete": not missing,
    }


def unresolved_graph_conflicts(profile: SourceProfile) -> list[Conflict]:
    return [c for c in profile.conflicts if c.origin == "graph" and c.resolution is None]


# ---- user corrections (PUT /api/source-cv) -----------------------------------------------------------------


class FactEdit(_Strict):
    id: Optional[str] = None
    text: str


class _DatedEdit(_Strict):
    startText: Optional[str] = None
    endText: Optional[str] = None
    current: bool = False


class EmploymentEdit(_DatedEdit):
    id: Optional[str] = None
    employer: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    facts: list[FactEdit] = Field(default_factory=list)


class EducationEdit(_DatedEdit):
    id: Optional[str] = None
    institution: str
    qualification: Optional[str] = None
    field: Optional[str] = None
    location: Optional[str] = None
    facts: list[FactEdit] = Field(default_factory=list)


class ProjectEdit(_DatedEdit):
    id: Optional[str] = None
    name: str
    role: Optional[str] = None
    url: Optional[str] = None
    facts: list[FactEdit] = Field(default_factory=list)


class CertificationEdit(_Strict):
    id: Optional[str] = None
    name: str
    issuer: Optional[str] = None
    dateText: Optional[str] = None


class LanguageEdit(_Strict):
    id: Optional[str] = None
    language: str
    proficiency: Optional[str] = None


class OtherSectionEdit(_Strict):
    id: Optional[str] = None
    heading: str
    items: list[FactEdit] = Field(default_factory=list)


class ProfileEdit(_Strict):
    expectedRevision: int
    contact: ContactInfo = Field(default_factory=ContactInfo)
    summary: Optional[str] = None
    employment: list[EmploymentEdit] = Field(default_factory=list)
    education: list[EducationEdit] = Field(default_factory=list)
    projects: list[ProjectEdit] = Field(default_factory=list)
    certifications: list[CertificationEdit] = Field(default_factory=list)
    languages: list[LanguageEdit] = Field(default_factory=list)
    otherSections: list[OtherSectionEdit] = Field(default_factory=list)
    # conflict id -> the user's explicit choice (null clears it)
    conflictResolutions: dict[str, Optional[Resolution]] = Field(default_factory=dict)


def _invalid(reason: str) -> InvalidProfileEditError:
    return InvalidProfileEditError(f"Some of the submitted values are not valid: {reason}.")


def _opt(value: Optional[str], max_chars: int, what: str) -> Optional[str]:
    if value is None or not value.strip():
        return None
    cleaned = clean_text(value, max_chars)
    if cleaned is None:
        raise _invalid(f"{what} is too long or unreadable")
    return cleaned


def _req(value: Optional[str], max_chars: int, what: str) -> str:
    cleaned = _opt(value, max_chars, what)
    if cleaned is None:
        raise _invalid(f"{what} is required")
    return cleaned


def _check_count(items: list, limit: int, what: str) -> None:
    if len(items) > limit:
        raise _invalid(f"too many {what} (at most {limit})")


def _apply_facts(
    edits: list[FactEdit], current: list[SourceFact], allocator: IdAllocator, owner_label: str
) -> list[SourceFact]:
    _check_count(edits, MAX_FACTS_PER_ENTRY, f"statements in {owner_label}")
    by_id = {f.id: f for f in current}
    used: set[str] = set()
    out: list[SourceFact] = []
    for edit in edits:
        text = clean_text(edit.text, MAX_FACT_CHARS)
        if not edit.text.strip():
            continue  # a blank statement is a removed statement
        if text is None:
            raise _invalid("a statement is too long or unreadable")
        if edit.id is None:
            out.append(SourceFact(id=allocator.random("f"), text=text, excerpt=None, userEdited=True))
            continue
        previous = by_id.get(edit.id)
        if previous is None or edit.id in used:
            raise _invalid("a statement refers to an unknown or repeated id")
        used.add(edit.id)
        out.append(
            SourceFact(
                id=previous.id, text=text, excerpt=previous.excerpt,
                userEdited=previous.userEdited or text != previous.text,
            )
        )
    return out


def _resolve_existing(edit_id: Optional[str], lookup: dict, seen: set[str], kind: str):
    if edit_id is None:
        return None
    if edit_id not in lookup or edit_id in seen:
        raise _invalid(f"a {kind} refers to an unknown or repeated id")
    seen.add(edit_id)
    return lookup[edit_id]


def _edit_dates(edit: _DatedEdit) -> dict:
    return {
        "startText": _opt(edit.startText, 60, "a date"),
        "endText": _opt(edit.endText, 60, "a date"),
        "current": edit.current,
    }


def apply_edit(current: SourceProfile, edit: ProfileEdit, when: datetime) -> SourceProfile:
    """The user's corrections applied to the stored profile, as a NEW profile (the input is untouched).
    Existing ids are preserved, new entries get fresh ids, excerpts are kept, normalized dates and
    derived warnings/conflicts are recomputed. Raises InvalidProfileEditError on any bad value."""
    allocator = IdAllocator(current.all_ids())
    _check_count(edit.employment, MAX_EMPLOYMENT, "positions")
    _check_count(edit.education, MAX_EDUCATION, "education entries")
    _check_count(edit.projects, MAX_PROJECTS, "projects")
    _check_count(edit.certifications, MAX_CERTIFICATIONS, "certifications")
    _check_count(edit.languages, MAX_LANGUAGES, "languages")
    _check_count(edit.otherSections, MAX_OTHER_SECTIONS, "other sections")

    contact_in = edit.contact
    contact = ContactInfo(
        fullName=_opt(contact_in.fullName, MAX_FIELD_CHARS, "the name"),
        email=_opt(contact_in.email, MAX_FIELD_CHARS, "the email"),
        telephone=_opt(contact_in.telephone, 60, "the telephone number"),
        city=_opt(contact_in.city, MAX_FIELD_CHARS, "the city"),
        country=_opt(contact_in.country, MAX_FIELD_CHARS, "the country"),
    )
    if contact.email and valid_email(contact.email) is None:
        raise _invalid("the email address is malformed")
    for attr, label in (("linkedinUrl", "LinkedIn"), ("githubUrl", "GitHub"), ("portfolioUrl", "portfolio")):
        raw = getattr(contact_in, attr)
        if raw and raw.strip():
            url = safe_url(raw)
            if url is None:
                raise _invalid(f"the {label} link must be a web address (http or https)")
            setattr(contact, attr, url)
    _check_count(contact_in.otherUrls, MAX_OTHER_URLS, "other links")
    for other in contact_in.otherUrls:
        url = safe_url(other.url)
        if url is None:
            raise _invalid("a link must be a web address (http or https)")
        contact.otherUrls.append(LabeledUrl(label=_opt(other.label, 80, "a link label"), url=url))

    summary = None
    if edit.summary and edit.summary.strip():
        text = clean_text(edit.summary, MAX_FACT_CHARS)
        if text is None:
            raise _invalid("the summary is too long or unreadable")
        prior = current.summary
        summary = SourceFact(
            id=prior.id if prior else allocator.random("sum"),
            text=text,
            excerpt=prior.excerpt if prior else None,
            userEdited=(prior.userEdited or text != prior.text) if prior else True,
        )

    employment_lookup = {e.id: e for e in current.employment}
    seen: set[str] = set()
    employment: list[EmploymentEntry] = []
    for item in edit.employment:
        previous = _resolve_existing(item.id, employment_lookup, seen, "position")
        employer = _opt(item.employer, MAX_FIELD_CHARS, "an employer")
        title = _opt(item.title, MAX_FIELD_CHARS, "a job title")
        if not (employer or title):
            raise _invalid("each position needs an employer or a job title")
        employment.append(
            EmploymentEntry(
                id=previous.id if previous else allocator.random("emp"),
                employer=employer, title=title,
                location=_opt(item.location, MAX_FIELD_CHARS, "a location"),
                facts=_apply_facts(item.facts, previous.facts if previous else [], allocator, "a position"),
                excerpt=previous.excerpt if previous else None,
                **_edit_dates(item),
            )
        )

    education_lookup = {e.id: e for e in current.education}
    seen = set()
    education: list[EducationEntry] = []
    for item in edit.education:
        previous = _resolve_existing(item.id, education_lookup, seen, "education entry")
        education.append(
            EducationEntry(
                id=previous.id if previous else allocator.random("edu"),
                institution=_req(item.institution, MAX_FIELD_CHARS, "an institution"),
                qualification=_opt(item.qualification, MAX_FIELD_CHARS, "a qualification"),
                field=_opt(item.field, MAX_FIELD_CHARS, "a field of study"),
                location=_opt(item.location, MAX_FIELD_CHARS, "a location"),
                facts=_apply_facts(item.facts, previous.facts if previous else [], allocator, "an education entry"),
                excerpt=previous.excerpt if previous else None,
                **_edit_dates(item),
            )
        )

    project_lookup = {p.id: p for p in current.projects}
    seen = set()
    projects: list[ProjectEntry] = []
    for item in edit.projects:
        previous = _resolve_existing(item.id, project_lookup, seen, "project")
        url = None
        if item.url and item.url.strip():
            url = safe_url(item.url)
            if url is None:
                raise _invalid("a project link must be a web address (http or https)")
        projects.append(
            ProjectEntry(
                id=previous.id if previous else allocator.random("prj"),
                name=_req(item.name, MAX_FIELD_CHARS, "a project name"),
                role=_opt(item.role, MAX_FIELD_CHARS, "a project role"),
                url=url,
                facts=_apply_facts(item.facts, previous.facts if previous else [], allocator, "a project"),
                excerpt=previous.excerpt if previous else None,
                **_edit_dates(item),
            )
        )

    cert_lookup = {c.id: c for c in current.certifications}
    seen = set()
    certifications: list[CertificationEntry] = []
    for item in edit.certifications:
        previous = _resolve_existing(item.id, cert_lookup, seen, "certification")
        certifications.append(
            CertificationEntry(
                id=previous.id if previous else allocator.random("crt"),
                name=_req(item.name, MAX_FIELD_CHARS, "a certification name"),
                issuer=_opt(item.issuer, MAX_FIELD_CHARS, "an issuer"),
                dateText=_opt(item.dateText, 60, "a date"),
                excerpt=previous.excerpt if previous else None,
            )
        )

    language_lookup = {c.id: c for c in current.languages}
    seen = set()
    languages: list[LanguageEntry] = []
    for item in edit.languages:
        previous = _resolve_existing(item.id, language_lookup, seen, "language")
        languages.append(
            LanguageEntry(
                id=previous.id if previous else allocator.random("lng"),
                language=_req(item.language, MAX_FIELD_CHARS, "a language"),
                proficiency=_opt(item.proficiency, 80, "a proficiency"),
                excerpt=previous.excerpt if previous else None,
            )
        )

    section_lookup = {s.id: s for s in current.otherSections}
    seen = set()
    sections: list[OtherSection] = []
    for item in edit.otherSections:
        previous = _resolve_existing(item.id, section_lookup, seen, "section")
        sections.append(
            OtherSection(
                id=previous.id if previous else allocator.random("oth"),
                heading=_req(item.heading, MAX_FIELD_CHARS, "a section heading"),
                items=_apply_facts(item.items, previous.items if previous else [], allocator, "a section"),
            )
        )

    conflicts = [c.model_copy(deep=True) for c in current.conflicts]
    known = {c.id: c for c in conflicts}
    for conflict_id, choice in edit.conflictResolutions.items():
        target = known.get(conflict_id)
        if target is None or target.origin != "graph":
            raise _invalid("a conflict resolution refers to an unknown conflict")
        target.resolution = choice

    updated = current.model_copy(
        deep=True,
        update={
            "revision": current.revision + 1,
            "updatedAt": when,
            "contact": contact,
            "summary": summary,
            "employment": employment,
            "education": education,
            "projects": projects,
            "certifications": certifications,
            "languages": languages,
            "otherSections": sections,
            "conflicts": conflicts,
        },
    )
    return derive(SourceProfile.model_validate(updated.model_dump()))
