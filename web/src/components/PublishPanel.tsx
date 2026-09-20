import { useEffect, useState } from 'react'
import { api, type YtJob } from '../api'

export function PublishPanel({ projectId, clipId, rendered }: { projectId: string; clipId: string; rendered: boolean }) {
  const [yt, setYt] = useState<{ configured: boolean; connected: boolean; message?: string; action?: string } | null>(null)
  const [job, setJob] = useState<YtJob>({ state: 'idle', pct: 0 })
  const [when, setWhen] = useState('')
  const [privacy, setPrivacy] = useState('private')
  const [pkg, setPkg] = useState('')
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
