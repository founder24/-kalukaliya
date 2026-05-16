/**
 * AdminHealth alert-state caption e2e coverage (Task #919).
 *
 * Task #902 already gives this surface solid lower-layer coverage:
 *   - backend contract: tests/test_admin_health_alert_state.py
 *   - frontend formatter: cronCaptionHelpers.test.js
 *   - per-pill render: CronHealthPill.test.jsx
 * What was missing is an end-to-end check that the three layers
 * (the `/admin/health/<pill>/cron/alert-state` route, AdminHealth's
 * loadXxxCronAlertState fetch wiring, and CronHealthPill's
 * `<prefix>-alert-state` caption render) actually stitch together
 * inside the live dashboard. The unit tests can't catch:
 *   - a renamed testId on the pill side that AdminHealth still
 *     selects via the old name,
 *   - AdminHealth dropping one of the three loaders from the
 *     polling useEffect's dependency array,
 *   - the alert-state fetch silently 404-ing because the wrong
 *     endpoint string was passed to axios.
 * This spec mocks the AdminHealth cron + alert-state endpoints with
 * fixtures that mimic what the backend would emit when an alerter
 * lock doc IS seeded ("last paged 2h ago" + optionally "in debounce
 * ~3h remaining"), navigates as an authenticated admin, and asserts
 * the caption renders next to the right pill with the right text.
 *
 * Mocking the API rather than seeding Mongo directly keeps this
 * spec aligned with admin-health-cron-pills.spec.ts and the rest of
 * the suite — the backend's read path is already covered by the
 * pytest contract tests (`test_admin_health_alert_state.py`), so
 * duplicating Mongo seeding would only re-test the pytest fixture.
 */
import { test, expect, type Page } from '@playwright/test';
import { installAdminApiMocks, seedAdminSession } from './admin-mocks';

const EDGE_PROXY_PREFIX = 'edge-proxy-deploy-cron';
const CF_WAF_DRIFT_PREFIX = 'cf-waf-drift-cron';
const TRUSTPILOT_PREFIX = 'trustpilot-refresh-cron';
const UNIFIED_LOGS_CF_PULL_PREFIX = 'unified-logs-cf-pull-cron';

const EDGE_PROXY_CRON_ENDPOINT = '/api/admin/health/edge-proxy-deploy/cron';
const CF_WAF_DRIFT_CRON_ENDPOINT = '/api/admin/health/cf-waf-drift/cron';
const TRUSTPILOT_CRON_ENDPOINT = '/api/admin/health/trustpilot/refresh-cron';
const UNIFIED_LOGS_CF_PULL_CRON_ENDPOINT =
  '/api/admin/health/unified-logs/cf-pull/cron';

const EDGE_PROXY_ALERT_STATE_ENDPOINT =
  '/api/admin/health/edge-proxy-deploy/cron/alert-state';
const CF_WAF_DRIFT_ALERT_STATE_ENDPOINT =
  '/api/admin/health/cf-waf-drift/cron/alert-state';
const TRUSTPILOT_ALERT_STATE_ENDPOINT =
  '/api/admin/health/trustpilot/refresh-cron/alert-state';
const UNIFIED_LOGS_CF_PULL_ALERT_STATE_ENDPOINT =
  '/api/admin/health/unified-logs/cf-pull/cron/alert-state';

// Healthy cron payloads — copy the same shapes the cron-pill
// e2e suite uses (admin-health-cron-pills.spec.ts) so the pill
// itself renders in green and we're cleanly testing only the
// alert-state caption layered on top, not a colour-mapping or
// loading-state regression.
const baseEdgeProxyHealthy = {
  configured: true,
  status: 'healthy',
  conclusion: 'success',
  html_url: 'https://github.com/syrabit/syrabit/actions/runs/777',
  lastRunUrl: 'https://github.com/syrabit/syrabit/actions/runs/777',
  updated_at: '2026-04-25T10:00:00Z',
  ageSeconds: 3600,
  runStatus: 'completed',
  workflowUrl:
    'https://github.com/syrabit/syrabit/actions/workflows/edge-proxy-deploy.yml',
  staleThresholdSeconds: 7 * 86400,
  error: null,
};

