"""Draft -> StructuredCV assembly, and the deterministic validation every source-aware CV must pass.

    "The LLM writes. The graph proves."  Here the split is explicit:

    DETERMINISTIC (rendered from validated data; the model has no field to write them in)
        name, contact details and links, every employer, title, place and period, education names and
        periods, certifications, languages -- and WHICH roles exist. Every reconciled role appears exactly
        once: an omitted role is added as a compact entry, never dropped.
    MODEL-WRITTEN (must be proven)
        headline, profile, bullets, the choice of featured vs compact, which projects/other-section items
        to keep, and which supported skills to list.

Validation is REJECT-NEVER-SILENTLY-REPAIR: `check_draft` returns the specific violations (each with a
`target` the repair loop may rewrite or remove) and cv_generation decides whether to repair, retry or fail.
The rules, each with a stable code:

  structure     unknown_role / duplicate_role / unknown_entry / duplicate_entry / unknown_other_item
  provenance    empty_evidence, missing_namespace, unknown_evidence_id, foreign_evidence
  claims        gap_claimed, internal_language, contact_in_prose, unsupported_number,
                quantified_claim_needs_graph, unsupported_term, unsupported_title, unsupported_qualification,
                unsupported_skill
  shape         empty_text, length_limit, compact_too_long
  accuracy      missing_role, header_mismatch, contact_mismatch, missing_education, ... (defence in depth:
                the assembler builds these from data, so a failure means something altered them)

The checks are lexical and structural; source-backed claims are additionally verified semantically by a
separate model pass (llm/complete_writers.py) -- lexical matching alone cannot tell a reworded claim from
an embellished one.
"""
import re
from dataclasses import dataclass
from typing import Optional

from cv_writer import DEFAULT_CV_NAME
from llm.complete_registry import (
    GRAPH,
    SOURCE,
    Citation,
    CompleteInput,
    Scope,
    canonical_id,
    known_terms,
    resolve_citation,
    scopes,
)
from llm.cv_assembly import (
    MAX_BULLET_CHARS,
    MAX_BULLETS_PER_ENTRY,
    MAX_ENTRIES_PER_SECTION,
    MAX_HEADLINE_CHARS,
    MAX_PROFILE_CHARS,
)
from llm.cv_input import redact_contact_details
from llm.errors import Violation
from llm.provenance import _INTERNAL_PATTERNS, numbers_in
from llm.source_schemas import CompleteCvDraft, DraftBullet
from llm.text import mentions_term, normalize
from source_cv.dates import format_partial
from source_cv.reconcile import ReconciledEducation, ReconciledProject, ReconciledRole, format_span
from source_cv.schema import ContactInfo
from structured_cv import (
    CVBullet,
    CVCertification,
    CVContact,
    CVEducationEntry,
    CVExperience,
    CVLink,
    CVOtherSection,
    CVProject,
    CVSkills,
    StructuredCV,
)

MAX_FEATURED_BULLETS = 5
MAX_COMPACT_BULLETS = 1
MAX_SKILLS_PER_BUCKET = 30

_ID_LEAK = re.compile(
    r"\b(?:graph:(?:story|achievement|transferable|education):|source:[a-z]{1,4}_[0-9a-f]{6,}|"
    r"(?:role|edu|prj):(?:graph:|[a-z]{3}_[0-9a-f]{6,}))",
    re.IGNORECASE,
)
_SENIORITY = ("senior", "lead", "principal", "staff", "head", "director", "manager", "chief", "vp")
_QUALIFICATION = ("phd", "doctorate", "msc", "mba", "bsc", "master", "masters", "bachelor", "diploma")


# ---- deterministic pieces (shared by the assembler and the validator) ---------------------------------------------


def role_fields(role: ReconciledRole) -> dict:
    h = role.header
    span = h.span
    return {
        "title": h.title or h.employer or "Position",
        "organization": h.employer if h.title else None,
        "location": h.location,
        "startDate": format_partial(span.start),
        "endDate": None if span.current else format_partial(span.end),
        "dateRange": h.period,
        "entryId": role.role_id,
    }


