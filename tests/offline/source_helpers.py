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


# ---- profiles, reconciliation and the complete-CV flow -------------------------------------------------------------------

import json  # noqa: E402
from typing import Any, Callable  # noqa: E402

from fixtures import build_cv_context  # noqa: E402
from llm.complete_registry import CompleteInput, build_complete_input  # noqa: E402
from llm.complete_writers import GroqCompleteWriter, GroqSupportVerifier  # noqa: E402
from llm.evidence_registry import build_registry  # noqa: E402
from llm.groq_client import StructuredCompletion  # noqa: E402
from llm_provider import TokenUsage  # noqa: E402
from source_cv.grounding import build_profile  # noqa: E402
from source_cv.reconcile import reconcile  # noqa: E402
from source_cv.schema import ParserInfo, SourceProfile, UploadInfo, derive  # noqa: E402
from source_fixtures import cv_text, valid_draft  # noqa: E402

TODAY_INDEX = 2026 * 12 + 8  # Sep 2026, so "present" roles are deterministic


def make_profile(mutate: Optional[Callable[[SourceProfile], None]] = None, revision: int = 1) -> SourceProfile:
    """The fictional candidate's profile (the same one a valid parse produces), optionally modified."""
    profile = build_profile(
        valid_draft(), cv_text(),
        upload=UploadInfo(originalFilename="cv.pdf", detectedType="pdf", sha256="0" * 64, sizeBytes=1),
        parser=ParserInfo(provider="groq", model="openai/gpt-oss-120b", mode="llm_structured"),
        revision=revision, now=FIXED_NOW,
    )
    if mutate:
        mutate(profile)
        derive(profile)
    return profile


def make_ci(profile: Optional[SourceProfile] = None, ctx=None, page_budget: int = 2) -> CompleteInput:
    profile = profile or make_profile()
    ctx = ctx or build_cv_context()
    registry = build_registry(ctx)
    return build_complete_input(ctx, registry, profile, reconcile(profile, registry, today_index=TODAY_INDEX), page_budget)


def all_supported(kwargs: dict) -> dict:
    claims = json.loads(kwargs["user_content"])["claims"]
    return {"verdicts": [{"ref": c["ref"], "verdict": "supported"} for c in claims]}


class ScriptedClient:
    """A StructuredClient double keyed by operation: write_complete_cv / repair_cv / verify_claims.
    Each entry is data, an Exception, or a callable(kwargs) -> data; the last entry repeats."""

    def __init__(self, **ops: Any):
        self.ops = {k: (list(v) if isinstance(v, list) else [v]) for k, v in ops.items()}
        self.calls: list[dict] = []

    def complete(self, **kwargs) -> StructuredCompletion:
        self.calls.append(kwargs)
        operation = kwargs["operation"]
        queue = self.ops.get(operation)
        if not queue:
            if operation == "verify_claims":
                data: Any = all_supported(kwargs)
            else:
                raise AssertionError(f"unexpected model call: {operation}")
        else:
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            data = item(kwargs) if callable(item) else item
        if isinstance(data, Exception):
            raise data
        return StructuredCompletion(data=data, model=kwargs["model"], usage=TokenUsage(100, 50, 150), attempts=1)

    def operations(self) -> list[str]:
        return [c["operation"] for c in self.calls]


def groq_complete_stack(client: ScriptedClient, **setting_overrides):
    return GroqCompleteWriter(settings(**setting_overrides), client), GroqSupportVerifier(settings(**setting_overrides), client)


