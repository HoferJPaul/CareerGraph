import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import { api, describeApiError, type DescribedError } from "../api/client";
import ErrorNotice from "../components/ErrorNotice";
import Progress from "../components/Progress";
import ProvenancePanel from "../components/ProvenancePanel";
import SourceCvAnalysisPanel from "../components/SourceCvAnalysisPanel";
import StepIndicator from "../components/StepIndicator";
import { FallbackBanner, GenerationDetails } from "../components/TechnicalDetails";
import { useAnalysis } from "../context/AnalysisContext";

const TEMPLATES = [
  { id: "modern", label: "Modern", available: true },
  { id: "technical", label: "Technical", available: false },
  { id: "classic", label: "Classic", available: false },
];

export default function CvPreviewPage() {
  const { analysis, cv, setCv, template, pageBudget, setPageBudget, sourceCvStatus } = useAnalysis();
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<DescribedError | null>(null);
  const [copied, setCopied] = useState(false);
  // A ref, not just state: two rapid clicks can both run before a state update re-renders.
  const inFlight = useRef(false);

  async function handleGenerate() {
    if (inFlight.current || !analysis) return;
    inFlight.current = true;
    setGenerating(true);
    setError(null);
    try {
      setCv(await api.generateCv(analysis.analysisId, template, pageBudget));
    } catch (err) {
      setError(describeApiError(err, "Couldn't generate the CV."));
    } finally {
      inFlight.current = false;
      setGenerating(false);
    }
  }

  // The Source CV panel already lists gaps and disputes, so the notices below skip those two to avoid saying them twice.
  const shownInPanel = new Set(cv?.sourceCv && cv.generation.completeCv ? ["chronology_gap", "unresolved_conflict"] : []);
  const notices = (cv?.layoutWarnings ?? []).filter((w) => !shownInPanel.has(w.code));

  if (!analysis) {
    return (
      <div>
        <StepIndicator current={3} />
        <div className="page-header">
          <h1>Your CV</h1>
        </div>
        <div className="empty-state">
          Nothing to show yet. <Link to="/new-cv">Paste a job description</Link> to get started.
        </div>
      </div>
    );
  }

  async function handleCopy() {
    if (!cv) return;
    await navigator.clipboard.writeText(cv.markdown);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  function handleDownload() {
    if (!cv) return;
    const blob = new Blob([cv.markdown], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "tailored_cv.md";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      <StepIndicator current={3} />
      <div className="page-header">
        <h1>Your CV</h1>
        <p>
          {analysis.sourceCv.used
            ? "This is a complete CV: your contact details, education and full employment history come from your source CV, and every bullet is backed by evidence. "
            : "This CV contains only the evidence-backed sections tailored from your career graph. "}
          Open “Where each claim comes from” to inspect the evidence behind any line.
        </p>
      </div>

      {cv?.generation.devFallback && (
        <FallbackBanner what="This CV was assembled by the rule-based writer." />
      )}

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
          <div style={{ display: "flex", gap: 8 }}>
            {TEMPLATES.map((t) => (
              <button
                key={t.id}
                className="btn"
                disabled={!t.available}
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
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {analysis.sourceCv.used && (
              <label className="field-inline" title="A length target. Exceeding it shows a warning; nothing is ever cut from your chronology.">
                <span>Target length</span>
                <select value={pageBudget} onChange={(e) => setPageBudget(Number(e.target.value))} disabled={generating}>
                  {[1, 2, 3, 4].map((n) => (
                    <option key={n} value={n}>
                      {n} {n === 1 ? "page" : "pages"}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <Link to="/match-review" className="btn" style={{ textDecoration: "none" }}>
              ← Back to matches
            </Link>
            <button className="btn btn-primary" onClick={handleGenerate} disabled={generating}>
              {generating ? "Generating…" : cv ? "Regenerate CV" : "Generate evidence-backed CV"}
            </button>
          </div>
        </div>
        {error && <ErrorNotice error={error} onRetry={handleGenerate} retryLabel="Generate again" busy={generating} />}
      </div>

      {generating && (
        <div className="section">
          <Progress
            title="Writing your CV"
            steps={[
              "Writing recruiter-facing text from the verified evidence",
              "Checking that every claim traces back to your career evidence",
              "Repairing any claim that fails a check (at most twice), then rendering the finished CV",
            ]}
          />
        </div>
      )}

      {notices.length > 0 && (
        <div className="section" style={{ marginTop: 0 }}>
          {notices.map((w, i) => (
            <div key={`${w.code}-${i}`} className={`notice ${w.severity === "warning" ? "notice-fallback" : ""}`} role="note">
              {w.message}
            </div>
          ))}
        </div>
      )}

      {cv?.sourceCv && cv.generation.completeCv && (
        <div className="section" style={{ marginTop: 0 }}>
          <SourceCvAnalysisPanel info={cv.sourceCv} status={sourceCvStatus} showTreatment />
        </div>
      )}

      {cv && (
        <div className="grid section" style={{ gridTemplateColumns: "1.3fr 1fr", alignItems: "start" }}>
          <div>
            <div className="section-title">
              <h2>Preview</h2>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn" onClick={handleCopy}>
                  {copied ? "Copied!" : "Copy Markdown"}
                </button>
                <button className="btn" onClick={handleDownload}>
                  Download .md
                </button>
              </div>
            </div>
            <div className="cv-page-wrap">
              <div className="cv-page">
                <div className="cv-document">
                  <ReactMarkdown>{cv.markdown}</ReactMarkdown>
                </div>
              </div>
            </div>
          </div>
          <div>
            <div className="section-title">
              <h2>Where each claim comes from</h2>
              <span className="count">{cv.provenance.length}</span>
            </div>
            <div className="card">
              <ProvenancePanel provenance={cv.provenance} />
              <GenerationDetails info={cv.generation} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
