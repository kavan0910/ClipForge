import { useCallback, useEffect, useRef, useState } from 'react'
import { api, subscribe, type ProgressEvent, type ProjectData } from '../api'
import { fmtBytes, fmtDuration, fmtEta } from '../format'

const STEPS = [
  { key: 'acquire', label: 'Get the video' },
  { key: 'audio', label: 'Prepare audio' },
  { key: 'proxy', label: 'Build preview proxy' },
  { key: 'transcribe', label: 'Transcribe with word timing' },
] as const

interface StepState {
  pct: number | null
  detail: string
  seconds?: number
  cached?: boolean
  done: boolean
}

const STAGE_ALIAS: Record<string, string> = { normalize_audio: 'audio', normalize_proxy: 'proxy', diarize: 'transcribe' }

export function ProjectView({ id, onBack }: { id: string; onBack: () => void }) {
  const [project, setProject] = useState<ProjectData | null>(null)
  const [steps, setSteps] = useState<Record<string, StepState>>({})
  const [logs, setLogs] = useState<string[]>([])
  const [showLogs, setShowLogs] = useState(false)
  const [warnings, setWarnings] = useState<string[]>([])
  const unsub = useRef<() => void>(() => undefined)

  const refresh = useCallback(() => api.project(id).then(setProject), [id])

  const listen = useCallback(() => {
    unsub.current()
    unsub.current = subscribe(
      id,
      (e: ProgressEvent) => {
        setLogs((l) => [...l.slice(-199), `${e.type} ${e.stage ?? ''} ${e.message ?? ''}`.trim()])
        if (e.type === 'source_ready' && e.warnings) setWarnings((w) => [...w, ...e.warnings!])
        if (e.type === 'warning' && e.message) setWarnings((w) => [...w, e.action ? `${e.message} ${e.action}` : e.message!])
        const key = STAGE_ALIAS[e.stage ?? ''] ?? e.stage
        if (!key) return
        if (e.type === 'progress') {
          const detail =
            e.bytes != null && e.total
              ? `${fmtBytes(e.bytes)} of ${fmtBytes(e.total)}${e.speed ? ` · ${fmtBytes(e.speed)}/s` : ''} ${fmtEta(e.eta)}`
              : e.chunks && e.chunks > 1
                ? `chunk ${e.chunk} of ${e.chunks}`
                : ''
          setSteps((s) => ({ ...s, [key]: { ...s[key], pct: e.pct ?? null, detail, done: false } }))
        }
        if (e.type === 'stage_done') {
          setSteps((s) => ({ ...s, [key]: { pct: 1, detail: '', seconds: e.seconds, cached: e.cached, done: true } }))
        }
      },
      () => void refresh(),
    )
  }, [id, refresh])

  useEffect(() => {
    void refresh().then(() => listen())
    return () => unsub.current()
  }, [refresh, listen])

  if (!project) return <p className="muted">Loading…</p>
  const running = project.status === 'running'
  const activeIndex = STEPS.findIndex((s) => !steps[s.key]?.done)

  return (
    <div className="grid gap-5">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <button className="btn" onClick={onBack}>← New project</button>
        <h2 style={{ fontSize: 18, flex: 1, minWidth: 0 }}>{project.source?.title ?? 'Working…'}</h2>
        <span className="muted" data-testid="status">{project.status}</span>
      </div>

      {project.status === 'error' && project.error && (
        <div className="alert" role="alert">
          <strong>{project.error.message}</strong>
          {project.error.action && <p style={{ margin: '4px 0 0' }}>{project.error.action}</p>}
          <button className="btn" style={{ marginTop: 8 }} onClick={() => api.resume(id).then(() => { void refresh(); listen() })}>
            Retry
          </button>
        </div>
      )}

      <section className="card" aria-label="Progress">
        <ol className="steps">
          {STEPS.map((s, i) => {
            const st = steps[s.key]
            const done = st?.done || (project.status === 'done')
            const state = done ? 'done' : running && i === activeIndex ? 'active' : project.status === 'error' && i === activeIndex ? 'error' : 'idle'
            if (s.key === 'proxy' && project.source && !project.source.probe.video && !running) return null
            return (
              <li key={s.key} className="step" data-state={state}>
                <span className="dot" data-state={state} />
                <div>
                  <div>{s.label}</div>
                  {state === 'active' && (
                    <>
                      <div className="bar" style={{ marginTop: 6 }} role="progressbar" aria-valuenow={Math.round((st?.pct ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
                        <i style={{ width: `${Math.round((st?.pct ?? 0) * 100)}%` }} />
                      </div>
                      {st?.detail && <div className="muted" style={{ fontSize: 13, marginTop: 4 }}>{st.detail}</div>}
                    </>
                  )}
                </div>
                <span className="muted" style={{ fontSize: 13 }}>
                  {st?.done ? (st.cached ? 'cached' : `${(st.seconds ?? 0).toFixed(1)} s`) : ''}
                </span>
              </li>
            )
          })}
        </ol>
        <div style={{ display: 'flex', gap: 8, marginTop: 16 }}>
          {running && (
            <button className="btn btn-danger" onClick={() => api.cancel(id).then(refresh)}>
              Cancel
            </button>
          )}
          {(project.status === 'cancelled' || project.status === 'idle') && (
            <button className="btn btn-primary" onClick={() => api.resume(id).then(() => { void refresh(); listen() })}>
              Resume
            </button>
          )}
          <button className="btn" onClick={() => setShowLogs((v) => !v)} aria-expanded={showLogs}>
            {showLogs ? 'Hide logs' : 'Logs'}
          </button>
        </div>
        {showLogs && <pre className="logs" style={{ marginTop: 12 }}>{logs.join('\n') || 'No events yet.'}</pre>}
      </section>

      {[...(project.source?.quality.warnings ?? []), ...warnings].filter((w, i, a) => a.indexOf(w) === i).map((w) => (
        <p key={w} className="warn">{w}</p>
      ))}

      {project.source && (
        <section className="card" aria-label="Source quality">
          <h3 style={{ fontSize: 15, marginBottom: 6 }}>Source</h3>
          <p className="muted" style={{ margin: 0 }} data-testid="quality">
            {[project.source.quality.resolution, project.source.quality.fps ? `${project.source.quality.fps} fps` : null, project.source.quality.audio_summary, fmtDuration(project.source.probe.duration)].filter(Boolean).join(' · ')}
          </p>
        </section>
      )}

      {project.transcript && (
        <section className="card" aria-label="Transcript">
          <h3 style={{ fontSize: 15, marginBottom: 6 }} data-testid="transcript-ready">
            {project.transcript.words === 0 ? 'No speech detected' : 'Transcript ready'}
          </h3>
          {project.transcript.words === 0 && (
            <p className="warn">
              The audio has no detectable speech (music or ambient sound only), so there is nothing to clip.
              Try a source with talking, or check that the right audio track was chosen.
            </p>
          )}
          <p className="muted" style={{ marginTop: 0 }}>
            {project.transcript.words} words · {project.transcript.sentences.length} sentences · {project.transcript.language} · {project.transcript.asr_model.split('/').pop()}
            {project.transcript.diarized ? ' · speakers labelled' : ''}
          </p>
          <div>
            {project.transcript.sentences.slice(0, 40).map((s) => (
              <div key={s.id} className="sentence">
                <span className="mono">{fmtDuration(s.start)}</span>
                <span>{s.speaker && <strong>{s.speaker}: </strong>}{s.text}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
