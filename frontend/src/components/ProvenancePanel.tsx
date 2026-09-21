import type { BulletProvenance, EvidenceRef } from "../types/career";

const SECTION_LABEL: Record<BulletProvenance["section"], string> = {
  experience: "Experience",
  projects: "Projects",
  education: "Education",
};

function EvidenceLine({ e }: { e: EvidenceRef }) {
  if (e.kind === "transferable") {
    return (
      <li>
        <span className="badge badge-transfer">Transferable</span> {e.label}
        <div className="prov-note">
          Related capability: <strong>{e.transferableFor}</strong>
          {e.relatedGaps.length > 0 && (
            <>
              {" "}
              — does <strong>not</strong> show experience with {e.relatedGaps.join(", ")}
            </>
          )}
        </div>
      </li>
    );
  }
  return (
    <li>
      <span className="badge badge-match">{e.kind === "achievement" ? "Achievement" : "Record"}</span> {e.label}
      {e.owner && e.kind === "achievement" && <div className="prov-note">from {e.owner}</div>}
    </li>
  );
}

/** Every bullet in the CV, with the verified evidence it was written from. Evidence is shown by
 * its human label; internal ids never appear. */
export default function ProvenancePanel({ provenance }: { provenance: BulletProvenance[] }) {
  if (provenance.length === 0) return <div className="empty-state">No bullets to trace.</div>;

  const entries: { key: string; section: BulletProvenance["section"]; entry: string; bullets: BulletProvenance[] }[] = [];
  for (const b of provenance) {
    const key = `${b.section}:${b.entry}`;
    const existing = entries.find((e) => e.key === key);
    if (existing) existing.bullets.push(b);
    else entries.push({ key, section: b.section, entry: b.entry, bullets: [b] });
  }

  return (
    <div>
      {entries.map((entry) => (
        <div key={entry.key} className="prov-entry">
          <h3>
            {entry.entry} <span className="prov-section">{SECTION_LABEL[entry.section]}</span>
          </h3>
          {entry.bullets.map((b, i) => (
            <details key={i} className="prov-bullet">
              <summary>{b.text}</summary>
              <ul>
                {b.evidence.map((e, j) => (
                  <EvidenceLine key={j} e={e} />
                ))}
              </ul>
            </details>
          ))}
        </div>
      ))}
    </div>
  );
}
