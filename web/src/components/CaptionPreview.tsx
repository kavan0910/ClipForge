import { Player } from '@remotion/player'
import { useEffect, useRef, useState } from 'react'
import { CaptionsLayer } from '@captions/Captions'
import type { CaptionProps, Template, Timeline } from '@captions/types'

const FPS = 30

/** In-app caption preview: the same React composition the export renderer draws, so they cannot drift apart. */
export function CaptionPreview({ projectId, clipId, background = '#556677' }: { projectId: string; clipId: string; background?: string }) {
  const [data, setData] = useState<{ timeline: Timeline; template: Template } | null>(null)
  const [error, setError] = useState('')
  const player = useRef<import('@remotion/player').PlayerRef>(null)

  useEffect(() => {
    const base = `/api/files/${projectId}/clips/${clipId}`
    Promise.all([fetch(`${base}/captions.json`), fetch(`${base}/captions_template.json`)])
      .then(async ([a, b]) => {
        if (!a.ok || !b.ok) throw new Error('This clip has no captions yet. Render it with captions first.')
        setData({ timeline: await a.json(), template: await b.json() })
      })
      .catch((e: Error) => setError(e.message))
  }, [projectId, clipId])

  useEffect(() => {
    if (data) (window as unknown as { __captionSeek?: (f: number) => void }).__captionSeek = (f) => player.current?.seekTo(f)
  }, [data])

  if (error) return <p className="alert" role="alert">{error}</p>
  if (!data) return <p className="muted">Loading captions…</p>
  const props: CaptionProps = { ...data, fontBase: '/fonts', layer: 'all' }
  return (
    <div data-testid="caption-preview" style={{ width: 1080, height: 1920, background }}>
      <Player
        ref={player}
        component={CaptionsLayer as never}
        inputProps={props as never}
        durationInFrames={Math.max(1, Math.ceil(data.timeline.duration * FPS))}
        compositionWidth={1080}
        compositionHeight={1920}
        fps={FPS}
        controls={false}
        style={{ width: 1080, height: 1920 }}
        acknowledgeRemotionLicense
      />
    </div>
  )
}
