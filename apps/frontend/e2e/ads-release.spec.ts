import { expect, test, type BrowserContext, type Page, type Route } from '@playwright/test';

const FIXTURE_ROUTE = '/ahsec/class-12/physics/release-ad-fixture';
const PRODUCTION_ROUTE = process.env.E2E_ADS_PRODUCTION_PATH
  || '/ahsec/hs-1st-year/physics/laws-of-motion';
const GOOGLE_LOADER = '**/pagead/js/adsbygoogle.js*';

const fixtureChapter = {
  chapter_id: 'release-ad-fixture',
  chapter_title: 'Release ad verification fixture',
  title: 'Release ad verification fixture',
  topic_title: 'Release ad verification fixture',
  subject_name: 'Physics',
  subject_id: 'physics',
  board_name: 'AHSEC',
  class_name: 'Class 12',
  content_type: 'notes',
  content: '# Notes\n\nThis controlled chapter verifies ad placement behavior.',
  content_en: '# Notes\n\nThis controlled chapter verifies ad placement behavior.',
  notes_en: '# Notes\n\nThis controlled chapter verifies ad placement behavior.',
  pyq_pdf_url: 'https://example.com/release-ad-fixture.pdf',
  published_topics: [
    { topic_slug: 'topic-one', title: 'Topic one', definition: 'Definition one.' },
    { topic_slug: 'topic-two', title: 'Topic two', definition: 'Definition two.' },
    { topic_slug: 'topic-three', title: 'Topic three', definition: 'Definition three.' },
    { topic_slug: 'topic-four', title: 'Topic four', definition: 'Definition four.' },
  ],
};

function googleLoaderFixture(mode: 'fill' | 'no-fill') {
  return `
    (() => {
      const mode = ${JSON.stringify(mode)};
      const fill = () => {
        if (mode !== 'fill') return;
        document.querySelectorAll('ins.adsbygoogle').forEach((slot) => {
          if (slot.querySelector('iframe[data-ad-fixture-fill]')) return;
          const iframe = document.createElement('iframe');
          iframe.title = 'Advertisement';
          iframe.src = 'about:blank';
          iframe.dataset.adFixtureFill = 'true';
          iframe.style.display = 'block';
          iframe.style.width = '100%';
          iframe.style.height = '90px';
          slot.appendChild(iframe);
        });
      };
      const queued = Array.isArray(window.adsbygoogle) ? window.adsbygoogle : [];
      window.adsbygoogle = {
        push() {
          fill();
          return 1;
        },
      };
      new MutationObserver(fill).observe(document.documentElement, {
        childList: true,
        subtree: true,
      });
      queued.forEach(() => fill());
      fill();
    })();
  `;
}

async function fulfillGoogleLoader(route: Route, mode: 'fill' | 'no-fill') {
  await route.fulfill({
    status: 200,
    contentType: 'application/javascript',
    body: googleLoaderFixture(mode),
  });
}

async function installFixture(page: Page, mode: 'fill' | 'no-fill') {
  await page.addInitScript(() => {
    localStorage.removeItem('syrabit_ads_optout');
    // Make every lazy slot eligible immediately. This only affects the
    // deterministic fixture; the production smoke keeps the browser native.
    window.IntersectionObserver = class {
      callback: IntersectionObserverCallback;
      constructor(callback: IntersectionObserverCallback) {
        this.callback = callback;
      }
      observe(target: Element) {
        this.callback([{
          isIntersecting: true,
          intersectionRatio: 1,
          target,
        } as IntersectionObserverEntry], this as unknown as IntersectionObserver);
      }
      disconnect() {}
      unobserve() {}
      takeRecords() { return []; }
      root = null;
      rootMargin = '0px';
      thresholds = [0];
    } as unknown as typeof IntersectionObserver;
  });

  await page.route(GOOGLE_LOADER, (route) => fulfillGoogleLoader(route, mode));
  await page.route('**/api/v1/**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({}),
  }));
  await page.route('**/api/v1/users/me', (route) => route.fulfill({
    status: 401,
    contentType: 'application/json',
    body: JSON.stringify({ detail: 'Not authenticated' }),
  }));
  await page.route('**/api/v1/content/chapter-by-slug/**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(fixtureChapter),
  }));
  await page.route('**/api/v1/content/chapters/release-ad-fixture/topic-pyqs**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ total: 0, pyqs: [], mark_wise: {} }),
  }));
  await page.route('**/api/v1/content/chapters/release-ad-fixture/topics-published**', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ topics: fixtureChapter.published_topics }),
  }));
  await page.route('**/api/v1/content/chapters/release-ad-fixture/pyq-images', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ papers: [] }),
  }));
  await page.route('**/api/v1/content/chapters/release-ad-fixture/faq-jsonld', (route) => route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ entries: [] }),
  }));
}

