import { useEffect, useState } from 'react'
import { api, type ProjectRow } from '../api'

export function ProjectsList({ onOpen }: { onOpen: (id: string) => void }) {
  const [rows, setRows] = useState<ProjectRow[] | null>(null)
  const [error, setError] = useState('')
  const load = () => api.projects().then(setRows).catch((e: Error) => setError(e.message))
  useEffect(() => { void load() }, [])
  if (error) return <p className="alert" role="alert">{error}</p>
  if (rows === null) return <p className="muted">Loading projects…</p>
  if (rows.length === 0) return <p className="muted">No projects yet. Paste a link or upload a file above to start one.</p>
  return (
    <section aria-label="Projects" className="grid gap-2">
      <h2 style={{ fontSize: 16 }}>Recent projects</h2>
      {rows.slice(0, 12).map((p) => (
        <div key={p.id} className="card" style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '10px 14px' }}>
          <button className="btn" onClick={() => onOpen(p.id)} style={{ flex: 1, textAlign: 'left', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.title}</button>
          <span className="muted" style={{ fontSize: 13 }}>{p.status}</span>
          <button className="btn btn-danger" aria-label={`Delete ${p.title}`} onClick={() => { if (window.confirm('Delete this project and every file it made?')) void api.remove(p.id).then(load) }}>Delete</button>
        </div>
      ))}
    </section>
  )
}
