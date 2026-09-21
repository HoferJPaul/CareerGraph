// Mirrors backend/api_schemas.py and the underlying pipeline models
// (requirement_schema.py, tailor_cv.py). Keep these in sync manually --
// there is no shared codegen step in this MVP.

export type Importance = "required" | "preferred" | "inferred";
export type RequirementCategory = "technology" | "capability" | "soft_skill" | "domain";
export type SourceType = "Project" | "Role" | "Achievement" | "Education";
export type RelationshipType = "USED" | "DEMONSTRATES" | "LEARNED";
export type MatchConfidence = "high" | "low" | "no_match";
export type MatchType =
  | "canonical_exact"
  | "alias_exact"
  | "lexical_multi_token"
  | "lexical_single_token"
  | "no_match";
export type EvidenceStrength = "strong" | "moderate" | "weak";
export type Recommendation = "include" | "optional" | "exclude";
export type CapabilitySource = "graph_vocabulary" | "llm_semantic";

export interface RelatedCapability {
  skillQuery: string;
  reason: string;
  source: CapabilitySource;
}

export interface Requirement {
  raw: string;
  skillQuery: string;
  importance: Importance;
  category: RequirementCategory;
  relatedCapabilities: RelatedCapability[];
}

export interface Evidence {
  source: string;
  sourceType: SourceType;
  relationship: RelationshipType;
  project: string | null;
  role: string | null;
  education: string | null;
  professionalContext: boolean;
  evidenceStrength: EvidenceStrength;
}

export interface TransferableEvidence {
  capabilityQuery: string;
  canonicalSkill: string | null;
  matchType: MatchType;
  evidence: Evidence[];
  reason: string | null;
  source: CapabilitySource | null;
}

export interface RequirementMatch {
  requirement: string;
  skillQuery: string;
  importance: Importance;
  category: RequirementCategory;
  matchType: MatchType;
  canonicalSkill: string | null;
  confidence: MatchConfidence;
  recommendation: Recommendation;
  evidence: Evidence[];
  transferableEvidence: TransferableEvidence[];
}

export interface AchievementRef {
  description: string;
  matchedSkills: string[];
  evidenceStrength: EvidenceStrength;
  hasMetric: boolean;
}

export interface EvidenceStory {
  label: string;
  sourceType: SourceType;
  project: string | null;
  roleTitle: string | null;
  company: string | null;
  type: string | null;
  domain: string | null;
  context: string | null;
  description: string | null;
  professionalContext: boolean;
  evidenceStrength: EvidenceStrength;
  directSkills: string[];
  strongestAchievements: AchievementRef[];
  supportsRequirements: string[];
  requiredCount: number;
}

export interface SkillSummary {
  name: string;
  displayName: string;
  category: string;
}

export interface CVGuidance {
  targetingPrinciples: string[];
}

export interface CVContext {
  requirements: Requirement[];
  matchedRequirements: RequirementMatch[];
  partialRequirements: RequirementMatch[];
  gaps: RequirementMatch[];
  evidenceStories: EvidenceStory[];
  skills: SkillSummary[];
  cvGuidance: CVGuidance;
}

export interface TokenUsage {
  promptTokens: number | null;
  completionTokens: number | null;
  totalTokens: number | null;
}

// Safe extraction metadata for the technical-details section. The backend never returns
// prompts, job-description text or raw provider errors, so none of that can appear here.
export interface ExtractionInfo {
  provider: string; // "groq" | "dev"
  model: string | null;
  mode: "llm_structured" | "cached_manual" | "heuristic_keyword";
  note: string;
  requirementCount: number;
  tokenUsage: TokenUsage | null;
  retried: boolean;
  attempts: number;
  devFallback: boolean; // true when a development fallback (not a real LLM) produced this
}

// The CVContext also stays server-side under `analysisId`: CV generation looks it up there,
// so the browser never supplies the evidence a CV is written from.
export interface AnalyzeResponse {
  analysisId: string;
  extraction: ExtractionInfo;
  requirementCount: number;
  cvContext: CVContext;
}

export interface GenerationInfo {
  provider: string;
  model: string | null;
  mode: "llm_structured" | "deterministic_fallback";
  devFallback: boolean;
  provenanceValidated: boolean;
  tokenUsage: TokenUsage | null;
  retried: boolean;
  attempts: number;
}

export interface EvidenceRef {
  label: string;
  kind: "story" | "achievement" | "transferable";
  sourceType: string | null;
  owner: string | null; // the experience/project this evidence belongs to
  transferableFor: string | null; // the related capability, for transferable evidence
  relatedGaps: string[]; // literal gaps a transferable item does NOT satisfy
}

export interface BulletProvenance {
  section: "experience" | "projects" | "education";
  entry: string;
  text: string;
  evidence: EvidenceRef[];
}

export interface LlmStatus {
  provider: string;
  extractionModel: string | null;
  writingModel: string | null;
  configured: boolean;
  devFallback: boolean;
  maxJobDescriptionChars: number;
}

export interface GraphSummary {
  totalNodes: number;
  totalRelationships: number;
  labelCounts: Record<string, number>;
  relationshipCounts: Record<string, number>;
}

export interface NodeRef {
  id: string;
  label: string;
  key: string;
  properties: Record<string, unknown>;
}

export interface GraphOverview {
  person: NodeRef | null;
  roles: NodeRef[];
  projects: NodeRef[];
  education: NodeRef[];
  curriculumProjects: Record<string, NodeRef[]>;
}

export interface RelatedNode {
  direction: "out" | "in";
  relationshipType: string;
  node: NodeRef;
}

export interface NodeDetail {
  node: NodeRef;
  relationships: RelatedNode[];
}

export interface CvGenerateResponse {
  markdown: string;
  template: string;
  generation: GenerationInfo;
  provenance: BulletProvenance[];
}
