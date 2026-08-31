import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import StepIndicator from "../components/StepIndicator";
import { useAnalysis } from "../context/AnalysisContext";
import { buildExtractionPrompt } from "../prompts";

export default function NewCvPage() {
  const { jobDescription, setJobDescription, setAnalysis, setCvMarkdown } = useAnalysis();
  const [loadingDemo, setLoadingDemo] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [debugOpen, setDebugOpen] = useState(false);
  const [debugLoading, setDebugLoading] = useState(false);
  const navigate = useNavigate();

  async function handleLoadDemo() {
    setLoadingDemo(true);
    setError(null);
    try {
      const result = await api.loadDemoJob();
      setJobDescription(result.jobDescription);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load the demo job description.");
    } finally {
      setLoadingDemo(false);
    }
  }

  function handleGeneratePrompt() {
    setPrompt(buildExtractionPrompt(jobDescription));
    setCopied(false);
  }

  async function handleCopy() {
    if (!prompt) return;
    await navigator.clipboard.writeText(prompt);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  async function handleDebugAutoAnalyze() {
    setDebugLoading(true);
    setError(null);
    try {
      const result = await api.analyzeJob(jobDescription);
      setAnalysis(result);
      setCvMarkdown(null);
      navigate("/cv-preview");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to analyze job description.");
    } finally {
      setDebugLoading(false);
    }
  }

  return (
    <div>
      <StepIndicator current={1} />
      <div className="page-header">
        <h1>Job Description</h1>
        <p>Paste a job description, then generate the extraction prompt to hand to Claude.</p>
      </div>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <label htmlFor="jd" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
            Job description
          </label>
          <button className="btn btn-ghost" onClick={handleLoadDemo} disabled={loadingDemo} style={{ fontSize: "0.82rem" }}>
            {loadingDemo ? "Loading…" : "Load demo job"}
          </button>
        </div>
        <textarea
          id="jd"
          rows={14}
          placeholder="Paste the full job description here…"
          value={jobDescription}
          onChange={(e) => {
            setJobDescription(e.target.value);
            setPrompt(null);
          }}
        />
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 14 }}>
          <button className="btn btn-primary" onClick={handleGeneratePrompt} disabled={!jobDescription.trim()}>
            Generate Claude Prompt
          </button>
        </div>
        {error && <div className="error-box">{error}</div>}
      </div>

      {prompt && (
        <div className="section">
          <div className="section-title">
            <h2>Requirement-extraction prompt</h2>
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn" onClick={handleCopy}>
                {copied ? "Copied!" : "Copy for Claude"}
              </button>
              <button className="btn btn-primary" onClick={() => navigate("/requirements")}>
                Continue → Upload Requirements
              </button>
            </div>
          </div>
          <p style={{ marginTop: -6, marginBottom: 14, fontSize: "0.82rem" }}>
            Paste this into Claude. It will return <code>requirements.json</code> — bring that back to the next
            screen.
          </p>
          <div className="card" style={{ maxHeight: 420, overflowY: "auto", background: "var(--color-bg-subtle)" }}>
            <pre style={{ whiteSpace: "pre-wrap", fontSize: "0.78rem", margin: 0 }}>{prompt}</pre>
          </div>
        </div>
      )}

      <div className="section">
        <button className="btn btn-ghost" onClick={() => setDebugOpen((v) => !v)} style={{ fontSize: "0.8rem" }}>
          {debugOpen ? "▾" : "▸"} Debug tools
        </button>
        {debugOpen && (
          <div className="card" style={{ marginTop: 8, background: "var(--color-bg-subtle)" }}>
            <p style={{ margin: 0, fontSize: "0.8rem" }}>
              Skips Claude entirely: runs the built-in heuristic/cached extraction fallback (no real LLM API
              configured) and jumps straight to the deterministic CV renderer. Useful for local development, not
              part of the presentation flow.
            </p>
            <button
              className="btn"
              style={{ marginTop: 10 }}
              onClick={handleDebugAutoAnalyze}
              disabled={debugLoading || !jobDescription.trim()}
            >
              {debugLoading ? "Analyzing…" : "Auto-analyze now (dev fallback)"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
