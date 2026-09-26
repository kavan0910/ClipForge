import { useCallback, useEffect, useRef, useState } from 'react'
import { api, subscribe, type ClipRow, type CostState, type ProgressEvent, type ProjectData } from '../api'
import { ClipCard } from './ClipCard'
import { CostMeter } from './CostMeter'
import { fmtBytes, fmtDuration, fmtEta } from '../format'

interface Step { key: string; label: string }

const STEPS: Step[] = [
  { key: 'acquire', label: 'Get the video' },
  { key: 'audio', label: 'Prepare audio' },
  { key: 'proxy', label: 'Build preview proxy' },
  { key: 'transcribe', label: 'Transcribe with word timing' },
  { key: 'signals', label: 'Measure audio, motion and audience signals' },
  { key: 'curate', label: 'Find, score and refine clips' },
]

// Character-edit mode has no transcript/ASR stage at all: it works from shots, not dialogue.
const CHARACTER_STEPS: Step[] = [
  { key: 'acquire', label: 'Get the video' },
  { key: 'proxy', label: 'Build preview proxy' },
  { key: 'signals', label: 'Detect shots and motion' },
  { key: 'curate', label: 'Scan frames for the character' },
]

interface StepState {
  pct: number | null
  detail: string
  seconds?: number
  cached?: boolean
  done: boolean
}

const STAGE_ALIAS: Record<string, string> = {
  normalize_audio: 'audio',
  normalize_proxy: 'proxy',
  diarize: 'transcribe',
  signals_events: 'signals',
  signals_visual: 'signals',
}

