"""Evidence-package builder: turns tailor_cv.CVContext (already-matched evidence
for one job description) into output/cv_evidence.json -- a clean, grouped,
LLM-ready evidence package for a human/Claude to write the actual CV from
afterward.

    output/cv_context.json --(this module)--> output/cv_evidence.json --(manual/Claude)--> CV

This module does NOT write CV prose. It never produces a headline, a profile
paragraph, polished bullets, or any Markdown/HTML/PDF/DOCX. Its only job is to
take CareerGraph's already-matched, already-scored evidence (matching.py's and
tailor_cv.py's job, both untouched by this file) and reshape it so a downstream
writer -- human or LLM -- never has to reconstruct relationships CareerGraph
already knows:

  - which fragmented Project/Role/Achievement/Education nodes belong to the
    same real-world experience (DataBridge is not a random personal project --
    it happened DURING the Werchota.ai role; a 42 curriculum project is not a
    random personal project -- it's PART_OF 42 Prague)
  - which requirements are literally matched vs. only have transferable
    (adjacent, non-literal) evidence vs. are hard gaps
  - which experiences are worth including in a CV for this job description at
    all, and roughly in what order

It reads NO Neo4j data of its own for the core grouping/ranking logic (that
would blur the "evidence retrieval already happened" boundary) -- everything in
build_cv_evidence() is a pure transform over the CVContext already produced by
the untouched matching.py/tailor_cv.py pipeline. The one exception, used only
by the CLI below and documented at each call site, is two *tiny* read-only
Neo4j lookups that expose properties CareerGraph already has but that never
flowed into cv_context.json: (1) the real Achievement.achievementId/.metric
properties (cv_context only ever carried the achievement's description text
and a hasMetric boolean), and (2) which Roles/Projects/Education exist at all,
independent of whether they matched anything for this JD -- needed so a
zero-evidence experience (e.g. a teaching role irrelevant to a software JD)
can be explicitly represented with an "omit" recommendation instead of simply
being absent with no explanation. Neither touches matching, scoring, ingestion,
or the graph schema.
"""
import json
import re
import sys
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

from metrics import has_quantified_metric
from tailor_cv import CVContext, EvidenceStory, RequirementMatch
from requirement_schema import Evidence

ExperienceType = Literal["professional", "independent_project", "education", "volunteer"]
InclusionRecommendation = Literal["include", "optional", "omit"]
LiteralStatus = Literal["matched", "partial", "gap"]

_STRENGTH_WEIGHT = {"strong": 3, "moderate": 2, "weak": 1}
_INCLUDE_THRESHOLD = 5.0


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------


class Provenance(BaseModel):
    sourceType: str
    project: Optional[str] = None
    role: Optional[str] = None
    company: Optional[str] = None
    education: Optional[str] = None
    relationship: Optional[str] = None


class AchievementEvidence(BaseModel):
    achievementId: str
    text: str
    metric: Optional[str] = None
    hasMetric: bool = False
    supportsRequirements: list[str] = []
    skills: list[str] = []
    sourceType: str
    source: str
    provenance: Provenance


class SubProjectEvidence(BaseModel):
    """A Project folded under a parent experience -- e.g. DataBridge under the
    Werchota.ai professional experience, or a 42 curriculum project under 42
    Prague -- instead of appearing as its own unrelated top-level entry."""

    name: str
    description: Optional[str] = None
    skills: list[str] = []
    achievements: list[AchievementEvidence] = []
    provenance: Provenance


class RoleEvidence(BaseModel):
    """A Role folded under a parent experience -- e.g. Student Tutor under 42
    Prague -- instead of appearing as its own thin top-level entry."""

    title: str
    description: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    skills: list[str] = []
    achievements: list[AchievementEvidence] = []
    provenance: Provenance


class RelevanceInfo(BaseModel):
    score: float
    supportedRequirements: list[str] = []
    evidenceStrength: Optional[str] = None
    professionalContext: bool = False


