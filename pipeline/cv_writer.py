"""CV-writing stage: turns tailor_cv.CVContext (raw matched evidence) into a
StructuredCV (finished, recruiter-facing content) -- see structured_cv.py.

This is the one place that decides:
  - which experiences belong in the CV, which are merged, and which are omitted
  - which achievement/description text becomes which bullet
  - how CareerGraph's internal skill categories map to recruiter-facing groups
  - how Education-affiliated Role/Project evidence (e.g. the Student Tutor role,
    or a 42 Prague curriculum project) folds into ONE coherent Education entry
    instead of becoming its own thin section
  - which human languages get pulled out of Skills entirely

It never invents content: every bullet's text comes verbatim from an existing
EvidenceStory/Achievement description already present in CVContext, and every
bullet keeps a hidden evidenceIds list for provenance (see CVBullet). It never
reads CVContext.gaps / partialRequirements / matchedRequirements at all -- gap
analysis and match-confidence language belong in Match Review, not in the CV,
so this stage is structurally incapable of leaking them into a StructuredCV.

Two modes, mirroring llm_provider.LLMProvider's ABC/two-implementation shape:
  - DeterministicCVWriter: rule-based, no LLM call. Used live by the web app
    (backend/routes/cv.py). Selects, suppresses, merges and buckets evidence,
    but does NOT rewrite prose -- bullets reuse existing achievement/description
    text as-is. Producing tighter, more polished bullets from raw evidence is
    exactly the kind of judgment call a real LLM does better; seev write_cv.py
    for the Claude-assisted alternative available today.
  - ManualStructuredCVWriter: loads a hand-authored (or Claude Code-authored)
    structured_cv.json and validates it against the StructuredCV schema before
    use -- the "Claude-assisted" path from write_cv.py. A future real LLM
    provider can implement this same CVWriter interface without any other
    pipeline code changing.
"""
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from structured_cv import (
    CVBullet,
    CVEducationEntry,
    CVExperience,
    CVProject,
    CVSkills,
    StructuredCV,
)
from tailor_cv import CVContext, EvidenceStory, SkillSummary

DEFAULT_HEADLINE = "Software Engineer"
MAX_BULLETS_PER_ENTRY = 4

# Deterministic skill classification: CareerGraph's own Skill.category (technical/
# tool/capability/language) doesn't cleanly separate "programming language" from
# "framework" from "database" -- e.g. postgresql, react and docker are all
# category="tool". This curated table maps the concrete, commonly-evidenced skill
# names into recruiter-facing buckets; anything not listed here falls back to a
# category-based default. Human languages (category=="language") never reach this
# table at all -- they're routed to StructuredCV.languages before bucketing runs.
_PROGRAMMING = {"python", "c", "c++", "typescript", "javascript", "sql"}
_FRAMEWORKS = {
    "fastapi", "fastify", "react", "react native", "expo", "expo router",
    "zod", "drizzle orm", "streamlit", "node.js",
}
_DATABASES = {"postgresql", "sqlite", "pglite"}
_TOOLS = {
    "docker", "docker compose", "git", "github", "pnpm", "jwt", "jwks", "oauth",
    "google oauth", "figma", "claude code", "chatgpt", "uvicorn", "groq api",
    "llama 3.3 70b", "pdfplumber", "python-docx", "swagger ui", "ngrok", "render",
    "make", "minilibx", "http", "json", "openapi", "unix", "linux",
    "posix threads", "tcp/ip", "gps systems", "supabase", "pandas", "numpy",
    "openpyxl",
}

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _bucket_for(skill_name: str, category: str) -> str:
    if skill_name in _PROGRAMMING:
        return "programming"
    if skill_name in _FRAMEWORKS:
        return "frameworks"
    if skill_name in _DATABASES:
        return "databases"
    if skill_name in _TOOLS:
        return "tools"
    if category == "technical":
        return "programming"
    if category == "tool":
        return "tools"
    return "capabilities"


def _format_date(value: Optional[str]) -> Optional[str]:
    """'2026-01-01' / '2025-04' / '2025' -> 'Jan 2026' / 'Apr 2025' / '2025'.
    Never fabricates a date that isn't present -- returns None for None/empty."""
    if not value:
        return None
    parts = value.split("-")
    try:
        year = int(parts[0])
    except ValueError:
        return value
    if len(parts) >= 2:
        try:
            month = int(parts[1])
        except ValueError:
            month = None
        if month and 1 <= month <= 12:
            return f"{_MONTHS[month - 1]} {year}"
    return str(year)


