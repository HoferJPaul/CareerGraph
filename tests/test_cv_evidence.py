"""Regression tests for the evidence-package builder (cv_evidence.py) against the
real data/jobs.txt / data/requirements.json pipeline. Hits the real (read-only)
Neo4j Aura instance -- no mocking layer, same convention as matching.py / test_tailor_cv.py /
test_capability_suggest.py / test_cv_writer.py / backend/test_api.py.

Usage:
    python tests/test_cv_evidence.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from match_job import bucket, load_requirements
from matching import match_requirements
from setup_schema import load_env
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
from cv_evidence import (
    CVEvidencePackage,
    build_cv_evidence,
    fetch_achievement_metadata,
    fetch_known_curriculum_projects,
    fetch_known_independent_projects,
    fetch_known_roles,
    _collect_achievement_descriptions,
)
from neo4j import GraphDatabase


def build_package() -> CVEvidencePackage:
    requirements = load_requirements(ROOT / "data" / "requirements.json")
    results = match_requirements(requirements, project_root=ROOT)
    matched, partial, gaps = bucket(results)
    aggregated = aggregate_evidence(matched + partial)

    project_names = sorted({name for (kind, name) in aggregated if kind == "Project"})
    role_names = sorted({name for (kind, name) in aggregated if kind == "Role"})
    education_names = sorted({name for (kind, name) in aggregated if kind == "Education"})
    skill_names = sorted(
        {r.canonicalSkill for r in results if r.hasEvidence and r.canonicalSkill and r.recommendation != "exclude"}
    )

    env = load_env(ROOT / ".env")
    driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))
    try:
        with driver.session(database=env["NEO4J_DATABASE"], default_access_mode="READ") as session:
            project_details = fetch_project_details(session, project_names)
            role_details = fetch_role_details(session, role_names)
            education_details = fetch_education_details(session, education_names)
            skill_details = fetch_skill_details(session, skill_names)

            cv_context = CVContext(
                requirements=[r.model_dump() for r in requirements.requirements],
                matchedRequirements=[to_requirement_match(r) for r in matched],
                partialRequirements=[to_requirement_match(r) for r in partial],
                gaps=[to_requirement_match(r) for r in gaps],
                evidenceStories=finalize_evidence_stories(aggregated, project_details, role_details, education_details),
                skills=build_skills(results, skill_details),
                cvGuidance=CV_GUIDANCE,
            )

            achievement_metadata = fetch_achievement_metadata(session, _collect_achievement_descriptions(cv_context))
            known_roles = fetch_known_roles(session)
            known_independent_projects = fetch_known_independent_projects(session)
            known_curriculum_projects = fetch_known_curriculum_projects(session)
    finally:
        driver.close()

    return build_cv_evidence(
        cv_context,
        achievement_metadata=achievement_metadata,
        known_roles=known_roles,
        known_independent_projects=known_independent_projects,
        known_curriculum_projects=known_curriculum_projects,
    )


_PACKAGE_CACHE: list[CVEvidencePackage] = []


def get_package() -> CVEvidencePackage:
    if not _PACKAGE_CACHE:
        _PACKAGE_CACHE.append(build_package())
    return _PACKAGE_CACHE[0]


def _by_id(pkg: CVEvidencePackage, experience_id: str):
    return next((e for e in pkg.experiences if e.experienceId == experience_id), None)


def test_databridge_grouped_under_werchota_professional_experience() -> None:
    pkg = get_package()
    werchota = _by_id(pkg, "werchota-ai")
    assert werchota is not None
    assert werchota.experienceType == "professional"
    assert werchota.organization == "Werchota.ai"
    project_names = {p.name for p in werchota.projects}
    assert any("DataBridge" in name for name in project_names)
    # DataBridge must not also appear as its own independent top-level experience.
    assert _by_id(pkg, "databridge") is None
    assert not any("DataBridge" in e.title for e in pkg.experiences if e.experienceId != "werchota-ai")


def test_42_projects_grouped_under_42_prague_education() -> None:
    pkg = get_package()
    prague = _by_id(pkg, "42-prague")
    assert prague is not None
    assert prague.experienceType == "education"
    assert prague.institution == "42 Prague"
    # Curriculum projects not directly relevant to this JD still surface, just
    # in the lower-priority reference field rather than a top-level section.
    assert "Libft" in prague.referenceProjects
    assert "cub3D" in prague.referenceProjects
    assert not any(e.title in ("Libft", "cub3D") for e in pkg.experiences if e.experienceId != "42-prague")


def test_student_tutor_nested_not_standalone() -> None:
    pkg = get_package()
    assert _by_id(pkg, "student-tutor") is None
    assert not any(e.title == "Student Tutor" for e in pkg.experiences)
    prague = _by_id(pkg, "42-prague")
    assert any(r.title == "Student Tutor" for r in prague.roles)


def test_el_compas_is_independent_project() -> None:
    pkg = get_package()
    elcompas = _by_id(pkg, "el-compas")
    assert elcompas is not None
    assert elcompas.experienceType == "independent_project"
    assert elcompas.recommendation == "include"


def test_der_hutterer_weg_language_evidence_not_mandatory() -> None:
    pkg = get_package()
    hutterer = _by_id(pkg, "der-hutterer-weg")
    assert hutterer is not None
    assert hutterer.recommendation == "optional"
    assert "german" in hutterer.skills
    german = next((lang for lang in pkg.languages if lang.name == "German"), None)
    assert german is not None
    assert german.proficiency is None  # never invented
    assert "Der Hutterer Weg" in german.evidence


def test_literal_gaps_remain_gaps() -> None:
    pkg = get_package()
    gap_queries = {g.skillQuery for g in pkg.requirements.gaps}
    assert "cloudwatch" in gap_queries
    assert "express" in gap_queries
    for g in pkg.requirements.gaps:
        assert g.literalStatus == "gap"
        assert g.evidence == []


def test_transferable_evidence_stays_separate_from_matches() -> None:
    pkg = get_package()
    matched_queries = {m.skillQuery for m in pkg.requirements.matched}
    assert "cloudwatch" not in matched_queries, "transferable evidence must never promote a literal gap to a match"
    cloudwatch = next(g for g in pkg.requirements.gaps if g.skillQuery == "cloudwatch")
    assert len(cloudwatch.transferableEvidence) > 0
    for t in cloudwatch.transferableEvidence:
        assert t.capability != "cloudwatch"
    flat_capabilities = {t.capability for t in pkg.transferableEvidence if t.requirement == cloudwatch.requirement}
    assert flat_capabilities  # also exposed in the flat top-level view


def test_every_achievement_has_provenance() -> None:
    pkg = get_package()
    all_achievements = [a for e in pkg.experiences for a in e.achievements]
    assert all_achievements
    for a in all_achievements:
        assert a.provenance is not None
        assert a.provenance.sourceType


def test_every_experience_has_inclusion_guidance() -> None:
    pkg = get_package()
    assert pkg.experiences
    for e in pkg.experiences:
        assert e.recommendation in ("include", "optional", "omit")
        assert e.recommendationReason


def test_no_cv_prose_generated() -> None:
    pkg = get_package()
    raw = json.dumps(pkg.model_dump())
    for forbidden in ("# Paul Hofer", "## Experience", "## Profile", "```markdown", "<html"):
        assert forbidden not in raw
    # The package is a data structure, not a document -- there is no field
    # anywhere named for a headline/profile paragraph/rendered document.
    dumped = pkg.model_dump()
    assert "headline" not in dumped
    assert "profile" not in dumped
    assert "markdown" not in dumped


if __name__ == "__main__":
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    failures = 0
    for test in tests:
        try:
            test()
            print(f"PASS {test.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
    print()
    print("All test_cv_evidence.py checks passed." if failures == 0 else f"{failures} check(s) failed.")
    raise SystemExit(1 if failures else 0)