class Experience(BaseModel):
    experienceId: str
    experienceType: ExperienceType
    title: str
    personRole: Optional[str] = None
    organization: Optional[str] = None
    institution: Optional[str] = None
    program: Optional[str] = None
    status: Optional[str] = None
    location: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    workMode: Optional[str] = None
    projects: list[SubProjectEvidence] = []
    roles: list[RoleEvidence] = []
    referenceProjects: list[str] = []
    relevance: RelevanceInfo
    skills: list[str] = []
    technologies: list[str] = []
    achievements: list[AchievementEvidence] = []
    metrics: list[str] = []
    provenance: list[Provenance] = []
    recommendation: InclusionRecommendation
    recommendationReason: str


class RequirementEvidenceRef(BaseModel):
    experience: Optional[str] = None
    sourceType: str
    evidenceStrength: str
    professionalContext: bool


class TransferableRef(BaseModel):
    capability: str
    experience: Optional[str] = None
    reason: Optional[str] = None
    evidenceStrength: Optional[str] = None


class RequirementSummary(BaseModel):
    requirement: str
    skillQuery: str
    importance: str
    category: str
    matchType: str
    matchConfidence: str
    recommendation: str
    literalStatus: LiteralStatus
    evidence: list[RequirementEvidenceRef] = []
    transferableEvidence: list[TransferableRef] = []


class RequirementsSummary(BaseModel):
    matched: list[RequirementSummary] = []
    partial: list[RequirementSummary] = []
    gaps: list[RequirementSummary] = []


class TransferableEvidenceEntry(BaseModel):
    requirement: str
    literalStatus: LiteralStatus
    capability: str
    experience: Optional[str] = None
    reason: Optional[str] = None


class RelevantSkill(BaseModel):
    name: str
    displayName: str
    category: str
    relevance: Literal["literal", "transferable"]


class LanguageEvidence(BaseModel):
    name: str
    proficiency: Optional[str] = None
    evidence: list[str] = []


class CVEvidencePackage(BaseModel):
    generatedFrom: str = "output/cv_context.json"
    experiences: list[Experience] = []
    recommendedExperienceOrder: list[str] = []
    requirements: RequirementsSummary
    transferableEvidence: list[TransferableEvidenceEntry] = []
    relevantSkills: list[RelevantSkill] = []
    languages: list[LanguageEvidence] = []
    strongestThemes: list[str] = []


# --------------------------------------------------------------------------
# Small, dependency-free helpers
# --------------------------------------------------------------------------


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug


def _achievement_key(description: str) -> str:
    return description


def _achievement_evidence(
    description: str,
    matched_skills: list[str],
    supports_requirements: list[str],
    source_type: str,
    source: str,
    provenance: Provenance,
    achievement_metadata: dict[str, dict],
) -> AchievementEvidence:
    meta = achievement_metadata.get(description, {})
    return AchievementEvidence(
        achievementId=meta.get("achievementId") or f"achievement:{_slugify(description)[:60]}",
        text=description,
        metric=meta.get("metric"),
        hasMetric=has_quantified_metric(description) or bool(meta.get("metric")),
        supportsRequirements=supports_requirements,
        skills=matched_skills,
        sourceType=source_type,
        source=source,
        provenance=provenance,
    )


def _education_home(story: EvidenceStory, education_institutions: set[str]) -> Optional[str]:
    """Structural fold-in rule, not name-hardcoded: an Education story is its
    own anchor; a Role folds in via its own AT-company; a Project folds in via
    its own PART_OF-education (both already resolved in tailor_cv.py)."""
    if story.sourceType == "Education" and story.label in education_institutions:
        return story.label
    if story.sourceType == "Role" and story.company in education_institutions:
        return story.company
    if story.sourceType == "Project" and story.education in education_institutions:
        return story.education
    return None


def _relevance_score(stories: list[EvidenceStory]) -> float:
    """Deliberately simple and additive, built only from signals that already
    exist on EvidenceStory (matching.py/tailor_cv.py's own outputs) -- no new
    scoring system, per the task's explicit instruction."""
    if not stories:
        return 0.0
    required_count = sum(s.requiredCount for s in stories)
    support_count = len({r for s in stories for r in s.supportsRequirements})
    strength_score = max((_STRENGTH_WEIGHT.get(s.evidenceStrength, 0) for s in stories), default=0)
    achievement_density = sum(len(s.strongestAchievements) for s in stories)
    professional_bonus = 2 if any(s.professionalContext for s in stories) else 0
    return round(
        required_count * 3 + support_count * 2 + strength_score + achievement_density + professional_bonus, 2
    )


