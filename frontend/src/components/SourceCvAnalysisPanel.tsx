import { Link } from "react-router-dom";
import type { RoleBasis, SourceCvAnalysisInfo, SourceCvStatus } from "../types/career";

const BASIS_LABEL: Record<RoleBasis, string> = {
  "graph+source": "Career graph evidence + your CV",
  source_only: "Your CV only",
  graph_only: "Career graph only",
};

const UNAVAILABLE_TEXT: Record<string, string> = {
  storage_failure: "The stored source CV could not be read on the server, so this analysis ran without it.",
};

interface PanelProps {
  info: SourceCvAnalysisInfo;
  status: SourceCvStatus | null;
  /** Show how each position was placed in the generated CV (only known once a CV exists). */
  showTreatment?: boolean;
}

/** How the Source CV took part: which positions came from where, what disagreed with the career graph, and
 * where the timeline has gaps. Nothing here invents anything -- gaps are reported, never filled. */
export default function SourceCvAnalysisPanel({ info, status, showTreatment = false }: PanelProps) {
  if (!info.used) {
    return (
      <div className="notice" role="note">
        <strong>No source CV was used.</strong>{" "}
        {info.unavailableReason ? (UNAVAILABLE_TEXT[info.unavailableReason] ?? "The stored source CV could not be used.") + " " : ""}
        The CV will contain only the evidence-backed sections tailored from your career graph — not your full employment
        history, contact details or education. <Link to="/new-cv">Upload a source CV</Link> on the Job page and run{" "}
        <em>Analyze job</em> again for a complete CV.
      </div>
    );
  }

  const changed = status !== null && (!status.exists || (status.revision ?? 0) !== info.revision);
  const unresolved = info.conflicts.filter((c) => c.resolution === null);

  return (
    <div className="card source-analysis">
      <div className="section-title" style={{ marginBottom: 8 }}>
        <h2>Source CV</h2>
        <span className="count">{info.roles.length} positions</span>
      </div>
      <p style={{ marginTop: 0, fontSize: "0.85rem" }}>
        Built from <strong>{info.filename}</strong>. Every position below appears exactly once in the CV; your career
        graph supplies the evidence-backed detail, your CV supplies the rest.
      </p>

      {changed && (
        <div className="notice notice-fallback" role="status">
          {status && !status.exists
            ? "Your source CV was deleted after this analysis. This analysis still uses the version it started with."
            : "Your source CV changed after this analysis. This analysis still uses the earlier version."}{" "}
          <Link to="/new-cv">Run Analyze job again</Link> to use the current one.
        </div>
      )}

      <table className="timeline-table">
        <thead>
          <tr>
            <th>Position</th>
            <th>Period</th>
            <th>Comes from</th>
            {showTreatment && <th>In the CV</th>}
          </tr>
        </thead>
        <tbody>
          {info.roles.map((role, i) => (
            <tr key={`${role.employer}-${role.title}-${i}`}>
              <td>
                <strong>{role.title ?? <span className="faint">title left out</span>}</strong>
                <div className="faint">{role.employer}</div>
              </td>
              <td>{role.period ?? <span className="faint">{role.omittedFields.includes("dates") ? "left out" : "no dates"}</span>}</td>
              <td>
                <span className={`badge ${role.basis === "graph+source" ? "badge-match" : "badge-neutral"}`}>{BASIS_LABEL[role.basis]}</span>
                {role.omittedFields.length > 0 && <div className="prov-note">Left out until resolved: {role.omittedFields.join(", ")}</div>}
              </td>
              {showTreatment && (
                <td>
                  {role.treatment === "featured" ? "Experience" : role.treatment === "additional" ? "Additional experience" : ""}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>

      {unresolved.length > 0 && (
        <div className="notice notice-conflict" role="note" style={{ marginTop: 12 }}>
          <strong>Your CV and your career graph disagree.</strong> The disputed detail is left out of the CV until you
          decide, on the Job page under Source CV.
          <ul className="conflict-list">
            {unresolved.map((c) => (
              <li key={c.id}>{c.description}</li>
            ))}
          </ul>
        </div>
      )}

      {info.chronologyGaps.length > 0 && (
        <div className="notice" role="note" style={{ marginTop: 12 }}>
          <strong>Possible gaps in your timeline</strong>
          <ul className="warning-list">
            {info.chronologyGaps.map((g) => (
              <li key={g}>{g}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
