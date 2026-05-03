/**
 * Task #321 — verify the public on-call status page mirrors the
 * R2 cold-storage "watchdog blind" indicator that already lives on
 * the admin dashboard. The data source is the new public
 * ``/api/r2-watchdog-status`` endpoint (see
 * artifacts/syrabit-backend/routes/admin_r2_storage_health.py).
 *
 * What we cover here, and why:
 *   1. Hidden in the steady state (counter 0) so the public status
 *      header does not get cluttered when the watchdog is healthy.
 *   2. Amber when ``consecutive_query_failures`` is below the
 *      threshold — a single failed monthly evaluation is the early
 *      signal on-call should notice ~30 days before the actual page.
 *   3. Red once the counter reaches the configured threshold — at
 *      that point the watchdog-blind page has fired and IA-share +
 *      Logpush-cap alerts are silently broken.
 *   4. Both the link and tooltip point at the same Step 5 runbook
 *      the admin tile uses, so on-call has one source of truth.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import axios from 'axios';

vi.mock('axios');
vi.mock('@/components/layout/PublicLayout', () => ({
  PublicLayout: ({ children }) => <div>{children}</div>,
}));
vi.mock('@/components/seo/PageMeta', () => ({ default: () => null }));

import StatusPage from './StatusPage';

const HEALTH_OK = {
  status: 'ok',
  dependencies: {
    postgresql: { status: 'ok' },
    mongodb: { status: 'ok' },
    redis: { status: 'ok' },
    llm: { status: 'ok' },
  },
};

function mockApi({ watchdog, healthFails = false } = {}) {
  axios.get.mockImplementation((url) => {
    if (url.endsWith('/health')) {
      return healthFails
        ? Promise.reject(new Error('boom'))
        : Promise.resolve({ data: HEALTH_OK });
    }
    if (url.endsWith('/r2-watchdog-status')) {
      return watchdog === undefined
        ? Promise.reject(new Error('no watchdog endpoint'))
        : Promise.resolve({ data: watchdog });
    }
    return Promise.reject(new Error(`unexpected url ${url}`));
  });
}

function renderPage() {
  return render(
    <MemoryRouter>
      <StatusPage />
    </MemoryRouter>,
  );
}

describe('StatusPage — R2 watchdog blindness indicator (Task #321)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('hides the indicator when the watchdog counter is 0', async () => {
    mockApi({
      watchdog: {
        configured: true,
        query_fail_threshold: 2,
        runbook_url:
          'https://github.com/syrabit/syrabit/blob/main/docs/cloudflare-monthly-cost-review.md#step-5',
        state: {
          consecutive_query_failures: 0,
          query_fail_last_fired_at: null,
          last_evaluated_at: '2026-04-01T00:00:00Z',
        },
      },
    });
    renderPage();
    await waitFor(() =>
      expect(axios.get).toHaveBeenCalledWith(
        expect.stringContaining('/r2-watchdog-status'),
      ),
    );
    expect(
      screen.queryByTestId('status-page-r2-watchdog-indicator'),
    ).toBeNull();
  });

  it('hides the indicator when the watchdog is not configured', async () => {
    mockApi({
      watchdog: {
        configured: false,
        reason: 'CF_EDGE_PROXY_URL or D1_SYNC_SECRET is not set',
        query_fail_threshold: 2,
        state: null,
      },
    });
    renderPage();
    await waitFor(() => expect(axios.get).toHaveBeenCalled());
    expect(
      screen.queryByTestId('status-page-r2-watchdog-indicator'),
    ).toBeNull();
  });

  it('renders an amber indicator on the first failed monthly evaluation', async () => {
    mockApi({
      watchdog: {
        configured: true,
        query_fail_threshold: 2,
        runbook_url:
          'https://github.com/syrabit/syrabit/blob/main/docs/cloudflare-monthly-cost-review.md#step-5',
        state: {
          consecutive_query_failures: 1,
          query_fail_last_fired_at: null,
          last_evaluated_at: '2026-04-01T00:00:00Z',
        },
      },
    });
    renderPage();
    const badge = await screen.findByTestId(
      'status-page-r2-watchdog-indicator',
    );
    expect(badge.getAttribute('data-watchdog-state')).toBe('warn');
    expect(badge.getAttribute('data-watchdog-count')).toBe('1');
    expect(badge.getAttribute('data-watchdog-threshold')).toBe('2');
    expect(badge.textContent).toMatch(/R2 watchdog 1\/2/);
    // Link + tooltip both deep-link into the same Step 5 runbook the
    // admin tile uses — keeps a single source of truth for on-call.
    expect(badge.getAttribute('href')).toMatch(
      /cloudflare-monthly-cost-review\.md#step-5$/,
    );
    expect(badge.getAttribute('title')).toMatch(/cloudflare-monthly-cost-review\.md#step-5/);
    expect(badge.getAttribute('title')).toMatch(/1 of 2/);
  });

  it('renders a red indicator once the counter reaches the threshold', async () => {
    const lastFiredIso = '2026-04-15T10:00:00Z';
    mockApi({
      watchdog: {
        configured: true,
        query_fail_threshold: 2,
        runbook_url:
          'https://github.com/syrabit/syrabit/blob/main/docs/cloudflare-monthly-cost-review.md#step-5',
        state: {
          consecutive_query_failures: 2,
          query_fail_last_fired_at: lastFiredIso,
          last_evaluated_at: '2026-05-01T00:00:00Z',
        },
      },
    });
    renderPage();
    const badge = await screen.findByTestId(
      'status-page-r2-watchdog-indicator',
    );
    expect(badge.getAttribute('data-watchdog-state')).toBe('tripped');
    expect(badge.getAttribute('data-watchdog-count')).toBe('2');
    expect(badge.textContent).toMatch(/R2 watchdog 2\/2/);
    expect(badge.getAttribute('title')).toMatch(/Watchdog-blind page has fired/);
    expect(badge.getAttribute('title')).toMatch(
      new RegExp(new Date(lastFiredIso).getFullYear().toString()),
    );
  });

  it('hides the indicator silently when the watchdog endpoint fails', async () => {
    // watchdog: undefined → axios.get rejects. The page should still
    // render the rest of the status board instead of throwing.
    mockApi({});
    renderPage();
    await waitFor(() =>
      expect(axios.get).toHaveBeenCalledWith(
        expect.stringContaining('/r2-watchdog-status'),
      ),
    );
    expect(
      screen.queryByTestId('status-page-r2-watchdog-indicator'),
    ).toBeNull();
    // Sanity: the rest of the status header is still there.
    expect(screen.getByText(/All Systems Operational/)).toBeInTheDocument();
  });
});
