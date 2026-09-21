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
  sourceCv: SourceCvAnalysisInfo;
}

// ---- Source CV: how it took part in an analysis / a generated CV ------------------------------------

export type RoleBasis = "graph+source" | "source_only" | "graph_only";

export interface RoleTreatment {
  title: string | null;
  employer: string | null;
  period: string | null;
  basis: RoleBasis;
  treatment: "featured" | "additional" | null; // known once a CV has been generated
  omittedFields: string[]; // header fields left out because of an unresolved conflict
}

export interface SourceConflict {
  id: string;
  origin: "graph" | "document";
  kind: string;
  field: string;
  description: string;
  sourceValue: string | null;
  graphValue: string | null;
  resolution: "use_source" | "use_graph" | null;
}

export interface SourceCvAnalysisInfo {
  used: boolean;
  revision: number | null;
  filename: string | null;
  unavailableReason: string | null;
  summary: Record<string, number> | null;
  roles: RoleTreatment[];
  conflicts: SourceConflict[];
  chronologyGaps: string[];
}

export interface LayoutWarning {
  code: string;
  message: string;
  severity: "warning" | "info";
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
  completeCv: boolean; // built from a Source CV as well as the graph
  repairAttempts: number; // automatic repair passes the validator triggered (at most 2)
  verification: "semantic" | "verbatim" | "not_needed" | null;
}

export interface EvidenceRef {
  origin: "graph" | "source"; // verified in Neo4j | from the candidate's own CV
  label: string;
  kind: "story" | "achievement" | "transferable" | "source";
  sourceType: string | null;
  owner: string | null; // the experience/project this evidence belongs to
  transferableFor: string | null; // the related capability, for transferable evidence
  relatedGaps: string[]; // literal gaps a transferable item does NOT satisfy
}

export interface BulletProvenance {
  section: "profile" | "experience" | "additional" | "projects" | "education";
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
  sourceCv: SourceCvAnalysisInfo | null;
  layoutWarnings: LayoutWarning[];
}

// ---- Source CV: the stored profile, as the review screen sees it ------------------------------------
// (No source excerpts, hash or server paths: the API never returns them.)

export interface LabeledUrl {
  label: string | null;
  url: string;
}

export interface ContactInfo {
  fullName: string | null;
  email: string | null;
  telephone: string | null;
  city: string | null;
  country: string | null;
  linkedinUrl: string | null;
  githubUrl: string | null;
  portfolioUrl: string | null;
  otherUrls: LabeledUrl[];
}

export interface SourceFactView {
  id: string;
  text: string;
  userEdited: boolean;
}

interface DatedFields {
  startText: string | null;
  endText: string | null;
  start: string | null; // normalized "YYYY" | "YYYY-MM", derived by the server
  end: string | null;
  current: boolean;
}

export interface EmploymentView extends DatedFields {
  id: string;
  employer: string | null;
  title: string | null;
  location: string | null;
  facts: SourceFactView[];
}

export interface EducationView extends DatedFields {
  id: string;
  institution: string;
  qualification: string | null;
  field: string | null;
  location: string | null;
  facts: SourceFactView[];
}

export interface ProjectView extends DatedFields {
  id: string;
  name: string;
  role: string | null;
  url: string | null;
  facts: SourceFactView[];
}

export interface CertificationView {
  id: string;
  name: string;
  issuer: string | null;
  dateText: string | null;
  date: string | null;
}

export interface LanguageView {
  id: string;
  language: string;
  proficiency: string | null;
}

export interface OtherSectionView {
  id: string;
  heading: string;
  items: SourceFactView[];
}

export interface ParseWarningView {
  code: string;
  message: string;
  entryId: string | null;
  origin: "parser" | "derived";
}

export interface SourceProfileView {
  schemaVersion: number;
  revision: number;
  upload: { originalFilename: string; detectedType: "pdf" | "docx"; sizeBytes: number };
  parser: { provider: string; model: string | null; mode: string; attempts: number };
  uploadedAt: string;
  parsedAt: string;
  updatedAt: string;
  contact: ContactInfo;
  summary: SourceFactView | null;
  employment: EmploymentView[];
  education: EducationView[];
  projects: ProjectView[];
  certifications: CertificationView[];
  languages: LanguageView[];
  otherSections: OtherSectionView[];
  warnings: ParseWarningView[];
  conflicts: SourceConflict[];
}

export interface SourceCvStatus {
  exists: boolean;
  limits: { maxUploadBytes: number; acceptedTypes: string[] };
  filename?: string;
  detectedType?: "pdf" | "docx";
  uploadedAt?: string;
  parsedAt?: string;
  updatedAt?: string;
  revision?: number;
  warningCount?: number;
  conflictCount?: number;
  unresolvedConflictCount?: number;
  completeness?: {
    employmentCount: number;
    educationCount: number;
    languageCount: number;
    certificationCount: number;
    projectCount: number;
    missing: string[];
    isComplete: boolean;
  };
  parser?: { provider: string; model: string | null; devParsed: boolean };
}

// What the review screen sends back (PUT /api/source-cv). Ids of existing entries are echoed; an entry
// without an id is new; an entry that is left out is deleted. Server-derived fields are never sent.
export interface FactEdit {
  id: string | null;
  text: string;
}

export interface DatedEdit {
  startText: string | null;
  endText: string | null;
  current: boolean;
}

export interface EmploymentEdit extends DatedEdit {
  id: string | null;
  employer: string | null;
  title: string | null;
  location: string | null;
  facts: FactEdit[];
}

export interface EducationEdit extends DatedEdit {
  id: string | null;
  institution: string;
  qualification: string | null;
  field: string | null;
  location: string | null;
  facts: FactEdit[];
}

export interface ProjectEdit extends DatedEdit {
  id: string | null;
  name: string;
  role: string | null;
  url: string | null;
  facts: FactEdit[];
}

export interface ProfileEdit {
  expectedRevision: number;
  contact: ContactInfo;
  summary: string | null;
  employment: EmploymentEdit[];
  education: EducationEdit[];
  projects: ProjectEdit[];
  certifications: { id: string | null; name: string; issuer: string | null; dateText: string | null }[];
  languages: { id: string | null; language: string; proficiency: string | null }[];
  otherSections: { id: string | null; heading: string; items: FactEdit[] }[];
  conflictResolutions: Record<string, "use_source" | "use_graph" | null>;
}

/** The stages of a Source CV upload, in order. "uploading" and "extracting" are observed by the browser
 * (the request body going out, then the server reading the file); the rest are reported by the server as
 * each one starts. */
export type IngestStage = "uploading" | "extracting" | "parsing" | "validating" | "saving";