def valid_complete_draft(profile: Optional[SourceProfile] = None) -> dict:
    """A well-behaved model response for the fictional career: graph-backed Acme role featured (one graph
    bullet pair plus one source-backed bullet), the two unrelated roles compact, every id namespaced."""
    profile = profile or make_profile()
    def entry_with_fact(prefix: str):  # order-independent, and robust to tests that edit employer/title/dates
        return next(e for e in profile.employment if any(f.text.startswith(prefix) for f in e.facts))

    acme, nordwind, kaffee = (entry_with_fact(p) for p in ("Built a FastAPI service", "Supervised a team", "Managed opening"))
    return {
        "headline": "Backend Engineer",
        "profile": "Backend engineer building Python APIs backed by PostgreSQL.",
        "profileEvidenceIds": ["graph:achievement:OrderSync#1", "graph:achievement:OrderSync#2"],
        "roles": [
            {
                "roleId": f"role:{acme.id}", "emphasis": "featured",
                "bullets": [
                    {"text": "Built a FastAPI service that uses an LLM to extract structured order data from PDFs, cutting manual data entry time by 60%.",
                     "evidenceIds": ["graph:achievement:OrderSync#1"]},
                    {"text": "Designed the PostgreSQL schema and migrations for multi-tenant order data.",
                     "evidenceIds": ["graph:achievement:OrderSync#2"]},
                    {"text": "Wrote onboarding documentation for new engineers.", "evidenceIds": [f"source:{acme.facts[1].id}"]},
                ],
            },
            {"roleId": f"role:{nordwind.id}", "emphasis": "compact", "bullets": []},
            {"roleId": f"role:{kaffee.id}", "emphasis": "compact", "bullets": []},
        ],
        "projects": [
            {"entryId": f"prj:{profile.projects[0].id}",
             "bullets": [{"text": "Implemented a Fastify REST backend with JWT authentication and Zod request validation.",
                          "evidenceIds": ["graph:achievement:TrailMap#1"]}]}
        ],
        "education": [],
        "otherSections": [],
        "skills": {
            "programming": ["Python", "TypeScript", "C"], "frameworks": ["FastAPI", "Fastify", "React"],
            "databases": ["PostgreSQL"], "tools": ["Unix"], "capabilities": ["Backend Engineering", "Debugging"],
        },
    }


# ---- API wiring for the complete-CV flow ---------------------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402

from analysis_store import AnalysisStore, get_analysis_store  # noqa: E402
from deps import get_session  # noqa: E402
from helpers import FakeSession  # noqa: E402
from llm.factory import LLMServices, get_llm_services  # noqa: E402
from llm.groq_cv_writer import GroqCVWriter  # noqa: E402
from llm_provider import ExtractionResult, LLMProvider  # noqa: E402
from requirement_schema import Requirement, RequirementList  # noqa: E402
from routes.llm_status import get_llm_settings  # noqa: E402

JD = "Backend engineer wanted. You know PostgreSQL. CloudWatch and AWS are a plus."


class SimpleProvider(LLMProvider):
    """Extraction double: one fixed requirement."""

    def extract_requirements(self, job_description, session):
        return ExtractionResult(
            requirements=RequirementList(requirements=[
                Requirement(raw=job_description[:60], skillQuery="postgresql", importance="required", category="technology")
            ]),
            mode="llm_structured", note="scripted", provider="groq", model="openai/gpt-oss-120b",
        )


@contextmanager
def complete_api(
    tmp_path: Path, client: Optional[ScriptedClient] = None, *, ctx=None, stored: bool = True, page_budget_default: int = 2,
):
    """The full app with the analysis + generation flow wired to scripted models and a temp Source CV store.
    Yields (TestClient, source service, scripted client, analysis store)."""
    ctx = ctx or build_cv_context()
    service = make_service(tmp_path)
    initial = None
    if stored:
        initial = service.run(service.ingest_events(service.prepare("cv.pdf", cv_pdf())))
    # the default model answer is written for the profile as first stored (what an analysis will snapshot)
    client = client or ScriptedClient(write_complete_cv=lambda kw: valid_complete_draft(initial))
    writer, verifier = groq_complete_stack(client)
    services = LLMServices(
        settings=settings(), extraction_provider=SimpleProvider(), cv_writer=GroqCVWriter(settings(), FakeStructuredClient({})),
        source_parser=groq_parser(), complete_writer=writer, support_verifier=verifier,
    )
    store = AnalysisStore()
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_llm_services] = lambda: services
    app.dependency_overrides[get_analysis_store] = lambda: store
    app.dependency_overrides[get_source_cv_service] = lambda: service
    app.dependency_overrides[get_llm_settings] = lambda: services.settings
    try:
        with patch("routes.jobs.build_cv_context", lambda reqs, session, root: ctx):
            yield TestClient(app), service, client, store
    finally:
        app.dependency_overrides.clear()
