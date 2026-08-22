import { test, expect } from '@playwright/test';

/**
 * Staff panel smoke tests — Task #256
 *
 * Verifies that all four new sidebar sections (Dashboard, Analytics, Users,
 * Conversations) render without blank screens, error boundaries, or uncaught
 * console errors after the admin-component consolidation into StaffDashboard.
 *
 * Strategy: mock every backend call so the test runs in isolation without a
 * live API. A staff-role user is injected by:
 *   1. Setting `syrabit_token` in sessionStorage so AuthContext detects a
 *      token and calls fetchMe() immediately (fast path).
 *   2. Mocking /api/v1/users/me to return a staff-role profile so
 *      StaffGuard allows the route to render.
 *   3. Mocking all admin/* and staff/* API calls with minimal valid payloads
 *      so each component can move past its loading state without crashing.
 *
 * Route registration order matters in Playwright: the MOST RECENTLY registered
 * route is matched first. Catch-alls are registered first; specific routes are
 * registered last so they take priority.
 */

// Staff user profile returned by /users/me mock
const STAFF_USER = {
  id: 'staff-001',
  email: 'staff@syrabit.com',
  name: 'Staff User',
  role: 'staff',
  plan: 'pro',
  subscription_tier: 'pro',
};

async function setupMocks(page: import('@playwright/test').Page) {
  // ── Step 1: register broad catch-alls first (lowest priority) ──────────────

  // Generic catch-all for any remaining /api/v1/* not covered below
  await page.route('**/api/v1/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({}) }),
  );

  // ── Step 2: register specific routes last (highest priority) ───────────────

  // Staff content loaded on StaffDashboard mount
  const staffContentRoutes = [
    { pattern: '**/api/v1/staff/content/boards',   body: [] },
    { pattern: '**/api/v1/staff/content/classes',  body: [] },
    { pattern: '**/api/v1/staff/content/streams',  body: [] },
    { pattern: '**/api/v1/staff/content/subjects', body: [] },
  ];
  for (const { pattern, body } of staffContentRoutes) {
    await page.route(pattern, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
    );
  }

  // AdminDashboard — all the parallel fetches in its load() call
  const adminDashboardRoutes = [
    { pattern: '**/api/v1/admin/dashboard', body: { total_users: 0, active_users: 0, total_subjects: 0, total_chapters: 0 } },
    { pattern: '**/api/v1/admin/dashboard/metrics', body: {} },
    { pattern: '**/api/v1/admin/rag/accuracy', body: {} },
    { pattern: '**/api/v1/admin/chat/fallbacks', body: { fallbacks: [] } },
    { pattern: '**/api/v1/admin/vector/stats', body: {} },
    { pattern: '**/api/v1/admin/perf/latency', body: {} },
    { pattern: '**/api/v1/admin/analytics/queries', body: { queries: [] } },
    { pattern: '**/api/v1/admin/billing/tokens', body: {} },
    { pattern: '**/api/v1/admin/monetization/funnel', body: { funnel: [] } },
    { pattern: '**/api/v1/admin/content/coverage', body: {} },
    { pattern: '**/api/v1/admin/pwa/stats', body: {} },
    { pattern: '**/api/v1/admin/analytics/bot-traffic*', body: {} },
    { pattern: '**/api/v1/admin/analytics/cf-ai-crawl-control*', body: { available: false } },
    { pattern: '**/api/v1/admin/indexnow/stats', body: {} },
    { pattern: '**/api/v1/admin/indexnow/history*', body: { history: [] } },
    { pattern: '**/api/v1/admin/seo/prewarm-coverage', body: {} },
    { pattern: '**/api/v1/admin/alerts*', body: { alerts: [], total: 0 } },
    { pattern: '**/api/v1/admin/seo/health-history*', body: { history: [] } },
    { pattern: '**/api/v1/admin/seo/health/live*', body: {} },
    { pattern: '**/api/v1/admin/notification-prefs*', body: { sound_enabled: true, push_enabled: false, chime_tone: 'default', sound_severities: [], push_severities: [] } },
    { pattern: '**/api/v1/admin/push/delivery-stats*', body: {} },
    { pattern: '**/api/v1/admin/alert-settings*', body: { channel_status: {} } },
    { pattern: '**/api/v1/admin/seo/daily-summary-dispatches*', body: { dispatches: [] } },
    { pattern: '**/api/v1/admin/kv-health*', body: { configured: false } },
    { pattern: '**/api/v1/admin/r2-storage-health*', body: { configured: false } },
    { pattern: '**/api/v1/admin/ci-status*', body: { configured: false } },
    { pattern: '**/api/v1/admin/vertex/probe-status*', body: { status: 'unknown' } },
    { pattern: '**/api/v1/admin/alerts/cooldowns*', body: { active: [], total: 0 } },
  ];

  // AdminAnalytics endpoints
  const adminAnalyticsRoutes = [
    { pattern: '**/api/v1/admin/analytics', body: { visitor_stats: {}, cf_connected: false } },
    { pattern: '**/api/v1/admin/analytics/funnel*', body: { funnel: [] } },
    { pattern: '**/api/v1/admin/analytics/content-heatmap*', body: {} },
    { pattern: '**/api/v1/admin/analytics/revenue*', body: { cohorts: {}, daily_revenue: [] } },
    { pattern: '**/api/v1/admin/analytics/predictor*', body: {} },
    { pattern: '**/api/v1/admin/analytics/ga4*', body: { connected: false } },
    { pattern: '**/api/v1/admin/analytics/cf-status*', body: { auth_ok: true, configured: true } },
    { pattern: '**/api/v1/admin/analytics/daily*', body: {} },
  ];

  // AdminUsers / AdminConversations endpoints
  const adminCrudRoutes = [
    { pattern: '**/api/v1/admin/users*', body: { users: [], total: 0 } },
    { pattern: '**/api/v1/admin/conversations/sentiment*', body: { total: 0, positive: 0, negative: 0, neutral: 0, positive_pct: 0, negative_pct: 0 } },
    { pattern: '**/api/v1/admin/conversations*', body: { data: [], total: 0 } },
    { pattern: '**/api/v1/admin/content/draft-served-subjects*', body: { subjects: [] } },
  ];

  // SEO live health (called directly, not via admin helper)
  const seoRoutes = [
    { pattern: '**/api/v1/seo/health*', body: {} },
  ];

  for (const { pattern, body } of [
    ...adminDashboardRoutes,
    ...adminAnalyticsRoutes,
    ...adminCrudRoutes,
    ...seoRoutes,
  ]) {
    await page.route(pattern, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) }),
    );
  }

  // Auth refresh — guard against token-expired retry loop
  await page.route('**/api/v1/auth/refresh*', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: 'mock-staff-token', refresh_token: 'mock-refresh' }),
    }),
  );

  // /users/me MUST be last (highest priority) — returns the staff profile that
  // lets StaffGuard pass. Must override the broad catch-all above.
  await page.route('**/api/v1/users/me', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(STAFF_USER),
    }),
  );
}

