"""Shared data model for the job-description -> requirements -> evidence pipeline.

Used by both extraction.py (LLM side) and matching.py (Neo4j side) so the two
stages stay decoupled but speak the same schema.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field

Importance = Literal["required", "preferred", "inferred"]
RequirementCategory = Literal["technology", "capability", "soft_skill", "domain"]
SourceType = Literal["Project", "Role", "Achievement", "Education"]
RelationshipType = Literal["USED", "DEMONSTRATES", "LEARNED"]
MatchConfidence = Literal["high", "low", "no_match"]
MatchType = Literal[
    "canonical_exact", "alias_exact", "lexical_multi_token", "lexical_single_token", "no_match"
]
EvidenceStrength = Literal["strong", "moderate", "weak"]
Recommendation = Literal["include", "optional", "exclude"]
CapabilitySource = Literal["graph_vocabulary", "llm_semantic"]


class RelatedCapability(BaseModel):
    """A candidate transferable-capability query for a literal requirement, discovered by the
    capability-suggestion stage (see capability_suggest.py) instead of hand-authored.

    Never treated as evidence for the literal requirement itself -- it is resolved completely
    separately (matching.py Stage 2b) into MatchResult.transferableEvidence, which can never flip
    a literal gap into a match.
    """

    skillQuery: str = Field(description="A canonical CareerGraph Skill name, confirmed to actually "
        "exist in the graph vocabulary before being recorded here -- never an invented/guessed name.")
    reason: str = Field(description="Why this capability is plausibly relevant to the literal "
        "requirement, in plain language (e.g. 'CloudWatch is used for application/infrastructure "
        "monitoring').")
    source: CapabilitySource = Field(
        description="How this candidate was identified. 'graph_vocabulary' if the literal query and "
        "the capability share meaningful lexical/category overlap discoverable by the read-only graph "
        "lookup alone. 'llm_semantic' if bridging the literal tool name to its underlying capability "
        "required domain knowledge (no shared vocabulary at all between e.g. 'cloudwatch' and "
        "'observability') before its existence was confirmed against the graph.",
    )


class Requirement(BaseModel):
    raw: str = Field(description="Exact wording or closest paraphrase from the job description.")
    skillQuery: str = Field(description="Concise, normalized, lowercase phrase suitable for skill_search.")
    importance: Importance
    category: RequirementCategory
    relatedCapabilities: list[RelatedCapability] = Field(
        default_factory=list,
        description="Underlying-capability candidates this literal requirement implies (e.g. "
        "'sentry' -> observability, application logging), produced by the capability-suggestion "
        "stage and confirmed to exist in CareerGraph. Resolved separately as transferable evidence "
        "-- never counted toward satisfying the literal requirement, so a missing literal tool "
        "always stays a gap even when related capability evidence exists.",
    )


class RequirementList(BaseModel):
    requirements: list[Requirement]


class Evidence(BaseModel):
    source: str
    sourceType: SourceType
    relationship: RelationshipType
    project: Optional[str] = Field(
        default=None,
        description="Name of the Project this evidence traces back to, if resolvable "
        "(the Project itself, or the Project that ACHIEVED an Achievement).",
    )
    role: Optional[str] = Field(
        default=None,
        description="Title of the Role this evidence traces back to, if resolvable "
        "(the Role itself, or the Role that ACHIEVED an Achievement).",
    )
    education: Optional[str] = Field(
        default=None,
        description="Institution name of the Education this evidence's Project is "
        "PART_OF, if any (e.g. '42 Prague' for a 42 curriculum project). Explicit "
        "educational provenance -- distinct from, and never a substitute for, "
        "professionalContext.",
    )
    professionalContext: bool = Field(
        default=False,
        description="True if this evidence is reached via Project-[:DURING]->Role-[:AT]->Company "
        "(paid/professional employment provenance), as opposed to a personal project, coursework, "
        "or a role with no employer-linked Project behind it.",
    )
    evidenceStrength: EvidenceStrength = Field(
        default="weak",
        description="Provenance-aware strength signal, independent of matchConfidence/matchType "
        "(which measure lexical certainty only). Considers source type (Achievement > Project/Role "
        "> Education), professional-context provenance, and presence of a real quantified outcome.",
    )


class TransferableEvidence(BaseModel):
    """Evidence for a related capability query, NOT for the literal requirement itself.

    Never counts toward matchConfidence/hasEvidence of the parent requirement -- a requirement
    can be a hard gap (literal tool missing) while still carrying transferable evidence here.
    """

    capabilityQuery: str
    canonicalSkill: Optional[str] = None
    matchType: MatchType
    evidence: list[Evidence] = []
    reason: Optional[str] = None
    source: Optional[CapabilitySource] = None


class MatchResult(BaseModel):
    raw: str
    skillQuery: str
    importance: Importance
    category: RequirementCategory
    canonicalSkill: Optional[str] = None
    matchScore: Optional[float] = None
    matchConfidence: MatchConfidence
    matchType: MatchType
    recommendation: Recommendation = Field(
        default="exclude",
        description="Automatic-CV-inclusion signal, independent of matchConfidence: 'include' for "
        "exact/strong matches, 'optional' for ambiguous multi-token matches, 'exclude' for "
        "single-token lexical overlaps (too weak to surface without human review).",
    )
    evidence: list[Evidence] = []
    hasEvidence: bool
    transferableEvidence: list[TransferableEvidence] = Field(default_factory=list)
