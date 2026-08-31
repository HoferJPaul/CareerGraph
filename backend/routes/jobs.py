"""Orchestration only -- every step here calls straight into the existing
root-level pipeline modules (llm_provider, tailor_cv) plus the shared
capability-expansion/matching stage in pipeline.py. No matching/tailoring
logic is reimplemented here.

This is the OPTIONAL/DEV single-shot flow: raw job description straight through
to CVContext in one call, using the heuristic/cached LLMProvider (no real LLM API
configured -- see llm_provider.py). It is NOT the primary presentation flow.
The primary flow instead generates a Claude prompt from the JD, has the user run
it through Claude by hand, and posts Claude's requirements.json to
/api/requirements/analyze (routes/requirements.py) -- which never performs
extraction itself. Both flows share the same second-half pipeline stage
(pipeline.build_cv_context) so they can't drift apart.

    jobDescription (raw text)
        -> llm_provider.extract_requirements()  -> RequirementList (validated)
        -> written to output/requirements.json  (transparency/debugging)
        -> pipeline.build_cv_context()          -> CVContext
"""
import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from api_schemas import AnalyzeRequest, AnalyzeResponse
from deps import get_session
from llm_provider import DevLLMProvider
from pipeline import build_cv_context
from requirement_schema import RequirementList

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

ROOT = Path(__file__).resolve().parent.parent.parent
_provider = DevLLMProvider(ROOT)


@router.get("/demo")
def get_demo_job() -> dict:
    """Demo convenience only: returns the text of jobs.txt so the UI can offer a
    'Load demo job' button that fills the textarea. Does not run analysis --
    the user still clicks a button themselves."""
    demo_path = ROOT / "data" / "jobs.txt"
    if not demo_path.exists():
        raise HTTPException(status_code=404, detail="jobs.txt not found")
    return {"jobDescription": demo_path.read_text(encoding="utf-8")}


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze_job(payload: AnalyzeRequest, session=Depends(get_session)) -> AnalyzeResponse:
    """Optional/dev endpoint: extraction + matching in one call. Not used by the
    primary presentation flow (see module docstring)."""
    jd = payload.jobDescription.strip()
    if not jd:
        raise HTTPException(status_code=400, detail="jobDescription must not be empty")

    # Mandatory extraction stage. This MUST run before any capability expansion or
    # matching happens -- raw job-description text is never an acceptable input to
    # match_requirements(). The assertion below is a defense-in-depth guard, not just
    # documentation: it fails loudly if a future change ever lets an LLMProvider
    # implementation return something other than a validated RequirementList.
    extraction = _provider.extract_requirements(jd, session)
    if not isinstance(extraction.requirements, RequirementList):
        raise TypeError(
            "LLMProvider.extract_requirements() must return a validated RequirementList "
            f"(got {type(extraction.requirements).__name__}); refusing to pass raw/untyped "
            "data to match_requirements()."
        )

    # Persist the extracted requirements for transparency/debugging -- this is what makes
    # the pipeline stage visible end-to-end: Job Description -> requirements.json -> Neo4j
    # Evidence. Written to output/, never to data/requirements.json (the curated reference
    # file ManualFileLLMProvider reads from -- overwriting it here would corrupt the cache).
    requirements_path = ROOT / "output" / "requirements.json"
    requirements_path.parent.mkdir(exist_ok=True)
    requirements_path.write_text(
        json.dumps(extraction.requirements.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    cv_context = build_cv_context(extraction.requirements, session, ROOT)

    return AnalyzeResponse(
        extractionMode=extraction.mode,
        extractionNote=extraction.note,
        requirementCount=len(extraction.requirements.requirements),
        requirementsPath=str(requirements_path.relative_to(ROOT)),
        cvContext=cv_context,
    )
