import { chromium } from '../apps/frontend/node_modules/@playwright/test/index.mjs';

const required = [
  'CUTOVER_STAFF_EMAIL',
  'CUTOVER_STAFF_PASSWORD',
  'CF_ACCESS_CLIENT_ID',
  'CF_ACCESS_CLIENT_SECRET',
];
for (const name of required) {
  if (!process.env[name]) throw new Error(`${name} is required`);
}

const site = (process.env.PUBLIC_SITE_URL || 'https://syrabit.ai').replace(/\/$/, '');
const edge = (process.env.PUBLIC_EDGE_URL || 'https://api.syrabit.ai').replace(/\/$/, '');
const accessHeaders = {
  'CF-Access-Client-Id': process.env.CF_ACCESS_CLIENT_ID,
  'CF-Access-Client-Secret': process.env.CF_ACCESS_CLIENT_SECRET,
};
const sections = [
  ['dashboard', 'Dashboard'],
  ['contenthub', 'Content Editor'],
  ['seomanager', 'SEO Manager'],
  ['users', 'Users'],
  ['conversations', 'Conversations'],
  ['notifications', 'Notifications'],
  ['ai', 'AI & Automation'],
  ['analytics', 'Analytics'],
  ['security', 'Access & Security'],
  ['logs', 'Logs'],
  ['health', 'Health / Uptime'],
  ['ops', 'Ops Console'],
  ['settings', 'Site Settings'],
];

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const runtimeErrors = [];
const forbiddenRequests = [];
const failedRequests = [];
const postLogoutAuthResponses = [];
let postLogoutProbe = false;

const isAccessServiceWorkerArtifact = text =>
  text.includes('Failed to update a ServiceWorker')
  && text.includes('object is in an invalid state');

page.on('pageerror', error => {
  const text = error.stack || error.message;
  if (!isAccessServiceWorkerArtifact(text)) runtimeErrors.push(text);
});
page.on('console', message => {
  if (message.type() !== 'error') return;
  const text = message.text();
  if (
    !text.includes('Failed to load resource')
    && !isAccessServiceWorkerArtifact(text)
  ) runtimeErrors.push(text);
});
page.on('response', response => {
  if (
    postLogoutProbe
    && (response.url().includes('/users/me') || response.url().includes('/admin/verify'))
  ) {
    postLogoutAuthResponses.push(
      `${response.status()} ${response.request().method()} ${response.url()}`,
    );
  }
  if (response.status() >= 400) {
    failedRequests.push(`${response.status()} ${response.request().method()} ${response.url()}`);
  }
  if ([401, 403].includes(response.status())) {
    forbiddenRequests.push(`${response.status()} ${response.request().method()} ${response.url()}`);
  }
});
page.on('requestfailed', request => {
  failedRequests.push(`NETWORK ${request.method()} ${request.url()} — ${request.failure()?.errorText || 'unknown error'}`);
});

// Cloudflare Access protects the Pages origin, but the public API uses the
// application's bearer-token contract. Do not leak Access service-token
// headers into cross-origin API preflights: browsers correctly reject those
// headers because they are not part of the public API CORS allowlist.
await page.route(`${site}/**`, async route => {
  await route.continue({
    headers: { ...route.request().headers(), ...accessHeaders },
  });
});