def _achievement_bullets(story: EvidenceStory) -> list[CVBullet]:
    return [
        CVBullet(text=a.description, evidenceIds=[a.description])
        for a in story.strongestAchievements[:MAX_BULLETS_PER_ENTRY]
    ]


def _fallback_bullet(story: EvidenceStory, skill_display: dict[str, str]) -> Optional[CVBullet]:
    """Used only when a story has no achievements to draw bullets from. Prefers
    the story's own (Project/Role/Education) description property -- a real
    CareerGraph fact, not invented text -- and falls back to a minimal, honestly
    templated sentence built only from directSkills that were actually matched."""
    if story.description:
        return CVBullet(text=story.description, evidenceIds=[f"story:{story.label}"])
    if story.directSkills:
        names = ", ".join(sorted(skill_display.get(s, s) for s in story.directSkills))
        verb = "Served as" if story.sourceType == "Role" else "Contributed to"
        text = f"{verb} {story.label}, applying {names}."
        return CVBullet(text=text, evidenceIds=[f"story:{story.label}"])
    return None


def _is_substantial_project(story: EvidenceStory, language_names: set[str]) -> bool:
    """An experience only becomes its own CV section if it has enough relevant,
    non-language evidence to justify the space -- a bare language skill (e.g.
    Der Hutterer Weg contributing only German) is not enough on its own."""
    non_language_skills = [s for s in story.directSkills if s not in language_names]
    return bool(story.strongestAchievements) or bool(non_language_skills)


def _education_home(story: EvidenceStory, education_institutions: set[str]) -> Optional[str]:
    """Which Education institution (if any) this story's evidence should fold
    into, instead of becoming its own Experience/Project section. Structural,
    not name-hardcoded: a Role folds in via its own AT-company, a Project via its
    own PART_OF-education -- both already resolved upstream in tailor_cv.py."""
    if story.sourceType == "Education" and story.label in education_institutions:
        return story.label
    if story.sourceType == "Role" and story.company in education_institutions:
        return story.company
    if story.sourceType == "Project" and story.education in education_institutions:
        return story.education
    return None


def _build_experience(story: EvidenceStory) -> CVExperience:
    bullets = _achievement_bullets(story)
    if not bullets:
        fallback = _fallback_bullet(story, {})
        if fallback:
            bullets = [fallback]
    return CVExperience(
        title=story.roleTitle or story.label,
        organization=story.company,
        location=story.location or story.workMode,
        startDate=_format_date(story.startDate),
        endDate=_format_date(story.endDate),
        bullets=bullets,
    )


def _build_project(story: EvidenceStory, skill_display: dict[str, str]) -> CVProject:
    bullets = _achievement_bullets(story)
    if not bullets:
        fallback = _fallback_bullet(story, skill_display)
        if fallback:
            bullets = [fallback]
    return CVProject(
        name=story.label,
        role=story.personRole,
        context=story.context,
        startDate=_format_date(story.startDate),
        endDate=_format_date(story.endDate),
        bullets=bullets,
    )


def _build_education_entry(
    institution: str, folded: list[EvidenceStory], skill_display: dict[str, str]
) -> CVEducationEntry:
    anchor = next((s for s in folded if s.sourceType == "Education"), folded[0])
    others = [s for s in folded if s is not anchor]

    bullets: list[CVBullet] = []
    if anchor.description:
        bullets.append(CVBullet(text=anchor.description, evidenceIds=[f"education:{institution}"]))
    for s in others:
        extra = _achievement_bullets(s) or (
            [b] if (b := _fallback_bullet(s, skill_display)) else []
        )
        bullets.extend(extra)

    return CVEducationEntry(
        institution=institution,
        program=anchor.program,
        startDate=_format_date(anchor.startDate),
        endDate=_format_date(anchor.endDate),
        bullets=bullets[:MAX_BULLETS_PER_ENTRY],
    )


