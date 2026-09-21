"""The model-facing halves of source-aware CV generation.

    CompleteCvWriter    write(): the CompleteCvDraft;  repair(): fix only the claims that failed validation
        GroqCompleteWriter          LLM_PROVIDER=groq  -- through the existing GroqStructuredClient
        DeterministicCompleteWriter LLM_PROVIDER=dev   -- verbatim evidence, labelled fallback
    SupportVerifier     independent check that source-backed bullets are entailed by what they cite
        GroqSupportVerifier         a separate structured-output call (lexical matching alone cannot tell a
                                    reworded claim from an embellished one)
        VerbatimSupportVerifier     dev: a bullet must be a verbatim restatement of its evidence

Nothing here decides whether a CV is acceptable: cv_generation.build_complete_cv_document runs the
deterministic validators and this loop's verdicts, and only a CV that passes them is ever rendered.
A failed model call raises (llm/errors.py); nothing falls back to a weaker writer.
"""
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import Optional

from cv_writer import DEFAULT_HEADLINE, _build_skills, _is_substantial_project
from llm.complete_registry import GRAPH, CompleteInput, _Redactor, evidence_for_entry, render_complete_message, scopes
from llm.complete_validation import CompleteTrace
from llm.config import LLMSettings
from llm.errors import InsufficientEvidenceError, Violation
from llm.groq_client import StructuredClient
from llm.schemas import DraftBullet, DraftSkills, parse_draft
from llm.source_prompts import COMPLETE_CV_WRITER_SYSTEM_PROMPT, REPAIR_SYSTEM_PROMPT, SUPPORT_VERIFIER_SYSTEM_PROMPT
from llm.source_schemas import (
    COMPLETE_CV_SCHEMA,
    REPAIR_SCHEMA,
    SUPPORT_SCHEMA,
    CompleteCvDraft,
    DraftEntryBullets,
    DraftRole,
    RepairDraft,
    RepairFix,
    SupportVerification,
)
from llm.text import normalize
from llm_provider import TokenUsage

MODE_LLM = "llm_structured"
MODE_DETERMINISTIC = "deterministic_fallback"
WRITER_MAX_COMPLETION_TOKENS = 16384  # gpt-oss counts reasoning tokens against this budget
REPAIR_MAX_COMPLETION_TOKENS = 8192
VERIFY_MAX_COMPLETION_TOKENS = 4096
WRITER_TEMPERATURE = 0.3
REPAIR_TEMPERATURE = 0.2
VERIFY_BATCH = 40
MAX_DETERMINISTIC_BULLETS = 4


def add_usage(a: Optional[TokenUsage], b: Optional[TokenUsage]) -> Optional[TokenUsage]:
    if a is None or b is None:
        return a or b

    def total(x: Optional[int], y: Optional[int]) -> Optional[int]:
        return None if x is None and y is None else (x or 0) + (y or 0)

    return TokenUsage(
        prompt_tokens=total(a.prompt_tokens, b.prompt_tokens),
        completion_tokens=total(a.completion_tokens, b.completion_tokens),
        total_tokens=total(a.total_tokens, b.total_tokens),
    )


@dataclass(frozen=True)
class DraftResult:
    draft: CompleteCvDraft
    provider: str
    model: Optional[str]
    mode: str
    usage: Optional[TokenUsage] = None
    attempts: int = 1  # model requests made so far, transport retries included
    ignored_fixes: int = 0


class CompleteCvWriter(ABC):
    @abstractmethod
    def write(self, ci: CompleteInput) -> DraftResult: ...

    @abstractmethod
    def repair(self, ci: CompleteInput, result: DraftResult, violations: list[Violation]) -> DraftResult:
        """Return a new draft in which only the flagged claims were rewritten or removed."""


# ---- targets: where a violation points in the draft --------------------------------------------------------------


def _bullet_location(draft: CompleteCvDraft, target: str):
    """'bullet:<entry id>#<n>' -> (list of bullets, zero-based index) or None."""
    ref = target.removeprefix("bullet:")
    entry_id, _, number = ref.rpartition("#")
    if not number.isdigit():
        return None
    for group, key in ((draft.roles, "roleId"), (draft.projects, "entryId"), (draft.education, "entryId")):
        for entry in group:
            if getattr(entry, key) == entry_id and 1 <= int(number) <= len(entry.bullets):
                return entry.bullets, int(number) - 1
    return None


