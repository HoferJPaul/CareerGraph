import { useEffect, useState } from "react";

interface ProgressProps {
  title: string;
  steps: string[];
}

/** Busy state for a long server-side request. The elapsed timer is real; the steps are listed
 * as what the server is doing, not as a claim about which one is currently running. */
export default function Progress({ title, steps }: ProgressProps) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className="card progress" role="status" aria-live="polite" aria-busy="true">
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span className="spinner" aria-hidden="true" />
        <strong>{title}</strong>
        <span style={{ color: "var(--color-text-faint)", fontSize: "0.82rem", marginLeft: "auto" }}>{seconds}s</span>
      </div>
      <ol className="progress-steps">
        {steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
    </div>
  );
}
