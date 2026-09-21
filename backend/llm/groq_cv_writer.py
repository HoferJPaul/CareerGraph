"""Evidence-grounded CV writing (LLM_PROVIDER=groq): an LLM-backed CVWriter.

    CVContext (validated, Neo4j-derived)
      -> EvidenceRegistry               every citable fact gets an id + an owner
      -> writer payload                 only this is sent: no graph, no credentials, no contact
                                        details, no internal scores, no candidate name
      -> Groq structured output         CvDraft (strict JSON Schema)
      -> Pydantic validation of the draft
      -> assembly into StructuredCV     titles/employers/dates copied from the graph, entries
                                        held to the deterministic writer's section rules
      -> provenance validation          rejects (never repairs) unsupported output
      -> WriterOutput

The model writes prose; it cannot create evidence. A response that cites a missing id, cites
nothing, claims a literal gap, lists an unsupported skill, or invents a number is rejected whole.
"""
from llm.config import LLMSettings
from llm.cv_assembly import assemble_structured_cv
from llm.cv_input import build_writer_payload, render_writer_message
from llm.errors import InsufficientEvidenceError
from llm.evidence_registry import build_registry
from llm.groq_client import StructuredClient
from llm.prompts import CV_WRITER_SYSTEM_PROMPT
from llm.provenance import validate_structured_cv
from llm.schemas import CV_DRAFT_SCHEMA, CvDraft, parse_draft
from llm.writer_base import MODE_LLM, ReportingCVWriter, WriterOutput
from cv_writer import DEFAULT_CV_NAME
from tailor_cv import CVContext

WRITER_MAX_COMPLETION_TOKENS = 12288  # gpt-oss counts reasoning tokens against this budget
WRITER_TEMPERATURE = 0.3


class GroqCVWriter(ReportingCVWriter):
    def __init__(self, settings: LLMSettings, client: StructuredClient):
        self._settings = settings
        self._client = client

    def generate(self, cv_context: CVContext, name: str = DEFAULT_CV_NAME) -> WriterOutput:
        registry = build_registry(cv_context)
        if not registry.stories:
            # Nothing verified to write from: refuse rather than pay a model to improvise.
            raise InsufficientEvidenceError()

        payload = build_writer_payload(cv_context, registry)
        message = render_writer_message(payload, self._settings.max_cv_context_chars)

        completion = self._client.complete(
            operation="write_cv",
            model=self._settings.writing_model,
            system_prompt=CV_WRITER_SYSTEM_PROMPT,
            user_content=message,
            schema_name="cv_draft",
            schema=CV_DRAFT_SCHEMA,
            max_completion_tokens=WRITER_MAX_COMPLETION_TOKENS,
            temperature=WRITER_TEMPERATURE,
        )

        draft = parse_draft(CvDraft, completion.data)
        structured_cv = assemble_structured_cv(draft, registry, name)
        validate_structured_cv(structured_cv, registry)  # raises ProvenanceValidationError

        return WriterOutput(
            structured_cv=structured_cv,
            provider="groq",
            model=completion.model,
            mode=MODE_LLM,
            usage=completion.usage,
            attempts=completion.attempts,
            retried=completion.retried,
        )
