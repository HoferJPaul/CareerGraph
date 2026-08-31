"""CLI: turn matched requirements into a curated CV evidence package.

    data/requirements.json --(matching.match_requirements, reused as-is)--> MatchResult[]
    MatchResult[] --(this script: dedup, rank, project/skill lookups)--> output/cv_context.json

This script never writes to Neo4j and never calls an LLM API. It performs two
kinds of read-only Neo4j work: (1) requirement matching, by calling straight
into matching.match_requirements() rather than re-implementing it, and (2) a
couple of small supplementary lookups -- Project properties/role-company
context, and Skill displayName/category -- needed to build a CV-ready summary,
using the same driver/session pattern already used throughout this codebase.

The output, output/cv_context.json, is an evidence package for a human or an
LLM (Claude Code) to write the actual tailored CV from. This script generates
no CV prose itself.

Usage:
    python tailor_cv.py data/requirements.json
"""
import json
import sys
from pathlib import Path
from typing import Optional

from neo4j import GraphDatabase
from pydantic import BaseModel

from match_job import bucket, load_requirements
from matching import match_requirements
from metrics import has_quantified_metric
from requirement_schema import (
    Evidence,
    EvidenceStrength,
    Importance,
    MatchResult,
    MatchType,
    Recommendation,
    RequirementCategory,
    SourceType,
    TransferableEvidence,
)
from setup_schema import load_env

SOURCE_TYPE_RANK = {"Achievement": 0, "Project": 1, "Role": 2, "Education": 3}
STRENGTH_RANK = {"strong": 0, "moderate": 1, "weak": 2}


class RequirementMatch(BaseModel):
    requirement: str
    skillQuery: str
    importance: Importance
    category: RequirementCategory
    matchType: MatchType
    canonicalSkill: Optional[str] = None
    confidence: str
    recommendation: Recommendation
    evidence: list[Evidence]
    transferableEvidence: list[TransferableEvidence] = []


class AchievementRef(BaseModel):
    description: str
    matchedSkills: list[str]
    evidenceStrength: EvidenceStrength
    hasMetric: bool


class EvidenceStory(BaseModel):
    """One coherent evidence package per underlying experience (a Project, a
    Role, or an Education entry), instead of leaving a downstream CV-writer to
    notice and merge overlapping fragments itself -- e.g. an Achievement and
    its own parent Project both nominally 'supporting' the same requirement.
    Provenance survives down to each individual achievement.
    """

    label: str
    sourceType: SourceType
    project: Optional[str] = None
    roleTitle: Optional[str] = None
    company: Optional[str] = None
    type: Optional[str] = None
    domain: Optional[str] = None
    context: Optional[str] = None
    description: Optional[str] = None
    personRole: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    location: Optional[str] = None
    workMode: Optional[str] = None
    education: Optional[str] = None
    program: Optional[str] = None
    status: Optional[str] = None
    professionalContext: bool
    evidenceStrength: EvidenceStrength
    directSkills: list[str]
    strongestAchievements: list[AchievementRef]
    supportsRequirements: list[str]
    requiredCount: int


class SkillSummary(BaseModel):
    name: str
    displayName: str
    category: str


class CVGuidance(BaseModel):
    targetingPrinciples: list[str]


class CVContext(BaseModel):
    requirements: list[dict]
    matchedRequirements: list[RequirementMatch]
    partialRequirements: list[RequirementMatch]
    gaps: list[RequirementMatch]
    evidenceStories: list[EvidenceStory]
    skills: list[SkillSummary]
    cvGuidance: CVGuidance


def to_requirement_match(r: MatchResult) -> RequirementMatch:
    return RequirementMatch(
        requirement=r.raw,
        skillQuery=r.skillQuery,
        importance=r.importance,
        category=r.category,
        matchType=r.matchType,
        canonicalSkill=r.canonicalSkill,
        confidence=r.matchConfidence,
        recommendation=r.recommendation,
        evidence=r.evidence,
        transferableEvidence=r.transferableEvidence,
    )


def _group_key(e: Evidence) -> tuple[str, str]:
    """The underlying experience an evidence fragment belongs to: its owning
    Project if resolvable, else its owning Role, else the node itself
    (Education, or the rare Project/Role with neither -- doesn't occur today
    but keeps this total)."""
    if e.project:
        return ("Project", e.project)
    if e.role:
        return ("Role", e.role)
    return (e.sourceType, e.source)


