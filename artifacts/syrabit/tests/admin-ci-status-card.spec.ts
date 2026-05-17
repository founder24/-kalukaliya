/**
 * Admin Dashboard — CI status card (Task #470).
 *
 * The card lives in AdminDashboard.jsx (the default `dashboard` section)
 * inside the notification-preferences widget and is fed by
 * GET /api/admin/ci-status.  Three cases are covered:
 *
 *   1. Configured + successful runs  → runs list renders, each
 *      workflow has a green success pill and a "view run →" link.
 *   2. One workflow failed           → that row shows a red pill.
 *   3. Token / repo not configured   → unconfigured notice renders
 *      with `GITHUB_REPO` copy and the runs list is absent.
 */
import { test, expect, type Page, type Route } from '@playwright/test';
import { installAdminApiMocks, seedAdminSession } from './admin-mocks';

const DEPLOY_WF  = 'azure-container-apps-deploy';
const PROXY_WF   = 'edge-proxy-deploy';

const ALL_SUCCESS = {
  configured: true,
  branch: 'main',
  repo: 'syrabit/syrabit',
  runs: {
    [DEPLOY_WF]: {
      status: 'completed', conclusion: 'success',
      run_number: 42, head_sha: 'abc1234', event: 'push',
      age_seconds: 3600, id: 12345,
      html_url: 'https://github.com/syrabit/syrabit/actions/runs/42',
    },
    [PROXY_WF]: {
      status: 'completed', conclusion: 'success',
      run_number: 38, head_sha: 'def5678', event: 'push',
      age_seconds: 5400, id: 12344,
      html_url: 'https://github.com/syrabit/syrabit/actions/runs/38',
    },
  },
};

const ONE_FAILED = {
  ...ALL_SUCCESS,
  runs: {
    ...ALL_SUCCESS.runs,
    [DEPLOY_WF]: {
      ...ALL_SUCCESS.runs[DEPLOY_WF],
      conclusion: 'failure',
    },
  },
};

const NOT_CONFIGURED = {
  configured: false,
  reason: 'GITHUB_REPO not set',
};

async function openDashboard(page: Page) {
  await page.goto('/admin');
  await expect(page.getByTestId('admin-dashboard')).toBeVisible({ timeout: 15_000 });
  // The CI status card lives inside the notification-preferences panel which
  // starts collapsed (notifPrefsOpen = false). Click "Preferences" to expand it.
  await page.getByTestId('notif-prefs-toggle').click();
}

test.describe('Admin Dashboard — CI status card', () => {
  test.beforeEach(async ({ page }) => {
    await seedAdminSession(page);
  });

  test('renders workflow run rows with success pills when GitHub token is configured', async ({ page }) => {
    await installAdminApiMocks(page, {
      overrides: { '/api/admin/ci-status': () => ALL_SUCCESS },
    });

    await openDashboard(page);

    const card = page.getByTestId('notif-prefs-ci-status');
    await expect(card).toBeVisible({ timeout: 15_000 });

    const runsList = card.getByTestId('notif-prefs-ci-status-runs');
    await expect(runsList).toBeVisible({ timeout: 10_000 });

    // Both workflow rows must be present.
    await expect(card.getByTestId(`notif-prefs-ci-status-row-${DEPLOY_WF}`)).toBeVisible({ timeout: 10_000 });
    await expect(card.getByTestId(`notif-prefs-ci-status-row-${PROXY_WF}`)).toBeVisible({ timeout: 5_000 });

    // Success pill text rendered by the component for conclusion=success.
    const deployRow = card.getByTestId(`notif-prefs-ci-status-row-${DEPLOY_WF}`);
    await expect(deployRow).toContainText(/success/i);

    // "view run →" link rendered when html_url is present.
    await expect(deployRow.getByRole('link', { name: /view run/i })).toBeVisible({ timeout: 5_000 });
  });

  test('shows a red pill for the failed workflow and a green pill for the passing one', async ({ page }) => {
    await installAdminApiMocks(page, {
      overrides: { '/api/admin/ci-status': () => ONE_FAILED },
    });

    await openDashboard(page);

    const card = page.getByTestId('notif-prefs-ci-status');
    await expect(card).toBeVisible({ timeout: 15_000 });

    const deployRow = card.getByTestId(`notif-prefs-ci-status-row-${DEPLOY_WF}`);
    await expect(deployRow).toBeVisible({ timeout: 10_000 });
    await expect(deployRow).toContainText(/failure/i);

    const proxyRow = card.getByTestId(`notif-prefs-ci-status-row-${PROXY_WF}`);
    await expect(proxyRow).toBeVisible({ timeout: 5_000 });
    await expect(proxyRow).toContainText(/success/i);
  });

  test('renders the unconfigured notice (no runs list) when GITHUB_REPO is not set', async ({ page }) => {
    await installAdminApiMocks(page, {
      overrides: { '/api/admin/ci-status': () => NOT_CONFIGURED },
    });

    await openDashboard(page);

    const card = page.getByTestId('notif-prefs-ci-status');
    await expect(card).toBeVisible({ timeout: 15_000 });

    // Unconfigured notice must mention GITHUB_REPO.
    const notice = card.getByTestId('notif-prefs-ci-status-unconfigured');
    await expect(notice).toBeVisible({ timeout: 10_000 });
    await expect(notice).toContainText(/GITHUB_REPO/);

    // The runs list must NOT be rendered.
    await expect(card.getByTestId('notif-prefs-ci-status-runs')).toHaveCount(0);
  });
});
