"""API layer with the LLM and Neo4j mocked: analyze -> match review data -> generate CV, error
mapping, tamper resistance, concurrency isolation, and no key/prompt/content leaks in responses
or logs. No Groq key, no network, no Neo4j."""
import ast
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from fixtures import ACH_ORDER_LLM, build_cv_context, valid_cv_draft
from helpers import (
    FAKE_KEY, PROVIDER_BODY_MARKER, FakeSession, RecordingTransport, client_over, draft_req, error_response,
    ok, requirement_draft, settings,
)
from analysis_store import AnalysisStore, get_analysis_store
from deps import get_session
from llm import factory
from llm.errors import (
    InsufficientEvidenceError, LLMAuthError, LLMConfigurationError, LLMInvalidResponseError, LLMRateLimitError,
    LLMTimeoutError, LLMUnavailableError, NoRequirementsError, ProvenanceValidationError, Violation,
)
from llm.factory import LLMServices, get_llm_services
from llm.groq_cv_writer import GroqCVWriter
from llm.groq_extraction import GroqLLMProvider
from llm.prompts import CV_WRITER_SYSTEM_PROMPT, EXTRACTION_SYSTEM_PROMPT
from llm.writer_base import DeterministicWriterAdapter, ReportingCVWriter
from llm_provider import ExtractionResult, LLMProvider
from main import app
from requirement_schema import Requirement, RequirementList
from routes.llm_status import get_llm_settings
from tailor_cv import CVContext

ROOT = Path(__file__).resolve().parent.parent.parent
JD = "Backend engineer wanted. You know PostgreSQL. CloudWatch and AWS are a plus."


class ScriptedProvider(LLMProvider):
    """Extraction double: derives its requirement from the JD, or raises a scripted error."""

    def __init__(self, error=None, provider="groq", mode="llm_structured"):
        self.error, self.provider, self.mode = error, provider, mode

    def extract_requirements(self, job_description, session):
        if self.error:
            raise self.error
        return ExtractionResult(
            requirements=RequirementList(requirements=[
                Requirement(raw=job_description[:60], skillQuery="postgresql", importance="required", category="technology")
            ]),
            mode=self.mode, note="scripted", provider=self.provider,
            model="openai/gpt-oss-120b" if self.provider == "groq" else None,
        )


class ScriptedWriter(ReportingCVWriter):
    def __init__(self, error):
        self.error = error

    def generate(self, cv_context, name="X"):
        raise self.error


def context_for(requirements: RequirementList) -> CVContext:
    ctx = build_cv_context()
    ctx.requirements = [r.model_dump() for r in requirements.requirements]
    return ctx


@contextmanager
def api(services: LLMServices, store: AnalysisStore | None = None, build=None):
    store = store or AnalysisStore()
    app.dependency_overrides[get_session] = lambda: FakeSession()
    app.dependency_overrides[get_llm_services] = lambda: services
    app.dependency_overrides[get_analysis_store] = lambda: store
    app.dependency_overrides[get_llm_settings] = lambda: services.settings
    try:
        with patch("routes.jobs.build_cv_context", build or (lambda reqs, session, root: context_for(reqs))):
            yield TestClient(app), store
    finally:
        app.dependency_overrides.clear()


def services_with(provider=None, writer=None, **setting_overrides) -> LLMServices:
    return LLMServices(
        settings=settings(**setting_overrides),
        extraction_provider=provider or ScriptedProvider(),
        cv_writer=writer or GroqCVWriter(settings(), _fake_writer_client()),
    )


def _fake_writer_client():
    from helpers import FakeStructuredClient
    return FakeStructuredClient(valid_cv_draft())


# ---- the autonomous flow ----------------------------------------------------------------------


