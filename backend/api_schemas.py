"""Response/request models specific to the API layer.

Where the existing pipeline already has a Pydantic model for a shape (CVContext,
RequirementMatch, EvidenceStory, SkillSummary from tailor_cv.py), routes return
that model directly instead of redefining it here -- see routes/jobs.py and
routes/cv.py.
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


class AnalyzeRequest(BaseModel):
    jobDescription: str


class AnalyzeResponse(BaseModel):
    extractionMode: str
    extractionNote: str
    requirementCount: int
    requirementsPath: str
    cvContext: CVContext


class RequirementsAnalyzeResponse(BaseModel):
    requirementCount: int
    cvContext: CVContext


class CvGenerateRequest(BaseModel):
    cvContext: CVContext
    template: str = "modern"


class CvGenerateResponse(BaseModel):
    markdown: str
    template: str
    structuredCv: StructuredCV