def education_fields(edu: ReconciledEducation) -> dict:
    return {
        "institution": edu.institution,
        "program": edu.program,
        "startDate": format_partial(edu.span.start),
        "endDate": None if edu.span.current else format_partial(edu.span.end),
        "dateRange": format_span(edu.span),
        "entryId": edu.entry_id,
    }


def project_fields(project: ReconciledProject) -> dict:
    return {
        "name": project.name,
        "role": project.role,
        "context": project.context,
        "startDate": format_partial(project.span.start),
        "endDate": None if project.span.current else format_partial(project.span.end),
        "dateRange": format_span(project.span),
        "entryId": project.entry_id,
    }


def cv_contact(contact: ContactInfo) -> Optional[CVContact]:
    links: list[CVLink] = []
    if contact.linkedinUrl:
        links.append(CVLink(label="LinkedIn", url=contact.linkedinUrl))
    if contact.githubUrl:
        links.append(CVLink(label="GitHub", url=contact.githubUrl))
    if contact.portfolioUrl:
        links.append(CVLink(label="Portfolio", url=contact.portfolioUrl))
    links.extend(CVLink(label=link.label or "Website", url=link.url) for link in contact.otherUrls)
    if not any((contact.email, contact.telephone, contact.city, contact.country, links)):
        return None
    return CVContact(email=contact.email, telephone=contact.telephone, city=contact.city, country=contact.country, links=links)


def cv_name(contact: ContactInfo) -> str:
    return contact.fullName or DEFAULT_CV_NAME


def expected_languages(ci: CompleteInput) -> list[str]:
    """The candidate's own languages exactly as written, then any human language the graph lists that the
    CV does not (the graph is authoritative for skills)."""
    out: list[str] = []
    seen: set[str] = set()
    for entry in ci.profile.languages:
        out.append(f"{entry.language} ({entry.proficiency})" if entry.proficiency else entry.language)
        seen.add(normalize(entry.language))
    for skill in ci.cv_context.skills:
        if skill.category == "language" and normalize(skill.displayName) not in seen and normalize(skill.name) not in seen:
            out.append(skill.displayName)
            seen.add(normalize(skill.name))
    return out


def expected_certifications(ci: CompleteInput) -> list[CVCertification]:
    return [CVCertification(name=c.name, issuer=c.issuer, date=c.dateText) for c in ci.profile.certifications]


# ---- assembly --------------------------------------------------------------------------------------------------------


def _clean_bullets(ci: CompleteInput, bullets: list[DraftBullet]) -> list[CVBullet]:
    out: list[CVBullet] = []
    for bullet in bullets:
        ids: list[str] = []
        for cited in bullet.evidenceIds:
            cid = canonical_id(ci, cited.strip())
            if cid not in ids:
                ids.append(cid)
        out.append(CVBullet(text=" ".join(bullet.text.split()), evidenceIds=ids))
    return out


def _clean_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        value = " ".join(value.split())
        if value and value.lower() not in seen:
            seen.add(value.lower())
            out.append(value)
    return out


def _entries_by_id(items, key, section: str, violations: list[Violation], valid_ids: set[str]) -> dict:
    found: dict = {}
    for item in items:
        entry_id = key(item)
        if entry_id not in valid_ids:
            violations.append(Violation("unknown_entry", f"{section}: unknown entry {entry_id!r}", "structure"))
        elif entry_id in found:
            violations.append(Violation("duplicate_entry", f"{section}: {entry_id!r} listed twice", "structure"))
        else:
            found[entry_id] = item
    return found


