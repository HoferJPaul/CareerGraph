"""Bounded retry with exponential backoff and full jitter.

Only errors the taxonomy marks as transient (timeouts, HTTP 429, transient 5xx / connection
failures) are retried. Authentication, configuration and rejected-request errors, and
deterministic validation failures, are raised on the first occurrence -- they cannot succeed
by being sent again, and retrying them just burns quota.
"""
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Optional, TypeVar

from llm.errors import LLMError, LLMRateLimitError

T = TypeVar("T")
log = logging.getLogger("careergraph.llm")


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3  # retries after the first attempt, so at most max_retries + 1 calls
    base_delay: float = 0.5
    max_delay: float = 8.0
    max_retry_after: float = 20.0  # a longer server-requested wait fails fast instead of hanging the UI

    def backoff(self, retry_number: int, uniform: Callable[[float, float], float]) -> float:
        """Full jitter: uniform(0, min(max_delay, base * 2**n)) for the n-th retry (0-based)."""
        return uniform(0.0, min(self.max_delay, self.base_delay * (2 ** retry_number)))


def run_with_retries(
    operation: Callable[[], T],
    policy: RetryPolicy,
    *,
    operation_name: str = "llm_call",
    sleep: Callable[[float], None] = time.sleep,
    uniform: Callable[[float, float], float] = random.uniform,
) -> tuple[T, int]:
    """Run `operation` until it succeeds, raises a non-retryable error, or retries are
    exhausted. Returns (result, attempts). The final error is re-raised unchanged."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return operation(), attempt
        except LLMError as exc:
            retries_used = attempt - 1
            if not exc.transient or retries_used >= policy.max_retries:
                log.warning(
                    "llm_call_failed operation=%s code=%s attempts=%d transient=%s",
                    operation_name, exc.code, attempt, exc.transient,
                )
                raise
            delay = policy.backoff(retries_used, uniform)
            retry_after: Optional[float] = getattr(exc, "retry_after", None) if isinstance(exc, LLMRateLimitError) else None
            if retry_after is not None:
                if retry_after > policy.max_retry_after:
                    raise
                delay = max(delay, retry_after)
            log.info(
                "llm_call_retry operation=%s code=%s attempt=%d delay_s=%.2f",
                operation_name, exc.code, attempt, delay,
            )
            sleep(delay)
