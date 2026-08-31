import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, formatApiErrorDetail } from "../api/client";
import StepIndicator from "../components/StepIndicator";
import { useAnalysis } from "../context/AnalysisContext";
import type { RequirementList } from "../types/career";

export default function RequirementsUploadPage() {
  const { setCvContext } = useAnalysis();
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const content = await file.text();
    setText(content);
    setErrors([]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  async function handleValidateAndMatch() {
    setErrors([]);

    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (err) {
      setErrors([`Invalid JSON: ${err instanceof Error ? err.message : String(err)}`]);
      return;
    }

    setLoading(true);
    try {
      // Not actually validated yet -- the server is the real gate (schema validation
      // happens there; see api_schemas.py / requirement_schema.py). This cast only
      // satisfies the client's own type signature for an unknown JSON blob.
      const result = await api.analyzeRequirements(parsed as RequirementList);
      setCvContext(result.cvContext);
      navigate("/match-review");
    } catch (err) {
      if (err instanceof ApiError) {
        setErrors(formatApiErrorDetail(err.detail));
      } else {
        setErrors([err instanceof Error ? err.message : "Failed to validate requirements."]);
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div>
      <StepIndicator current={2} />
      <div className="page-header">
        <h1>Upload Claude's requirements</h1>
        <p>
          Paste or upload the requirements.json returned by Claude. CareerGraph will validate it and match the
          requirements against verified career evidence in Neo4j.
        </p>
      </div>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <label htmlFor="req-json" style={{ fontSize: "0.85rem", fontWeight: 600 }}>
            requirements.json
          </label>
          <label className="btn btn-ghost" style={{ fontSize: "0.82rem", cursor: "pointer" }}>
            Upload .json
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,application/json"
              onChange={handleFileChange}
              style={{ display: "none" }}
            />
          </label>
        </div>
        <textarea
          id="req-json"
          rows={16}
          placeholder='Paste the JSON Claude returned, e.g. { "requirements": [ ... ] }'
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setErrors([]);
          }}
        />
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 14 }}>
          <button className="btn btn-primary" onClick={handleValidateAndMatch} disabled={loading || !text.trim()}>
            {loading ? "Validating & matching…" : "Validate & Match"}
          </button>
        </div>
        {errors.length > 0 && (
          <div className="error-box">
            <strong>Validation failed — nothing was matched against Neo4j:</strong>
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
