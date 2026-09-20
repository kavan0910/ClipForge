import type { CostState } from '../api'

export function CostMeter({ cost }: { cost: CostState | null }) {
  const pct = cost ? Math.min(100, (cost.usd / cost.cap_usd) * 100) : 0
  return (
    <div className="warn" role="status" aria-label="LLM cost" data-testid="cost-meter">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
        <span>
          AI cost{' '}
          <strong>${(cost?.usd ?? 0).toFixed(4)}</strong>
          <span className="muted"> of ${(cost?.cap_usd ?? 1).toFixed(2)} cap</span>
        </span>
        {cost && (
          <span className="muted">
            {cost.input_tokens.toLocaleString()} in · {cost.cache_read_tokens.toLocaleString()} cached · {cost.output_tokens.toLocaleString()} out
          </span>
        )}
      </div>
      <div className="bar" style={{ marginTop: 6 }} aria-hidden>
        <i style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}
