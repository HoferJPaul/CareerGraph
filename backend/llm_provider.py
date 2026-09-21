"""LLM abstraction for JD -> requirements extraction.

`LLMProvider` is the ONE interface the pipeline depends on for extraction. Every job
description passes through `extract_requirements()` and comes out as a validated
`RequirementList` before capability expansion or Neo4j matching ever sees it.

Implementations:

  - backend/llm/groq_extraction.GroqLLMProvider -- the production provider
    (LLM_PROVIDER=groq). All provider-specific code lives under backend/llm/, so another
    provider can be added by implementing this interface -- nothing else changes.

  - DevLLMProvider (this file) -- an explicitly selected DEVELOPMENT FALLBACK
    (LLM_PROVIDER=dev). It never runs implicitly: a failed Groq call is surfaced as an error,
    never silently downgraded to these lower-quality extractors. It composes two honest,
    non-LLM implementations:
      * ManualFileLLMProvider: returns the curated data/requirements.json for the demo job
        description (data/jobs.txt) when the pasted text is recognizably that same JD.
      * HeuristicKeywordLLMProvider: scans any pasted text for literal CareerGraph Skill
        names/aliases. No semantic understanding, no paraphrase handling, no capability
        inference -- an honest keyword matcher, not a stand-in for real extraction.

Every ExtractionResult says which mode produced it (`mode`, `provider`, `model`), so the UI
can label fallback output clearly and never present it as equivalent to a real extraction.
"""
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from capability_suggest import list_vocabulary
from requirement_schema import Requirement, RequirementList

ExtractionMode = str  # "llm_structured" | "cached_manual" | "heuristic_keyword"


def same_job_description(cached_jd: str, pasted_jd: str) -> bool:
    """Deliberately simple, not real semantic matching: if a sizeable verbatim chunk of
    the cached JD's opening text appears in the pasted text, treat it as "the same JD,
    pasted". Shared between ManualFileLLMProvider and extract_requirements.py so both
    sides of the cache-recognition check can never drift apart.
    """
    cached_flat = " ".join(cached_jd.split())
    pasted_flat = " ".join(pasted_jd.split())
    if not pasted_flat:
        return False
    probe = cached_flat[:200]
    return len(probe) > 40 and probe in pasted_flat


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


@dataclass
class ExtractionResult:
    requirements: RequirementList
    mode: ExtractionMode
    note: str
    # Safe debugging metadata -- never prompts, job-description text or provider errors.
    provider: str = "dev"
    model: Optional[str] = None
    usage: Optional[TokenUsage] = None
    retried: bool = False
    attempts: int = 1


class LLMProvider(ABC):
    """Mandatory, first-class extraction stage. Every job description MUST pass through
    extract_requirements() before anything downstream (capability expansion, matching.py,
    tailor_cv.py) ever sees it -- raw job-description text is never an acceptable input to
    the matcher. `ExtractionResult.requirements` is always a validated `RequirementList`
    (see requirement_schema.py), never a bare string or dict.
    """

    @abstractmethod
    def extract_requirements(self, job_description: str, session) -> Optional[ExtractionResult]:
        """Return an ExtractionResult wrapping a validated RequirementList, or None if this
        provider can't handle the input (composed providers fall through to the next one)."""


class ManualFileLLMProvider(LLMProvider):
    """DEV-MODE ADAPTER -- not a real extraction step.

    Returns the existing, human-curated requirements.json (originally authored
    interactively by Claude Code from jobs.txt) whenever the pasted text is
    recognizably the same job description, instead of pretending to run a
    live LLM call.
    """

    def __init__(self, requirements_path: Path, jobs_txt_path: Path):
        self.requirements_path = requirements_path
        self.jobs_txt_path = jobs_txt_path

    def _looks_like_cached_jd(self, job_description: str) -> bool:
        if not self.jobs_txt_path.exists() or not self.requirements_path.exists():
            return False
        cached_jd = self.jobs_txt_path.read_text(encoding="utf-8")
        return same_job_description(cached_jd, job_description)

    def extract_requirements(self, job_description: str, session) -> Optional[ExtractionResult]:
        if not self._looks_like_cached_jd(job_description):
            return None
        data = json.loads(self.requirements_path.read_text(encoding="utf-8"))
        return ExtractionResult(
            requirements=RequirementList.model_validate(data),
            mode="cached_manual",
            note=(
                "Development fallback: recognized this as the demo job description, so it reused the "
                "curated requirements file instead of running a live LLM extraction."
            ),
        )


class HeuristicKeywordLLMProvider(LLMProvider):
    """Fully automatic, zero-semantics fallback: scans the pasted text for literal
    CareerGraph Skill names/aliases (case-insensitive, word-boundary) and emits one
    literal Requirement per hit. No paraphrase handling, no implied requirements, no
    capability inference -- an honest keyword matcher, not a stand-in for real
    extraction.
    """

    _TECHNOLOGY_CATEGORIES = {"technical", "tool", "language"}

    def extract_requirements(self, job_description: str, session) -> ExtractionResult:
        vocabulary = list_vocabulary(session)
        requirements: list[Requirement] = []
        seen: set[str] = set()

        for skill in vocabulary:
            candidates = [skill["name"], *(skill.get("aliases") or [])]
            hit_span = None
            for candidate in candidates:
                if not candidate:
                    continue
                pattern = r"(?<![a-z0-9])" + re.escape(candidate.lower()) + r"(?![a-z0-9])"
                match = re.search(pattern, job_description.lower())
                if match:
                    hit_span = match.span()
                    break
            if not hit_span or skill["name"] in seen:
                continue
            seen.add(skill["name"])
            start = max(0, hit_span[0] - 60)
            end = min(len(job_description), hit_span[1] + 60)
            raw_snippet = job_description[start:end].strip() or skill.get("displayName") or skill["name"]
            category = "technology" if skill.get("category") in self._TECHNOLOGY_CATEGORIES else "capability"
            requirements.append(
                Requirement(
                    raw=raw_snippet,
                    skillQuery=skill["name"],
                    importance="preferred",
                    category=category,
                )
            )

        return ExtractionResult(
            requirements=RequirementList(requirements=requirements),
            mode="heuristic_keyword",
            note=(
                "Development fallback: keyword extraction found literal CareerGraph skill names in "
                "the pasted text. This is not a real LLM extraction -- paraphrased or implied "
                "requirements can be missed."
            ),
        )


class DevLLMProvider(LLMProvider):
    """Composes the two dev-mode providers: cached/manual match first, honest
    automatic keyword matching as the fallback for anything else."""

    def __init__(self, root: Path):
        self._manual = ManualFileLLMProvider(root / "data" / "requirements.json", root / "data" / "jobs.txt")
        self._heuristic = HeuristicKeywordLLMProvider()

    def extract_requirements(self, job_description: str, session) -> ExtractionResult:
        cached = self._manual.extract_requirements(job_description, session)
        if cached is not None:
            return cached
        return self._heuristic.extract_requirements(job_description, session)
