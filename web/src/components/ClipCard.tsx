import { useState } from 'react'
import { api, type ClipRow } from '../api'
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
  onOpen,
  onDecide,
  characterMode = false,
}: {
  clip: ClipRow
  onMoreLike: (id: string) => void
  onOpen: (id: string) => void
  onDecide: (id: string, status: 'approved' | 'rejected') => void
  /** A character edit has no transcript, so the editor (trim/captions/scores) doesn't apply — only
   * approve/reject and the publish step below do. */
  characterMode?: boolean
}) {
  const score = Math.round(clip.rank_score * 100)
  const tone = clip.status === 'approved' ? 'ok' : clip.status === 'rejected' ? 'bad' : undefined
  const [copied, setCopied] = useState(false)
  const tiktokCaption = `${clip.hook} ${clip.hashtags.slice(0, 5).map((h) => `#${h}`).join(' ')}`.trim().slice(0, 2200)
  const copyCaption = () => {
    void navigator.clipboard.writeText(tiktokCaption).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
  }
  return (
    <article className="card" data-testid="clip-card" data-hover="true" style={{ display: 'grid', gap: 14, opacity: clip.status === 'rejected' ? 0.62 : 1 }}>
      <header style={{ display: 'flex', gap: 16, alignItems: 'center' }}>
        <div className="score-ring" style={{ ['--p' as string]: score }} title="Clip score: a blend of the AI's rating and measured audio and audience signals. Not a virality prediction." role="img" aria-label={`Clip score ${score} out of 100 (heuristic)`}>
          <span>{score}</span>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <h3 style={{ fontSize: 17, lineHeight: 1.3 }}>{clip.title}</h3>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 6, alignItems: 'center' }}>
            <span className="chip">{fmtDuration(clip.start)}–{fmtDuration(clip.end)} · {Math.round(clip.duration)} s</span>
            {clip.status !== 'proposed' && <span className="chip" data-tone={tone}>{clip.status}</span>}
            {clip.risk_flags.map((f) => <span key={f} className="chip" data-tone="bad">{f.replace('_', ' ')}</span>)}
          </div>
        </div>
      </header>
      <p style={{ margin: 0, fontWeight: 650, fontSize: 16 }}>“{clip.hook}”</p>
      {!clip.hook_check.passed && (
        <p className="warn" role="note">
          This hook may not match the clip (unsupported: {clip.hook_check.missing.join(', ')}). Edit it before posting.
        </p>
      )}
      <p className="muted" style={{ margin: 0 }}>{clip.why_it_works}</p>
      {!characterMode && (
        <details>
          <summary className="muted" style={{ cursor: 'pointer' }}>Scores by dimension</summary>
          <dl style={{ display: 'grid', gridTemplateColumns: '1fr 44px', gap: '6px 12px', margin: '10px 0 0' }}>
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
      )}
      <footer style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span className="muted" style={{ flex: 1, fontSize: 13 }}>{clip.hashtags.map((h) => `#${h}`).join(' ')}</span>
        {!characterMode && <button className="btn" onClick={() => onMoreLike(clip.id)}>More like this</button>}
        <button className="btn" aria-pressed={clip.status === 'approved'} onClick={() => onDecide(clip.id, 'approved')} data-testid="card-approve">Approve</button>
        <button className="btn btn-danger" aria-pressed={clip.status === 'rejected'} onClick={() => onDecide(clip.id, 'rejected')}>Reject</button>
        {!characterMode && <button className="btn btn-primary" onClick={() => onOpen(clip.id)} data-testid="open-editor">Edit &amp; render</button>}
      </footer>
      {clip.package_ready && (
        <div className="card" style={{ padding: 12, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', background: 'var(--surface-2)' }} role="status">
          <span style={{ fontSize: 13 }}>📱 Ready to post &mdash; rendered and packaged for TikTok, Reels and Shorts.</span>
          <button className="btn" onClick={copyCaption}>{copied ? 'Copied' : 'Copy TikTok caption'}</button>
          {clip.package_path && <button className="btn" onClick={() => void api.reveal(clip.package_path!)}>Reveal in Finder</button>}
        </div>
      )}
      {!clip.package_ready && clip.rendered && (
        <p className="muted" style={{ margin: 0, fontSize: 12 }}>Rendered; building the ready-to-upload package&hellip;</p>
      )}
    </article>
  )
}