def assemble_complete_cv(draft: CompleteCvDraft, ci: CompleteInput) -> tuple[StructuredCV, list[Violation]]:
    """Build the StructuredCV from data + the model's wording. Structural problems in the draft are
    returned as violations (and the offending entries ignored), never silently absorbed."""
    violations: list[Violation] = []
    recon = ci.recon

    role_ids = {r.role_id for r in recon.roles}
    draft_roles = _entries_by_id(draft.roles, lambda d: d.roleId, "roles", violations, role_ids)
    experience: list[CVExperience] = []
    additional: list[CVExperience] = []
    for role in recon.roles:
        entry_draft = draft_roles.get(role.role_id)
        bullets = _clean_bullets(ci, entry_draft.bullets) if entry_draft else []
        wants_featured = entry_draft is not None and entry_draft.emphasis == "featured" and bool(bullets)
        entry = CVExperience(**role_fields(role), bullets=bullets)
        # A role backed by graph evidence is relevant by definition; anything else is featured only if the
        # model both asked for it and wrote something to feature. Otherwise it is kept, compactly.
        (experience if (role.has_graph_evidence or wants_featured) else additional).append(entry)

    project_ids = {p.entry_id for p in recon.projects}
    draft_projects = _entries_by_id(draft.projects, lambda d: d.entryId, "projects", violations, project_ids)
    projects: list[CVProject] = []
    for project in recon.projects:
        entry_draft = draft_projects.get(project.entry_id)
        if entry_draft and entry_draft.bullets:
            projects.append(CVProject(**project_fields(project), bullets=_clean_bullets(ci, entry_draft.bullets)))
    if len(projects) > MAX_ENTRIES_PER_SECTION:
        violations.append(Violation("length_limit", "projects: too many entries", "structure"))

    education_ids = {e.entry_id for e in recon.education}
    draft_education = _entries_by_id(draft.education, lambda d: d.entryId, "education", violations, education_ids)
    education = [
        CVEducationEntry(
            **education_fields(edu),
            bullets=_clean_bullets(ci, draft_education[edu.entry_id].bullets) if edu.entry_id in draft_education else [],
        )
        for edu in recon.education
    ]

    sections_by_id = {s.id: s for s in ci.profile.otherSections}
    picks = _entries_by_id(draft.otherSections, lambda d: d.sectionId, "otherSections", violations, set(sections_by_id))
    other: list[CVOtherSection] = []
    for section in ci.profile.otherSections:
        pick = picks.get(section.id)
        if not pick:
            continue
        wanted = set(pick.factIds)
        for fact_id in wanted - {item.id for item in section.items}:
            violations.append(Violation("unknown_other_item", f"otherSections[{section.id}]: unknown item {fact_id!r}", "structure"))
        items = [item.text for item in section.items if item.id in wanted]
        if items:
            other.append(CVOtherSection(heading=section.heading, items=items))

    skills = CVSkills(**{k: _clean_list(v) for k, v in draft.skills.model_dump().items()})
    cv = StructuredCV(
        name=cv_name(ci.profile.contact),
        headline=" ".join(draft.headline.split()),
        profile=" ".join(draft.profile.split()),
        profileEvidenceIds=_clean_list([canonical_id(ci, i.strip()) for i in draft.profileEvidenceIds]),
        contact=cv_contact(ci.profile.contact),
        experience=experience,
        additionalExperience=additional,
        projects=projects,
        education=education,
        certifications=expected_certifications(ci),
        skills=skills,
        languages=expected_languages(ci),
        otherSections=other,
    )
    return cv, violations


# ---- validation --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TracedEvidence:
    origin: str  # "graph" | "source"
    kind: str
    text: str
    owner: Optional[str]
    source_type: Optional[str] = None
    transferable_for: Optional[str] = None
    gaps: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompleteTrace:
    section: str  # "profile" | "experience" | "additional" | "projects" | "education"
    entry: str
    text: str
    evidence: tuple[TracedEvidence, ...]
    ref: str  # the repair/verification handle: "profile" or "<entry id>#<n>"


