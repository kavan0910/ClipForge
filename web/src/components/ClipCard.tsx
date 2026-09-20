import type { ClipRow } from '../api'
import { fmtDuration } from '../format'

const DIMENSIONS: [string, string][] = [
  ['hook', 'Hook'],
  ['self_contained', 'Self-contained'],
  ['single_idea', 'Single idea'],
  ['payoff', 'Payoff'],
  ['emotion_novelty_utility', 'Feeling / novelty / use'],
  ['shareability', 'Shareability'],
]

export function ClipCard({
  clip,
  onMoreLike,
}: {
  clip: ClipRow
  onMoreLike: (id: string) => void
}) {
  const score = Math.round(clip.rank_score * 100)
  return (
    <article className="card" data-testid="clip-card" style={{ display: 'grid', gap: 10 }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'baseline' }}>
        <h3 style={{ fontSize: 16 }}>{clip.title}</h3>
        <span className="muted" style={{ whiteSpace: 'nowrap' }}>
          {fmtDuration(clip.start)}–{fmtDuration(clip.end)} · {Math.round(clip.duration)} s
        </span>
      </header>
      <p style={{ margin: 0, fontWeight: 600 }}>“{clip.hook}”</p>
      {!clip.hook_check.passed && (
        <p className="warn" role="note">
          This hook may not match the clip (unsupported: {clip.hook_check.missing.join(', ')}). Edit it before posting.
        </p>
      )}
      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span className="muted" title="A blend of the AI's rating and measured audio and audience signals. Not a virality prediction.">
            Clip score (heuristic)
          </span>
          <strong>{score}</strong>
        </div>
        <div className="bar" aria-hidden><i style={{ width: `${score}%` }} /></div>
      </div>
      <p className="muted" style={{ margin: 0 }}>{clip.why_it_works}</p>
      <details>
        <summary className="muted" style={{ cursor: 'pointer' }}>Scores by dimension</summary>
        <dl style={{ display: 'grid', gridTemplateColumns: '1fr 44px', gap: '4px 12px', margin: '8px 0 0' }}>
          {DIMENSIONS.map(([k, label]) => (
            <div key={k} style={{ display: 'contents' }}>
              <dt>
                <div style={{ fontSize: 13 }}>{label}</div>
                <div className="bar"><i style={{ width: `${clip.scores[k]}%` }} /></div>
              </dt>
              <dd style={{ margin: 0, textAlign: 'right' }}>{clip.scores[k]}</dd>
            </div>
          ))}
        </dl>
      </details>
      <footer style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {clip.risk_flags.map((f) => (
          <span key={f} className="warn" style={{ padding: '2px 8px' }}>{f.replace('_', ' ')}</span>
        ))}
        <span className="muted" style={{ flex: 1 }}>{clip.hashtags.map((h) => `#${h}`).join(' ')}</span>
        <button className="btn" onClick={() => onMoreLike(clip.id)}>More like this</button>
      </footer>
    </article>
  )
}
