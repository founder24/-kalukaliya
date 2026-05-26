import { defineConfig } from 'vitest/config';
import path from 'node:path';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src'),
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.{js,jsx,ts,tsx}'],
    exclude: [
      'src/test/edge-do-routing.test.js',
      'src/test/edge-proxy-kv-aggregation.test.ts',
      'src/test/middleware-ssr-route.test.js',
    ],
    setupFiles: ['./src/test/setup.js'],
  },
});
