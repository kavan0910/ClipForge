import { defineConfig } from '@playwright/test'

const PORT = 8765
export default defineConfig({
  testDir: './e2e',
  timeout: 6 * 60_000,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
  webServer: {
    command: `cd .. && rm -rf .e2e-data && DATA_DIR=.e2e-data uv run uvicorn clipforge.api.app:app --app-dir backend --host 127.0.0.1 --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