def target_text(draft: CompleteCvDraft, target: str) -> Optional[str]:
    if target == "headline":
        return draft.headline
    if target == "profile":
        return draft.profile
    if target.startswith("bullet:"):
        found = _bullet_location(draft, target)
        return found[0][found[1]].text if found else None
    if target.startswith("skill:"):
        return target.split(":", 2)[2]
    return None


def apply_structural_fixes(draft: CompleteCvDraft, ci: CompleteInput) -> CompleteCvDraft:
    """Deterministic clean-up of entries that name things which do not exist or repeat: dropped, no model
    involved. (The assembler already ignored them; this makes the next round's draft clean.)"""
    def first_valid(items, key, valid):
        seen, out = set(), []
        for item in items:
            if key(item) in valid and key(item) not in seen:
                seen.add(key(item))
                out.append(item)
        return out

    fixed = draft.model_copy(deep=True)
    fixed.roles = first_valid(fixed.roles, lambda d: d.roleId, {r.role_id for r in ci.recon.roles})
    fixed.projects = first_valid(fixed.projects, lambda d: d.entryId, {p.entry_id for p in ci.recon.projects})
    fixed.education = first_valid(fixed.education, lambda d: d.entryId, {e.entry_id for e in ci.recon.education})
    sections = {s.id: {i.id for i in s.items} for s in ci.profile.otherSections}
    fixed.otherSections = first_valid(fixed.otherSections, lambda d: d.sectionId, set(sections))
    for pick in fixed.otherSections:
        pick.factIds = [f for f in pick.factIds if f in sections[pick.sectionId]]
    return fixed


def apply_repair_fixes(draft: CompleteCvDraft, fixes: list[RepairFix], flagged: set[str]) -> tuple[CompleteCvDraft, int]:
    """Apply only the fixes that target something that was actually flagged. Returns (draft, ignored)."""
    fixed = draft.model_copy(deep=True)
    ignored = 0
    removals: list[tuple[list[DraftBullet], int]] = []
    for fix in fixes:
        if fix.target not in flagged:
            ignored += 1  # "repair or remove only those claims": anything else the model touched is discarded
            continue
        if fix.target == "headline":
            fixed.headline = fix.text if fix.action == "rewrite" else ""
        elif fix.target == "profile":
            if fix.action == "rewrite":
                fixed.profile = fix.text
                if fix.evidenceIds:
                    fixed.profileEvidenceIds = list(fix.evidenceIds)
            else:
                fixed.profile, fixed.profileEvidenceIds = "", []
        elif fix.target.startswith("bullet:"):
            found = _bullet_location(fixed, fix.target)
            if found is None:
                ignored += 1
                continue
            bullets, index = found
            if fix.action == "remove":
                removals.append((bullets, index))
            else:
                bullets[index] = DraftBullet(text=fix.text, evidenceIds=list(fix.evidenceIds) or bullets[index].evidenceIds)
        elif fix.target.startswith("skill:") and fix.action == "remove":
            _, bucket, name = fix.target.split(":", 2)
            current = getattr(fixed.skills, bucket, None)
            if current is not None:
                setattr(fixed.skills, bucket, [s for s in current if s != name])
        else:
            ignored += 1
    # remove bullets last, highest index first, so earlier indexes stay valid
    for bullets, index in sorted(removals, key=lambda r: (id(r[0]), -r[1])):
        if index < len(bullets):
            del bullets[index]
    return fixed, ignored


# ---- Groq writer ---------------------------------------------------------------------------------------------------------


