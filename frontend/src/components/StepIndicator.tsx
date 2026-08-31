const STEPS = [
  { n: 1, label: "Job" },
  { n: 2, label: "Claude" },
  { n: 3, label: "Neo4j" },
  { n: 4, label: "Claude" },
] as const;

export default function StepIndicator({ current }: { current: 1 | 2 | 3 | 4 }) {
  return (
    <div style={{ display: "flex", gap: 8, marginBottom: 20, alignItems: "center" }}>
      {STEPS.map((s, i) => (
        <span key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            className={`badge ${s.n === current ? "badge-neutral" : ""}`}
            style={{ opacity: s.n === current ? 1 : 0.45 }}
          >
            {s.n} {s.label}
          </span>
          {i < STEPS.length - 1 && <span style={{ color: "var(--color-text-faint)" }}>→</span>}
        </span>
      ))}
    </div>
  );
}