try {
  const login = await context.request.post(`${edge}/api/v1/auth/login`, {
    headers: { ...accessHeaders, 'Content-Type': 'application/json' },
    data: {
      email: process.env.CUTOVER_STAFF_EMAIL,
      password: process.env.CUTOVER_STAFF_PASSWORD,
    },
  });
  if (!login.ok()) throw new Error(`Staff login failed with HTTP ${login.status()}`);
  const tokens = await login.json();
  if (!tokens.access_token || !tokens.refresh_token) {
    throw new Error('Staff login did not return both session tokens');
  }

  await page.addInitScript(({ accessToken, refreshToken }) => {
    const seedMarker = 'syrabit:staff-verifier-seeded';
    if (sessionStorage.getItem(seedMarker)) return;
    sessionStorage.setItem('syrabit_token', accessToken);
    localStorage.setItem('syrabit_refresh_token', refreshToken);
    sessionStorage.setItem(seedMarker, '1');
  }, { accessToken: tokens.access_token, refreshToken: tokens.refresh_token });

  await page.goto(`${site}/staff`, { waitUntil: 'domcontentloaded' });
  try {
    await page.getByTestId('admin-dashboard').waitFor({ state: 'visible', timeout: 30_000 });
  } catch (error) {
    const globalAlert = page.getByRole('alert').first();
    const boundaryError = await globalAlert.evaluate(element => {
      const fiberKey = Object.keys(element).find(key => key.startsWith('__reactFiber$'));
      let fiber = fiberKey ? element[fiberKey] : null;
      while (fiber) {
        const caught = fiber.stateNode?.state?.error;
        if (caught) return `${caught.name || 'Error'}: ${caught.message || String(caught)}\n${caught.stack || ''}`;
        fiber = fiber.return;
      }
      return '(React boundary error object unavailable)';
    }).catch(() => '(React boundary inspection failed)');
    const body = (await page.locator('body').innerText().catch(() => '')).slice(0, 4_000);
    throw new Error([
      `Staff shell unavailable at ${page.url()}`,
      `Boundary exception:\n${boundaryError}`,
      `Visible page text:\n${body || '(empty)'}`,
      `Runtime errors:\n${runtimeErrors.join('\n') || '(none)'}`,
      `Failed requests:\n${failedRequests.join('\n') || '(none)'}`,
      `Original wait failure: ${error.message}`,
    ].join('\n\n'));
  }

  for (const [id, label] of sections) {
    await page.getByTestId(`admin-nav-${id}`).click();
    await page.waitForURL(url => url.pathname === '/staff' && url.searchParams.get('s') === id);
    await page.waitForTimeout(800);
    if (await page.getByRole('heading', { name: 'Something went wrong' }).count()) {
      throw new Error(`${label} triggered the global error boundary`);
    }
    if (await page.getByText(new RegExp(`${label} failed to load`, 'i')).count()) {
      const alert = page.getByText(new RegExp(`${label} failed to load`, 'i')).first().locator('..');
      const boundaryError = await alert.evaluate(element => {
        const fiberKey = Object.keys(element).find(key => key.startsWith('__reactFiber$'));
        let fiber = fiberKey ? element[fiberKey] : null;
        while (fiber) {
          const error = fiber.stateNode?.state?.error;
          if (error) return `${error.name || 'Error'}: ${error.message || String(error)}\n${error.stack || ''}`;
          fiber = fiber.return;
        }
        return '(React boundary error object unavailable)';
      }).catch(() => '(React boundary inspection failed)');
      const body = (await page.locator('body').innerText().catch(() => '')).slice(0, 6_000);
      throw new Error([
        `${label} triggered its section error boundary`,
        `Boundary exception:\n${boundaryError}`,
        `Visible page text:\n${body || '(empty)'}`,
        `Runtime errors:\n${runtimeErrors.join('\n') || '(none)'}`,
        `Failed requests:\n${failedRequests.join('\n') || '(none)'}`,
      ].join('\n\n'));
    }
    await page.getByTestId('admin-dashboard').waitFor({ state: 'visible' });
    console.log(`Staff portal read passed: ${label}`);
  }

  await page.goto(`${site}/admin?s=analytics&t=usage#report`, { waitUntil: 'domcontentloaded' });
  await page.waitForURL(url =>
    url.pathname === '/staff'
    && url.searchParams.get('s') === 'analytics'
    && url.searchParams.get('t') === 'usage'
    && url.hash === '#report');
  await page.getByTestId('admin-dashboard').waitFor({ state: 'visible', timeout: 30_000 });

  if (forbiddenRequests.length) {
    throw new Error(`Staff portal issued forbidden requests:\n${forbiddenRequests.join('\n')}`);
  }
  forbiddenRequests.length = 0;
  const declineConsent = page.getByRole('button', { name: 'Decline', exact: true });
  if (await declineConsent.isVisible().catch(() => false)) {
    await declineConsent.click();
  }
  await page.getByRole('button', { name: 'Logout' }).click();
  await page.waitForURL(url => url.pathname === '/login' && url.searchParams.get('next') === '/staff');
  const remainingStorageKeys = await page.evaluate(() => ({
    session: ['syrabit_token', 'syrabit_refresh_token'].filter(key => sessionStorage.getItem(key)),
    local: ['syrabit_token', 'syrabit_refresh_token'].filter(key => localStorage.getItem(key)),
  }));
  postLogoutProbe = true;
  await page.goto(`${site}/staff`, { waitUntil: 'domcontentloaded' });
  try {
    await page.waitForURL(
      url => url.pathname === '/login' && url.searchParams.get('next') === '/staff',
      { timeout: 15_000 },
    );
  } catch (error) {
    const body = (await page.locator('body').innerText().catch(() => '')).slice(0, 2_000);
    const cookieNames = (await context.cookies())
      .map(({ name, domain, path }) => `${name} (${domain}${path})`);
    throw new Error([
      `Protected route did not redirect after UI sign-out: ${page.url()}`,
      `Stored auth keys: ${JSON.stringify(remainingStorageKeys)}`,
      `Protected staff shell visible: ${Boolean(await page.getByTestId('admin-dashboard').count())}`,
      `Post-logout auth responses:\n${postLogoutAuthResponses.join('\n') || '(none)'}`,
      `Cookie names:\n${cookieNames.join('\n') || '(none)'}`,
      `Visible page text:\n${body}`,
      `Original wait failure: ${error.message}`,
    ].join('\n\n'));
  }
  if (await page.getByTestId('admin-dashboard').count()) {
    throw new Error('Protected staff content remained visible after UI sign-out');
  }

  if (runtimeErrors.length) {
    throw new Error(`Staff portal emitted runtime errors:\n${runtimeErrors.join('\n')}`);
  }
  console.log('Production staff portal browser lifecycle passed.');
} finally {
  await context.close();
  await browser.close();
}