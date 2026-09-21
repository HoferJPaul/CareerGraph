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


# ---- Source CV (also opt-in; SAME switch and key as above; synthetic fictional CV only) -------------------------------------------------
#
# Two more small real calls each (parse; write + verify [+ repair only if the model needs it]). As above, a
# rejection here is a REAL signal: printed violation codes tell you whether it was a fluke or a prompt problem.


def test_live_source_cv_parse_is_grounded_in_the_document() -> None:
    from datetime import datetime, timezone

    from llm.source_profile import GroqSourceCvParser
    from source_cv.errors import ProfileRejectedError
    from source_cv.grounding import build_profile
    from source_cv.schema import ParserInfo, UploadInfo
    from source_fixtures import EMAIL, cv_text

    settings = _live_settings()
    output = GroqSourceCvParser(settings, GroqStructuredClient(settings)).parse(cv_text())
    try:
        profile = build_profile(
            output.draft, cv_text(),
            upload=UploadInfo(originalFilename="synthetic.pdf", detectedType="pdf", sha256="0" * 64, sizeBytes=1),
            parser=ParserInfo(provider="groq", model=output.model, mode=output.mode), revision=1, now=datetime.now(timezone.utc),
        )
    except ProfileRejectedError as exc:
        pytest.fail(f"the model's parse was rejected (correctly): {exc.violations}")
    print(f"\nparsed {len(profile.employment)} positions, {len(profile.education)} education entries with {output.model}; "
          f"usage={output.usage}; warnings={[w.code for w in profile.warnings]}")
    assert len(profile.employment) == 3 and profile.contact.email == EMAIL
    assert {e.employer for e in profile.employment} == {"Acme Analytics", "Nordwind Logistics", "Kaffeehaus Ringstrasse"}


def test_live_complete_cv_generation_keeps_every_position_and_passes_validation() -> None:
    from cv_generation import build_complete_cv_document
    from llm.complete_writers import GroqCompleteWriter, GroqSupportVerifier
    from source_helpers import make_ci

    settings = _live_settings()
    client = GroqStructuredClient(settings)
    ci = make_ci()
    try:
        document = build_complete_cv_document(GroqCompleteWriter(settings, client), GroqSupportVerifier(settings, client), ci)
    except ProvenanceValidationError as exc:
        pytest.fail(f"the model produced an ungrounded CV (rejected correctly): {[v.code for v in exc.violations]}")
    entries = [*document.structured_cv.experience, *document.structured_cv.additionalExperience]
    print(f"\nwrote a complete CV with {document.model}; repairs={document.repair_attempts}; "
          f"requests={document.attempts}; usage={document.usage}; verification={document.verification}")
    assert {e.entryId for e in entries} == {r.role_id for r in ci.recon.roles}
    assert "cloudwatch" not in document.markdown.lower() and "aws" not in document.markdown.lower()
