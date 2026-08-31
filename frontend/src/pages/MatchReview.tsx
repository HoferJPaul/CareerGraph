import { useState } from "react";
import { Link } from "react-router-dom";
import EvidencePanel from "../components/EvidencePanel";
import RequirementCard, { type CardKind } from "../components/RequirementCard";
import StepIndicator from "../components/StepIndicator";
import { useAnalysis } from "../context/AnalysisContext";
import type { RequirementMatch } from "../types/career";

export default function MatchReviewPage() {
  const { cvContext } = useAnalysis();
  const [active, setActive] = useState<RequirementMatch | null>(null);

  if (!cvContext) {
    return (
      <div>
        <StepIndicator current={3} />
        <div className="page-header">
          <h1>Match Review</h1>
        </div>
        <div className="empty-state">
          No requirements matched yet. <Link to="/new-cv">Start with a job description</Link> to get started.
        </div>
      </div>
    );
  }

  const transferable = cvContext.gaps.filter((g) => g.transferableEvidence.length > 0);
  const hardGaps = cvContext.gaps.filter((g) => g.transferableEvidence.length === 0);

  const groups: { title: string; kind: CardKind; items: RequirementMatch[]; hint: string }[] = [
    {
      title: "Matched",
      kind: "matched",
      items: cvContext.matchedRequirements,
      hint: "High-confidence, evidence-backed matches for the literal requirement.",
    },
    {
      title: "Transferable",
      kind: "transferable",
      items: transferable,
      hint: "The literal requirement is a GAP. Related capability evidence exists, but it never means the missing tool itself was used.",
    },
    {
      title: "Gaps",
      kind: "gap",
      items: hardGaps,
      hint: "No verified evidence, and no related capability either.",
    },
  ];

  return (
    <div>
      <StepIndicator current={3} />
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1>Match Review</h1>
          <p>
            {cvContext.matchedRequirements.length} matched · {transferable.length} transferable ·{" "}
            {hardGaps.length} gaps · {cvContext.partialRequirements.length} low-confidence
          </p>
        </div>
        <Link to="/cv-context" className="btn btn-primary" style={{ textDecoration: "none" }}>
          Continue to CV Context →
        </Link>
      </div>

      <div className="card" style={{ background: "var(--color-bg-subtle)", fontSize: "0.82rem", display: "flex", gap: 10, alignItems: "flex-start" }}>
        <span className="badge badge-neutral" style={{ flexShrink: 0 }}>
          Validated
        </span>
        <span style={{ color: "var(--color-text-muted)" }}>
          {cvContext.requirements.length} requirement(s) from Claude's extraction, matched against verified
          career evidence in Neo4j. A literal gap never becomes a match just because transferable evidence exists.
        </span>
      </div>

      {groups.map((group) => (
        <div className="section" key={group.title}>
          <div className="section-title">
            <h2>{group.title}</h2>
            <span className="count">{group.items.length}</span>
          </div>
          <p style={{ marginTop: -6, marginBottom: 14, fontSize: "0.82rem" }}>{group.hint}</p>
          {group.items.length === 0 ? (
            <div className="empty-state">None.</div>
          ) : (
            <div className="grid" style={{ gridTemplateColumns: "1fr" }}>
              {group.items.map((m) => (
                <RequirementCard key={m.requirement + m.skillQuery} match={m} kind={group.kind} onViewEvidence={() => setActive(m)} />
              ))}
            </div>
          )}
        </div>
      ))}

      {cvContext.partialRequirements.length > 0 && (
        <div className="section">
          <div className="section-title">
            <h2>Low-confidence matches</h2>
            <span className="count">{cvContext.partialRequirements.length}</span>
          </div>
          <p style={{ marginTop: -6, marginBottom: 14, fontSize: "0.82rem" }}>
            Weak lexical overlaps only — shown here for transparency, but excluded from the generated CV.
          </p>
          <div className="grid" style={{ gridTemplateColumns: "1fr" }}>
            {cvContext.partialRequirements.map((m) => (
              <RequirementCard key={m.requirement + m.skillQuery} match={m} kind="partial" onViewEvidence={() => setActive(m)} />
            ))}
          </div>
        </div>
      )}

      {active && (
        <EvidencePanel match={active} evidenceStories={cvContext.evidenceStories} onClose={() => setActive(null)} />
      )}

      <div style={{ marginTop: 32 }}>
        <Link to="/cv-context" className="btn btn-primary" style={{ textDecoration: "none", display: "inline-block" }}>
          Continue to CV Context →
        </Link>
      </div>
    </div>
  );
}
