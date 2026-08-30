interface SimilarityBarProps {
  value: number
  label?: string
}

export function SimilarityBar({ value, label }: SimilarityBarProps) {
  const pct = Math.max(0, Math.min(1, value || 0)) * 100
  const tone = pct >= 75 ? 'green' : pct >= 50 ? 'yellow' : 'red'
  return (
    <div className="simbar">
      <div className="simbar__track">
        <div className={`simbar__fill simbar__fill--${tone}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="simbar__label">{label ?? `${pct.toFixed(1)}%`}</span>
    </div>
  )
}
