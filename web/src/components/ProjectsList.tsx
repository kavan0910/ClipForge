import { useEffect, useState } from 'react'
import { api, type ProjectRow } from '../api'

export function ProjectsList({ onOpen }: { onOpen: (id: string) => void }) {
  const [rows, setRows] = useState<ProjectRow[] | null>(null)
  const [error, setError] = useState('')
  const load = () => api.projects().then(setRows).catch((e: Error) => setError(e.message))
  useEffect(() => { void load() }, [])
  if (error) return <p className="alert" role="alert">{error}</p>
  if (rows === null) return <div className="pgrid" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="skeleton" style={{ height: 96 }} />)}</div>
  if (rows.length === 0) return <p className="muted" style={{ textAlign: 'center' }}>No projects yet. Paste a link or drop a file above to make your first shorts.</p>
  const tone = (st: string) => (st === 'done' ? 'ok' : st === 'running' ? 'run' : st === 'error' ? 'bad' : undefined)
  return (
    <section aria-label="Projects" className="grid gap-3">
      <h2 style={{ fontSize: 18 }}>Recent projects</h2>
      <div className="pgrid">
        {rows.slice(0, 12).map((p) => (
          <article key={p.id} className="card pcard" data-hover="true" style={{ padding: 16 }}>
            <button onClick={() => onOpen(p.id)} style={{ all: 'unset', cursor: 'pointer', display: 'grid', gap: 10 }} aria-label={`Open ${p.title}`}>
              <h3>{p.title}</h3>
              <span className="chip" data-tone={tone(p.status)}>{p.status === 'running' && <span className="spinner" />}{p.status}</span>
            </button>
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button className="btn btn-danger" style={{ padding: '4px 10px', fontSize: 13 }} aria-label={`Delete ${p.title}`} onClick={() => { if (window.confirm('Delete this project and every file it made?')) void api.remove(p.id).then(load) }}>Delete</button>
            </div>
          </article>
        ))}
      </div>
    </section>
  )
}
