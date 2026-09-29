import { defineConfig, devices } from '@playwright/test';

const systemChromium = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
  || process.env.REPLIT_PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || undefined;
const stagingE2e = process.env.STAGING_E2E === '1';
const skipWebServer = process.env.PLAYWRIGHT_SKIP_WEB_SERVER === '1';
const stagingWorkerUrl = process.env.STAGING_WORKER_URL || '';
const stagingWorkerHostExpected = 'syrabitworker-staging.axomxplain.workers.dev';
let stagingWorkerOrigin = '';
let stagingWorkerHost = '';
let baseURL = process.env.BASE_URL || 'http://localhost:5000';
let webServerUrl = process.env.PLAYWRIGHT_WEB_SERVER_URL || 'http://localhost:5000';

if (stagingE2e) {
  let workerUrl: URL;
  try {
    workerUrl = new URL(stagingWorkerUrl);
  } catch {
    throw new Error('STAGING_WORKER_URL must be the HTTPS origin of the staging Worker.');
  }
  if (
    workerUrl.protocol !== 'https:'
    || workerUrl.hostname !== stagingWorkerHostExpected
    || workerUrl.username
    || workerUrl.password
    || workerUrl.port
    || workerUrl.pathname !== '/'
    || workerUrl.search
    || workerUrl.hash
  ) {
    throw new Error('STAGING_WORKER_URL must be an exact HTTPS workers.dev origin.');
  }
  if (!(process.env.STAGING_ACCESS_TOKEN || '').trim()) {
    throw new Error('STAGING_ACCESS_TOKEN is required for staging E2E.');
  }
  if (skipWebServer || process.env.PLAYWRIGHT_WEB_SERVER_COMMAND) {
    throw new Error('Staging E2E must start its own isolated Vite proxy on port 5001.');
  }

  const requestedBase = new URL(baseURL);
  if (
    requestedBase.protocol !== 'http:'
    || !['127.0.0.1', 'localhost', '[::1]'].includes(requestedBase.hostname)
    || requestedBase.port !== '5001'
    || requestedBase.pathname !== '/'
    || requestedBase.search
    || requestedBase.hash
  ) {
    throw new Error('Staging E2E BASE_URL must be a loopback origin on port 5001.');
  }

  stagingWorkerOrigin = workerUrl.origin;
  stagingWorkerHost = workerUrl.hostname.toLowerCase();
  baseURL = requestedBase.origin;
  webServerUrl = process.env.PLAYWRIGHT_WEB_SERVER_URL || baseURL;
  const requestedServerUrl = new URL(webServerUrl);
  if (
    requestedServerUrl.origin !== baseURL
    || requestedServerUrl.pathname !== '/'
    || requestedServerUrl.search
    || requestedServerUrl.hash
  ) {
    throw new Error('Staging E2E web server URL must match its loopback BASE_URL.');
  }
  webServerUrl = requestedServerUrl.origin;
}

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL,
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
  ...(skipWebServer ? {} : {
    webServer: {
      command: stagingE2e
        ? 'pnpm exec vite --host 127.0.0.1 --port 5001'
        : process.env.PLAYWRIGHT_WEB_SERVER_COMMAND || 'pnpm dev',
      url: webServerUrl,
      reuseExistingServer: !stagingE2e,
      timeout: 30000,
      ...(stagingE2e ? {
        env: {
          ...process.env,
          PORT: '5001',
          STAGING_E2E: '1',
          BACKEND_PROXY_URL: stagingWorkerOrigin,
          STAGING_ACCESS_HOST: stagingWorkerHost,
          VITE_BACKEND_URL: '',
          VITE_CHAT_API_ORIGIN: '',
        },
      } : {}),
    },
  }),
});
