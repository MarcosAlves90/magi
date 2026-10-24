import { defineConfig } from 'vitest/config'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  resolve: {
    alias: {
      '@hermes/plugin-sdk': fileURLToPath(new URL('./tests/desktop/sdk.mock.js', import.meta.url))
    }
  },
  test: {
    include: ['tests/desktop/*.test.jsx'],
    environment: 'jsdom',
    restoreMocks: true,
    clearMocks: true,
    maxWorkers: 1,
    testTimeout: 5000
  }
})
