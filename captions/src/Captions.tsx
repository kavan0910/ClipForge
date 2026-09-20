import type { CSSProperties } from 'react'
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from 'remotion'
import type { CaptionProps, Chunk, Font, Motion, Template } from './types'

const SAFE_LEFT = 0.06
const SAFE_RIGHT = 0.14

function ease(kind: Motion['easing'], u: number): number {
  const x = Math.min(Math.max(u, 0), 1)
  switch (kind) {
    case 'ease_out': return 1 - (1 - x) ** 3
    case 'ease_in_out': return x < 0.5 ? 4 * x ** 3 : 1 - (-2 * x + 2) ** 3 / 2
    case 'back_out': { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2 }
    default: return x
  }
}

export function fontFaces(props: CaptionProps): string {
  const files = new Map<string, Font>()
  const add = (f: Font | null | undefined) => f && files.set(f.file, f)
  add(props.template.font); add(props.template.hook.font)
  for (const c of props.timeline.chunks) add(c.font)
  return [...files.values()].map((f) => `@font-face{font-family:'${f.family}';src:url('${props.fontBase}/${f.file}');font-weight:100 900;font-style:${f.italic ? 'italic' : 'normal'};}`).join('\n')
}

function textShadow(t: Template, w: number): string {
  const s = t.shadow
  if (s.opacity <= 0) return 'none'
  const hex = s.color.replace('#', '')
  const rgba = `rgba(${parseInt(hex.slice(0, 2), 16)},${parseInt(hex.slice(2, 4), 16)},${parseInt(hex.slice(4, 6), 16)},${s.opacity})`
  const layers = [`${s.dx * w}px ${s.dy * w}px ${s.blur * w}px ${rgba}`]
  if (s.blur > 0.012) layers.push(`0 0 ${s.blur * w * 2}px ${rgba}`) // glow needs a second, wider pass
  return layers.join(',')
}

function rgba(color: string, opacity: number): string {
  const h = color.replace('#', '')
  return `rgba(${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)},${opacity})`
}

function chunkStyle(t: Template, c: Chunk, time: number, W: number, H: number, y0: number): CSSProperties {
  const m = t.motion
  const age = (time - c.start) * 1000
  let opacity = 1, scale = 1, dy = 0
  if (m.kind === 'pop') { const u = ease(m.easing, age / m.ms); scale = 0.82 + 0.18 * u; opacity = Math.min(1, age / 60) }
  if (m.kind === 'fade') opacity = ease(m.easing, age / Math.max(m.ms, 1))
  if (m.kind === 'slide_up') { const u = ease(m.easing, age / m.ms); dy = (1 - u) * 40; opacity = u }
  if (m.out_kind === 'fade' && m.out_ms > 0) { const left = (c.end - time) * 1000; if (left < m.out_ms) opacity *= Math.max(left, 0) / m.out_ms }
  const cx = W * (SAFE_LEFT + (1 - SAFE_LEFT - SAFE_RIGHT) / 2)
  return {
    position: 'absolute', left: cx, top: H * t.anchor.y - y0, transform: `translate(-50%,-50%) translateY(${dy}px) scale(${scale * 1})`,
    opacity, textAlign: 'center', whiteSpace: 'nowrap',
    ...(t.box ? { background: rgba(t.box.color, t.box.opacity), borderRadius: t.box.radius * W, padding: `${t.box.pad_y * W}px ${t.box.pad_x * W}px` } : {}),
  }
}