def test_job_description_to_finished_cv_without_leaving_the_app() -> None:
    with api(services_with()) as (client, store):
        analyzed = client.post("/api/jobs/analyze", json={"jobDescription": JD})
        assert analyzed.status_code == 200
        body = analyzed.json()
        assert "requirementsPath" not in body  # no shared file any more
        assert body["extraction"] == {
            "provider": "groq", "model": "openai/gpt-oss-120b", "mode": "llm_structured", "note": "scripted",
            "requirementCount": 1, "tokenUsage": None, "retried": False, "attempts": 1, "devFallback": False,
        }
        assert body["cvContext"]["gaps"] and body["cvContext"]["evidenceStories"]

        generated = client.post("/api/cv/generate", json={"analysisId": body["analysisId"], "template": "modern"})
        assert generated.status_code == 200
        cv = generated.json()
        assert cv["markdown"].startswith("# ") and cv["template"] == "modern"
        assert cv["generation"]["mode"] == "llm_structured" and cv["generation"]["devFallback"] is False
        assert cv["generation"]["provenanceValidated"] is True and cv["generation"]["model"] == "openai/gpt-oss-120b"
        assert cv["structuredCv"]["experience"][0]["organization"] == "Acme Analytics"

        # Provenance is inspectable per bullet, and transferable evidence stays labelled as such.
        by_text = {p["text"]: p for p in cv["provenance"]}
        assert len(cv["provenance"]) == 6
        transferable = [
            e for p in cv["provenance"] for e in p["evidence"] if e["kind"] == "transferable"
        ]
        assert transferable and transferable[0]["transferableFor"] == "observability"
        assert transferable[0]["relatedGaps"] == ["cloudwatch"]
        assert by_text[
            "Built a FastAPI service that uses an LLM to extract structured order data from PDFs, "
            "cutting manual data entry time by 60%."
        ]["evidence"][0]["label"] == ACH_ORDER_LLM


def test_api_passes_the_matchers_output_through_without_reclassifying_gaps() -> None:
    """Matching itself is mocked here (the real gap-vs-transferable behaviour is covered by the
    Neo4j-backed tests in backend/test_api.py). What this proves is that the LLM-era routes hand
    the matcher's CVContext to the client byte-for-byte: nothing promotes a gap or edits evidence."""
    with api(services_with()) as (client, _):
        body = client.post("/api/jobs/analyze", json={"jobDescription": JD}).json()
    reqs = RequirementList(requirements=[
        Requirement(raw=JD[:60], skillQuery="postgresql", importance="required", category="technology")
    ])
    assert body["cvContext"] == context_for(reqs).model_dump()
    gaps = body["cvContext"]["gaps"]
    assert all(g["evidence"] == [] and g["confidence"] == "no_match" for g in gaps)
    assert next(g for g in gaps if g["skillQuery"] == "cloudwatch")["transferableEvidence"]


def test_dev_fallback_extraction_and_writing_are_labelled_never_presented_as_llm_output() -> None:
    services = LLMServices(
        settings=settings(provider="dev", api_key=None),
        extraction_provider=ScriptedProvider(provider="dev", mode="heuristic_keyword"),
        cv_writer=DeterministicWriterAdapter(),
    )
    with api(services) as (client, _):
        analyzed = client.post("/api/jobs/analyze", json={"jobDescription": JD}).json()
        assert analyzed["extraction"]["devFallback"] is True and analyzed["extraction"]["mode"] == "heuristic_keyword"
        cv = client.post("/api/cv/generate", json={"analysisId": analyzed["analysisId"]}).json()
        assert cv["generation"]["devFallback"] is True and cv["generation"]["mode"] == "deterministic_fallback"
        assert cv["generation"]["model"] is None
        status = client.get("/api/llm/status").json()
        assert status["devFallback"] is True and status["extractionModel"] is None


def test_status_endpoint_reports_configuration_without_the_key() -> None:
    with api(services_with()) as (client, _):
        response = client.get("/api/llm/status")
    assert response.json() == {
        "provider": "groq", "extractionModel": "openai/gpt-oss-120b", "writingModel": "openai/gpt-oss-120b",
        "configured": True, "devFallback": False, "maxJobDescriptionChars": 20000,
    }
    assert FAKE_KEY not in response.text


# ---- server-side evidence: the browser cannot supply what a CV is written from ---------------------


def test_cv_generation_requires_a_server_side_analysis() -> None:
    with api(services_with()) as (client, _):
        missing = client.post("/api/cv/generate", json={"analysisId": "does-not-exist"})
        assert missing.status_code == 404 and missing.json()["detail"]["code"] == "analysis_not_found"
        # A browser-supplied CVContext is not accepted in place of an analysisId.
        forged = client.post("/api/cv/generate", json={"cvContext": build_cv_context().model_dump()})
        assert forged.status_code == 422


