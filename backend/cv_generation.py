"""The single choke point between CV writing and CV rendering.

    writer.generate(cv_context) -> StructuredCV
      -> provenance validation against the SAME cv_context      (raises on any failure)
      -> deterministic render(validated StructuredCV, template)

The renderer is only ever called after validation succeeds, and only with a StructuredCV -- never
with model text, a dict, or Markdown a model wrote. Both writers (Groq and the deterministic
development fallback) go through here, so the fallback is held to the same provenance rules.
"""
from dataclasses import dataclass
from typing import Callable

from cv_markdown import render
from llm.evidence_registry import build_registry
from llm.provenance import ProvenanceReport, validate_structured_cv
from llm.writer_base import ReportingCVWriter, WriterOutput
from structured_cv import StructuredCV
from tailor_cv import CVContext


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
