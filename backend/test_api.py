"""Backend API tests. Run from within backend/:

    cd backend
    python -m pytest test_api.py -v

or without pytest installed:

    cd backend
    python test_api.py

These hit the real (read-only) Neo4j Aura instance -- there is no mocking layer
anywhere in this codebase (see the existing test_tailor_cv.py / test_capability_
suggest.py / matching.py smoke tests, which do the same). Tests assert
structural/self-consistency properties rather than hardcoded graph counts,
since the graph is allowed to grow over time and these tests must not need
updating every time it does.
"""
import ast
import re
from pathlib import Path

from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

BACKEND_DIR = Path(__file__).resolve().parent
JOBS_TXT = (BACKEND_DIR.parent / "data" / "jobs.txt").read_text(encoding="utf-8")

EXPECTED_LABELS = {"Person", "Role", "Company", "Project", "Education", "Skill", "Achievement"}


def test_health() -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_graph_summary_shape_and_self_consistency() -> None:
    resp = client.get("/api/graph/summary")
    assert resp.status_code == 200
    data = resp.json()

    assert set(data.keys()) == {"totalNodes", "totalRelationships", "labelCounts", "relationshipCounts"}
    assert EXPECTED_LABELS <= set(data["labelCounts"])
    assert all(isinstance(v, int) and v > 0 for v in data["labelCounts"].values())

    # Self-consistency, not hardcoded counts: the graph is allowed to grow.
    assert data["totalNodes"] == sum(data["labelCounts"].values())
    assert data["totalRelationships"] == sum(data["relationshipCounts"].values())
    assert data["labelCounts"]["Person"] == 1


def test_graph_overview_shape() -> None:
    resp = client.get("/api/graph/overview")
    assert resp.status_code == 200
    data = resp.json()

    assert data["person"] is not None
    assert data["person"]["label"] == "Person"
    assert "name" in data["person"]["properties"]

    for key in ("roles", "projects", "education"):
        assert isinstance(data[key], list)
        assert len(data[key]) > 0
        for node in data[key]:
            assert {"id", "label", "key", "properties"} <= set(node)


def test_graph_node_detail() -> None:
    resp = client.get("/api/graph/node/Project/elcompas")
    assert resp.status_code == 200
    data = resp.json()
    assert data["node"]["id"] == "Project:elcompas"
    assert len(data["relationships"]) > 0
    assert all({"direction", "relationshipType", "node"} <= set(r) for r in data["relationships"])


def test_graph_node_not_found() -> None:
    resp = client.get("/api/graph/node/Project/does-not-exist")
    assert resp.status_code == 404


def test_analyze_response_shape() -> None:
    resp = client.post("/api/jobs/analyze", json={"jobDescription": JOBS_TXT})
    assert resp.status_code == 200
    data = resp.json()

    assert data["extractionMode"] in ("cached_manual", "heuristic_keyword")
    assert isinstance(data["extractionNote"], str) and data["extractionNote"]
    assert data["requirementCount"] > 0

    cv = data["cvContext"]
    for key in ("requirements", "matchedRequirements", "partialRequirements", "gaps", "evidenceStories", "skills", "cvGuidance"):
        assert key in cv

    for match in cv["matchedRequirements"] + cv["partialRequirements"] + cv["gaps"]:
        for key in ("requirement", "skillQuery", "matchType", "confidence", "recommendation", "evidence", "transferableEvidence"):
            assert key in match


def test_analyze_rejects_empty_job_description() -> None:
    resp = client.post("/api/jobs/analyze", json={"jobDescription": "   "})
    assert resp.status_code == 400


