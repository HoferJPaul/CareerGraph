import type {
  AnalyzeResponse,
  CvGenerateResponse,
  GraphOverview,
  GraphSummary,
  IngestStage,
  LlmStatus,
  NodeDetail,
  NodeRef,
  ProfileEdit,
  SourceCvStatus,
  SourceProfileView,
} from "../types/career";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

/** The backend's LLM-layer error shape: `{ detail: { code, message, retryable } }`. Messages are
 * fixed, human-readable and non-sensitive -- provider error bodies never reach the browser. */
export interface ApiErrorDetail {
  code: string;
  message: string;
  retryable: boolean;
  // Present when a generated CV failed validation: violation codes and counts only, never claim text.
  violations?: Record<string, number>;
  repairAttempts?: number;
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
  violations?: Record<string, number>;
  repairAttempts?: number;
}

/** One place that turns anything thrown by `api.*` into text safe to show a user. */
export function describeApiError(err: unknown, fallback: string): DescribedError {
  if (err instanceof ApiError) {
    if (isApiErrorDetail(err.detail)) {
      return {
        message: err.detail.message,
        code: err.detail.code,
        retryable: err.detail.retryable,
        violations: err.detail.violations,
        repairAttempts: err.detail.repairAttempts,
      };
    }
    const lines = formatApiErrorDetail(err.detail);
    return { message: lines.length > 0 ? lines.join(" ") : fallback, code: null, retryable: err.status >= 500 };
  }
  return { message: fallback, code: null, retryable: true };
}

/** Uploads (or re-parses) a Source CV and reports the stage the server is really in. Uses XMLHttpRequest so
 * the end of the upload is observable, and asks for newline-delimited progress events: the server sends
 * {"stage": "parsing" | "validating" | "saving"}, then {"stage": "done", "profile": ...} or
 * {"stage": "error", "detail": ...}. File-level problems (type, size, empty, encrypted, scanned) come back as
 * ordinary HTTP errors before any stage starts. */
function ingest(path: string, body: FormData | null, onStage: (stage: IngestStage) => void): Promise<SourceProfileView> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE_URL}${path}`);
    xhr.setRequestHeader("Accept", "application/x-ndjson");

    let consumed = 0;
    let finished = false;
    const fail = (status: number, detail: unknown) => {
      finished = true;
      reject(new ApiError(status, detail, `${status}`));
    };

    const drain = () => {
      const text = xhr.responseText;
      let newline = text.indexOf("\n", consumed);
      while (newline !== -1 && !finished) {
        const line = text.slice(consumed, newline).trim();
        consumed = newline + 1;
        newline = text.indexOf("\n", consumed);
        if (!line) continue;
        let event: { stage: string; profile?: SourceProfileView; detail?: unknown };
        try {
          event = JSON.parse(line);
        } catch {
          continue;
        }
        if (event.stage === "done" && event.profile) {
          finished = true;
          resolve(event.profile);
        } else if (event.stage === "error") {
          fail(xhr.status || 500, event.detail);
        } else if (event.stage === "parsing" || event.stage === "validating" || event.stage === "saving") {
          onStage(event.stage);
        }
      }
    };

    if (body) {
      xhr.upload.onloadstart = () => onStage("uploading");
      // The request body is fully sent: from here to the first server event the server is reading the file.
      xhr.upload.onload = () => onStage("extracting");
    } else {
      onStage("extracting");
    }
    xhr.onprogress = () => {
      if (xhr.status === 200) drain();
    };
    xhr.onerror = () => {
      const detail: ApiErrorDetail = {
        code: "network_error",
        message: `Can't reach the CareerGraph backend at ${BASE_URL}. Check that it is running.`,
        retryable: true,
      };
      fail(0, detail);
    };
    xhr.onload = () => {
      if (finished) return;
      if (xhr.status !== 200) {
        let detail: unknown = xhr.responseText;
        try {
          const parsed = JSON.parse(xhr.responseText);
          detail = "detail" in parsed ? parsed.detail : parsed;
        } catch {
          // Not JSON -- keep the raw text as the detail.
        }
        fail(xhr.status, detail);
        return;
      }
      drain();
      if (!finished) {
        fail(500, { code: "incomplete_response", message: "The server stopped before finishing. Try again.", retryable: true });
      }
    };
    xhr.send(body);
  });
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
  generateCv: (analysisId: string, template: string, pageBudget?: number) =>
    request<CvGenerateResponse>("/api/cv/generate", {
      method: "POST",
      body: JSON.stringify({ analysisId, template, ...(pageBudget ? { pageBudget } : {}) }),
    }),

  // ---- Source CV: the candidate's complete base CV, stored privately on the server ----------------------
  getSourceCvStatus: () => request<SourceCvStatus>("/api/source-cv/status"),
  getSourceCv: () => request<{ profile: SourceProfileView }>("/api/source-cv").then((r) => r.profile),
  uploadSourceCv: (file: File, onStage: (stage: IngestStage) => void) => {
    const form = new FormData();
    form.append("file", file, file.name);
    return ingest("/api/source-cv", form, onStage);
  },
  reparseSourceCv: (onStage: (stage: IngestStage) => void) => ingest("/api/source-cv/reparse", null, onStage),
  saveSourceCv: (edit: ProfileEdit) =>
    request<{ profile: SourceProfileView }>("/api/source-cv", { method: "PUT", body: JSON.stringify(edit) }).then(
      (r) => r.profile,
    ),
  deleteSourceCv: () => request<{ deleted: boolean }>("/api/source-cv", { method: "DELETE" }),
};
