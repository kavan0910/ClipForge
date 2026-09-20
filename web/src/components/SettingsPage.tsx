import { useEffect, useState } from 'react'
import { api, type BrandKitData, type DoctorCheck, type SettingsData, type StorageData } from '../api'
import { fmtBytes } from '../format'

export function SettingsPage() {
  const [s, setS] = useState<SettingsData | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [key, setKey] = useState('')
  const [hf, setHf] = useState('')
  const [doctor, setDoctor] = useState<DoctorCheck[] | null>(null)
  const [storage, setStorage] = useState<StorageData | null>(null)
  const [kits, setKits] = useState<BrandKitData[]>([])
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')

  const load = () => {
    api.settings().then((d) => { setS(d); setValues(d.values) }).catch((e: Error) => setError(e.message))
    api.storage().then(setStorage).catch(() => undefined)
    api.brandKits().then(setKits).catch(() => undefined)
  }
  useEffect(load, [])

  const wrap = (p: Promise<unknown>, ok: string) => { setError(''); p.then(() => { setMsg(ok); load() }).catch((e: Error) => setError(e.message)) }
  const set = (k: string, v: string) => setValues((x) => ({ ...x, [k]: v }))

  if (error && !s) return <div className="alert" role="alert">{error}</div>
  if (!s) return <p className="muted">Loading…</p>
  return (
    <div className="grid gap-4" data-testid="settings">
      <h1 style={{ fontSize: 24 }}>Settings</h1>
      <p className="muted" style={{ margin: 0 }} aria-live="polite">{msg}</p>
      {error && <div className="alert" role="alert">{error}</div>}

      <section className="card" aria-label="Keys">
        <h3 style={{ fontSize: 15 }}>Keys</h3>
        <p className="muted" style={{ fontSize: 13 }}>Stored in your local .env (owner-only). Shown masked and never sent anywhere except the provider.</p>
        <label>Anthropic API key <span className="muted">{s.anthropic_api_key ?? 'not set'}</span>
          <input className="field" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="sk-ant-…" />
        </label>
        <label style={{ display: 'block', marginTop: 8 }}>Hugging Face token <span className="muted">{s.hf_token ?? 'not set'}</span>
          <input className="field" type="password" autoComplete="off" value={hf} onChange={(e) => setHf(e.target.value)} placeholder="hf_…" />
        </label>
        <button className="btn btn-primary" style={{ marginTop: 10 }} disabled={!key && !hf} onClick={() => { wrap(api.saveSecrets({ ...(key ? { anthropic_api_key: key } : {}), ...(hf ? { hf_token: hf } : {}) }), 'Keys saved.'); setKey(''); setHf('') }}>Save keys</button>
      </section>

      <section className="card" aria-label="Preferences">
        <h3 style={{ fontSize: 15 }}>Preferences</h3>
        <div style={{ display: 'grid', gap: 8, gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', marginTop: 8 }}>
          {Object.keys(values).map((k) => (
            <label key={k} style={{ fontSize: 13 }}>{k.replace(/_/g, ' ')}
              <input className="field" value={values[k]} onChange={(e) => set(k, e.target.value)} />
            </label>
          ))}
        </div>
        <button className="btn btn-primary" style={{ marginTop: 10 }} onClick={() => wrap(api.saveSettings(values), 'Preferences saved. New projects use them.')}>Save preferences</button>
      </section>

      <section className="card" aria-label="Health">
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <h3 style={{ fontSize: 15, flex: 1 }}>Health check</h3>
          <button className="btn" onClick={() => void api.doctor().then((d) => setDoctor(d.checks))}>Run</button>
          <button className="btn" onClick={() => wrap(api.updateYtdlp(), 'yt-dlp updated.')}>Update yt-dlp</button>
        </div>
        {doctor && (
          <ul style={{ listStyle: 'none', padding: 0, margin: '8px 0 0', display: 'grid', gap: 4 }}>
            {doctor.map((c) => (
              <li key={c.name}><strong>{c.status === 'ok' ? '✓' : c.status === 'warn' ? '!' : '✗'} {c.name}</strong> <span className="muted">{c.detail}{c.status !== 'ok' && c.fix ? ` — ${c.fix}` : ''}</span></li>
            ))}
          </ul>
        )}
      </section>

      <section className="card" aria-label="Storage">
        <h3 style={{ fontSize: 15 }}>Storage</h3>
        {storage && (
          <>
            <p className="muted" style={{ margin: '4px 0' }}>{fmtBytes(storage.total_bytes)} used · {fmtBytes(storage.free_bytes)} free in {s.data_dir}</p>
            {storage.projects.length === 0 && <p className="muted">No projects yet.</p>}
            <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 6 }}>
              {storage.projects.map((p) => (
                <li key={p.id} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <a href={`#/p/${p.id}`} style={{ flex: 1 }}>{p.id}</a>
                  <span className="muted">{fmtBytes(p.bytes)}</span>
                  <button className="btn" disabled={p.intermediate_bytes === 0} onClick={() => wrap(api.cleanup(p.id), 'Intermediates removed. Rendered clips are kept.')}>Free {fmtBytes(p.intermediate_bytes)}</button>
                  <button className="btn btn-danger" onClick={() => { if (window.confirm(`Delete project ${p.id} and its files?`)) wrap(api.remove(p.id), 'Project deleted.') }}>Delete</button>
                </li>
              ))}
            </ul>
          </>
        )}
      </section>

      <section className="card" aria-label="Brand kits">
        <h3 style={{ fontSize: 15 }}>Brand kits</h3>
        {kits.length === 0 && <p className="muted">No brand kits yet.</p>}
        {kits.map((k) => (
          <div key={k.id} style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '6px 0' }}>
            <strong style={{ flex: 1 }}>{k.name}</strong>
            <input aria-label={`Accent colour for ${k.name}`} type="color" value={k.accent_color ?? '#ffd400'} onChange={(e) => wrap(api.saveBrand({ ...k, accent_color: e.target.value }), 'Brand kit saved.')} />
            <label className="btn">Logo
              <input type="file" accept="image/png,image/svg+xml,image/jpeg" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) { const fd = new FormData(); fd.append('file', f); wrap(fetch(`/api/brand/${k.id}/logo`, { method: 'POST', body: fd }).then((r) => { if (!r.ok) throw new Error('Logo upload failed') }), 'Logo saved.') } }} />
            </label>
          </div>
        ))}
        <button className="btn" onClick={() => { const name = window.prompt('Brand kit name'); if (name) wrap(api.saveBrand({ id: name.toLowerCase().replace(/[^a-z0-9]+/g, '-'), name, default_template: 'karaoke-pop', vocabulary: [] }), 'Brand kit created.') }}>New brand kit</button>
      </section>
    </div>
  )
}
