import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { AnalyzeResponse, CVContext, CvGenerateResponse, SourceCvStatus } from "../types/career";

interface AnalysisContextValue {
  jobDescription: string;
  setJobDescription: (value: string) => void;

  // Step 1 result: extraction metadata + the matched CVContext. The CVContext itself also lives
  // server-side under `analysis.analysisId`; this copy is only for display (Match Review).
  analysis: AnalyzeResponse | null;
  cvContext: CVContext | null;
  // Storing a new analysis discards any CV generated from the previous one.
  setAnalysis: (value: AnalyzeResponse | null) => void;

  // Step 2 result: the generated CV (rendered Markdown + provenance).
  cv: CvGenerateResponse | null;
  setCv: (value: CvGenerateResponse | null) => void;
  template: string;
  setTemplate: (value: string) => void;
  // A length TARGET for a complete CV: exceeding it shows a warning, it never removes chronology.
  pageBudget: number;
  setPageBudget: (value: number) => void;

  // The stored Source CV's status (null while unknown). An analysis holds a server-side SNAPSHOT of the
  // profile it ran against, so pages compare `analysis.sourceCv.revision` with this to say when the
  // stored CV has changed since.
  sourceCvStatus: SourceCvStatus | null;
  sourceCvStatusFailed: boolean;
  setSourceCvStatus: (value: SourceCvStatus | null) => void;
  refreshSourceCvStatus: () => Promise<void>;
}

const AnalysisContext = createContext<AnalysisContextValue | null>(null);

export function AnalysisProvider({ children }: { children: ReactNode }) {
  const [jobDescription, setJobDescription] = useState("");
  const [analysis, setAnalysisState] = useState<AnalyzeResponse | null>(null);
  const [cv, setCv] = useState<CvGenerateResponse | null>(null);
  const [template, setTemplate] = useState("modern");
  const [pageBudget, setPageBudget] = useState(2);
  const [sourceCvStatus, setSourceCvStatus] = useState<SourceCvStatus | null>(null);
  const [sourceCvStatusFailed, setSourceCvStatusFailed] = useState(false);

  function setAnalysis(value: AnalyzeResponse | null) {
    setAnalysisState(value);
    setCv(null);
  }

  const refreshSourceCvStatus = useCallback(async () => {
    try {
      setSourceCvStatus(await api.getSourceCvStatus());
      setSourceCvStatusFailed(false);
    } catch {
      setSourceCvStatusFailed(true);
    }
  }, []);

  useEffect(() => {
    api
      .getSourceCvStatus()
      .then((status) => {
        setSourceCvStatus(status);
        setSourceCvStatusFailed(false);
      })
      .catch(() => setSourceCvStatusFailed(true));
  }, []);

  return (
    <AnalysisContext.Provider
      value={{
        jobDescription,
        setJobDescription,
        analysis,
        cvContext: analysis?.cvContext ?? null,
        setAnalysis,
        cv,
        setCv,
        template,
        setTemplate,
        pageBudget,
        setPageBudget,
        sourceCvStatus,
        sourceCvStatusFailed,
        setSourceCvStatus,
        refreshSourceCvStatus,
      }}
    >
      {children}
    </AnalysisContext.Provider>
  );
}

export function useAnalysis(): AnalysisContextValue {
  const ctx = useContext(AnalysisContext);
  if (!ctx) throw new Error("useAnalysis must be used within AnalysisProvider");
  return ctx;
}
