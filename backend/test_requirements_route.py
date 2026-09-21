"""Tests for the dev/advanced second-half endpoint: /api/requirements/analyze
(routes/requirements.py).

This route must NEVER perform requirement extraction -- these tests lock in the
boundary between "already-extracted requirements" (a caller-supplied RequirementList,
validated by the same schema LLM extraction output passes through) and the raw-JD
/api/jobs/analyze endpoint (routes/jobs.py), which is the application's own flow.

Run from within backend/:
    python test_requirements_route.py
"""
import json
from pathlib import Path

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

BACKEND_DIR = Path(__file__).resolve().parent
ROOT_DIR = BACKEND_DIR.parent
REQUIREMENTS_JSON = json.loads((ROOT_DIR / "data" / "requirements.json").read_text(encoding="utf-8"))


def test_valid_requirements_are_validated_and_matched() -> None:
    resp = client.post("/api/requirements/analyze", json=REQUIREMENTS_JSON)
    assert resp.status_code == 200
    data = resp.json()
    assert data["analysisId"], "the CVContext is stored server-side under an analysisId"
    assert data["requirementCount"] == len(REQUIREMENTS_JSON["requirements"])
    cv = data["cvContext"]
    assert len(cv["matchedRequirements"]) > 0, "matching must actually run from the uploaded requirements"
    assert len(cv["gaps"]) > 0
    assert len(cv["evidenceStories"]) > 0, "cv_context.json's selected evidence must be generated"


def test_raw_job_description_is_rejected_not_matched() -> None:
    """A raw JD -- wrong shape entirely, or the right key with the wrong type --
    must 422 before any Neo4j matching happens. This endpoint only ever accepts an
    already-extracted RequirementList; it must not silently coerce or partially
    accept raw text."""
    resp = client.post("/api/requirements/analyze", json={"jobDescription": "We need a Python engineer."})
    assert resp.status_code == 422
    assert "cvContext" not in resp.text

    resp2 = client.post(
        "/api/requirements/analyze",
        content="just some raw job description text, not even json",
        headers={"Content-Type": "application/json"},
    )
    assert resp2.status_code == 422


def test_invalid_requirement_shape_is_rejected_before_matching() -> None:
    bad = {"requirements": [{"raw": "Postgres", "skillQuery": "postgresql"}]}  # missing importance/category
    resp = client.post("/api/requirements/analyze", json=bad)
    assert resp.status_code == 422
    assert "cvContext" not in resp.text


def test_empty_requirements_list_is_schema_valid_but_matches_nothing() -> None:
    resp = client.post("/api/requirements/analyze", json={"requirements": []})
    assert resp.status_code == 200
    assert resp.json()["cvContext"]["matchedRequirements"] == []


def test_capability_expansion_runs_after_upload() -> None:
    """relatedCapabilities left empty by extraction (the LLM, or a human) must still
    get filled in by this route's own graph-vocabulary capability-expansion stage --
    the exact behavior /api/jobs/analyze gets from the same shared
    pipeline.build_cv_context. "backend development" shares real, non-generic
    vocabulary with the existing "backend engineering" Skill node, so the
    mechanical graph-vocabulary pass (capability_suggest.suggest_from_context) must
    find it on its own -- unlike a semantic bridge (e.g. sentry -> observability),
    which requires world knowledge only the extraction step (the LLM) supplies."""
    payload = {
        "requirements": [
            {
                "raw": "Experience with backend development",
                "skillQuery": "backend development",
                "importance": "required",
                "category": "capability",
                "relatedCapabilities": [],
            }
        ]
    }

    resp = client.post("/api/requirements/analyze", json=payload)
    assert resp.status_code == 200
    req = resp.json()["cvContext"]["requirements"][0]
    caps = {c["skillQuery"] for c in req["relatedCapabilities"]}
    assert "backend engineering" in caps, "capability expansion must run even when the uploaded JSON left it empty"


def test_literal_gap_vs_transferable_evidence_separation() -> None:
    resp = client.post("/api/requirements/analyze", json=REQUIREMENTS_JSON)
    cv = resp.json()["cvContext"]
    gap_queries = {g["skillQuery"] for g in cv["gaps"]}
    matched_queries = {m["skillQuery"] for m in cv["matchedRequirements"]}
    assert "cloudwatch" in gap_queries
    assert "cloudwatch" not in matched_queries, "transferable evidence must never promote a literal gap to a match"

    cloudwatch = next(g for g in cv["gaps"] if g["skillQuery"] == "cloudwatch")
    assert cloudwatch["evidence"] == [], "a gap must have no literal evidence"
    assert len(cloudwatch["transferableEvidence"]) > 0


def test_route_and_pipeline_never_reference_cv_evidence() -> None:
    """The primary flow (jobs.py's extraction hand-off, requirements.py, and the
    shared pipeline stage) must never route through cv_evidence.json -- structural
    guard on the actual source, not just behavior."""
    for path in (BACKEND_DIR / "routes" / "requirements.py", BACKEND_DIR / "pipeline.py"):
        assert "cv_evidence" not in path.read_text(encoding="utf-8"), f"{path.name} must not reference cv_evidence"


def test_cv_evidence_support_is_not_deleted() -> None:
    """cv_evidence.py stays available as an optional/experimental layer -- it must
    exist and remain importable, just outside the primary route above."""
    assert (ROOT_DIR / "pipeline" / "cv_evidence.py").exists()
    assert (ROOT_DIR / "tests" / "test_cv_evidence.py").exists()


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
    print("All test_requirements_route.py checks passed." if failures == 0 else f"{failures} check(s) failed.")
    raise SystemExit(1 if failures else 0)
