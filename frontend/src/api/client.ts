import type {
  AnalyzeResponse,
  CvGenerateResponse,
  GraphOverview,
  GraphSummary,
  LlmStatus,
  NodeDetail,
  NodeRef,
} from "../types/career";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

/** The backend's LLM-layer error shape: `{ detail: { code, message, retryable } }`. Messages are
 * fixed, human-readable and non-sensitive -- provider error bodies never reach the browser. */
export interface ApiErrorDetail {
  code: string;
  message: string;
  retryable: boolean;
}

// Thrown on any non-2xx response. `detail` carries the parsed FastAPI error body
// when available -- an ApiErrorDetail object, a plain string (HTTPException), or the
// Pydantic validation-error array (`[{loc, msg, type}, ...]`) FastAPI returns on a 422 --
// so callers can render readable messages instead of a raw blob.
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
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    const detail: ApiErrorDetail = {
      code: "network_error",
      message: `Can't reach the CareerGraph backend at ${BASE_URL}. Check that it is running.`,
      retryable: true,
    };
    throw new ApiError(0, detail, "Network error");
  }
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

function isApiErrorDetail(detail: unknown): detail is ApiErrorDetail {
  return (
    typeof detail === "object" &&
    detail !== null &&
    typeof (detail as ApiErrorDetail).message === "string" &&
    typeof (detail as ApiErrorDetail).code === "string"
  );
}

export interface DescribedError {
  message: string;
  code: string | null;
  retryable: boolean;
}

/** One place that turns anything thrown by `api.*` into text safe to show a user. */
export function describeApiError(err: unknown, fallback: string): DescribedError {
  if (err instanceof ApiError) {
    if (isApiErrorDetail(err.detail)) {
      return { message: err.detail.message, code: err.detail.code, retryable: err.detail.retryable };
    }
    const lines = formatApiErrorDetail(err.detail);
    return { message: lines.length > 0 ? lines.join(" ") : fallback, code: null, retryable: err.status >= 500 };
  }
  return { message: fallback, code: null, retryable: true };
}

export const api = {
  getGraphSummary: () => request<GraphSummary>("/api/graph/summary"),
  getGraphOverview: () => request<GraphOverview>("/api/graph/overview"),
  listNodes: (label: string) => request<NodeRef[]>(`/api/graph/list/${label}`),
  getNodeDetail: (label: string, key: string) =>
    request<NodeDetail>(`/api/graph/node/${label}/${encodeURIComponent(key)}`),
  loadDemoJob: () => request<{ jobDescription: string }>("/api/jobs/demo"),
  // Which provider/model is active (never the API key) -- lets the UI label development fallbacks.
  getLlmStatus: () => request<LlmStatus>("/api/llm/status"),
  // Step 1: job description -> Groq requirement extraction -> Neo4j matching, in one server-side
  // request. The CVContext also stays server-side under the returned analysisId.
  analyzeJob: (jobDescription: string) =>
    request<AnalyzeResponse>("/api/jobs/analyze", {
      method: "POST",
      body: JSON.stringify({ jobDescription }),
    }),
  // Step 2: CV writing (Groq) -> provenance validation -> deterministic rendering. Takes only the
  // analysisId: the server looks up the evidence itself, so the browser can't supply any.
  generateCv: (analysisId: string, template: string) =>
    request<CvGenerateResponse>("/api/cv/generate", {
      method: "POST",
      body: JSON.stringify({ analysisId, template }),
    }),
};