def _traced(citation: Citation) -> TracedEvidence:
    item = citation.item
    return TracedEvidence(
        origin=citation.origin, kind=item.kind, text=item.text, owner=item.owner, source_type=item.source_type,
        transferable_for=item.transferable_for, gaps=item.gaps,
    )


def _owner_text(ci: CompleteInput, citation: Citation) -> str:
    """The full text of the evidence's owning story, for graph evidence (the same corpus the graph-only
    validator uses); a source fact stands for itself."""
    if citation.origin == "graph" and citation.item.owner:
        return ci.registry.story_texts.get(citation.item.owner, "")
    return ""


def _contains_any(text: str, corpus: str, vocabulary: tuple[str, ...]) -> list[str]:
    return [w for w in vocabulary if mentions_term(text, w) and not mentions_term(corpus, w)]


def _prose_violations(where: str, text: str, target: str, ci: CompleteInput) -> list[Violation]:
    out: list[Violation] = []
    for term in ci.registry.gap_terms:
        if mentions_term(text, term):
            out.append(Violation("gap_claimed", f"{where}: names literal gap {term!r}", target))
    for pattern in _INTERNAL_PATTERNS:
        if pattern.search(text):
            out.append(Violation("internal_language", f"{where}: matches {pattern.pattern!r}", target))
    if _ID_LEAK.search(text):
        out.append(Violation("internal_language", f"{where}: an evidence or entry id appears in the text", target))
    if redact_contact_details(text) != text:
        out.append(Violation("contact_in_prose", f"{where}: contact details in generated text", target))
    return out


def _support_violations(
    where: str, text: str, target: str, ci: CompleteInput, citations: list[Citation], extra_corpus: str, graph_authoritative: bool
) -> list[Violation]:
    out: list[Violation] = []
    cited_text = " ".join(c.item.text + " " + _owner_text(ci, c) for c in citations)
    corpus = f"{cited_text} {extra_corpus}"
    text_numbers = numbers_in(text)
    supported = numbers_in(corpus)
    for number in sorted(text_numbers - supported):
        out.append(Violation("unsupported_number", f"{where}: {number!r} appears in no cited evidence", target))
    if graph_authoritative:
        graph_corpus = " ".join(c.item.text + " " + _owner_text(ci, c) for c in citations if c.origin == "graph") + f" {extra_corpus}"
        for number in sorted((text_numbers & supported) - numbers_in(graph_corpus)):
            out.append(Violation(
                "quantified_claim_needs_graph",
                f"{where}: {number!r} comes only from the source CV, but the career graph is authoritative for numbers on this role",
                target,
            ))
    for term in sorted(known_terms(ci)):
        if mentions_term(text, term) and not mentions_term(corpus, term):
            out.append(Violation("unsupported_term", f"{where}: names {term!r}, which the cited evidence does not", target))
    for word in _contains_any(text, corpus, _SENIORITY):
        out.append(Violation("unsupported_title", f"{where}: claims seniority {word!r} not in the evidence or title", target))
    for word in _contains_any(text, corpus, _QUALIFICATION):
        out.append(Violation("unsupported_qualification", f"{where}: names qualification {word!r} not in the evidence", target))
    return out


def _resolve_all(
    where: str, target: str, ids: list[str], scope: Optional[Scope], ci: CompleteInput
) -> tuple[list[Violation], list[Citation]]:
    out: list[Violation] = []
    citations: list[Citation] = []
    for cited in ids:
        citation, code = resolve_citation(ci, cited)
        if citation is None:
            out.append(Violation(code, f"{where}: {cited!r}", target))
            continue
        if scope is not None and citation.item.owner not in scope.owners:
            out.append(Violation("foreign_evidence", f"{where}: {cited!r} belongs to a different entry", target))
        citations.append(citation)
    return out, citations


