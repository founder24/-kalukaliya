/**
 * Admin Dashboard — CI re-run button (Task #470 / handleCiRerun).
 *
 * The re-run button renders inside a workflow row only when the run
 * has completed with a non-success conclusion and carries a run id.
 * Clicking it:
 *   1. Immediately disables the button and changes its text to
 *      "re-running…" (React state set synchronously before the POST).
 *   2. Fires POST /api/admin/ci-rerun with { run_id, failed_only: true }.
 *   3. Returns the button to "re-run" (enabled) once the POST settles.
 *
 * Four cases:
 *   A. Re-run button is visible on a failed row and absent on a success row.
 *   B. Clicking re-run fires the POST with the correct run_id payload.
 *   C. Button is disabled and shows "re-running…" while POST is in flight.
 *   D. Button returns to "re-run" (enabled) after the POST resolves.
 */
import { test, expect, type Page, type Route } from '@playwright/test';
import { installAdminApiMocks, seedAdminSession } from './admin-mocks';

const DEPLOY_WF = 'azure-container-apps-deploy';
const PROXY_WF  = 'edge-proxy-deploy';

const FAILED_RUN_ID = 12345;

const CI_ONE_FAILED = {
  configured: true,
  branch: 'main',
  repo: 'syrabit/syrabit',
  runs: {
    [DEPLOY_WF]: {
      status: 'completed', conclusion: 'failure',
      run_number: 42, head_sha: 'abc1234', event: 'push',
      age_seconds: 3600, id: FAILED_RUN_ID,
      html_url: `https://github.com/syrabit/syrabit/actions/runs/${FAILED_RUN_ID}`,
    },
    [PROXY_WF]: {
      status: 'completed', conclusion: 'success',
      run_number: 38, head_sha: 'def5678', event: 'push',
      age_seconds: 5400, id: 12344,
      html_url: 'https://github.com/syrabit/syrabit/actions/runs/38',
    },
  },
};

async function openDashboardWithCiStatus(page: Page, ciPayload: unknown) {
  await seedAdminSession(page);
  await installAdminApiMocks(page, {
    overrides: { '/api/admin/ci-status': () => ciPayload },
  });
  await page.goto('/admin');
  await expect(page.getByTestId('admin-dashboard')).toBeVisible({ timeout: 15_000 });
}

test.describe('Admin Dashboard — CI re-run button', () => {
  test('re-run button visible on failed row, absent on success row', async ({ page }) => {
    await openDashboardWithCiStatus(page, CI_ONE_FAILED);

    const card = page.getByTestId('notif-prefs-ci-status');
    await expect(card).toBeVisible({ timeout: 15_000 });

    const failedRow  = card.getByTestId(`notif-prefs-ci-status-row-${DEPLOY_WF}`);
    const successRow = card.getByTestId(`notif-prefs-ci-status-row-${PROXY_WF}`);

    await expect(failedRow).toBeVisible({ timeout: 10_000 });

    // Button present on failed row.
    const rerunBtn = failedRow.getByRole('button', { name: /re-run/i });
    await expect(rerunBtn).toBeVisible({ timeout: 5_000 });
    await expect(rerunBtn).toBeEnabled();

    // Button absent on success row (conclusion === 'success' → no button rendered).
    await expect(successRow.getByRole('button', { name: /re-run/i })).toHaveCount(0);
  });

  test('clicking re-run fires POST /api/admin/ci-rerun with the correct run_id', async ({ page }) => {
    await openDashboardWithCiStatus(page, CI_ONE_FAILED);

    const rerunCalls: Array<{ run_id: number; failed_only: boolean }> = [];

    // Register narrow route AFTER the broad catch-all (LIFO wins).
    await page.route('**/api/admin/ci-rerun**', async (route: Route) => {
      const body = JSON.parse(route.request().postData() || '{}');
      rerunCalls.push(body);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    });

    const card = page.getByTestId('notif-prefs-ci-status');
    await expect(card).toBeVisible({ timeout: 15_000 });

    const failedRow = card.getByTestId(`notif-prefs-ci-status-row-${DEPLOY_WF}`);
    await expect(failedRow).toBeVisible({ timeout: 10_000 });

    await failedRow.getByRole('button', { name: /re-run/i }).click();

    await expect.poll(() => rerunCalls.length, { timeout: 8_000 }).toBe(1);
    expect(rerunCalls[0].run_id).toBe(FAILED_RUN_ID);
    expect(rerunCalls[0].failed_only).toBe(true);
  });

  test('button is disabled and shows "re-running…" while POST is in flight', async ({ page }) => {
    await openDashboardWithCiStatus(page, CI_ONE_FAILED);

    // Slow route — resolves only after we have checked the in-flight state.
    let resolveRerun!: () => void;
    const rerunInflight = new Promise<void>((res) => { resolveRerun = res; });

    await page.route('**/api/admin/ci-rerun**', async (route: Route) => {
      // Block until the test asserts the in-flight state.
      await rerunInflight;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    });

    const card = page.getByTestId('notif-prefs-ci-status');
    await expect(card).toBeVisible({ timeout: 15_000 });

    const failedRow = card.getByTestId(`notif-prefs-ci-status-row-${DEPLOY_WF}`);
    await expect(failedRow).toBeVisible({ timeout: 10_000 });

    const rerunBtn = failedRow.getByRole('button', { name: /re-run/i });
    await rerunBtn.click();

    // After click, the component sets ciRerunning = runId synchronously
    // (before the async POST resolves) — button must be disabled & show
    // "re-running…" text immediately.
    await expect(failedRow.getByRole('button', { name: /re-running/i })).toBeVisible({ timeout: 5_000 });
    await expect(failedRow.getByRole('button', { name: /re-running/i })).toBeDisabled();

    // Allow the POST to complete.
    resolveRerun();

    // Once POST resolves, ciRerunning resets to null → button re-enables.
    await expect(failedRow.getByRole('button', { name: /re-run/i })).toBeEnabled({ timeout: 8_000 });
  });

  test('button returns to "re-run" (enabled) after a successful POST', async ({ page }) => {
    await openDashboardWithCiStatus(page, CI_ONE_FAILED);

    await page.route('**/api/admin/ci-rerun**', async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true }),
      });
    });

    const card = page.getByTestId('notif-prefs-ci-status');
    await expect(card).toBeVisible({ timeout: 15_000 });

    const failedRow = card.getByTestId(`notif-prefs-ci-status-row-${DEPLOY_WF}`);
    await expect(failedRow).toBeVisible({ timeout: 10_000 });

    await failedRow.getByRole('button', { name: /re-run/i }).click();

    // POST resolves quickly — button must return to "re-run" and be enabled.
    await expect(failedRow.getByRole('button', { name: /re-run/i })).toBeEnabled({ timeout: 8_000 });
    await expect(failedRow.getByRole('button', { name: /re-running/i })).toHaveCount(0);
  });
});