def aggregate_evidence(results: list[MatchResult]) -> dict[tuple[str, str], dict]:
    """Group per-node evidence fragments into one bucket per underlying
    experience. Only evidence from MatchResults with recommendation != "exclude"
    is used -- single-token lexical false positives (see
    matching._recommendation_for) should not become CV-ready evidence at all;
    they remain visible in partialRequirements for transparency but are never
    turned into a story here.
    """
    stories: dict[tuple[str, str], dict] = {}
    for r in results:
        if r.recommendation == "exclude":
            continue
        for e in r.evidence:
            key = _group_key(e)
            story = stories.setdefault(
                key,
                {
                    "anchorSourceType": None,
                    "professionalContext": False,
                    "directSkills": set(),
                    "achievements": {},
                    "supportsRequirements": [],
                    "requiredCount": 0,
                    "strengths": [],
                },
            )
            story["professionalContext"] = story["professionalContext"] or e.professionalContext
            story["strengths"].append(e.evidenceStrength)
            if e.sourceType == "Achievement":
                ach = story["achievements"].setdefault(
                    e.source,
                    {
                        "matchedSkills": set(),
                        "evidenceStrength": e.evidenceStrength,
                        "hasMetric": has_quantified_metric(e.source),
                    },
                )
                if r.canonicalSkill:
                    ach["matchedSkills"].add(r.canonicalSkill)
            else:
                story["anchorSourceType"] = e.sourceType
                if r.canonicalSkill:
                    story["directSkills"].add(r.canonicalSkill)
            if r.raw not in story["supportsRequirements"]:
                story["supportsRequirements"].append(r.raw)
                if r.importance == "required":
                    story["requiredCount"] += 1
    return stories


def finalize_evidence_stories(
    stories: dict[tuple[str, str], dict],
    project_details: dict[str, dict],
    role_details: Optional[dict[str, dict]] = None,
    education_details: Optional[dict[str, dict]] = None,
) -> list[EvidenceStory]:
    role_details = role_details or {}
    education_details = education_details or {}
    out = []
    for (group_type, name), data in stories.items():
        anchor_type = data["anchorSourceType"] or group_type
        if anchor_type == "Project":
            props = project_details.get(name, {})
        elif anchor_type == "Role":
            props = role_details.get(name, {})
        elif anchor_type == "Education":
            props = education_details.get(name, {})
        else:
            props = {}
        best_strength = (
            min(data["strengths"], key=lambda s: STRENGTH_RANK[s]) if data["strengths"] else "weak"
        )
        out.append(
            EvidenceStory(
                label=name,
                sourceType=anchor_type,
                project=name if anchor_type == "Project" else None,
                roleTitle=props.get("roleTitle"),
                company=props.get("company"),
                type=props.get("type"),
                domain=props.get("domain"),
                context=props.get("context"),
                description=props.get("description"),
                personRole=props.get("role"),
                startDate=props.get("startDate"),
                endDate=props.get("endDate"),
                location=props.get("location"),
                workMode=props.get("workMode"),
                education=props.get("education"),
                program=props.get("program"),
                status=props.get("status"),
                professionalContext=data["professionalContext"],
                evidenceStrength=best_strength,
                directSkills=sorted(data["directSkills"]),
                strongestAchievements=[
                    AchievementRef(
                        description=desc,
                        matchedSkills=sorted(a["matchedSkills"]),
                        evidenceStrength=a["evidenceStrength"],
                        hasMetric=a["hasMetric"],
                    )
                    for desc, a in data["achievements"].items()
                ],
                supportsRequirements=data["supportsRequirements"],
                requiredCount=data["requiredCount"],
            )
        )

    def sort_key(story: EvidenceStory):
        # Achievement-level evidence (whichever Project/Role it's grouped
        # under) still outranks a bare Project/Role/Education story with no
        # achievements attached -- preserves the original
        # Achievement > Project > Role > Education ordering.
        min_rank = 0 if story.strongestAchievements else SOURCE_TYPE_RANK.get(story.sourceType, 9)
        any_metric = any(a.hasMetric for a in story.strongestAchievements)
        return (
            min_rank,
            -len(story.supportsRequirements),
            0 if story.requiredCount > 0 else 1,
            # Professional-context/quantified-outcome strength is a real but
            # non-absolute tiebreaker: it only decides ties in source-type and
            # requirement-breadth, so a highly specific personal-project
            # achievement can still outrank vaguer professional evidence.
            STRENGTH_RANK[story.evidenceStrength],
            0 if any_metric else 1,
        )

    return sorted(out, key=sort_key)


