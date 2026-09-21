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
import re

from structured_cv import (
    CVBullet,
    CVCertification,
    CVContact,
    CVEducationEntry,
    CVExperience,
    CVOtherSection,
    CVProject,
    StructuredCV,
)

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>])")


def _escape(text: str) -> str:
    """Backslash-escape Markdown control characters in text that came from the candidate's own CV
    (names of employers, titles, ...) so it renders exactly as written."""
    return _MD_SPECIAL.sub(r"\\\1", text)


def _bullets(bullets: list[CVBullet]) -> list[str]:
    return [f"- {b.text}" for b in bullets]


def _date_range(start: str | None, end: str | None) -> str | None:
    if not start:
        return None
    return f"{start} – {end}" if end else f"{start} – Present"


def _plain(text: str) -> str:
    return text


def _escaper(entry_id: str | None):
    """Text from the candidate's own CV (entries carrying an entryId) is escaped; graph-derived text is
    rendered exactly as it always was."""
    return _escape if entry_id else _plain


def _experience_block(exp: CVExperience) -> list[str]:
    lines = []
    esc = _escaper(exp.entryId)
    header = esc(exp.title)
    if exp.organization:
        header += f" — {esc(exp.organization)}"
    lines.append(f"### {header}")
    meta = " | ".join(
        x for x in [esc(exp.location) if exp.location else None, exp.dateRange or _date_range(exp.startDate, exp.endDate)] if x
    )
    if meta:
        lines.append(f"*{meta}*")
    lines.append("")
    lines.extend(_bullets(exp.bullets))
    return lines


def _compact_experience_block(exp: CVExperience) -> list[str]:
    """One line per less-relevant role, plus at most one short bullet under it."""
    head = f"**{_escape(exp.title)}**" + (f", {_escape(exp.organization)}" if exp.organization else "")
    meta = " | ".join(
        x for x in [_escape(exp.location) if exp.location else None, exp.dateRange or _date_range(exp.startDate, exp.endDate)] if x
    )
    lines = [f"- {head}" + (f" | {meta}" if meta else "")]
    lines.extend(f"  - {_escape(b.text)}" for b in exp.bullets)
    return lines


def _project_block(proj: CVProject) -> list[str]:
    lines = []
    esc = _escaper(proj.entryId)
    header = esc(proj.name)
    if proj.role:
        header += f" — {esc(proj.role)}"
    lines.append(f"### {header}")
    meta = " | ".join(
        x for x in [esc(proj.context) if proj.context else None, proj.dateRange or _date_range(proj.startDate, proj.endDate)] if x
    )
    if meta:
        lines.append(f"*{meta}*")
    lines.append("")
    lines.extend(_bullets(proj.bullets))
    return lines


def _education_block(edu: CVEducationEntry) -> list[str]:
    esc = _escaper(edu.entryId)
    lines = [f"### {esc(edu.institution)}"]
    meta = " | ".join(
        x for x in [esc(edu.program) if edu.program else None, edu.dateRange or _date_range(edu.startDate, edu.endDate)] if x
    )
    if meta:
        lines.append(f"*{meta}*")
    lines.append("")
    lines.extend(_bullets(edu.bullets))
    return lines


def _skill_line(label: str, items: list[str]) -> str | None:
    return f"**{label}:** {', '.join(items)}" if items else None


def _contact_lines(contact: CVContact) -> list[str]:
    """Header details, copied verbatim from the validated profile. E-mail and links are Markdown
    autolinks so underscores in them are not read as emphasis; nothing is hidden behind link text."""
    place = ", ".join(x for x in [contact.city, contact.country] if x)
    first = " | ".join(
        x for x in [_escape(place) if place else None, f"<{contact.email}>" if contact.email else None,
                    _escape(contact.telephone) if contact.telephone else None] if x
    )
    second = " | ".join(f"{_escape(link.label)}: <{link.url}>" for link in contact.links)
    return [line for line in (first, second) if line]


def _certification_line(cert: CVCertification) -> str:
    detail = ", ".join(x for x in [cert.issuer, cert.date] if x)
    return f"- {_escape(cert.name)}" + (f" — {_escape(detail)}" if detail else "")


def _other_section_block(section: CVOtherSection) -> list[str]:
    return [f"## {_escape(section.heading)}", *[f"- {_escape(item)}" for item in section.items], ""]


def render_modern(cv: StructuredCV) -> str:
    parts = [f"# {cv.name}", ""]
    if cv.contact:
        for line in _contact_lines(cv.contact):
            parts.append(line)
            parts.append("")

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

    if cv.additionalExperience:
        parts.append("## Additional Experience")
        for exp in cv.additionalExperience:
            parts.extend(_compact_experience_block(exp))
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

    if cv.certifications:
        parts.append("## Certifications")
        parts.extend(_certification_line(c) for c in cv.certifications)
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

    for section in cv.otherSections:
        parts.extend(_other_section_block(section))

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
