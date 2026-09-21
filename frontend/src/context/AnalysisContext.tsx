import { createContext, useContext, useState, type ReactNode } from "react";
import type { AnalyzeResponse, CVContext, CvGenerateResponse } from "../types/career";

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
}

const AnalysisContext = createContext<AnalysisContextValue | null>(null);

export function AnalysisProvider({ children }: { children: ReactNode }) {
  const [jobDescription, setJobDescription] = useState("");
  const [analysis, setAnalysisState] = useState<AnalyzeResponse | null>(null);
  const [cv, setCv] = useState<CvGenerateResponse | null>(null);
  const [template, setTemplate] = useState("modern");

  function setAnalysis(value: AnalyzeResponse | null) {
    setAnalysisState(value);
    setCv(null);
  }

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
