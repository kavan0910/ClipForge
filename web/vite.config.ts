/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // The caption composition lives in ../captions and is shared with the export renderer.
  resolve: { alias: { '@captions': fileURLToPath(new URL('../captions/src', import.meta.url)) }, dedupe: ['react', 'react-dom', 'remotion'] },
  server: { proxy: { '/api': 'http://127.0.0.1:8765' }, fs: { allow: ['..'] } },
  test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test-setup.ts'] },
})
