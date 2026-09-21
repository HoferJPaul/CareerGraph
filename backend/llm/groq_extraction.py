"""Production requirement extraction (LLM_PROVIDER=groq), behind the LLMProvider interface.

    job description
      -> Groq structured output (RequirementExtractionDraft, strict JSON Schema)
      -> Pydantic validation of the draft
      -> deterministic normalization (whitespace, lowercase skillQuery, dedupe, cap)
      -> related capabilities VERIFIED against the graph vocabulary (invented ones discarded)
      -> RequirementList.model_validate(...)   <- the domain validation boundary
      -> ExtractionResult

Nothing the model returns reaches Neo4j matching without passing through the last two steps.
A failed Groq call raises (see errors.py); this provider never falls back to a lower-quality
extractor -- the explicit development fallback is DevLLMProvider (LLM_PROVIDER=dev).
"""
from typing import Any, Callable, Optional

from capability_suggest import check_candidates
from llm.config import LLMSettings
from llm.errors import NoRequirementsError
from llm.groq_client import StructuredClient
from llm.prompts import EXTRACTION_SYSTEM_PROMPT
from llm.schemas import (
    REQUIREMENT_EXTRACTION_SCHEMA,
    RequirementExtractionDraft,
    parse_draft,
)
from llm.text import normalize
from llm_provider import ExtractionResult, LLMProvider
from requirement_schema import RelatedCapability, RequirementList

MAX_REQUIREMENTS = 50
MAX_RELATED_PER_REQUIREMENT = 3
EXTRACTION_MAX_COMPLETION_TOKENS = 8192  # gpt-oss counts reasoning tokens against this budget
EXTRACTION_TEMPERATURE = 0.1
_IMPORTANCE_RANK = {"required": 0, "preferred": 1, "inferred": 2}

# (neo4j session, capability names) -> {name: canonical skill name, or None if not in the graph}
CapabilityVerifier = Callable[[Any, list[str]], dict[str, Optional[str]]]


def graph_capability_verifier(session, queries: list[str]) -> dict[str, Optional[str]]:
    """Confirms each proposed capability is a real Skill (canonical name or alias) using the
    graph's own resolution rules -- capability_suggest.check_candidates, read-only."""
    return {
        row["query"]: (row["canonicalSkill"] if row["exists"] else None)
        for row in check_candidates(session, queries)
    }


def _normalize_draft(draft: RequirementExtractionDraft) -> list[dict]:
    """Whitespace/case normalization, dedupe by skillQuery (keeping the strongest importance),
    and a hard cap. Purely mechanical -- adds no requirement the model did not return."""
    merged: dict[str, dict] = {}
    for req in draft.requirements:
        raw = " ".join(req.raw.split())
        query = normalize(req.skillQuery)
        if not raw or not query:
            continue
        related = []
        for cap in req.relatedCapabilities:
            cap_query = normalize(cap.skillQuery)
            if cap_query and cap_query != query and cap_query not in {r["query"] for r in related}:
                related.append({"query": cap_query, "reason": " ".join(cap.reason.split())})
        existing = merged.get(query)
        if existing is None:
            merged[query] = {
                "raw": raw, "skillQuery": query, "importance": req.importance,
                "category": req.category, "related": related,
            }
        else:
            if _IMPORTANCE_RANK[req.importance] < _IMPORTANCE_RANK[existing["importance"]]:
                existing["importance"] = req.importance
            known = {r["query"] for r in existing["related"]}
            existing["related"] += [r for r in related if r["query"] not in known]
    return list(merged.values())[:MAX_REQUIREMENTS]


class GroqLLMProvider(LLMProvider):
    def __init__(
        self,
        settings: LLMSettings,
        client: StructuredClient,
        *,
        capability_verifier: CapabilityVerifier = graph_capability_verifier,
    ):
        self._settings = settings
        self._client = client
        self._verify_capabilities = capability_verifier

    def extract_requirements(self, job_description: str, session) -> ExtractionResult:
        completion = self._client.complete(
            operation="extract_requirements",
            model=self._settings.extraction_model,
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            user_content=f"<job_description>\n{job_description}\n</job_description>",
            schema_name="requirement_list",
            schema=REQUIREMENT_EXTRACTION_SCHEMA,
            max_completion_tokens=EXTRACTION_MAX_COMPLETION_TOKENS,
            temperature=EXTRACTION_TEMPERATURE,
        )
        draft = parse_draft(RequirementExtractionDraft, completion.data)
        normalized = _normalize_draft(draft)
        if not normalized:
            raise NoRequirementsError()

        proposed = sorted({r["query"] for req in normalized for r in req["related"]})
        verified = self._verify_capabilities(session, proposed) if proposed else {}
        discarded = 0

        requirements = []
        for req in normalized:
            related: list[RelatedCapability] = []
            for cap in req["related"]:
                canonical = verified.get(cap["query"])
                if canonical is None:
                    discarded += 1  # not in the graph vocabulary: never accepted
                elif canonical != req["skillQuery"] and canonical not in {c.skillQuery for c in related}:
                    related.append(
                        RelatedCapability(skillQuery=canonical, reason=cap["reason"], source="llm_semantic")
                    )
            requirements.append(
                {
                    "raw": req["raw"],
                    "skillQuery": req["skillQuery"],
                    "importance": req["importance"],
                    "category": req["category"],
                    "relatedCapabilities": [c.model_dump() for c in related[:MAX_RELATED_PER_REQUIREMENT]],
                }
            )

        # The domain validation boundary: exactly the model the matcher consumes.
        requirement_list = RequirementList.model_validate({"requirements": requirements})

        note = (
            f"Extracted by {completion.model} via Groq and validated against the requirement schema."
        )
        if discarded:
            note += (
                f" {discarded} suggested related capabilit{'y was' if discarded == 1 else 'ies were'} "
                "discarded because not found in the skill vocabulary."
            )
        return ExtractionResult(
            requirements=requirement_list,
            mode="llm_structured",
            note=note,
            provider="groq",
            model=completion.model,
            usage=completion.usage,
            retried=completion.retried,
            attempts=completion.attempts,
        )
