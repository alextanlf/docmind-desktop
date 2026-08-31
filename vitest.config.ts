import { defineConfig } from 'vitest/config'
import { resolve } from 'node:path'
export default defineConfig({ test: { environment: 'jsdom', setupFiles: [resolve(__dirname, 'electron/tests/setup.ts')] } })
