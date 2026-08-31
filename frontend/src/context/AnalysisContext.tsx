import { createContext, useContext, useState, type ReactNode } from "react";
import type { AnalyzeResponse, CVContext } from "../types/career";

interface AnalysisContextValue {
  jobDescription: string;
  setJobDescription: (value: string) => void;

  // Primary presentation flow: Claude-extracted requirements, validated and
  // matched via /api/requirements/analyze. Powers Match Review + CV Context Ready.
  cvContext: CVContext | null;
  setCvContext: (value: CVContext | null) => void;

  // Debug/dev-only flow: single-shot raw-JD analyze + deterministic CV renderer
  // (see the "Debug" nav item / NewCv page's debug panel). Kept separate so the
  // two flows never overwrite each other's state.
  analysis: AnalyzeResponse | null;
  setAnalysis: (value: AnalyzeResponse | null) => void;
  cvMarkdown: string | null;
  setCvMarkdown: (value: string | null) => void;
  template: string;
  setTemplate: (value: string) => void;
}

const AnalysisContext = createContext<AnalysisContextValue | null>(null);

export function AnalysisProvider({ children }: { children: ReactNode }) {
  const [jobDescription, setJobDescription] = useState("");
  const [cvContext, setCvContext] = useState<CVContext | null>(null);
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [cvMarkdown, setCvMarkdown] = useState<string | null>(null);
  const [template, setTemplate] = useState("modern");

  return (
    <AnalysisContext.Provider
      value={{
        jobDescription,
        setJobDescription,
        cvContext,
        setCvContext,
        analysis,
        setAnalysis,
        cvMarkdown,
        setCvMarkdown,
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
