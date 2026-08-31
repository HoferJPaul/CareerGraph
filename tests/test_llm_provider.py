"""Regression tests for the mandatory extraction stage (backend/llm_provider.py).

Locks in the contract the rest of the pipeline depends on: a job description must pass
through LLMProvider.extract_requirements() and come out as a validated RequirementList
(requirement_schema.py) before anything else (capability expansion, matching.py) ever
sees it. Raw text must never reach the matcher.

Usage:
    python tests/test_llm_provider.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "backend"))

from setup_schema import load_env
from requirement_schema import Requirement, RequirementList
from llm_provider import DevLLMProvider, same_job_description
from neo4j import GraphDatabase

JOBS_TXT = (ROOT / "data" / "jobs.txt").read_text(encoding="utf-8")


def _assert_valid_requirement_list(requirements) -> None:
    assert isinstance(requirements, RequirementList), (
        f"extract_requirements() must return a RequirementList, got {type(requirements).__name__} "
        "-- raw text/dicts must never reach match_requirements()"
    )
    for r in requirements.requirements:
        assert isinstance(r, Requirement)


def test_cached_manual_mode_returns_valid_requirement_list() -> None:
    env = load_env(ROOT / ".env")
    driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))
    try:
        with driver.session(database=env["NEO4J_DATABASE"], default_access_mode="READ") as session:
            extraction = DevLLMProvider(ROOT).extract_requirements(JOBS_TXT, session)
    finally:
        driver.close()

    assert extraction.mode == "cached_manual"
    _assert_valid_requirement_list(extraction.requirements)
    assert len(extraction.requirements.requirements) > 0


def test_heuristic_fallback_for_unrecognized_text_returns_valid_requirement_list() -> None:
    unrelated = "We are hiring a pastry chef experienced in laminated dough and viennoiserie."
    env = load_env(ROOT / ".env")
    driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))
    try:
        with driver.session(database=env["NEO4J_DATABASE"], default_access_mode="READ") as session:
            extraction = DevLLMProvider(ROOT).extract_requirements(unrelated, session)
    finally:
        driver.close()

    assert extraction.mode == "heuristic_keyword"
    _assert_valid_requirement_list(extraction.requirements)


def test_same_job_description_helper() -> None:
    assert same_job_description(JOBS_TXT, JOBS_TXT)
    assert same_job_description(JOBS_TXT, f"   {JOBS_TXT}   extra pasted whitespace")
    assert not same_job_description(JOBS_TXT, "a completely unrelated job description")
    assert not same_job_description(JOBS_TXT, "")


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
    print("All test_llm_provider.py checks passed." if failures == 0 else f"{failures} check(s) failed.")
    raise SystemExit(1 if failures else 0)
