"""Error taxonomy for the LLM layer.

Every error carries a fixed, human-readable, NON-SENSITIVE message. Provider error bodies,
prompts, model output and CV/job-description text are never copied into an LLMError -- the
API returns `to_detail()` verbatim, so anything stored here is public.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Violation:
    """One failed provenance rule. `code` is stable and safe to expose; `detail` may name a
    job-derived term or an evidence id, so it is for logs/tests only -- never the API."""

    code: str
    detail: str = ""
    # Source-aware generation only: what the repair loop may rewrite or remove ("headline", "profile",
    # "bullet:<entry>#<n>", "skill:<bucket>:<name>"; "structure" for problems fixed without a model).
    target: str = ""


class LLMError(Exception):
    code = "llm_error"
    http_status = 502
    # `retryable`: it is reasonable for the USER to try the action again (shown in the API/UI).
    # `transient`: the transport layer may automatically re-send the SAME request (bounded
    # backoff, see retry.py). Only timeouts, 429s and transient 5xx are transient; a response
    # that failed validation or a rejected credential is never re-sent automatically.
    retryable = False
    transient = False
    default_message = "The language model request failed."

    def __init__(self, message: Optional[str] = None):
        self.message = message or self.default_message
        super().__init__(self.message)

    @property
    def headers(self) -> dict[str, str]:
        return {}

    def to_detail(self) -> dict:
        return {"code": self.code, "message": self.message, "retryable": self.retryable}


class LLMConfigurationError(LLMError):
    code = "llm_not_configured"
    http_status = 503
    default_message = "The language model is not configured on the server."


class LLMAuthError(LLMError):
    code = "llm_auth_failed"
    http_status = 502
    default_message = (
        "The language model provider rejected the server's credentials. "
        "Check the GROQ_API_KEY configured on the server."
    )


class LLMRateLimitError(LLMError):
    code = "llm_rate_limited"
    http_status = 429
    retryable = True
    transient = True
    default_message = "The language model provider is rate limiting requests. Wait a moment and try again."

    def __init__(self, message: Optional[str] = None, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after

    @property
    def headers(self) -> dict[str, str]:
        return {"Retry-After": str(max(1, int(self.retry_after)))} if self.retry_after else {}


class LLMTimeoutError(LLMError):
    code = "llm_timeout"
    http_status = 504
    retryable = True
    transient = True
    default_message = "The language model provider timed out. Try again."


class LLMUnavailableError(LLMError):
    code = "llm_unavailable"
    http_status = 503
    retryable = True
    transient = True
    default_message = "The language model provider is temporarily unavailable. Try again shortly."


class LLMRejectedError(LLMError):
    code = "llm_request_rejected"
    http_status = 502
    default_message = "The language model provider rejected the request."


class LLMInvalidResponseError(LLMError):
    code = "llm_invalid_response"
    http_status = 502
    retryable = True  # a fresh generation usually succeeds; the transport layer never auto-retries it
    default_message = "The language model returned a response that failed validation. Try again."


class NoRequirementsError(LLMError):
    code = "no_requirements"
    http_status = 422
    default_message = (
        "No hiring requirements could be extracted from this text. Paste the full job description."
    )


class InsufficientEvidenceError(LLMError):
    code = "no_evidence"
    http_status = 422
    default_message = (
        "Matching found no career evidence to write a CV from for this job, so nothing was generated."
    )


class InputTooLargeError(LLMError):
    code = "input_too_large"
    http_status = 413
    default_message = "The input is too large to send to the language model."


class ProvenanceValidationError(LLMError):
    """The generated CV failed evidence-grounding validation and was rejected outright."""

    code = "cv_provenance_failed"
    http_status = 502
    retryable = True
    default_message = (
        "The generated CV could not be verified against your career evidence, so it was rejected. "
        "Try generating again."
    )

    def __init__(self, violations: list[Violation], repair_attempts: int = 0):
        super().__init__()
        self.violations = violations
        self.repair_attempts = repair_attempts

    def to_detail(self) -> dict:
        detail = super().to_detail()
        counts: dict[str, int] = {}
        for v in self.violations:
            counts[v.code] = counts.get(v.code, 0) + 1
        detail["violations"] = counts  # codes and counts only -- never terms or evidence text
        if self.repair_attempts:
            detail["repairAttempts"] = self.repair_attempts
        return detail
