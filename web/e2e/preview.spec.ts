import { copyFileSync, existsSync, mkdirSync, readFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import pixelmatch from 'pixelmatch'
import { PNG } from 'pngjs'

const SRC = join(homedir(), 'Clipforge/projects/kende1/clips/c001')
const HERE = dirname(fileURLToPath(import.meta.url))
const DEST = join(HERE, '../../.e2e-data/projects/prev0001/clips/c001')
const BG = [0x55, 0x66, 0x77]

test.skip(!existsSync(join(SRC, 'layer_captions/times.json')), 'render a clip with --renderer remotion first')

/** Composite an exported RGBA band still onto the same solid background at its y offset. */
function exportedFrame(png: Buffer, y0: number): PNG {
  const band = PNG.sync.read(png)
  const full = new PNG({ width: 1080, height: 1920 })
  for (let i = 0; i < full.data.length; i += 4) { full.data[i] = BG[0]; full.data[i + 1] = BG[1]; full.data[i + 2] = BG[2]; full.data[i + 3] = 255 }
  for (let y = 0; y < band.height; y++) {
    for (let x = 0; x < band.width; x++) {
      const s = (y * band.width + x) * 4
      const d = ((y + y0) * 1080 + x) * 4
      const a = band.data[s + 3] / 255
      for (let c = 0; c < 3; c++) full.data[d + c] = Math.round(band.data[s + c] * a + full.data[d + c] * (1 - a))
    }
  }
  return full
}

test('the in-app preview and the exported caption layer render the same pixels', async ({ page }) => {
  for (const f of ['captions.json', 'captions_template.json']) {
    mkdirSync(DEST, { recursive: true })
    copyFileSync(join(SRC, f), join(DEST, f))
  }
  const times: number[] = JSON.parse(readFileSync(join(SRC, 'layer_captions/times.json'), 'utf8'))
  const template = JSON.parse(readFileSync(join(SRC, 'captions_template.json'), 'utf8'))
  const y0 = Math.max(Math.round(template.anchor.y * 1920 - 0.15 * 1920), 0)
  const pngs = (await import('node:fs')).readdirSync(join(SRC, 'layer_captions/png')).filter((f) => f.endsWith('.png')).sort()

  await page.setViewportSize({ width: 1100, height: 1950 })
  await page.goto('/#/preview/prev0001/c001')
  const box = page.getByTestId('caption-preview')
  await expect(box).toBeVisible()
  await page.evaluate(() => document.fonts.ready)

  const results: number[] = []
  const inks: number[] = []
  for (const idx of [30, 55, 80]) {
    const t = times[idx]
    await page.evaluate((f) => (window as unknown as { __captionSeek: (n: number) => void }).__captionSeek(f), Math.round(t * 30))
    await page.waitForTimeout(400)
    const shot = PNG.sync.read(await box.screenshot())
    const want = exportedFrame(readFileSync(join(SRC, 'layer_captions/png', pngs[idx])), y0)
    // Compare only the caption band; count pixels that differ noticeably.
    const bandTop = y0, bandH = 576
    const a = new PNG({ width: 1080, height: bandH }), b = new PNG({ width: 1080, height: bandH })
    PNG.bitblt(shot, a, 0, bandTop, 1080, bandH, 0, 0)
    PNG.bitblt(want, b, 0, bandTop, 1080, bandH, 0, 0)
    const diff = pixelmatch(a.data, b.data, undefined, 1080, bandH, { threshold: 0.2 })
    const blank = new PNG({ width: 1080, height: bandH })
    for (let i = 0; i < blank.data.length; i += 4) { blank.data[i] = BG[0]; blank.data[i + 1] = BG[1]; blank.data[i + 2] = BG[2]; blank.data[i + 3] = 255 }
    const ink = pixelmatch(b.data, blank.data, undefined, 1080, bandH, { threshold: 0.2 })
    expect(ink, 'the exported still must actually contain caption pixels').toBeGreaterThan(3000)
    inks.push(ink)
    results.push(diff / ink)
  }
  console.log('caption pixels:', inks.join(', '), '| mismatch share:', results.map((r) => r.toFixed(4)).join(', '))
  for (const r of results) expect(r).toBeLessThan(0.08)
})
