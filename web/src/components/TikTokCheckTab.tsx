import { useRef, useState } from 'react'
import { abortUpload, api, ApiError, uploadFile, type TikTokPreflight } from '../api'
import { fmtBytes } from '../format'

const ACCEPT = '.mp4,.mov,.mkv,.webm,.avi,.m4v'

export function TikTokCheckTab() {
  const [over, setOver] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [sent, setSent] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState('')
  const [report, setReport] = useState<TikTokPreflight | null>(null)
  const abort = useRef<AbortController | null>(null)
  const input = useRef<HTMLInputElement>(null)

  async function check(f: File) {
    setFile(f)
    setSent(0)
    setError('')
    setReport(null)
    setUploading(true)
    const controller = new AbortController()
    abort.current = controller
    let uploadId: string | null = null
    try {
      uploadId = await uploadFile(f, (n) => setSent(n), controller.signal)
      setUploading(false)
      setChecking(true)
      setReport(await api.tiktokPreflightUpload(uploadId))
    } catch (e) {
      if (uploadId) await abortUpload(uploadId)
      if (e instanceof DOMException && e.name === 'AbortError') {
        setError('Upload paused. Choose the same file to resume.')
      } else {
        const err = e as ApiError
        setError(err.message + (err.action ? ` ${err.action}` : ''))
      }
    } finally {
      setUploading(false)
      setChecking(false)
    }
  }

  const pct = file ? Math.round((sent / file.size) * 100) : 0

  return (
    <div role="tabpanel" aria-labelledby="tab-check" className="grid gap-4">
      <div
        className="drop"
        data-over={over}
        role="button"
        tabIndex={0}
        aria-label="Choose an existing video to check"
        onClick={() => input.current?.click()}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setOver(true) }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          const f = e.dataTransfer.files[0]
          if (f) void check(f)
        }}
      >
        <strong style={{ fontSize: 17 }}>Choose your finished video</strong>
        <p className="muted" style={{ margin: '6px 0 0' }}>Drop it here or click to browse · MP4, MOV, MKV, WebM, AVI</p>
        <input ref={input} type="file" accept={ACCEPT} hidden onChange={(e) => {
          const selected = e.target.files?.[0]
          if (selected) void check(selected)
          e.target.value = ''
        }} />
      </div>

      {file && (uploading || checking) && (
        <div aria-live="polite">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{file.name}</span>
            <span className="muted">{uploading ? `${fmtBytes(sent)} / ${fmtBytes(file.size)} (${pct}%)` : 'Checking video…'}</span>
          </div>
          <div className="bar" style={{ margin: '10px 0' }} role="progressbar" aria-valuenow={uploading ? pct : 100} aria-valuemin={0} aria-valuemax={100}>
            <i style={{ width: `${uploading ? pct : 100}%` }} />
          </div>
          {uploading && <button className="btn" onClick={() => abort.current?.abort()}>Pause upload</button>}
        </div>
      )}

      {error && <div className="alert" role="alert">{error}</div>}
      {report && (
        <section aria-label="TikTok preflight results" aria-live="polite" className="grid gap-3">
          <div>
            <strong>{report.review_count ? `${report.review_count} item${report.review_count === 1 ? '' : 's'} to review` : 'No technical concerns found'}</strong>
            <p className="muted" style={{ margin: '4px 0 0', fontSize: 12 }}>{report.notice}</p>
          </div>
          <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 10 }}>
            {report.checks.map((item) => (
              <li key={item.name}>
                <strong className={item.status === 'ok' ? 'good' : 'warn'}>{item.status === 'ok' ? '✓' : 'Review'} · {item.name}</strong>
                <div className="muted" style={{ fontSize: 13 }}>{item.detail}</div>
                {item.action && <div style={{ fontSize: 13 }}>{item.action}</div>}
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="muted" style={{ margin: 0, fontSize: 12 }}>The uploaded copy is deleted after the check. This does not create a project or modify your original clip. Authorship and third-party watermarks still need your review; no check can guarantee TikTok’s decision.</p>
    </div>
  )
}