def fetch_project_details(session, project_names: list[str]) -> dict[str, dict]:
    """Supplementary Project lookup, extended (additively) with two more read-only
    hops: the professional Role/Company a project was built DURING (already existed),
    and the Education it's PART_OF, if any -- both needed by cv_writer.py to render
    dates/context and to fold a curriculum project's evidence into its Education
    entry instead of giving it its own thin CV section. Neither hop changes which
    evidence is selected or how it's scored (see matching.py) -- this only pulls a
    few more already-existing node properties for already-selected evidence."""
    if not project_names:
        return {}
    rows = session.run(
        "MATCH (p:Project) WHERE p.name IN $names "
        "OPTIONAL MATCH (p)-[:DURING]->(role:Role)-[:AT]->(company:Company) "
        "OPTIONAL MATCH (p)-[:PART_OF]->(edu:Education) "
        "RETURN p, role.title AS roleTitle, company.name AS company, "
        "role.startDate AS roleStartDate, role.endDate AS roleEndDate, "
        "role.workMode AS roleWorkMode, role.location AS roleLocation, "
        "edu.institution AS education",
        names=project_names,
    ).data()
    details = {}
    for row in rows:
        props = dict(row["p"])
        props["roleTitle"] = row["roleTitle"]
        props["company"] = row["company"]
        props["education"] = row["education"]
        if row["roleTitle"]:
            props["startDate"] = props.get("startDate") or row["roleStartDate"]
            props["endDate"] = props.get("endDate") or row["roleEndDate"]
            props["location"] = props.get("location") or row["roleLocation"]
            props["workMode"] = props.get("workMode") or row["roleWorkMode"]
        details[props["name"]] = props
    return details


def fetch_role_details(session, role_titles: list[str]) -> dict[str, dict]:
    """Supplementary Role lookup for evidenceStories anchored directly on a Role
    (e.g. Student Tutor) rather than a Project -- returns the Role's own properties
    (description, dates, location, workMode) plus its Company via AT, so cv_writer.py
    can render dates/context for a bare-Role story without inventing them."""
    if not role_titles:
        return {}
    rows = session.run(
        "MATCH (r:Role) WHERE r.title IN $titles "
        "OPTIONAL MATCH (r)-[:AT]->(c:Company) "
        "RETURN r, c.name AS company",
        titles=role_titles,
    ).data()
    details = {}
    for row in rows:
        props = dict(row["r"])
        props["company"] = row["company"]
        details[props["title"]] = props
    return details


def fetch_education_details(session, institutions: list[str]) -> dict[str, dict]:
    """Supplementary Education lookup for evidenceStories anchored directly on an
    Education node (e.g. 42 Prague) -- returns its own properties (description,
    program, dates, status) so cv_writer.py can render a real curriculum summary
    instead of inventing one."""
    if not institutions:
        return {}
    rows = session.run(
        "MATCH (e:Education) WHERE e.institution IN $names RETURN e",
        names=institutions,
    ).data()
    details = {}
    for row in rows:
        props = dict(row["e"])
        details[props["institution"]] = props
    return details


def fetch_skill_details(session, skill_names: list[str]) -> dict[str, dict]:
    if not skill_names:
        return {}
    rows = session.run(
        "MATCH (s:Skill) WHERE s.name IN $names "
        "RETURN s.name AS name, s.displayName AS displayName, s.category AS category",
        names=skill_names,
    ).data()
    return {r["name"]: r for r in rows}


def build_skills(
    results: list[MatchResult], skill_details: dict[str, dict]
) -> list[SkillSummary]:
    # Mirror the evidenceStories filter: a recommendation="exclude" match (single-token
    # lexical false positive) must not resurface here either, or the flat skill list
    # would quietly undo the exclusion evidenceStories just enforced.
    names = sorted(
        {
            r.canonicalSkill
            for r in results
            if r.hasEvidence and r.canonicalSkill and r.recommendation != "exclude"
        }
    )
    skills = []
    for name in names:
        details = skill_details.get(name)
        if details:
            skills.append(
                SkillSummary(
                    name=name,
                    displayName=details.get("displayName") or name,
                    category=details.get("category") or "",
                )
            )
        else:
            skills.append(SkillSummary(name=name, displayName=name, category=""))
    return skills


