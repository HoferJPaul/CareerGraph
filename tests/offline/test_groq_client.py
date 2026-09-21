"""Groq transport: structured-output request shape, bounded retries with backoff + jitter, and
error translation -- driven through the REAL Groq SDK over a mocked HTTP transport."""
import httpx
import pytest

from helpers import (
    CALL_KWARGS, FAKE_KEY, PROVIDER_BODY_MARKER, RecordingTransport, client_over, error_response, ok,
)
from llm.errors import (
    LLMAuthError, LLMConfigurationError, LLMInvalidResponseError, LLMRateLimitError, LLMRejectedError,
    LLMTimeoutError, LLMUnavailableError,
)


def test_sends_a_strict_json_schema_request_and_parses_usage() -> None:
    transport = RecordingTransport([ok({"ok": True})])
    result = client_over(transport).complete(**CALL_KWARGS)

    assert result.data == {"ok": True}
    assert result.attempts == 1 and result.retried is False
    assert (result.usage.prompt_tokens, result.usage.completion_tokens, result.usage.total_tokens) == (120, 80, 200)

    body = transport.json_bodies[0]
    assert body["model"] == "openai/gpt-oss-120b"
    fmt = body["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True and fmt["json_schema"]["name"] == "test_schema"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert body["max_completion_tokens"] == 256
    assert FAKE_KEY not in transport.requests[0].content.decode()  # key travels only in the auth header


def test_strict_mode_can_be_switched_off_for_models_without_it() -> None:
    transport = RecordingTransport([ok({"ok": True})])
    client_over(transport, strict_schema=False).complete(**CALL_KWARGS)
    assert transport.json_bodies[0]["response_format"]["json_schema"]["strict"] is False


def test_rate_limit_is_retried_with_backoff_then_succeeds() -> None:
    transport = RecordingTransport([error_response(429), error_response(429), ok({"ok": True})])
    sleeps: list[float] = []
    result = client_over(transport, sleeps).complete(**CALL_KWARGS)

    assert result.attempts == 3 and result.retried is True
    assert len(transport.requests) == 3
    # Full jitter, deterministic here (uniform -> upper bound): base 0.5 * 2**n.
    assert sleeps == [0.5, 1.0]


def test_retry_after_header_is_honoured_when_reasonable() -> None:
    transport = RecordingTransport([error_response(429, headers={"retry-after": "3"}), ok({"ok": True})])
    sleeps: list[float] = []
    client_over(transport, sleeps).complete(**CALL_KWARGS)
    assert sleeps == [3.0]


def test_absurd_retry_after_fails_fast_instead_of_hanging_the_request() -> None:
    transport = RecordingTransport([error_response(429, headers={"retry-after": "600"})])
    sleeps: list[float] = []
    with pytest.raises(LLMRateLimitError) as exc:
        client_over(transport, sleeps).complete(**CALL_KWARGS)
    assert sleeps == [] and len(transport.requests) == 1
    assert exc.value.headers == {"Retry-After": "600"}


def test_retry_exhaustion_raises_after_max_retries_plus_one_attempts() -> None:
    transport = RecordingTransport([error_response(429)])  # every call is rate limited
    sleeps: list[float] = []
    with pytest.raises(LLMRateLimitError) as exc:
        client_over(transport, sleeps, max_retries=3).complete(**CALL_KWARGS)
    assert len(transport.requests) == 4  # 1 attempt + 3 retries, never more
    assert len(sleeps) == 3 and sleeps == sorted(sleeps)  # exponential growth
    assert exc.value.http_status == 429 and exc.value.retryable


def test_max_retries_zero_means_a_single_attempt() -> None:
    transport = RecordingTransport([error_response(500)])
    with pytest.raises(LLMUnavailableError):
        client_over(transport, max_retries=0).complete(**CALL_KWARGS)
    assert len(transport.requests) == 1


def test_authentication_failure_is_never_retried() -> None:
    for status in (401, 403):
        transport = RecordingTransport([error_response(status)])
        sleeps: list[float] = []
        with pytest.raises(LLMAuthError):
            client_over(transport, sleeps).complete(**CALL_KWARGS)
        assert len(transport.requests) == 1 and sleeps == []


def test_timeout_is_retried_then_reported_as_a_timeout() -> None:
    transport = RecordingTransport([httpx.ReadTimeout("read timed out")])
    sleeps: list[float] = []
    with pytest.raises(LLMTimeoutError) as exc:
        client_over(transport, sleeps, max_retries=2).complete(**CALL_KWARGS)
    assert len(transport.requests) == 3 and len(sleeps) == 2
    assert exc.value.http_status == 504


def test_timeout_then_success_recovers() -> None:
    transport = RecordingTransport([httpx.ConnectTimeout("slow"), ok({"ok": True})])
    result = client_over(transport).complete(**CALL_KWARGS)
    assert result.retried is True and result.data == {"ok": True}


def test_transient_5xx_and_connection_errors_are_retried() -> None:
    transport = RecordingTransport([error_response(503), httpx.ConnectError("refused"), error_response(502), ok({"ok": True})])
    result = client_over(transport, max_retries=3).complete(**CALL_KWARGS)
    assert result.attempts == 4


def test_connection_failure_maps_to_provider_unavailable() -> None:
    transport = RecordingTransport([httpx.ConnectError("refused")])
    with pytest.raises(LLMUnavailableError):
        client_over(transport, max_retries=0).complete(**CALL_KWARGS)


@pytest.mark.parametrize("status", [400, 422])
def test_provider_schema_failure_is_an_invalid_response_and_not_retried(status: int) -> None:
    transport = RecordingTransport([error_response(status, code="json_validate_failed")])
    with pytest.raises(LLMInvalidResponseError):
        client_over(transport).complete(**CALL_KWARGS)
    assert len(transport.requests) == 1


def test_other_client_errors_are_rejections_and_a_missing_model_is_a_config_error() -> None:
    with pytest.raises(LLMRejectedError):
        client_over(RecordingTransport([error_response(400, code="bad_request")])).complete(**CALL_KWARGS)
    with pytest.raises(LLMConfigurationError) as exc:
        client_over(RecordingTransport([error_response(404, code="model_not_found")])).complete(**CALL_KWARGS)
    assert "LLM_EXTRACTION_MODEL" in exc.value.message


@pytest.mark.parametrize(
    "content,kwargs",
    [
        ("this is not json", {}),
        ('["a", "list"]', {}),
        ("", {}),
        ('{"ok": tr', {"finish_reason": "length"}),  # truncated by the token budget
    ],
)
def test_unusable_model_output_is_an_invalid_response_and_not_retried(content: str, kwargs: dict) -> None:
    transport = RecordingTransport([ok(content, **kwargs)])
    with pytest.raises(LLMInvalidResponseError):
        client_over(transport).complete(**CALL_KWARGS)
    assert len(transport.requests) == 1


def test_provider_error_bodies_never_reach_the_exception_message_detail_or_cause() -> None:
    for status in (401, 429, 500, 400):
        transport = RecordingTransport([error_response(status)])
        with pytest.raises(Exception) as exc:
            client_over(transport, max_retries=0).complete(**CALL_KWARGS)
        err = exc.value
        assert PROVIDER_BODY_MARKER not in str(err)
        assert PROVIDER_BODY_MARKER not in str(err.to_detail())
        assert err.__cause__ is None and err.__suppress_context__  # `raise ... from None`
