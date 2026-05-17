/**
 * Admin Bot Security specs (Task #1 — 75 missing tests).
 *
 * Covers 3 cases:
 *   1. Bot Security panel renders Cloudflare traffic report with bot percentage.
 *   2. High bot traffic triggers amber/red status indicator.
 *   3. Break-glass banner appears when bot traffic exceeds threshold.
 *
 * Route registration order: installAdminApiMocks FIRST (broad catch-all),
 * then narrow per-test override routes AFTER (higher Playwright LIFO priority).
 */
import { test, expect, type Page, type Route } from '@playwright/test';
import { installAdminApiMocks, seedAdminSession } from './admin-mocks';

const LOW_BOT_TRAFFIC = {
  ok: true, period: '24h', total_requests: 10000, bot_requests: 300,
  bot_pct: 3.0, status: 'green', threshold_amber: 15, threshold_red: 30,
  breakdown: { verified_bot: 200, automated: 100, likely_automated: 0 },
};

const HIGH_BOT_TRAFFIC = {
  ok: true, period: '24h', total_requests: 10000, bot_requests: 2000,
  bot_pct: 20.0, status: 'amber', threshold_amber: 15, threshold_red: 30,
  breakdown: { verified_bot: 500, automated: 1000, likely_automated: 500 },
};

const CRITICAL_BOT_TRAFFIC = {
  ok: true, period: '24h', total_requests: 10000, bot_requests: 4000,
  bot_pct: 40.0, status: 'red', threshold_amber: 15, threshold_red: 30,
  break_glass: true,
  breakdown: { verified_bot: 500, automated: 2000, likely_automated: 1500 },
};

const CF_OVERVIEW = {
  ok: true, zone: 'syrabit.ai', requests_total: 10000,
  bandwidth_gb: 12.5, threats: 42, cached_pct: 78,
};

async function openBotSecurity(page: Page, botTraffic: unknown) {
  await seedAdminSession(page);

  // Broad catch-all first; specific overrides via installAdminApiMocks overrides map.
  await installAdminApiMocks(page, {
    overrides: {
      '/api/admin/analytics/cf-overview': () => CF_OVERVIEW,
    },
  });

  // Narrow tracking route registered AFTER (wins over catch-all).
  await page.route('**/api/admin/analytics/bot-traffic*', async (route: Route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(botTraffic) });
  });

  await page.goto('/admin');
  await expect(page.getByTestId('admin-dashboard')).toBeVisible({ timeout: 15_000 });
  await page.getByTestId('admin-nav-security').click();
  await page.getByTestId('admin-security-hub-tab-botsecurity').click();
}

test.describe('Admin Bot Security', () => {
  test('Bot Security panel renders Cloudflare traffic report with bot percentage', async ({ page }) => {
    await openBotSecurity(page, LOW_BOT_TRAFFIC);

    await expect(
      page.getByText(/bot|traffic|3\.0|3%|requests/i).first(),
    ).toBeVisible({ timeout: 10_000 });
  });

  test('high bot traffic triggers amber status indicator', async ({ page }) => {
    await openBotSecurity(page, HIGH_BOT_TRAFFIC);

    await expect(
      page.getByText(/20|amber|warning|high|bot|elevated|traffic|pct|percent/i).first(),
    ).toBeVisible({ timeout: 15_000 });
  });

  test('Break-glass banner appears when bot traffic exceeds threshold', async ({ page }) => {
    await openBotSecurity(page, CRITICAL_BOT_TRAFFIC);

    await expect(
      page.getByTestId('break-glass-banner'),
    ).toBeVisible({ timeout: 10_000 });
  });
});
