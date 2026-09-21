"""Provenance validation: the gate every StructuredCV passes before it can be rendered.

    The LLM writes. The graph proves.

A generated CV is REJECTED (ProvenanceValidationError) -- never repaired -- if any rule fails.
Silently dropping a bad evidence id or bullet would leave the surrounding prose standing on
nothing, so failures are all-or-nothing.

Rules (each failure has a stable violation code):
  empty_evidence      a bullet cites no evidence id
  unknown_evidence_id a bullet cites an id that is not in the supplied CVContext
  gap_claimed         a literal gap (a requirement with NO literal evidence) is named anywhere in
                      the CV -- headline, profile, bullets, skills. Transferable evidence may
                      support a broader capability statement, never the missing technology.
  unsupported_skill   a listed skill/language is not one the CVContext supports
  unsupported_number  a number in the prose appears in no cited evidence (metrics must be exact)
  internal_language   graph/system terms (ids, confidence, match scores...) leaked into the prose
  empty_text          a bullet with no text

The checks are deliberately lexical and conservative: they catch the dominant failure mode
(a model pulling technologies out of the job description into the CV) deterministically. They
cannot detect a paraphrase of a gap, or a wrong-but-plausible claim that reuses supported words;
that residual risk is why every bullet must also carry citable evidence, and why the UI
exposes each bullet's provenance for human review.
"""
import re
from dataclasses import dataclass
from typing import Iterable

from llm.errors import ProvenanceValidationError, Violation
from llm.evidence_registry import EvidenceItem, EvidenceRegistry
from llm.text import mentions_term, normalize
from structured_cv import CVBullet, StructuredCV

_NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*")

_INTERNAL_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:story|achievement|transferable|education):\S",  # evidence-id prefixes
        r"evidence\s*ids?\b",
        r"match\s*(?:confidence|score)",
        r"confidence\s*score",
        r"evidence\s*strength",
        r"transferable\s+(?:evidence|capabilit)",
        r"literal\s+gaps?\b",
        r"evidence-backed",
        r"skills used:",
        r"knowledge graph",
    )
]


@dataclass(frozen=True)
class BulletTrace:
    section: str  # "experience" | "projects" | "education"
    entry: str
    text: str
    evidence: tuple[EvidenceItem, ...]


@dataclass(frozen=True)
class ProvenanceReport:
    bullets: tuple[BulletTrace, ...]


def numbers_in(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _NUMBER_RE.finditer(text)}


def _prose_violations(where: str, text: str, registry: EvidenceRegistry) -> list[Violation]:
    out: list[Violation] = []
    for term in registry.gap_terms:
        if mentions_term(text, term):
            out.append(Violation("gap_claimed", f"{where}: names literal gap {term!r}"))
    for pattern in _INTERNAL_PATTERNS:
        if pattern.search(text):
            out.append(Violation("internal_language", f"{where}: matches {pattern.pattern!r}"))
    return out


def _check_numbers(where: str, text: str, corpus: str) -> list[Violation]:
    supported = numbers_in(corpus)
    return [
        Violation("unsupported_number", f"{where}: {number!r} appears in no supporting evidence")
        for number in sorted(numbers_in(text) - supported)
    ]


def _check_bullet(
    where: str, bullet: CVBullet, registry: EvidenceRegistry
) -> tuple[list[Violation], tuple[EvidenceItem, ...]]:
    violations: list[Violation] = []
    if not bullet.text.strip():
        violations.append(Violation("empty_text", where))
    if not bullet.evidenceIds:
        violations.append(Violation("empty_evidence", f"{where}: bullet has no evidence ids"))
        return violations, ()

    items: list[EvidenceItem] = []
    for evidence_id in bullet.evidenceIds:
        item = registry.resolve(evidence_id)
        if item is None:
            violations.append(Violation("unknown_evidence_id", f"{where}: {evidence_id!r}"))
        else:
            items.append(item)
    if items:
        corpus = " ".join(
            [i.text for i in items] + [registry.story_texts.get(i.owner, "") for i in items if i.owner]
        )
        violations += _check_numbers(where, bullet.text, corpus)
    violations += _prose_violations(where, bullet.text, registry)
    return violations, tuple(items)


def _check_skills(cv: StructuredCV, registry: EvidenceRegistry) -> list[Violation]:
    out: list[Violation] = []
    buckets = cv.skills.model_dump()
    for bucket, names in buckets.items():
        for name in names:
            if normalize(name) not in registry.allowed_skills:
                out.append(Violation("unsupported_skill", f"skills.{bucket}: {name!r} is not a supported skill"))
            out += [Violation(v.code, f"skills.{bucket}: {v.detail}") for v in _prose_violations("", name, registry)]
    for name in cv.languages:
        if normalize(name) not in registry.language_skills:
            out.append(Violation("unsupported_skill", f"languages: {name!r} is not a supported language"))
    return out


def _entries(cv: StructuredCV) -> Iterable[tuple[str, str, list[CVBullet]]]:
    for exp in cv.experience:
        label = f"{exp.title} — {exp.organization}" if exp.organization else exp.title
        yield "experience", label, exp.bullets
    for proj in cv.projects:
        yield "projects", proj.name, proj.bullets
    for edu in cv.education:
        yield "education", edu.institution, edu.bullets


def validate_structured_cv(cv: StructuredCV, registry: EvidenceRegistry) -> ProvenanceReport:
    """Raise ProvenanceValidationError if `cv` is not fully grounded in `registry`'s CVContext;
    otherwise return each bullet's resolved evidence (for the UI's provenance view)."""
    violations: list[Violation] = []
    traces: list[BulletTrace] = []

    for label, text in (("headline", cv.headline), ("profile", cv.profile)):
        violations += _prose_violations(label, text, registry)
        violations += _check_numbers(label, text, registry.global_text())

    for section, entry, bullets in _entries(cv):
        for i, bullet in enumerate(bullets, start=1):
            found, items = _check_bullet(f"{section}[{entry}] bullet {i}", bullet, registry)
            violations += found
            traces.append(BulletTrace(section=section, entry=entry, text=bullet.text, evidence=items))

    violations += _check_skills(cv, registry)

    if violations:
        raise ProvenanceValidationError(violations)
    return ProvenanceReport(bullets=tuple(traces))