def _recommendation(
    score: float, has_evidence: bool, is_language_only: bool
) -> tuple[InclusionRecommendation, str]:
    if not has_evidence:
        return "omit", "No matched evidence found for the current job description."
    if is_language_only:
        return "optional", "Only language-skill evidence found -- relevant only if the role values that language."
    if score >= _INCLUDE_THRESHOLD:
        return "include", "Strong, relevant matched evidence for this job description."
    return "optional", "Some relevant evidence, but limited in strength or breadth."


_THEME_RULES: list[tuple[set[str], str]] = [
    ({"typescript", "react", "javascript"}, "Full-stack TypeScript/React development"),
    ({"llm api integration", "prompt engineering", "ai automation", "llm engineering"}, "AI/LLM automation pipelines"),
    ({"fastify", "fastapi", "node.js", "backend engineering", "api development"}, "Backend API development"),
    ({"postgresql", "sqlite", "sql"}, "PostgreSQL/data systems"),
    ({"claude code"}, "AI-assisted development with Claude Code"),
    ({"c", "c++", "systems programming", "algorithms"}, "Systems programming fundamentals"),
    ({"docker", "docker compose", "containerization"}, "Containerized deployment"),
]


def _strongest_themes(relevant_skill_names: set[str]) -> list[str]:
    scored = []
    for rule_skills, theme in _THEME_RULES:
        overlap = rule_skills & relevant_skill_names
        if overlap:
            scored.append((len(overlap), theme))
    scored.sort(key=lambda t: -t[0])
    return [theme for _, theme in scored[:5]]


# --------------------------------------------------------------------------
# Core builder
# --------------------------------------------------------------------------


