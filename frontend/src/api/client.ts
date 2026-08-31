import type {
  AnalyzeResponse,
  CVContext,
  CvGenerateResponse,
  GraphOverview,
  GraphSummary,
  NodeDetail,
  NodeRef,
  RequirementList,
  RequirementsAnalyzeResponse,
} from "../types/career";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

// Thrown on any non-2xx response. `detail` carries the parsed FastAPI error body
// when available -- either a plain string (HTTPException) or the Pydantic
// validation-error array (`[{loc, msg, type}, ...]`) FastAPI returns on a 422 --
// so callers can render field-level validation errors instead of a raw blob.
export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const bodyText = await res.text();
    let detail: unknown = bodyText;
    try {
      const parsed = JSON.parse(bodyText);
      detail = "detail" in parsed ? parsed.detail : parsed;
    } catch {
      // Not JSON -- keep the raw text as the detail.
    }
    throw new ApiError(res.status, detail, `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

/** Pydantic validation-error entries as returned by FastAPI's 422 response. */
export interface ValidationErrorItem {
  loc: (string | number)[];
  msg: string;
  type: string;
}

/** Renders an ApiError's `detail` into human-readable lines, whatever shape it is. */
export function formatApiErrorDetail(detail: unknown): string[] {
  if (Array.isArray(detail)) {
    return detail.map((item) => {
      const d = item as Partial<ValidationErrorItem>;
      if (d.loc && d.msg) {
        const path = d.loc.filter((p) => p !== "body").join(".");
        return path ? `${path}: ${d.msg}` : d.msg;
      }
      return JSON.stringify(item);
    });
  }
  if (typeof detail === "string") return [detail];
  return [JSON.stringify(detail)];
}

export const api = {
  getGraphSummary: () => request<GraphSummary>("/api/graph/summary"),
  getGraphOverview: () => request<GraphOverview>("/api/graph/overview"),
  listNodes: (label: string) => request<NodeRef[]>(`/api/graph/list/${label}`),
  getNodeDetail: (label: string, key: string) =>
    request<NodeDetail>(`/api/graph/node/${label}/${encodeURIComponent(key)}`),
  loadDemoJob: () => request<{ jobDescription: string }>("/api/jobs/demo"),
  // Debug/dev-only: single-shot raw JD -> extraction -> match. Not used by the
  // primary presentation flow (see prompts.ts / RequirementsUpload page).
  analyzeJob: (jobDescription: string) =>
    request<AnalyzeResponse>("/api/jobs/analyze", {
      method: "POST",
      body: JSON.stringify({ jobDescription }),
    }),
  // Primary flow: an already-extracted RequirementList (Claude's output) straight
  // into capability expansion + Neo4j matching + cv_context generation. Never runs
  // extraction.
  analyzeRequirements: (requirements: RequirementList) =>
    request<RequirementsAnalyzeResponse>("/api/requirements/analyze", {
      method: "POST",
      body: JSON.stringify(requirements),
    }),
  // Debug/dev-only: deterministic CV renderer. Not used by the primary flow, which
  // instead hands cv_context.json to Claude directly via prompts.buildCvPrompt.
  generateCv: (cvContext: CVContext, template: string) =>
    request<CvGenerateResponse>("/api/cv/generate", {
      method: "POST",
      body: JSON.stringify({ cvContext, template }),
    }),
};
