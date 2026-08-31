"""CLI: load an already-extracted data/requirements.json and match it against
CareerGraph.

Extraction is NOT automated in this prototype. The pipeline is:

    data/jobs.txt --(Claude Code reads it, writes data/requirements.json by hand,
                following the RequirementList schema)--> data/requirements.json
    data/requirements.json --(this script)--> match_requirements() --> Neo4j evidence

This script only performs the second half: load + validate the JSON against
the RequirementList schema, run the read-only Neo4j matching query, and print
the results grouped into MATCHED / PARTIAL / GAPS. It never writes to Neo4j
and never calls any LLM API.

Usage:
    python match_job.py data/requirements.json
"""
import json
import sys
from pathlib import Path

from matching import match_requirements
from requirement_schema import MatchResult, RequirementList


def load_requirements(path: Path) -> RequirementList:
    data = json.loads(path.read_text(encoding="utf-8"))
    return RequirementList.model_validate(data)


def bucket(
    results: list[MatchResult],
) -> tuple[list[MatchResult], list[MatchResult], list[MatchResult]]:
    matched = [r for r in results if r.hasEvidence and r.matchConfidence == "high"]
    partial = [r for r in results if r.hasEvidence and r.matchConfidence == "low"]
    gaps = [r for r in results if not r.hasEvidence]
    return matched, partial, gaps


def print_result(r: MatchResult) -> None:
    print(f"  [{r.importance:<9}] {r.raw}")
    print(
        f"      skillQuery: {r.skillQuery!r}  ->  canonicalSkill: {r.canonicalSkill!r}"
        f"  (score={r.matchScore}, confidence={r.matchConfidence})"
    )
    if r.evidence:
        for e in r.evidence:
            print(f"      evidence: {e.sourceType} '{e.source}' --{e.relationship}-->")
    else:
        reason = (
            "no matching skill in graph"
            if r.canonicalSkill is None
            else "skill exists but no evidence recorded"
        )
        print(f"      evidence: none ({reason})")
    print()


def print_section(title: str, results: list[MatchResult]) -> None:
    print("=" * 60)
    print(f"{title} ({len(results)})")
    print("=" * 60)
    if not results:
        print("  (none)\n")
        return
    for r in results:
        print_result(r)


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python match_job.py data/requirements.json", file=sys.stderr)
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

    print_section("MATCHED REQUIREMENTS", matched)
    print_section("PARTIAL / LOW-CONFIDENCE MATCHES", partial)
    print_section("GAPS", gaps)

    total = len(results)
    print(
        f"Summary: {len(matched)}/{total} matched, "
        f"{len(partial)}/{total} partial, {len(gaps)}/{total} gaps."
    )
    print("(read-only — nothing written to Neo4j)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
