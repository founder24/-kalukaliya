import { expect, test, type Page, type Route } from '@playwright/test';

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

test.describe('live production AdSense smoke', () => {
  test.use({ serviceWorkers: 'block' });

  test.skip(
    process.env.E2E_ADS_PRODUCTION !== '1',
    'Run with E2E_ADS_PRODUCTION=1 and BASE_URL=https://syrabit.ai.',
  );

  test('reports the exact route, request hosts, slot metadata, and stale assets', async ({ page }) => {
    const attempt = Number(process.env.E2E_ADS_ASSET_SMOKE_ATTEMPT || '1');
    if (!Number.isSafeInteger(attempt) || attempt < 1) {
      throw new Error('E2E_ADS_ASSET_SMOKE_ATTEMPT must be a positive integer');
    }
    const routeUrl = new URL(PRODUCTION_ROUTE, process.env.BASE_URL || 'https://syrabit.ai');
    routeUrl.searchParams.set('__pages_asset_smoke', `${attempt}-${Date.now()}`);
    const diagnostics = attachProductionDiagnostics(page);
    const response = await page.goto(routeUrl.toString(), { waitUntil: 'domcontentloaded' });
    const html = response ? await response.text() : '';
    const hashedAssetUrls = extractHashedAssetReferences(html, response?.url() || routeUrl.toString());
    const hashedAssetChecks = await checkHashedAssetReferences(
      hashedAssetUrls,
      async (assetUrl) => {
        const cacheBustedAssetUrl = new URL(assetUrl);
        cacheBustedAssetUrl.searchParams.set('__pages_asset_smoke', `${attempt}-${Date.now()}`);
        const assetResponse = await page.context().request.get(cacheBustedAssetUrl.toString(), {
          timeout: 20_000,
          headers: {
            'Cache-Control': 'no-cache',
            Pragma: 'no-cache',
          },
        });
        return assetResponse.status();
      },
    );
    const missingHashedAssets = hashedAssetChecks.filter(
      ({ status, error }) => Boolean(error) || (status ?? 0) >= 400,
    );
    await page.waitForTimeout(3500);

    const slots = await readSlots(page);
    const report = JSON.stringify({
      attempt,
      route: routeUrl.toString(),
      finalRoute: page.url(),
      routeStatus: response?.status() ?? null,
      requestHosts: [...diagnostics.requestHosts],
      hashedAssetUrls,
      hashedAssetChecks,
      missingHashedAssets,
      slots,
      pageErrors: diagnostics.pageErrors,
      requestFailures: diagnostics.requestFailures,
      consoleErrors: diagnostics.consoleErrors,
      staleAssets: diagnostics.staleAssets,
    }, null, 2);

    expect(response?.status(), report).toBeLessThan(400);
    expect(hashedAssetUrls.length, report).toBeGreaterThan(0);
    expect(missingHashedAssets, report).toEqual([]);
    expect(diagnostics.staleAssets, report).toEqual([]);
    expect(diagnostics.pageErrors, report).toEqual([]);
    expect(slots.length, report).toBeGreaterThan(0);
    for (const slot of slots) {
      expect(slot.slot, report).toMatch(/^\d{5,20}$/);
    }
  });
});