import { defineConfig, devices } from '@playwright/test';

const systemChromium = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
  || process.env.REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || undefined;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:5000',
    trace: 'on-first-retry',
    headless: true,
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        ...(systemChromium
          ? { launchOptions: { executablePath: systemChromium, args: ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'] } }
          : {}),
      },
    },
  ],
  webServer: {
    command: 'pnpm dev',
    url: 'http://localhost:5000',
    reuseExistingServer: true,
    timeout: 30000,
  },
});
