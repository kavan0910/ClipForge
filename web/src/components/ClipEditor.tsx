import { Player, type PlayerRef } from '@remotion/player'
import { useCallback, useEffect, useRef, useState } from 'react'
import { CaptionsLayer } from '@captions/Captions'
import type { CaptionProps, Template, Timeline } from '@captions/types'
import { PublishPanel } from './PublishPanel'
import { api, type ClipEdits, type EditorData } from '../api'
import { LAYOUT_CHOICES, activeWord, addExclude, fmtClock, removeExclude, srcToOut, type Range } from '../editor'

const FPS = 30
const SPEEDS = [0.5, 1, 1.5, 2]

export function ClipEditor({ projectId, clipId, onBack }: { projectId: string; clipId: string; onBack: () => void }) {
  const [data, setData] = useState<EditorData | null>(null)
  const [edits, setEdits] = useState<ClipEdits | null>(null)
  const [error, setError] = useState('')
  const [sel, setSel] = useState<[number, number] | null>(null)
  const [time, setTime] = useState(0)
  const [speed, setSpeed] = useState(1)
  const [stale, setStale] = useState(false)
  const [starting, setStarting] = useState(false)
  const [saved, setSaved] = useState<'idle' | 'saving' | 'saved'>('idle')
  const [cap, setCap] = useState<{ timeline: Timeline; template: Template } | null>(null)
  const video = useRef<HTMLVideoElement>(null)
  const player = useRef<PlayerRef>(null)
  const dirty = useRef(false)

  const load = useCallback(async () => {
    try {
      const d = await api.editor(projectId, clipId)
      setData(d)
      setEdits((cur) => (dirty.current && cur ? cur : d.edits))
    } catch (e) {
      setError((e as Error).message)
    }
  }, [projectId, clipId])
  useEffect(() => { void load() }, [load])

  // Debounced autosave; the server recomputes the EDL, so the transcript pane always shows what will be rendered.
  useEffect(() => {
    if (!edits || !dirty.current) return
    setSaved('saving')
    const h = setTimeout(() => {
      api.saveEdits(projectId, clipId, edits).then(() => { dirty.current = false; setSaved('saved'); setStale(true); return load() }).catch((e: Error) => setError(e.message))
    }, 500)
    return () => clearTimeout(h)
  }, [edits, projectId, clipId, load])

  const change = (patch: Partial<ClipEdits>) => {
    dirty.current = true
    setEdits((e) => (e ? { ...e, ...patch } : e))
  }

  // Live caption preview (same React composition as the export). Reloaded when the template or text changes.
  useEffect(() => {
    if (!edits) return
    api.timeline(projectId, clipId, edits.template ?? undefined).then((r) => setCap(r as { timeline: Timeline; template: Template })).catch(() => setCap(null))
  }, [projectId, clipId, edits?.template, data?.duration, edits?.hook, edits?.hook_enabled]) // eslint-disable-line react-hooks/exhaustive-deps

  // Poll while a render runs.
  const busy = starting || data?.render.state === 'running'
  useEffect(() => {
    if (!busy) return
    const h = setInterval(() => void api.editor(projectId, clipId).then((d) => {
      setData(d)
      if (d.render.state !== 'idle') setStarting(false)
      if (d.render.state === 'done') setStale(false)
    }), 1000)
    return () => clearInterval(h)
  }, [busy, projectId, clipId])

  // Keep the caption overlay on the video's clock.
  useEffect(() => {
    let raf = 0
    const tick = () => {
      const t = video.current?.currentTime ?? 0
      setTime(t)
      player.current?.seekTo(Math.max(0, Math.round(t * FPS)))
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [])

  const seekOut = (t: number) => { if (video.current) video.current.currentTime = Math.max(0, t) }
  const active = data ? activeWord(data.words, data.segments, time) : -1

  const clickWord = (k: number, shift: boolean) => {
    if (!data) return
    setSel((s) => (shift && s ? [s[0], k] : [k, k]))
    const out = srcToOut(data.segments, data.words[k].start)
    if (out !== null) seekOut(out)
  }
  const selRange = (): [number, number] | null => {
    if (!data || !sel) return null
    const a = Math.min(sel[0], sel[1]), b = Math.max(sel[0], sel[1])
    return [data.words[a].i, data.words[b].i]
  }
  const cutSelection = () => { const r = selRange(); if (r && edits) { change({ exclude: addExclude(edits.exclude as Range[], r[0], r[1]) }); setSel(null) } }
  const restoreSelection = () => { const r = selRange(); if (r && edits) { change({ exclude: removeExclude(edits.exclude as Range[], r[0], r[1]) }); setSel(null) } }
  const setIn = () => { const k = sel ? Math.min(sel[0], sel[1]) : active; if (data && k >= 0) change({ start_word: data.words[k].i }) }
  const setOut = () => { const k = sel ? Math.max(sel[0], sel[1]) : active; if (data && k >= 0) change({ end_word: data.words[k].i }) }
  const step = (dir: 1 | -1) => {
    if (!data) return
    let k = (sel ? sel[1] : active) + dir
    while (k >= 0 && k < data.words.length && !data.words[k].kept) k += dir
    if (k >= 0 && k < data.words.length) clickWord(k, false)
  }
  const decide = (status: 'approved' | 'rejected') => change({ status })
  const doRender = () => { setStarting(true); void api.render(projectId, clipId, { template: edits?.template ?? undefined, brand: edits?.brand ?? undefined }).catch((e: Error) => { setStarting(false); setError(e.message) }) }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t.closest('input, textarea, select') || e.metaKey || e.ctrlKey) return
      const v = video.current
      const map: Record<string, () => void> = {
        ' ': () => { if (v) void (v.paused ? v.play() : v.pause()) },
        k: () => v?.pause(),
        j: () => { if (v) { v.currentTime = Math.max(0, v.currentTime - 3); v.pause() } },
        l: () => { if (v) { const i = SPEEDS.indexOf(speed); const s = SPEEDS[Math.min(i + 1, SPEEDS.length - 1)]; setSpeed(s); v.playbackRate = s; void v.play() } },
        '[': setIn, ']': setOut,
        a: () => decide('approved'), r: () => decide('rejected'),
        x: cutSelection, u: restoreSelection,
        ArrowRight: () => step(1), ArrowLeft: () => step(-1),
      }
      const fn = map[e.key.length === 1 ? e.key.toLowerCase() : e.key]
      if (fn) { e.preventDefault(); fn() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (error) return <div className="alert" role="alert"><strong>{error}</strong><div><button className="btn" onClick={onBack}>Back</button></div></div>
  if (!data || !edits) return <p className="muted">Loading the editor…</p>
  const c = data.clip
  const hasVideo = data.files['base_preview.mp4']
  const ctx = data.words
  const lo = data.first_word, hi = data.last_word
  const startIdx = edits.start_word ?? lo, endIdx = edits.end_word ?? hi
  const capProps: CaptionProps | null = cap ? { timeline: cap.timeline, template: cap.template, fontBase: '/fonts', layer: 'all' } : null
  const restoredAuto = data.removed.filter((r) => r.restored)
  const autoRemovals = data.removed.filter((r) => r.kind !== 'manual' && !r.restored)
  const manualRanges = edits.exclude

  return (
    <div className="grid gap-4" data-testid="clip-editor">
      <header style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn" onClick={onBack}>← Clips</button>
        <input className="field" style={{ flex: 1, minWidth: 220 }} aria-label="Title" value={edits.title ?? c.title} onChange={(e) => change({ title: e.target.value })} maxLength={60} />
        <span className="muted" aria-live="polite">{saved === 'saving' ? 'Saving…' : saved === 'saved' ? 'Saved' : ''}</span>
        <button className="btn" aria-pressed={c.status === 'approved'} onClick={() => decide('approved')} data-testid="approve">Approve <kbd>A</kbd></button>
        <button className="btn btn-danger" aria-pressed={c.status === 'rejected'} onClick={() => decide('rejected')} data-testid="reject">Reject <kbd>R</kbd></button>
        <span className="warn" data-testid="clip-status" style={{ padding: '2px 10px' }}>{c.status}</span>
      </header>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,360px) minmax(0,1fr)', gap: 20 }} className="editor-cols">
        <section aria-label="Preview">
          <div style={{ position: 'relative', width: 360, height: 640, background: '#000', borderRadius: 12, overflow: 'hidden' }}>
            {hasVideo ? (
              <>
                <video ref={video} src={`/api/files/${projectId}/clips/${clipId}/base_preview.mp4?v=${data.render.state}${stale ? 's' : ''}`} style={{ width: 360, height: 640 }} playsInline data-testid="preview-video" />
                {capProps && (
                  <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', opacity: stale ? 0.5 : 1 }} aria-hidden>
                    <Player ref={player} component={CaptionsLayer as never} inputProps={capProps as never} durationInFrames={Math.max(1, Math.ceil(cap!.timeline.duration * FPS))} compositionWidth={1080} compositionHeight={1920} fps={FPS} controls={false} style={{ width: 360, height: 640 }} acknowledgeRemotionLicense />
                  </div>
                )}
              </>
            ) : (
              <div style={{ display: 'grid', placeItems: 'center', height: '100%', padding: 24, textAlign: 'center', color: '#9aa2af' }}>
                Nothing to preview yet. Render this clip once and the preview appears here.
              </div>
            )}
          </div>
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            <span className="mono" data-testid="clock">{fmtClock(time)} / {fmtClock(data.duration)}</span>
            <span className="muted" style={{ fontSize: 12 }}>Space play · J back · K pause · L faster · ←/→ word</span>
          </div>
          {stale && <p className="warn" role="status" style={{ marginTop: 8 }}>Your edits are saved. Render again to refresh the video preview.</p>}
          <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
            <button className="btn btn-primary" onClick={doRender} disabled={busy} data-testid="render">
              {busy ? `Rendering ${Math.round(data.render.pct * 100)}%` : 'Render clip'}
            </button>
            {busy && <button className="btn" onClick={() => void api.cancelRender(projectId, clipId).then(load)}>Cancel</button>}
          </div>
          {data.render.state === 'error' && <p className="alert" role="alert">{data.render.error?.message} {data.render.error?.action}</p>}
        </section>

        <div className="grid gap-4">
          <section className="card" aria-label="Trim">
            <h3 style={{ fontSize: 15 }}>Trim</h3>
            <p className="muted" style={{ margin: '4px 0 8px', fontSize: 13 }}>Cuts land in the pauses around whole words. Use [ and ] to set the start and end at the selected word.</p>
            <label className="muted" style={{ fontSize: 13 }}>Start: {fmtClock(ctx.find((w) => w.i === startIdx)?.start ?? 0)}</label>
            <input type="range" aria-label="Clip start word" min={ctx[0].i} max={ctx[ctx.length - 1].i} value={startIdx} onChange={(e) => change({ start_word: Math.min(Number(e.target.value), endIdx - 3) })} style={{ width: '100%' }} />
            <label className="muted" style={{ fontSize: 13 }}>End: {fmtClock(ctx.find((w) => w.i === endIdx)?.end ?? 0)}</label>
            <input type="range" aria-label="Clip end word" min={ctx[0].i} max={ctx[ctx.length - 1].i} value={endIdx} onChange={(e) => change({ end_word: Math.max(Number(e.target.value), startIdx + 3) })} style={{ width: '100%' }} />
            <p className="muted" style={{ margin: '6px 0 0', fontSize: 13 }} data-testid="duration">Length {data.duration.toFixed(1)} s</p>
          </section>

          <section className="card" aria-label="Transcript">
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
              <h3 style={{ fontSize: 15, flex: 1 }}>Transcript</h3>
              <button className="btn" onClick={cutSelection} disabled={!sel} data-testid="cut">Cut selection <kbd>X</kbd></button>
              <button className="btn" onClick={restoreSelection} disabled={!sel}>Put back <kbd>U</kbd></button>
            </div>
            <div style={{ maxHeight: 280, overflow: 'auto', lineHeight: 2 }} data-testid="transcript">
              {ctx.map((w, k) => {
                const inSel = sel && k >= Math.min(sel[0], sel[1]) && k <= Math.max(sel[0], sel[1])
                const outside = w.i < startIdx || w.i > endIdx
                return (
                  <button key={w.i} className="word" data-active={k === active} data-cut={!w.kept && !outside} data-outside={outside} data-selected={!!inSel} onClick={(e) => clickWord(k, e.shiftKey)} aria-label={`${w.w} at ${fmtClock(w.start)}${!w.kept && !outside ? ', cut' : ''}`}>
                    {w.w}
                  </button>
                )
              })}
            </div>
          </section>

          <section className="card" aria-label="Removed">
            <h3 style={{ fontSize: 15 }}>Removed from this clip</h3>
            {autoRemovals.length + manualRanges.length + restoredAuto.length === 0 && <p className="muted" style={{ margin: '6px 0 0' }}>Nothing was removed. Fillers and long pauses are removed by the cleanup level below.</p>}
            <ul style={{ listStyle: 'none', padding: 0, margin: '8px 0 0', display: 'grid', gap: 6 }}>
              {autoRemovals.map((r) => (
                <li key={r.id} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span className="warn" style={{ padding: '1px 8px' }}>{r.kind}</span>
                  <span style={{ flex: 1 }}>{r.text || `${(r.src_out - r.src_in).toFixed(1)} s`}</span>
                  <button className="btn" onClick={() => change({ restored: [...edits.restored, r.src_in] })}>Restore</button>
                </li>
              ))}
              {manualRanges.map(([a, b]) => (
                <li key={`m${a}`} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span className="warn" style={{ padding: '1px 8px' }}>cut</span>
                  <span style={{ flex: 1 }}>{data.words.filter((w) => w.i >= a && w.i <= b).map((w) => w.w).join(' ')}</span>
                  <button className="btn" onClick={() => change({ exclude: removeExclude(manualRanges as Range[], a, b) })}>Restore</button>
                </li>
              ))}
            </ul>
            <label className="muted" style={{ display: 'block', marginTop: 10, fontSize: 13 }}>Cleanup level
              <select className="field" value={edits.cleanup} onChange={(e) => change({ cleanup: e.target.value as ClipEdits['cleanup'], restored: [] })} style={{ marginTop: 4 }}>
                <option value="off">Off</option><option value="light">Light (fillers, long pauses)</option><option value="aggressive">Aggressive (also repeats, false starts)</option>
              </select>
            </label>
          </section>

          <section className="card" aria-label="Layout">
            <h3 style={{ fontSize: 15 }}>Layout per segment</h3>
            {data.layouts.length === 0 ? <p className="muted" style={{ margin: '6px 0 0' }}>Render once to plan the layouts, then override any segment here.</p> : (
              <ul style={{ listStyle: 'none', padding: 0, margin: '8px 0 0', display: 'grid', gap: 6 }}>
                {data.layouts.map((s) => {
                  const ov = edits.layouts.find((o) => Math.abs(o.t0 - s.t0) < 0.01)
                  return (
                    <li key={s.t0} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <span className="mono" style={{ width: 120 }}>{fmtClock(s.t0)}–{fmtClock(s.t1)}</span>
                      <span className="muted" style={{ flex: 1 }}>planned: {s.layout.replace('_', ' ')}</span>
                      <select aria-label={`Layout for ${fmtClock(s.t0)}`} className="field" style={{ width: 150 }} value={ov?.layout ?? 'auto'} onChange={(e) => change({ layouts: [...edits.layouts.filter((o) => Math.abs(o.t0 - s.t0) >= 0.01), ...(e.target.value === 'auto' ? [] : [{ t0: s.t0, t1: s.t1, layout: e.target.value }])] })}>
                        {LAYOUT_CHOICES.map((l) => <option key={l.value} value={l.value}>{l.label}</option>)}
                      </select>
                    </li>
                  )
                })}
              </ul>
            )}
          </section>

          <PublishPanel projectId={projectId} clipId={clipId} rendered={!!data.files['out.mp4']} />

          <section className="card" aria-label="Captions">
            <h3 style={{ fontSize: 15 }}>Captions</h3>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', margin: '8px 0' }} role="radiogroup" aria-label="Caption template">
              {data.templates.map((t) => (
                <button key={t} className="btn" role="radio" aria-checked={(edits.template ?? 'karaoke-pop') === t} data-testid={`tpl-${t}`} onClick={() => change({ template: t })}>{t.replace('-', ' ')}</button>
              ))}
            </div>
            <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}><input type="checkbox" checked={edits.hook_enabled} onChange={(e) => change({ hook_enabled: e.target.checked })} /> Show the hook at the start</label>
            <input className="field" style={{ marginTop: 8 }} aria-label="Hook text" value={edits.hook ?? c.hook} onChange={(e) => change({ hook: e.target.value })} />
            <textarea className="field" style={{ marginTop: 8 }} rows={2} aria-label="Description" value={edits.description ?? c.description} onChange={(e) => change({ description: e.target.value })} />
            <input className="field" style={{ marginTop: 8 }} aria-label="Hashtags" value={(edits.hashtags ?? c.hashtags).join(', ')} onChange={(e) => change({ hashtags: e.target.value.split(',').map((h) => h.trim().replace(/^#/, '')).filter(Boolean) })} />
            {data.brand_kits.length > 0 && (
              <label className="muted" style={{ display: 'block', marginTop: 8, fontSize: 13 }}>Brand kit
                <select className="field" value={edits.brand ?? ''} onChange={(e) => change({ brand: e.target.value || null })} style={{ marginTop: 4 }}>
                  <option value="">None</option>{data.brand_kits.map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              </label>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}
