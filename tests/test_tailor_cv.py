"""Regression tests for tailor_cv.py's evidence grouping/ranking (Sections 4/5/7
of the pipeline-hardening work). Pure Python against hand-built MatchResult
fixtures -- no Neo4j connection needed, since aggregate_evidence/
finalize_evidence_stories operate entirely on in-memory MatchResult objects.

Usage:
    python tests/test_tailor_cv.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from requirement_schema import Evidence, MatchResult
from tailor_cv import aggregate_evidence, build_skills, finalize_evidence_stories

MVP_ACHIEVEMENT = "Took El Compas from concept to a functioning MVP architecture."
ENGLISH_ACHIEVEMENT = "Delivered specialized English instruction for advanced adult learners."
DATABRIDGE_ACHIEVEMENT = "Integrated Groq and Llama 3.3 70B to convert unstructured documents into structured JSON."


def _mk(**overrides) -> MatchResult:
    base = dict(
        raw="placeholder requirement",
        skillQuery="placeholder",
        importance="required",
        category="technology",
        canonicalSkill="placeholder",
        matchScore=5.0,
        matchConfidence="high",
        matchType="canonical_exact",
        recommendation="include",
        evidence=[],
        hasEvidence=True,
        transferableEvidence=[],
    )
    base.update(overrides)
    return MatchResult(**base)


def build_fixture() -> list[MatchResult]:
    typescript = _mk(
        raw="Solid fundamentals in TypeScript/JavaScript",
        skillQuery="typescript",
        canonicalSkill="typescript",
        evidence=[
            Evidence(
                source="El Compas", sourceType="Project", relationship="USED", project="El Compas",
                professionalContext=False, evidenceStrength="weak",
            )
        ],
    )
    full_stack = _mk(
        raw="Ship features end-to-end",
        skillQuery="full-stack development",
        canonicalSkill="full-stack development",
        evidence=[
            Evidence(
                source=MVP_ACHIEVEMENT, sourceType="Achievement", relationship="DEMONSTRATES",
                project="El Compas", professionalContext=False, evidenceStrength="moderate",
            ),
            Evidence(
                source="El Compas", sourceType="Project", relationship="DEMONSTRATES", project="El Compas",
                professionalContext=False, evidenceStrength="weak",
            ),
        ],
    )
    prompt_engineering = _mk(
        raw="Help build our AI pipeline: prompt engineering",
        skillQuery="prompt engineering",
        canonicalSkill="prompt engineering",
        evidence=[
            Evidence(
                source=DATABRIDGE_ACHIEVEMENT, sourceType="Achievement", relationship="DEMONSTRATES",
                project="DataBridge", professionalContext=True, evidenceStrength="strong",
            )
        ],
    )
    llm_api = _mk(
        raw="First experience with AI tools and LLM APIs",
        skillQuery="llm api",
        importance="preferred",
        canonicalSkill="llm api integration",
        evidence=[
            Evidence(
                source=DATABRIDGE_ACHIEVEMENT, sourceType="Achievement", relationship="DEMONSTRATES",
                project="DataBridge", professionalContext=True, evidenceStrength="strong",
            )
        ],
    )
    excluded_partial = _mk(
        raw="1-3 years as a software engineer",
        skillQuery="professional software engineering experience",
        canonicalSkill="professional communication",
        matchConfidence="low",
        matchType="lexical_single_token",
        recommendation="exclude",
        evidence=[
            Evidence(
                source=ENGLISH_ACHIEVEMENT, sourceType="Achievement", relationship="DEMONSTRATES",
                role="English Teacher", professionalContext=False, evidenceStrength="weak",
            )
        ],
    )
    return [typescript, full_stack, prompt_engineering, llm_api, excluded_partial]


def run() -> None:
    results = build_fixture()
    aggregated = aggregate_evidence(results)

    # 1. The excluded single-token partial must never become a story.
    assert ("Role", "English Teacher") not in aggregated, (
        "regression: recommendation='exclude' evidence must not be aggregated into a story"
    )
    assert len(aggregated) == 2, f"regression: expected exactly 2 stories (El Compas, DataBridge), got {len(aggregated)}"

    stories = finalize_evidence_stories(aggregated, project_details={})

    # 2. El Compas: the Achievement and its parent Project fragment merge into ONE story,
    #    not two/three separate CV candidates.
    el_compas = next(s for s in stories if s.label == "El Compas")
    assert len(el_compas.strongestAchievements) == 1, (
        "regression: El Compas should have exactly 1 distinct achievement, not a duplicate "
        f"fragment per requirement (got {len(el_compas.strongestAchievements)})"
    )
    assert el_compas.strongestAchievements[0].description == MVP_ACHIEVEMENT
    assert "typescript" in el_compas.directSkills, "regression: Project-level USED evidence must surface as directSkills"
    assert set(el_compas.supportsRequirements) == {
        "Solid fundamentals in TypeScript/JavaScript", "Ship features end-to-end",
    }, "regression: one story should accumulate every requirement it supports, not fragment per requirement"

    # 3. DataBridge: two requirements point at the SAME achievement -- must not duplicate it.
    databridge = next(s for s in stories if s.label == "DataBridge")
    assert len(databridge.strongestAchievements) == 1, (
        "regression: the same achievement supporting 2 requirements must appear once, not twice"
    )
    assert databridge.professionalContext is True
    assert databridge.evidenceStrength == "strong"

    # 4. Professional-context ranking advantage: DataBridge (strong, professional) sorts
    #    ahead of El Compas (moderate, personal) once source-type/requirement-breadth tie --
    #    but this is a tiebreaker, not an absolute override (see sort_key in tailor_cv.py).
    labels_in_order = [s.label for s in stories]
    assert labels_in_order.index("DataBridge") < labels_in_order.index("El Compas"), (
        "regression: professional-context achievement should rank ahead of a comparable "
        f"personal-project achievement when source-type and requirement-breadth tie (got {labels_in_order})"
    )

    # 5. The flat skills list must mirror the same exclusion -- it must not leak
    #    "professional communication"-style single-token false positives back in
    #    just because evidenceStories filtered them out.
    skills = build_skills(results, skill_details={})
    skill_names = {s.name for s in skills}
    assert "professional communication" not in skill_names, (
        "regression: build_skills must not resurface a recommendation='exclude' skill"
    )
    assert "prompt engineering" in skill_names, (
        "regression: build_skills must still include legitimately included skills"
    )

    print("All test_tailor_cv.py regression checks passed.")


if __name__ == "__main__":
    run()
