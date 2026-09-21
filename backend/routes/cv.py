"""Orchestration only. Evidence retrieval already happened (routes/jobs.py -> pipeline.py);
this route runs the remaining stages, in order:

    AnalysisStore lookup      the CVContext (and Source CV snapshot) THIS SERVER built -- never
                              anything sent by the browser
      -> writer                GroqCVWriter / GroqCompleteWriter (production) or the labelled fallbacks
      -> validation            reject, never silently repair (llm/provenance.py, llm/complete_validation.py)
      -> cv_markdown.render()  deterministic layout of the validated StructuredCV

Two shapes, chosen by what the ANALYSIS holds (never by the request):

    no Source CV   -> the original graph-only flow, unchanged
    Source CV      -> a complete CV: graph evidence + the candidate's full chronology, contact details and
                      education, with a bounded automatic repair loop (cv_generation.py)

All of the sequencing lives in cv_generation, so the renderer can only ever receive a StructuredCV that
passed validation. Neither stage is reimplemented in this file.
"""
from fastapi import APIRouter, Depends, HTTPException

from analysis_store import AnalysisRecord, AnalysisStore, get_analysis_store
from api_schemas import (
    BulletProvenance,
    CvGenerateRequest,
    CvGenerateResponse,
    EvidenceRef,
    GenerationInfo,
    LayoutWarningInfo,
    TokenUsageInfo,
)
from cv_generation import CompleteDocument, CvDocument, build_complete_cv_document, build_cv_document
from llm.complete_registry import build_complete_input
from llm.complete_validation import CompleteTrace, TracedEvidence
from llm.errors import LLMConfigurationError
from llm.evidence_registry import EvidenceItem, build_registry
from llm.factory import LLMServices, get_llm_services
from llm.provenance import BulletTrace
from source_views import source_cv_info

router = APIRouter(prefix="/api/cv", tags=["cv"])


def _usage_info(usage):
    return (
        TokenUsageInfo(
            promptTokens=usage.prompt_tokens, completionTokens=usage.completion_tokens, totalTokens=usage.total_tokens
        )
        if usage
        else None
    )


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
    return GenerationInfo(
        provider=out.provider,
        model=out.model,
        mode=out.mode,
        devFallback=out.provider != "groq",
        tokenUsage=_usage_info(out.usage),
        retried=out.retried,
        attempts=out.attempts,
    )


def _traced_ref(evidence: TracedEvidence, entry: str) -> EvidenceRef:
    if evidence.origin == "source":
        # A statement from the candidate's own CV: shown as written, attributed to its entry.
        return EvidenceRef(origin="source", label=evidence.text, kind="source", sourceType="Source CV", owner=entry)
    owner = evidence.owner.removeprefix("story:") if evidence.owner else None
    label = (owner or evidence.text) if evidence.kind == "story" else evidence.text
    return EvidenceRef(
        origin="graph", label=label, kind=evidence.kind, sourceType=evidence.source_type, owner=owner,
        transferableFor=evidence.transferable_for, relatedGaps=list(evidence.gaps),
    )


def _complete_provenance(trace: CompleteTrace) -> BulletProvenance:
    return BulletProvenance(
        section=trace.section,
        entry=trace.entry,
        text=trace.text,
        evidence=[_traced_ref(e, trace.entry) for e in trace.evidence],
    )


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail={
            "code": "analysis_not_found",
            "message": "This analysis has expired or does not exist. Analyze the job description again.",
            "retryable": False,
        },
    )


def _generate_complete(record: AnalysisRecord, payload: CvGenerateRequest, services: LLMServices) -> CvGenerateResponse:
    if services.complete_writer is None or services.support_verifier is None:
        raise LLMConfigurationError("The source-aware CV writer is not configured on the server.")
    ci = build_complete_input(
        record.cv_context, build_registry(record.cv_context), record.source_profile, record.reconciliation, payload.pageBudget
    )
    document: CompleteDocument = build_complete_cv_document(
        services.complete_writer, services.support_verifier, ci, payload.template
    )
    cv = document.structured_cv
    treatments = {e.entryId: "featured" for e in cv.experience} | {e.entryId: "additional" for e in cv.additionalExperience}
    return CvGenerateResponse(
        markdown=document.markdown,
        template=payload.template,
        structuredCv=cv,
        generation=GenerationInfo(
            provider=document.provider,
            model=document.model,
            mode=document.mode,
            devFallback=document.provider != "groq",
            tokenUsage=_usage_info(document.usage),
            retried=False,  # `attempts` counts every model request (write, repairs, verification)
            attempts=document.attempts,
            completeCv=True,
            repairAttempts=document.repair_attempts,
            verification=document.verification,
        ),
        provenance=[_complete_provenance(t) for t in document.traces],
        sourceCv=source_cv_info(record.source_profile, record.reconciliation, treatments=treatments),
        layoutWarnings=[LayoutWarningInfo(code=w.code, message=w.message, severity=w.severity) for w in document.layout_warnings],
    )


@router.post("/generate", response_model=CvGenerateResponse)
def generate_cv(
    payload: CvGenerateRequest,
    services: LLMServices = Depends(get_llm_services),
    store: AnalysisStore = Depends(get_analysis_store),
) -> CvGenerateResponse:
    record = store.get(payload.analysisId)
    if record is None:
        raise _not_found()
    if record.source_profile is not None and record.reconciliation is not None:
        return _generate_complete(record, payload, services)
    document = build_cv_document(services.cv_writer, record.cv_context, payload.template)
    return CvGenerateResponse(
        markdown=document.markdown,
        template=payload.template,
        structuredCv=document.output.structured_cv,
        generation=_generation_info(document),
        provenance=[_provenance(t) for t in document.report.bullets],
    )