async function readSlots(page: Page) {
  return page.locator('[data-ad-placement] ins.adsbygoogle').evaluateAll((slots) => slots.map((slot) => {
    const parent = slot.parentElement;
    const parentRect = parent?.getBoundingClientRect();
    const rect = slot.getBoundingClientRect();
    return {
      placement: parent?.getAttribute('data-ad-placement') || '',
      slot: slot.getAttribute('data-ad-slot') || '',
      format: slot.getAttribute('data-ad-format') || '',
      layout: slot.getAttribute('data-ad-layout') || '',
      height: rect.height,
      parentHeight: parentRect?.height || 0,
      iframeCount: slot.querySelectorAll('iframe').length,
    };
  }));
}

async function expectFixtureTab(
  page: Page,
  tab: 'notes' | 'qa' | 'pyq',
  expectedPlacements: string[],
  mode: 'fill' | 'no-fill',
) {
  await page.goto(`${FIXTURE_ROUTE}?tab=${tab}`);
  await expect.poll(() => readSlots(page), { timeout: 5000 }).toHaveLength(expectedPlacements.length);

  const slots = await readSlots(page);
  expect(slots.map((slot) => slot.placement)).toEqual(expectedPlacements);
  for (const slot of slots) {
    expect(slot.slot, `${tab} ${slot.placement} slot metadata`).toMatch(/^\d{5,20}$/);
    expect(slot.format, `${tab} ${slot.placement} format metadata`).toBeTruthy();
    if (mode === 'no-fill' && slot.format === 'fluid') {
      expect(slot.iframeCount, `${tab} ${slot.placement} no-fill iframe count`).toBe(0);
      expect(slot.height, `${tab} ${slot.placement} fluid height`).toBe(0);
      expect(slot.parentHeight, `${tab} ${slot.placement} fluid parent height`).toBe(0);
    }
    if (mode === 'fill') {
      expect(slot.iframeCount, `${tab} ${slot.placement} supplied fill`).toBeGreaterThan(0);
    }
  }
}

test.describe('deterministic AdSense release fixture', () => {
  test.skip(
    process.env.E2E_ADS_RELEASE !== '1',
    'Run with test:e2e:ads-release after the production-shaped client build.',
  );

  test('proves numeric Notes, Q&A, and PYQ metadata and collapses no-fill fluid slots', async ({ page }) => {
    await installFixture(page, 'no-fill');
    await expectFixtureTab(
      page,
      'notes',
      ['chapter.notes.top', 'chapter.notes.inContent', 'chapter.notes.end', 'chapter.sidebar'],
      'no-fill',
    );
    await expectFixtureTab(
      page,
      'qa',
      ['chapter.qa.inContent', 'chapter.qa.end', 'chapter.sidebar'],
      'no-fill',
    );
    await expectFixtureTab(
      page,
      'pyq',
      ['chapter.pyq.top', 'chapter.pyq.inContent', 'chapter.sidebar'],
      'no-fill',
    );
  });

  test('detects a supplied iframe fill without interacting with the ad', async ({ page }) => {
    await installFixture(page, 'fill');
    await expectFixtureTab(
      page,
      'notes',
      ['chapter.notes.top', 'chapter.notes.inContent', 'chapter.notes.end', 'chapter.sidebar'],
      'fill',
    );
  });
});


