"""What a source-aware CV may cite, and the payload the writer model is shown.

Two evidence namespaces, never mixed up:

    graph:<evidence id>     verified against Neo4j -- exactly the ids llm/evidence_registry.py already
                            issues (story:..., achievement:...#n, transferable:...#n)
    source:<fact id>        a statement from the candidate's own CV, from the stored profile SNAPSHOT
                            this analysis holds (so later edits to the stored profile cannot change what
                            an existing analysis may cite)

A citation must carry its namespace. An id without one, an id in the wrong namespace, or an id that is
not in the snapshot / the CVContext is "unknown" -- it can never be resolved by accident.

The writer payload deliberately contains NO contact details and not the candidate's name: those are
rendered from the validated profile and the model has no field to put them in. Employers, titles and
periods ARE included (needed to judge relevance). E-mail addresses, phone numbers and the candidate's own
link/name values are redacted from every text before it is sent, best effort.
"""
from dataclasses import dataclass
from typing import Literal, Optional

from llm.cv_input import redact_contact_details, render_writer_message
from llm.evidence_registry import EvidenceItem, EvidenceRegistry
from llm.text import normalize
from source_cv.reconcile import Reconciliation, ReconciledEducation, ReconciledProject, ReconciledRole, format_span
from source_cv.schema import ContactInfo, SourceProfile
from tailor_cv import CVContext

GRAPH = "graph:"
SOURCE = "source:"

Origin = Literal["graph", "source"]


@dataclass(frozen=True)
class Citation:
    origin: Origin
    item: EvidenceItem  # for a source fact: a synthetic item whose owner is the source entity id


@dataclass(frozen=True)
class CompleteInput:
    """Everything one generation is built from -- all of it server-side and already validated."""

    cv_context: CVContext
    registry: EvidenceRegistry  # graph evidence (built from the CVContext)
    profile: SourceProfile  # the immutable snapshot the analysis holds
    recon: Reconciliation
    facts: dict[str, EvidenceItem]  # "source:<fact id>" -> item
    page_budget: int = 2


def build_complete_input(
    cv_context: CVContext, registry: EvidenceRegistry, profile: SourceProfile, recon: Reconciliation, page_budget: int = 2
) -> CompleteInput:
    facts = {
        f"{SOURCE}{fact_id}": EvidenceItem(
            id=f"{SOURCE}{fact_id}", kind="source_fact", text=fact.text, owner=owner, source_type="Source CV"
        )
        for fact_id, (fact, owner, _kind) in profile.fact_index().items()
    }
    return CompleteInput(cv_context, registry, profile, recon, facts, page_budget)


def resolve_citation(ci: CompleteInput, cited_id: str) -> tuple[Optional[Citation], str]:
    """(citation, "") on success, else (None, violation code)."""
    if cited_id.startswith(GRAPH):
        item = ci.registry.resolve(cited_id[len(GRAPH):])
        return (Citation("graph", item), "") if item else (None, "unknown_evidence_id")
    if cited_id.startswith(SOURCE):
        item = ci.facts.get(cited_id)
        return (Citation("source", item), "") if item else (None, "unknown_evidence_id")
    return None, "missing_namespace"


def canonical_id(ci: CompleteInput, cited_id: str) -> str:
    """The namespaced canonical form (legacy aliases resolve to the story id); unknown ids are kept as
    written so validation can report them."""
    citation, _ = resolve_citation(ci, cited_id)
    if citation is None:
        return cited_id
    return cited_id if citation.origin == "source" else f"{GRAPH}{citation.item.id}"


# ---- entry scopes --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scope:
    """One CV entry that bullets can be written under, and the evidence that belongs to it."""

    kind: Literal["role", "project", "education"]
    entry_id: str
    label: str
    header_text: str  # the entry's own title/employer/period, part of what its bullets may mention
    owners: frozenset[str]  # graph story ids and source entity ids whose evidence may be cited here
    has_graph_evidence: bool


