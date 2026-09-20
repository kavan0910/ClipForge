import { Composition } from 'remotion'
import { CaptionsLayer } from './Captions'
import type { CaptionProps } from './types'

const empty: CaptionProps = { timeline: { template_id: '', width: 1080, height: 1920, duration: 1, chunks: [], hook: null, safe: {} }, template: {} as never, fontBase: 'fonts' }

export const Root = () => (
  <Composition
    id="Captions"
    component={CaptionsLayer as never}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={30}
    defaultProps={empty as never}
    calculateMetadata={({ props }: { props: CaptionProps }) => ({ durationInFrames: Math.max(1, Math.ceil(props.timeline.duration * 30)), width: props.timeline.width, height: props.band?.h ?? props.timeline.height })}
  />
)
