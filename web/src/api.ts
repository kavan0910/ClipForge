const metaToken = document.querySelector<HTMLMetaElement>('meta[name="clipforge-token"]')?.content
const TOKEN = metaToken ?? import.meta.env.VITE_CLIPFORGE_TOKEN ?? ''

export class ApiError extends Error {
  action: string
  status: number
  offset?: number
  constructor(message: string, action = '', status = 0, offset?: number) {
    super(message)
    this.action = action
    this.status = status
    this.offset = offset
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('x-clipforge-token', TOKEN)
  if (init.body && typeof init.body === 'string') headers.set('content-type', 'application/json')
  let res: Response
  try {
    res = await fetch(path, { ...init, headers })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') throw e
    throw new ApiError('Cannot reach the Clipforge server.', 'Is `make start` still running?')
  }
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    throw new ApiError(body.message ?? body.detail ?? `Request failed (${res.status})`, body.action ?? '', res.status, body.offset)
  }
  return body as T
}

export interface UrlPreview {
  url: string
  title: string
  channel: string | null
  duration: number | null
  upload_date: string | null
  thumbnail: string | null
  heights: number[]
  note: string | null
  will_download_height: number | null
  max_source_height: number
}

export interface Quality {
  resolution: string | null
  fps: number | null
  video_bitrate_kbps: number | null
  audio_summary: string
  warnings: string[]
}

export interface SentenceRow {
  id: string
  start: number
  end: number
  text: string
  speaker: string | null
}

export interface ProjectData {
  id: string
  status: 'idle' | 'running' | 'done' | 'error' | 'cancelled'
  source?: { title: string; kind: string; quality: Quality; probe: { duration: number; video: unknown | null } }
  transcript?: { language: string; asr_model: string; duration: number; words: number; sentences: SentenceRow[]; diarized: boolean }
  error?: { code: string; message: string; action: string }
  curation_error?: { code: string; message: string; action: string }
}

export interface ClipRow {
  id: string
  start: number
  end: number
  duration: number
  title: string
  hook: string
  summary: string
  why_it_works: string
  scores: Record<string, number>
  overall: number
  rank_score: number
  hashtags: string[]
  risk_flags: string[]
  hook_check: { passed: boolean; missing: string[] }
  status: 'proposed' | 'approved' | 'rejected'
  transcript: string
}

export interface CostState {
  usd: number
  cap_usd: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
}

export interface Removed { id: string; kind: string; src_in: number; src_out: number; text: string; restored: boolean }
export interface ClipEdits {
  title?: string | null; hook?: string | null; description?: string | null; hashtags?: string[] | null
  start_word?: number | null; end_word?: number | null; exclude: [number, number][]; cleanup: 'off' | 'light' | 'aggressive'
  restored: number[]; template?: string | null; brand?: string | null; hook_enabled: boolean
  layouts: { t0: number; t1: number; layout: string }[]; status?: 'proposed' | 'approved' | 'rejected' | null
}
export interface RenderState { state: 'idle' | 'running' | 'done' | 'error' | 'cancelled'; pct: number; note?: string | null; error?: { message: string; action: string } }
export interface EditorData {
  clip: ClipRow & { description: string }
  edits: ClipEdits
  duration: number
  segments: { src_in: number; src_out: number; out_in: number }[]
  removed: Removed[]
  words: { i: number; w: string; start: number; end: number; kept: boolean; speaker: string | null }[]
  first_word: number
  last_word: number
  layouts: { t0: number; t1: number; layout: string }[]
  files: Record<string, boolean>
  render: RenderState
  templates: string[]
  brand_kits: string[]
}
export interface RenderRow { project: string; clip: string; title: string; duration: number; state: RenderState['state']; pct: number; note?: string | null; has_video: boolean; status: string; error?: { message: string; action: string } }
export interface SettingsData { anthropic_api_key: string | null; hf_token: string | null; google_client_secret: string | null; values: Record<string, string>; data_dir: string }
export interface DoctorCheck { name: string; status: 'ok' | 'warn' | 'fail'; detail: string; fix: string }
export interface StorageData { projects: { id: string; bytes: number; intermediate_bytes: number }[]; total_bytes: number; free_bytes: number }
export interface BrandKitData { id: string; name: string; text_color: string | null; accent_color: string | null; default_template: string; vocabulary: string[]; logo: unknown; intro: { title: string } | null; outro: { title: string } | null }
export interface YtJob { state: 'idle' | 'running' | 'done' | 'error'; pct: number; url?: string; scheduled_for?: string | null; warnings?: string[]; message?: string; action?: string }
export interface ProjectRow { id: string; title: string; status: string }