export function ProjectView({ id, onBack }: { id: string; onBack: () => void }) {
  const [project, setProject] = useState<ProjectData | null>(null)
  const [steps, setSteps] = useState<Record<string, StepState>>({})
  const [logs, setLogs] = useState<string[]>([])
  const [showLogs, setShowLogs] = useState(false)
  const [warnings, setWarnings] = useState<string[]>([])
  const [cost, setCost] = useState<CostState | null>(null)
  const [stageCost, setStageCost] = useState<Record<string, number>>({})
  const [clips, setClips] = useState<ClipRow[]>([])
  const [steer, setSteer] = useState('')
  const unsub = useRef<() => void>(() => undefined)
  const [actionError, setActionError] = useState('')
  const [track, setTrack] = useState<number | null>(null)

  const refresh = useCallback(
    () =>
      Promise.all([api.project(id), api.clips(id)]).then(([p, c]) => {
        setProject(p)
        setClips(c.clips)
      }),
    [id],
  )

  const listen = useCallback(() => {
    unsub.current()
    unsub.current = subscribe(
      id,
      (e: ProgressEvent) => {
        setLogs((l) => [...l.slice(-199), `${e.type} ${e.stage ?? ''} ${e.message ?? ''}`.trim()])
        if (e.type === 'source_ready' && e.warnings) setWarnings((w) => [...w, ...e.warnings!])
        if (e.type === 'cost' && e.call_usd !== undefined) {
          const k = e.stage ?? 'curate'
          setStageCost((c) => ({ ...c, [k]: (c[k] ?? 0) + e.call_usd! }))
        }
        if (e.type === 'cost' && e.usd !== undefined) {
          setCost({ usd: e.usd, cap_usd: e.cap_usd ?? 1, input_tokens: e.input_tokens ?? 0, output_tokens: e.output_tokens ?? 0, cache_read_tokens: e.cache_read_tokens ?? 0 })
        }
        if (e.type === 'clips_ready') setSteps((s) => ({ ...s, curate: { pct: 1, detail: '', done: true } }))
        if (e.type === 'warning' && e.message) setWarnings((w) => [...w, e.action ? `${e.message} ${e.action}` : e.message!])
        const key = STAGE_ALIAS[e.stage ?? ''] ?? e.stage
        if (!key) return
        if (e.type === 'progress' && e.stage === 'diarize') {
          setSteps((s) => ((s[key]?.pct ?? 0) >= 0.999 ? { ...s, [key]: { ...s[key], pct: 1, detail: 'Finishing speaker identification. It runs alongside transcription and shows no percentage.', done: false } } : s))
          return
        }
        if (e.type === 'progress') {
          const detail =
            e.note ? e.note : e.bytes != null && e.total
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

  const recurate = (body: { mode: string; reference_clip_id?: string; steering?: string }) => {
    setActionError('')
    return api.recurate(id, body).then(() => { void refresh(); listen() }).catch((e: { message?: string; action?: string }) => setActionError([e.message, e.action].filter(Boolean).join(' ')))
  }

  // The best-ranked clip renders and packages itself in the background after clip selection
  // finishes; the main job's event stream is long closed by then, so poll until it catches up
  // (capped, in case auto-render is off or the clip was rejected before it finished).
  const topCandidate = [...clips].filter((c) => c.status !== 'rejected').sort((a, b) => b.rank_score - a.rank_score)[0]
  const awaitingAutoPackage = project?.status === 'done' && !!topCandidate && !topCandidate.package_ready
  useEffect(() => {
    if (!awaitingAutoPackage) return
    let attempts = 0
    const h = setInterval(() => {
      attempts += 1
      if (attempts > 60) { clearInterval(h); return }
      void api.clips(id).then((r) => setClips(r.clips))
    }, 3000)
    return () => clearInterval(h)
  }, [awaitingAutoPackage, id])

  if (!project) return <div className="grid gap-4" aria-busy="true"><div className="skeleton" style={{ height: 56 }} /><div className="skeleton" style={{ height: 260 }} /><div className="skeleton" style={{ height: 120 }} /></div>
  const characterMode = project.mode === 'character_edit'
  const stepsList = characterMode ? CHARACTER_STEPS : STEPS
  const running = project.status === 'running'
  const activeIndex = stepsList.findIndex((s) => !steps[s.key]?.done)

  const visible = stepsList.filter((s) => !(s.key === 'proxy' && project.source && !project.source.probe.video && !running))
  const stateOf = (key: string, i: number) => {
    const st = steps[key]
    if (st?.done || project.status === 'done') return 'done'
    if (running && i === activeIndex) return 'active'
    if (project.status === 'error' && i === activeIndex) return 'error'
    return 'idle'
  }
  const states = visible.map((s) => stateOf(s.key, stepsList.indexOf(s)))
  const doneCount = states.filter((x) => x === 'done').length
  const activeStep = visible.find((_, i) => states[i] === 'active')
  const activePct = activeStep ? (steps[activeStep.key]?.pct ?? 0) : 0
  const finished = project.status === 'done' || (visible.length > 0 && doneCount === visible.length)
  const overall = finished ? 1 : Math.min((doneCount + activePct) / visible.length, 0.999)
  const pctText = `${Math.round(overall * 100)}%`
  // Only the AI steps cost money; every other step runs on this computer. Unknown cost stages belong to clip selection.
  const stepOf = (stage: string) => (stepsList.some((x) => x.key === (STAGE_ALIAS[stage] ?? stage)) ? (STAGE_ALIAS[stage] ?? stage) : 'curate')
  const stepCost = (key: string) => Object.entries(stageCost).filter(([k]) => stepOf(k) === key).reduce((a, [, v]) => a + v, 0)
  const breakdown = (key: string) => Object.entries(stageCost).filter(([k]) => stepOf(k) === key).map(([k, v]) => `${k}: ${fmtUsd(v)}`).join(' · ')
  const fmtUsd = (v: number) => `$${v < 0.01 ? v.toFixed(4) : v.toFixed(2)}`
  const R = 54, C = 2 * Math.PI * R
  const headline =
    finished ? 'Ready to review'
    : project.status === 'error' ? 'Something went wrong'
    : project.status === 'cancelled' ? 'Paused'
    : project.status === 'idle' ? 'Paused'
    : activeStep?.label ?? 'Starting'
  const sub = (activeStep && steps[activeStep.key]?.detail) || (running ? 'Working on your video. You can leave this page open.' : '')
  const ringColour = project.status === 'error' ? 'var(--danger)' : finished ? 'var(--ok)' : 'var(--accent)'

  return (
    <div className="grid gap-5">
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <button className="btn" onClick={onBack}>← New project</button>
        <span className="muted" style={{ fontSize: 13 }}>Project</span>
        <span className="mono" style={{ fontSize: 13 }} data-testid="status">{project.status}</span>
      </div>

      {actionError && <div className="alert" role="alert" data-testid="action-error"><strong>{actionError}</strong></div>}

      {project.status === 'error' && project.error?.code === 'choose_audio_track' && (
        <section className="card" aria-label="Choose audio track" data-testid="audio-track-choice" style={{ display: 'grid', gap: 12 }}>
          <div>
            <h3 style={{ fontSize: 17 }}>Which audio track should we use?</h3>
            <p className="muted" style={{ margin: '4px 0 0' }}>{project.error.message} {project.error.action}</p>
          </div>
          <div role="radiogroup" aria-label="Audio tracks" style={{ display: 'grid', gap: 8 }}>
            {(project.error.tracks ?? []).map((t) => (
              <label key={t.index} className="card" data-hover="true" style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '12px 14px', cursor: 'pointer', borderColor: track === t.index ? 'var(--accent)' : undefined }}>
                <input type="radio" name="audio-track" checked={track === t.index} onChange={() => setTrack(t.index)} />
                <span style={{ flex: 1 }}>
                  <strong>{t.language ? t.language.toUpperCase() : 'Unknown language'}</strong>
                  {t.title ? ` · ${t.title}` : ''}
                  <span className="muted"> · {t.channels === 2 ? 'stereo' : t.channels === 1 ? 'mono' : `${t.channels} ch`} · {t.codec} · track {t.index}</span>
                </span>
                {t.default && <span className="chip">default</span>}
              </label>
            ))}
          </div>
          <div><button className="btn btn-primary" disabled={track === null} onClick={() => track !== null && void api.chooseAudioTrack(id, track).then(() => { void refresh(); listen() }).catch((e: Error) => setActionError(e.message))}>Continue with this track</button></div>
        </section>
      )}

      {project.status === 'error' && project.error && project.error.code !== 'choose_audio_track' && (
        <div className="alert" role="alert">
          <strong>{project.error.message}</strong>
          {project.error.action && <p style={{ margin: '4px 0 0' }}>{project.error.action}</p>}
          <button className="btn" style={{ marginTop: 8 }} onClick={() => api.resume(id).then(() => { void refresh(); listen() })}>
            Retry
          </button>
        </div>
      )}

      <section aria-label="Progress" style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 22, padding: 28, display: 'flex', flexDirection: 'column', gap: 26 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 28, flexWrap: 'wrap' }}>
          <div style={{ position: 'relative', width: 132, height: 132, flex: 'none' }} role="progressbar" aria-valuenow={Math.round(overall * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Overall progress">
            <svg width="132" height="132" viewBox="0 0 132 132" aria-hidden style={{ display: 'block', transform: 'rotate(-90deg)' }}>
              <circle cx="66" cy="66" r={R} fill="none" stroke="var(--surface-2)" strokeWidth="10" />
              <circle cx="66" cy="66" r={R} fill="none" stroke={ringColour} strokeWidth="10" strokeLinecap="round" strokeDasharray={C} strokeDashoffset={C * (1 - overall)} style={{ transition: 'stroke-dashoffset 400ms ease-out' }} />
            </svg>
            <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', fontSize: 28, fontWeight: 650, letterSpacing: '-0.02em' }}>{pctText}</div>
          </div>
          <div style={{ flex: '1 1 260px', minWidth: 0 }}>
            <div className="muted" style={{ fontSize: 12, textTransform: 'uppercase', letterSpacing: '0.08em' }}>{finished ? 'Done' : running ? 'Now processing' : 'Status'}</div>
            <h2 style={{ fontSize: 26, margin: '4px 0 6px', lineHeight: 1.2 }}>{headline}</h2>
            <p className="muted" style={{ margin: 0, overflowWrap: 'anywhere' }}>{sub}</p>
            {project.source?.title && <p style={{ margin: '10px 0 0', fontSize: 13, overflowWrap: 'anywhere', color: 'var(--muted)' }}>{project.source.title}</p>}
          </div>
        </div>

        <ol style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          {visible.map((s, i) => {
            const state = states[i]
            const st = steps[s.key]
            const tone = state === 'done' ? 'var(--ok)' : state === 'active' ? 'var(--accent)' : state === 'error' ? 'var(--danger)' : 'var(--muted)'
            return (
              <li key={s.key} data-state={state} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px', borderRadius: 999, border: `1px solid ${state === 'idle' ? 'var(--border)' : tone}`, background: state === 'active' ? 'var(--surface-2)' : 'transparent', fontSize: 13, color: state === 'idle' ? 'var(--muted)' : 'var(--text)' }}>
                <span aria-hidden style={{ width: 18, height: 18, borderRadius: 99, display: 'grid', placeItems: 'center', flex: 'none', fontSize: 11, fontWeight: 700, background: state === 'idle' ? 'transparent' : tone, border: `1.5px solid ${tone}`, color: state === 'idle' ? tone : 'var(--bg)' }}>
                  {state === 'done' ? '✓' : state === 'error' ? '!' : i + 1}
                </span>
                <span>{s.label}</span>
                {stepCost(s.key) > 0 && <span title={breakdown(s.key)} style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent)' }}>{fmtUsd(stepCost(s.key))}</span>}
                {st?.done && <span className="muted" style={{ fontSize: 12 }}>{st.cached ? 'cached' : `${(st.seconds ?? 0).toFixed(1)} s`}</span>}
              </li>
            )
          })}
        </ol>

        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {running && <button className="btn btn-danger" onClick={() => api.cancel(id).then(refresh)}>Cancel</button>}
          {!finished && (project.status === 'cancelled' || project.status === 'idle') && (
            <button className="btn btn-primary" onClick={() => api.resume(id).then(() => { void refresh(); listen() })}>Resume</button>
          )}
          <button className="btn" onClick={() => setShowLogs((v) => !v)} aria-expanded={showLogs}>{showLogs ? 'Hide logs' : 'Logs'}</button>
        </div>
        {showLogs && <pre className="logs" style={{ margin: 0 }}>{logs.join('\n') || 'No events yet.'}</pre>}
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

      {project.curation_error && clips.length === 0 && (
        <div className="alert" role="alert" data-testid="curation-error">
          <strong>{characterMode ? 'The scan found nothing' : 'Clip selection did not run'}: {project.curation_error.message}</strong>
          {project.curation_error.action && <p style={{ margin: '4px 0 0' }}>{project.curation_error.action}</p>}
          {!characterMode && (
            <button className="btn" style={{ marginTop: 8 }} onClick={() => recurate({ mode: 'fresh' })}>
              Retry clip selection
            </button>
          )}
        </div>
      )}

      {project.status === 'done' && clips.length === 0 && !project.curation_error && (project.transcript?.words ?? 0) > 0 && (
        <div className="warn" role="status" data-testid="no-clips">
          <strong>No clips were proposed.</strong> The AI found no self-contained moment of 20–90 seconds in this source.
          Short sources, or ones that are mostly music or small talk, often have none. Try a longer video, or re-curate with a
          steering note such as "find the most useful tip".
        </div>
      )}

      {(cost || clips.length > 0) && <CostMeter cost={cost} />}

      {clips.length > 0 && (
        <section aria-label="Clips" className="grid gap-3">
          <h3 style={{ fontSize: 16 }} data-testid="clips-ready">{clips.length} {characterMode ? (clips.length === 1 ? 'edit' : 'edits') : 'clips'} proposed</h3>
          {clips.map((c) => (
            <ClipCard
              key={c.id}
              clip={c}
              characterMode={characterMode}
              onOpen={(cid) => { window.location.hash = `#/p/${id}/c/${cid}` }}
              onDecide={(cid, status) =>
                void (characterMode
                  ? api.setClipStatus(id, cid, status)
                  : api.editor(id, cid).then((d) => api.saveEdits(id, cid, { ...d.edits, status }))
                ).then(refresh)
              }
              onMoreLike={(cid) => recurate({ mode: 'more_like', reference_clip_id: cid })}
            />
          ))}
          {!characterMode && (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button className="btn" onClick={() => recurate({ mode: 'shorter' })}>Shorter</button>
              <button className="btn" onClick={() => recurate({ mode: 'different_topic' })}>Different topic</button>
              <input className="field" style={{ flex: 1, minWidth: 200 }} placeholder="Steer: e.g. find the funniest moments" aria-label="Steering text" value={steer} onChange={(e) => setSteer(e.target.value)} />
              <button className="btn" disabled={!steer.trim()} onClick={() => recurate({ mode: 'fresh', steering: steer })}>Re-curate</button>
            </div>
          )}
        </section>
      )}

      {project.transcript && (
        <section className="card" aria-label="Transcript">
          <h3 style={{ fontSize: 15, marginBottom: 6 }} data-testid="transcript-ready">
            {project.transcript.words === 0 ? 'No speech detected' : 'Transcript ready'}
          </h3>
          {(project.transcript.skipped?.length ?? 0) > 0 && (
            <p className="warn" role="status" data-testid="skipped-notice">
              This video mixes languages. Only the English parts were transcribed, and clips come only from those.
              Skipped: {project.transcript.skipped!.map((x) => `${fmtDuration(x.start)}–${fmtDuration(x.end)} (${x.language})`).join(', ')}.
              To transcribe everything, set LANGUAGE_POLICY=auto in Settings and import again.
            </p>
          )}
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
