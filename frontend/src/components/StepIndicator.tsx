const STEPS = [
  { n: 1, label: "Job" },
  { n: 2, label: "Match" },
  { n: 3, label: "CV" },
] as const;

export default function StepIndicator({ current }: { current: 1 | 2 | 3 }) {
  return (
    <div style={{ display: "flex", gap: 8, marginBottom: 20, alignItems: "center" }}>
      {STEPS.map((s, i) => (
        <span key={s.n} style={{ display: "flex", alignItems: "center", gap: 8 }}>
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