CV_GUIDANCE = CVGuidance(
    targetingPrinciples=[
        "Prioritize evidence for required skills.",
        "Use achievements before generic skill claims.",
        "Never claim unsupported technologies.",
        "Do not convert gaps into experience.",
        "Use metrics exactly as provided.",
        "Preserve project/employment provenance.",
        "evidenceStories are already deduplicated per experience -- do not re-split an "
        "achievement back out from its project/role as a separate bullet.",
        "A gap's transferableEvidence describes a RELATED capability only. It may be "
        "mentioned as relevant adjacent experience, but never as satisfying the literal "
        "missing requirement -- the literal tool/technology stays a gap regardless.",
        "matchResult.recommendation is a pre-filter: 'exclude' items (single-token lexical "
        "overlaps) were deliberately left out of evidenceStories and should not be manually "
        "reintroduced without a clear, specific reason.",
    ]
)


def truncate(text: str, width: int = 90) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def print_console_summary(
    requirements_count: int,
    matched: list[MatchResult],
    partial: list[MatchResult],
    gaps: list[MatchResult],
    evidence_stories: list[EvidenceStory],
) -> None:
    print("CareerGraph CV Context")
    print("=" * 23)
    print()
    print(f"Requirements: {requirements_count}")
    print(f"Matched: {len(matched)}")
    print(f"Partial: {len(partial)}")
    print(f"Gaps: {len(gaps)}")
    print()
    print("Top evidence stories:")
    if evidence_stories:
        for i, s in enumerate(evidence_stories[:3], start=1):
            tag = " [professional]" if s.professionalContext else ""
            print(f"  {i}. [{s.sourceType}/{s.evidenceStrength}]{tag} {s.label}")
            for a in s.strongestAchievements[:2]:
                print(f"       - {truncate(a.description)}")
    else:
        print("  (none)")
    print()
    print("Gaps with transferable evidence (related capability, NOT the literal tool):")
    gaps_with_transferable = [g for g in gaps if g.transferableEvidence]
    if gaps_with_transferable:
        for g in gaps_with_transferable:
            caps = ", ".join(t.capabilityQuery for t in g.transferableEvidence)
            print(f"  - {g.skillQuery}: related evidence via [{caps}]")
    else:
        print("  (none)")
    print()
    print("Unsupported requirements (no evidence, no transferable evidence):")
    hard_gaps = [g for g in gaps if not g.transferableEvidence]
    if hard_gaps:
        for g in hard_gaps:
            print(f"  - {g.skillQuery}")
    else:
        print("  (none)")
    print()


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tailor_cv.py data/requirements.json", file=sys.stderr)
        return 1

    req_path = Path(sys.argv[1])
    if not req_path.exists():
        print(f"ERROR: {req_path} not found", file=sys.stderr)
        return 1

    try:
        requirements = load_requirements(req_path)
    except Exception as exc:
        print(
            f"ERROR: {req_path} does not match the RequirementList schema: {exc}",
            file=sys.stderr,
        )
        return 1

    if not requirements.requirements:
        print("No requirements found in input file.")
        return 0

    try:
        results = match_requirements(requirements)
    except Exception as exc:
        print(f"ERROR querying Neo4j: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    matched, partial, gaps = bucket(results)
    aggregated = aggregate_evidence(matched + partial)

    project_names = sorted({name for (kind, name) in aggregated if kind == "Project"})
    role_names = sorted({name for (kind, name) in aggregated if kind == "Role"})
    education_names = sorted({name for (kind, name) in aggregated if kind == "Education"})
    skill_names = sorted(
        {
            r.canonicalSkill
            for r in results
            if r.hasEvidence and r.canonicalSkill and r.recommendation != "exclude"
        }
    )

    root = Path(__file__).resolve().parent.parent
    env = load_env(root / ".env")
    driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))
    try:
        driver.verify_connectivity()
        with driver.session(database=env["NEO4J_DATABASE"], default_access_mode="READ") as session:
            project_details = fetch_project_details(session, project_names)
            role_details = fetch_role_details(session, role_names)
            education_details = fetch_education_details(session, education_names)
            skill_details = fetch_skill_details(session, skill_names)
    finally:
        driver.close()

    evidence_stories = finalize_evidence_stories(aggregated, project_details, role_details, education_details)
    skills = build_skills(results, skill_details)

    context = CVContext(
        requirements=[r.model_dump() for r in requirements.requirements],
        matchedRequirements=[to_requirement_match(r) for r in matched],
        partialRequirements=[to_requirement_match(r) for r in partial],
        gaps=[to_requirement_match(r) for r in gaps],
        evidenceStories=evidence_stories,
        skills=skills,
        cvGuidance=CV_GUIDANCE,
    )

    print_console_summary(len(results), matched, partial, gaps, evidence_stories)

    out_dir = root / "output"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "cv_context.json"
    out_path.write_text(
        json.dumps(context.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {out_path}")
    print("(read-only — nothing written to Neo4j)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
