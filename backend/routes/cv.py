"""Orchestration only. Evidence retrieval already happened (routes/jobs.py ->
tailor_cv.py); this route runs the two remaining stages in order: CV *writing*
(cv_writer.DeterministicCVWriter turns the CVContext into a StructuredCV -- all
selection/merging/bucketing decisions happen here) then CV *rendering*
(cv_markdown.render lays the StructuredCV out as Markdown, with zero
selection decisions of its own). Neither stage is reimplemented in this file.
"""
from fastapi import APIRouter

from api_schemas import CvGenerateRequest, CvGenerateResponse
from cv_markdown import render
from cv_writer import DeterministicCVWriter

router = APIRouter(prefix="/api/cv", tags=["cv"])
_writer = DeterministicCVWriter()


@router.post("/generate", response_model=CvGenerateResponse)
def generate_cv(payload: CvGenerateRequest) -> CvGenerateResponse:
    structured_cv = _writer.write(payload.cvContext)
    markdown = render(structured_cv, payload.template)
    return CvGenerateResponse(markdown=markdown, template=payload.template, structuredCv=structured_cv)
