"""The single choke point between CV writing and CV rendering.

Graph-only (no Source CV):

    writer.generate(cv_context) -> StructuredCV
      -> provenance validation against the SAME cv_context      (raises on any failure)
      -> deterministic render(validated StructuredCV, template)

Source-aware (a Source CV snapshot is held by the analysis):

    writer.write(input)            model wording, as an UNTRUSTED draft
      -> assemble                  deterministic: header, chronology, contact, education from data
      -> validate                  provenance namespaces, gaps, numbers, terms, coverage, accuracy
      -> semantic verification     source-backed claims checked against what they cite (separate call)
      -> on failure: repair        the specific invalid claims go back to the model, which may only rewrite
                                   or remove THOSE; the whole result is then revalidated. At most
                                   MAX_REPAIR_ATTEMPTS times -- then the CV is rejected with a report, and
                                   no rule is ever relaxed to get one through.
      -> deterministic render      only ever of a StructuredCV that passed every check above

The renderer is only ever called after validation succeeds, and only with a StructuredCV -- never
with model text, a dict, or Markdown a model wrote. Both writers (Groq and the deterministic
development fallback) go through here, so the fallback is held to the same provenance rules.
"""
import logging
import math
from dataclasses import dataclass
from typing import Callable, Optional

from cv_markdown import render
from llm.complete_registry import CompleteInput
from llm.complete_validation import CompleteTrace, check_draft
from llm.complete_writers import (
    CompleteCvWriter,
    DraftResult,
    SupportVerifier,
    Verification,
    add_usage,
    claims_from_traces,
)
from llm.errors import ProvenanceValidationError
from llm.evidence_registry import build_registry
from llm.provenance import ProvenanceReport, validate_structured_cv
from llm.writer_base import ReportingCVWriter, WriterOutput
from llm_provider import TokenUsage
from structured_cv import StructuredCV
from tailor_cv import CVContext

log = logging.getLogger("careergraph.api")

MAX_REPAIR_ATTEMPTS = 2
CHARS_PER_LINE = 95
LINES_PER_PAGE = 52


@dataclass(frozen=True)
class CvDocument:
    output: WriterOutput
    report: ProvenanceReport
    markdown: str


def build_cv_document(
    writer: ReportingCVWriter,
    cv_context: CVContext,
    template: str = "modern",
    *,
    renderer: Callable[[StructuredCV, str], str] = render,
) -> CvDocument:
    output = writer.generate(cv_context)
    report = validate_structured_cv(output.structured_cv, build_registry(cv_context))
    markdown = renderer(output.structured_cv, template)
    return CvDocument(output=output, report=report, markdown=markdown)


# ---- source-aware generation -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LayoutWarning:
    code: str
    message: str
    severity: str = "warning"  # "warning" | "info"


@dataclass(frozen=True)
class CompleteDocument:
    structured_cv: StructuredCV
    markdown: str
    traces: list[CompleteTrace]
    provider: str
    model: Optional[str]
    mode: str
    usage: Optional[TokenUsage]
    attempts: int
    repair_attempts: int
    verification: str  # "semantic" | "verbatim"
    layout_warnings: list[LayoutWarning]


def estimate_pages(markdown: str) -> float:
    """A rough, deliberately simple length estimate (rendered lines at a typical CV width)."""
    lines = 0.0
    for line in markdown.splitlines():
        lines += 0.5 if not line.strip() else max(1, math.ceil(len(line) / CHARS_PER_LINE))
    return lines / LINES_PER_PAGE


def build_layout_warnings(ci: CompleteInput, cv: StructuredCV, markdown: str) -> list[LayoutWarning]:
    """Everything the reader should know about how this CV was put together. Nothing here removes
    content: chronology is never cut to fit, gaps are reported not filled, disputes are surfaced."""
    warnings: list[LayoutWarning] = []
    if not ci.profile.contact.fullName:
        warnings.append(LayoutWarning("no_name", "No name was found in your source CV, so a default name is shown."))
    for conflict in ci.recon.conflicts:
        if conflict.resolution is None:
            warnings.append(LayoutWarning(
                "unresolved_conflict",
                f"{conflict.description} The disputed {conflict.field} is left out of the CV until you choose a value.",
            ))
    for gap in ci.recon.chronology_gaps:
        warnings.append(LayoutWarning("chronology_gap", gap.message))
    if ci.recon.undated_roles:
        count = len(ci.recon.undated_roles)
        warnings.append(LayoutWarning(
            "undated_role", f"{count} position{'s have' if count != 1 else ' has'} no dates and {'are' if count != 1 else 'is'} listed after the dated ones."
        ))
    if cv.additionalExperience:
        count = len(cv.additionalExperience)
        warnings.append(LayoutWarning(
            "additional_experience",
            f"{count} less relevant position{'s are' if count != 1 else ' is'} listed compactly under Additional Experience so the timeline stays complete.",
            "info",
        ))
    pages = estimate_pages(markdown)
    if pages > ci.page_budget + 0.1:
        warnings.append(LayoutWarning(
            "over_page_budget",
            f"This CV is about {pages:.1f} pages, above your {ci.page_budget}-page target. Nothing was removed to fit: "
            "the full chronology is kept. Shorten entries in your source CV or raise the target.",
        ))
    return warnings


def build_complete_cv_document(
    writer: CompleteCvWriter,
    verifier: SupportVerifier,
    ci: CompleteInput,
    template: str = "modern",
    *,
    max_repairs: int = MAX_REPAIR_ATTEMPTS,
    renderer: Callable[[StructuredCV, str], str] = render,
) -> CompleteDocument:
    result: DraftResult = writer.write(ci)
    verify_usage: Optional[TokenUsage] = None
    verify_attempts = 0
    verified = False
    repairs = 0

    while True:
        checked = check_draft(result.draft, ci)
        violations = list(checked.violations)
        if not violations:
            claims = claims_from_traces(checked.traces)
            verification = verifier.verify(claims, ci) if claims else Verification([])
            verified = verified or bool(claims)
            verify_usage = add_usage(verify_usage, verification.usage)
            verify_attempts += verification.attempts
            violations = verification.violations
        if not violations:
            break
        if repairs >= max_repairs:
            log.warning("cv_rejected repairs=%d violations=%d", repairs, len(violations))
            raise ProvenanceValidationError(violations, repair_attempts=repairs)
        result = writer.repair(ci, result, violations)
        repairs += 1

    markdown = renderer(checked.cv, template)
    log.info(
        "cv_generated complete=true repairs=%d verification=%s roles=%d featured=%d additional=%d ignored_fixes=%d",
        repairs, verifier.mode if verified else "not_needed", len(ci.recon.roles), len(checked.cv.experience), len(checked.cv.additionalExperience),
        result.ignored_fixes,
    )
    return CompleteDocument(
        structured_cv=checked.cv,
        markdown=markdown,
        traces=checked.traces,
        provider=result.provider,
        model=result.model,
        mode=result.mode,
        usage=add_usage(result.usage, verify_usage),
        attempts=result.attempts + verify_attempts,
        repair_attempts=repairs,
        verification=verifier.mode if verified else "not_needed",
        layout_warnings=build_layout_warnings(ci, checked.cv, markdown),
    )
