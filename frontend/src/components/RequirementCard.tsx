import type { EvidenceStrength, MatchType, MatchConfidence, Recommendation, RequirementMatch } from "../types/career";

export type CardKind = "matched" | "transferable" | "gap" | "partial";

const KIND_BADGE: Record<CardKind, { label: string; className: string }> = {
  matched: { label: "Matched", className: "badge-match" },
  transferable: { label: "Transferable evidence", className: "badge-transfer" },
  gap: { label: "Gap", className: "badge-gap" },
  partial: { label: "Low confidence", className: "badge-gap" },
};

const MATCH_TYPE_LABEL: Record<MatchType, string> = {
  canonical_exact: "Exact match",
  alias_exact: "Alias match",
  lexical_multi_token: "Related terms",
  lexical_single_token: "Weak overlap",
  no_match: "No match",
};

const CONFIDENCE_LABEL: Record<MatchConfidence, string> = {
  high: "High",
  low: "Low",
  no_match: "None",
};

const RECOMMENDATION_LABEL: Record<Recommendation, string> = {
  include: "Recommended for CV",
  optional: "Optional",
  exclude: "Not auto-included",
};

const STRENGTH_RANK: Record<EvidenceStrength, number> = { strong: 0, moderate: 1, weak: 2 };

function strongestEvidenceStrength(match: RequirementMatch): EvidenceStrength | null {
  if (match.evidence.length === 0) return null;
  return [...match.evidence].sort((a, b) => STRENGTH_RANK[a.evidenceStrength] - STRENGTH_RANK[b.evidenceStrength])[0]
    .evidenceStrength;
}

function evidenceSources(match: RequirementMatch): string[] {
  const names = match.evidence
    .map((e) => e.project ?? e.role ?? e.source)
    .filter((v, i, arr) => arr.indexOf(v) === i);
  return names;
}

interface RequirementCardProps {
  match: RequirementMatch;
  kind: CardKind;
  onViewEvidence: () => void;
}

export default function RequirementCard({ match, kind, onViewEvidence }: RequirementCardProps) {
  const badge = KIND_BADGE[kind];
  const strength = strongestEvidenceStrength(match);
  const sources = evidenceSources(match);

  return (
    <div className={`card req-card req-card--${kind}`}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" }}>
        <div style={{ minWidth: 0 }}>
          <span className={`badge ${badge.className}`}>{badge.label}</span>
          <h3 style={{ fontSize: "1rem", margin: "8px 0 2px" }}>
            {match.canonicalSkill ?? match.skillQuery}
          </h3>
          <p style={{ fontSize: "0.82rem", margin: 0 }}>{match.requirement}</p>
        </div>
        <button className="btn" onClick={onViewEvidence} style={{ whiteSpace: "nowrap", flexShrink: 0 }}>
          Why? / View evidence
        </button>
      </div>

      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: "6px 18px",
          marginTop: 14,
          fontSize: "0.8rem",
          color: "var(--color-text-muted)",
        }}
      >
        <span>
          <strong>Importance:</strong> {match.importance}
        </span>
        <span>
          <strong>Match type:</strong> {MATCH_TYPE_LABEL[match.matchType]}
        </span>
        <span>
          <strong>Confidence:</strong> {CONFIDENCE_LABEL[match.confidence]}
        </span>
        {strength && (
          <span>
            <strong>Evidence strength:</strong> {strength}
          </span>
        )}
        <span>
          <strong>Recommendation:</strong> {RECOMMENDATION_LABEL[match.recommendation]}
        </span>
      </div>

      {sources.length > 0 && kind !== "transferable" && (
        <p style={{ fontSize: "0.82rem", marginTop: 10, marginBottom: 0 }}>
          <strong style={{ color: "var(--color-text)" }}>Evidence:</strong> {sources.join(", ")}
        </p>
      )}

      {match.transferableEvidence.length > 0 && (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px dashed var(--color-border)" }}>
          <p style={{ fontSize: "0.78rem", margin: "0 0 6px", color: "var(--color-transfer)", fontWeight: 600 }}>
            Literal status: GAP — related capability only, not the missing tool itself
          </p>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {match.transferableEvidence.map((t) => (
              <span key={t.capabilityQuery} className="badge badge-transfer">
                {t.canonicalSkill ?? t.capabilityQuery}
              </span>
            ))}
          </div>
        </div>
      )}

      {kind === "gap" && match.evidence.length === 0 && match.transferableEvidence.length === 0 && (
        <p style={{ fontSize: "0.82rem", marginTop: 10, marginBottom: 0, color: "var(--color-text-faint)" }}>
          No verified evidence found.
        </p>
      )}
    </div>
  );
}
