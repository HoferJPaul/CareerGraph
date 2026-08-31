import type { ReactNode } from "react";

interface MetricCardProps {
  label: string;
  value: ReactNode;
}

export default function MetricCard({ label, value }: MetricCardProps) {
  return (
    <div className="card" style={{ textAlign: "center", padding: "18px 12px" }}>
      <div style={{ fontSize: "1.7rem", fontWeight: 700, color: "var(--color-accent-strong)" }}>
        {value}
      </div>
      <div style={{ fontSize: "0.78rem", color: "var(--color-text-faint)", marginTop: 4 }}>
        {label}
      </div>
    </div>
  );
}