function ChunkView({ t, c, time, W, H, y0 }: { t: Template; c: Chunk; time: number; W: number; H: number; y0: number }) {
  const font = c.font ?? t.font
  const size = t.size * W * c.scale
  return (
    <div style={chunkStyle(t, c, time, W, H, y0)}>
      <div style={{ fontFamily: `'${font.family}', 'Noto Sans', sans-serif`, fontWeight: font.weight, fontStyle: font.italic ? 'italic' : 'normal', fontSize: size, lineHeight: t.line_height, letterSpacing: `${t.tracking}em`, color: t.fill, textShadow: textShadow(t, W), WebkitTextStroke: t.stroke.width > 0 ? `${t.stroke.width * W * 2}px ${t.stroke.color}` : undefined, paintOrder: 'stroke fill' as never, direction: c.script === 'arabic' ? 'rtl' : 'ltr' }}>
        {c.lines.map((line, li) => (
          <div key={li} style={{ display: 'flex', justifyContent: 'center', alignItems: 'baseline', gap: c.script === 'cjk' ? 0 : '0.32em' }}>
            {line.map((wi) => {
              const w = c.words[wi]
              const active = t.active.mode !== 'none' && time >= w.start && (wi === c.words.length - 1 ? time < c.end : time < c.words[wi + 1].start)
              const hidden = t.motion.kind === 'typewriter' && time < w.start
              const st: CSSProperties = { display: 'inline-block', opacity: hidden ? 0 : 1 }
              if (w.emphasis) { if (t.emphasis.color) st.color = t.emphasis.color; if (t.emphasis.weight) st.fontWeight = t.emphasis.weight; if (t.emphasis.scale !== 1) st.transform = `scale(${t.emphasis.scale})` }
              if (active) {
                const a = t.active
                if (a.mode === 'color') st.color = a.color
                if (a.mode === 'scale') { st.color = a.color; st.transform = `scale(${a.scale})`; st.transformOrigin = '50% 70%'; st.margin = `0 ${(a.scale - 1) * 0.5}em` }
                if (a.mode === 'underline') { st.color = a.color === t.fill ? a.underline_color : a.color; st.borderBottom = `${a.underline_height * W}px solid ${a.underline_color}` }
                if (a.mode === 'pill') { st.color = a.color; st.background = a.pill_color; st.borderRadius = a.pill_radius * W; st.padding = `0 ${a.pill_radius * W * 0.6}px`; st.WebkitTextStroke = '0' }
              }
              return <span key={wi} style={st}>{w.w}</span>
            })}
          </div>
        ))}
        {c.emoji && <div style={{ fontSize: size * 0.9 }}>{c.emoji}</div>}
      </div>
    </div>
  )
}

function HookView({ p, time, W, H, y0 }: { p: CaptionProps; time: number; W: number; H: number; y0: number }) {
  const h = p.timeline.hook
  const s = p.template.hook
  if (!h || time < h.start || time >= h.end) return null
  const font = s.font ?? p.template.font
  const fade = Math.min(1, (time - h.start) / 0.16, (h.end - time) / 0.16)
  return (
    <div style={{ position: 'absolute', left: W / 2, top: H * s.y - y0, transform: 'translate(-50%,-50%)', opacity: Math.max(fade, 0), textAlign: 'center', maxWidth: W * s.max_width,
      ...(s.box ? { background: rgba(s.box.color, s.box.opacity), borderRadius: s.box.radius * W, padding: `${s.box.pad_y * W}px ${s.box.pad_x * W}px` } : {}) }}>
      <div style={{ fontFamily: `'${font.family}', sans-serif`, fontWeight: font.weight, fontSize: s.size * W * h.scale, lineHeight: 1.12, color: s.fill, WebkitTextStroke: s.stroke.width > 0 ? `${s.stroke.width * W * 2}px ${s.stroke.color}` : undefined, paintOrder: 'stroke fill' as never, whiteSpace: 'nowrap' }}>
        {h.lines.map((l, i) => <div key={i}>{l}</div>)}
      </div>
    </div>
  )
}

export function CaptionsLayer(p: CaptionProps) {
  const frame = useCurrentFrame()
  const { fps, width: W } = useVideoConfig()
  const H = p.timeline.height // full output height; the composition may be a band of it
  const y0 = p.band?.y0 ?? 0
  const time = p.times ? p.times[Math.min(frame, p.times.length - 1)] : frame / fps // 'states' mode: frame k shows time times[k]
  const chunk = p.timeline.chunks.find((c) => time >= c.start && time < c.end)
  const layer = p.layer ?? 'all'
  return (
    <AbsoluteFill style={{ background: 'transparent' }}>
      <style>{fontFaces(p)}</style>
      {layer !== 'hook' && chunk && <ChunkView t={p.template} c={chunk} time={time} W={W} H={H} y0={y0} />}
      {layer !== 'captions' && <HookView p={p} time={time} W={W} H={H} y0={y0} />}
    </AbsoluteFill>
  )
}
