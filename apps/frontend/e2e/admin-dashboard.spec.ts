import { test, expect } from '@playwright/test';

/**
 * Admin dashboard smoke test.
 *
 * The dashboard mounts six data-heavy widgets at once. Keep this test
 * isolated from the backend by mocking the admin session and every dashboard
 * request with the smallest valid response shape. The test still goes
 * through the login page so the route is exercised as an admin would use it.
 */

const ADMIN_PROFILE = {
  name: 'Admin User',
  email: 'admin@syrabit.com',
};

async function setupMocks(page: import('@playwright/test').Page) {
  // Register the broad fallback first. Playwright gives newer routes priority,
  // so the specific responses below override this for the dashboard calls.
  await page.route('**/api/v1/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({}),
    }),
  );

  const routes = [
    // Admin authentication
    { pattern: '**/api/v1/admin/login', body: ADMIN_PROFILE },
    { pattern: '**/api/v1/admin/verify', body: ADMIN_PROFILE },

    // AdminPage shell requests
    { pattern: '**/api/v1/admin/settings', body: {} },
    { pattern: '**/api/v1/admin/alerts/unacknowledged/count', body: { count: 0 } },

    // AdminDashboard load() requests
    {
      pattern: '**/api/v1/admin/dashboard',
      body: {
        total_users: 0,
        total_conversations: 0,
        total_messages: 0,
        total_subjects: 0,
        visitor_stats: { daily_visitors: [], cloudflare: {} },
        recent_events: [],
      },
    },
    { pattern: '**/api/v1/admin/dashboard/metrics', body: { dependencies: {} } },
    { pattern: '**/api/v1/admin/rag/accuracy', body: {} },
    { pattern: '**/api/v1/admin/chat/fallbacks', body: { daily: [] } },
    { pattern: '**/api/v1/admin/vector/stats', body: { pages: {}, chapters: {} } },
    { pattern: '**/api/v1/admin/perf/latency', body: { daily: [] } },
    { pattern: '**/api/v1/admin/analytics/queries', body: { top_queries: [] } },
    { pattern: '**/api/v1/admin/billing/tokens', body: { daily: [], totals: {} } },
    { pattern: '**/api/v1/admin/monetization/funnel', body: { funnel: [] } },
    { pattern: '**/api/v1/admin/content/coverage', body: { subjects: [] } },
    { pattern: '**/api/v1/admin/pwa/stats', body: {} },
    { pattern: '**/api/v1/admin/analytics/bot-traffic*', body: {} },
    { pattern: '**/api/v1/admin/analytics/cf-ai-crawl-control*', body: { available: false } },
    { pattern: '**/api/v1/admin/indexnow/stats', body: {} },
    { pattern: '**/api/v1/admin/indexnow/history*', body: { history: [] } },
    { pattern: '**/api/v1/admin/seo/prewarm-coverage', body: {} },
    { pattern: '**/api/v1/admin/alerts*', body: { alerts: [], total: 0 } },
    { pattern: '**/api/v1/admin/seo/health-history*', body: { history: [] } },

    // Dashboard effects that run after the initial load
    { pattern: '**/api/v1/admin/analytics/cf-overview*', body: { connected: false } },
    { pattern: '**/api/v1/admin/chat/speedups*', body: { daily: [], warm_runs: [], totals: {} } },
    { pattern: '**/api/v1/admin/chat/anon-quota-exhausted*', body: { daily: [], by_hour: {}, by_day_of_week: {} } },
    { pattern: '**/api/v1/seo/health*', body: {} },
    { pattern: '**/api/v1/admin/notification-prefs*', body: { sound_enabled: true, push_enabled: false, chime_tone: 'default', sound_severities: [], push_severities: [] } },
    { pattern: '**/api/v1/admin/push/delivery-stats*', body: {} },
    { pattern: '**/api/v1/admin/alert-settings*', body: { channel_status: {} } },
    { pattern: '**/api/v1/admin/seo/daily-summary-dispatches*', body: { dispatches: [] } },
    { pattern: '**/api/v1/admin/kv-health*', body: { configured: false } },
    { pattern: '**/api/v1/admin/r2-storage-health*', body: { configured: false } },
    { pattern: '**/api/v1/admin/ci-status*', body: { configured: false } },
    { pattern: '**/api/v1/admin/vertex/probe-status*', body: { status: 'unknown' } },
    { pattern: '**/api/v1/admin/alerts/cooldowns*', body: { active: [], active_count: 0, total: 0 } },
  ];

  for (const { pattern, body } of routes) {
    await page.route(pattern, (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      }),
    );
  }
}

test.describe('Admin dashboard', () => {
  test('logs in and renders Overview without an error boundary or uncaught errors', async ({ page }) => {
    const errors: string[] = [];

    page.on('console', (message) => {
      if (message.type() === 'error') {
        const text = message.text();
        const knownNoise = [
          'favicon',
          'net::ERR_',
          'Failed to load resource',
          'ResizeObserver loop',
          'VITE_BACKEND_URL',
          'API requests will use relative paths',
        ];
        if (!knownNoise.some((fragment) => text.includes(fragment))) {
          errors.push(`console: ${text}`);
        }
      }
    });
    page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));

    await setupMocks(page);
    await page.goto('/admin/login');
    await page.getByTestId('admin-email-input').fill(ADMIN_PROFILE.email);
    await page.getByTestId('admin-password-input').fill('mock-admin-password');
    await page.getByTestId('admin-login-submit-button').click();

    await expect(page).toHaveURL(/\/admin$/);
    const main = page.locator('main');
    await expect(main.getByRole('heading', { name: 'Overview' })).toBeVisible({ timeout: 20_000 });

    await expect(main.locator('text=/Something went wrong|could not be loaded/i')).toHaveCount(0);
    expect(errors, 'The admin dashboard must not emit uncaught errors on load').toHaveLength(0);
  });
});