"""Shared second-half pipeline stage: capability expansion -> Neo4j matching ->
evidence aggregation -> CVContext. Takes an ALREADY-EXTRACTED RequirementList and
never performs requirement extraction itself.

Used by both:
  - routes/jobs.py's /api/jobs/analyze (raw JD -> LLMProvider.extract_requirements()
    -> this stage) -- the optional/dev single-shot flow.
  - routes/requirements.py's /api/requirements/analyze (uploaded/pasted Claude
    requirements.json -> this stage directly) -- the primary presentation flow.

Factored out so the two entry points can never drift apart. No matching/tailoring
logic is reimplemented here -- every step calls straight into the existing
pipeline/ modules (capability_suggest, matching, match_job, tailor_cv).
"""
from pathlib import Path

from capability_suggest import suggest_from_context
from match_job import bucket
from matching import match_requirements
from requirement_schema import RelatedCapability, RequirementList
from tailor_cv import (
    CV_GUIDANCE,
    CVContext,
    aggregate_evidence,
    build_skills,
    fetch_education_details,
    fetch_project_details,
    fetch_role_details,
    fetch_skill_details,
    finalize_evidence_stories,
    to_requirement_match,
)


def build_cv_context(requirements: RequirementList, session, root: Path) -> CVContext:
    # Automatic capability-suggestion autofill: for any requirement the extraction
    # step left with no relatedCapabilities, look for graph-vocabulary candidates
    # sharing REAL (non-generic) vocabulary with the LITERAL requirement itself.
    #
    # Deliberately scoped to req.skillQuery, not req.raw: a JD sentence often
    # bundles several distinct literal items together (e.g. "Node/Express on the
    # backend, Postgres, AWS" or "Claude Code or Cursor"), and matching against
    # the whole shared sentence would let one sibling's vocabulary bleed onto
    # another -- e.g. AWS picking up "backend engineering" only because it shares
    # a sentence with "Express", or Cursor picking up "Claude Code" only because
    # it shares a sentence with "Claude Code". Both are explicitly the kind of
    # false positive this pipeline must reject (see matching.py's generic-token
    # regression tests). Scoping to the literal skillQuery alone means this pass
    # mostly finds nothing for genuine tool-name gaps -- correct and honest, since
    # tool names generically share no vocabulary with their capability category.
    # The semantic bridge (e.g. "cloudwatch" -> "observability", no shared
    # vocabulary at all) is what Claude's own extraction (or the cached/manual
    # requirements.json) already carries -- it is never invented here.
    for req in requirements.requirements:
        if req.relatedCapabilities:
            continue
        candidates = suggest_from_context(session, req.skillQuery, top_k=3)
        req.relatedCapabilities = [
            RelatedCapability(
                skillQuery=c["name"],
                reason="Shares specific, non-generic vocabulary with the requirement's own wording.",
                source="graph_vocabulary",
            )
            for c in candidates
            if c["name"] != req.skillQuery
        ]

    results = match_requirements(requirements, project_root=root)
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
    project_details = fetch_project_details(session, project_names)
    role_details = fetch_role_details(session, role_names)
    education_details = fetch_education_details(session, education_names)
    skill_details = fetch_skill_details(session, skill_names)

    evidence_stories = finalize_evidence_stories(aggregated, project_details, role_details, education_details)
    skills = build_skills(results, skill_details)

    return CVContext(
        requirements=[r.model_dump() for r in requirements.requirements],
        matchedRequirements=[to_requirement_match(r) for r in matched],
        partialRequirements=[to_requirement_match(r) for r in partial],
        gaps=[to_requirement_match(r) for r in gaps],
        evidenceStories=evidence_stories,
        skills=skills,
        cvGuidance=CV_GUIDANCE,
    )