def _build_skills(skills: list[SkillSummary], language_names: set[str]) -> CVSkills:
    buckets: dict[str, list[str]] = {
        "programming": [], "frameworks": [], "databases": [], "tools": [], "capabilities": [],
    }
    for s in skills:
        if s.name in language_names:
            continue
        buckets[_bucket_for(s.name, s.category)].append(s.displayName)
    return CVSkills(**{k: sorted(v) for k, v in buckets.items()})


def _format_list(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def _build_profile(
    experience: list[CVExperience],
    projects: list[CVProject],
    prominent_tech: list[str],
) -> tuple[str, str]:
    """Rule-based profile paragraph. Deliberately plain: this deterministic mode
    does not rewrite prose (see module docstring) -- it composes a short, honest
    summary from tech actually present in the selected evidence, with no
    match-confidence or 'evidence-backed' system language."""
    tech_str = _format_list(prominent_tech[:6])
    if tech_str:
        clauses = [f"Software engineer with hands-on experience building full-stack "
                   f"applications and AI-assisted tooling using {tech_str}."]
    else:
        clauses = ["Software engineer with hands-on, project-based engineering experience."]

    context_bits = []
    if experience:
        context_bits.append("professional engineering work")
    if projects:
        context_bits.append("independent full-stack projects")
    if context_bits:
        clauses.append(f"Experience gained through {', '.join(context_bits)}.")

    return DEFAULT_HEADLINE, " ".join(clauses)


class CVWriter(ABC):
    @abstractmethod
    def write(self, cv_context: CVContext, name: str = "Paul Hofer") -> StructuredCV:
        """Turn a CVContext into a StructuredCV. Must never invent evidence and
        must never read cv_context.gaps/partialRequirements/matchedRequirements."""


class DeterministicCVWriter(CVWriter):
    def write(self, cv_context: CVContext, name: str = "Paul Hofer") -> StructuredCV:
        skills_by_name = {s.name: s for s in cv_context.skills}
        language_names = {n for n, s in skills_by_name.items() if s.category == "language"}
        languages = sorted(skills_by_name[n].displayName for n in language_names)
        skill_display = {n: s.displayName for n, s in skills_by_name.items()}

        stories = list(cv_context.evidenceStories)
        education_institutions = {s.label for s in stories if s.sourceType == "Education"}

        experience_stories: list[EvidenceStory] = []
        project_stories: list[EvidenceStory] = []
        education_folded: dict[str, list[EvidenceStory]] = {inst: [] for inst in education_institutions}

        for story in stories:
            home = _education_home(story, education_institutions)
            if home:
                education_folded[home].append(story)
            elif story.professionalContext:
                experience_stories.append(story)
            else:
                project_stories.append(story)

        kept_projects = [s for s in project_stories if _is_substantial_project(s, language_names)]

        experience = [_build_experience(s) for s in experience_stories]
        projects = [_build_project(s, skill_display) for s in kept_projects]
        education = [
            _build_education_entry(inst, folded, skill_display)
            for inst, folded in education_folded.items()
            if folded
        ]
        skills = _build_skills(cv_context.skills, language_names)

        prominent_tech: list[str] = []
        for story in experience_stories + kept_projects:
            for skill_name in story.directSkills:
                s = skills_by_name.get(skill_name)
                if s and s.category in ("technical", "tool") and s.displayName not in prominent_tech:
                    prominent_tech.append(s.displayName)

        headline, profile = _build_profile(experience, projects, prominent_tech)

        return StructuredCV(
            name=name,
            headline=headline,
            profile=profile,
            experience=experience,
            projects=projects,
            education=education,
            skills=skills,
            languages=languages,
        )


class ManualStructuredCVWriter(CVWriter):
    """Claude-assisted mode: structured_cv.json was authored ahead of time (by a
    human, or by Claude Code reading cv_context.json against the StructuredCV
    schema -- see write_cv.py) and is loaded + Pydantic-validated here rather than
    computed. `cv_context` is accepted only to satisfy the shared CVWriter
    interface; it is not read."""

    def __init__(self, structured_cv_path: Path):
        self.structured_cv_path = structured_cv_path

    def write(self, cv_context: CVContext, name: str = "Paul Hofer") -> StructuredCV:
        data = json.loads(self.structured_cv_path.read_text(encoding="utf-8"))
        return StructuredCV.model_validate(data)