def test_a_forged_cv_context_sent_alongside_a_valid_id_is_ignored() -> None:
    with api(services_with()) as (client, _):
        analysis_id = client.post("/api/jobs/analyze", json={"jobDescription": JD}).json()["analysisId"]
        forged = build_cv_context().model_dump()
        forged["evidenceStories"][0]["label"] = "FABRICATED-EMPLOYER-XYZ"
        response = client.post(
            "/api/cv/generate", json={"analysisId": analysis_id, "cvContext": forged},
        )
    assert response.status_code == 200 and "FABRICATED-EMPLOYER-XYZ" not in response.text


def test_analysis_store_expires_and_bounds_entries() -> None:
    now = [0.0]
    store = AnalysisStore(max_entries=2, ttl_seconds=10, clock=lambda: now[0])
    first, second, third = (store.put(build_cv_context()) for _ in range(3))
    assert store.get(first.analysis_id) is None  # evicted: oldest beyond max_entries
    assert store.get(third.analysis_id) is not None
    now[0] = 11.0
    assert store.get(second.analysis_id) is None and store.get(third.analysis_id) is None  # expired
    assert len({first.analysis_id, second.analysis_id, third.analysis_id}) == 3  # unguessable, unique


# ---- error mapping -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error,status,code",
    [
        (LLMConfigurationError(), 503, "llm_not_configured"),
        (LLMRateLimitError(retry_after=7), 429, "llm_rate_limited"),
        (LLMTimeoutError(), 504, "llm_timeout"),
        (LLMUnavailableError(), 503, "llm_unavailable"),
        (LLMInvalidResponseError(), 502, "llm_invalid_response"),
        (LLMAuthError(), 502, "llm_auth_failed"),
        (NoRequirementsError(), 422, "no_requirements"),
    ],
)
def test_extraction_failures_map_to_clear_api_errors(error, status, code) -> None:
    with api(services_with(provider=ScriptedProvider(error=error))) as (client, store):
        response = client.post("/api/jobs/analyze", json={"jobDescription": JD})
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["code"] == code and detail["message"] and isinstance(detail["retryable"], bool)
    assert set(detail) == {"code", "message", "retryable"}
    if code == "llm_rate_limited":
        assert response.headers["retry-after"] == "7"


def test_generation_failures_map_to_clear_api_errors() -> None:
    provenance = ProvenanceValidationError([Violation("gap_claimed", "names literal gap 'aws'"), Violation("empty_evidence", "x")])
    cases = [
        (provenance, 502, "cv_provenance_failed"),
        (InsufficientEvidenceError(), 422, "no_evidence"),
        (LLMTimeoutError(), 504, "llm_timeout"),
    ]
    for error, status, code in cases:
        with api(services_with(writer=ScriptedWriter(error))) as (client, store):
            analysis_id = client.post("/api/jobs/analyze", json={"jobDescription": JD}).json()["analysisId"]
            response = client.post("/api/cv/generate", json={"analysisId": analysis_id})
        assert response.status_code == status and response.json()["detail"]["code"] == code
    detail = provenance.to_detail()
    assert detail["violations"] == {"gap_claimed": 1, "empty_evidence": 1}
    assert "aws" not in str(detail)  # the offending term is not exposed


def test_a_real_sdk_rate_limit_surfaces_as_429_without_the_provider_body() -> None:
    transport = RecordingTransport([error_response(429)])
    sleeps: list[float] = []
    client_sdk = client_over(transport, sleeps, max_retries=2)
    services = LLMServices(
        settings=settings(max_retries=2),
        extraction_provider=GroqLLMProvider(settings(), client_sdk, capability_verifier=lambda s, q: {}),
        cv_writer=GroqCVWriter(settings(), client_sdk),
    )
    with api(services) as (client, _):
        response = client.post("/api/jobs/analyze", json={"jobDescription": JD})
    assert response.status_code == 429 and response.json()["detail"]["code"] == "llm_rate_limited"
    assert len(transport.requests) == 3 and len(sleeps) == 2  # bounded: 1 attempt + 2 retries
    assert PROVIDER_BODY_MARKER not in response.text


