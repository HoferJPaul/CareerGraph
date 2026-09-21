"""Evidence-grounded CV generation: Groq output -> StructuredCV, and rejection (never repair) of
anything the CVContext cannot prove."""
import copy

import pytest

from fixtures import build_cv_context, valid_cv_draft
from helpers import FakeStructuredClient, settings
from llm.errors import (
    InputTooLargeError, InsufficientEvidenceError, LLMInvalidResponseError, LLMRateLimitError,
    ProvenanceValidationError,
)
from llm.evidence_registry import build_registry
from llm.groq_cv_writer import GroqCVWriter
from llm.provenance import validate_structured_cv
from llm.schemas import CV_DRAFT_SCHEMA
from llm.writer_base import DeterministicWriterAdapter, ReportingCVWriter
from cv_writer import CVWriter, DeterministicCVWriter
from structured_cv import StructuredCV
from tailor_cv import CVContext


def write(draft: dict, **kw):
    client = FakeStructuredClient(draft)
    writer = GroqCVWriter(settings(**kw), client)
    return writer, client


def rejected(mutate, ctx: CVContext | None = None) -> set[str]:
    draft = valid_cv_draft()
    mutate(draft)
    writer, _ = write(draft)
    with pytest.raises(ProvenanceValidationError) as exc:
        writer.generate(ctx or build_cv_context())
    return {v.code for v in exc.value.violations}


# ---- happy path -----------------------------------------------------------------------------


def test_groq_output_becomes_a_validated_structured_cv() -> None:
    writer, client = write(valid_cv_draft())
    out = writer.generate(build_cv_context(), name="Test Person")

    assert isinstance(writer, ReportingCVWriter) and isinstance(writer, CVWriter)
    assert isinstance(out.structured_cv, StructuredCV)
    assert StructuredCV.model_validate(out.structured_cv.model_dump()) == out.structured_cv
    assert out.mode == "llm_structured" and out.provider == "groq" and out.model == "openai/gpt-oss-120b"
    assert client.calls[0]["schema"] is CV_DRAFT_SCHEMA
    assert client.calls[0]["model"] == "openai/gpt-oss-120b"


def test_writing_uses_the_writing_model() -> None:
    writer, client = write(valid_cv_draft(), extraction_model="openai/gpt-oss-20b", writing_model="openai/gpt-oss-120b")
    writer.generate(build_cv_context())
    assert client.calls[0]["model"] == "openai/gpt-oss-120b"


def test_facts_about_entries_come_from_the_graph_not_the_model() -> None:
    cv = write(valid_cv_draft())[0].generate(build_cv_context(), name="Test Person").structured_cv

    assert cv.name == "Test Person"  # supplied by the application, never sent to the model
    exp = cv.experience[0]
    assert (exp.title, exp.organization, exp.location) == ("Backend Engineer", "Acme Analytics", "Remote")
    assert (exp.startDate, exp.endDate) == ("Apr 2025", "Sep 2025")
    assert cv.projects[0].name == "TrailMap" and cv.projects[0].startDate == "Oct 2025"
    assert cv.education[0].institution == "Riverbank University"
    assert cv.education[0].program == "BSc Computer Science"


def test_every_bullet_carries_canonical_evidence_ids_that_exist_in_the_context() -> None:
    ctx = build_cv_context()
    cv = write(valid_cv_draft())[0].generate(ctx).structured_cv
    registry = build_registry(ctx)
    bullets = [b for e in cv.experience for b in e.bullets] + [b for p in cv.projects for b in p.bullets] + [
        b for e in cv.education for b in e.bullets
    ]
    assert len(bullets) == 6
    for b in bullets:
        assert b.evidenceIds
        assert all(registry.resolve(i) is not None for i in b.evidenceIds)


def test_transferable_evidence_may_support_a_capability_bullet_without_naming_the_gap() -> None:
    cv = write(valid_cv_draft())[0].generate(build_cv_context()).structured_cv
    observability = [b for b in cv.experience[0].bullets if "monitor" in b.text][0]
    assert observability.evidenceIds == ["transferable:observability#1"]
    assert "cloudwatch" not in cv.model_dump_json().lower()


