// node render.mjs <timeline.json> <template.json> <out> [mode=vp9|png] [fps=30] [layer=all|captions|hook] [y0] [h]
// states: render only the frames listed in props.times (unique visual states) as PNGs into <out> (fast path).
// vp9: alpha WebM (yuva420p). png: PNG sequence into the directory <out> (for the ADR-006 benchmark).
import { bundle } from '@remotion/bundler'
import { renderFrames, renderMedia, selectComposition } from '@remotion/renderer'
import { mkdirSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const [timelinePath, templatePath, out, mode = 'vp9', fpsArg = '30', layer = 'all', y0Arg, hArg] = process.argv.slice(2)
const timeline = JSON.parse(readFileSync(timelinePath, 'utf8'))
const template = JSON.parse(readFileSync(templatePath, 'utf8'))
const fps = Number(fpsArg)
const band = y0Arg !== undefined && hArg !== undefined ? { y0: Number(y0Arg), h: Number(hArg) } : undefined

const serveUrl = await bundle({ entryPoint: resolve(here, 'src/index.ts'), publicDir: resolve(here, 'public') })
const extra = process.env.CAPTION_TIMES ? { times: JSON.parse(readFileSync(process.env.CAPTION_TIMES, 'utf8')) } : {}
const inputProps = { ...extra, timeline, template, fontBase: '/public/fonts', layer, band }
const base = await selectComposition({ serveUrl, id: 'Captions', inputProps })
const nFrames = extra.times ? extra.times.length : Math.max(1, Math.ceil(timeline.duration * fps))
const comp = { ...base, fps, durationInFrames: nFrames }
const started = Date.now()
if (mode === 'png' || mode === 'states') {
  mkdirSync(out, { recursive: true })
  await renderFrames({ composition: comp, serveUrl, inputProps, outputDir: out, imageFormat: 'png', onFrameUpdate: () => undefined, onStart: () => undefined, concurrency: Number(process.env.CAPTION_CONCURRENCY || 2), logLevel: 'error' })
} else {
  await renderMedia({ composition: comp, serveUrl, inputProps, outputLocation: out, codec: 'vp9', imageFormat: 'png', pixelFormat: 'yuva420p', concurrency: 2, logLevel: 'error' })
}
console.log(JSON.stringify({ seconds: (Date.now() - started) / 1000, frames: comp.durationInFrames, width: comp.width, height: comp.height, out }))