def _check_bullets(
    section: str, entry, scope: Scope, ci: CompleteInput, compact: bool
) -> tuple[list[Violation], list[CompleteTrace]]:
    violations: list[Violation] = []
    traces: list[CompleteTrace] = []
    limit = MAX_COMPACT_BULLETS if compact else (MAX_FEATURED_BULLETS if scope.kind == "role" else MAX_BULLETS_PER_ENTRY)
    for i, bullet in enumerate(entry.bullets, start=1):
        ref = f"{scope.entry_id}#{i}"
        target = f"bullet:{ref}"
        where = f"{section}[{scope.label}] bullet {i}"
        if i > limit:
            code = "compact_too_long" if compact else "length_limit"
            violations.append(Violation(code, f"{where}: more than {limit} bullet(s) for this entry", target))
        if not bullet.text.strip():
            violations.append(Violation("empty_text", where, target))
        if len(bullet.text) > MAX_BULLET_CHARS:
            violations.append(Violation("length_limit", f"{where}: over {MAX_BULLET_CHARS} characters", target))
        if not bullet.evidenceIds:
            violations.append(Violation("empty_evidence", f"{where}: bullet has no evidence ids", target))
            traces.append(CompleteTrace(section, scope.label, bullet.text, (), ref))
            continue
        found, citations = _resolve_all(where, target, bullet.evidenceIds, scope, ci)
        violations += found
        if citations:
            violations += _support_violations(
                where, bullet.text, target, ci, citations, scope.header_text,
                graph_authoritative=scope.has_graph_evidence,
            )
        violations += _prose_violations(where, bullet.text, target, ci)
        traces.append(CompleteTrace(section, scope.label, bullet.text, tuple(_traced(c) for c in citations), ref))
    return violations, traces


def _check_accuracy(cv: StructuredCV, ci: CompleteInput) -> list[Violation]:
    out: list[Violation] = []
    recon = ci.recon

    roles = {r.role_id: r for r in recon.roles}
    seen: dict[str, int] = {}
    for entry in [*cv.experience, *cv.additionalExperience]:
        seen[entry.entryId or ""] = seen.get(entry.entryId or "", 0) + 1
        role = roles.get(entry.entryId or "")
        if role is None:
            out.append(Violation("unknown_role", f"an entry {entry.entryId!r} is not part of the reconciled career", "structure"))
            continue
        expected = role_fields(role)
        actual = {k: getattr(entry, k) for k in expected}
        if actual != expected:
            out.append(Violation("header_mismatch", f"{role.label}: header differs from the verified record", "structure"))
    for role_id, role in roles.items():
        count = seen.get(role_id, 0)
        if count == 0:
            out.append(Violation("missing_role", f"{role.label} is missing from the CV", "structure"))
        elif count > 1:
            out.append(Violation("duplicate_role", f"{role.label} appears {count} times", "structure"))

    dated = [(normalize(e.title), normalize(e.organization or ""), e.startDate) for e in [*cv.experience, *cv.additionalExperience] if e.startDate]
    if len(dated) != len(set(dated)):
        out.append(Violation("duplicate_role", "two entries describe the same position", "structure"))

    expected_education = {e.entry_id: education_fields(e) for e in recon.education}
    seen_education = [e.entryId for e in cv.education]
    for entry in cv.education:
        expected = expected_education.get(entry.entryId or "")
        if expected is None or {k: getattr(entry, k) for k in expected} != expected:
            out.append(Violation("header_mismatch", "an education entry differs from the verified record", "structure"))
    for entry_id in expected_education:
        if seen_education.count(entry_id) != 1:
            out.append(Violation("missing_education", "an education entry is missing or repeated", "structure"))

    if cv.name != cv_name(ci.profile.contact) or cv.contact != cv_contact(ci.profile.contact):
        out.append(Violation("contact_mismatch", "name or contact details differ from the source profile", "structure"))
    if cv.certifications != expected_certifications(ci):
        out.append(Violation("header_mismatch", "certifications differ from the source profile", "structure"))
    if cv.languages != expected_languages(ci):
        out.append(Violation("header_mismatch", "languages differ from the verified record", "structure"))
    return out