def _role_scope(role: ReconciledRole) -> Scope:
    h = role.header
    owners = set(role.graph_story_ids) | ({role.source_entry_id} if role.source_entry_id else set())
    return Scope("role", role.role_id, role.label, " ".join(x for x in (h.title, h.employer, h.location, h.period) if x),
                 frozenset(owners), role.has_graph_evidence)


def _education_scope(edu: ReconciledEducation) -> Scope:
    owners = set(edu.graph_story_ids) | ({edu.source_entry_id} if edu.source_entry_id else set())
    return Scope("education", edu.entry_id, edu.institution,
                 " ".join(x for x in (edu.institution, edu.program, format_span(edu.span)) if x),
                 frozenset(owners), bool(edu.graph_story_ids))


def _project_scope(project: ReconciledProject) -> Scope:
    owners = ({project.graph_story_id} if project.graph_story_id else set()) | (
        {project.source_entry_id} if project.source_entry_id else set()
    )
    return Scope("project", project.entry_id, project.name,
                 " ".join(x for x in (project.name, project.role, project.context, format_span(project.span)) if x),
                 frozenset(owners), bool(project.graph_story_id))


def scopes(ci: CompleteInput) -> dict[str, Scope]:
    out: dict[str, Scope] = {}
    for role in ci.recon.roles:
        out[role.role_id] = _role_scope(role)
    for edu in ci.recon.education:
        out[edu.entry_id] = _education_scope(edu)
    for project in ci.recon.projects:
        out[project.entry_id] = _project_scope(project)
    return out


# ---- the writer payload ---------------------------------------------------------------------------------------


