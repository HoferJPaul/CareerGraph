"""Structured CV model -- the contract between CV *writing* (cv_writer.py) and
CV *rendering* (backend/cv_markdown.py).

A CVWriter turns a tailor_cv.CVContext (raw matched evidence, requirements,
gaps) into a StructuredCV: finished, recruiter-facing content with every
evidence-selection decision already made. The renderer never sees CVContext
and makes no selection decisions of its own -- it only lays out whatever
StructuredCV it's given. This is what lets future renderers (technical/classic
templates, PDF, DOCX) be added without touching how content is chosen.

Every bullet carries a hidden evidenceIds list: identifiers tracing that bullet
back to the CareerGraph evidence it was built from (an achievement's own text,
or a "story:<label>" / "education:<institution>" reference into the
EvidenceStory it came from). Renderers read only `.text` and never display
these IDs -- they exist so CareerGraph can prove where every CV claim came
from, not to be shown to a recruiter.
"""
from typing import Optional

from pydantic import BaseModel, Field


class CVBullet(BaseModel):
    text: str
    evidenceIds: list[str] = Field(
        default_factory=list,
        description="Internal provenance only, never rendered.",
    )


class CVExperience(BaseModel):
    title: str
    organization: Optional[str] = None
    location: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    bullets: list[CVBullet] = []
    # Source-CV-aware generation only (both default to "absent", so graph-only CVs are unchanged):
    entryId: Optional[str] = None  # hidden id of the reconciled role, used to prove every role appears once
    dateRange: Optional[str] = None  # preformatted period; when set the renderer uses it as written


class CVProject(BaseModel):
    name: str
    role: Optional[str] = None
    context: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    bullets: list[CVBullet] = []
    entryId: Optional[str] = None
    dateRange: Optional[str] = None


class CVEducationEntry(BaseModel):
    institution: str
    program: Optional[str] = None
    startDate: Optional[str] = None
    endDate: Optional[str] = None
    bullets: list[CVBullet] = []
    entryId: Optional[str] = None
    dateRange: Optional[str] = None


class CVSkills(BaseModel):
    programming: list[str] = []
    frameworks: list[str] = []
    databases: list[str] = []
    tools: list[str] = []
    capabilities: list[str] = []


class CVLink(BaseModel):
    label: str
    url: str


class CVContact(BaseModel):
    """Deterministic header details copied from the validated Source CV profile -- never model text."""

    email: Optional[str] = None
    telephone: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    links: list[CVLink] = []


class CVCertification(BaseModel):
    name: str
    issuer: Optional[str] = None
    date: Optional[str] = None


class CVOtherSection(BaseModel):
    heading: str
    items: list[str]


class StructuredCV(BaseModel):
    name: str
    headline: str
    profile: str
    experience: list[CVExperience] = []
    projects: list[CVProject] = []
    education: list[CVEducationEntry] = []
    skills: CVSkills = CVSkills()
    languages: list[str] = []
    # Source-CV-aware generation only; all default to "absent" so graph-only CVs are unchanged.
    contact: Optional[CVContact] = None
    additionalExperience: list[CVExperience] = []  # compact entries: less relevant roles, kept for a complete timeline
    certifications: list[CVCertification] = []
    otherSections: list[CVOtherSection] = []
    profileEvidenceIds: list[str] = []  # hidden provenance for the profile paragraph