const baseCfWafDriftHealthy = {
  configured: true,
  status: 'healthy',
  lastHeartbeatAgeSeconds: 1800,
  lastSuccessHeartbeatAgeSeconds: 1800,
  lastRunUrl: 'https://github.com/syrabit/syrabit/actions/runs/555',
  workflowUrl:
    'https://github.com/syrabit/syrabit/actions/workflows/cf-waf-drift-daily.yml',
  staleThresholdSeconds: 36 * 3600,
  error: null,
};

const baseTrustpilotHealthy = {
  configured: true,
  status: 'healthy',
  lastHeartbeatAgeSeconds: 1800,
  lastSuccessHeartbeatAgeSeconds: 1800,
  workflowUrl:
    'https://github.com/syrabit/syrabit/actions/workflows/trustpilot-aggregate-refresh.yml',
  staleThresholdSeconds: 36 * 3600,
  error: null,
};

// Task #956 — healthy fixture for the unified-logs Cloudflare
// GraphQL pull silence pill. Same shape as the other base*Healthy
// fixtures in this file so the pill renders green and the
// alert-state caption is the only thing under test.
const baseUnifiedLogsCfPullHealthy = {
  configured: true,
  status: 'healthy',
  lastUpdatedTs: 1_700_000_000,
  lastUpdatedAt: '2026-04-26T05:00:00Z',
  lastUpdatedAgeSeconds: 1800,
  leaseOwner: 'replica-A',
  leaseExpiresAt: '2026-04-26T05:30:00Z',
  cursor: 'cursor-xyz',
  silentThresholdSeconds: 900,
  statusUrl: '/api/admin/logs/status',
};

// Default alert-state shape mimicking
// `_build_alert_state_response` for an alerter that paged 2h ago
// and has now exited the debounce window. Per cronCaptionHelpers
// the caption renders as "last paged 2h ago" (no debounce suffix).
const pagedNotInDebounce = {
  present: true,
  lastAlertAt: '2026-04-26T03:00:00Z',
  lastAlertAgeSeconds: 7200,        // → "2h"
  inDebounce: false,
  debounceRemainingSeconds: null,
  realertIntervalSeconds: 6 * 3600,
};

// Same alerter, but now still inside the debounce window so the
// caption appends " · in debounce ~3h remaining".
const pagedInDebounce = {
  present: true,
  lastAlertAt: '2026-04-26T03:00:00Z',
  lastAlertAgeSeconds: 7200,        // → "2h"
  inDebounce: true,
  debounceRemainingSeconds: 10800,  // → "3h"
  realertIntervalSeconds: 6 * 3600,
};

// No alerter lock doc was ever written (brand-new deployment, or
// the alerter has never paged). Per `formatAlertStateCaption` we
// short-circuit to null and the caption row must not appear.
const neverPaged = {
  present: false,
  lastAlertAt: null,
  lastAlertAgeSeconds: null,
  inDebounce: false,
  debounceRemainingSeconds: null,
  realertIntervalSeconds: 6 * 3600,
};

async function openAdminHealth(page: Page) {
  await page.goto('/admin');
  await expect(page.getByTestId('admin-dashboard')).toBeVisible({ timeout: 30_000 });
  await page.getByTestId('admin-nav-health').click();
}