def test_literal_gap_vs_transferable_evidence_separation() -> None:
    """The core product guarantee: a literal gap (e.g. CloudWatch) may carry
    transferable capability evidence, but must NEVER be reclassified as a
    match because of it."""
    resp = client.post("/api/jobs/analyze", json={"jobDescription": JOBS_TXT})
    data = resp.json()
    cv = data["cvContext"]

    matched_queries = {m["skillQuery"] for m in cv["matchedRequirements"]}
    gap_queries = {g["skillQuery"] for g in cv["gaps"]}

    assert "cloudwatch" in gap_queries, "CloudWatch has no Skill node and must be a gap"
    assert "cloudwatch" not in matched_queries, "transferable evidence must never promote a literal gap to a match"

    cloudwatch = next(g for g in cv["gaps"] if g["skillQuery"] == "cloudwatch")
    assert cloudwatch["evidence"] == [], "a gap must have no literal evidence"
    assert len(cloudwatch["transferableEvidence"]) > 0, "CloudWatch should surface transferable observability evidence"
    for t in cloudwatch["transferableEvidence"]:
        assert t["capabilityQuery"] != "cloudwatch"

    # Every gap in general: literal evidence empty, by construction.
    for g in cv["gaps"]:
        assert g["evidence"] == []
        assert g["confidence"] == "no_match"


def test_cv_generate_is_grounded_in_evidence_stories() -> None:
    analyze_resp = client.post("/api/jobs/analyze", json={"jobDescription": JOBS_TXT})
    cv_context = analyze_resp.json()["cvContext"]

    resp = client.post("/api/cv/generate", json={"cvContext": cv_context, "template": "modern"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["template"] == "modern"
    assert isinstance(data["markdown"], str) and data["markdown"].startswith("#")

    # cv_writer.py deliberately selects/merges/suppresses evidenceStories rather
    # than dumping every one verbatim as its own section (see cv_writer.py) -- so
    # "grounded" means every entry it DID choose to include is both traceable
    # (every bullet keeps evidenceIds) and actually rendered, not that every raw
    # evidenceStory label survives unchanged.
    structured = data["structuredCv"]
    entries = structured["experience"] + structured["projects"] + structured["education"]
    assert entries, "expected at least one CV entry for this job description"
    for entry in entries:
        label = entry.get("title") or entry.get("name") or entry.get("institution")
        assert label in data["markdown"]
        for bullet in entry["bullets"]:
            assert bullet["evidenceIds"], f"bullet has no provenance: {bullet['text']!r}"


_WRITE_CLAUSE_RE = re.compile(r"\b(MERGE|CREATE|DELETE|SET|REMOVE)\b", re.IGNORECASE)


def _string_constants(node: ast.AST):
    """Yield every string literal in a call's arguments, joining adjacent
    implicitly-concatenated string constants and f-string literal segments
    (ignoring the {expr} parts, which can't contain a hardcoded clause)."""
    for arg in list(getattr(node, "args", [])) + [kw.value for kw in getattr(node, "keywords", [])]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            yield arg.value
        elif isinstance(arg, ast.JoinedStr):  # f-string
            yield "".join(v.value for v in arg.values if isinstance(v, ast.Constant))


def test_no_write_cypher_anywhere_in_backend() -> None:
    """Static guard: every Cypher string passed to a `.run(...)` call anywhere
    in backend/ (this test file excluded) must be free of write-capable
    clauses. Scoped to actual query-call arguments via the AST -- not a raw
    text scan -- so this can't false-positive on the English word "set" in a
    docstring/comment, and it directly enforces the read-only constraint at
    the one place a write could ever be introduced.
    """
    offenders = []
    for path in BACKEND_DIR.rglob("*.py"):
        if path.name == "test_api.py" or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run"):
                continue
            for text in _string_constants(node):
                match = _WRITE_CLAUSE_RE.search(text)
                if match:
                    offenders.append(f"{path.relative_to(BACKEND_DIR)}: {match.group(0)!r} in {text!r}")
    assert not offenders, f"write-capable Cypher keywords found in a .run(...) query in backend/: {offenders}"


def test_get_session_is_read_only() -> None:
    deps_source = (BACKEND_DIR / "deps.py").read_text(encoding="utf-8")
    assert 'default_access_mode="READ"' in deps_source, (
        "the shared session dependency must open every session in READ mode"
    )


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
    print("All backend API tests passed." if failures == 0 else f"{failures} test(s) failed.")
    raise SystemExit(1 if failures else 0)
