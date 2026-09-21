"""Groq requirement extraction: structured output -> validated RequirementList, with related
capabilities verified against the graph vocabulary before being accepted."""
import pytest

from helpers import FakeSession, FakeStructuredClient, RecordingTransport, client_over, draft_req, ok, requirement_draft, settings
from llm.errors import LLMInvalidResponseError, LLMRateLimitError, NoRequirementsError
from llm.groq_extraction import GroqLLMProvider, graph_capability_verifier
from llm.schemas import (
    CV_DRAFT_SCHEMA, REQUIREMENT_EXTRACTION_SCHEMA, RequirementExtractionDraft, find_strict_violations,
)
from llm_provider import DevLLMProvider, LLMProvider
from requirement_schema import RequirementList

JD = "We need someone with PostgreSQL and CloudWatch experience. Kubernetes is a plus."


def provider(data, *, verifier=None, **setting_overrides):
    client = FakeStructuredClient(data)
    verify = verifier or (lambda session, queries: {q: None for q in queries})
    return GroqLLMProvider(settings(**setting_overrides), client, capability_verifier=verify), client


def test_valid_extraction_returns_a_validated_requirement_list_with_safe_metadata() -> None:
    p, client = provider(requirement_draft(
        draft_req("Experience with PostgreSQL", "PostgreSQL", "required"),
        draft_req("Kubernetes is a plus", "kubernetes", "preferred"),
    ))
    result = p.extract_requirements(JD, FakeSession())

    assert isinstance(p, LLMProvider)
    assert isinstance(result.requirements, RequirementList)  # the domain model, not a dict
    assert [r.skillQuery for r in result.requirements.requirements] == ["postgresql", "kubernetes"]  # normalized
    assert result.requirements.requirements[0].raw == "Experience with PostgreSQL"  # wording preserved
    assert result.mode == "llm_structured" and result.provider == "groq"
    assert result.model == "openai/gpt-oss-120b" and result.retried is False
    assert result.usage.total_tokens == 150

    call = client.calls[0]
    assert call["schema"] is REQUIREMENT_EXTRACTION_SCHEMA
    assert JD in call["user_content"]  # the JD is data in the user message...
    assert JD not in call["system_prompt"]  # ...never mixed into the instructions


def test_extraction_uses_the_extraction_model_not_the_writing_model() -> None:
    p, client = provider(
        requirement_draft(draft_req()),
        extraction_model="openai/gpt-oss-20b", writing_model="openai/gpt-oss-120b",
    )
    result = p.extract_requirements(JD, FakeSession())
    assert client.calls[0]["model"] == result.model == "openai/gpt-oss-20b"


def test_retry_metadata_is_surfaced() -> None:
    client = FakeStructuredClient(requirement_draft(draft_req()), attempts=3)
    p = GroqLLMProvider(settings(), client, capability_verifier=lambda s, q: {})
    result = p.extract_requirements(JD, FakeSession())
    assert result.retried is True and result.attempts == 3


@pytest.mark.parametrize(
    "bad",
    [
        {},  # missing "requirements"
        {"requirements": [{"raw": "x"}]},  # incomplete requirement
        {"requirements": [draft_req(importance="critical")]},  # not in the Importance enum
        {"requirements": [draft_req(category="hobby")]},  # not in the category enum
        {"requirements": [{**draft_req(), "surprise": 1}]},  # extra fields are rejected, not ignored
        {"requirements": "postgresql"},
        {"requirements": [draft_req(related=[{"skillQuery": "observability"}])]},  # related cap missing reason
    ],
)
def test_invalid_or_incomplete_structured_output_is_rejected_before_matching(bad: dict) -> None:
    p, _ = provider(bad)
    with pytest.raises(LLMInvalidResponseError):
        p.extract_requirements(JD, FakeSession())


def test_an_empty_extraction_is_a_clear_error_not_an_empty_match_run() -> None:
    p, _ = provider(requirement_draft(draft_req(raw="  ", skill="  ")))
    with pytest.raises(NoRequirementsError):
        p.extract_requirements("hello", FakeSession())


def test_duplicates_are_merged_keeping_the_strongest_importance() -> None:
    p, _ = provider(requirement_draft(
        draft_req("Postgres a must", "PostgreSQL", "preferred"),
        draft_req("Strong postgres", " postgresql ", "required"),
        draft_req("postgres, again", "POSTGRESQL", "inferred"),
    ))
    reqs = p.extract_requirements(JD, FakeSession()).requirements.requirements
    assert len(reqs) == 1
    assert reqs[0].importance == "required" and reqs[0].raw == "Postgres a must"


