import { useRef, useState } from 'react'
import { abortUpload, api, ApiError, uploadFile } from '../api'
import { fmtBytes } from '../format'

const ACCEPT = '.mp4,.mov,.mkv,.webm,.avi,.m4v,.mp3,.wav,.m4a'

export function UploadTab({ onStart }: { onStart: (id: string) => void }) {
  const [over, setOver] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [sent, setSent] = useState(0)
  const [error, setError] = useState<string>('')
  const [uploading, setUploading] = useState(false)
  const [path, setPath] = useState('')
  const abort = useRef<AbortController | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  async function begin(f: File) {
    setFile(f)
    setError('')
    setSent(0)
    setUploading(true)
    const ctl = new AbortController()
    abort.current = ctl
    try {
      const id = await uploadFile(f, (s) => setSent(s), ctl.signal)
      const r = await api.createProject({ type: 'upload', upload_id: id })
      onStart(r.project_id)
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') setError('Upload paused. Choose the same file to resume.')
      else setError((e as ApiError).message + ((e as ApiError).action ? ' ' + (e as ApiError).action : ''))
      setUploading(false)
    }
  }

  async function importPath() {
    try {
      const r = await api.createProject({ type: 'path', path: path.trim() })
      onStart(r.project_id)
    } catch (e) {
      setError((e as ApiError).message)
    }
  }

  const pct = file ? Math.round((sent / file.size) * 100) : 0
  return (
    <div role="tabpanel" aria-labelledby="tab-upload" className="grid gap-4">
      <div
        className="drop"
        data-over={over}
        role="button"
        tabIndex={0}
        aria-label="Choose a file to upload"
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          const f = e.dataTransfer.files[0]
          if (f) void begin(f)
        }}
      >
        <strong>Drop a video or audio file here</strong>
        <p className="muted" style={{ margin: '6px 0 0' }}>or click to browse · mp4, mov, mkv, webm, avi, m4v, mp3, wav, m4a</p>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          hidden
          data-testid="file-input"
          onChange={(e) => e.target.files?.[0] && void begin(e.target.files[0])}
        />
      </div>
      {file && (
        <div className="card" aria-live="polite">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{file.name}</span>
            <span className="muted">
              {fmtBytes(sent)} / {fmtBytes(file.size)} ({pct}%)
            </span>
          </div>
          <div className="bar" style={{ margin: '10px 0' }} role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
            <i style={{ width: `${pct}%` }} />
          </div>
          {uploading && (
            <button className="btn" onClick={() => abort.current?.abort()}>
              Pause
            </button>
          )}
          {uploading && (
            <button
              className="btn btn-danger"
              style={{ marginLeft: 8 }}
              onClick={async () => {
                abort.current?.abort()
                const key = `clipforge-upload:${file.name}:${file.size}:${file.lastModified}`
                const id = localStorage.getItem(key)
                if (id) await abortUpload(id)
                localStorage.removeItem(key)
                setFile(null)
              }}
            >
              Cancel
            </button>
          )}
        </div>
      )}
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      <details>
        <summary className="muted" style={{ cursor: 'pointer' }}>Import a file already on this computer (no copy)</summary>
        <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
          <input className="field" placeholder="/Users/you/Videos/podcast.mp4" value={path} onChange={(e) => setPath(e.target.value)} aria-label="Local file path" />
          <button className="btn" disabled={!path.trim()} onClick={importPath}>
            Import
          </button>
        </div>
      </details>
    </div>
  )
}
