"""Provider-response schemas.

The domain models (RequirementList, StructuredCV) are deliberately NOT weakened to suit the
provider: they have optional fields, defaults and header facts (titles, employers, dates) that
strict-mode structured output either cannot express or that the model must not be trusted with.
So the model is asked for these narrower *draft* shapes instead, and every draft is then
converted into the real domain model by deterministic code (groq_extraction.py /
cv_assembly.py) and validated again with Pydantic. Groq guaranteeing schema conformity never
replaces that validation.

`strict_json_schema()` turns a draft model into a Groq strict-mode-compatible JSON Schema:
every object lists ALL its properties as required, `additionalProperties` is false, and
keywords strict mode does not support are stripped.
"""
import logging
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from llm.errors import LLMInvalidResponseError
from requirement_schema import Importance, RequirementCategory

log = logging.getLogger("careergraph.llm")
D = TypeVar("D", bound=BaseModel)

# Keywords Groq's strict mode rejects, plus pydantic noise the model does not need.
_UNSUPPORTED_KEYWORDS = {
    "title", "default", "minLength", "maxLength", "minItems", "maxItems", "pattern", "format",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "uniqueItems", "examples",
}


class _Draft(BaseModel):
    # Groq strict mode already guarantees this shape; forbidding extras makes any drift
    # (a provider bug, a non-strict model) fail loudly instead of being silently ignored.
    model_config = ConfigDict(extra="forbid")


# ---- requirement extraction -------------------------------------------------------------


class DraftRelatedCapability(_Draft):
    skillQuery: str = Field(
        description="A short, lowercase capability name (e.g. 'observability'). It will be checked "
        "against the real skill vocabulary, and discarded if it does not exist there."
    )
    reason: str = Field(description="One plain-language sentence on why this capability is related.")


class DraftRequirement(_Draft):
    raw: str = Field(description="The requirement in the job description's own wording.")
    skillQuery: str = Field(description="Concise, normalized, lowercase skill or capability phrase.")
    importance: Importance
    category: RequirementCategory
    relatedCapabilities: list[DraftRelatedCapability] = Field(
        description="Usually empty. Only underlying capabilities this literal requirement genuinely "
        "implies. Never restate the literal requirement itself."
    )


class RequirementExtractionDraft(_Draft):
    requirements: list[DraftRequirement]


# ---- CV writing -------------------------------------------------------------------------


class DraftBullet(_Draft):
    text: str = Field(description="One recruiter-facing achievement bullet, built only from cited evidence.")
    evidenceIds: list[str] = Field(
        description="One or more evidence ids, copied exactly from the input, that fully support this bullet."
    )


class DraftEntry(_Draft):
    storyId: str = Field(description="The storyId of the input story this entry is about, copied exactly.")
    bullets: list[DraftBullet]


class DraftSkills(_Draft):
    programming: list[str]
    frameworks: list[str]
    databases: list[str]
    tools: list[str]
    capabilities: list[str]


class CvDraft(_Draft):
    headline: str = Field(description="A short professional headline for the target role.")
    profile: str = Field(description="A 2-3 sentence targeted profile using only supported facts.")
    experience: list[DraftEntry]
    projects: list[DraftEntry]
    education: list[DraftEntry]
    skills: DraftSkills
    languages: list[str]


# ---- strict-mode conversion -------------------------------------------------------------


def _strictify(node: Any) -> Any:
    if isinstance(node, list):
        return [_strictify(item) for item in node]
    if not isinstance(node, dict):
        return node
    out = {k: _strictify(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYWORDS}
    props = out.get("properties")
    if isinstance(props, dict):
        # Groq strict mode: every property required, no extras. Keys of "properties" are field
        # names, not keywords, so they must survive even when named like a keyword.
        out["properties"] = {k: _strictify(v) for k, v in node["properties"].items()}
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    return out


def strict_json_schema(model: type[BaseModel]) -> dict:
    return _strictify(model.model_json_schema())


def find_strict_violations(schema: Any, path: str = "$") -> list[str]:
    """Independent check (used by tests) that a schema obeys Groq strict-mode rules."""
    problems: list[str] = []
    if isinstance(schema, list):
        for i, item in enumerate(schema):
            problems += find_strict_violations(item, f"{path}[{i}]")
    elif isinstance(schema, dict):
        for key in schema:
            if key in _UNSUPPORTED_KEYWORDS and not (path.endswith(".properties") or path.endswith(".$defs")):
                problems.append(f"{path}: unsupported keyword {key!r}")
        if "properties" in schema:
            if schema.get("additionalProperties") is not False:
                problems.append(f"{path}: additionalProperties must be false")
            if sorted(schema.get("required", [])) != sorted(schema["properties"]):
                problems.append(f"{path}: every property must be required")
        for key, value in schema.items():
            problems += find_strict_violations(value, f"{path}.{key}")
    return problems


def parse_draft(model: type[D], data: Any) -> D:
    """Validate a model response into its draft type. Pydantic's error text embeds the offending
    input (i.e. model output, possibly CV content), so only the error COUNT is logged and the
    original exception is not chained."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        log.warning("llm_invalid_response draft=%s errors=%d", model.__name__, exc.error_count())
        raise LLMInvalidResponseError() from None


REQUIREMENT_EXTRACTION_SCHEMA = strict_json_schema(RequirementExtractionDraft)
CV_DRAFT_SCHEMA = strict_json_schema(CvDraft)

__all__ = [
    "CV_DRAFT_SCHEMA",
    "REQUIREMENT_EXTRACTION_SCHEMA",
    "CvDraft",
    "DraftBullet",
    "DraftEntry",
    "DraftRelatedCapability",
    "DraftRequirement",
    "DraftSkills",
    "RequirementExtractionDraft",
    "find_strict_violations",
    "parse_draft",
    "strict_json_schema",
]
