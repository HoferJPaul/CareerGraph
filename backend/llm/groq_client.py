"""Groq transport: one structured-output chat completion, with bounded retries.

This is the ONLY module that talks to the Groq SDK. It:
  - sends a JSON-Schema structured-output request (strict mode by default),
  - retries timeouts / HTTP 429 / transient 5xx with exponential backoff + jitter (retry.py),
  - translates every SDK failure into the non-sensitive taxonomy in errors.py -- provider error
    bodies, request payloads and model output are never copied into an exception or a log line,
  - returns the parsed JSON object (a plain dict). It does NOT trust it: callers must validate
    it into a domain model before it reaches Neo4j or the renderer.

The SDK client is injectable (tests pass a fake, or a real `groq.Groq` over an httpx
MockTransport), so nothing here needs a real key or network access to be tested.
"""
import json
import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

import groq

from llm.config import LLMSettings
from llm.errors import (
    LLMAuthError,
    LLMConfigurationError,
    LLMError,
    LLMInvalidResponseError,
    LLMRateLimitError,
    LLMRejectedError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from llm.retry import RetryPolicy, run_with_retries
from llm_provider import TokenUsage

log = logging.getLogger("careergraph.llm")


@dataclass(frozen=True)
class StructuredCompletion:
    data: dict
    model: str
    usage: Optional[TokenUsage]
    attempts: int

    @property
    def retried(self) -> bool:
        return self.attempts > 1


class StructuredClient(Protocol):
    """What the extraction provider and CV writer need from a provider transport."""

    def complete(
        self,
        *,
        operation: str,
        model: str,
        system_prompt: str,
        user_content: str,
        schema_name: str,
        schema: dict,
        max_completion_tokens: int,
        temperature: float,
    ) -> StructuredCompletion: ...


def _retry_after_seconds(exc: "groq.APIStatusError") -> Optional[float]:
    try:
        raw = exc.response.headers.get("retry-after")
        return float(raw) if raw is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _is_schema_failure(exc: "groq.APIStatusError") -> bool:
    """Best-effort structured-output failures come back as HTTP 400 ('Generated JSON does not
    match the expected schema'). Read only to CLASSIFY -- the body is never surfaced."""
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            if err.get("code") == "json_validate_failed":
                return True
            if "does not match the expected schema" in str(err.get("message", "")).lower():
                return True
    return False


def translate_sdk_error(exc: "groq.APIError") -> LLMError:
    """Map a Groq SDK exception to a safe LLMError. Order matters: APITimeoutError is a
    subclass of APIConnectionError."""
    if isinstance(exc, groq.APITimeoutError):
        return LLMTimeoutError()
    if isinstance(exc, groq.APIConnectionError):
        return LLMUnavailableError()
    status = getattr(exc, "status_code", None)
    if isinstance(exc, groq.RateLimitError) or status == 429:
        return LLMRateLimitError(retry_after=_retry_after_seconds(exc))
    if isinstance(exc, (groq.AuthenticationError, groq.PermissionDeniedError)) or status in (401, 403):
        return LLMAuthError()
    if status is not None and status >= 500:
        return LLMUnavailableError()
    if status == 404:
        return LLMConfigurationError(
            "The configured language model was not found. Check LLM_EXTRACTION_MODEL and LLM_WRITING_MODEL."
        )
    if status in (400, 422) and _is_schema_failure(exc):
        return LLMInvalidResponseError()
    return LLMRejectedError()


def _parse_completion(response: Any) -> tuple[dict, Optional[TokenUsage]]:
    try:
        choice = response.choices[0]
        content = choice.message.content
        finish_reason = getattr(choice, "finish_reason", None)
    except (AttributeError, IndexError, TypeError):
        raise LLMInvalidResponseError() from None
    if finish_reason == "length":
        raise LLMInvalidResponseError(
            "The language model's response was cut off before it finished. Try again."
        )
    if not content:
        raise LLMInvalidResponseError()
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        raise LLMInvalidResponseError() from None
    if not isinstance(data, dict):
        raise LLMInvalidResponseError()
    raw_usage = getattr(response, "usage", None)
    usage = (
        TokenUsage(
            prompt_tokens=getattr(raw_usage, "prompt_tokens", None),
            completion_tokens=getattr(raw_usage, "completion_tokens", None),
            total_tokens=getattr(raw_usage, "total_tokens", None),
        )
        if raw_usage is not None
        else None
    )
    return data, usage


class GroqStructuredClient:
    def __init__(
        self,
        settings: LLMSettings,
        *,
        sdk_client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
    ):
        self._settings = settings
        self._sdk_client = sdk_client
        self._sleep = sleep
        self._uniform = uniform
        self._lock = threading.Lock()

    def _sdk(self) -> Any:
        with self._lock:
            if self._sdk_client is None:
                # Raises LLMConfigurationError (non-sensitive) when GROQ_API_KEY is missing.
                api_key = self._settings.require_api_key()
                # max_retries=0: the SDK's own retry is disabled so ALL retry policy lives in retry.py.
                self._sdk_client = groq.Groq(
                    api_key=api_key, timeout=self._settings.timeout_seconds, max_retries=0
                )
            return self._sdk_client

    def complete(
        self,
        *,
        operation: str,
        model: str,
        system_prompt: str,
        user_content: str,
        schema_name: str,
        schema: dict,
        max_completion_tokens: int,
        temperature: float,
    ) -> StructuredCompletion:
        sdk = self._sdk()
        started = time.monotonic()

        def call_once() -> tuple[dict, Optional[TokenUsage]]:
            try:
                response = sdk.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "strict": self._settings.strict_schema,
                            "schema": schema,
                        },
                    },
                    temperature=temperature,
                    max_completion_tokens=max_completion_tokens,
                )
            except groq.APIError as exc:
                # `from None`: the SDK exception carries the provider's raw error body.
                raise translate_sdk_error(exc) from None
            return _parse_completion(response)

        (data, usage), attempts = run_with_retries(
            call_once,
            RetryPolicy(max_retries=self._settings.max_retries),
            operation_name=operation,
            sleep=self._sleep,
            uniform=self._uniform,
        )
        log.info(
            "llm_call operation=%s model=%s attempts=%d latency_ms=%d prompt_tokens=%s completion_tokens=%s",
            operation,
            model,
            attempts,
            int((time.monotonic() - started) * 1000),
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
        )
        return StructuredCompletion(data=data, model=model, usage=usage, attempts=attempts)
