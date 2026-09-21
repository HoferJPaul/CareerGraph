"""Test doubles for the offline suite. No real Groq key, network access or Neo4j is used anywhere.

FAKE_KEY is a clearly-fake sentinel (not a real key format) so leak tests can search for it.
"""
import json
from typing import Any, Callable, Optional

import groq
import httpx

from llm.config import LLMSettings
from llm.groq_client import GroqStructuredClient, StructuredCompletion
from llm_provider import TokenUsage
from pydantic import SecretStr

FAKE_KEY = "test-sentinel-key-000-not-a-real-key"
PROVIDER_BODY_MARKER = "PROVIDER-INTERNAL-DETAIL-do-not-leak"


def settings(**overrides) -> LLMSettings:
    base = dict(provider="groq", api_key=SecretStr(FAKE_KEY), max_retries=3)
    base.update(overrides)
    return LLMSettings(**base)


def completion_body(content: Any, *, finish_reason: str = "stop", prompt_tokens: int = 120, completion_tokens: int = 80) -> dict:
    """A Groq/OpenAI-compatible chat completion response body."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "openai/gpt-oss-120b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content if isinstance(content, str) else json.dumps(content)},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class RecordingTransport:
    """httpx handler that plays back scripted responses and records every request."""

    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return step(request)
        return step

    @property
    def json_bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def json_response(status: int, body: Any, headers: Optional[dict] = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers or {})


def ok(content: Any, **kw) -> httpx.Response:
    return json_response(200, completion_body(content, **kw))


def error_response(status: int, code: str = "server_error", headers: Optional[dict] = None) -> httpx.Response:
    return json_response(
        status, {"error": {"message": PROVIDER_BODY_MARKER, "type": "test", "code": code}}, headers
    )


def real_sdk_client(transport: RecordingTransport) -> groq.Groq:
    """The REAL Groq SDK, pointed at a mocked HTTP transport: exercises genuine request building,
    response parsing and SDK exception classes with no network."""
    return groq.Groq(
        api_key=FAKE_KEY,
        base_url="https://groq.invalid",
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
        max_retries=0,
    )


def client_over(transport: RecordingTransport, sleeps: Optional[list] = None, **setting_overrides) -> GroqStructuredClient:
    sleeps = sleeps if sleeps is not None else []
    return GroqStructuredClient(
        settings(**setting_overrides),
        sdk_client=real_sdk_client(transport),
        sleep=sleeps.append,
        uniform=lambda low, high: high,  # deterministic: always the full backoff window
    )


CALL_KWARGS = dict(
    operation="test_op",
    model="openai/gpt-oss-120b",
    system_prompt="SYSTEM-PROMPT-MARKER",
    user_content="USER-CONTENT-MARKER",
    schema_name="test_schema",
    schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False},
    max_completion_tokens=256,
    temperature=0.0,
)


class FakeStructuredClient:
    """A StructuredClient double: returns scripted data (or raises) and records each call."""

    def __init__(self, data: Any = None, *, error: Optional[Exception] = None, usage: Optional[TokenUsage] = None, attempts: int = 1):
        self.data = data
        self.error = error
        self.usage = usage or TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        self.attempts = attempts
        self.calls: list[dict] = []

    def complete(self, **kwargs) -> StructuredCompletion:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return StructuredCompletion(data=self.data, model=kwargs["model"], usage=self.usage, attempts=self.attempts)


class FakeSession:
    """Stands in for a Neo4j session where a route needs one but the LLM layer never touches it."""

    def run(self, *args, **kwargs):  # pragma: no cover - a call here means the LLM layer hit Neo4j
        raise AssertionError("the offline tests must not run Cypher")


def requirement_draft(*items: dict) -> dict:
    return {"requirements": list(items)}


def draft_req(raw="Experience with PostgreSQL", skill="postgresql", importance="required", category="technology", related=None) -> dict:
    return {
        "raw": raw,
        "skillQuery": skill,
        "importance": importance,
        "category": category,
        "relatedCapabilities": related or [],
    }
