import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, describeApiError, type DescribedError } from "../api/client";
import ErrorNotice from "../components/ErrorNotice";
import Progress from "../components/Progress";
import SourceCvCard from "../components/SourceCvCard";
import StepIndicator from "../components/StepIndicator";
import { FallbackBanner } from "../components/TechnicalDetails";
import { useAnalysis } from "../context/AnalysisContext";
import type { LlmStatus } from "../types/career";

export default function NewCvPage() {
  const { jobDescription, setJobDescription, setAnalysis, sourceCvStatus } = useAnalysis();
  const [status, setStatus] = useState<LlmStatus | null>(null);
  const [loadingDemo, setLoadingDemo] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<DescribedError | null>(null);
  // A ref, not just state: two rapid clicks can both run before a state update re-renders.
  const inFlight = useRef(false);
  const navigate = useNavigate();

  useEffect(() => {
    api
      .getLlmStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  const limit = status?.maxJobDescriptionChars ?? null;
  const tooLong = limit !== null && jobDescription.trim().length > limit;
  const notConfigured = status !== null && !status.configured;

  async function handleLoadDemo() {
    setLoadingDemo(true);
    setError(null);
    try {
      const result = await api.loadDemoJob();
      setJobDescription(result.jobDescription);
    } catch (err) {
      setError(describeApiError(err, "Couldn't load the demo job description."));
    } finally {
      setLoadingDemo(false);
    }
  }

  async function handleAnalyze() {
    if (inFlight.current || !jobDescription.trim() || tooLong) return;
    inFlight.current = true;
    setAnalyzing(true);
    setError(null);
    try {
      const result = await api.analyzeJob(jobDescription);
      setAnalysis(result);
      navigate("/match-review");
    } catch (err) {
      setError(describeApiError(err, "Couldn't analyze the job description."));
    } finally {
      inFlight.current = false;
      setAnalyzing(false);
    }
  }

  return (
    <div>
      <StepIndicator current={1} />
      <div className="page-header">
        <h1>Job Description</h1>
        <p>
          Paste a job description. CareerGraph extracts its requirements, matches them against your verified career
          evidence, then writes a CV that only claims what the graph can prove.
        </p>
      </div>

      {notConfigured && (
        <div className="notice" role="note">
          <strong>Groq isn't configured on the server yet.</strong> Add your Groq API key to the backend environment
          and restart it (see the README), or set <code>LLM_PROVIDER=dev</code> to use the development fallbacks.
        </div>
      )}
      {status?.devFallback && (
        <FallbackBanner what="Requirement extraction is keyword-based and CV writing is rule-based." />
      )}

      <SourceCvCard llmStatus={status} />

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <label htmlFor="jd" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
            Job description
          </label>
          <button
            className="btn btn-ghost"
            onClick={handleLoadDemo}
            disabled={loadingDemo || analyzing}
            style={{ fontSize: "0.82rem" }}
          >
            {loadingDemo ? "Loading…" : "Load demo job"}
          </button>
        </div>
        <textarea
          id="jd"
          rows={14}
          placeholder="Paste the full job description here…"
          value={jobDescription}
          disabled={analyzing}
          onChange={(e) => setJobDescription(e.target.value)}
        />
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 14, gap: 12 }}>
          <span style={{ fontSize: "0.78rem", color: tooLong ? "#9a2f2a" : "var(--color-text-faint)" }}>
            {limit !== null
              ? `${jobDescription.trim().length.toLocaleString()} / ${limit.toLocaleString()} characters`
              : ""}
            {tooLong && " — too long, please shorten it"}
          </span>
          <button
            className="btn btn-primary"
            onClick={handleAnalyze}
            disabled={analyzing || !jobDescription.trim() || tooLong}
          >
            {analyzing ? "Analyzing…" : "Analyze job"}
          </button>
        </div>
        {error && <ErrorNotice error={error} onRetry={handleAnalyze} busy={analyzing} />}
        {sourceCvStatus !== null && !sourceCvStatus.exists && (
          <div className="notice notice-incomplete" role="note" style={{ marginTop: 14, marginBottom: 0 }}>
            <strong>Without a source CV the generated CV may be incomplete.</strong> It will contain only the
            evidence-backed sections tailored from your career graph — not your contact details, links, education or
            your full employment history. Upload one above for a complete CV.
          </div>
        )}
      </div>

      {analyzing && (
        <div className="section">
          <Progress
            title="Analyzing the job description"
            steps={[
              "Extracting the hiring requirements",
              "Matching them against your verified career evidence",
            ]}
          />
        </div>
      )}
    </div>
  );
}
