"""Orchestration only. Dev/advanced endpoint: runs the SECOND half of the pipeline only --
capability expansion + Neo4j matching -- against an ALREADY-EXTRACTED RequirementList that the
caller supplies (for example one authored by hand or saved from an earlier run).

The application's own flow never uses this: the UI posts a job description to
/api/jobs/analyze, which extracts requirements server-side with the configured LLM provider.

This route must NEVER perform requirement extraction itself. FastAPI validates the request body
against RequirementList automatically (via requirement_schema.py) -- malformed JSON or a schema
mismatch is rejected with 422 before any Neo4j call. That validation boundary is identical to
the one LLM extraction output passes through.
"""
from pathlib import Path

from fastapi import APIRouter, Depends

from analysis_store import AnalysisStore, get_analysis_store
from api_schemas import RequirementsAnalyzeResponse
from deps import get_session
from pipeline import build_cv_context
from requirement_schema import RequirementList

router = APIRouter(prefix="/api/requirements", tags=["requirements"])

ROOT = Path(__file__).resolve().parent.parent.parent


@router.post("/analyze", response_model=RequirementsAnalyzeResponse)
def analyze_requirements(
    payload: RequirementList,
    session=Depends(get_session),
    store: AnalysisStore = Depends(get_analysis_store),
) -> RequirementsAnalyzeResponse:
    cv_context = build_cv_context(payload, session, ROOT)
    record = store.put(cv_context)
    return RequirementsAnalyzeResponse(
        analysisId=record.analysis_id,
        requirementCount=len(payload.requirements),
        cvContext=cv_context,
    )
