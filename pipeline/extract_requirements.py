"""Explicit checkpoint for the MANUAL (offline / development) extraction workflow.

The web app extracts requirements automatically with Groq (backend/llm/, LLM_PROVIDER=groq).
This CLI belongs to the older manual workflow, kept for offline use and development: there,
Claude Code (or a human) reads the job description and hand-authors data/requirements.json
against the RequirementList schema in requirement_schema.py.

This script does not call any LLM API and does not attempt extraction itself. It only
answers one question -- "is data/requirements.json valid and does it correspond to this
job description?" -- so the pipeline stage is visible and enforced rather than silently
skipped:

    data/jobs.txt (job description)
        -> [THIS CHECKPOINT] -> data/requirements.json (validated against RequirementList)
        -> python pipeline/match_job.py data/requirements.json
        -> python pipeline/tailor_cv.py data/requirements.json
        -> python pipeline/cv_evidence.py output/cv_context.json

Usage:
    python pipeline/extract_requirements.py [path/to/job_description.txt]

Exit code 0: data/requirements.json is schema-valid and matches the given job
             description -- safe to proceed to match_job.py / tailor_cv.py.
Exit code 1: manual extraction is required (missing, invalid, or stale requirements.json).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from pydantic import ValidationError

from llm_provider import same_job_description
from requirement_schema import RequirementList

INSTRUCTIONS = """
MANUAL EXTRACTION REQUIRED
===========================
No LLM API is configured for this project, so this step is intentionally manual.

  1. Read the job description at: {jd_path}
  2. Author {req_path} as a JSON object matching RequirementList in
     requirement_schema.py:

       {{
         "requirements": [
           {{
             "raw": "<exact wording or closest paraphrase from the JD>",
             "skillQuery": "<concise, normalized, lowercase phrase>",
             "importance": "required" | "preferred" | "inferred",
             "category": "technology" | "capability" | "soft_skill" | "domain",
             "relatedCapabilities": []
           }}
         ]
       }}

  3. relatedCapabilities can stay empty -- the capability-suggestion stage fills in
     graph-vocabulary candidates automatically for anything left blank.
  4. Re-run this script to validate the result:
       python pipeline/extract_requirements.py {jd_path}

If Claude Code is available, ask it to do steps 1-2 -- that is the "Claude Code /
manual mode" this pipeline is designed around; it is the high-quality extraction
path until a real LLM API is wired into llm_provider.py.
""".strip()


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    jd_path = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "data" / "jobs.txt"
    req_path = root / "data" / "requirements.json"

    if not jd_path.exists():
        print(f"ERROR: {jd_path} not found", file=sys.stderr)
        return 1

    jd_text = jd_path.read_text(encoding="utf-8")

    if not req_path.exists():
        print(f"No {req_path} found yet.\n")
        print(INSTRUCTIONS.format(jd_path=jd_path, req_path=req_path))
        return 1

    try:
        requirements = RequirementList.model_validate_json(req_path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        print(f"{req_path} exists but does NOT validate against RequirementList:\n{exc}\n")
        print(INSTRUCTIONS.format(jd_path=jd_path, req_path=req_path))
        return 1

    if not _matches(req_path, jd_text):
        print(f"{req_path} is schema-valid, but does not appear to correspond to {jd_path}.\n")
        print(INSTRUCTIONS.format(jd_path=jd_path, req_path=req_path))
        return 1

    print(f"OK: {req_path} is schema-valid and matches {jd_path}.")
    print(f"    {len(requirements.requirements)} requirement(s) -- safe to proceed:")
    print(f"    python pipeline/match_job.py {req_path}")
    print(f"    python pipeline/tailor_cv.py {req_path}")
    return 0


def _matches(req_path: Path, jd_text: str) -> bool:
    """A requirements.json is only trustworthy for THIS job description if it was
    authored against the same jobs.txt -- reuses the exact heuristic ManualFileLLMProvider
    applies live, so the CLI checkpoint and the API can never silently disagree."""
    cached_jd_path = req_path.parent / "jobs.txt"
    if not cached_jd_path.exists():
        return True  # no cached JD to compare against (e.g. a fresh custom JD file)
    cached_jd = cached_jd_path.read_text(encoding="utf-8")
    return same_job_description(cached_jd, jd_text)


if __name__ == "__main__":
    sys.exit(main())