// ---------------------------------------------------------------------------
// Test suite
// ---------------------------------------------------------------------------

test.describe('Staff panel — sidebar sections', () => {
  let consoleErrors: string[] = [];

  test.beforeEach(async ({ page }) => {
    consoleErrors = [];

    // Collect uncaught console errors (filter known non-critical noise)
    page.on('console', (msg) => {
      if (msg.type() === 'error') {
        const text = msg.text();
        const ignored = [
          'favicon',
          'net::ERR_',
          'Failed to load resource',
          'ResizeObserver loop',
          'Non-Error promise rejection',
          '[vite]',
          'VITE_BACKEND_URL',
          'API requests will use relative paths',
        ];
        if (!ignored.some((s) => text.includes(s))) {
          consoleErrors.push(text);
        }
      }
    });

    await setupMocks(page);

    // Inject the token into sessionStorage BEFORE the page loads so that
    // AuthContext.hydrateTokensFromStorage() finds it and calls fetchMe()
    // immediately (hasToken=true fast path). Key: 'syrabit_token' per
    // useTokenManager.ts (safeSessionSet / safeSessionGet).
    await page.addInitScript(() => {
      sessionStorage.setItem('syrabit_token', 'mock-staff-token');
    });
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Helpers
  // ──────────────────────────────────────────────────────────────────────────

  /** Navigate to /staff and wait until the sidebar appears (guard passed). */
  async function gotoStaff(p: import('@playwright/test').Page) {
    await p.goto('/staff');
    // <aside> is only rendered when StaffGuard is satisfied (user.role===staff)
    await p.waitForSelector('aside', { timeout: 20_000 });
  }

  /** Click a sidebar nav button by its visible label. */
  async function clickSidebar(p: import('@playwright/test').Page, label: string) {
    await p.click(`aside button:has-text("${label}")`);
    // Allow async useEffect calls to fire and state to flush
    await p.waitForTimeout(400);
  }

  /**
   * Assert the section is alive: its <h2> or a loading spinner must appear,
   * and no error boundary message must be present.
   *
   * @param label     - Sidebar button label (used in error messages).
   * @param h2Text    - The actual text of the <h2> the section renders.
   *                    Defaults to `label` when omitted (Analytics/Users/Conversations).
   *                    Pass explicitly when the section's <h2> differs from the label
   *                    (e.g. Dashboard renders <h2>Overview</h2>).
   */
  async function assertSectionAlive(
    p: import('@playwright/test').Page,
    label: string,
    h2Text: string = label,
  ) {
    const main = p.locator('main');

    // Must not show a React error boundary
    const errorBoundaryCount = await main
      .locator('text=/Something went wrong|could not be loaded/i')
      .count();
    expect(
      errorBoundaryCount,
      `"${label}" must not show an error boundary message`,
    ).toBe(0);

    // Either the heading or a spinner must be visible (loaded or in-flight)
    const headingVisible = await main
      .locator(`h2:has-text("${h2Text}")`)
      .isVisible()
      .catch(() => false);
    const spinnerVisible = await main
      .locator('.animate-spin')
      .isVisible()
      .catch(() => false);

    expect(
      headingVisible || spinnerVisible,
      `"${label}" must show its heading or a loading spinner — not a blank/crashed page`,
    ).toBe(true);
  }

  // ──────────────────────────────────────────────────────────────────────────
  // Per-section tests
  // ──────────────────────────────────────────────────────────────────────────

  test('Dashboard section renders without blank page or uncaught errors', async ({ page }) => {
    await gotoStaff(page);
    await clickSidebar(page, 'Dashboard');
    // AdminDashboard renders <h2>Overview</h2> — not "Dashboard"
    await assertSectionAlive(page, 'Dashboard', 'Overview');
    expect(consoleErrors, 'No uncaught console errors on Dashboard').toHaveLength(0);
  });

  test('Analytics section renders without blank page or uncaught errors', async ({ page }) => {
    await gotoStaff(page);
    await clickSidebar(page, 'Analytics');
    await assertSectionAlive(page, 'Analytics');
    expect(consoleErrors, 'No uncaught console errors on Analytics').toHaveLength(0);
  });

  test('Users section renders without blank page or uncaught errors', async ({ page }) => {
    await gotoStaff(page);
    await clickSidebar(page, 'Users');
    await assertSectionAlive(page, 'Users');
    expect(consoleErrors, 'No uncaught console errors on Users').toHaveLength(0);
  });

  test('Conversations section renders without blank page or uncaught errors', async ({ page }) => {
    await gotoStaff(page);
    await clickSidebar(page, 'Conversations');
    await assertSectionAlive(page, 'Conversations');
    expect(consoleErrors, 'No uncaught console errors on Conversations').toHaveLength(0);
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Interactive controls test — exercises state-setter props (setSeoLive,
  // setR2Health) that were missing from ctx before the widget prop-scoping fix.
  // Clicking these controls would throw "X is not a function" if the setters
  // were not supplied via props.
  // ──────────────────────────────────────────────────────────────────────────

  test('Dashboard interactive controls (Probe now, R2 re-evaluate) complete without crashing', async ({ page }) => {
    // Override r2-storage-health to return a configured state so the
    // "Re-evaluate now" button is enabled (it's disabled when configured===false)
    await page.route('**/api/v1/admin/r2-storage-health*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          configured: true,
          state: { watchdog_blind: false, last_evaluated_at: null },
          buckets: [],
        }),
      }),
    );
    // POST endpoints triggered by the interactive controls
    await page.route('**/api/v1/admin/r2-storage-health/run', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ state: { watchdog_blind: false, last_evaluated_at: new Date().toISOString() } }),
      }),
    );
    await page.route('**/api/v1/admin/seo/health/live*', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'ok', sitemaps: [], checked_at: new Date().toISOString() }),
      }),
    );

    await gotoStaff(page);
    await clickSidebar(page, 'Dashboard');
    await assertSectionAlive(page, 'Dashboard', 'Overview');

    // ── "Probe now" — exercises setSeoLive / setSeoLiveLoading / setSeoLiveError
    const probeBtn = page.locator('[data-testid="seo-live-refresh"]');
    await probeBtn.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
    if (await probeBtn.isVisible()) {
      await probeBtn.click();
      await page.waitForTimeout(500);
      // Probe now changes to "Probing…" then back — no crash means setters worked
      const hasErrorBoundary = await page.locator('text=Something went wrong').count();
      expect(hasErrorBoundary, '"Probe now" must not trigger an error boundary').toBe(0);
    }

    // ── "Re-evaluate now" R2 — exercises setR2Health via onReevaluate callback
    const r2Btn = page.locator('[data-testid="r2-cold-storage-reevaluate"]');
    await r2Btn.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
    if (await r2Btn.isVisible() && !(await r2Btn.isDisabled())) {
      await r2Btn.click();
      await page.waitForTimeout(500);
      const hasErrorBoundary = await page.locator('text=Something went wrong').count();
      expect(hasErrorBoundary, '"Re-evaluate now" must not trigger an error boundary').toBe(0);
    }

    expect(consoleErrors, 'No uncaught console errors during interactive controls test').toHaveLength(0);
  });

  // ──────────────────────────────────────────────────────────────────────────
  // Combined navigation test
  // ──────────────────────────────────────────────────────────────────────────

  test('all four sections render correctly when cycled in sequence', async ({ page }) => {
    await gotoStaff(page);

    for (const { label, h2Text } of [
      // AdminDashboard renders <h2>Overview</h2>, not <h2>Dashboard</h2>
      { label: 'Dashboard',     h2Text: 'Overview' },
      { label: 'Analytics',     h2Text: 'Analytics' },
      { label: 'Users',         h2Text: 'Users' },
      { label: 'Conversations', h2Text: 'Conversations' },
    ]) {
      await clickSidebar(page, label);
      await assertSectionAlive(page, label, h2Text);
    }

    expect(
      consoleErrors,
      'No uncaught console errors while cycling through all four sections',
    ).toHaveLength(0);
  });
});