export type SourceSpec =
  | { type: 'url'; url: string }
  | { type: 'upload'; upload_id: string }
  | { type: 'path'; path: string }

export const api = {
  resolve: (url: string) => request<UrlPreview>('/api/sources/resolve', { method: 'POST', body: JSON.stringify({ url }) }),
  createProject: (source: SourceSpec) =>
    request<{ project_id: string }>('/api/projects', { method: 'POST', body: JSON.stringify({ source, options: {} }) }),
  project: (id: string) => request<ProjectData>(`/api/projects/${id}`),
  cancel: (id: string) => request<{ cancelled: boolean }>(`/api/projects/${id}/cancel`, { method: 'POST' }),
  resume: (id: string) => request<{ pid: number }>(`/api/projects/${id}/resume`, { method: 'POST' }),
  remove: (id: string) => request<{ ok: boolean }>(`/api/projects/${id}`, { method: 'DELETE' }),
  clips: (id: string) => request<{ clips: ClipRow[]; label: string }>(`/api/projects/${id}/clips`),
  recurate: (id: string, body: { mode: string; reference_clip_id?: string; steering?: string }) =>
    request<{ pid: number }>(`/api/projects/${id}/recurate`, { method: 'POST', body: JSON.stringify(body) }),
  projects: () => request<ProjectRow[]>('/api/projects'),
  editor: (id: string, cid: string) => request<EditorData>(`/api/projects/${id}/clips/${cid}/editor`),
  saveEdits: (id: string, cid: string, e: ClipEdits) => request<{ ok: boolean }>(`/api/projects/${id}/clips/${cid}/edits`, { method: 'PUT', body: JSON.stringify(e) }),
  timeline: (id: string, cid: string, template?: string) => request<{ timeline: unknown; template: unknown }>(`/api/projects/${id}/clips/${cid}/timeline${template ? `?template=${template}` : ''}`),
  render: (id: string, cid: string, body: Record<string, unknown> = {}) => request<{ pid: number }>(`/api/projects/${id}/clips/${cid}/render`, { method: 'POST', body: JSON.stringify(body) }),
  cancelRender: (id: string, cid: string) => request<{ cancelled: boolean }>(`/api/projects/${id}/clips/${cid}/render/cancel`, { method: 'POST' }),
  renders: () => request<RenderRow[]>('/api/renders'),
  exportClips: (clips: [string, string][], format: string) => request<{ path: string; name: string; warnings: string[]; is_zip: boolean }>('/api/export', { method: 'POST', body: JSON.stringify({ clips, format }) }),
  settings: () => request<SettingsData>('/api/settings'),
  saveSettings: (values: Record<string, string>) => request<{ ok: boolean }>('/api/settings', { method: 'PUT', body: JSON.stringify({ values }) }),
  saveSecrets: (body: { anthropic_api_key?: string; hf_token?: string; google_client_secret?: string }) => request<{ ok: boolean }>('/api/settings/secrets', { method: 'PUT', body: JSON.stringify(body) }),
  doctor: () => request<{ ok: boolean; checks: DoctorCheck[] }>('/api/doctor'),
  storage: () => request<StorageData>('/api/storage'),
  cleanup: (id: string) => request<{ freed_bytes: number }>(`/api/projects/${id}/cleanup`, { method: 'POST' }),
  brandKits: () => request<BrandKitData[]>('/api/brand'),
  saveBrand: (kit: Record<string, unknown>) => request<{ ok: boolean }>(`/api/brand/${kit.id as string}`, { method: 'PUT', body: JSON.stringify(kit) }),
  makePackage: (id: string, cid: string) => request<{ path: string }>(`/api/projects/${id}/clips/${cid}/package`, { method: 'POST' }),
  ytStatus: () => request<{ configured: boolean; connected: boolean; message?: string; action?: string }>('/api/publish/youtube/status'),
  ytConnect: () => request<{ connected: boolean }>('/api/publish/youtube/connect', { method: 'POST' }),
  ytDisconnect: () => request<{ connected: boolean }>('/api/publish/youtube/disconnect', { method: 'POST' }),
  ytPublish: (id: string, cid: string, body: { schedule?: string; privacy?: string }) => request<{ state: string }>(`/api/projects/${id}/clips/${cid}/publish/youtube`, { method: 'POST', body: JSON.stringify(body) }),
  ytJob: (id: string, cid: string) => request<YtJob>(`/api/projects/${id}/clips/${cid}/publish/youtube`),
  updateYtdlp: () => request<{ ok: boolean; note: string }>('/api/ytdlp/update', { method: 'POST' }),
}