def test_legacy_deterministic_evidence_ids_and_aliases_resolve_to_canonical_ids() -> None:
    draft = valid_cv_draft()
    draft["experience"][0]["bullets"][0]["evidenceIds"] = [
        "Built a FastAPI service that extracts structured order data from PDFs with an LLM, "
        "cutting manual data entry time by 60%."
    ]
    draft["education"][0]["bullets"][0]["evidenceIds"] = ["education:Riverbank University"]
    cv = write(draft)[0].generate(build_cv_context()).structured_cv
    assert cv.experience[0].bullets[0].evidenceIds == ["achievement:OrderSync#1"]
    assert cv.education[0].bullets[0].evidenceIds == ["story:Riverbank University"]


# ---- rejection ------------------------------------------------------------------------------


def test_a_bullet_without_evidence_ids_is_rejected() -> None:
    assert "empty_evidence" in rejected(lambda d: d["experience"][0]["bullets"][0].update(evidenceIds=[]))


def test_an_unknown_evidence_id_is_rejected_not_silently_dropped() -> None:
    def mutate(d):
        d["experience"][0]["bullets"][0]["evidenceIds"] = ["achievement:OrderSync#1", "achievement:Invented#7"]

    # A valid id sits next to the bogus one: the draft must still be rejected whole.
    assert "unknown_evidence_id" in rejected(mutate)


@pytest.mark.parametrize(
    "text",
    [
        "Deployed and monitored services on AWS.",  # literal gap: aws (nothing supports it)
        "Set up CloudWatch dashboards for API latency.",  # literal gap that HAS transferable evidence
        "Built Node/Express services with JWT authentication.",  # literal gap: express, transferable backend evidence
        "Experienced with cloudwatch-style dashboards.",  # "similar to" phrasing is still naming the gap
    ],
)
def test_a_literal_gap_presented_as_direct_experience_is_rejected(text: str) -> None:
    codes = rejected(lambda d: d["experience"][0]["bullets"][2].update(text=text))
    assert "gap_claimed" in codes


def test_gap_terms_are_matched_as_whole_words() -> None:
    """'laws' contains 'aws' but is not the technology: no false positive."""
    draft = valid_cv_draft()
    draft["experience"][0]["bullets"][0]["text"] = (
        "Built a FastAPI service that extracts structured order data from PDFs with an LLM, "
        "cutting manual data entry time by 60% and easing compliance with data laws."
    )
    writer, _ = write(draft)
    writer.generate(build_cv_context())  # does not raise


def test_gaps_are_rejected_in_the_profile_headline_and_skills_too() -> None:
    assert "gap_claimed" in rejected(lambda d: d.update(profile="Backend engineer experienced with AWS and Python."))
    assert "gap_claimed" in rejected(lambda d: d.update(headline="AWS Backend Engineer"))
    codes = rejected(lambda d: d["skills"]["tools"].append("AWS"))
    assert {"gap_claimed", "unsupported_skill"} <= codes


def test_unsupported_skills_and_languages_are_rejected() -> None:
    assert "unsupported_skill" in rejected(lambda d: d["skills"]["frameworks"].append("Django"))
    assert "unsupported_skill" in rejected(lambda d: d.update(languages=["German", "Klingon"]))
    assert "unsupported_skill" in rejected(lambda d: d.update(languages=["Python"]))  # not a human language


def test_invented_metrics_are_rejected_but_metrics_from_the_evidence_pass() -> None:
    assert "unsupported_number" in rejected(
        lambda d: d["experience"][0]["bullets"][1].update(text="Designed the PostgreSQL schema for 10,000 tenants.")
    )
    assert "unsupported_number" in rejected(lambda d: d.update(profile="Engineer with 7 years of experience."))
    write(valid_cv_draft())[0].generate(build_cv_context())  # "60%" is in the cited evidence


def test_evidence_must_belong_to_the_entry_it_is_cited_under() -> None:
    codes = rejected(
        lambda d: d["experience"][0]["bullets"][0].update(evidenceIds=["achievement:TrailMap#1"])
    )
    assert "foreign_evidence" in codes
    # Transferable evidence is owned by its own story too (backend engineering -> TrailMap).
    assert "foreign_evidence" in rejected(
        lambda d: d["experience"][0]["bullets"][2].update(evidenceIds=["transferable:backend engineering#1"])
    )


def test_entries_must_be_real_stories_in_the_section_the_deterministic_rules_assign() -> None:
    assert "unknown_story" in rejected(lambda d: d["experience"].append({"storyId": "story:Made Up Corp", "bullets": []}))
    # OrderSync is professional: it is experience, not a "project".
    def as_project(d):
        d["projects"].append({"storyId": "story:OrderSync", "bullets": copy.deepcopy(d["experience"][0]["bullets"][:1])})

    assert "wrong_section" in rejected(as_project)

    # Peer Tutor folds into the Education entry; it is never its own experience entry.
    def standalone_tutor(d):
        d["experience"].append(
            {"storyId": "story:Peer Tutor", "bullets": [{"text": "Tutored students.", "evidenceIds": ["achievement:Peer Tutor#1"]}]}
        )

    assert "wrong_section" in rejected(standalone_tutor)


