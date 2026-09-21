"""Orchestration only -- every step calls straight into an existing module (the configured
LLMProvider, pipeline.build_cv_context). No matching/tailoring logic is reimplemented here.

The primary flow, one request:

    jobDescription (raw text)
        -> LLMProvider.extract_requirements()   Groq structured output -> RequirementList (validated)
        -> pipeline.build_cv_context()          capability expansion + Neo4j matching -> CVContext
        -> AnalysisStore                        the CVContext stays SERVER-SIDE under an analysisId

The response carries the CVContext for the Match Review screen and the analysisId the CV step
uses. Nothing is written to disk: there is no shared requirements file that concurrent analyses
could overwrite (the old output/requirements.json is gone).

LLM_PROVIDER=dev selects the explicit development fallbacks (cached/heuristic extraction); the
response labels them (`extraction.devFallback`) so they are never presented as equivalent to a
real extraction. A failed Groq call is an error -- it never silently degrades to those.
"""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from analysis_store import AnalysisStore, get_analysis_store
from api_schemas import AnalyzeRequest, AnalyzeResponse, ExtractionInfo, TokenUsageInfo
from deps import get_session
from llm.errors import InputTooLargeError
from llm.factory import LLMServices, get_llm_services
from llm_provider import ExtractionResult
from pipeline import build_cv_context
from requirement_schema import RequirementList

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
log = logging.getLogger("careergraph.api")

ROOT = Path(__file__).resolve().parent.parent.parent


def extraction_info(extraction: ExtractionResult) -> ExtractionInfo:
    usage = extraction.usage
    return ExtractionInfo(
        provider=extraction.provider,
        model=extraction.model,
        mode=extraction.mode,
        note=extraction.note,
        requirementCount=len(extraction.requirements.requirements),
        tokenUsage=(
            TokenUsageInfo(
                promptTokens=usage.prompt_tokens,
                completionTokens=usage.completion_tokens,
                totalTokens=usage.total_tokens,
            )
            if usage
            else None
        ),
        retried=extraction.retried,
        attempts=extraction.attempts,
        devFallback=extraction.provider != "groq",
    )


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
def analyze_job(
    payload: AnalyzeRequest,
    session=Depends(get_session),
    services: LLMServices = Depends(get_llm_services),
    store: AnalysisStore = Depends(get_analysis_store),
) -> AnalyzeResponse:
    jd = payload.jobDescription.strip()
    if not jd:
        raise HTTPException(status_code=400, detail="jobDescription must not be empty")
    limit = services.settings.max_job_description_chars
    if len(jd) > limit:
        raise InputTooLargeError(f"The job description is longer than the {limit:,}-character limit.")

    # Mandatory extraction stage. This MUST run before any capability expansion or
    # matching happens -- raw job-description text is never an acceptable input to
    # match_requirements(). The assertion below is a defense-in-depth guard, not just
    # documentation: it fails loudly if a future change ever lets an LLMProvider
    # implementation return something other than a validated RequirementList.
    extraction = services.extraction_provider.extract_requirements(jd, session)
    if not isinstance(extraction.requirements, RequirementList):
        raise TypeError(
            "LLMProvider.extract_requirements() must return a validated RequirementList "
            f"(got {type(extraction.requirements).__name__}); refusing to pass raw/untyped "
            "data to match_requirements()."
        )

    cv_context = build_cv_context(extraction.requirements, session, ROOT)
    record = store.put(cv_context)

    # Counts and ids only -- never job-description text or career evidence.
    log.info(
        "analysis_complete analysis_id=%s provider=%s mode=%s requirements=%d matched=%d gaps=%d",
        record.analysis_id, extraction.provider, extraction.mode,
        len(extraction.requirements.requirements),
        len(cv_context.matchedRequirements), len(cv_context.gaps),
    )
    return AnalyzeResponse(
        analysisId=record.analysis_id,
        extraction=extraction_info(extraction),
        requirementCount=len(extraction.requirements.requirements),
        cvContext=cv_context,
    )
