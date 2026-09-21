"""Build the ONLY thing the CV-writing model is shown.

Input is the validated CVContext (via its EvidenceRegistry) -- never raw claims from anywhere
else, never the whole graph, never Neo4j credentials, and no contact details. What is
deliberately left out: cvGuidance/match confidence/evidence strength/recommendation values (they
are internal signals the model could leak into prose; the rules live in the system prompt), the
candidate's name (the application sets it), and any e-mail/phone-like text (best-effort
redaction below).
"""
import json
import re

from cv_writer import project_header
from llm.errors import InputTooLargeError
from llm.evidence_registry import EvidenceRegistry
from tailor_cv import CVContext

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# International (+CC ...) and (NNN) NNN-NNNN forms only. Deliberately narrow: a looser phone
# pattern would eat ISO dates ("2025-04-01") and metrics ("1,000,000").
_PHONE_RE = re.compile(r"\+\d{1,3}[\s.-]?(?:\(?\d{1,4}\)?[\s.-]?){2,4}\d{2,4}|\(\d{3}\)\s?\d{3}-\d{4}")


def redact_contact_details(text: str) -> str:
    return _PHONE_RE.sub("[redacted]", _EMAIL_RE.sub("[redacted]", text))


def _period(story) -> str | None:
    """The same 'Jan 2025 – Present' period the rendered entry header will show."""
    header = project_header(story)
    if not header.startDate:
        return None
    return f"{header.startDate} – {header.endDate}" if header.endDate else f"{header.startDate} – Present"


def build_writer_payload(cv_context: CVContext, registry: EvidenceRegistry) -> dict:
    skill_display = {s.name: s.displayName for s in cv_context.skills}
    red = redact_contact_details

    stories = []
    for story_id, story in registry.stories.items():
        placement = registry.placements[story_id]
        facts = {
            "title": story.roleTitle or story.label,
            "organization": story.company,
            "project": story.project,
            "role": story.personRole,
            "program": story.program,
            "period": _period(story),
            "context": story.context,
            "description": story.description,
            "domain": story.domain,
        }
        stories.append(
            {
                "storyId": story_id,
                "section": placement.section,
                "foldsInto": placement.folds_into,
                "facts": {k: red(v) for k, v in facts.items() if v},
                "evidenceId": story_id,
                "skills": [skill_display.get(s, s) for s in story.directSkills],
                "achievements": [
                    {
                        "evidenceId": f"achievement:{story.label}#{n}",
                        "text": red(a.description),
                        "skills": [skill_display.get(s, s) for s in a.matchedSkills],
                    }
                    for n, a in enumerate(story.strongestAchievements, start=1)
                ],
                "transferable": [
                    {
                        "evidenceId": item.id,
                        "capability": item.transferable_for,
                        "text": red(item.text),
                    }
                    for item in registry.items.values()
                    if item.kind == "transferable" and item.owner == story_id
                ],
            }
        )

    return {
        "targetRequirements": [
            {
                "skillQuery": r.get("skillQuery"),
                "importance": r.get("importance"),
                "category": r.get("category"),
            }
            for r in cv_context.requirements
        ],
        "literalGaps": list(registry.gap_terms),
        "availableSkills": [
            {"name": s.displayName, "category": s.category or None} for s in cv_context.skills
        ]
        + [
            {"name": display, "category": "related capability"}
            for norm, display in registry.allowed_skills.items()
            if not any(norm == s.name.lower() or norm == s.displayName.lower() for s in cv_context.skills)
        ],
        "stories": stories,
    }


def render_writer_message(payload: dict, max_chars: int) -> str:
    """Serialize the payload compactly; refuse (before any API call) if it exceeds the size
    limit, so an oversized context can never silently truncate or cost an unbounded amount."""
    message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(message) > max_chars:
        raise InputTooLargeError(
            "The evidence selected for this job is too large to send to the language model."
        )
    return message
