export function fmtDuration(s: number | null | undefined): string {
  if (s == null) return '–'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`
}

export function fmtBytes(n: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function fmtEta(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s)) return ''
  return s >= 60 ? `${Math.floor(s / 60)}m ${Math.round(s % 60)}s left` : `${Math.round(s)}s left`
}

export function fmtUploadDate(d: string | null): string {
  return d && d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : (d ?? '')
}
