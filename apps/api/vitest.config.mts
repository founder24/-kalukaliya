import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Standard Node.js worker threads — no Workers runtime needed.
    // Tests call app.fetch() directly with real D1 via getPlatformProxy.
    pool: 'forks',
    testTimeout: 60_000,
    hookTimeout: 60_000,
  },
});