def test_internal_graph_language_in_prose_is_rejected() -> None:
    for text in (
        "Strong match (match confidence: high) for backend work.",
        "Demonstrates transferable evidence of monitoring.",
        "See evidenceIds achievement:OrderSync#1 for details.",
    ):
        assert "internal_language" in rejected(lambda d, t=text: d["experience"][0]["bullets"][1].update(text=t))


def test_runaway_output_is_rejected() -> None:
    assert "length_limit" in rejected(lambda d: d["experience"][0]["bullets"][0].update(text="word " * 200))
    assert "empty_entry" in rejected(lambda d: d["projects"][0].update(bullets=[]))


def test_violations_are_all_reported_per_stage_and_the_api_detail_exposes_only_codes() -> None:
    draft = valid_cv_draft()
    draft["experience"][0]["bullets"][0]["evidenceIds"] = []
    draft["experience"][0]["bullets"][1]["evidenceIds"] = ["achievement:Invented#1"]
    writer, _ = write(draft)
    with pytest.raises(ProvenanceValidationError) as exc:
        writer.generate(build_cv_context())
    detail = exc.value.to_detail()
    assert detail["violations"] == {"empty_evidence": 1, "unknown_evidence_id": 1}
    assert "invented" not in str(detail).lower()  # ids, terms and evidence text stay out of the API response

    prose = valid_cv_draft()
    prose["experience"][0]["bullets"][1]["text"] = "Deployed to AWS."
    with pytest.raises(ProvenanceValidationError) as exc:
        write(prose)[0].generate(build_cv_context())
    assert exc.value.to_detail()["violations"] == {"gap_claimed": 1}
    assert "aws" not in str(exc.value.to_detail()).lower()


# ---- input / output boundaries ----------------------------------------------------------------


def test_malformed_draft_shapes_are_invalid_responses() -> None:
    for bad in ({}, {"headline": "x"}, {**valid_cv_draft(), "name": "Injected Name"}, "text"):
        writer, _ = write(bad)
        with pytest.raises(LLMInvalidResponseError):
            writer.generate(build_cv_context())


def test_no_evidence_means_no_model_call() -> None:
    ctx = build_cv_context().model_copy(update={"evidenceStories": []})
    writer, client = write(valid_cv_draft())
    with pytest.raises(InsufficientEvidenceError):
        writer.generate(ctx)
    assert client.calls == []


def test_oversized_context_is_refused_before_any_model_call() -> None:
    writer, client = write(valid_cv_draft(), max_cv_context_chars=1000)
    with pytest.raises(InputTooLargeError):
        writer.generate(build_cv_context())
    assert client.calls == []


def test_provider_failures_propagate_and_do_not_fall_back_to_the_deterministic_writer() -> None:
    client = FakeStructuredClient(error=LLMRateLimitError())
    with pytest.raises(LLMRateLimitError):
        GroqCVWriter(settings(), client).generate(build_cv_context())


# ---- the deterministic writer stays a valid oracle -------------------------------------------


def test_deterministic_writer_output_satisfies_the_same_provenance_rules() -> None:
    ctx = build_cv_context()
    report = validate_structured_cv(DeterministicCVWriter().write(ctx), build_registry(ctx))
    assert len(report.bullets) == 6


def test_deterministic_writer_output_is_unchanged_by_the_shared_helper_refactor() -> None:
    ctx = build_cv_context()
    cv = DeterministicWriterAdapter().generate(ctx).structured_cv
    assert cv.experience[0].title == "Backend Engineer" and cv.experience[0].organization == "Acme Analytics"
    assert [p.name for p in cv.projects] == ["TrailMap"]  # Village Chronicle (language-only) omitted
    assert [b.text for b in cv.education[0].bullets] == [
        "Software engineering curriculum covering systems programming and databases.",
        "Tutored 30 students in debugging and C programming.",
        "Implemented a Unix shell in C.",
    ]
    assert cv.languages == ["German"]


def test_dev_fallback_output_is_labelled_as_a_fallback() -> None:
    out = DeterministicWriterAdapter().generate(build_cv_context())
    assert out.mode == "deterministic_fallback" and out.provider == "dev" and out.model is None
