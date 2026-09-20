import { useEffect, useState } from 'react'
import { LinkTab } from './components/LinkTab'
import { CaptionPreview } from './components/CaptionPreview'
import { ProjectView } from './components/ProjectView'
import { UploadTab } from './components/UploadTab'
import { ClipEditor } from './components/ClipEditor'
import { ProjectsList } from './components/ProjectsList'
import { RenderQueue } from './components/RenderQueue'
import { SettingsPage } from './components/SettingsPage'

type Tab = 'link' | 'upload'

function useHashRoute(): [string, (h: string) => void] {
  const [hash, setHash] = useState(window.location.hash)
  useEffect(() => {
    const on = () => setHash(window.location.hash)
    window.addEventListener('hashchange', on)
    return () => window.removeEventListener('hashchange', on)
  }, [])
  return [hash, (h) => (window.location.hash = h)]
}

export default function App() {
  const [hash, go] = useHashRoute()
  const [tab, setTab] = useState<Tab>('link')
  const parts = hash.startsWith('#/p/') ? hash.slice(4).split('/') : []
  const projectId = parts[0] || null
  const clipId = parts[1] === 'c' ? parts[2] : null
  const preview = hash.startsWith('#/preview/') ? hash.slice(10).split('/') : null

  function onKey(e: React.KeyboardEvent) {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') setTab((t) => (t === 'link' ? 'upload' : 'link'))
  }

  const route = hash === '#/queue' ? 'queue' : hash === '#/settings' ? 'settings' : hash.startsWith('#/p/') || hash.startsWith('#/preview/') ? 'project' : 'new'
  const link = (h: string, key: string, label: string, icon: React.ReactNode) => (
    <a href={h} aria-current={route === key ? 'page' : undefined}>{icon}{label}</a>
  )
  const ic = (d: string) => <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden><path d={d} /></svg>

  return (
    <>
      <div className="aurora" aria-hidden><i /><i /><i /></div>
      <main className="shell">
        <header className="topbar">
          <a className="brand" href="#/" aria-label="Clipforge home">
            <span className="brand-mark"><svg width="16" height="16" viewBox="0 0 24 24" fill="#fff" aria-hidden><path d="M8 5v14l11-7z" /></svg></span>
            Clipforge
          </a>
          <nav className="nav" aria-label="Main">
            {link('#/', 'new', 'Create', ic('M12 5v14M5 12h14'))}
            {link('#/queue', 'queue', 'Renders', ic('M4 6h16M4 12h16M4 18h10'))}
            {link('#/settings', 'settings', 'Settings', ic('M12 15a3 3 0 100-6 3 3 0 000 6zM19.4 15a1.7 1.7 0 00.3 1.9l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.9-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.9.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.9 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.9l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.9.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.9-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.9V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z'))}
          </nav>
          <span className="privacy">Your video stays on this computer. Only transcript text is sent to the Anthropic API for clip selection.</span>
        </header>
        <div className="page" key={hash || '#/'}>
          {preview ? (
            <CaptionPreview projectId={preview[0]} clipId={preview[1]} />
          ) : hash === '#/queue' ? (
            <RenderQueue />
          ) : hash === '#/settings' ? (
            <SettingsPage />
          ) : projectId && clipId ? (
            <ClipEditor projectId={projectId} clipId={clipId} onBack={() => go(`#/p/${projectId}`)} />
          ) : projectId ? (
            <ProjectView id={projectId} onBack={() => go('#/')} />
          ) : (
            <>
              <section className="hero">
                <h1>Turn a long video into <span className="gradient-text">shorts that get watched</span></h1>
                <p>Paste a link or drop a file. Clipforge finds the best moments, reframes them for vertical, adds captions and hands you ready-to-post clips.</p>
                <div className="how" aria-label="How it works">
                  <div><b>1</b><span><strong>Add a video</strong>Link or file</span></div>
                  <div><b>2</b><span><strong>AI picks moments</strong>Ranked, with hooks</span></div>
                  <div><b>3</b><span><strong>Edit in seconds</strong>Trim by text, captions</span></div>
                  <div><b>4</b><span><strong>Export or post</strong>MP4, NLE, YouTube</span></div>
                </div>
              </section>
              <div className="card grid gap-4">
                <div className="tabs" role="tablist" aria-label="Source" onKeyDown={onKey}>
                  <button role="tab" id="tab-link" className="tab" aria-selected={tab === 'link'} tabIndex={tab === 'link' ? 0 : -1} onClick={() => setTab('link')}>
                    Paste a link
                  </button>
                  <button role="tab" id="tab-upload" className="tab" aria-selected={tab === 'upload'} tabIndex={tab === 'upload' ? 0 : -1} onClick={() => setTab('upload')}>
                    Upload a file
                  </button>
                </div>
                {tab === 'link' ? <LinkTab onStart={(id) => go(`#/p/${id}`)} /> : <UploadTab onStart={(id) => go(`#/p/${id}`)} />}
              </div>
              <ProjectsList onOpen={(id) => go(`#/p/${id}`)} />
            </>
          )}
        </div>
      </main>
    </>
  )
}