class GroqCompleteWriter(CompleteCvWriter):
    def __init__(self, settings: LLMSettings, client: StructuredClient):
        self._settings = settings
        self._client = client

    def write(self, ci: CompleteInput) -> DraftResult:
        if not ci.registry.stories and not ci.facts:
            raise InsufficientEvidenceError()  # nothing verified and nothing from the CV to write from
        message = render_complete_message(ci, self._settings.max_cv_context_chars)
        completion = self._client.complete(
            operation="write_complete_cv",
            model=self._settings.writing_model,
            system_prompt=COMPLETE_CV_WRITER_SYSTEM_PROMPT,
            user_content=message,
            schema_name="complete_cv_draft",
            schema=COMPLETE_CV_SCHEMA,
            max_completion_tokens=WRITER_MAX_COMPLETION_TOKENS,
            temperature=WRITER_TEMPERATURE,
        )
        return DraftResult(
            draft=parse_draft(CompleteCvDraft, completion.data), provider="groq", model=completion.model,
            mode=MODE_LLM, usage=completion.usage, attempts=completion.attempts,
        )

    def repair(self, ci: CompleteInput, result: DraftResult, violations: list[Violation]) -> DraftResult:
        draft = apply_structural_fixes(result.draft, ci)
        by_target: dict[str, list[str]] = {}
        for v in violations:
            if v.target and v.target != "structure":
                by_target.setdefault(v.target, [])
                if v.code not in by_target[v.target]:
                    by_target[v.target].append(v.code)
        invalid = []
        for target, codes in by_target.items():
            text = target_text(result.draft, target)
            if text is None:
                continue
            item: dict = {"target": target, "codes": codes, "text": text}
            if target.startswith("bullet:"):
                item["allowedEvidence"] = evidence_for_entry(ci, target.removeprefix("bullet:").rpartition("#")[0])
            elif target == "profile":
                item["allowedEvidence"] = _profile_evidence(ci)
            invalid.append(item)
        if not invalid:
            return replace(result, draft=draft)

        message = json.dumps({"literalGaps": list(ci.registry.gap_terms), "invalid": invalid}, ensure_ascii=False, separators=(",", ":"))
        completion = self._client.complete(
            operation="repair_cv",
            model=self._settings.writing_model,
            system_prompt=REPAIR_SYSTEM_PROMPT,
            user_content=message,
            schema_name="cv_repair",
            schema=REPAIR_SCHEMA,
            max_completion_tokens=REPAIR_MAX_COMPLETION_TOKENS,
            temperature=REPAIR_TEMPERATURE,
        )
        fixes = parse_draft(RepairDraft, completion.data).fixes
        repaired, ignored = apply_repair_fixes(draft, fixes, {i["target"] for i in invalid})
        return replace(
            result, draft=repaired, usage=add_usage(result.usage, completion.usage),
            attempts=result.attempts + completion.attempts, ignored_fixes=result.ignored_fixes + ignored,
        )


def _profile_evidence(ci: CompleteInput) -> list[dict]:
    """A bounded set of evidence a rewritten profile may rest on: the first items of each role."""
    out: list[dict] = []
    for role in ci.recon.roles[:6]:
        out.extend(evidence_for_entry(ci, role.role_id)[:3])
    if ci.profile.summary:
        redact = _Redactor(ci.profile.contact)
        out.append({"evidenceId": f"source:{ci.profile.summary.id}", "text": redact(ci.profile.summary.text)})
    return out[:30]


# ---- deterministic (development) writer ---------------------------------------------------------------------------------------


class DeterministicCompleteWriter(CompleteCvWriter):
    """LLM_PROVIDER=dev: no model. Graph-backed roles are featured with their graph evidence, verbatim;
    every other role is kept compactly with no bullet. Explicitly labelled as a fallback in the UI."""

    def write(self, ci: CompleteInput) -> DraftResult:
        reg = ci.registry
        skill_display = {s.name: s.displayName for s in ci.cv_context.skills}
        language_names = {s.name for s in ci.cv_context.skills if s.category == "language"}

        def graph_bullets(story_ids) -> list[DraftBullet]:
            bullets: list[DraftBullet] = []
            for story_id in story_ids:
                story = reg.stories.get(story_id)
                if story is None:
                    continue
                for n, achievement in enumerate(story.strongestAchievements, start=1):
                    bullets.append(DraftBullet(text=achievement.description, evidenceIds=[f"{GRAPH}achievement:{story.label}#{n}"]))
                if not story.strongestAchievements and story.description:
                    bullets.append(DraftBullet(text=story.description, evidenceIds=[f"{GRAPH}{story_id}"]))
            return bullets[:MAX_DETERMINISTIC_BULLETS]

        roles, cited_stories = [], []
        for role in ci.recon.roles:
            bullets = graph_bullets(role.graph_story_ids) if role.has_graph_evidence else []
            roles.append(DraftRole(roleId=role.role_id, emphasis="featured" if bullets else "compact", bullets=bullets))
            if bullets:
                cited_stories.extend(role.graph_story_ids[:1])

        projects = [
            DraftEntryBullets(entryId=p.entry_id, bullets=graph_bullets((p.graph_story_id,)))
            for p in ci.recon.projects
            if p.graph_story_id and _is_substantial_project(reg.stories[p.graph_story_id], language_names)
        ]
        education = [
            DraftEntryBullets(entryId=e.entry_id, bullets=graph_bullets(e.graph_story_ids)[:MAX_DETERMINISTIC_BULLETS])
            for e in ci.recon.education if e.graph_story_ids
        ]

        profile, profile_ids = "", []
        first_ids = [b.evidenceIds[0] for r in roles for b in r.bullets[:1]]
        if first_ids:
            names = []
            for story_id in cited_stories:
                for skill_name in reg.stories[story_id].directSkills:
                    skill = next((s for s in ci.cv_context.skills if s.name == skill_name), None)
                    if skill and skill.category in ("technical", "tool") and skill_display[skill_name] not in names:
                        names.append(skill_display[skill_name])
            profile = "Software engineer with hands-on experience" + (f" using {', '.join(names[:5])}." if names else ".")
            profile_ids = first_ids[:3]

        skills = _build_skills(ci.cv_context.skills, language_names)
        draft = CompleteCvDraft(
            headline=DEFAULT_HEADLINE, profile=profile, profileEvidenceIds=profile_ids, roles=roles,
            projects=[p for p in projects if p.bullets], education=[e for e in education if e.bullets],
            otherSections=[], skills=DraftSkills(**skills.model_dump()),
        )
        return DraftResult(draft=draft, provider="dev", model=None, mode=MODE_DETERMINISTIC)

    def repair(self, ci: CompleteInput, result: DraftResult, violations: list[Violation]) -> DraftResult:
        from llm.errors import ProvenanceValidationError

        raise ProvenanceValidationError(violations)  # nothing to ask: the fallback writes verbatim evidence or nothing


