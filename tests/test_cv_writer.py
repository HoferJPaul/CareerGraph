"""Regression tests for the CV-generation layer (cv_writer.py + backend/cv_markdown.py)
against the real data/jobs.txt / data/requirements.json pipeline. Hits the real
(read-only) Neo4j Aura instance -- no mocking layer, same convention as matching.py /
test_tailor_cv.py / test_capability_suggest.py / backend/test_api.py.

Exercises the exact same path backend/routes/cv.py runs live:

    data/requirements.json --(matching.match_requirements)--> MatchResult[]
                       --(tailor_cv aggregation)--> CVContext
                       --(cv_writer.DeterministicCVWriter)--> StructuredCV
                       --(backend/cv_markdown.render)--> Markdown

Usage:
    python tests/test_cv_writer.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "backend"))

from match_job import bucket, load_requirements
from matching import match_requirements
from setup_schema import load_env
from tailor_cv import (
    CVContext,
    aggregate_evidence,
    build_skills,
    fetch_education_details,
    fetch_project_details,
    fetch_role_details,
    fetch_skill_details,
    finalize_evidence_stories,
    to_requirement_match,
    CV_GUIDANCE,
)
from cv_writer import DeterministicCVWriter
from cv_markdown import render as render_markdown
from neo4j import GraphDatabase

_BANNED_PHRASES = [
    "Note on role alignment",
    "Skills used:",
    "Evidence-backed match",
    "no evidence found",
    "match-confidence",
    "matchConfidence",
    "CareerGraph found",
]
_UNSUPPORTED_TECH = ["aws", "express", "sentry", "cloudwatch"]


def build_structured_cv():
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
    finally:
        driver.close()

    evidence_stories = finalize_evidence_stories(aggregated, project_details, role_details, education_details)
    skills = build_skills(results, skill_details)

    cv_context = CVContext(
        requirements=[r.model_dump() for r in requirements.requirements],
        matchedRequirements=[to_requirement_match(r) for r in matched],
        partialRequirements=[to_requirement_match(r) for r in partial],
        gaps=[to_requirement_match(r) for r in gaps],
        evidenceStories=evidence_stories,
        skills=skills,
        cvGuidance=CV_GUIDANCE,
    )
    structured = DeterministicCVWriter().write(cv_context)
    markdown = render_markdown(structured, "modern")
    return structured, markdown


def test_no_gap_analysis_section() -> None:
    _, markdown = build_structured_cv()
    assert "Note on role alignment" not in markdown


def test_no_skills_used_label() -> None:
    _, markdown = build_structured_cv()
    assert "Skills used:" not in markdown


def test_no_internal_match_language() -> None:
    _, markdown = build_structured_cv()
    for phrase in _BANNED_PHRASES:
        assert phrase.lower() not in markdown.lower(), f"internal/system phrase leaked into CV: {phrase!r}"


def test_unsupported_technologies_never_claimed() -> None:
    _, markdown = build_structured_cv()
    lowered = markdown.lower()
    for tech in _UNSUPPORTED_TECH:
        assert tech not in lowered, f"{tech!r} has no CareerGraph evidence and must never appear in the CV"


def test_student_tutor_is_not_a_standalone_section() -> None:
    structured, markdown = build_structured_cv()
    experience_titles = {e.title for e in structured.experience}
    project_names = {p.name for p in structured.projects}
    assert "Student Tutor" not in experience_titles
    assert "Student Tutor" not in project_names
    # It should still be represented -- folded as a bullet under 42 Prague.
    assert "Student Tutor" in markdown


def test_german_under_languages_without_der_hutterer_weg_section() -> None:
    structured, _ = build_structured_cv()
    assert "German" in structured.languages
    project_names = {p.name for p in structured.projects}
    assert "Der Hutterer Weg" not in project_names, (
        "a project whose only relevant evidence is a language skill must not get its own section"
    )


def test_databridge_professional_experience_is_prioritized() -> None:
    structured, markdown = build_structured_cv()
    assert len(structured.experience) >= 1
    assert any("Werchota.ai" == e.organization for e in structured.experience)
    # Experience section must render before Projects/Education in the document.
    assert markdown.index("## Experience") < markdown.index("## Projects")
    assert markdown.index("## Experience") < markdown.index("## Education")


def test_el_compas_is_included() -> None:
    structured, _ = build_structured_cv()
    assert any(p.name == "El Compas" for p in structured.projects)


def test_42_prague_represented_coherently() -> None:
    structured, _ = build_structured_cv()
    prague_entries = [e for e in structured.education if e.institution == "42 Prague"]
    assert len(prague_entries) == 1, "42 Prague must appear exactly once, not split into multiple sections"
    assert len(prague_entries[0].bullets) >= 1


def test_every_bullet_has_evidence_provenance() -> None:
    structured, _ = build_structured_cv()
    all_bullets = (
        [b for e in structured.experience for b in e.bullets]
        + [b for p in structured.projects for b in p.bullets]
        + [b for edu in structured.education for b in edu.bullets]
    )
    assert all_bullets, "expected at least one bullet across experience/projects/education"
    for b in all_bullets:
        assert b.evidenceIds, f"bullet has no provenance: {b.text!r}"


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
    print("All test_cv_writer.py checks passed." if failures == 0 else f"{failures} check(s) failed.")
    raise SystemExit(1 if failures else 0)
