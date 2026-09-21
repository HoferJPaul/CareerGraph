import type { ExtractionInfo, GenerationInfo, SourceProfileView, TokenUsage } from "../types/career";

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
        ...(info.completeCv
          ? ([
              ["Built from", "your source CV and the career graph"],
              ["Claim verification", VERIFICATION_LABEL[info.verification ?? "not_needed"]],
              ["Automatic repairs", info.repairAttempts === 0 ? "none needed" : `${info.repairAttempts} of at most 2`],
              ["Model requests", String(info.attempts)],
            ] as [string, string][])
          : []),
        ["Tokens", usageText(info.tokenUsage)],
        ["Retried", info.retried ? `yes (${info.attempts} attempts)` : "no"],
      ]}
    />
  );
}

const VERIFICATION_LABEL: Record<string, string> = {
  semantic: "source-backed claims checked by a second model pass",
  verbatim: "development fallback: claims must restate their evidence",
  not_needed: "no source-backed claims to check",
};

const PARSER_MODE_LABEL: Record<string, string> = {
  llm_structured: "LLM structured output",
  heuristic_dev: "Development fallback: rule-based parser",
};

export function SourceParseDetails({ profile }: { profile: SourceProfileView }) {
  return (
    <Details
      rows={[
        ["Parser", profile.parser.provider],
        ["Model", profile.parser.model ?? "none (no LLM used)"],
        ["Parse mode", PARSER_MODE_LABEL[profile.parser.mode] ?? profile.parser.mode],
        ["Parse requests", String(profile.parser.attempts)],
        ["Version", String(profile.revision)],
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
