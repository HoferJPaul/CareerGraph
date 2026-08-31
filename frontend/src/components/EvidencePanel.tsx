import type { Evidence, EvidenceStory, RequirementMatch, TransferableEvidence } from "../types/career";

interface EvidencePanelProps {
  match: RequirementMatch;
  evidenceStories: EvidenceStory[];
  onClose: () => void;
}

function findStory(evidence: Evidence, stories: EvidenceStory[]): EvidenceStory | undefined {
  if (evidence.project) return stories.find((s) => s.project === evidence.project);
  if (evidence.role) return stories.find((s) => s.label === evidence.role);
  return undefined;
}

function PathChain({ steps }: { steps: { label: string; sub?: string }[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", maxWidth: "100%" }}>
      {steps.map((step, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", maxWidth: "100%" }}>
          <div
            style={{
              border: "1px solid var(--color-border-strong)",
              borderRadius: "var(--radius-sm)",
              padding: "8px 14px",
              background: i === 0 ? "var(--color-bg-subtle)" : "var(--color-bg)",
              fontWeight: i === 0 ? 600 : 500,
              fontSize: "0.88rem",
              maxWidth: "100%",
              boxSizing: "border-box",
              overflowWrap: "break-word",
            }}
          >
            {step.label}
            {step.sub && (
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-faint)", fontWeight: 400 }}>
                {step.sub}
              </div>
            )}
          </div>
          {i < steps.length - 1 && (
            <div style={{ color: "var(--color-text-faint)", fontSize: "0.8rem", padding: "4px 0 4px 18px" }}>
              &darr;
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function literalEvidenceSteps(match: RequirementMatch, e: Evidence, stories: EvidenceStory[]) {
  const steps: { label: string; sub?: string }[] = [
    { label: match.requirement.length > 60 ? match.requirement.slice(0, 60) + "…" : match.requirement, sub: "Requirement" },
    { label: match.canonicalSkill ?? match.skillQuery, sub: "Matched skill" },
    { label: e.source, sub: `${e.relationship} · ${e.sourceType} · ${e.evidenceStrength} evidence` },
  ];
  const story = findStory(e, stories);
  if (e.professionalContext && story?.roleTitle && story?.company) {
    steps.push({ label: story.roleTitle, sub: "DURING" });
    steps.push({ label: story.company, sub: "AT" });
  }
  if (e.education) {
    steps.push({ label: e.education, sub: "PART_OF · educational provenance" });
  }
  return steps;
}

function transferableSteps(t: TransferableEvidence, e: Evidence) {
  return [
    { label: t.canonicalSkill ?? t.capabilityQuery, sub: "Transferable capability" },
    { label: e.source, sub: `DEMONSTRATED BY · ${e.sourceType} · ${e.evidenceStrength} evidence` },
  ];
}

export default function EvidencePanel({ match, evidenceStories, onClose }: EvidencePanelProps) {
  const isGap = match.evidence.length === 0;

  return (
    <div
      role="dialog"
      aria-label="Evidence provenance"
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(20, 30, 34, 0.35)",
        display: "flex",
        justifyContent: "flex-end",
        zIndex: 50,
      }}
      onClick={onClose}
    >
      <div
        className="card"
        style={{
          width: 440,
          maxWidth: "90vw",
          height: "100%",
          borderRadius: 0,
          overflowY: "auto",
          boxShadow: "var(--shadow-md)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <h2 style={{ fontSize: "1.05rem" }}>{match.canonicalSkill ?? match.skillQuery}</h2>
          <button className="btn" onClick={onClose}>
            Close
          </button>
        </div>
        <p style={{ fontSize: "0.78rem", color: "var(--color-text-faint)", marginTop: 2 }}>
          Provenance from the Neo4j evidence graph — exactly how this match was found.
        </p>

        {isGap && (
          <p style={{ fontSize: "0.85rem" }}>
            <strong>Literal requirement:</strong> {match.skillQuery}
            <br />
            <strong>Status:</strong> GAP
          </p>
        )}

        {match.evidence.length > 0 && (
          <div className="section" style={{ marginTop: 20 }}>
            <h3 style={{ fontSize: "0.85rem", color: "var(--color-text-faint)", textTransform: "uppercase" }}>
              Literal evidence path
            </h3>
            {match.evidence.map((e, i) => (
              <div key={i} style={{ marginTop: 14 }}>
                <PathChain steps={literalEvidenceSteps(match, e, evidenceStories)} />
              </div>
            ))}
          </div>
        )}

        {match.transferableEvidence.length > 0 && (
          <div className="section" style={{ marginTop: 20 }}>
            <h3 style={{ fontSize: "0.85rem", color: "var(--color-transfer)", textTransform: "uppercase" }}>
              Transferable capability (not the literal tool)
            </h3>
            <p style={{ fontSize: "0.8rem" }}>
              This shows related experience CareerGraph found for a nearby capability. It never means the
              literal requirement (<strong>{match.skillQuery}</strong>) was actually used.
            </p>
            {match.transferableEvidence.map((t) => (
              <div key={t.capabilityQuery} style={{ marginTop: 14 }}>
                {t.reason && (
                  <p style={{ fontSize: "0.78rem", color: "var(--color-text-faint)", marginBottom: 8 }}>
                    {t.reason}
                  </p>
                )}
                {t.evidence.length === 0 && (
                  <p style={{ fontSize: "0.82rem", color: "var(--color-text-faint)" }}>
                    Confirmed as a real CareerGraph skill, but no supporting evidence recorded.
                  </p>
                )}
                {t.evidence.map((e, i) => (
                  <div key={i} style={{ marginTop: 8 }}>
                    <PathChain steps={transferableSteps(t, e)} />
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}

        {match.evidence.length === 0 && match.transferableEvidence.length === 0 && (
          <div className="empty-state" style={{ marginTop: 20 }}>
            No verified evidence and no transferable capability found in CareerGraph for this requirement.
          </div>
        )}
      </div>
    </div>
  );
}
