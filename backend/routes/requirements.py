"""Orchestration only. This is the PRIMARY presentation-flow endpoint: it runs the
second half of the pipeline only, against an already-extracted RequirementList --
uploaded or pasted by the user after Claude produced it from the requirement-
extraction prompt (see routes/jobs.py's module docstring for the full flow).

This route must NEVER perform requirement extraction itself. FastAPI validates the
request body against RequirementList automatically (via requirement_schema.py) --
malformed JSON or a schema mismatch is rejected with 422 before any Neo4j call.
"""
from pathlib import Path

from fastapi import APIRouter, Depends

from api_schemas import RequirementsAnalyzeResponse
from deps import get_session
from pipeline import build_cv_context
from requirement_schema import RequirementList

router = APIRouter(prefix="/api/requirements", tags=["requirements"])

ROOT = Path(__file__).resolve().parent.parent.parent


@router.post("/analyze", response_model=RequirementsAnalyzeResponse)
def analyze_requirements(payload: RequirementList, session=Depends(get_session)) -> RequirementsAnalyzeResponse:
    cv_context = build_cv_context(payload, session, ROOT)
    return RequirementsAnalyzeResponse(
        requirementCount=len(payload.requirements),
        cvContext=cv_context,
    )
