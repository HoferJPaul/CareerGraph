"""The set of things a generated CV is allowed to cite.

Built ONLY from a CVContext (the validated, Neo4j-derived evidence package), the registry gives
every citable fact a stable id and records who owns it:

    story:<label>              story-level facts of one experience (description, title, dates)
    achievement:<label>#<n>    the n-th selected achievement of that story
    transferable:<capability>#<n>   evidence for a RELATED capability of a literal gap. It never
                               counts as evidence for the gap itself -- it is citable only as
                               transferable evidence, under the story that owns it.

Ids follow (and extend) the convention DeterministicCVWriter already uses -- `story:<label>`,
`education:<institution>`, and the achievement's own text -- and those legacy ids are kept as
aliases, so the deterministic writer's output validates against the same registry (it is the
regression oracle for the LLM writer).

The same object also answers the other grounding questions: which terms are literal gaps,
which skills may be listed, which numbers are supported, and which stories may own which
evidence.
"""
from dataclasses import dataclass, field
from typing import Optional

from cv_writer import story_placement
from llm.text import normalize
from tailor_cv import CVContext, EvidenceStory

_MIN_GAP_TERM_LENGTH = 2


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    kind: str  # "story" | "achievement" | "transferable"
    text: str
    owner: Optional[str]  # id of the owning story, or None if no selected story owns it
    source_type: Optional[str] = None
    transferable_for: Optional[str] = None  # capability name -- transferable items only
    gaps: tuple[str, ...] = ()  # literal gap skillQueries the capability is related to


@dataclass(frozen=True)
class StoryPlacement:
    story_id: str
    section: str  # "experience" | "project" | "education"
    folds_into: Optional[str] = None  # the Education story id this story folds into


def _story_text(story: EvidenceStory, skill_labels: list[str]) -> str:
    parts = [
        story.label, story.roleTitle, story.company, story.type, story.domain, story.context,
        story.description, story.personRole, story.startDate, story.endDate, story.location,
        story.workMode, story.education, story.program, story.status,
    ]
    return " ".join([p for p in parts if p] + skill_labels)


@dataclass
class EvidenceRegistry:
    items: dict[str, EvidenceItem] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)
    stories: dict[str, EvidenceStory] = field(default_factory=dict)
    placements: dict[str, StoryPlacement] = field(default_factory=dict)
    story_texts: dict[str, str] = field(default_factory=dict)
    gap_terms: list[str] = field(default_factory=list)
    allowed_skills: dict[str, str] = field(default_factory=dict)  # normalized -> display name
    language_skills: set[str] = field(default_factory=set)  # normalized

    def resolve(self, evidence_id: str) -> Optional[EvidenceItem]:
        canonical = self.aliases.get(evidence_id, evidence_id)
        return self.items.get(canonical)

    def owners_allowed_under(self, story_id: str) -> set[str]:
        """Story ids whose evidence may be cited by bullets under `story_id`'s entry: the story
        itself, plus -- for an Education entry -- every story folded into it."""
        owners = {story_id}
        owners.update(p.story_id for p in self.placements.values() if p.folds_into == story_id)
        return owners

    def global_text(self) -> str:
        return " ".join(
            [item.text for item in self.items.values()]
            + list(self.story_texts.values())
            + list(self.allowed_skills.values())
        )


def _assign_story_ids(cv_context: CVContext, reg: EvidenceRegistry) -> dict[str, str]:
    """First pass: every story gets its id up front, so placements can refer to a story that
    appears later in the list (e.g. a folded Role that precedes its Education anchor)."""
    label_to_story_id: dict[str, str] = {}
    for story in cv_context.evidenceStories:
        story_id = f"story:{story.label}"
        n = 2
        while story_id in reg.stories:  # two stories sharing a label: keep both, distinct ids
            story_id = f"story:{story.label}#{n}"
            n += 1
        reg.stories[story_id] = story
        label_to_story_id.setdefault(story.label, story_id)
    return label_to_story_id