def test_missing_api_key_is_a_clear_request_time_error_not_a_crash() -> None:
    with patch.dict(os.environ, {"LLM_PROVIDER": "groq", "GROQ_API_KEY": ""}):
        factory._default_services.cache_clear()
        try:
            app.dependency_overrides[get_session] = lambda: FakeSession()
            client = TestClient(app)
            assert client.get("/api/health").status_code == 200  # the rest of the app still works
            response = client.post("/api/jobs/analyze", json={"jobDescription": JD})
        finally:
            app.dependency_overrides.clear()
            factory._default_services.cache_clear()
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "llm_not_configured" and "GROQ_API_KEY" in detail["message"]


# ---- limits ---------------------------------------------------------------------------------------


def test_job_description_size_and_emptiness_limits() -> None:
    with api(services_with(max_job_description_chars=500)) as (client, _):
        too_big = client.post("/api/jobs/analyze", json={"jobDescription": "x" * 501})
        empty = client.post("/api/jobs/analyze", json={"jobDescription": "   \n "})
        exact = client.post("/api/jobs/analyze", json={"jobDescription": "x" * 500})
    assert too_big.status_code == 413 and too_big.json()["detail"]["code"] == "input_too_large"
    assert "500" in too_big.json()["detail"]["message"]
    assert empty.status_code == 400
    assert exact.status_code == 200


def test_oversized_cv_context_is_refused_with_a_clear_error() -> None:
    small = settings(max_cv_context_chars=1000)
    services = LLMServices(small, ScriptedProvider(), GroqCVWriter(small, _fake_writer_client()))
    with api(services) as (client, _):
        analysis_id = client.post("/api/jobs/analyze", json={"jobDescription": JD}).json()["analysisId"]
        response = client.post("/api/cv/generate", json={"analysisId": analysis_id})
    assert response.status_code == 413 and response.json()["detail"]["code"] == "input_too_large"


# ---- the RequirementList validation boundary is unchanged ---------------------------------------------


def test_requirements_endpoint_still_validates_the_exact_requirement_list_schema() -> None:
    with api(services_with()) as (client, _):
        raw_jd = client.post("/api/requirements/analyze", json={"jobDescription": "We need a Python engineer."})
        missing = client.post("/api/requirements/analyze", json={"requirements": [{"raw": "Postgres", "skillQuery": "postgresql"}]})
        bad_enum = client.post("/api/requirements/analyze", json={"requirements": [
            {"raw": "x", "skillQuery": "x", "importance": "critical", "category": "technology"}]})
        not_json = client.post("/api/requirements/analyze", content="raw text", headers={"Content-Type": "application/json"})
    for response in (raw_jd, missing, bad_enum, not_json):
        assert response.status_code == 422 and "cvContext" not in response.text


def test_requirements_endpoint_matches_valid_lists_and_returns_a_server_side_analysis_id() -> None:
    payload = {"requirements": [{"raw": "PostgreSQL", "skillQuery": "postgresql", "importance": "required",
                                 "category": "technology", "relatedCapabilities": []}]}
    with api(services_with()) as (client, store):
        with patch("routes.requirements.build_cv_context", lambda reqs, session, root: context_for(reqs)):
            response = client.post("/api/requirements/analyze", json=payload)
        assert response.status_code == 200
        body = response.json()
        assert body["requirementCount"] == 1 and store.get(body["analysisId"]) is not None


# ---- concurrency: no shared requirements file ------------------------------------------------------------------


