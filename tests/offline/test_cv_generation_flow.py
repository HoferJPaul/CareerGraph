"""The validate-then-render choke point: the deterministic renderer only ever receives a StructuredCV
that passed provenance validation, and never sees Markdown, dicts or unvalidated model output."""
import pytest

from fixtures import build_cv_context, valid_cv_draft
from helpers import FakeStructuredClient, settings
from cv_generation import build_cv_document
from cv_markdown import render
from llm.errors import ProvenanceValidationError
from llm.evidence_registry import build_registry
from llm.groq_cv_writer import GroqCVWriter
from llm.provenance import validate_structured_cv
from llm.writer_base import WriterOutput, ReportingCVWriter, MODE_LLM, DeterministicWriterAdapter
from structured_cv import CVBullet, CVExperience, StructuredCV


class Spy:
    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, cv, template):
        self.calls.append((cv, template))
        return render(cv, template)


class UnvalidatedWriter(ReportingCVWriter):
    """A writer that skips its own validation -- the route-level gate must still catch it."""

    def __init__(self, cv: StructuredCV):
        self._cv = cv

    def generate(self, cv_context, name="X"):
        return WriterOutput(structured_cv=self._cv, provider="groq", model="m", mode=MODE_LLM)


def test_renderer_receives_exactly_the_validated_structured_cv() -> None:
    ctx, spy = build_cv_context(), Spy()
    writer = GroqCVWriter(settings(), FakeStructuredClient(valid_cv_draft()))
    doc = build_cv_document(writer, ctx, "modern", renderer=spy)

    assert len(spy.calls) == 1
    rendered_cv, template = spy.calls[0]
    assert isinstance(rendered_cv, StructuredCV) and template == "modern"
    assert rendered_cv is doc.output.structured_cv
    validate_structured_cv(rendered_cv, build_registry(ctx))  # would raise if it were not grounded
    assert doc.markdown.startswith("# ")


def test_rendered_markdown_never_contains_evidence_ids_or_graph_language() -> None:
    doc = build_cv_document(GroqCVWriter(settings(), FakeStructuredClient(valid_cv_draft())), build_cv_context())
    lowered = doc.markdown.lower()
    for leak in ("achievement:", "story:", "transferable:", "evidenceids", "confidence", "match score", "cloudwatch", "aws"):
        assert leak not in lowered
    assert "Acme Analytics" in doc.markdown and "Backend Engineer" in doc.markdown


def test_renderer_is_never_called_when_the_writer_output_fails_validation() -> None:
    bad = StructuredCV(
        name="T", headline="Engineer", profile="Engineer.",
        experience=[CVExperience(title="Backend Engineer", bullets=[CVBullet(text="Built things.", evidenceIds=[])])],
    )
    spy = Spy()
    with pytest.raises(ProvenanceValidationError) as exc:
        build_cv_document(UnvalidatedWriter(bad), build_cv_context(), renderer=spy)
    assert spy.calls == []
    assert {v.code for v in exc.value.violations} == {"empty_evidence"}


def test_renderer_is_never_called_when_the_model_response_is_rejected() -> None:
    draft = valid_cv_draft()
    draft["experience"][0]["bullets"][0]["evidenceIds"] = ["achievement:Invented#1"]
    spy = Spy()
    with pytest.raises(ProvenanceValidationError):
        build_cv_document(GroqCVWriter(settings(), FakeStructuredClient(draft)), build_cv_context(), renderer=spy)
    assert spy.calls == []


def test_the_writer_never_returns_markdown_the_model_wrote() -> None:
    """The model's schema has no markdown field, and a draft carrying one is rejected outright."""
    draft = valid_cv_draft()
    draft["markdown"] = "# Injected CV\n\nI have 20 years of AWS experience."
    from llm.errors import LLMInvalidResponseError
    with pytest.raises(LLMInvalidResponseError):
        GroqCVWriter(settings(), FakeStructuredClient(draft)).generate(build_cv_context())


def test_deterministic_fallback_goes_through_the_same_gate() -> None:
    spy = Spy()
    doc = build_cv_document(DeterministicWriterAdapter(), build_cv_context(), renderer=spy)
    assert len(spy.calls) == 1 and doc.output.mode == "deterministic_fallback"
    assert len(doc.report.bullets) == 6


@pytest.mark.parametrize(
    "cv,code",
    [
        (  # a cited id that is not in the CVContext
            StructuredCV(name="T", headline="Engineer", profile="Engineer.", experience=[CVExperience(
                title="Backend Engineer", bullets=[CVBullet(text="Built things.", evidenceIds=["achievement:Invented#1"])])]),
            "unknown_evidence_id",
        ),
        (  # a literal gap presented as experience, with otherwise valid provenance
            StructuredCV(name="T", headline="Engineer", profile="Engineer.", experience=[CVExperience(
                title="Backend Engineer", bullets=[CVBullet(text="Ran services on AWS.", evidenceIds=["achievement:OrderSync#1"])])]),
            "gap_claimed",
        ),
        (  # an unsupported technology in the skills list
            StructuredCV(name="T", headline="Engineer", profile="Engineer.", skills={"tools": ["Kubernetes"]}),
            "unsupported_skill",
        ),
        (  # a metric that appears in no cited evidence
            StructuredCV(name="T", headline="Engineer", profile="Engineer.", experience=[CVExperience(
                title="Backend Engineer", bullets=[CVBullet(text="Cut costs by 45%.", evidenceIds=["achievement:OrderSync#1"])])]),
            "unsupported_number",
        ),
    ],
)
def test_the_route_level_gate_enforces_every_rule_independently_of_the_writer(cv: StructuredCV, code: str) -> None:
    """The writer's own checks are not trusted: build_cv_document re-validates whatever any
    writer returns, so each rule must hold at the render boundary on its own."""
    spy = Spy()
    with pytest.raises(ProvenanceValidationError) as exc:
        build_cv_document(UnvalidatedWriter(cv), build_cv_context(), renderer=spy)
    assert code in {v.code for v in exc.value.violations}
    assert spy.calls == []
