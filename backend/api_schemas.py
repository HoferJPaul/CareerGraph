"""Response/request models specific to the API layer.

Where the existing pipeline already has a Pydantic model for a shape (CVContext,
RequirementMatch, EvidenceStory, SkillSummary from tailor_cv.py), routes return
that model directly instead of redefining it here -- see routes/jobs.py and
routes/cv.py.

Nothing in these models can carry an API key, a prompt or a raw provider error.
"""
from typing import Optional

from pydantic import BaseModel, Field

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


class ConflictView(BaseModel):
    """A disagreement between the Source CV and the career graph (or within the CV itself). Neither
    value is used in the generated CV until the user resolves it."""

    id: str
    origin: str  # "graph" | "document"
    kind: str
    field: str
    description: str
    sourceValue: Optional[str] = None
    graphValue: Optional[str] = None
    resolution: Optional[str] = None  # "use_source" | "use_graph" | None


class RoleTreatment(BaseModel):
    title: Optional[str] = None
    employer: Optional[str] = None
    period: Optional[str] = None
    basis: str  # "graph+source" | "source_only" | "graph_only"
    treatment: Optional[str] = None  # "featured" | "additional" -- known once a CV has been generated
    omittedFields: list[str] = []  # header fields left out because of an unresolved conflict


class SourceCvAnalysisInfo(BaseModel):
    """How the Source CV took part in this analysis. Never carries CV text beyond what the reader needs."""

    used: bool
    revision: Optional[int] = None
    filename: Optional[str] = None
    unavailableReason: Optional[str] = None  # an error code when a stored CV could not be read
    summary: Optional[dict] = None
    roles: list[RoleTreatment] = []
    conflicts: list[ConflictView] = []
    chronologyGaps: list[str] = []


class AnalyzeResponse(BaseModel):
    analysisId: str
    extraction: ExtractionInfo
    requirementCount: int
    cvContext: CVContext
    sourceCv: SourceCvAnalysisInfo = SourceCvAnalysisInfo(used=False)


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
    pageBudget: int = Field(default=2, ge=1, le=4)  # source-aware CVs only: a target, never a cut-off


class GenerationInfo(BaseModel):
    provider: str  # "groq" | "dev"
    model: Optional[str] = None
    mode: str  # "llm_structured" | "deterministic_fallback"
    devFallback: bool
    provenanceValidated: bool = True
    tokenUsage: Optional[TokenUsageInfo] = None
    retried: bool = False
    attempts: int = 1
    completeCv: bool = False  # True when the CV was built from a Source CV as well as the graph
    repairAttempts: int = 0  # automatic repair passes the validator triggered (at most 2)
    verification: Optional[str] = None  # "semantic" | "verbatim" | "not_needed"


class EvidenceRef(BaseModel):
    origin: str = "graph"  # "graph" (verified in Neo4j) | "source" (from your own CV)
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


class LayoutWarningInfo(BaseModel):
    code: str
    message: str
    severity: str = "warning"  # "warning" | "info"


class CvGenerateResponse(BaseModel):
    markdown: str
    template: str
    structuredCv: StructuredCV
    generation: GenerationInfo
    provenance: list[BulletProvenance]
    sourceCv: Optional[SourceCvAnalysisInfo] = None
    layoutWarnings: list[LayoutWarningInfo] = []


class LlmStatus(BaseModel):
    provider: str
    extractionModel: Optional[str] = None
    writingModel: Optional[str] = None
    configured: bool
    devFallback: bool
    maxJobDescriptionChars: int