function attachProductionDiagnostics(page: Page) {
  const pageErrors: string[] = [];
  const requestFailures: string[] = [];
  const staleAssets: string[] = [];
  const consoleErrors: string[] = [];
  const requestHosts = new Set<string>();

  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => requestHosts.add(new URL(request.url()).host));
  page.on('requestfailed', (request) => {
    requestFailures.push(`${request.method()} ${request.url()} — ${request.failure()?.errorText || 'failed'}`);
  });
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('response', (response) => {
    if (response.status() === 404 && new URL(response.url()).pathname.includes('/assets/')) {
      staleAssets.push(`${response.status()} ${response.url()}`);
    }
  });

  return { pageErrors, requestFailures, staleAssets, consoleErrors, requestHosts };
}

function extractHashedAssetReferences(html: string, documentUrl: string): string[] {
  const assets = new Set<string>();
  for (const match of html.matchAll(/\s(?:src|href)=["'](\/assets\/[^"'?#]+)(?:\?[^"']*)?["']/gi)) {
    assets.add(new URL(match[1], documentUrl).toString());
  }
  return [...assets];
}

async function checkHashedAssetReferences(
  assetUrls: string[],
  fetchStatus: (assetUrl: string) => Promise<number>,
) {
  return Promise.all(assetUrls.map(async (assetUrl) => {
    try {
      return { assetUrl, status: await fetchStatus(assetUrl) };
    } catch (error) {
      return {
        assetUrl,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }));
}

function positiveIntegerEnv(name: string, fallback: number, maximum: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw === '') return fallback;
  if (!/^\d+$/.test(raw)) {
    throw new Error(`${name} must be a positive integer`);
  }
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < 1 || value > maximum) {
    throw new Error(`${name} must be between 1 and ${maximum}`);
  }
  return value;
}

async function pollUntilHealthy<T extends { passed: boolean }>(
  deadlineMs: number,
  pollIntervalMs: number,
  runAttempt: (attempt: number, deadlineAt: number) => Promise<T>,
  now: () => number = Date.now,
  sleep: (durationMs: number) => Promise<void> = (durationMs) =>
    new Promise((resolve) => setTimeout(resolve, durationMs)),
) {
  const deadlineAt = now() + deadlineMs;
  const attempts: T[] = [];
  let latePassDiscarded = false;
  while (attempts.length === 0 || now() < deadlineAt) {
    const report = await runAttempt(attempts.length + 1, deadlineAt);
    attempts.push(report);
    if (report.passed && now() <= deadlineAt) {
      return { passed: true, attempts, latePassDiscarded: false };
    }
    if (report.passed) latePassDiscarded = true;

    const remainingMs = deadlineAt - now();
    if (remainingMs <= 0) break;
    await sleep(Math.min(pollIntervalMs, remainingMs));
  }
  return { passed: false, attempts, latePassDiscarded };
}

test('finds and checks every distinct root-relative hashed asset in the document', async () => {
  const documentUrl = 'https://syrabit.ai/ahsec/physics/laws-of-motion';
  const html = [
    '<script type="module" src="/assets/index-a1b2c3d4.js"></script>',
    '<link rel="modulepreload" href="/assets/vendor-e5f6g7h8.js">',
    '<link rel="stylesheet" href="/assets/index-i9j0k1l2.css?build=1">',
    '<script src="/assets/index-a1b2c3d4.js"></script>',
    '<script src="https://pagead2.googlesyndication.com/ads.js"></script>',
  ].join('');
  const assetUrls = extractHashedAssetReferences(html, documentUrl);
  const requested: string[] = [];
  const checks = await checkHashedAssetReferences(assetUrls, async (assetUrl) => {
    requested.push(assetUrl);
    return assetUrl.endsWith('/assets/vendor-e5f6g7h8.js') ? 404 : 200;
  });

  expect(assetUrls).toEqual([
    'https://syrabit.ai/assets/index-a1b2c3d4.js',
    'https://syrabit.ai/assets/vendor-e5f6g7h8.js',
    'https://syrabit.ai/assets/index-i9j0k1l2.css',
  ]);
  expect(requested).toEqual(assetUrls);
  expect(checks.filter(({ status, error }) => error || (status ?? 0) >= 400)).toEqual([
    { assetUrl: 'https://syrabit.ai/assets/vendor-e5f6g7h8.js', status: 404 },
  ]);
});

test('retries stale asset observations and retains them when a later document is healthy', async () => {
  let now = 0;
  const result = await pollUntilHealthy(
    100,
    15,
    async (attempt) => {
      now += 10;
      return attempt === 1
        ? { passed: false, staleAssets: ['404 https://syrabit.ai/assets/old-hash.js'] }
        : { passed: true, staleAssets: [] };
    },
    () => now,
    async (durationMs) => { now += durationMs; },
  );

  expect(result.passed).toBe(true);
  expect(result.attempts).toHaveLength(2);
  expect(result.attempts[0].staleAssets).toEqual([
    '404 https://syrabit.ai/assets/old-hash.js',
  ]);
});

test('stops at the deadline and keeps the latest missing-asset diagnostic', async () => {
  let now = 0;
  const result = await pollUntilHealthy(
    60,
    10,
    async (attempt) => {
      now += 25;
      return {
        attempt,
        passed: false,
        missingHashedAssets: [{ assetUrl: 'https://syrabit.ai/assets/old-hash.js', status: 404 }],
      };
    },
    () => now,
    async (durationMs) => { now += durationMs; },
  );

  expect(result.passed).toBe(false);
  expect(result.attempts).toHaveLength(2);
  expect(result.attempts.at(-1)?.missingHashedAssets).toEqual([
    { assetUrl: 'https://syrabit.ai/assets/old-hash.js', status: 404 },
  ]);
});

test('does not accept a healthy document check completed after the deadline', async () => {
  let now = 0;
  const result = await pollUntilHealthy(
    60,
    10,
    async () => {
      now = 61;
      return { passed: true };
    },
    () => now,
    async (durationMs) => { now += durationMs; },
  );

  expect(result.passed).toBe(false);
  expect(result.latePassDiscarded).toBe(true);
});

test.describe('live production AdSense smoke', () => {
  test.skip(
    process.env.E2E_ADS_PRODUCTION !== '1',
    'Run with E2E_ADS_PRODUCTION=1 and BASE_URL=https://syrabit.ai.',
  );

  test('polls a fresh production document and its hashed assets until the deadline', async ({ browser }) => {
    const deadlineMs = positiveIntegerEnv(
      'E2E_ADS_ASSET_SMOKE_DEADLINE_MS',
      240_000,
      300_000,
    );
    const pollIntervalMs = positiveIntegerEnv(
      'E2E_ADS_ASSET_SMOKE_POLL_MS',
      15_000,
      60_000,
    );
    test.setTimeout(deadlineMs + 60_000);

    const baseRouteUrl = new URL(PRODUCTION_ROUTE, process.env.BASE_URL || 'https://syrabit.ai');
    const result = await pollUntilHealthy(
      deadlineMs,
      pollIntervalMs,
      async (attempt, deadlineAt) => {
        const routeUrl = new URL(baseRouteUrl.toString());
        routeUrl.searchParams.set('__pages_asset_smoke', `${attempt}-${Date.now()}`);

        let context: BrowserContext | undefined;
        let diagnostics: ReturnType<typeof attachProductionDiagnostics> | undefined;
        const attemptReport = {
          attempt,
          freshContext: true,
          serviceWorkers: 'block',
          route: routeUrl.toString(),
          finalRoute: null as string | null,
          routeStatus: null as number | null,
          requestHosts: [] as string[],
          hashedAssetUrls: [] as string[],
          hashedAssetChecks: [] as Awaited<ReturnType<typeof checkHashedAssetReferences>>,
          missingHashedAssets: [] as Awaited<ReturnType<typeof checkHashedAssetReferences>>,
          slots: [] as Awaited<ReturnType<typeof readSlots>>,
          pageErrors: [] as string[],
          requestFailures: [] as string[],
          consoleErrors: [] as string[],
          staleAssets: [] as string[],
          attemptError: null as string | null,
          contextCloseError: null as string | null,
          passed: false,
        };

        try {
          context = await browser.newContext({
            serviceWorkers: 'block',
            extraHTTPHeaders: {
              'Cache-Control': 'no-cache',
              Pragma: 'no-cache',
            },
          });
          const page = await context.newPage();
          diagnostics = attachProductionDiagnostics(page);
          const response = await page.goto(routeUrl.toString(), {
            waitUntil: 'domcontentloaded',
            timeout: Math.max(1, Math.min(20_000, deadlineAt - Date.now())),
          });
          attemptReport.routeStatus = response?.status() ?? null;
          attemptReport.finalRoute = response?.url() ?? page.url();
          const html = response ? await response.text() : '';
          attemptReport.hashedAssetUrls = extractHashedAssetReferences(
            html,
            attemptReport.finalRoute || routeUrl.toString(),
          );
          attemptReport.hashedAssetChecks = await checkHashedAssetReferences(
            attemptReport.hashedAssetUrls,
            async (assetUrl) => {
              const cacheBustedAssetUrl = new URL(assetUrl);
              cacheBustedAssetUrl.searchParams.set(
                '__pages_asset_smoke',
                `${attempt}-${Date.now()}`,
              );
              const assetResponse = await context!.request.get(cacheBustedAssetUrl.toString(), {
                timeout: Math.max(1, Math.min(15_000, deadlineAt - Date.now())),
                headers: {
                  'Cache-Control': 'no-cache',
                  Pragma: 'no-cache',
                },
              });
              return assetResponse.status();
            },
          );
          attemptReport.missingHashedAssets = attemptReport.hashedAssetChecks.filter(
            ({ status, error }) => Boolean(error) || (status ?? 0) >= 400,
          );
          const candidateAssetsHealthy = attemptReport.routeStatus !== null
            && attemptReport.routeStatus < 400
            && attemptReport.hashedAssetUrls.length > 0
            && attemptReport.missingHashedAssets.length === 0;
          const settleMs = candidateAssetsHealthy ? 3500 : 750;
          await page.waitForTimeout(Math.max(0, Math.min(settleMs, deadlineAt - Date.now())));
          attemptReport.slots = await readSlots(page);
        } catch (error) {
          attemptReport.attemptError = error instanceof Error ? error.message : String(error);
        } finally {
          if (context) {
            try {
              await context.close();
            } catch (error) {
              attemptReport.contextCloseError = error instanceof Error
                ? error.message
                : String(error);
              attemptReport.passed = false;
            }
          }
          if (diagnostics) {
            attemptReport.requestHosts = [...diagnostics.requestHosts];
            attemptReport.pageErrors = [...diagnostics.pageErrors];
            attemptReport.requestFailures = [...diagnostics.requestFailures];
            attemptReport.consoleErrors = [...diagnostics.consoleErrors];
            attemptReport.staleAssets = [...diagnostics.staleAssets];
          }
        }

        const validSlots = attemptReport.slots.length > 0
          && attemptReport.slots.every(
            (slot) => typeof slot.slot === 'string' && /^\d{5,20}$/.test(slot.slot),
          );
        attemptReport.passed = attemptReport.attemptError === null
          && attemptReport.contextCloseError === null
          && diagnostics !== undefined
          && attemptReport.routeStatus !== null
          && attemptReport.routeStatus < 400
          && attemptReport.hashedAssetUrls.length > 0
          && attemptReport.missingHashedAssets.length === 0
          && attemptReport.staleAssets.length === 0
          && attemptReport.pageErrors.length === 0
          && validSlots;
        console.log(`[live-ad-smoke] attempt ${attempt}: ${JSON.stringify(attemptReport)}`);
        return attemptReport;
      },
    );

    const report = JSON.stringify({
      route: baseRouteUrl.toString(),
      deadline_ms: deadlineMs,
      poll_interval_ms: pollIntervalMs,
      passed: result.passed,
      late_pass_discarded: result.latePassDiscarded,
      attempts: result.attempts,
      latest_attempt: result.attempts.at(-1) ?? null,
    }, null, 2);
    console.log(`[live-ad-smoke] polling summary: ${report}`);
    expect(result.passed, report).toBe(true);
  });
});