"""Read-only capability-suggestion stage (Neo4j -> candidates -> Claude's semantic pick).

This is NOT part of the automated matching.py/tailor_cv.py pipeline -- it's a standalone
tool run once per JD, while writing requirements.json, replacing what used to be a hand-typed
relatedCapabilities list with a discover-then-verify workflow:

    literal requirement (+ raw JD sentence)
         |
         v
    suggest_from_context()  -- Python/Neo4j: lexical candidates sharing REAL vocabulary
    |                          with the JD sentence (reuses matching.py's own generic-token
    |                          filtering, so no more of a "match" than the literal matcher
    |                          would accept). Catches the rare case where the JD phrasing
    |                          happens to share real vocabulary with a Skill name.
    v
    (Claude reads the requirement, brainstorms plausible underlying capability concepts from
     domain knowledge -- e.g. "CloudWatch" -> observability/monitoring/logging -- since most
     tool -> capability relationships share NO lexical overlap at all and can't be found by
     any string-similarity method)
         |
         v
    check_candidates(["observability", "monitoring", ...])  -- Neo4j: confirms which of
    |                                                          Claude's guesses actually exist
    |                                                          as real Skills (name or alias),
    |                                                          rejecting the rest outright
    v
    Claude keeps only the confirmed, genuinely-relevant ones (max 3), writes them into
    requirements.json as RelatedCapability{skillQuery, reason, source}

Nothing here writes to Neo4j, and nothing here invents a Skill name that doesn't already
exist in the graph -- check_candidates is the enforcement point for that rule.

Usage:
    python capability_suggest.py vocabulary [category]
    python capability_suggest.py context "<raw JD sentence>"
    python capability_suggest.py check "<candidate 1>" "<candidate 2>" ...
"""
import sys
from pathlib import Path

from neo4j import GraphDatabase

from matching import (
    _CANDIDATE_LIMIT,
    _escape_lucene_query,
    _meaningful_overlap,
    _resolve_candidate,
)
from setup_schema import load_env

VOCABULARY_QUERY = """
MATCH (s:Skill)
WHERE $category IS NULL OR s.category = $category
RETURN s.name AS name, s.displayName AS displayName, s.aliases AS aliases, s.category AS category
ORDER BY s.name
""".strip()


def _session():
    root = Path(__file__).resolve().parent.parent
    env = load_env(root / ".env")
    driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))
    driver.verify_connectivity()
    return driver, driver.session(database=env["NEO4J_DATABASE"], default_access_mode="READ")


def list_vocabulary(session, category: str | None = None) -> list[dict]:
    """Full (or category-filtered) read-only dump of the Skill vocabulary: canonical
    name, displayName, aliases, category. Small and bounded (a few hundred rows at
    most) -- meant to be scanned once per JD, not per requirement."""
    return session.run(VOCABULARY_QUERY, category=category).data()


def suggest_from_context(session, raw_text: str, top_k: int = 3) -> list[dict]:
    """Graph-side candidate generation: find Skills sharing REAL (non-generic)
    vocabulary with the JD sentence around a requirement, using the exact same
    fulltext + meaningful-overlap filtering matching.py applies to literal
    requirements. This is deliberately conservative -- raw Lucene score alone is
    never treated as relevance (see matching.py's docstring); a candidate must
    survive the generic-token filter to be returned at all.

    Most literal tool -> capability relationships (CloudWatch -> observability,
    Express -> backend engineering) share NO vocabulary with the JD sentence and
    will correctly return nothing here -- that gap is what Claude's semantic step
    (check_candidates) is for.
    """
    lucene_query = _escape_lucene_query(raw_text)
    rows = session.run(
        "CALL db.index.fulltext.queryNodes('skill_search', $luceneQuery) YIELD node, score "
        "WITH node, score ORDER BY score DESC LIMIT $limit "
        "RETURN collect({name: node.name, aliases: node.aliases, displayName: node.displayName, "
        "category: node.category, score: score}) AS candidates",
        luceneQuery=lucene_query,
        limit=_CANDIDATE_LIMIT * 3,
    ).single()
    candidates = rows["candidates"] if rows else []
    out = []
    for c in candidates:
        overlap = _meaningful_overlap(raw_text, c)
        if overlap:
            out.append({**c, "sharedTerms": sorted(overlap)})
    out.sort(key=lambda c: c["score"], reverse=True)
    return out[:top_k]


def check_candidates(session, queries: list[str]) -> list[dict]:
    """Verify a list of Claude-proposed capability names against the graph. Reuses
    matching.py's own candidate-retrieval + exact/lexical resolution so a name only
    counts as "exists" under the exact same rules the real matcher will later apply
    to it -- no separate, looser notion of "exists" is introduced here.

    Returns one row per query: {query, exists, canonicalSkill, displayName, category,
    matchType}. exists=False means Claude's guess does not correspond to any real
    CareerGraph Skill and must be dropped, not recorded.
    """
    out = []
    for q in queries:
        lucene_query = _escape_lucene_query(q)
        row = session.run(
            "CALL db.index.fulltext.queryNodes('skill_search', $luceneQuery) YIELD node, score "
            "WITH node, score ORDER BY score DESC LIMIT $limit "
            "RETURN collect({name: node.name, aliases: node.aliases, displayName: node.displayName, "
            "category: node.category, score: score}) AS candidates",
            luceneQuery=lucene_query,
            limit=_CANDIDATE_LIMIT,
        ).single()
        candidates = row["candidates"] if row else []
        skill, score, confidence, match_type = _resolve_candidate(q, candidates)
        exists = skill is not None and match_type in ("canonical_exact", "alias_exact")
        out.append(
            {
                "query": q,
                "exists": exists,
                "canonicalSkill": skill["name"] if skill else None,
                "displayName": skill.get("displayName") if skill else None,
                "category": skill.get("category") if skill else None,
                "matchType": match_type,
            }
        )
    return out


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("  (none)")
        return
    for r in rows:
        print(f"  {r}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 1

    mode = sys.argv[1]
    driver, session = _session()
    try:
        if mode == "vocabulary":
            category = sys.argv[2] if len(sys.argv) > 2 else None
            _print_table(list_vocabulary(session, category))
        elif mode == "context":
            if len(sys.argv) < 3:
                print("Usage: python capability_suggest.py context \"<raw JD sentence>\"", file=sys.stderr)
                return 1
            _print_table(suggest_from_context(session, sys.argv[2]))
        elif mode == "check":
            if len(sys.argv) < 3:
                print("Usage: python capability_suggest.py check \"<candidate>\" [...]", file=sys.stderr)
                return 1
            _print_table(check_candidates(session, sys.argv[2:]))
        else:
            print(f"Unknown mode: {mode!r}. Use vocabulary | context | check.", file=sys.stderr)
            return 1
    finally:
        session.close()
        driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