def _add_story_evidence(cv_context: CVContext, reg: EvidenceRegistry, label_to_story_id: dict[str, str]) -> None:
    skill_display = {s.name: s.displayName for s in cv_context.skills}
    education_institutions = {s.label for s in cv_context.evidenceStories if s.sourceType == "Education"}

    for story_id, story in reg.stories.items():
        skill_labels = [skill_display.get(s, s) for s in story.directSkills]
        skill_labels += [skill_display.get(s, s) for a in story.strongestAchievements for s in a.matchedSkills]
        reg.story_texts[story_id] = _story_text(story, skill_labels)
        reg.items[story_id] = EvidenceItem(
            id=story_id, kind="story", text=reg.story_texts[story_id], owner=story_id,
            source_type=story.sourceType,
        )
        if story.sourceType == "Education":
            reg.aliases[f"education:{story.label}"] = story_id  # DeterministicCVWriter's legacy id

        section, home = story_placement(story, education_institutions)
        is_anchor = story.sourceType == "Education" and story.label == home
        folds_into = label_to_story_id.get(home) if home and not is_anchor else None
        reg.placements[story_id] = StoryPlacement(story_id, section, folds_into)

        for n, ach in enumerate(story.strongestAchievements, start=1):
            ach_id = f"achievement:{story.label}#{n}"
            reg.items[ach_id] = EvidenceItem(
                id=ach_id, kind="achievement", text=ach.description, owner=story_id,
                source_type="Achievement",
            )
            reg.aliases.setdefault(ach.description, ach_id)  # DeterministicCVWriter's legacy id


def _add_transferable_evidence(cv_context: CVContext, reg: EvidenceRegistry, label_to_story_id: dict[str, str]) -> None:
    """Related-capability evidence of literal gaps, deduplicated by (capability, source) because
    several gaps can point at the same capability."""
    collected: dict[tuple[str, str], dict] = {}
    for gap in cv_context.gaps:
        for t in gap.transferableEvidence:
            capability = t.canonicalSkill or t.capabilityQuery
            for ev in t.evidence:
                entry = collected.setdefault(
                    (capability, ev.source),
                    {
                        "capability": capability,
                        "text": ev.source,
                        "source_type": ev.sourceType,
                        "owner_label": ev.project or ev.role or (
                            ev.source if ev.sourceType in ("Project", "Role", "Education") else None
                        ),
                        "gaps": [],
                    },
                )
                if gap.skillQuery not in entry["gaps"]:
                    entry["gaps"].append(gap.skillQuery)
            if t.evidence:
                reg.allowed_skills.setdefault(normalize(capability), capability)

    counters: dict[str, int] = {}
    for entry in collected.values():
        capability = entry["capability"]
        counters[capability] = counters.get(capability, 0) + 1
        item_id = f"transferable:{capability}#{counters[capability]}"
        reg.items[item_id] = EvidenceItem(
            id=item_id,
            kind="transferable",
            text=entry["text"],
            owner=label_to_story_id.get(entry["owner_label"]) if entry["owner_label"] else None,
            source_type=entry["source_type"],
            transferable_for=capability,
            gaps=tuple(entry["gaps"]),
        )


def build_registry(cv_context: CVContext) -> EvidenceRegistry:
    reg = EvidenceRegistry()
    label_to_story_id = _assign_story_ids(cv_context, reg)
    _add_story_evidence(cv_context, reg, label_to_story_id)

    for s in cv_context.skills:
        reg.allowed_skills[normalize(s.name)] = s.displayName
        reg.allowed_skills[normalize(s.displayName)] = s.displayName
        if s.category == "language":
            reg.language_skills.update({normalize(s.name), normalize(s.displayName)})

    _add_transferable_evidence(cv_context, reg, label_to_story_id)

    # Literal gaps: nothing supports them, so no generated text may claim them.
    for gap in cv_context.gaps:
        for candidate in (gap.skillQuery, gap.canonicalSkill):
            term = normalize(candidate) if candidate else ""
            if len(term) >= _MIN_GAP_TERM_LENGTH and term not in reg.gap_terms and term not in reg.allowed_skills:
                reg.gap_terms.append(term)
    return reg
