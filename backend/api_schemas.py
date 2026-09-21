"""Response/request models specific to the API layer.

Where the existing pipeline already has a Pydantic model for a shape (CVContext,
RequirementMatch, EvidenceStory, SkillSummary from tailor_cv.py), routes return
that model directly instead of redefining it here -- see routes/jobs.py and
routes/cv.py.

Nothing in these models can carry an API key, a prompt or a raw provider error.
"""
from typing import Optional

from pydantic import BaseModel

from structured_cv import StructuredCV
from tailor_cv import CVContext


class NodeRef(BaseModel):
    id: str  # "<Label>:<key>", key per ingest_career.KEY_PROPERTY
    label: str
    key: str
    properties: dict


class GraphSummary(BaseModel):
    totalNodes: int
    totalRelationships: int
    labelCounts: dict[str, int]
    relationshipCounts: dict[str, int]


class GraphOverview(BaseModel):
    person: Optional[NodeRef] = None
    roles: list[NodeRef] = []
    projects: list[NodeRef] = []
    education: list[NodeRef] = []
    curriculumProjects: dict[str, list[NodeRef]] = {}


class RelatedNode(BaseModel):
    direction: str  # "out" | "in"
    relationshipType: str
    node: NodeRef


class NodeDetail(BaseModel):
    node: NodeRef
    relationships: list[RelatedNode]


class SkillListItem(BaseModel):
    name: str
    displayName: str
    category: str
    aliases: list[str] = []


class TokenUsageInfo(BaseModel):
    promptTokens: Optional[int] = None
    completionTokens: Optional[int] = None
    totalTokens: Optional[int] = None


class ExtractionInfo(BaseModel):
    """Safe extraction metadata for the UI's technical-details section: never prompts, job
    description text, or provider error bodies."""

    provider: str  # "groq" | "dev"
    model: Optional[str] = None
    mode: str  # "llm_structured" | "cached_manual" | "heuristic_keyword" | "client_supplied"
    note: str
    requirementCount: int
    tokenUsage: Optional[TokenUsageInfo] = None
    retried: bool = False
    attempts: int = 1
    devFallback: bool  # True when a development fallback (not a real LLM) produced this


class AnalyzeRequest(BaseModel):
    jobDescription: str


class AnalyzeResponse(BaseModel):
    analysisId: str
    extraction: ExtractionInfo
    requirementCount: int
    cvContext: CVContext


class RequirementsAnalyzeResponse(BaseModel):
    """Dev/advanced endpoint: matching for an already-extracted RequirementList."""

    analysisId: str
    requirementCount: int
    cvContext: CVContext


class CvGenerateRequest(BaseModel):
    # The CVContext is looked up server-side from the analysis -- the browser never supplies
    # the evidence a CV is written from.
    analysisId: str
    template: str = "modern"


class GenerationInfo(BaseModel):
    provider: str  # "groq" | "dev"
    model: Optional[str] = None
    mode: str  # "llm_structured" | "deterministic_fallback"
    devFallback: bool
    provenanceValidated: bool = True
    tokenUsage: Optional[TokenUsageInfo] = None
    retried: bool = False
    attempts: int = 1


class EvidenceRef(BaseModel):
    label: str
    kind: str  # "story" | "achievement" | "transferable"
    sourceType: Optional[str] = None
    owner: Optional[str] = None  # the experience/project this evidence belongs to
    transferableFor: Optional[str] = None  # the related capability, for transferable evidence
    relatedGaps: list[str] = []  # literal gaps a transferable item does NOT satisfy


class BulletProvenance(BaseModel):
    section: str  # "experience" | "projects" | "education"
    entry: str
    text: str
    evidence: list[EvidenceRef]


class CvGenerateResponse(BaseModel):
    markdown: str
    template: str
    structuredCv: StructuredCV
    generation: GenerationInfo
    provenance: list[BulletProvenance]


class LlmStatus(BaseModel):
    provider: str
    extractionModel: Optional[str] = None
    writingModel: Optional[str] = None
    configured: bool
    devFallback: bool
    maxJobDescriptionChars: int
