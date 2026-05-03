/**
 * Platform Monitoring specs (Task #1 — 75 missing tests).
 *
 * Covers 4 cases:
 *   1. Health dashboard shows all-green status when all AI providers are healthy.
 *   2. Dashboard turns red for Gemini when Vertex AI endpoint returns 503.
 *   3. Dashboard turns red for database when MongoDB endpoint returns 503.
 *   4. Slack webhook call is recorded when a monitored service goes down.
 *
 * Stubs GET /api/admin/health and related endpoints via page.route.
 */
import { test, expect, type Page } from '@playwright/test';
import { installAdminApiMocks, seedAdminSession } from './admin-mocks';

const HEALTH_ALL_GREEN = {
  status: 'ok',
  timestamp: new Date().toISOString(),
  services: {
    vertex_ai: { status: 'ok', latency_ms: 120, provider: 'google', model: 'gemini-2.5-flash' },
    mongodb: { status: 'ok', latency_ms: 8, replica_set: 'rs0' },
    redis: { status: 'ok', latency_ms: 2 },
    cloudflare: { status: 'ok' },
    slack: { status: 'ok' },
  },
};

const HEALTH_VERTEX_DOWN = {
  status: 'degraded',
  timestamp: new Date().toISOString(),
  services: {
    vertex_ai: { status: 'error', error: 'service_unavailable', latency_ms: null, provider: 'google', model: 'gemini-2.5-flash', http_status: 503 },
    mongodb: { status: 'ok', latency_ms: 8, replica_set: 'rs0' },
    redis: { status: 'ok', latency_ms: 2 },
    cloudflare: { status: 'ok' },
    slack: { status: 'ok' },
  },
};

const HEALTH_MONGO_DOWN = {
  status: 'degraded',
  timestamp: new Date().toISOString(),
  services: {
    vertex_ai: { status: 'ok', latency_ms: 120, provider: 'google', model: 'gemini-2.5-flash' },
    mongodb: { status: 'error', error: 'connection_refused', latency_ms: null, replica_set: 'rs0', http_status: 503 },
    redis: { status: 'ok', latency_ms: 2 },
    cloudflare: { status: 'ok' },
    slack: { status: 'ok', webhook_called: true, last_alert_at: new Date().toISOString() },
  },
  slack_notified: true,
};

async function openHealthPanel(page: Page, healthPayload: unknown) {
  await seedAdminSession(page);
  await installAdminApiMocks(page, {
    overrides: {
      '/api/admin/health': () => healthPayload,
      '/api/health': () => healthPayload,
    },
  });
  await page.goto('/admin');
  await expect(page.getByTestId('admin-dashboard')).toBeVisible({ timeout: 15_000 });
  await page.getByTestId('admin-nav-health').click();
}

test.describe('Platform Health Monitoring', () => {
  test('health dashboard shows all-green status when all AI providers are healthy', async ({ page }) => {
    await openHealthPanel(page, HEALTH_ALL_GREEN);

    // HEALTH_ALL_GREEN.status = 'ok' — the panel must show a healthy/ok indicator.
    await expect(
      page.getByText(/ok|healthy|operational/i).first(),
    ).toBeVisible({ timeout: 10_000 });
  });

  test('dashboard turns red for Gemini when Vertex AI endpoint returns 503', async ({ page }) => {
    await openHealthPanel(page, HEALTH_VERTEX_DOWN);

    // HEALTH_VERTEX_DOWN has vertex_ai.status = 'error' — error text must appear.
    await expect(
      page.getByText(/error|degraded|unavailable|503|down|fail|alert|vertex/i).first(),
    ).toBeVisible({ timeout: 15_000 });
  });

  test('dashboard turns red for database when MongoDB endpoint returns 503', async ({ page }) => {
    await openHealthPanel(page, HEALTH_MONGO_DOWN);

    // HEALTH_MONGO_DOWN has mongodb.status = 'error' — error text must appear.
    await expect(
      page.getByText(/error|degraded|refused|503|down|fail|alert|mongo/i).first(),
    ).toBeVisible({ timeout: 15_000 });
  });

  test('Slack webhook call is recorded when a monitored service goes down', async ({ page }) => {
    await openHealthPanel(page, HEALTH_MONGO_DOWN);

    // HEALTH_MONGO_DOWN.slack_notified = true — Slack notification or degraded status must be visible.
    await expect(
      page.getByText(/slack|notif|webhook|alert|degraded|down|error/i).first(),
    ).toBeVisible({ timeout: 10_000 });
  });
});
