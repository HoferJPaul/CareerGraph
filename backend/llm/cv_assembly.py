"""CvDraft (model output) -> StructuredCV (the existing domain model).

The model chooses WHICH stories to write about and writes the bullets, profile and headline.
Everything factual about an entry -- title, employer, place, dates, program -- is copied from the
graph-derived EvidenceStory by the same helpers DeterministicCVWriter uses, so the model cannot
invent an employer, title or date: it has no field to put one in. The candidate's name is passed
in by the application and never sent to the model.

Structural rules enforced here (each failure is a violation; any violation rejects the draft):
  unknown_story / wrong_section / duplicate_entry   the entry must be a real story in the section
                                                    DeterministicCVWriter's rules place it in
  foreign_evidence    a bullet cites evidence owned by a DIFFERENT story than its entry (e.g. an
                      El-Compas achievement under an employer entry). Education entries may also
                      cite the stories folded into them.
  empty_evidence / unknown_evidence_id   reported here too, so a draft fails fast
  empty_entry / length_limit             a header with no bullets; runaway output
"""
from cv_writer import education_header, experience_header, project_header
from llm.errors import ProvenanceValidationError, Violation
from llm.evidence_registry import EvidenceRegistry
from llm.schemas import CvDraft, DraftEntry
from structured_cv import (
    CVBullet,
    CVEducationEntry,
    CVExperience,
    CVProject,
    CVSkills,
    StructuredCV,
)

MAX_ENTRIES_PER_SECTION = 8
MAX_BULLETS_PER_ENTRY = 8
MAX_BULLET_CHARS = 500
MAX_PROFILE_CHARS = 1200
MAX_HEADLINE_CHARS = 150


def _clean_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        v = " ".join(v.split())
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def _bullets(entry: DraftEntry, where: str, registry: EvidenceRegistry, violations: list[Violation]) -> list[CVBullet]:
    allowed_owners = registry.owners_allowed_under(entry.storyId)
    if not entry.bullets:
        violations.append(Violation("empty_entry", f"{where}: entry has no bullets"))
    if len(entry.bullets) > MAX_BULLETS_PER_ENTRY:
        violations.append(Violation("length_limit", f"{where}: more than {MAX_BULLETS_PER_ENTRY} bullets"))

    out: list[CVBullet] = []
    for i, bullet in enumerate(entry.bullets, start=1):
        loc = f"{where} bullet {i}"
        text = " ".join(bullet.text.split())
        if len(text) > MAX_BULLET_CHARS:
            violations.append(Violation("length_limit", f"{loc}: over {MAX_BULLET_CHARS} characters"))
        if not bullet.evidenceIds:
            violations.append(Violation("empty_evidence", f"{loc}: bullet has no evidence ids"))
        ids: list[str] = []
        for evidence_id in bullet.evidenceIds:
            item = registry.resolve(evidence_id)
            if item is None:
                violations.append(Violation("unknown_evidence_id", f"{loc}: {evidence_id!r}"))
                canonical = evidence_id
            else:
                canonical = item.id
                if item.owner not in allowed_owners:
                    violations.append(
                        Violation("foreign_evidence", f"{loc}: {evidence_id!r} belongs to a different entry")
                    )
            if canonical not in ids:
                ids.append(canonical)
        out.append(CVBullet(text=text, evidenceIds=ids))
    return out


def assemble_structured_cv(draft: CvDraft, registry: EvidenceRegistry, name: str) -> StructuredCV:
    violations: list[Violation] = []

    if len(draft.headline) > MAX_HEADLINE_CHARS:
        violations.append(Violation("length_limit", "headline too long"))
    if len(draft.profile) > MAX_PROFILE_CHARS:
        violations.append(Violation("length_limit", "profile too long"))

    experience: list[CVExperience] = []
    projects: list[CVProject] = []
    education: list[CVEducationEntry] = []

    sections = (
        ("experience", draft.experience, "experience"),
        ("projects", draft.projects, "project"),
        ("education", draft.education, "education"),
    )
    for section_name, entries, expected in sections:
        if len(entries) > MAX_ENTRIES_PER_SECTION:
            violations.append(Violation("length_limit", f"{section_name}: too many entries"))
        seen: set[str] = set()
        for entry in entries:
            where = f"{section_name}[{entry.storyId}]"
            story = registry.stories.get(entry.storyId)
            placement = registry.placements.get(entry.storyId)
            if story is None or placement is None:
                violations.append(Violation("unknown_story", where))
                continue
            if placement.section != expected or (expected == "education" and placement.folds_into):
                violations.append(
                    Violation("wrong_section", f"{where}: belongs in {placement.section!r}, not {section_name!r}")
                )
                continue
            if entry.storyId in seen:
                violations.append(Violation("duplicate_entry", where))
                continue
            seen.add(entry.storyId)

            bullets = _bullets(entry, where, registry, violations)
            if expected == "experience":
                experience.append(experience_header(story).model_copy(update={"bullets": bullets}))
            elif expected == "project":
                projects.append(project_header(story).model_copy(update={"bullets": bullets}))
            else:
                education.append(education_header(story, story.label).model_copy(update={"bullets": bullets}))

    if violations:
        raise ProvenanceValidationError(violations)

    return StructuredCV(
        name=name,
        headline=" ".join(draft.headline.split()),
        profile=" ".join(draft.profile.split()),
        experience=experience,
        projects=projects,
        education=education,
        skills=CVSkills(**{k: _clean_list(v) for k, v in draft.skills.model_dump().items()}),
        languages=_clean_list(draft.languages),
    )
