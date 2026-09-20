import { cpSync, existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'

// A real, already-transcribed-and-curated project is copied in, so this flow spends no API money.
const SRC = join(homedir(), 'Clipforge/projects/fx-udio-tutoria')
const DST = join(process.cwd(), '../.e2e-data/projects/fx-e2e')

test.skip(!existsSync(SRC), 'fixture project not present (run scripts/fetch_fixtures.py and an eval run)')

test('keyboard-only: open a clip, cut a phrase, approve, render, export', async ({ page }) => {
  cpSync(SRC, DST, { recursive: true, filter: (s) => !s.includes('/clips/') || s.endsWith('/clips') })
  await page.goto('/#/p/fx-e2e')
  await expect(page.getByTestId('clips-ready')).toBeVisible({ timeout: 30_000 })

  // Reach the editor with Tab + Enter only.
  const open = page.getByTestId('open-editor').first()
  await open.focus()
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('clip-editor')).toBeVisible()

  // Select a word with the arrow keys, cut it with X, approve with A.
  await page.locator('body').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  const before = await page.getByTestId('duration').innerText()
  await page.keyboard.press('x')
  await expect(page.getByText('Saved', { exact: true })).toBeVisible()
  await expect(page.getByTestId('duration')).not.toHaveText(before)
  await page.keyboard.press('a')
  await expect(page.getByTestId('clip-status')).toHaveText('approved')

  // Render and wait for it.
  await page.getByRole('combobox', { name: 'Render quality' }).selectOption('off')  // the AI upscale is too slow for a test
  await page.getByTestId('render').focus()
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('render')).toHaveText(/Rendering/)
  await expect(page.getByTestId('preview-video')).toBeVisible({ timeout: 5 * 60_000 })

  // Export from the queue.
  await page.goto('/#/queue')
  await page.getByRole('checkbox').first().check()
  await page.getByRole('combobox', { name: 'Format' }).selectOption('edl')
  await page.getByTestId('export').click()
  await expect(page.getByTestId('export-result')).toBeVisible()
})
