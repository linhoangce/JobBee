/** Match score as a ring: green from 80, honey from 60, grey below. */
export default function ScoreRing({ score, size = 52 }: { score: number | null; size?: number }) {
  const value = score ?? 0;
  const stroke = 5;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const color = value >= 80 ? "var(--ok)" : value >= 60 ? "var(--accent)" : "var(--text-3)";
  return (
    <div className="score-ring" style={{ width: size, height: size }} title={`Match ${value}/100`}>
      <svg width={size} height={size}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--surface-3)"
          strokeWidth={stroke}
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - value / 100)}
        />
      </svg>
      <div className="score-ring-value">{score ?? "–"}</div>
    </div>
  );
}
