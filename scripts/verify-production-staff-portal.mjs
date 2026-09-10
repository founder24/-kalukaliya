import { mkdir, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { chromium } from '../apps/frontend/node_modules/@playwright/test/index.mjs';
import {
  isExpectedPostLogoutAuthResponse,
  isPostLogoutAuthEndpoint,
} from './staff-portal-response-policy.mjs';
import {
  STAFF_PORTAL_SECTIONS,
  assertStaffSectionCoverage,
  assertStaffSectionReleaseChecks,
} from '../apps/frontend/src/config/staffPortalSections.mjs';
import {
  STAFF_PORTAL_TRACE_OPTIONS,
  addLoginTokensToRedactions,
  saveRedactedTrace,
} from './staff-portal-diagnostics.mjs';

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
assertStaffSectionReleaseChecks(STAFF_PORTAL_SECTIONS);
const verifierSections = STAFF_PORTAL_SECTIONS.map(({ id, label, releaseCheck }) => ({
  id,
  label,
  releaseCheck,
}));
assertStaffSectionCoverage(STAFF_PORTAL_SECTIONS, verifierSections);
const sections = verifierSections.map(({ id, label, releaseCheck }) => [id, label, releaseCheck]);
const contentHubTabs = [
  { id: 'editor', label: 'Content Editor', requiredReads: [] },
  { id: 'cms', label: 'CMS / Docs', unsupported: true },
  {
    id: 'blog',
    label: 'Blog Publisher',
    requiredReads: [
      '/api/v1/staff/content/boards',
      '/api/v1/staff/content/classes',
      '/api/v1/staff/content/streams',
      '/api/v1/staff/content/subjects',
    ],
  },
  { id: 'translation', label: 'Assamese', unsupported: true },
  { id: 'progress', label: 'Translation Progress', unsupported: true },
  {
    id: 'seeder',
    label: 'Seeder History',
    requiredReads: ['/api/v1/admin/content/seed-notes/history'],
  },
  { id: 'rag-mirror', label: 'RAG Mirror', unsupported: true },
];

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext();
const page = await context.newPage();
const diagnosticsDir = resolve(process.env.STAFF_PORTAL_DIAGNOSTICS_DIR || 'staff-portal-diagnostics');
const sensitiveValues = new Set([
  process.env.CUTOVER_STAFF_EMAIL,
  process.env.CUTOVER_STAFF_PASSWORD,
  process.env.CF_ACCESS_CLIENT_ID,
  process.env.CF_ACCESS_CLIENT_SECRET,
].filter(Boolean));
await rm(diagnosticsDir, { recursive: true, force: true });
let tracingStopped = false;
const runtimeErrors = [];
const forbiddenRequests = [];
const failedRequests = [];
const postLogoutAuthResponses = [];
const strippedApiAccessHeaders = [];
const contentReadsWithoutBearer = [];
let postLogoutProbe = false;
let activeSection = 'dashboard';
const successfulReads = new Map(sections.map(([id]) => [id, new Set()]));
const apiReadsStarted = new Map(sections.map(([id]) => [id, []]));
const requestSections = new WeakMap();
const REQUIRED_READ_TIMEOUT_MS = 15_000;

await context.tracing.start(STAFF_PORTAL_TRACE_OPTIONS);

async function waitForRequiredReads(id, label, expected) {
  if (!expected.length) return;
  const deadline = Date.now() + REQUIRED_READ_TIMEOUT_MS;
  let missing = expected;
  while (Date.now() < deadline) {
    missing = expected.filter(path => !successfulReads.get(id)?.has(path));
    if (!missing.length) return;
    await page.waitForTimeout(100);
  }
  throw new Error(
    `${label} did not complete required read-only Worker requests within `
      + `${REQUIRED_READ_TIMEOUT_MS}ms:\n${missing.join('\n')}`,
  );
}

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
page.on('request', request => {
  const section = activeSection;
  requestSections.set(request, section);
  const url = new URL(request.url());
  if (request.method() === 'GET' && url.origin === edge) {
    apiReadsStarted.get(section)?.push(url.pathname);
    if (
      section === 'contenthub'
      && url.pathname.startsWith('/api/v1/')
      && !request.headers().authorization?.startsWith('Bearer ')
    ) {
      contentReadsWithoutBearer.push(url.pathname);
    }
  }
});
page.on('response', response => {
  const responseSection = requestSections.get(response.request()) || activeSection;
  const isPostLogoutAuthProbe = isPostLogoutAuthEndpoint(response.url(), postLogoutProbe);
  const isExpectedPostLogoutDenial = isExpectedPostLogoutAuthResponse(
    response.url(),
    response.status(),
    postLogoutProbe,
  );
  if (isPostLogoutAuthProbe) {
    postLogoutAuthResponses.push(
      `${response.status()} ${response.request().method()} ${response.url()}`,
    );
  }
  if (response.status() >= 400 && !isExpectedPostLogoutDenial) {
    failedRequests.push(
      `[${responseSection}] ${response.status()} ${response.request().method()} ${response.url()}`,
    );
  }
  if ([401, 403].includes(response.status()) && !isExpectedPostLogoutDenial) {
    forbiddenRequests.push(`${response.status()} ${response.request().method()} ${response.url()}`);
  }
  if (response.ok() && response.request().method() === 'GET') {
    const url = new URL(response.url());
    successfulReads.get(responseSection)?.add(url.pathname);
  }
});
page.on('requestfailed', request => {
  const requestSection = requestSections.get(request) || activeSection;
  failedRequests.push(
    `[${requestSection}] NETWORK ${request.method()} ${request.url()} — ${request.failure()?.errorText || 'unknown error'}`,
  );
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
await page.route(`${edge}/**`, async route => {
  const headers = { ...route.request().headers() };
  for (const name of Object.keys(headers)) {
    if (name.toLowerCase().startsWith('cf-access-client-')) {
      strippedApiAccessHeaders.push(`${route.request().method()} ${route.request().url()}`);
      delete headers[name];
    }
  }
  await route.continue({ headers });
});

try {
  const login = await context.request.post(`${edge}/api/v1/auth/login`, {
    headers: { 'Content-Type': 'application/json' },
    data: {
      email: process.env.CUTOVER_STAFF_EMAIL,
      password: process.env.CUTOVER_STAFF_PASSWORD,
    },
  });
  if (!login.ok()) throw new Error(`Staff login failed with HTTP ${login.status()}`);
  const tokens = await login.json();
  addLoginTokensToRedactions(tokens, sensitiveValues);
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

  for (const [id, label, releaseCheck] of sections) {
    activeSection = id;
    await page.getByTestId(`admin-nav-${id}`).click();
    await page.waitForURL(url => url.pathname === '/staff' && url.searchParams.get('s') === id);
    await waitForRequiredReads(id, label, releaseCheck.requiredReads);
    if (await page.getByRole('heading', { name: 'Something went wrong' }).count()) {
      throw new Error(`${label} triggered the global error boundary`);
    }
    const sectionBoundary = page.getByRole('alert').filter({ hasText: /failed to load/i }).first();
    if (await sectionBoundary.count()) {
      const alert = sectionBoundary;
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
    if (!releaseCheck.supported) {
      await page.getByTestId(`admin-module-unavailable-${id}`).waitFor({ state: 'visible' });
      const unexpectedReads = apiReadsStarted.get(id) || [];
      if (unexpectedReads.length) {
        throw new Error(
          `${label} is documented as unsupported but initiated API reads:\n${unexpectedReads.join('\n')}`,
        );
      }
    }
    if (id === 'contenthub') {
      for (const tab of contentHubTabs) {
        const readsBefore = apiReadsStarted.get(id)?.length || 0;
        await page.getByTestId(`content-hub-tab-${tab.id}`).click();

        if (tab.unsupported) {
          await page.getByTestId(`admin-module-unavailable-content-${tab.id}`)
            .waitFor({ state: 'visible' });
          await page.waitForTimeout(250);
          const unexpectedReads = (apiReadsStarted.get(id) || []).slice(readsBefore);
          if (unexpectedReads.length) {
            throw new Error(
              `${tab.label} is documented as unsupported but initiated API reads:\n`
                + unexpectedReads.join('\n'),
            );
          }
          continue;
        }

        for (const path of tab.requiredReads || []) {
          const deadline = Date.now() + REQUIRED_READ_TIMEOUT_MS;
          while (Date.now() < deadline) {
            const reads = (apiReadsStarted.get(id) || []).slice(readsBefore);
            if (reads.includes(path) && successfulReads.get(id)?.has(path)) break;
            await page.waitForTimeout(100);
          }
          const reads = (apiReadsStarted.get(id) || []).slice(readsBefore);
          if (!reads.includes(path)) {
            throw new Error(`${tab.label} did not initiate required Worker read ${path}`);
          }
          if (!successfulReads.get(id)?.has(path)) {
            throw new Error(`${tab.label} did not complete required Worker read ${path}`);
          }
        }
      }
    }
    console.log(`Staff portal read passed: ${label}`);
  }

  await page.waitForLoadState('networkidle', { timeout: 5_000 }).catch(() => {});
  for (const [id, label, releaseCheck] of sections) {
    if (releaseCheck.supported) continue;
    const unexpectedReads = apiReadsStarted.get(id) || [];
    if (unexpectedReads.length) {
      throw new Error(
        `${label} is documented as unsupported but initiated API reads:\n${unexpectedReads.join('\n')}`,
      );
    }
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
  if (failedRequests.length) {
    throw new Error(`Staff portal issued failed requests:\n${failedRequests.join('\n')}`);
  }
  if (strippedApiAccessHeaders.length) {
    throw new Error(
      `Staff portal attempted to attach Cloudflare Access headers to the public API:\n${strippedApiAccessHeaders.join('\n')}`,
    );
  }
  if (contentReadsWithoutBearer.length) {
    throw new Error(
      `Content Editor issued Worker reads without bearer authentication:\n${contentReadsWithoutBearer.join('\n')}`,
    );
  }
  forbiddenRequests.length = 0;
  const declineConsent = page.getByRole('button', { name: 'Decline', exact: true });
  if (await declineConsent.isVisible().catch(() => false)) {
    await declineConsent.click();
  }
  const [logoutResponse] = await Promise.all([
    page.waitForResponse(response =>
      response.request().method() === 'POST'
      && response.url().includes('/api/v1/auth/logout')),
    page.getByRole('button', { name: 'Logout' }).click(),
  ]);
  if (!logoutResponse.ok()) {
    throw new Error(`Staff logout failed with HTTP ${logoutResponse.status()}`);
  }
  await page.waitForURL(url => url.pathname === '/login' && url.searchParams.get('next') === '/staff');
  const remainingStorageKeys = await page.evaluate(() => ({
    session: ['syrabit_token', 'syrabit_refresh_token'].filter(key => sessionStorage.getItem(key)),
    local: ['syrabit_token', 'syrabit_refresh_token'].filter(key => localStorage.getItem(key)),
  }));
  if (remainingStorageKeys.session.length || remainingStorageKeys.local.length) {
    throw new Error(`UI sign-out left authentication storage behind: ${JSON.stringify(remainingStorageKeys)}`);
  }
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
  if (!postLogoutAuthResponses.length || postLogoutAuthResponses.some(item => !item.startsWith('401 '))) {
    throw new Error(
      `Post-logout authentication probes did not consistently return 401:\n${postLogoutAuthResponses.join('\n') || '(none)'}`,
    );
  }
  if (forbiddenRequests.length || failedRequests.length) {
    throw new Error([
      forbiddenRequests.length
        ? `Unexpected forbidden requests:\n${forbiddenRequests.join('\n')}`
        : '',
      failedRequests.length
        ? `Unexpected failed requests:\n${failedRequests.join('\n')}`
        : '',
    ].filter(Boolean).join('\n\n'));
  }

  if (runtimeErrors.length) {
    throw new Error(`Staff portal emitted runtime errors:\n${runtimeErrors.join('\n')}`);
  }
  console.log('Production staff portal browser lifecycle passed.');
} catch (error) {
  await mkdir(diagnosticsDir, { recursive: true });
  const screenshotPath = resolve(diagnosticsDir, 'staff-portal-failure.png');
  const tracePath = resolve(diagnosticsDir, 'staff-portal-trace.zip');
  const rawTracePath = resolve(tmpdir(), `staff-portal-trace-${process.pid}.zip`);
  const diagnosticErrors = [];

  const pageRedacted = await page.evaluate(values => {
    const replacements = values.filter(Boolean);
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      for (const value of replacements) {
        node.textContent = node.textContent.replaceAll(value, '[REDACTED]');
      }
    }
    for (const element of document.querySelectorAll('input, textarea')) {
      for (const value of replacements) {
        element.value = element.value.replaceAll(value, '[REDACTED]');
      }
    }
    return true;
  }, [...sensitiveValues]).catch(captureError => {
    diagnosticErrors.push(`page redaction: ${captureError.message}`);
    return false;
  });
  if (pageRedacted) {
    await page.screenshot({ path: screenshotPath, fullPage: true }).catch(captureError => {
      diagnosticErrors.push(`screenshot: ${captureError.message}`);
    });
  } else {
    await rm(screenshotPath, { force: true });
  }
  try {
    await context.tracing.stop({ path: rawTracePath });
    tracingStopped = true;
    await saveRedactedTrace(rawTracePath, tracePath, [...sensitiveValues]);
  } catch (captureError) {
    diagnosticErrors.push(`trace: ${captureError.message}`);
  } finally {
    await rm(rawTracePath, { force: true });
  }

  if (diagnosticErrors.length) {
    console.error(`Failed to preserve complete staff portal diagnostics: ${diagnosticErrors.join('; ')}`);
  } else {
    console.error(`Staff portal failure diagnostics saved to ${diagnosticsDir}`);
  }
  throw error;
} finally {
  if (!tracingStopped) {
    await context.tracing.stop().catch(() => {});
  }
  await context.close();
  await browser.close();
}