def test_related_capabilities_are_kept_only_if_they_exist_in_the_graph_vocabulary() -> None:
    seen: dict = {}

    def verifier(session, queries):
        seen["queries"] = queries
        return {"observability": "observability", "application logging": None, "made-up ontology node": None}

    p, _ = provider(
        requirement_draft(
            draft_req(
                "CloudWatch", "cloudwatch", related=[
                    {"skillQuery": "Observability", "reason": "CloudWatch is a monitoring service."},
                    {"skillQuery": "application logging", "reason": "It aggregates logs."},
                    {"skillQuery": "made-up ontology node", "reason": "Invented by the model."},
                ],
            )
        ),
        verifier=verifier,
    )
    result = p.extract_requirements(JD, FakeSession())
    caps = result.requirements.requirements[0].relatedCapabilities

    assert [c.skillQuery for c in caps] == ["observability"]  # invented values discarded
    assert caps[0].source == "llm_semantic" and caps[0].reason.startswith("CloudWatch")
    assert sorted(seen["queries"]) == ["application logging", "made-up ontology node", "observability"]
    assert "2 suggested related capabilities were discarded" in result.note


def test_a_related_capability_can_never_be_the_literal_requirement_itself() -> None:
    p, _ = provider(
        requirement_draft(draft_req("Docker", "docker", related=[{"skillQuery": "docker", "reason": "same thing"}])),
        verifier=lambda s, q: {"docker": "docker"},
    )
    assert p.extract_requirements(JD, FakeSession()).requirements.requirements[0].relatedCapabilities == []


def test_related_capabilities_are_capped_at_three() -> None:
    names = ["observability", "monitoring", "logging", "alerting", "tracing"]
    p, _ = provider(
        requirement_draft(draft_req("X", "x", related=[{"skillQuery": n, "reason": "r"} for n in names])),
        verifier=lambda s, q: {n: n for n in q},
    )
    assert len(p.extract_requirements(JD, FakeSession()).requirements.requirements[0].relatedCapabilities) == 3


class _Row(dict):
    pass


class _CandidateSession:
    """Just enough of a Neo4j session for capability_suggest.check_candidates: the fulltext lookup
    returns a fixed vocabulary, and the REAL resolution rules decide what 'exists' means."""

    VOCABULARY = [
        {"name": "observability", "aliases": ["o11y"], "displayName": "Observability", "category": "capability", "score": 3.0},
        {"name": "backend engineering", "aliases": [], "displayName": "Backend Engineering", "category": "capability", "score": 1.0},
    ]

    def run(self, query, **params):
        class Result:
            def single(_self):
                return {"candidates": list(_CandidateSession.VOCABULARY)}
        return Result()


def test_graph_verifier_uses_the_existing_resolution_rules() -> None:
    verified = graph_capability_verifier(_CandidateSession(), ["o11y", "Observability", "site reliability"])
    assert verified["o11y"] == "observability"  # alias -> canonical name
    assert verified["Observability"] == "observability"
    assert verified["site reliability"] is None  # no exact/alias match: not accepted


def test_the_real_sdk_path_end_to_end_over_a_mocked_transport() -> None:
    transport = RecordingTransport([ok(requirement_draft(draft_req()))])
    client = client_over(transport)
    p = GroqLLMProvider(settings(), client, capability_verifier=lambda s, q: {})
    result = p.extract_requirements(JD, FakeSession())
    assert result.requirements.requirements[0].skillQuery == "postgresql"
    assert transport.json_bodies[0]["response_format"]["json_schema"]["strict"] is True


def test_a_failed_groq_call_raises_and_never_falls_back_to_the_heuristic_extractor() -> None:
    client = FakeStructuredClient(error=LLMRateLimitError())
    p = GroqLLMProvider(settings(), client, capability_verifier=lambda s, q: {})
    with pytest.raises(LLMRateLimitError):
        p.extract_requirements(JD, FakeSession())
    assert not isinstance(p, DevLLMProvider)
    # Structural guard: the production provider never imports or references the dev fallbacks
    # (checked on real code references, not docstrings).
    import ast
    import llm.groq_extraction as module
    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    referenced = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    referenced |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert not referenced & {"DevLLMProvider", "HeuristicKeywordLLMProvider", "ManualFileLLMProvider"}


def test_provider_response_schemas_obey_groq_strict_mode() -> None:
    for schema in (REQUIREMENT_EXTRACTION_SCHEMA, CV_DRAFT_SCHEMA):
        assert find_strict_violations(schema) == []


def test_domain_models_are_not_weakened_to_suit_the_provider() -> None:
    """The provider draft is separate from RequirementList: the domain model keeps its defaults,
    and pydantic's native schema is NOT strict-compatible (which is why drafts exist)."""
    native = RequirementList.model_json_schema()
    assert find_strict_violations(native) != []
    assert RequirementExtractionDraft is not RequirementList


def test_extraction_output_still_passes_the_exact_requirement_list_boundary() -> None:
    """Requirement extraction output must round-trip through RequirementList unchanged -- the same
    model /api/requirements/analyze validates request bodies against."""
    p, _ = provider(requirement_draft(draft_req("Docker", "docker", related=[{"skillQuery": "containers", "reason": "r"}])),
                    verifier=lambda s, q: {"containers": "containerization"})
    result = p.extract_requirements(JD, FakeSession())
    dumped = result.requirements.model_dump()
    assert RequirementList.model_validate(dumped) == result.requirements
    assert set(dumped["requirements"][0]) == {"raw", "skillQuery", "importance", "category", "relatedCapabilities"}