def build_cv_evidence(
    cv_context: CVContext,
    achievement_metadata: Optional[dict[str, dict]] = None,
    known_roles: Optional[list[dict]] = None,
    known_independent_projects: Optional[list[str]] = None,
    known_curriculum_projects: Optional[dict[str, list[str]]] = None,
) -> CVEvidencePackage:
    achievement_metadata = achievement_metadata or {}
    known_roles = known_roles or []
    known_independent_projects = known_independent_projects or []
    known_curriculum_projects = known_curriculum_projects or {}

    skills_by_name = {s.name: s for s in cv_context.skills}
    language_names = {n for n, s in skills_by_name.items() if s.category == "language"}

    stories = list(cv_context.evidenceStories)
    education_institutions = {s.label for s in stories if s.sourceType == "Education"}

    # ---- group evidenceStories into experience buckets --------------------
    professional_groups: dict[str, list[EvidenceStory]] = {}
    independent_groups: dict[str, list[EvidenceStory]] = {}
    education_groups: dict[str, list[EvidenceStory]] = {inst: [] for inst in education_institutions}

    for story in stories:
        home = _education_home(story, education_institutions)
        if home:
            education_groups[home].append(story)
        elif story.professionalContext:
            key = story.company or story.roleTitle or story.label
            professional_groups.setdefault(key, []).append(story)
        else:
            independent_groups.setdefault(story.label, []).append(story)

    experiences: list[Experience] = []
    name_to_experience_id: dict[str, str] = {}

    def register(names: list[str], experience_id: str) -> None:
        for n in names:
            if n:
                name_to_experience_id[n] = experience_id

    # ---- professional experiences ------------------------------------------
    for company, group in professional_groups.items():
        anchor = group[0]
        title = anchor.roleTitle or anchor.label
        experience_id = _slugify(company)
        sub_projects: list[SubProjectEvidence] = []
        all_achievements: list[AchievementEvidence] = []
        direct_skills: set[str] = set()
        provenance_list: list[Provenance] = []
        supported_reqs: set[str] = set()

        for s in group:
            direct_skills.update(s.directSkills)
            supported_reqs.update(s.supportsRequirements)
            prov = Provenance(
                sourceType=s.sourceType, project=s.project, role=s.roleTitle, company=s.company,
                education=None, relationship="DURING" if s.project else None,
            )
            provenance_list.append(prov)
            achievements = [
                _achievement_evidence(
                    a.description, a.matchedSkills, s.supportsRequirements, "Achievement", s.label, prov,
                    achievement_metadata,
                )
                for a in s.strongestAchievements
            ]
            all_achievements.extend(achievements)
            if s.project:
                sub_projects.append(
                    SubProjectEvidence(
                        name=s.project, description=s.description, skills=sorted(s.directSkills),
                        achievements=achievements, provenance=prov,
                    )
                )

        register([company, title] + [p.name for p in sub_projects], experience_id)
        score = _relevance_score(group)
        recommendation, reason = _recommendation(score, has_evidence=True, is_language_only=False)
        experiences.append(
            Experience(
                experienceId=experience_id,
                experienceType="professional",
                title=title,
                organization=company,
                location=anchor.location,
                startDate=anchor.startDate,
                endDate=anchor.endDate,
                workMode=anchor.workMode,
                projects=sub_projects,
                relevance=RelevanceInfo(
                    score=score, supportedRequirements=sorted(supported_reqs),
                    evidenceStrength=anchor.evidenceStrength, professionalContext=True,
                ),
                skills=sorted(direct_skills),
                technologies=sorted(
                    s for s in direct_skills if skills_by_name.get(s) and skills_by_name[s].category in ("technical", "tool")
                ),
                achievements=all_achievements,
                metrics=sorted({a.metric for a in all_achievements if a.metric}),
                provenance=provenance_list,
                recommendation=recommendation,
                recommendationReason=reason,
            )
        )

    # ---- independent / volunteer projects ----------------------------------
    for name, group in independent_groups.items():
        anchor = group[0]
        experience_id = _slugify(name)
        non_language_skills = [s for skl in group for s in skl.directSkills if s not in language_names]
        has_achievements = any(s.strongestAchievements for s in group)
        is_language_only = not non_language_skills and not has_achievements

        all_achievements: list[AchievementEvidence] = []
        direct_skills: set[str] = set()
        provenance_list: list[Provenance] = []
        supported_reqs: set[str] = set()
        for s in group:
            direct_skills.update(s.directSkills)
            supported_reqs.update(s.supportsRequirements)
            prov = Provenance(sourceType=s.sourceType, project=s.project, role=s.roleTitle, company=None, education=None)
            provenance_list.append(prov)
            all_achievements.extend(
                _achievement_evidence(
                    a.description, a.matchedSkills, s.supportsRequirements, "Achievement", s.label, prov,
                    achievement_metadata,
                )
                for a in s.strongestAchievements
            )

        register([name], experience_id)
        score = _relevance_score(group)
        recommendation, reason = _recommendation(score, has_evidence=True, is_language_only=is_language_only)
        experience_type = "volunteer" if anchor.type and "volunteer" in anchor.type.lower() else "independent_project"
        experiences.append(
            Experience(
                experienceId=experience_id,
                experienceType=experience_type,
                title=name,
                personRole=anchor.personRole,
                organization=None,
                location=anchor.location,
                startDate=anchor.startDate,
                endDate=anchor.endDate,
                workMode=anchor.workMode,
                relevance=RelevanceInfo(
                    score=score, supportedRequirements=sorted(supported_reqs),
                    evidenceStrength=anchor.evidenceStrength, professionalContext=False,
                ),
                skills=sorted(direct_skills),
                technologies=sorted(
                    s for s in direct_skills if skills_by_name.get(s) and skills_by_name[s].category in ("technical", "tool")
                ),
                achievements=all_achievements,
                metrics=sorted({a.metric for a in all_achievements if a.metric}),
                provenance=provenance_list,
                recommendation=recommendation,
                recommendationReason=reason,
            )
        )

    # ---- education experiences ----------------------------------------------
    for institution, group in education_groups.items():
        if not group:
            continue
        anchor = next((s for s in group if s.sourceType == "Education"), group[0])
        others = [s for s in group if s is not anchor]
        experience_id = _slugify(institution)

        sub_projects: list[SubProjectEvidence] = []
        sub_roles: list[RoleEvidence] = []
        all_achievements: list[AchievementEvidence] = []
        direct_skills: set[str] = set(anchor.directSkills)
        provenance_list: list[Provenance] = [
            Provenance(sourceType="Education", education=institution)
        ]
        supported_reqs: set[str] = set(anchor.supportsRequirements)

        for s in others:
            direct_skills.update(s.directSkills)
            supported_reqs.update(s.supportsRequirements)
            achievements = [
                _achievement_evidence(
                    a.description, a.matchedSkills, s.supportsRequirements, "Achievement", s.label,
                    Provenance(sourceType=s.sourceType, project=s.project, role=s.roleTitle, education=institution),
                    achievement_metadata,
                )
                for a in s.strongestAchievements
            ]
            all_achievements.extend(achievements)
            if s.sourceType == "Project":
                prov = Provenance(sourceType="Project", project=s.project, education=institution, relationship="PART_OF")
                provenance_list.append(prov)
                sub_projects.append(
                    SubProjectEvidence(
                        name=s.project, description=s.description, skills=sorted(s.directSkills),
                        achievements=achievements, provenance=prov,
                    )
                )
            elif s.sourceType == "Role":
                prov = Provenance(sourceType="Role", role=s.label, company=s.company, education=institution)
                provenance_list.append(prov)
                sub_roles.append(
                    RoleEvidence(
                        title=s.label, description=s.description, startDate=s.startDate, endDate=s.endDate,
                        skills=sorted(s.directSkills), achievements=achievements, provenance=prov,
                    )
                )

        register([institution] + [p.name for p in sub_projects] + [r.title for r in sub_roles], experience_id)
        score = _relevance_score(group)
        recommendation, reason = _recommendation(score, has_evidence=True, is_language_only=False)
        experiences.append(
            Experience(
                experienceId=experience_id,
                experienceType="education",
                title=institution,
                institution=institution,
                program=anchor.program,
                status=anchor.status,
                startDate=anchor.startDate,
                endDate=anchor.endDate,
                projects=sub_projects,
                roles=sub_roles,
                referenceProjects=sorted(
                    set(known_curriculum_projects.get(institution, [])) - {p.name for p in sub_projects}
                ),
                relevance=RelevanceInfo(
                    score=score, supportedRequirements=sorted(supported_reqs),
                    evidenceStrength=anchor.evidenceStrength, professionalContext=False,
                ),
                skills=sorted(direct_skills),
                technologies=sorted(
                    s for s in direct_skills if skills_by_name.get(s) and skills_by_name[s].category in ("technical", "tool")
                ),
                achievements=all_achievements,
                metrics=sorted({a.metric for a in all_achievements if a.metric}),
                provenance=provenance_list,
                recommendation=recommendation,
                recommendationReason=reason,
            )
        )

    # ---- zero-evidence experiences (known to exist, but not matched here) --
    # Grouped by company (or by title when there's no company) so that multiple
    # zero-evidence Roles at the same Company -- e.g. three separate Grandview
    # Farm roles -- collapse into one experience with one experienceId, instead
    # of colliding on the same auto-derived slug.
    represented_companies = {e.organization for e in experiences if e.organization}
    represented_names = {e.title for e in experiences}
    zero_evidence_groups: dict[str, list[dict]] = {}
    for role in known_roles:
        company = role.get("company")
        title = role["title"]
        if company in education_institutions or company in represented_companies or title in represented_names:
            continue
        zero_evidence_groups.setdefault(company or title, []).append(role)

    for key, roles in zero_evidence_groups.items():
        primary = roles[0]
        company = primary.get("company")
        titles = [r["title"] for r in roles]
        experience_id = _slugify(key)
        experience_type = (
            "volunteer" if any((r.get("employmentType") or "").lower() == "volunteer" for r in roles) else "professional"
        )
        title = titles[0] if len(titles) == 1 else f"{titles[0]} (+{len(titles) - 1} more role(s))"
        experiences.append(
            Experience(
                experienceId=experience_id,
                experienceType=experience_type,
                title=title,
                organization=company,
                startDate=primary.get("startDate"),
                endDate=primary.get("endDate"),
                relevance=RelevanceInfo(score=0.0),
                recommendation="omit",
                recommendationReason="No matched evidence found for the current job description.",
            )
        )
        register([company] + titles, experience_id)

    for name in known_independent_projects:
        if name in represented_names:
            continue
        experience_id = _slugify(name)
        experiences.append(
            Experience(
                experienceId=experience_id,
                experienceType="independent_project",
                title=name,
                relevance=RelevanceInfo(score=0.0),
                recommendation="omit",
                recommendationReason="No matched evidence found for the current job description.",
            )
        )
        register([name], experience_id)

    # ---- recommended order --------------------------------------------------
    recommended_order = [
        e.experienceId
        for e in sorted(experiences, key=lambda e: (-e.relevance.score, e.experienceType != "professional"))
        if e.recommendation != "omit"
    ]

    # ---- requirements summary + transferable evidence flat list -----------
    def _resolve_experience(ev: Evidence) -> Optional[str]:
        # ev.project/.role/.education cover Project- and Role-sourced evidence, and
        # Project-sourced evidence PART_OF an Education. An Education-sourced
        # evidence item (e.g. "debugging" via 42 Prague's own LEARNED edge) has
        # none of those set -- its own institution name lives in ev.source instead.
        for name in (ev.project, ev.role, ev.education):
            if name and name in name_to_experience_id:
                return name_to_experience_id[name]
        if ev.sourceType == "Education" and ev.source in name_to_experience_id:
            return name_to_experience_id[ev.source]
        return None

    def _requirement_summary(m: RequirementMatch, status: LiteralStatus) -> RequirementSummary:
        return RequirementSummary(
            requirement=m.requirement,
            skillQuery=m.skillQuery,
            importance=m.importance,
            category=m.category,
            matchType=m.matchType,
            matchConfidence=m.confidence,
            recommendation=m.recommendation,
            literalStatus=status,
            evidence=[
                RequirementEvidenceRef(
                    experience=_resolve_experience(ev), sourceType=ev.sourceType,
                    evidenceStrength=ev.evidenceStrength, professionalContext=ev.professionalContext,
                )
                for ev in m.evidence
            ],
            transferableEvidence=[
                TransferableRef(
                    capability=t.canonicalSkill or t.capabilityQuery,
                    experience=_resolve_experience(t.evidence[0]) if t.evidence else None,
                    reason=t.reason,
                    evidenceStrength=t.evidence[0].evidenceStrength if t.evidence else None,
                )
                for t in m.transferableEvidence
            ],
        )

    requirements = RequirementsSummary(
        matched=[_requirement_summary(m, "matched") for m in cv_context.matchedRequirements],
        partial=[_requirement_summary(m, "partial") for m in cv_context.partialRequirements],
        gaps=[_requirement_summary(m, "gap") for m in cv_context.gaps],
    )

    transferable_flat: list[TransferableEvidenceEntry] = []
    for bucket, status in ((cv_context.gaps, "gap"), (cv_context.partialRequirements, "partial"), (cv_context.matchedRequirements, "matched")):
        for m in bucket:
            for t in m.transferableEvidence:
                transferable_flat.append(
                    TransferableEvidenceEntry(
                        requirement=m.requirement,
                        literalStatus=status,
                        capability=t.canonicalSkill or t.capabilityQuery,
                        experience=_resolve_experience(t.evidence[0]) if t.evidence else None,
                        reason=t.reason,
                    )
                )

    # ---- relevant skills + languages ---------------------------------------
    literal_skill_names = {
        m.canonicalSkill for m in cv_context.matchedRequirements + cv_context.partialRequirements if m.canonicalSkill
    }
    transferable_skill_names = {
        t.canonicalSkill
        for m in cv_context.matchedRequirements + cv_context.partialRequirements + cv_context.gaps
        for t in m.transferableEvidence
        if t.canonicalSkill
    } - literal_skill_names

    relevant_skills = [
        RelevantSkill(name=s.name, displayName=s.displayName, category=s.category, relevance="literal")
        for s in cv_context.skills
        if s.name in literal_skill_names and s.category != "language"
    ] + [
        RelevantSkill(name=name, displayName=skills_by_name[name].displayName, category=skills_by_name[name].category, relevance="transferable")
        for name in sorted(transferable_skill_names)
        if name in skills_by_name and skills_by_name[name].category != "language"
    ]

    languages: list[LanguageEvidence] = []
    for name in sorted(language_names):
        evidence_labels = sorted({s.label for s in stories if name in s.directSkills})
        languages.append(LanguageEvidence(name=skills_by_name[name].displayName, proficiency=None, evidence=evidence_labels))

    strongest_themes = _strongest_themes(literal_skill_names | {s.name for s in cv_context.skills if s.category != "language"})

    return CVEvidencePackage(
        experiences=experiences,
        recommendedExperienceOrder=recommended_order,
        requirements=requirements,
        transferableEvidence=transferable_flat,
        relevantSkills=relevant_skills,
        languages=languages,
        strongestThemes=strongest_themes,
    )