# ---- semantic support verification ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    ref: str  # the violation target: "profile" or "bullet:<entry id>#<n>"
    text: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class Verification:
    violations: list[Violation]
    usage: Optional[TokenUsage] = None
    attempts: int = 0


def claims_from_traces(traces: list[CompleteTrace]) -> list[Claim]:
    """Only claims that cite the candidate's own CV need the semantic pass; graph-only claims are held to
    the existing graph validator."""
    out = []
    for trace in traces:
        if any(e.origin == "source" for e in trace.evidence):
            ref = "profile" if trace.section == "profile" else f"bullet:{trace.ref}"
            out.append(Claim(ref=ref, text=trace.text, evidence=tuple(e.text for e in trace.evidence)))
    return out


class SupportVerifier(ABC):
    mode: str = "none"

    @abstractmethod
    def verify(self, claims: list[Claim], ci: CompleteInput) -> Verification: ...


class VerbatimSupportVerifier(SupportVerifier):
    mode = "verbatim"

    def verify(self, claims: list[Claim], ci: CompleteInput) -> Verification:
        violations = [
            Violation("unsupported_source_claim", f"{c.ref}: not a verbatim restatement of its evidence", c.ref)
            for c in claims
            if normalize(c.text) not in normalize(" ".join(c.evidence))
        ]
        return Verification(violations)


class GroqSupportVerifier(SupportVerifier):
    mode = "semantic"

    def __init__(self, settings: LLMSettings, client: StructuredClient):
        self._settings = settings
        self._client = client

    def verify(self, claims: list[Claim], ci: CompleteInput) -> Verification:
        redact = _Redactor(ci.profile.contact)
        violations: list[Violation] = []
        usage: Optional[TokenUsage] = None
        attempts = 0
        for start in range(0, len(claims), VERIFY_BATCH):
            batch = claims[start:start + VERIFY_BATCH]
            payload = {"claims": [
                {"ref": c.ref, "text": redact(c.text), "evidence": [redact(e) for e in c.evidence]} for c in batch
            ]}
            completion = self._client.complete(
                operation="verify_claims",
                model=self._settings.writing_model,
                system_prompt=SUPPORT_VERIFIER_SYSTEM_PROMPT,
                user_content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                schema_name="claim_support",
                schema=SUPPORT_SCHEMA,
                max_completion_tokens=VERIFY_MAX_COMPLETION_TOKENS,
                temperature=0.0,
            )
            usage = add_usage(usage, completion.usage)
            attempts += completion.attempts
            verdicts = {v.ref: v.verdict for v in parse_draft(SupportVerification, completion.data).verdicts}
            for claim in batch:
                verdict = verdicts.get(claim.ref)  # a missing verdict fails closed
                if verdict != "supported":
                    violations.append(Violation("unsupported_source_claim", f"{claim.ref}: {verdict or 'no verdict'}", claim.ref))
        return Verification(violations, usage, attempts)


__all__ = [
    "Claim", "CompleteCvWriter", "DeterministicCompleteWriter", "DraftResult", "GroqCompleteWriter",
    "GroqSupportVerifier", "MODE_DETERMINISTIC", "MODE_LLM", "SupportVerifier", "Verification",
    "VerbatimSupportVerifier", "add_usage", "apply_repair_fixes", "apply_structural_fixes", "claims_from_traces",
    "scopes", "target_text",
]