def test_concurrent_analyses_cannot_overwrite_or_leak_into_each_other() -> None:
    shared = ROOT / "output" / "requirements.json"
    before = shared.stat().st_mtime_ns if shared.exists() else None

    def slow_build(reqs, session, root):
        time.sleep(0.02)  # widen the window in which a shared file/global would be clobbered
        return context_for(reqs)

    markers = [f"MARKER-{i:02d} backend role requiring skill number {i}" for i in range(16)]
    with api(services_with(), build=slow_build) as (client, store):
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(pool.map(lambda m: client.post("/api/jobs/analyze", json={"jobDescription": m}), markers))

        assert all(r.status_code == 200 for r in responses)
        ids = [r.json()["analysisId"] for r in responses]
        assert len(set(ids)) == len(ids)
        for marker, response in zip(markers, responses):
            # Each response, and each stored analysis, contains ONLY its own job's requirements.
            assert response.json()["cvContext"]["requirements"][0]["raw"] == marker[:60]
            assert store.get(response.json()["analysisId"]).cv_context.requirements[0]["raw"] == marker[:60]
            assert sum(other[:60] in response.text for other in markers) == 1

    after = shared.stat().st_mtime_ns if shared.exists() else None
    assert before == after, "analyze must never write output/requirements.json"


def _code_nodes_without_docstrings(tree):
    """All AST nodes except docstring expressions, so prose about a removed file is not code."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                docstrings.add(id(body[0].value))
    return [n for n in ast.walk(tree) if id(n) not in docstrings]


def test_routes_never_write_a_shared_requirements_file() -> None:
    for path in (ROOT / "backend" / "routes").glob("*.py"):
        nodes = _code_nodes_without_docstrings(ast.parse(path.read_text(encoding="utf-8")))
        for node in nodes:
            if isinstance(node, ast.Call):
                name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                assert name not in {"open", "write_text", "write_bytes", "write", "mkdir", "dump"}, f"{path.name}: {name}()"
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert "requirements.json" not in node.value, f"{path.name} references requirements.json"


# ---- no key, prompt or content in responses or logs ------------------------------------------------------------


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def test_no_key_prompt_or_content_appears_in_responses_or_logs_across_retries_and_failures() -> None:
    jd_marker = "SECRET-JD-MARKER-4471 seeking a PostgreSQL engineer"
    extraction = requirement_draft(draft_req("PostgreSQL", "postgresql"))
    transport = RecordingTransport([
        error_response(429),  # forces a logged retry on extraction
        ok(extraction),
        ok(valid_cv_draft()),
        # the third CV call returns an ungrounded draft to exercise the failure path
        ok({**valid_cv_draft(), "profile": "Engineer experienced with AWS."}),
    ])
    sdk = client_over(transport, [], max_retries=2)
    services = LLMServices(
        settings=settings(),
        extraction_provider=GroqLLMProvider(settings(), sdk, capability_verifier=lambda s, q: {}),
        cv_writer=GroqCVWriter(settings(), sdk),
    )

    handler = _Collect()
    logger = logging.getLogger("careergraph")
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        with api(services) as (client, _):
            analyzed = client.post("/api/jobs/analyze", json={"jobDescription": jd_marker})
            aid = analyzed.json()["analysisId"]
            ok_cv = client.post("/api/cv/generate", json={"analysisId": aid})
            bad_cv = client.post("/api/cv/generate", json={"analysisId": aid})
            status = client.get("/api/llm/status")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)

    assert analyzed.status_code == 200 and analyzed.json()["extraction"]["retried"] is True
    assert ok_cv.status_code == 200 and bad_cv.status_code == 502
    assert bad_cv.json()["detail"]["violations"] == {"gap_claimed": 1}

    log_text = "\n".join(handler.lines)
    assert "llm_call_retry" in log_text and "llm_call " in log_text and "analysis_complete" in log_text
    secrets_and_content = [
        FAKE_KEY, jd_marker, "SECRET-JD-MARKER", "Acme Analytics", ACH_ORDER_LLM, "FastAPI service",
        EXTRACTION_SYSTEM_PROMPT[:60], CV_WRITER_SYSTEM_PROMPT[:60], PROVIDER_BODY_MARKER, "experienced with AWS",
    ]
    for needle in secrets_and_content:
        assert needle not in log_text, f"leaked into logs: {needle[:40]!r}"

    for response in (analyzed, ok_cv, bad_cv, status):
        for needle in (FAKE_KEY, EXTRACTION_SYSTEM_PROMPT[:60], CV_WRITER_SYSTEM_PROMPT[:60], PROVIDER_BODY_MARKER):
            assert needle not in response.text
    assert "experienced with AWS" not in bad_cv.text and "aws" not in bad_cv.text.lower()
