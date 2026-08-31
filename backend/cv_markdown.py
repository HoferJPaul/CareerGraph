"""Deterministic Markdown renderer for a finished StructuredCV.

Pure presentation: this file makes zero evidence-selection decisions -- that's
cv_writer.py's job. It only lays out whatever StructuredCV it's given, and
never reads CVContext, evidenceStories, gaps, or match-confidence data at all
(there is no import of tailor_cv or requirement_schema here). If a template
wants to show something, that something must already be a field on
StructuredCV -- this keeps content and presentation independent, so a future
"technical"/"classic"/PDF/DOCX renderer is just another function over the same
StructuredCV, never a second place that re-decides what belongs in the CV.
"""
from structured_cv import CVBullet, CVEducationEntry, CVExperience, CVProject, StructuredCV


def _bullets(bullets: list[CVBullet]) -> list[str]:
    return [f"- {b.text}" for b in bullets]


def _date_range(start: str | None, end: str | None) -> str | None:
    if not start:
        return None
    return f"{start} – {end}" if end else f"{start} – Present"


def _experience_block(exp: CVExperience) -> list[str]:
    lines = []
    header = exp.title
    if exp.organization:
        header += f" — {exp.organization}"
    lines.append(f"### {header}")
    meta = " | ".join(x for x in [exp.location, _date_range(exp.startDate, exp.endDate)] if x)
    if meta:
        lines.append(f"*{meta}*")
    lines.append("")
    lines.extend(_bullets(exp.bullets))
    return lines


def _project_block(proj: CVProject) -> list[str]:
    lines = []
    header = proj.name
    if proj.role:
        header += f" — {proj.role}"
    lines.append(f"### {header}")
    meta = " | ".join(x for x in [proj.context, _date_range(proj.startDate, proj.endDate)] if x)
    if meta:
        lines.append(f"*{meta}*")
    lines.append("")
    lines.extend(_bullets(proj.bullets))
    return lines


def _education_block(edu: CVEducationEntry) -> list[str]:
    lines = [f"### {edu.institution}"]
    meta = " | ".join(x for x in [edu.program, _date_range(edu.startDate, edu.endDate)] if x)
    if meta:
        lines.append(f"*{meta}*")
    lines.append("")
    lines.extend(_bullets(edu.bullets))
    return lines


def _skill_line(label: str, items: list[str]) -> str | None:
    return f"**{label}:** {', '.join(items)}" if items else None


def render_modern(cv: StructuredCV) -> str:
    parts = [f"# {cv.name}", ""]

    if cv.headline:
        parts.append(f"### {cv.headline}")
        parts.append("")
    if cv.profile:
        parts.append(cv.profile)
        parts.append("")

    if cv.experience:
        parts.append("## Experience")
        for exp in cv.experience:
            parts.extend(_experience_block(exp))
            parts.append("")

    if cv.projects:
        parts.append("## Projects")
        for proj in cv.projects:
            parts.extend(_project_block(proj))
            parts.append("")

    if cv.education:
        parts.append("## Education")
        for edu in cv.education:
            parts.extend(_education_block(edu))
            parts.append("")

    skill_lines = [
        line
        for line in (
            _skill_line("Languages", cv.skills.programming),
            _skill_line("Frameworks & Libraries", cv.skills.frameworks),
            _skill_line("Data", cv.skills.databases),
            _skill_line("Tools", cv.skills.tools),
            _skill_line("Capabilities", cv.skills.capabilities),
        )
        if line
    ]
    if skill_lines:
        parts.append("## Technical Skills")
        parts.extend(skill_lines)
        parts.append("")

    if cv.languages:
        parts.append("## Languages")
        parts.append(", ".join(cv.languages))
        parts.append("")

    return "\n".join(parts).strip() + "\n"


TEMPLATES = {
    "modern": render_modern,
}


def render(cv: StructuredCV, template: str = "modern") -> str:
    """Only 'modern' is implemented for this milestone (see product spec: one
    working template, others disabled/"coming soon" in the UI). Falls back to
    'modern' defensively rather than erroring if an unimplemented template name
    somehow reaches the backend."""
    renderer = TEMPLATES.get(template, render_modern)
    return renderer(cv)
