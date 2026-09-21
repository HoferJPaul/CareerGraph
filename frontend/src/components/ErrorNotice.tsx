import type { DescribedError } from "../api/client";

interface ErrorNoticeProps {
  error: DescribedError;
  onRetry?: () => void;
  retryLabel?: string;
  busy?: boolean;
}

// What each validation rule means to a reader. The backend sends codes and counts only -- never the claim
// text -- so this is all the detail a rejected CV can show.
const VIOLATION_TEXT: Record<string, string> = {
  gap_claimed: "named something your career graph has no evidence of",
  unsupported_number: "used a number that appears in no cited evidence",
  quantified_claim_needs_graph: "used a figure only your CV states, where the career graph is the authority",
  unsupported_source_claim: "said more than your source CV supports",
  unsupported_term: "named a skill or technology its evidence does not mention",
  unsupported_title: "claimed a seniority level the evidence does not show",
  unsupported_qualification: "named a qualification the evidence does not show",
  unsupported_skill: "listed a skill your career graph does not support",
  unknown_evidence_id: "cited evidence that does not exist",
  missing_namespace: "cited evidence without saying where it comes from",
  foreign_evidence: "cited evidence that belongs to a different entry",
  empty_evidence: "made a claim without citing any evidence",
  internal_language: "used system wording in the CV text",
  contact_in_prose: "put contact details into the CV text",
  compact_too_long: "wrote too much for a less relevant position",
  length_limit: "wrote more than the length limits allow",
  empty_text: "left a bullet empty",
};

function violationLines(violations: Record<string, number>): string[] {
  return Object.entries(violations).map(([code, count]) => {
    const meaning = VIOLATION_TEXT[code] ?? `broke the “${code}” rule`;
    return `${count} ${count === 1 ? "claim" : "claims"} ${meaning}`;
  });
}

/** A human-readable failure. The message comes from the backend's fixed, non-sensitive error
 * text (see describeApiError) -- never a raw provider error object. */
export default function ErrorNotice({ error, onRetry, retryLabel = "Try again", busy = false }: ErrorNoticeProps) {
  const lines = error.violations ? violationLines(error.violations) : [];
  return (
    <div className="error-box" role="alert">
      <div>{error.message}</div>
      {lines.length > 0 && (
        <div>
          <div style={{ marginTop: 6 }}>
            {error.repairAttempts
              ? `After ${error.repairAttempts} automatic repair ${error.repairAttempts === 1 ? "attempt" : "attempts"}, these problems remained:`
              : "What failed the check:"}
          </div>
          <ul className="violation-list">
            {lines.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </div>
      )}
      {error.retryable && onRetry && (
        <button className="btn" style={{ marginTop: 8 }} onClick={onRetry} disabled={busy}>
          {retryLabel}
        </button>
      )}
    </div>
  );
}
