import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import { api } from "../api/client";
import { useAnalysis } from "../context/AnalysisContext";

const TEMPLATES = [
  { id: "modern", label: "Modern", available: true },
  { id: "technical", label: "Technical", available: false },
  { id: "classic", label: "Classic", available: false },
];

export default function CvPreviewPage() {
  const { analysis, cvContext, template, setTemplate, cvMarkdown, setCvMarkdown } = useAnalysis();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Debug-only page: works from either the primary flow's cvContext (Claude-
  // extracted requirements, validated and matched) or the dev-fallback single-shot
  // analysis, whichever is populated.
  const effectiveCvContext = cvContext ?? analysis?.cvContext ?? null;

  async function handleGenerate(nextTemplate = template) {
    if (!effectiveCvContext) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.generateCv(effectiveCvContext, nextTemplate);
      setCvMarkdown(result.markdown);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to generate CV.");
    } finally {
      setLoading(false);
    }
  }

  // Auto-generate once on arrival so the demo flow doesn't need an extra click
  // when coming straight from Match Review.
  useEffect(() => {
    if (effectiveCvContext && !cvMarkdown && !loading) {
      handleGenerate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveCvContext]);

  if (!effectiveCvContext) {
    return (
      <div>
        <div className="page-header">
          <h1>CV Preview (debug)</h1>
        </div>
        <div className="empty-state">
          No context yet. <Link to="/new-cv">Paste a job description</Link> to get started.
        </div>
      </div>
    );
  }

  async function handleCopy() {
    if (!cvMarkdown) return;
    await navigator.clipboard.writeText(cvMarkdown);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  function handleDownload() {
    if (!cvMarkdown) return;
    const blob = new Blob([cvMarkdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "tailored_cv.md";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      <div className="page-header">
        <h1>CV Preview (debug)</h1>
        <p>
          Deterministic rule-based renderer — dev/debug only. The presentation flow instead hands cv_context.json
          to Claude directly (see the CV Context step) so Claude can make the narrative decisions.
        </p>
      </div>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
          <div style={{ display: "flex", gap: 8 }}>
            {TEMPLATES.map((t) => (
              <button
                key={t.id}
                className="btn"
                disabled={!t.available}
                onClick={() => {
                  setTemplate(t.id);
                  handleGenerate(t.id);
                }}
                style={{
                  background: template === t.id ? "var(--color-accent-soft)" : undefined,
                  borderColor: template === t.id ? "var(--color-accent)" : undefined,
                }}
                title={t.available ? undefined : "Coming soon"}
              >
                {t.label}
                {!t.available && (
                  <span style={{ color: "var(--color-text-faint)", fontWeight: 400 }}> · Coming soon</span>
                )}
              </button>
            ))}
          </div>
          <button className="btn btn-primary" onClick={() => handleGenerate()} disabled={loading}>
            {loading ? "Generating…" : cvMarkdown ? "Regenerate CV" : "Generate CV"}
          </button>
        </div>
        {error && <div className="error-box">{error}</div>}
      </div>

      {loading && !cvMarkdown && (
        <div className="empty-state section">Generating your tailored CV from verified evidence…</div>
      )}

      {cvMarkdown && (
        <div className="grid section" style={{ gridTemplateColumns: "1.3fr 1fr", alignItems: "start" }}>
          <div>
            <div className="section-title">
              <h2>Preview</h2>
            </div>
            <div className="cv-page-wrap">
              <div className="cv-page">
                <div className="cv-document">
                  <ReactMarkdown>{cvMarkdown}</ReactMarkdown>
                </div>
              </div>
            </div>
          </div>
          <div>
            <div className="section-title">
              <h2>Markdown source</h2>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn" onClick={handleCopy}>
                  {copied ? "Copied!" : "Copy"}
                </button>
                <button className="btn" onClick={handleDownload}>
                  Download .md
                </button>
              </div>
            </div>
            <div className="card" style={{ maxHeight: 720, overflowY: "auto", background: "var(--color-bg-subtle)" }}>
              <pre style={{ whiteSpace: "pre-wrap", fontSize: "0.78rem", margin: 0 }}>{cvMarkdown}</pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
