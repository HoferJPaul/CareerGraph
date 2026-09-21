"""Draft (model-facing) schemas for the Source CV feature.

Same rule as llm/schemas.py: the model is asked for narrow *draft* shapes that Groq strict mode can
express -- every field required, "" meaning "absent" (never null, never a placeholder) -- and every
draft is converted into the real domain models by deterministic code and validated again. Groq
guaranteeing the shape never replaces that validation.
"""
from typing import Literal

from pydantic import Field

from llm.schemas import DraftBullet, DraftSkills, _Draft, strict_json_schema

# ---- Source CV parsing --------------------------------------------------------------------------------


class DraftFact(_Draft):
    text: str = Field(
        description="One statement from the document. Only bullet symbols and line-wrap breaks may be removed; nothing added."
    )
    excerpt: str = Field(description="The exact characters of the document this statement was taken from, copied verbatim.")


class DraftContact(_Draft):
    fullName: str = Field(description='The candidate\'s name as written, or "".')
    email: str
    telephone: str
    city: str
    country: str
    linkedinUrl: str
    githubUrl: str
    portfolioUrl: str = Field(description='A personal website or portfolio, or "".')
    otherUrls: list[str] = Field(description="Any other links of the candidate.")


class DraftEmployment(_Draft):
    employer: str
    title: str
    location: str
    startText: str = Field(description='The start date exactly as written, e.g. "Mar 2018" or "2015". "" if absent.')
    endText: str = Field(description='The end date exactly as written, or the word used for an ongoing role (e.g. "Present"). "" if absent.')
    current: bool = Field(description="True only if the document says the position is ongoing.")
    excerpt: str = Field(description="The heading line(s) of this position (title, employer, dates) copied verbatim.")
    facts: list[DraftFact] = Field(description="One entry per responsibility/achievement bullet of this position.")


class DraftEducation(_Draft):
    institution: str
    qualification: str
    fieldOfStudy: str
    location: str
    startText: str
    endText: str
    current: bool
    excerpt: str
    details: list[DraftFact] = Field(description="Extra detail lines (honours, thesis, coursework) as written.")


class DraftProject(_Draft):
    name: str
    role: str
    url: str
    startText: str
    endText: str
    current: bool
    excerpt: str
    facts: list[DraftFact]


class DraftCertification(_Draft):
    name: str
    issuer: str
    dateText: str
    excerpt: str


class DraftLanguage(_Draft):
    language: str = Field(description="A spoken/written human language only.")
    proficiency: str = Field(description='The level exactly as written (e.g. "Native", "C1"), or "".')
    excerpt: str


class DraftOtherSection(_Draft):
    heading: str
    items: list[DraftFact]


class DraftWarning(_Draft):
    code: Literal["ambiguous_date", "conflicting_values", "unclear_section", "possible_omission"]
    message: str = Field(description="One short sentence with no personal data.")


class SourceProfileDraft(_Draft):
    contact: DraftContact
    summary: DraftFact = Field(description='The candidate\'s own profile/summary paragraph; text and excerpt "" when there is none.')
    employment: list[DraftEmployment]
    education: list[DraftEducation]
    projects: list[DraftProject]
    certifications: list[DraftCertification]
    languages: list[DraftLanguage]
    otherSections: list[DraftOtherSection]
    warnings: list[DraftWarning]


# ---- Complete CV writing ------------------------------------------------------------------------------


class DraftRole(_Draft):
    roleId: str = Field(description="The roleId of the input role this entry is about, copied exactly.")
    emphasis: Literal["featured", "compact"]
    bullets: list[DraftBullet]


class DraftEntryBullets(_Draft):
    entryId: str = Field(description="The entryId of the input project or education entry, copied exactly.")
    bullets: list[DraftBullet]


class DraftOtherPick(_Draft):
    sectionId: str
    factIds: list[str] = Field(description="Ids of the input items to include in this section, copied exactly.")


class CompleteCvDraft(_Draft):
    headline: str = Field(description="A short professional headline for the target role.")
    profile: str = Field(description="A 2-3 sentence targeted profile using only supported facts.")
    profileEvidenceIds: list[str] = Field(description="Evidence ids (copied exactly) that support the profile.")
    roles: list[DraftRole]
    projects: list[DraftEntryBullets]
    education: list[DraftEntryBullets]
    otherSections: list[DraftOtherPick]
    skills: DraftSkills


class RepairFix(_Draft):
    target: str = Field(description="One of the targets listed as invalid, copied exactly.")
    action: Literal["rewrite", "remove"]
    text: str = Field(description='The corrected text for a rewrite; "" for a removal.')
    evidenceIds: list[str] = Field(description="Evidence ids supporting a rewritten bullet; [] otherwise.")


class RepairDraft(_Draft):
    fixes: list[RepairFix]


class SupportVerdict(_Draft):
    ref: str
    verdict: Literal["supported", "adds_facts", "overstates", "contradicts", "unrelated"]


class SupportVerification(_Draft):
    verdicts: list[SupportVerdict]


SOURCE_PROFILE_SCHEMA = strict_json_schema(SourceProfileDraft)
COMPLETE_CV_SCHEMA = strict_json_schema(CompleteCvDraft)
REPAIR_SCHEMA = strict_json_schema(RepairDraft)
SUPPORT_SCHEMA = strict_json_schema(SupportVerification)
