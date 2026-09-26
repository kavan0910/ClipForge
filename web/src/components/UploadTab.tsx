import { useRef, useState } from 'react'
import { abortUpload, api, ApiError, type CreateOptions, uploadFile } from '../api'
import { fmtBytes } from '../format'

const ACCEPT = '.mp4,.mov,.mkv,.webm,.avi,.m4v,.mp3,.wav,.m4a,.ogg,.opus,.flac,.aac'
const MAX_REFS = 3

function fileToBase64(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = () => reject(r.error)
    r.readAsDataURL(f)
  })
}

export function UploadTab({ onStart }: { onStart: (id: string) => void }) {
  const [over, setOver] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [sent, setSent] = useState(0)
  const [error, setError] = useState<string>('')
  const [uploading, setUploading] = useState(false)
  const [path, setPath] = useState('')
  const abort = useRef<AbortController | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const [mode, setMode] = useState<'clips' | 'character_edit'>('clips')
  const [character, setCharacter] = useState('')
  const [refs, setRefs] = useState<string[]>([])
  const [targetDuration, setTargetDuration] = useState(30)
  const refInputRef = useRef<HTMLInputElement>(null)

  const characterOptions = (): CreateOptions =>
    mode === 'character_edit'
      ? { mode, character: character.trim(), reference_images: refs, target_duration: targetDuration }
      : {}

  async function begin(f: File) {
    setFile(f)
    setError('')
    setSent(0)
    setUploading(true)
    const ctl = new AbortController()
    abort.current = ctl
    try {
      const id = await uploadFile(f, (s) => setSent(s), ctl.signal)
      const r = await api.createProject({ type: 'upload', upload_id: id }, characterOptions())
      onStart(r.project_id)
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') setError('Upload paused. Choose the same file to resume.')
      else setError((e as ApiError).message + ((e as ApiError).action ? ' ' + (e as ApiError).action : ''))
      setUploading(false)
    }
  }

  async function importPath() {
    try {
      const r = await api.createProject({ type: 'path', path: path.trim() }, characterOptions())
      onStart(r.project_id)
    } catch (e) {
      setError((e as ApiError).message)
    }
  }

  const pct = file ? Math.round((sent / file.size) * 100) : 0
  const canStart = mode === 'clips' || character.trim().length > 0
  return (
    <div role="tabpanel" aria-labelledby="tab-upload" className="grid gap-4">
      <div role="radiogroup" aria-label="What to make" style={{ display: 'flex', gap: 8 }}>
        <button className="btn" aria-pressed={mode === 'clips'} onClick={() => setMode('clips')}>
          Clips
        </button>
        <button className="btn" aria-pressed={mode === 'character_edit'} onClick={() => setMode('character_edit')} data-testid="mode-character-edit">
          Character edit
        </button>
      </div>
      {mode === 'character_edit' && (
        <div className="card grid gap-3" aria-label="Character edit options">
          <div>
            <label className="muted" style={{ fontSize: 12 }} htmlFor="character-name">Character name</label>
            <input
              id="character-name"
              className="field"
              placeholder="e.g. Gojo Satoru"
              value={character}
              onChange={(e) => setCharacter(e.target.value)}
              data-testid="character-name"
            />
          </div>
          <div>
            <label className="muted" style={{ fontSize: 12 }}>Reference images (optional, up to {MAX_REFS} — helps the scan recognise them)</label>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4, alignItems: 'center' }}>
              {refs.map((r, i) => (
                <img key={i} src={r} alt={`Reference ${i + 1}`} style={{ width: 56, height: 56, objectFit: 'cover', borderRadius: 8, border: '1px solid var(--border)' }} />
              ))}
              {refs.length < MAX_REFS && (
                <button className="btn" onClick={() => refInputRef.current?.click()}>Add image</button>
              )}
              <input
                ref={refInputRef}
                type="file"
                accept="image/*"
                hidden
                onChange={async (e) => {
                  const f = e.target.files?.[0]
                  e.target.value = ''
                  if (!f) return
                  const data = await fileToBase64(f)
                  setRefs((rs) => [...rs, data].slice(0, MAX_REFS))
                }}
              />
            </div>
          </div>
          <div>
            <label className="muted" style={{ fontSize: 12 }} htmlFor="target-duration">Target edit length (seconds)</label>
            <input
              id="target-duration"
              className="field"
              type="number"
              min={10}
              max={120}
              style={{ width: 100 }}
              value={targetDuration}
              onChange={(e) => setTargetDuration(Number(e.target.value) || 30)}
            />
          </div>
          <p className="muted" style={{ margin: 0, fontSize: 12 }}>
            Upload the episode or movie below; no transcript is needed for this mode, so it skips straight to scanning frames for {character.trim() || 'the character'}.
          </p>
        </div>
      )}
      <div
        className="drop"
        data-over={over}
        role="button"
        tabIndex={0}
        aria-label="Choose a file to upload"
        aria-disabled={!canStart}
        onClick={() => canStart && inputRef.current?.click()}
        onKeyDown={(e) => canStart && (e.key === 'Enter' || e.key === ' ') && inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setOver(true)
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault()
          setOver(false)
          const f = e.dataTransfer.files[0]
          if (f && canStart) void begin(f)
        }}
      >
        <div className="drop-icon" aria-hidden><svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 16V4m0 0l-4 4m4-4l4 4M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3" /></svg></div>
        <strong style={{ fontSize: 17 }}>Drop a video or audio file here</strong>
        <p className="muted" style={{ margin: '6px 0 0' }}>
          {canStart ? 'or click to browse · mp4, mov, mkv, webm, avi, m4v, mp3, wav, m4a' : 'Type the character’s name above first'}
        </p>
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          hidden
          data-testid="file-input"
          onChange={(e) => e.target.files?.[0] && canStart && void begin(e.target.files[0])}
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
          <button className="btn" disabled={!path.trim() || !canStart} onClick={importPath}>
            Import
          </button>
        </div>
      </details>
    </div>
  )
}
