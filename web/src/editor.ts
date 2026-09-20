// Pure helpers for the clip editor: source/output time mapping through the EDL, word lookup, and text-based cuts.

export interface Segment { src_in: number; src_out: number; out_in: number }
export interface EditorWord { i: number; w: string; start: number; end: number; kept: boolean; speaker: string | null }

/** Output time of a source time, or null when that instant was cut out. */
export function srcToOut(segments: Segment[], t: number): number | null {
  for (const s of segments) {
    if (t >= s.src_in - 1e-6 && t <= s.src_out + 1e-6) return s.out_in + Math.min(Math.max(t - s.src_in, 0), s.src_out - s.src_in)
  }
  return null
}

/** Source time of an output time (clamped to the timeline). */
export function outToSrc(segments: Segment[], t: number): number {
  if (!segments.length) return 0
  for (let k = segments.length - 1; k >= 0; k--) {
    if (t >= segments[k].out_in - 1e-6) return segments[k].src_in + Math.min(Math.max(t - segments[k].out_in, 0), segments[k].src_out - segments[k].src_in)
  }
  return segments[0].src_in
}

/** The kept word being spoken at output time `t`, or the nearest previous one. */
export function activeWord(words: EditorWord[], segments: Segment[], t: number): number {
  const src = outToSrc(segments, t)
  let best = -1
  for (let k = 0; k < words.length; k++) {
    if (!words[k].kept) continue
    if (words[k].start <= src + 1e-6) best = k
    else break
  }
  return best
}

export type Range = [number, number]

/** Add a word range to the exclusion list, merging overlaps and adjacency. Ranges are inclusive word indices. */
export function addExclude(ranges: Range[], lo: number, hi: number): Range[] {
  const all = [...ranges, [Math.min(lo, hi), Math.max(lo, hi)] as Range].sort((a, b) => a[0] - b[0])
  const out: Range[] = []
  for (const r of all) {
    const last = out[out.length - 1]
    if (last && r[0] <= last[1] + 1) last[1] = Math.max(last[1], r[1])
    else out.push([r[0], r[1]])
  }
  return out
}

/** Remove a word range from the exclusion list (splitting ranges that partly overlap it). */
export function removeExclude(ranges: Range[], lo: number, hi: number): Range[] {
  const out: Range[] = []
  for (const [a, b] of ranges) {
    if (b < lo || a > hi) { out.push([a, b]); continue }
    if (a < lo) out.push([a, lo - 1])
    if (b > hi) out.push([hi + 1, b])
  }
  return out
}

export const LAYOUT_CHOICES = [
  { value: 'auto', label: 'Auto' },
  { value: 'single', label: 'Speaker' },
  { value: 'two', label: 'Two-shot' },
  { value: 'stacked', label: 'Split' },
  { value: 'fit_blur', label: 'Fit' },
  { value: 'screen_cam', label: 'Screen + cam' },
] as const

export function fmtClock(t: number): string {
  const m = Math.floor(t / 60)
  const s = t - m * 60
  return `${m}:${s.toFixed(1).padStart(4, '0')}`
}
