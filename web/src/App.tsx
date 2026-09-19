import { useEffect, useState } from 'react'
import { LinkTab } from './components/LinkTab'
import { ProjectView } from './components/ProjectView'
import { UploadTab } from './components/UploadTab'

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
  const projectId = hash.startsWith('#/p/') ? hash.slice(4) : null

  function onKey(e: React.KeyboardEvent) {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') setTab((t) => (t === 'link' ? 'upload' : 'link'))
  }

  return (
    <main className="shell">
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 28 }}>
        <span className="brand">Clipforge</span>
        <span className="notice" style={{ maxWidth: 420, textAlign: 'right' }}>
          Your video stays on this computer. Only transcript text is sent to the Anthropic API for clip selection.
        </span>
      </header>
      {projectId ? (
        <ProjectView id={projectId} onBack={() => go('#/')} />
      ) : (
        <div className="grid gap-5">
          <h1 style={{ fontSize: 26 }}>Turn a long video into shorts</h1>
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
      )}
    </main>
  )
}