const CHUNK = 8 * 1024 * 1024
const resumeKey = (f: File) => `clipforge-upload:${f.name}:${f.size}:${f.lastModified}`

/** Chunked, resumable upload. Returns the finished upload id. */
export async function uploadFile(
  file: File,
  onProgress: (sent: number, total: number) => void,
  signal: AbortSignal,
): Promise<string> {
  let id: string | null = null
  let offset = 0
  const saved = localStorage.getItem(resumeKey(file))
  if (saved) {
    try {
      const s = await request<{ id: string; offset: number; size: number }>(`/api/uploads/${saved}`)
      if (s.size === file.size) ({ id, offset } = { id: s.id, offset: s.offset })
    } catch {
      localStorage.removeItem(resumeKey(file))
    }
  }
  if (!id) {
    const created = await request<{ id: string }>('/api/uploads', {
      method: 'POST',
      body: JSON.stringify({ filename: file.name, size: file.size }),
    })
    id = created.id
    localStorage.setItem(resumeKey(file), id)
  }
  onProgress(offset, file.size)
  while (offset < file.size) {
    if (signal.aborted) throw new DOMException('aborted', 'AbortError')
    const end = Math.min(offset + CHUNK, file.size)
    try {
      const r = await request<{ offset: number }>(`/api/uploads/${id}`, {
        method: 'PATCH',
        headers: { 'upload-offset': String(offset) },
        body: file.slice(offset, end),
        signal,
      })
      offset = r.offset
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.offset !== undefined) offset = e.offset
      else throw e
    }
    onProgress(offset, file.size)
  }
  localStorage.removeItem(resumeKey(file))
  return id
}

export async function abortUpload(id: string) {
  await request(`/api/uploads/${id}`, { method: 'DELETE' }).catch(() => undefined)
}

export interface ProgressEvent {
  type: string
  stage?: string
  pct?: number | null
  bytes?: number | null
  total?: number | null
  speed?: number | null
  eta?: number | null
  seconds?: number
  cached?: boolean
  message?: string
  action?: string
  warnings?: string[]
  chunk?: number
  chunks?: number
  usd?: number
  cap_usd?: number
  input_tokens?: number
  output_tokens?: number
  cache_read_tokens?: number
  note?: string
  count?: number
}

export function subscribe(id: string, onEvent: (e: ProgressEvent) => void, onEnd: () => void): () => void {
  const es = new EventSource(`/api/projects/${id}/events`)
  const handler = (m: MessageEvent) => onEvent(JSON.parse(m.data))
  for (const t of ['job_started', 'progress', 'stage_done', 'probe', 'source_ready', 'warning', 'cost', 'clips_ready', 'stage_error', 'job_done', 'job_error', 'job_cancelled']) {
    es.addEventListener(t, handler as EventListener)
  }
  for (const t of ['job_done', 'job_error', 'job_cancelled']) {
    es.addEventListener(t, () => {
      es.close()
      onEnd()
    })
  }
  return () => es.close()
}
