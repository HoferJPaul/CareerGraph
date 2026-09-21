"""Orchestration only. Evidence retrieval already happened (routes/jobs.py -> pipeline.py);
this route runs the remaining stages, in order:

    AnalysisStore lookup      the CVContext THIS SERVER built -- never one sent by the browser
      -> writer.generate()    GroqCVWriter (production) or the labelled deterministic fallback
      -> provenance validation  reject, never repair (llm/provenance.py)
      -> cv_markdown.render()   deterministic layout of the validated StructuredCV

All of that sequencing lives in cv_generation.build_cv_document, so the renderer can only ever
receive a StructuredCV that passed validation. Neither stage is reimplemented in this file.
"""
from fastapi import APIRouter, Depends, HTTPException

from analysis_store import AnalysisStore, get_analysis_store
from api_schemas import (
    BulletProvenance,
    CvGenerateRequest,
    CvGenerateResponse,
    EvidenceRef,
    GenerationInfo,
    TokenUsageInfo,
)
from cv_generation import CvDocument, build_cv_document
from llm.evidence_registry import EvidenceItem
from llm.factory import LLMServices, get_llm_services
from llm.provenance import BulletTrace

router = APIRouter(prefix="/api/cv", tags=["cv"])


def _evidence_ref(item: EvidenceItem) -> EvidenceRef:
    owner = item.owner.removeprefix("story:") if item.owner else None
    label = (owner or item.text) if item.kind == "story" else item.text
    return EvidenceRef(
        label=label,
        kind=item.kind,
        sourceType=item.source_type,
        owner=owner,
        transferableFor=item.transferable_for,
        relatedGaps=list(item.gaps),
    )


def _provenance(trace: BulletTrace) -> BulletProvenance:
    return BulletProvenance(
        section=trace.section,
        entry=trace.entry,
        text=trace.text,
        evidence=[_evidence_ref(item) for item in trace.evidence],
    )


def _generation_info(document: CvDocument) -> GenerationInfo:
    out = document.output
    usage = out.usage
    return GenerationInfo(
        provider=out.provider,
        model=out.model,
        mode=out.mode,
        devFallback=out.provider != "groq",
        tokenUsage=(
            TokenUsageInfo(
                promptTokens=usage.prompt_tokens,
                completionTokens=usage.completion_tokens,
                totalTokens=usage.total_tokens,
            )
            if usage
            else None
        ),
        retried=out.retried,
        attempts=out.attempts,
    )


@router.post("/generate", response_model=CvGenerateResponse)
def generate_cv(
    payload: CvGenerateRequest,
    services: LLMServices = Depends(get_llm_services),
    store: AnalysisStore = Depends(get_analysis_store),
) -> CvGenerateResponse:
    record = store.get(payload.analysisId)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "analysis_not_found",
                "message": "This analysis has expired or does not exist. Analyze the job description again.",
                "retryable": False,
            },
        )
    document = build_cv_document(services.cv_writer, record.cv_context, payload.template)
    return CvGenerateResponse(
        markdown=document.markdown,
        template=payload.template,
        structuredCv=document.output.structured_cv,
        generation=_generation_info(document),
        provenance=[_provenance(t) for t in document.report.bullets],
    )
