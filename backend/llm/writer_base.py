"""CV writers that also report HOW they wrote the CV (provider, model, mode, usage).

The pipeline's CVWriter ABC (pipeline/cv_writer.py) returns just a StructuredCV. The API also
needs safe generation metadata for its technical-details section, so writers used by the API
implement `generate()`, which returns a WriterOutput; `write()` stays available and returns
the bare StructuredCV, so any ReportingCVWriter is still a plain CVWriter.
"""
from abc import abstractmethod
from dataclasses import dataclass
from typing import Optional

from cv_writer import DEFAULT_CV_NAME, CVWriter, DeterministicCVWriter
from llm_provider import TokenUsage
from structured_cv import StructuredCV
from tailor_cv import CVContext

MODE_LLM = "llm_structured"
MODE_DETERMINISTIC = "deterministic_fallback"


@dataclass(frozen=True)
class WriterOutput:
    structured_cv: StructuredCV
    provider: str
    model: Optional[str]
    mode: str  # MODE_LLM | MODE_DETERMINISTIC
    usage: Optional[TokenUsage] = None
    attempts: int = 1
    retried: bool = False


class ReportingCVWriter(CVWriter):
    @abstractmethod
    def generate(self, cv_context: CVContext, name: str = DEFAULT_CV_NAME) -> WriterOutput:
        """Write a StructuredCV from a CVContext. The result is NOT yet trusted: callers must
        run provenance validation (cv_generation.build_cv_document) before rendering it."""

    def write(self, cv_context: CVContext, name: str = DEFAULT_CV_NAME) -> StructuredCV:
        return self.generate(cv_context, name).structured_cv


class DeterministicWriterAdapter(ReportingCVWriter):
    """The explicit development fallback (LLM_PROVIDER=dev) and regression oracle: the
    rule-based DeterministicCVWriter, labelled so the UI never presents it as LLM output."""

    def __init__(self, writer: Optional[CVWriter] = None):
        self._writer = writer or DeterministicCVWriter()

    def generate(self, cv_context: CVContext, name: str = DEFAULT_CV_NAME) -> WriterOutput:
        return WriterOutput(
            structured_cv=self._writer.write(cv_context, name),
            provider="dev",
            model=None,
            mode=MODE_DETERMINISTIC,
        )
