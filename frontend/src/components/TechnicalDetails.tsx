import type { ExtractionInfo, GenerationInfo, TokenUsage } from "../types/career";

function usageText(usage: TokenUsage | null): string {
  if (!usage) return "not reported";
  return `${usage.promptTokens ?? "?"} in / ${usage.completionTokens ?? "?"} out`;
}

function Details({ rows }: { rows: [string, string][] }) {
  return (
    <details className="tech-details">
      <summary>Technical details</summary>
      <dl>
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </details>
  );
}

const EXTRACTION_MODE_LABEL: Record<ExtractionInfo["mode"], string> = {
  llm_structured: "LLM structured extraction",
  cached_manual: "Development fallback: curated requirements file",
  heuristic_keyword: "Development fallback: keyword matching",
};

export function ExtractionDetails({ info }: { info: ExtractionInfo }) {
  return (
    <Details
      rows={[
        ["Provider", info.provider],
        ["Model", info.model ?? "none (no LLM used)"],
        ["Extraction mode", EXTRACTION_MODE_LABEL[info.mode]],
        ["Requirements", String(info.requirementCount)],
        ["Tokens", usageText(info.tokenUsage)],
        ["Retried", info.retried ? `yes (${info.attempts} attempts)` : "no"],
      ]}
    />
  );
}

export function GenerationDetails({ info }: { info: GenerationInfo }) {
  return (
    <Details
      rows={[
        ["Provider", info.provider],
        ["Model", info.model ?? "none (no LLM used)"],
        ["Writer", info.mode === "llm_structured" ? "LLM structured output" : "Development fallback: rule-based"],
        ["Provenance check", info.provenanceValidated ? "passed" : "not run"],
        ["Tokens", usageText(info.tokenUsage)],
        ["Retried", info.retried ? `yes (${info.attempts} attempts)` : "no"],
      ]}
    />
  );
}

/** Shown wherever development-fallback output is on screen, so it is never mistaken for LLM output. */
export function FallbackBanner({ what }: { what: string }) {
  return (
    <div className="notice notice-fallback" role="note">
      <strong>Development fallback.</strong> {what} This was <em>not</em> written by an LLM and is lower quality than
      the Groq-backed output.
    </div>
  );
}
