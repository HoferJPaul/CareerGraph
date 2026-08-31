"""Regression tests for the capability-suggestion stage (capability_suggest.py) and
for the discipline of requirements.json itself: relatedCapabilities must only ever
contain graph-confirmed, conservative candidates -- never invented, never a broad
generic link, never a same-category-tool coincidence.

Usage:
    python tests/test_capability_suggest.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from capability_suggest import check_candidates, list_vocabulary
from match_job import load_requirements
from setup_schema import load_env
from neo4j import GraphDatabase


def run() -> None:
    env = load_env(ROOT / ".env")
    driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))
    with driver.session(database=env["NEO4J_DATABASE"], default_access_mode="READ") as session:
        # 1. check_candidates must confirm real skills and reject invented/nonsense ones.
        results = {r["query"]: r for r in check_candidates(session, [
            "observability", "application logging", "backend engineering", "api development",
            "quantum flux capacitor tuning", "production monitoring",
        ])}
        assert results["observability"]["exists"], "regression: 'observability' is a real Skill and must resolve"
        assert results["application logging"]["exists"], "regression: 'application logging' is a real Skill"
        assert results["backend engineering"]["exists"], "regression: 'backend engineering' is a real Skill"
        assert results["api development"]["exists"], "regression: 'api development' is a real Skill"
        assert not results["quantum flux capacitor tuning"]["exists"], (
            "regression: a nonsense/invented capability name must never be reported as existing"
        )
        assert not results["production monitoring"]["exists"], (
            "regression: 'production monitoring' does not exist in this graph and must be rejected, "
            "not silently accepted as a near-enough match"
        )

        # 2. Vocabulary listing is read-only and returns the expected shape.
        vocab = list_vocabulary(session, category="capability")
        assert all({"name", "displayName", "aliases", "category"} <= set(row) for row in vocab)
        assert any(row["name"] == "observability" for row in vocab)

    driver.close()

    # 3. requirements.json itself must reflect the conservative rules: Sentry/CloudWatch/
    #    Express get real, existing, narrow capability suggestions; AWS and Cursor -- the
    #    explicit BAD examples (too broad / coincidental tool overlap) -- must stay empty.
    requirements = load_requirements(ROOT / "data" / "requirements.json")
    by_query = {r.skillQuery: r for r in requirements.requirements}

    for q in ("sentry", "cloudwatch"):
        caps = {c.skillQuery for c in by_query[q].relatedCapabilities}
        assert "observability" in caps, f"regression: {q!r} should surface 'observability' as transferable"
        assert len(by_query[q].relatedCapabilities) <= 3, "regression: keep relatedCapabilities small (<=3)"

    express_caps = {c.skillQuery for c in by_query["express"].relatedCapabilities}
    assert "backend engineering" in express_caps or "api development" in express_caps, (
        "regression: 'express' should surface backend/API transferable capabilities"
    )

    assert by_query["aws"].relatedCapabilities == [], (
        "regression: AWS is a broad cloud platform -- must NOT auto-resolve to a generic capability "
        "like 'backend engineering' just because it's broadly cloud/backend-adjacent"
    )
    assert by_query["cursor"].relatedCapabilities == [], (
        "regression: Cursor must not automatically equal Claude Code -- two AI coding tools sharing "
        "a category is not meaningful transferable evidence for each other"
    )

    print("All test_capability_suggest.py regression checks passed.")


if __name__ == "__main__":
    run()
