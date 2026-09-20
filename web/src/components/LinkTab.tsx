import { useEffect, useState } from 'react'
import { api, ApiError, type UrlPreview } from '../api'
import { fmtDuration, fmtUploadDate } from '../format'

export function LinkTab({ onStart }: { onStart: (id: string) => void }) {
  const [url, setUrl] = useState('')
  const [preview, setPreview] = useState<UrlPreview | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const [starting, setStarting] = useState(false)
  const [updated, setUpdated] = useState('')

  useEffect(() => {
    if (!/^https?:\/\/\S+/i.test(url.trim())) return
    let stale = false
    const handle = setTimeout(() => {
      setBusy(true)
      api
        .resolve(url.trim())
        .then((p) => !stale && setPreview(p))
        .catch((e: ApiError) => !stale && setError(e))
        .finally(() => !stale && setBusy(false))
    }, 350)
    return () => {
      stale = true
      clearTimeout(handle)
    }
  }, [url])

  function onUrlChange(value: string) {
    setUrl(value)
    setPreview(null)
    setError(null)
    setBusy(false)
  }

  async function start() {
    setStarting(true)
    try {
      const r = await api.createProject({ type: 'url', url: url.trim() })
      onStart(r.project_id)
    } catch (e) {
      setError(e as ApiError)
      setStarting(false)
    }
  }

  const softer = preview && preview.will_download_height !== null && preview.will_download_height < 1440
  const canUpdate = error?.action.toLowerCase().includes('update yt-dlp')

  return (
    <div role="tabpanel" aria-labelledby="tab-link" className="grid gap-4">
      <label htmlFor="url" className="sr-only">Video link</label>
      <input
        id="url"
        className="field"
        placeholder="Paste a video link (YouTube or any site yt-dlp supports)"
        value={url}
        onChange={(e) => onUrlChange(e.target.value)}
        autoComplete="off"
        spellCheck={false}
      />
      <p className="notice">Only process videos you own or have permission to reuse.</p>
      <div aria-live="polite">
        {busy && <p className="muted"><span className="spinner" /> Reading video details…</p>}
        {error && (
          <div className="alert" role="alert">
            <strong>{error.message}</strong>
            {error.action && <p style={{ margin: '4px 0 0' }}>{error.action}</p>}
            {canUpdate && (
              <button
                className="btn"
                style={{ marginTop: 8 }}
                onClick={() => api.updateYtdlp().then((r) => setUpdated(r.note)).catch((e: ApiError) => setUpdated(e.message))}
              >
                Update yt-dlp
              </button>
            )}
            {updated && <p className="muted">{updated}</p>}
          </div>
        )}
      </div>
      {preview && (
        <article className="card" style={{ display: 'grid', gridTemplateColumns: 'minmax(0,180px) 1fr', gap: 16 }}>
          {preview.thumbnail && (
            <img src={preview.thumbnail} alt="" style={{ width: '100%', borderRadius: 10, aspectRatio: '16/9', objectFit: 'cover' }} />
          )}
          <div style={{ minWidth: 0 }}>
            <h2 style={{ fontSize: 17 }}>{preview.title}</h2>
            <p className="muted" style={{ margin: '4px 0' }}>
              {[preview.channel, fmtDuration(preview.duration), fmtUploadDate(preview.upload_date)].filter(Boolean).join(' · ')}
            </p>
            <p className="muted" style={{ margin: '4px 0' }}>
              Available up to {preview.heights[0] ?? '?'}p · will download {preview.will_download_height ?? '?'}p
            </p>
            {preview.note && <p className="warn">{preview.note}</p>}
            {softer && (
              <p className="warn" style={{ marginTop: 8 }}>
                A vertical crop of a {preview.will_download_height}p frame is only about{' '}
                {Math.round(((preview.will_download_height ?? 0) * 9) / 16)} px wide, so clips will be softer. Set MAX_SOURCE_HEIGHT to 2160 in Settings to download 4K when the video has it.
              </p>
            )}
          </div>
        </article>
      )}
      <div>
        <button className="btn btn-primary" disabled={!preview || starting} onClick={start}>
          {starting ? 'Starting…' : 'Fetch and transcribe'}
        </button>
      </div>
    </div>
  )
}