test.describe('AdminHealth alert-state caption', () => {
  // Cron pills and alert-state captions are lazy-loaded and need extra time in CI.
  test.setTimeout(60_000);

  test('edge-proxy-deploy pill shows the "last paged Xh ago" caption when the alerter lock doc is seeded', async ({ page }) => {
    await seedAdminSession(page);
    await installAdminApiMocks(page, {
      overrides: {
        [EDGE_PROXY_CRON_ENDPOINT]: () => baseEdgeProxyHealthy,
        [EDGE_PROXY_ALERT_STATE_ENDPOINT]: () => pagedNotInDebounce,
      },
    });

    // Wait for the alert-state GET to fire — proves AdminHealth's
    // `loadEdgeProxyDeployCronAlertState` ran and the polling
    // useEffect's dependency array still includes it.
    // waitForRequest (not waitForResponse) fires when the browser
    // issues the request, before the mock route handler responds,
    // so it is unaffected by any response-detection timing issue.
    await Promise.all([
      openAdminHealth(page),
      page.waitForRequest(
        (req) => req.url().includes(EDGE_PROXY_ALERT_STATE_ENDPOINT),
        { timeout: 55_000 },
      ),
    ]);

    const tile = page.getByTestId(`${EDGE_PROXY_PREFIX}-tile`);
    await expect(tile).toBeVisible({ timeout: 25_000 });

    const caption = tile.getByTestId(`${EDGE_PROXY_PREFIX}-alert-state`);
    await expect(caption).toBeVisible({ timeout: 25_000 });
    await expect(caption).toHaveText('last paged 2h ago');
  });

  test('edge-proxy-deploy caption appends "in debounce ~Yh remaining" while inside the realert window', async ({ page }) => {
    // Same lock doc but still inside the realert debounce window —
    // admins need to be able to tell apart "I can re-page now"
    // from "the next page is auto-suppressed for another Yh".
    await seedAdminSession(page);
    await installAdminApiMocks(page, {
      overrides: {
        [EDGE_PROXY_CRON_ENDPOINT]: () => baseEdgeProxyHealthy,
        [EDGE_PROXY_ALERT_STATE_ENDPOINT]: () => pagedInDebounce,
      },
    });

    await openAdminHealth(page);

    const caption = page
      .getByTestId(`${EDGE_PROXY_PREFIX}-tile`)
      .getByTestId(`${EDGE_PROXY_PREFIX}-alert-state`);
    await expect(caption).toBeVisible({ timeout: 25_000 });
    await expect(caption).toHaveText('last paged 2h ago · in debounce ~3h remaining');
  });

  test('cf-waf-drift pill shows the "last paged Xh ago" caption when the alerter lock doc is seeded', async ({ page }) => {
    await seedAdminSession(page);
    await installAdminApiMocks(page, {
      overrides: {
        [CF_WAF_DRIFT_CRON_ENDPOINT]: () => baseCfWafDriftHealthy,
        [CF_WAF_DRIFT_ALERT_STATE_ENDPOINT]: () => pagedNotInDebounce,
      },
    });

    await Promise.all([
      openAdminHealth(page),
      page.waitForRequest(
        (req) => req.url().includes(CF_WAF_DRIFT_ALERT_STATE_ENDPOINT),
        { timeout: 55_000 },
      ),
    ]);

    const caption = page
      .getByTestId(`${CF_WAF_DRIFT_PREFIX}-tile`)
      .getByTestId(`${CF_WAF_DRIFT_PREFIX}-alert-state`);
    await expect(caption).toBeVisible({ timeout: 25_000 });
    await expect(caption).toHaveText('last paged 2h ago');
  });

  test('trustpilot-refresh-cron pill shows the "last paged Xh ago" caption when the alerter lock doc is seeded', async ({ page }) => {
    await seedAdminSession(page);
    await installAdminApiMocks(page, {
      overrides: {
        [TRUSTPILOT_CRON_ENDPOINT]: () => baseTrustpilotHealthy,
        [TRUSTPILOT_ALERT_STATE_ENDPOINT]: () => pagedNotInDebounce,
      },
    });

    await Promise.all([
      openAdminHealth(page),
      page.waitForRequest(
        (req) => req.url().includes(TRUSTPILOT_ALERT_STATE_ENDPOINT),
        { timeout: 55_000 },
      ),
    ]);

    const caption = page
      .getByTestId(`${TRUSTPILOT_PREFIX}-tile`)
      .getByTestId(`${TRUSTPILOT_PREFIX}-alert-state`);
    await expect(caption).toBeVisible({ timeout: 25_000 });
    await expect(caption).toHaveText('last paged 2h ago');
  });

  test('unified-logs-cf-pull pill shows the "last paged Xh ago" caption when the alerter lock doc is seeded', async ({ page }) => {
    // Task #956 — same contract as the sibling alerters above:
    // when the unified-logs CF pull silence alerter has paged
    // within the realert window, the pill must surface the
    // "last paged Xh ago" caption sourced from
    // /admin/health/unified-logs/cf-pull/cron/alert-state.
    await seedAdminSession(page);
    await installAdminApiMocks(page, {
      overrides: {
        [UNIFIED_LOGS_CF_PULL_CRON_ENDPOINT]: () => baseUnifiedLogsCfPullHealthy,
        [UNIFIED_LOGS_CF_PULL_ALERT_STATE_ENDPOINT]: () => pagedNotInDebounce,
      },
    });

    await Promise.all([
      openAdminHealth(page),
      page.waitForRequest(
        (req) => req.url().includes(UNIFIED_LOGS_CF_PULL_ALERT_STATE_ENDPOINT),
        { timeout: 55_000 },
      ),
    ]);

    const caption = page
      .getByTestId(`${UNIFIED_LOGS_CF_PULL_PREFIX}-tile`)
      .getByTestId(`${UNIFIED_LOGS_CF_PULL_PREFIX}-alert-state`);
    await expect(caption).toBeVisible({ timeout: 25_000 });
    await expect(caption).toHaveText('last paged 2h ago');
  });

  test('caption row is omitted on every pill when no alerter lock doc has been written', async ({ page }) => {
    // present:false from the backend means the alerter has never
    // paged — the caption short-circuits to null in the formatter
    // and the `<prefix>-alert-state` testId must not appear.
    // This also asserts the absence is uniform across all three
    // pills, so a future regression that accidentally renders an
    // empty "last paged: just now" line under one pill but not the
    // others would fail here.
    await seedAdminSession(page);
    await installAdminApiMocks(page, {
      overrides: {
        [EDGE_PROXY_CRON_ENDPOINT]: () => baseEdgeProxyHealthy,
        [CF_WAF_DRIFT_CRON_ENDPOINT]: () => baseCfWafDriftHealthy,
        [TRUSTPILOT_CRON_ENDPOINT]: () => baseTrustpilotHealthy,
        [UNIFIED_LOGS_CF_PULL_CRON_ENDPOINT]: () => baseUnifiedLogsCfPullHealthy,
        [EDGE_PROXY_ALERT_STATE_ENDPOINT]: () => neverPaged,
        [CF_WAF_DRIFT_ALERT_STATE_ENDPOINT]: () => neverPaged,
        [TRUSTPILOT_ALERT_STATE_ENDPOINT]: () => neverPaged,
        [UNIFIED_LOGS_CF_PULL_ALERT_STATE_ENDPOINT]: () => neverPaged,
      },
    });

    // Open AdminHealth and wait for all four alert-state GETs to fire.
    // Asserting that all four actually fire closes the false-negative gap
    // where a typo in the endpoint wiring would 404 and silently satisfy
    // the `toHaveCount(0)` check below for the wrong reason.
    // waitForRequest fires when the browser issues the request (before
    // the mock responds) — more reliable than waitForResponse for mocked
    // routes and semantically correct: we are asserting the loaders run.
    await Promise.all([
      openAdminHealth(page),
      page.waitForRequest(
        (req) => req.url().includes(EDGE_PROXY_ALERT_STATE_ENDPOINT),
        { timeout: 55_000 },
      ),
      page.waitForRequest(
        (req) => req.url().includes(CF_WAF_DRIFT_ALERT_STATE_ENDPOINT),
        { timeout: 55_000 },
      ),
      page.waitForRequest(
        (req) => req.url().includes(TRUSTPILOT_ALERT_STATE_ENDPOINT),
        { timeout: 55_000 },
      ),
      page.waitForRequest(
        (req) => req.url().includes(UNIFIED_LOGS_CF_PULL_ALERT_STATE_ENDPOINT),
        { timeout: 55_000 },
      ),
    ]);

    // Wait for at least one pill to render before asserting the
    // captions are absent — otherwise a slow load could pass the
    // negative assertion before the page has even hydrated.
    await expect(page.getByTestId(`${EDGE_PROXY_PREFIX}-tile`)).toBeVisible({ timeout: 25_000 });
    await expect(page.getByTestId(`${CF_WAF_DRIFT_PREFIX}-tile`)).toBeVisible({ timeout: 25_000 });
    await expect(page.getByTestId(`${TRUSTPILOT_PREFIX}-tile`)).toBeVisible({ timeout: 25_000 });
    await expect(page.getByTestId(`${UNIFIED_LOGS_CF_PULL_PREFIX}-tile`)).toBeVisible({ timeout: 25_000 });

    await expect(page.getByTestId(`${EDGE_PROXY_PREFIX}-alert-state`)).toHaveCount(0);
    await expect(page.getByTestId(`${CF_WAF_DRIFT_PREFIX}-alert-state`)).toHaveCount(0);
    await expect(page.getByTestId(`${TRUSTPILOT_PREFIX}-alert-state`)).toHaveCount(0);
    await expect(page.getByTestId(`${UNIFIED_LOGS_CF_PULL_PREFIX}-alert-state`)).toHaveCount(0);
  });
});