class _Redactor:
    """E-mails/phones by pattern, plus the candidate's own contact values by exact match."""

    def __init__(self, contact: ContactInfo):
        values = [contact.email, contact.telephone, contact.linkedinUrl, contact.githubUrl, contact.portfolioUrl]
        values += [u.url for u in contact.otherUrls]
        if contact.fullName and len(contact.fullName) >= 5:
            values.append(contact.fullName)
        bare = [v.split("://", 1)[-1].removeprefix("www.").rstrip("/") for v in values if v]
        self.values = sorted({v for v in [*[x for x in values if x], *bare] if len(v) >= 5}, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for value in self.values:
            text = text.replace(value, "[redacted]")
        return redact_contact_details(text)


def _graph_evidence(ci: CompleteInput, story_ids: tuple[str, ...], redact) -> tuple[list[dict], list[dict], list[str]]:
    """(graph evidence, transferable evidence, skills) for the stories behind one entry."""
    reg = ci.registry
    skill_display = {s.name: s.displayName for s in ci.cv_context.skills}
    evidence: list[dict] = []
    transferable: list[dict] = []
    skills: list[str] = []
    for story_id in story_ids:
        story = reg.stories.get(story_id)
        if story is None:
            continue
        if story.description:
            evidence.append({"evidenceId": f"{GRAPH}{story_id}", "text": redact(story.description)})
        for n, achievement in enumerate(story.strongestAchievements, start=1):
            evidence.append({
                "evidenceId": f"{GRAPH}achievement:{story.label}#{n}",
                "text": redact(achievement.description),
                "skills": [skill_display.get(s, s) for s in achievement.matchedSkills],
            })
        for name in story.directSkills:
            display = skill_display.get(name, name)
            if display not in skills:
                skills.append(display)
        for item in reg.items.values():
            if item.kind == "transferable" and item.owner == story_id:
                transferable.append({"evidenceId": f"{GRAPH}{item.id}", "capability": item.transferable_for, "text": redact(item.text)})
    return evidence, transferable, skills


def _source_facts(ci: CompleteInput, entity_id: Optional[str], redact) -> list[dict]:
    if not entity_id:
        return []
    return [
        {"evidenceId": item.id, "text": redact(item.text)} for item in ci.facts.values() if item.owner == entity_id
    ]


def build_complete_payload(ci: CompleteInput) -> dict:
    redact = _Redactor(ci.profile.contact)
    cv_context, reg = ci.cv_context, ci.registry

    roles = []
    for role in ci.recon.roles:
        graph_evidence, transferable, skills = _graph_evidence(ci, role.graph_story_ids, redact)
        h = role.header
        entry = {
            "roleId": role.role_id,
            "title": h.title,
            "employer": h.employer,
            "period": h.period,
            "hasGraphEvidence": role.has_graph_evidence,
            "graphEvidence": graph_evidence,
            "transferable": transferable,
            "sourceFacts": _source_facts(ci, role.source_entry_id, redact),
        }
        roles.append({k: v for k, v in entry.items() if v not in (None, [])} | {"hasGraphEvidence": role.has_graph_evidence})

    projects = []
    for project in ci.recon.projects:
        graph_evidence, transferable, _ = _graph_evidence(ci, (project.graph_story_id,) if project.graph_story_id else (), redact)
        facts = _source_facts(ci, project.source_entry_id, redact)
        if graph_evidence or facts:
            projects.append({k: v for k, v in {
                "entryId": project.entry_id, "name": project.name, "period": format_span(project.span),
                "graphEvidence": graph_evidence, "transferable": transferable, "sourceFacts": facts,
            }.items() if v not in (None, [])})

    education = []
    for edu in ci.recon.education:
        graph_evidence, transferable, _ = _graph_evidence(ci, edu.graph_story_ids, redact)
        facts = _source_facts(ci, edu.source_entry_id, redact)
        if graph_evidence or facts:
            education.append({k: v for k, v in {
                "entryId": edu.entry_id, "institution": edu.institution, "graphEvidence": graph_evidence,
                "transferable": transferable, "sourceFacts": facts,
            }.items() if v not in (None, [])})

    other = [
        {"sectionId": section.id, "heading": section.heading,
         "items": [{"factId": item.id, "text": redact(item.text)} for item in section.items]}
        for section in ci.profile.otherSections
    ]

    payload: dict = {
        "targetRequirements": [
            {"skillQuery": r.get("skillQuery"), "importance": r.get("importance"), "category": r.get("category")}
            for r in cv_context.requirements
        ],
        "literalGaps": list(reg.gap_terms),
        "availableSkills": [{"name": s.displayName, "category": s.category or None} for s in cv_context.skills]
        + [
            {"name": display, "category": "related capability"}
            for norm, display in reg.allowed_skills.items()
            if not any(norm == s.name.lower() or norm == s.displayName.lower() for s in cv_context.skills)
        ],
        "roles": roles,
        "projects": projects,
        "education": education,
        "otherSections": other,
    }
    if ci.profile.summary:
        payload["candidateSummary"] = {"evidenceId": f"{SOURCE}{ci.profile.summary.id}", "text": redact(ci.profile.summary.text)}
    return payload


def render_complete_message(ci: CompleteInput, max_chars: int) -> str:
    return render_writer_message(build_complete_payload(ci), max_chars)


def evidence_for_entry(ci: CompleteInput, entry_id: str) -> list[dict]:
    """The evidence ids and texts a bullet under `entry_id` may cite (used to brief the repair step)."""
    redact = _Redactor(ci.profile.contact)
    scope = scopes(ci).get(entry_id)
    if scope is None:
        return []
    out: list[dict] = []
    graph_story_ids = tuple(sorted(o for o in scope.owners if o.startswith("story:")))
    graph_evidence, transferable, _ = _graph_evidence(ci, graph_story_ids, redact)
    out.extend({"evidenceId": e["evidenceId"], "text": e["text"]} for e in graph_evidence + transferable)
    out.extend({"evidenceId": item.id, "text": redact(item.text)} for item in ci.facts.values() if item.owner in scope.owners)
    return out


def known_terms(ci: CompleteInput) -> set[str]:
    """Job requirement terms and supported skills: the vocabulary the term-support check watches for."""
    terms = {normalize(str(r.get("skillQuery", ""))) for r in ci.cv_context.requirements}
    terms |= set(ci.registry.allowed_skills)
    return {t for t in terms if len(t) >= 2} - set(ci.registry.gap_terms)
