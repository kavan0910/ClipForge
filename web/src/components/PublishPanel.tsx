import { useEffect, useState } from 'react'
import { api, type TikTokPreflight, type YtJob } from '../api'

export function PublishPanel({ projectId, clipId, rendered }: { projectId: string; clipId: string; rendered: boolean }) {
  const [yt, setYt] = useState<{ configured: boolean; connected: boolean; message?: string; action?: string } | null>(null)
  const [job, setJob] = useState<YtJob>({ state: 'idle', pct: 0 })
  const [when, setWhen] = useState('')
  const [privacy, setPrivacy] = useState('private')
  const [pkg, setPkg] = useState('')
  const [preflight, setPreflight] = useState<TikTokPreflight | null>(null)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    void api.ytStatus().then(setYt).catch(() => setYt(null))
    void api.ytJob(projectId, clipId).then(setJob).catch(() => undefined)
  }, [projectId, clipId])
  useEffect(() => {
    if (job.state !== 'running') return
    const h = setInterval(() => void api.ytJob(projectId, clipId).then(setJob), 1500)
    return () => clearInterval(h)
  }, [job.state, projectId, clipId])

  const fail = (e: Error) => setError(e.message)
  return (
    <section className="card" aria-label="Publish">
      <h3 style={{ fontSize: 15 }}>Publish</h3>
      {!rendered && <p className="muted" style={{ margin: '6px 0 0' }}>Render the clip first, then publish or package it.</p>}
      {rendered && (
        <div className="grid gap-3" style={{ marginTop: 8 }}>
          <div>
            <strong style={{ fontSize: 14 }}>TikTok preflight</strong>
            <p className="muted" style={{ margin: '4px 0 8px', fontSize: 12 }}>Checks the rendered file for technical issues and visible QR/static-content signals. It cannot determine ownership or predict TikTok's decision.</p>
            <button className="btn" disabled={checking} onClick={() => {
              setError('')
              setChecking(true)
              void api.tiktokPreflight(projectId, clipId).then(setPreflight).catch(fail).finally(() => setChecking(false))
            }}>{checking ? 'Checking…' : 'Check TikTok readiness'}</button>
            {preflight && (
              <div style={{ marginTop: 8 }}>
                <p className="muted" role="status" style={{ margin: '0 0 6px' }}>{preflight.review_count ? `${preflight.review_count} item${preflight.review_count === 1 ? '' : 's'} to review` : 'No technical concerns found'}. {preflight.notice}</p>
                <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 6 }}>
                  {preflight.checks.map((check) => (
                    <li key={check.name}>
                      <strong className={check.status === 'ok' ? 'good' : 'warn'}>{check.status === 'ok' ? '✓' : 'Review'} · {check.name}</strong>
                      <div className="muted" style={{ fontSize: 12 }}>{check.detail}</div>
                      {check.action && <div className="muted" style={{ fontSize: 12 }}>{check.action}</div>}
                    </li>
                  ))}
                </ul>
                <p className="muted" style={{ margin: '8px 0 0', fontSize: 12 }}>Make changes in Trim & transcript, Captions, or Layout, then render again. Preflight never hides watermarks or removes QR codes automatically.</p>
              </div>
            )}
          </div>
          <div>
            <button className="btn" onClick={() => { setError(''); void api.makePackage(projectId, clipId).then((r) => setPkg(r.path)).catch(fail) }}>Make ready-to-upload package</button>
            {pkg && <p className="muted" style={{ margin: '6px 0 0' }} role="status">Saved to <span className="mono">{pkg}</span> with copy for YouTube Shorts, Instagram Reels and TikTok.</p>}
            <p className="muted" style={{ margin: '4px 0 0', fontSize: 12 }}>Instagram and TikTok do not offer an upload API a local app can use, so these go through the package.</p>
          </div>
          <div>
            <strong style={{ fontSize: 14 }}>YouTube</strong>
            {yt && !yt.configured && <p className="muted" style={{ margin: '4px 0' }}>{yt.message} {yt.action} <a href="#/settings">Open Settings</a></p>}
            {yt?.configured && !yt.connected && <div><button className="btn" onClick={() => { setError(''); void api.ytConnect().then(() => api.ytStatus()).then(setYt).catch(fail) }}>Connect YouTube</button> <span className="muted">A browser tab opens to approve access.</span></div>}
            {yt?.connected && (
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center', marginTop: 6 }}>
                <select className="field" style={{ width: 130 }} aria-label="Privacy" value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
                  <option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option>
                </select>
                <input className="field" style={{ width: 210 }} type="datetime-local" aria-label="Schedule" value={when} onChange={(e) => setWhen(e.target.value)} />
                <button className="btn btn-primary" disabled={job.state === 'running'} onClick={() => { setError(''); setJob({ state: 'running', pct: 0 }); void api.ytPublish(projectId, clipId, { schedule: when || undefined, privacy }).catch((e: Error) => { setJob({ state: 'idle', pct: 0 }); fail(e) }) }}>
                  {job.state === 'running' ? `Uploading ${Math.round(job.pct * 100)}%` : when ? 'Schedule upload' : 'Upload'}
                </button>
              </div>
            )}
            {job.state === 'done' && <p role="status" style={{ margin: '6px 0 0' }}>Uploaded: <a href={job.url} target="_blank" rel="noreferrer">{job.url}</a>{job.scheduled_for ? ` (goes public ${new Date(job.scheduled_for).toLocaleString()})` : ''}{job.warnings?.map((w) => <span key={w} className="warn" style={{ display: 'block' }}>{w}</span>)}</p>}
            {job.state === 'error' && <p className="alert" role="alert">{job.message} {job.action}</p>}
          </div>
          {error && <p className="alert" role="alert">{error}</p>}
        </div>
      )}
    </section>
  )
}
