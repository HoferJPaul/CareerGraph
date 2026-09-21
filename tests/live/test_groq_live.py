"""OPTIONAL, manually invoked live integration test against the real Groq API.

It is skipped unless BOTH are true, so a normal test run never spends money or needs a key:

    CAREERGRAPH_LIVE_GROQ=1      explicit opt-in
    GROQ_API_KEY set             in the process environment or the repo-root .env

Run it by hand, from the repo root:

    CAREERGRAPH_LIVE_GROQ=1 python -m pytest tests/live -v -s

It makes two small real calls (one extraction, one CV write) using the models configured in
LLM_EXTRACTION_MODEL / LLM_WRITING_MODEL, against a synthetic job description and the synthetic
fixture career from tests/offline/fixtures.py -- no personal data, and no Neo4j: the capability
verifier is stubbed. A CV rejection here (ProvenanceValidationError) is a REAL signal: the model
produced something the evidence cannot support. The violation codes are printed; rerun once to
distinguish a fluke from a prompt problem.
"""
import os

import pytest

from fixtures import build_cv_context
from llm.config import load_settings
from llm.errors import ProvenanceValidationError
from llm.evidence_registry import build_registry
from llm.groq_client import GroqStructuredClient
from llm.groq_cv_writer import GroqCVWriter
from llm.groq_extraction import GroqLLMProvider
from llm.provenance import validate_structured_cv
from requirement_schema import RequirementList

pytestmark = pytest.mark.skipif(
    os.environ.get("CAREERGRAPH_LIVE_GROQ") != "1",
    reason="live Groq test: set CAREERGRAPH_LIVE_GROQ=1 and GROQ_API_KEY to run (consumes paid API calls)",
)

JD = """Backend Engineer (Python)
You will build and operate APIs for our order-processing platform.
Requirements: 2+ years of Python; PostgreSQL; experience with FastAPI or similar frameworks.
Nice to have: AWS, CloudWatch, Kubernetes. You enjoy debugging production issues."""


def _live_settings():
    settings = load_settings()
    if settings.provider != "groq" or not settings.configured:
        pytest.skip("LLM_PROVIDER=groq with GROQ_API_KEY is required for the live test")
    return settings


def test_live_requirement_extraction_returns_a_validated_requirement_list() -> None:
    settings = _live_settings()
    provider = GroqLLMProvider(
        settings, GroqStructuredClient(settings), capability_verifier=lambda session, queries: {q: None for q in queries}
    )
    result = provider.extract_requirements(JD, session=None)

    assert isinstance(result.requirements, RequirementList)
    queries = {r.skillQuery for r in result.requirements.requirements}
    print(f"\nextracted {len(queries)} requirements with {result.model}: {sorted(queries)}")
    assert {"python", "postgresql"} & queries
    assert result.provider == "groq" and result.mode == "llm_structured"


def test_live_cv_generation_is_grounded_in_the_fixture_evidence() -> None:
    settings = _live_settings()
    ctx = build_cv_context()
    writer = GroqCVWriter(settings, GroqStructuredClient(settings))
    try:
        out = writer.generate(ctx, name="Test Person")
    except ProvenanceValidationError as exc:
        pytest.fail(f"the model produced an ungrounded CV (rejected correctly): {[v.code for v in exc.violations]}")

    report = validate_structured_cv(out.structured_cv, build_registry(ctx))
    print(f"\nwrote {len(report.bullets)} grounded bullets with {out.model}; usage={out.usage}")
    assert report.bullets
    assert "cloudwatch" not in out.structured_cv.model_dump_json().lower()