# --------------------------------------------------------------------------
# Tiny, clearly-scoped supplementary Neo4j lookups (read-only) -- see module
# docstring for why these two exist. Used only by the CLI below.
# --------------------------------------------------------------------------


def fetch_achievement_metadata(session, descriptions: list[str]) -> dict[str, dict]:
    if not descriptions:
        return {}
    rows = session.run(
        "MATCH (a:Achievement) WHERE a.description IN $descriptions "
        "RETURN a.achievementId AS achievementId, a.description AS description, a.metric AS metric",
        descriptions=descriptions,
    ).data()
    return {row["description"]: row for row in rows}


def fetch_known_roles(session) -> list[dict]:
    rows = session.run(
        "MATCH (:Person)-[:HAD_ROLE]->(r:Role) "
        "OPTIONAL MATCH (r)-[:AT]->(c:Company) "
        "RETURN r.title AS title, c.name AS company, r.employmentType AS employmentType, "
        "r.startDate AS startDate, r.endDate AS endDate"
    ).data()
    return rows


def fetch_known_independent_projects(session) -> list[str]:
    rows = session.run(
        "MATCH (:Person)-[:BUILT]->(p:Project) "
        "WHERE NOT (p)-[:PART_OF]->(:Education) AND NOT (p)-[:DURING]->(:Role) "
        "RETURN p.name AS name"
    ).data()
    return [row["name"] for row in rows]


