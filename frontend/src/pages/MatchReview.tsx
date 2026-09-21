import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, describeApiError, type DescribedError } from "../api/client";
import ErrorNotice from "../components/ErrorNotice";
import EvidencePanel from "../components/EvidencePanel";
import Progress from "../components/Progress";
import RequirementCard, { type CardKind } from "../components/RequirementCard";
import SourceCvAnalysisPanel from "../components/SourceCvAnalysisPanel";
import StepIndicator from "../components/StepIndicator";
import { ExtractionDetails, FallbackBanner } from "../components/TechnicalDetails";
import { useAnalysis } from "../context/AnalysisContext";
import type { RequirementMatch } from "../types/career";

export default function MatchReviewPage() {
  const { analysis, cvContext, template, setCv, pageBudget, sourceCvStatus } = useAnalysis();
  const [active, setActive] = useState<RequirementMatch | null>(null);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<DescribedError | null>(null);
  // A ref, not just state: two rapid clicks can both run before a state update re-renders.
  const inFlight = useRef(false);
  const navigate = useNavigate();

  if (!analysis || !cvContext) {
    return (
      <div>
        <StepIndicator current={2} />
        <div className="page-header">
          <h1>Match Review</h1>
        </div>
        <div className="empty-state">
          No job analyzed yet. <Link to="/new-cv">Start with a job description</Link> to get started.
        </div>
      </div>
    );
  }

  const transferable = cvContext.gaps.filter((g) => g.transferableEvidence.length > 0);
  const hardGaps = cvContext.gaps.filter((g) => g.transferableEvidence.length === 0);
  const canGenerate = cvContext.evidenceStories.length > 0;

  async function handleGenerate() {
    if (inFlight.current || !analysis) return;
    inFlight.current = true;
    setGenerating(true);
    setError(null);
    try {
      const result = await api.generateCv(analysis.analysisId, template, pageBudget);
      setCv(result);
      navigate("/cv");
    } catch (err) {
      setError(describeApiError(err, "Couldn't generate the CV."));
    } finally {
      inFlight.current = false;
      setGenerating(false);
    }
  }

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

  const generateButton = (
    <button className="btn btn-primary" onClick={handleGenerate} disabled={generating || !canGenerate}>
      {generating ? "Generating…" : "Generate evidence-backed CV"}
    </button>
  );

  return (
    <div>
      <StepIndicator current={2} />
      <div className="page-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1>Match Review</h1>
          <p>
            {cvContext.matchedRequirements.length} matched · {transferable.length} transferable ·{" "}
            {hardGaps.length} gaps · {cvContext.partialRequirements.length} low-confidence
          </p>
        </div>
        {generateButton}
      </div>

      {generating && (
        <div className="section" style={{ marginTop: 0 }}>
          <Progress
            title="Writing your CV"
            steps={[
              "Writing recruiter-facing text from the verified evidence",
              "Checking that every claim traces back to your career evidence",
              "Rendering the finished CV",
            ]}
          />
        </div>
      )}
      {error && (
        <div style={{ marginBottom: 16 }}>
          <ErrorNotice error={error} onRetry={handleGenerate} retryLabel="Generate again" busy={generating} />
          {error.code === "analysis_not_found" && (
            <p style={{ fontSize: "0.85rem" }}>
              <Link to="/new-cv">Analyze the job description again</Link> to continue.
            </p>
          )}
        </div>
      )}
      {!canGenerate && (
        <div className="notice" role="note">
          Matching found no career evidence for this job, so there is nothing to write a CV from.
        </div>
      )}
      {analysis.extraction.devFallback && (
        <FallbackBanner what="The requirements below were extracted by keyword matching, not by an LLM." />
      )}

      <div className="card" style={{ background: "var(--color-bg-subtle)", fontSize: "0.82rem" }}>
        <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
          <span className="badge badge-neutral" style={{ flexShrink: 0 }}>
            Validated
          </span>
          <span style={{ color: "var(--color-text-muted)" }}>
            {cvContext.requirements.length} requirement(s) extracted from the job description, validated, and matched
            against verified career evidence in Neo4j. A literal gap never becomes a match just because transferable
            evidence exists.
          </span>
        </div>
        <ExtractionDetails info={analysis.extraction} />
      </div>

      <div className="section" style={{ marginTop: 0 }}>
        <SourceCvAnalysisPanel info={analysis.sourceCv} status={sourceCvStatus} />
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

      <div style={{ marginTop: 32 }}>{generateButton}</div>
    </div>
  );
}
