"""Shared plumbing for the Source CV tests: a service over a temp-dir store, and a TestClient wired to it."""
import io
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi.testclient import TestClient

from docfixtures import make_docx, make_pdf
from helpers import FakeStructuredClient, RecordingTransport, client_over, ok, settings
from llm.source_profile import DevSourceCvParser, GroqSourceCvParser, SourceCvParser
from main import app
from source_cv.service import SourceCvService
from source_cv.store import FileSourceCvStore
from source_cv.wiring import get_source_cv_service
from source_fixtures import cv_lines, valid_source_draft

FIXED_NOW = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def cv_pdf(**kwargs) -> bytes:
    return make_pdf([cv_lines()], **kwargs)


def cv_docx() -> bytes:
    return make_docx(cv_lines())


def groq_parser(draft: Optional[dict] = None, **kwargs) -> SourceCvParser:
    return GroqSourceCvParser(settings(), FakeStructuredClient(draft if draft is not None else valid_source_draft(), **kwargs))


def transport_parser(*responses, sleeps: Optional[list] = None, **overrides) -> tuple[SourceCvParser, RecordingTransport]:
    """The REAL Groq SDK over a mocked HTTP transport: proves the request/response contract end to end."""
    transport = RecordingTransport(list(responses) or [ok(valid_source_draft())])
    return GroqSourceCvParser(settings(**overrides), client_over(transport, sleeps, **overrides)), transport


def make_service(tmp_path: Path, parser: Optional[SourceCvParser] = None, **kwargs) -> SourceCvService:
    kwargs.setdefault("max_text_chars", 40_000)
    kwargs.setdefault("clock", lambda: FIXED_NOW)
    return SourceCvService(FileSourceCvStore(tmp_path), parser or groq_parser(), **kwargs)


@contextmanager
def source_api(service: SourceCvService):
    app.dependency_overrides[get_source_cv_service] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_source_cv_service, None)


def upload(client: TestClient, data: bytes, name: str = "cv.pdf", content_type: str = "application/pdf", **kwargs):
    return client.post("/api/source-cv", files={"file": (name, io.BytesIO(data), content_type)}, **kwargs)


def dev_parser() -> SourceCvParser:
    return DevSourceCvParser()