def validate_complete_cv(cv: StructuredCV, ci: CompleteInput) -> tuple[list[Violation], list[CompleteTrace]]:
    violations = _check_accuracy(cv, ci)
    traces: list[CompleteTrace] = []
    entry_scopes = scopes(ci)

    for section, entries, compact in (
        ("experience", cv.experience, False),
        ("additional", cv.additionalExperience, True),
        ("projects", cv.projects, False),
        ("education", cv.education, False),
    ):
        for entry in entries:
            scope = entry_scopes.get(entry.entryId or "")
            if scope is None:
                continue  # reported by the accuracy check
            found, entry_traces = _check_bullets(section, entry, scope, ci, compact)
            violations += found
            traces += entry_traces

    role_headers = " ".join(s.header_text for s in entry_scopes.values())

    # headline: a short label, checked like prose and against everything the candidate has
    all_text = " ".join(
        [ci.registry.global_text(), role_headers, ci.profile.summary.text if ci.profile.summary else ""]
        + [item.text for item in ci.facts.values()] + [c.name for c in ci.profile.certifications]
    )
    if cv.headline:
        violations += _prose_violations("headline", cv.headline, "headline", ci)
        for number in sorted(numbers_in(cv.headline) - numbers_in(all_text)):
            violations.append(Violation("unsupported_number", f"headline: {number!r} appears in no evidence", "headline"))
        for word in _contains_any(cv.headline, all_text, _SENIORITY):
            violations.append(Violation("unsupported_title", f"headline: claims seniority {word!r}", "headline"))
        for word in _contains_any(cv.headline, all_text, _QUALIFICATION):
            violations.append(Violation("unsupported_qualification", f"headline: names qualification {word!r}", "headline"))
    if len(cv.headline) > MAX_HEADLINE_CHARS:
        violations.append(Violation("length_limit", "headline too long", "headline"))

    # profile: prose that must rest on cited evidence
    if cv.profile:
        found, citations = _resolve_all("profile", "profile", cv.profileEvidenceIds, None, ci)
        violations += found
        if not cv.profileEvidenceIds:
            violations.append(Violation("empty_evidence", "profile: the profile cites no evidence", "profile"))
        violations += _prose_violations("profile", cv.profile, "profile", ci)
        if citations:
            violations += _support_violations("profile", cv.profile, "profile", ci, citations, role_headers, graph_authoritative=False)
        if len(cv.profile) > MAX_PROFILE_CHARS:
            violations.append(Violation("length_limit", "profile too long", "profile"))
        traces.insert(0, CompleteTrace("profile", "Profile", cv.profile, tuple(_traced(c) for c in citations), "profile"))

    # skills: only what the career graph supports
    for bucket, names in cv.skills.model_dump().items():
        for name in names:
            target = f"skill:{bucket}:{name}"
            if normalize(name) not in ci.registry.allowed_skills:
                violations.append(Violation("unsupported_skill", f"skills.{bucket}: {name!r} is not a supported skill", target))
            violations += _prose_violations(f"skills.{bucket}", name, target, ci)
        if len(names) > MAX_SKILLS_PER_BUCKET:
            violations.append(Violation("length_limit", f"skills.{bucket}: too many entries", "structure"))
    return violations, traces


@dataclass(frozen=True)
class Checked:
    cv: StructuredCV
    violations: list[Violation]
    traces: list[CompleteTrace]


def check_draft(draft: CompleteCvDraft, ci: CompleteInput) -> Checked:
    cv, structural = assemble_complete_cv(draft, ci)
    violations, traces = validate_complete_cv(cv, ci)
    return Checked(cv=cv, violations=structural + violations, traces=traces)


__all__ = [
    "Checked", "CompleteTrace", "GRAPH", "SOURCE", "TracedEvidence", "assemble_complete_cv", "check_draft",
    "cv_contact", "cv_name", "education_fields", "expected_certifications", "expected_languages", "project_fields",
    "role_fields", "validate_complete_cv",
]
