import { execFileSync } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test, type APIRequestContext } from '@playwright/test'

const HERE = dirname(fileURLToPath(import.meta.url))
const FIXTURE = join(HERE, '../../eval/fixtures/media/kende_internet_hall_of_fame.webm')
const ids: Record<string, string> = {}

async function projectId(page: import('@playwright/test').Page) {
  await expect(page).toHaveURL(/#\/p\/[a-f0-9]+/)
  return page.url().split('#/p/')[1]
}

// Curation either proposes clips, or fails in isolation with a clear, actionable message
// (for example no API credit). The transcript must never be lost either way.
async function expectCurationOutcome(page: import('@playwright/test').Page) {
  await expect(page.getByTestId('clips-ready').or(page.getByTestId('curation-error'))).toBeVisible({ timeout: 90_000 })
}

async function sourceShape(request: APIRequestContext, id: string) {
  const p = await (await request.get(`/api/projects/${id}`)).json()
  const keys = (o: Record<string, unknown>): string[] => Object.keys(o).sort()
  return {
    source: keys(p.source),
    probe: keys(p.source.probe),
    quality: keys(p.source.quality),
    transcript: keys(p.transcript),
    words: p.transcript.words as number,
    sentences: p.transcript.sentences as Array<Record<string, unknown>>,
  }
}

test('tabs are equal, keyboard reachable, and state the privacy promise', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('tab', { name: 'Paste a link' })).toBeVisible()
  await expect(page.getByRole('tab', { name: 'Upload a file' })).toBeVisible()
  await expect(page.getByText(/stays on this computer/i)).toBeVisible()
  await page.getByRole('tab', { name: 'Paste a link' }).focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'Upload a file' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByText('Drop a video or audio file here')).toBeVisible()
})

test('link flow: a bad link explains itself; a real YouTube link becomes a transcript', async ({ page }) => {
  await page.goto('/')
  const input = page.getByPlaceholder(/Paste a video link/)
  await input.fill('http://127.0.0.1/private')
  await expect(page.getByRole('alert')).toContainText(/private or local network/i)

  await input.fill('https://www.youtube.com/watch?v=jNQXAC9IVRw')
  await expect(page.getByRole('heading', { name: /Me at the zoo/i })).toBeVisible({ timeout: 60_000 })
  await expect(page.getByText(/will download/i)).toBeVisible()
  await expect(page.getByText(/permission to reuse/i)).toBeVisible()
  await page.getByRole('button', { name: 'Fetch and transcribe' }).click()
  ids.link = await projectId(page)
  await expect(page.getByTestId('transcript-ready')).toBeVisible({ timeout: 5 * 60_000 })
  await expect(page.getByTestId('status')).toHaveText('done')
  await expect(page.getByTestId('quality')).toContainText('fps')
  await expectCurationOutcome(page)
})

test('upload flow: a real file streams in chunks and becomes a transcript', async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), 'cf-e2e-'))
  const clip = join(dir, 'interview_excerpt.mp4')
  execFileSync('ffmpeg', ['-v', 'error', '-y', '-i', FIXTURE, '-t', '45', '-vf', 'scale=640:-2', '-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac', clip])
  await page.goto('/')
  await page.getByRole('tab', { name: 'Upload a file' }).click()
  await page.getByTestId('file-input').setInputFiles(clip)
  ids.upload = await projectId(page)
  await expect(page.getByTestId('transcript-ready')).toBeVisible({ timeout: 4 * 60_000 })
  await expect(page.getByTestId('status')).toHaveText('done')
  await expectCurationOutcome(page)
})

test('both sources produce the same Source and transcript schema', async ({ request }) => {
  expect(ids.link && ids.upload).toBeTruthy()
  const a = await sourceShape(request, ids.link)
  const b = await sourceShape(request, ids.upload)
  expect(a.source).toEqual(b.source)
  expect(a.probe).toEqual(b.probe)
  expect(a.quality).toEqual(b.quality)
  expect(a.transcript).toEqual(b.transcript)
  for (const t of [a, b]) {
    expect(t.words).toBeGreaterThan(20)
    expect(t.sentences[0]).toHaveProperty('id', 'S0001')
  }
})

test('an interrupted upload resumes from the server offset after a reload', async ({ page }) => {
  const dir = mkdtempSync(join(tmpdir(), 'cf-resume-'))
  const big = join(dir, 'big.mp4')
  const size = 48 * 1024 * 1024 // 6 chunks of 8 MiB
  writeFileSync(big, randomBytes(size))

  await page.goto('/')
  await page.getByRole('tab', { name: 'Upload a file' }).click()
  // Slow each chunk so the upload can be paused mid-way, like a flaky connection.
  await page.route('**/api/uploads/*', async (route) => {
    if (route.request().method() === 'PATCH') await new Promise((r) => setTimeout(r, 700))
    await route.continue()
  })
  await page.getByTestId('file-input').setInputFiles(big)
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible()
  await expect(page.getByText(/\(\s*1[0-9]%\)|\(\s*[2-6][0-9]%\)/)).toBeVisible({ timeout: 20_000 })
  await page.getByRole('button', { name: 'Pause' }).click()
  await expect(page.getByText(/Upload paused/)).toBeVisible()

  const key = await page.evaluate(() => Object.keys(localStorage).find((k) => k.startsWith('clipforge-upload:')))
  const uploadId = await page.evaluate((k) => localStorage.getItem(k!), key)
  const mid = await (await page.request.get(`/api/uploads/${uploadId}`)).json()
  expect(mid.offset).toBeGreaterThan(0)
  expect(mid.offset).toBeLessThan(size)

  await page.reload()
  await page.getByRole('tab', { name: 'Upload a file' }).click()
  const offsets: number[] = []
  page.on('request', (r) => {
    if (r.method() === 'PATCH') offsets.push(Number(r.headers()['upload-offset']))
  })
  await page.getByTestId('file-input').setInputFiles(big)
  await expect(page).toHaveURL(/#\/p\/[a-f0-9]+/, { timeout: 60_000 })
  expect(offsets[0]).toBe(mid.offset) // continued from the server's offset, not from zero
})