def fetch_known_curriculum_projects(session) -> dict[str, list[str]]:
    rows = session.run(
        "MATCH (p:Project)-[:PART_OF]->(e:Education) RETURN e.institution AS institution, p.name AS name"
    ).data()
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(row["institution"], []).append(row["name"])
    return out


def _collect_achievement_descriptions(cv_context: CVContext) -> list[str]:
    descriptions: set[str] = set()
    for s in cv_context.evidenceStories:
        descriptions.update(a.description for a in s.strongestAchievements)
    return sorted(descriptions)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python cv_evidence.py output/cv_context.json", file=sys.stderr)
        return 1

    context_path = Path(sys.argv[1])
    if not context_path.exists():
        print(f"ERROR: {context_path} not found", file=sys.stderr)
        return 1

    cv_context = CVContext.model_validate_json(context_path.read_text(encoding="utf-8"))

    from neo4j import GraphDatabase
    from setup_schema import load_env

    root = Path(__file__).resolve().parent.parent
    env = load_env(root / ".env")
    driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))
    try:
        with driver.session(database=env["NEO4J_DATABASE"], default_access_mode="READ") as session:
            achievement_metadata = fetch_achievement_metadata(session, _collect_achievement_descriptions(cv_context))
            known_roles = fetch_known_roles(session)
            known_independent_projects = fetch_known_independent_projects(session)
            known_curriculum_projects = fetch_known_curriculum_projects(session)
    finally:
        driver.close()

    package = build_cv_evidence(
        cv_context,
        achievement_metadata=achievement_metadata,
        known_roles=known_roles,
        known_independent_projects=known_independent_projects,
        known_curriculum_projects=known_curriculum_projects,
    )

    out_path = context_path.parent / "cv_evidence.json"
    out_path.write_text(json.dumps(package.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out_path}")
    print(f"  experiences={len(package.experiences)} recommendedOrder={package.recommendedExperienceOrder}")
    print("(read-only -- nothing written to Neo4j; no CV prose generated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
