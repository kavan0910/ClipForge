import { useCallback, useEffect, useState } from 'react'
import { api, type RenderRow } from '../api'

export function RenderQueue() {
  const [rows, setRows] = useState<RenderRow[] | null>(null)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [format, setFormat] = useState('mp4_folder')
  const [result, setResult] = useState<{ name: string; path: string; warnings: string[]; is_zip: boolean } | null>(null)
  const [error, setError] = useState('')
  const key = (r: RenderRow) => `${r.project}/${r.clip}`

  const load = useCallback(() => api.renders().then(setRows).catch((e: Error) => setError(e.message)), [])
  useEffect(() => {
    void load()
    const h = setInterval(() => void load(), 2000)
    return () => clearInterval(h)
  }, [load])

  const toggle = (r: RenderRow) => setPicked((s) => { const n = new Set(s); if (n.has(key(r))) n.delete(key(r)); else n.add(key(r)); return n })
  const doExport = () => {
    setError('')
    api.exportClips([...picked].map((k) => k.split('/') as [string, string]), format).then(setResult).catch((e: Error) => setError(e.message))
  }

  if (error && !rows) return <div className="alert" role="alert">{error}</div>
  if (!rows) return <p className="muted">Loading…</p>
  const ready = rows.filter((r) => r.has_video)
  return (
    <div className="grid gap-4" data-testid="queue">
      <h1 style={{ fontSize: 24 }}>Render queue</h1>
      {rows.length === 0 && <div className="card muted">Nothing rendered yet. Open a clip, then choose Render.</div>}
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 8 }}>
        {rows.map((r) => (
          <li key={key(r)} className="card" style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <input type="checkbox" aria-label={`Select ${r.title}`} disabled={!r.has_video} checked={picked.has(key(r))} onChange={() => toggle(r)} />
            <div style={{ flex: 1, minWidth: 200 }}>
              <strong>{r.title}</strong>
              <div className="muted" style={{ fontSize: 13 }}>{Math.round(r.duration)} s · {r.status}</div>
              {r.state === 'running' && <div className="bar" role="progressbar" aria-valuenow={Math.round(r.pct * 100)}><i style={{ width: `${r.pct * 100}%` }} /></div>}
              {r.state === 'error' && <div className="warn" role="alert">{r.error?.message} {r.error?.action}</div>}
            </div>
            <span className="muted">{r.state === 'running' ? `${Math.round(r.pct * 100)}% ${r.note ?? ''}` : r.state === 'done' || r.has_video ? 'ready' : r.state}</span>
            {r.state === 'running' && <button className="btn" onClick={() => void api.cancelRender(r.project, r.clip).then(load)}>Cancel</button>}
            {r.has_video && <a className="btn" href={`/api/files/${r.project}/clips/${r.clip}/final.mp4`} download>Download</a>}
            <a className="btn" href={`#/p/${r.project}/c/${r.clip}`}>Open</a>
          </li>
        ))}
      </ul>
      {ready.length > 0 && (
        <section className="card" aria-label="Export">
          <h3 style={{ fontSize: 15 }}>Export {picked.size} selected</h3>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', margin: '8px 0' }}>
            <select className="field" aria-label="Format" style={{ width: 260 }} value={format} onChange={(e) => setFormat(e.target.value)}>
              <option value="mp4_folder">MP4 folder with captions and metadata</option>
              <option value="mp4_zip">MP4 zip</option>
              <option value="otio">OpenTimelineIO</option>
              <option value="fcpxml">Final Cut Pro X (FCPXML)</option>
              <option value="fcp7">Final Cut Pro 7 XML (Premiere)</option>
              <option value="edl">EDL (CMX 3600, DaVinci Resolve)</option>
            </select>
            <button className="btn btn-primary" disabled={picked.size === 0} onClick={doExport} data-testid="export">Export</button>
          </div>
          {error && <p className="alert" role="alert">{error}</p>}
          {result && (
            <div role="status" data-testid="export-result">
              <p style={{ margin: 0 }}>Exported to <span className="mono">{result.path}</span></p>
              {result.is_zip && <a className="btn" href={`/api/exports/${result.name}`} download>Download zip</a>}
              {result.warnings.map((w) => <p key={w} className="warn">{w}</p>)}
            </div>
          )}
        </section>
      )}
    </div>
  )
}
