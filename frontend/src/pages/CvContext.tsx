import { useState } from "react";
import { Link } from "react-router-dom";
import MetricCard from "../components/MetricCard";
import StepIndicator from "../components/StepIndicator";
import { useAnalysis } from "../context/AnalysisContext";
import { buildCvPrompt } from "../prompts";

export default function CvContextPage() {
  const { cvContext } = useAnalysis();
  const [viewJson, setViewJson] = useState(false);
  const [prompt, setPrompt] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  if (!cvContext) {
    return (
      <div>
        <StepIndicator current={4} />
        <div className="page-header">
          <h1>CV Context Ready</h1>
        </div>
        <div className="empty-state">
          No cv_context.json yet. <Link to="/new-cv">Start with a job description</Link> to get started.
        </div>
      </div>
    );
  }

  const transferableCount = cvContext.gaps.filter((g) => g.transferableEvidence.length > 0).length;

  function handleDownload() {
    const blob = new Blob([JSON.stringify(cvContext, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "cv_context.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handleCopyPrompt() {
    const built = buildCvPrompt(cvContext!);
    setPrompt(built);
    await navigator.clipboard.writeText(built);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div>
      <StepIndicator current={4} />
      <div className="page-header">
        <h1>CV Context Ready</h1>
        <p>cv_context.json has been generated from the Neo4j match above. Hand it to Claude to write the CV.</p>
      </div>

      <div className="grid grid-metrics">
        <MetricCard label="Requirements" value={cvContext.requirements.length} />
        <MetricCard label="Matches" value={cvContext.matchedRequirements.length} />
        <MetricCard label="Transferable evidence" value={transferableCount} />
        <MetricCard label="Gaps" value={cvContext.gaps.length} />
        <MetricCard label="Selected evidence" value={cvContext.evidenceStories.length} />
      </div>

      <div className="section" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn" onClick={() => setViewJson((v) => !v)}>
          {viewJson ? "Hide" : "View"} cv_context.json
        </button>
        <button className="btn" onClick={handleDownload}>
          Download cv_context.json
        </button>
        <button className="btn btn-primary" onClick={handleCopyPrompt}>
          {copied ? "Copied!" : "Copy CV Prompt for Claude"}
        </button>
      </div>

      {viewJson && (
        <div className="card section" style={{ maxHeight: 480, overflowY: "auto", background: "var(--color-bg-subtle)" }}>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: "0.78rem", margin: 0 }}>
            {JSON.stringify(cvContext, null, 2)}
          </pre>
        </div>
      )}

      {prompt && (
        <div className="section">
          <div className="section-title">
            <h2>CV-writing prompt</h2>
          </div>
          <p style={{ marginTop: -6, marginBottom: 14, fontSize: "0.82rem" }}>
            Copied to your clipboard — paste this into Claude. It contains the full cv_context.json above.
          </p>
          <div className="card" style={{ maxHeight: 420, overflowY: "auto", background: "var(--color-bg-subtle)" }}>
            <pre style={{ whiteSpace: "pre-wrap", fontSize: "0.78rem", margin: 0 }}>{prompt}</pre>
          </div>
        </div>
      )}

      <div className="section" style={{ fontSize: "0.8rem" }}>
        <Link to="/cv-preview" style={{ color: "var(--color-text-faint)" }}>
          Debug: auto-generate CV now (deterministic, dev mode) →
        </Link>
      </div>
    </div>
  );
}
