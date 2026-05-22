/**
 * Task #461 — AdminDashboard integration test for the manual D1 sync
 * trigger + extended_mirror result rendering.
 *
 * Covers the wiring layer between the new "Sync now" button next to
 * the D1 Sync row and the post-sync result panel:
 *   click "Sync now"
 *     → POST /admin/d1-sync with adminHdr(token)
 *     → primary result renders (ok badge + per-table row counts)
 *     → extended_mirror sub-section renders with success badge + rows
 *     → failed extended_mirror surfaces the `reason` string instead
 *       of being silently dropped
 *     → outer-level failure surfaces the error detail
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

const { axiosGet, axiosPost, axiosPatch, axiosPut, axiosDelete } = vi.hoisted(() => ({
  axiosGet:    vi.fn(),
  axiosPost:   vi.fn(),
  axiosPatch:  vi.fn(),
  axiosPut:    vi.fn(),
  axiosDelete: vi.fn(),
}));

vi.mock('axios', () => ({
  default: {
    get:    axiosGet,
    post:   axiosPost,
    patch:  axiosPatch,
    put:    axiosPut,
    delete: axiosDelete,
    create: vi.fn(),
  },
  get: axiosGet,
  post: axiosPost,
}));

const { toastSuccess, toastError, toastMessage, toastInfo } = vi.hoisted(() => ({
  toastSuccess: vi.fn(),
  toastError:   vi.fn(),
  toastMessage: vi.fn(),
  toastInfo:    vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: {
    success: toastSuccess,
    error:   toastError,
    message: toastMessage,
    info:    toastInfo,
  },
}));

vi.mock('recharts', () => ({
  AreaChart:           ({ children }) => <div>{children}</div>,
  BarChart:            ({ children }) => <div>{children}</div>,
  LineChart:           ({ children }) => <div>{children}</div>,
  Area:                () => null,
  Bar:                 () => null,
  Line:                () => null,
  XAxis:               () => null,
  YAxis:               () => null,
  CartesianGrid:       () => null,
  Tooltip:             () => null,
  Legend:              () => null,
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  ReferenceLine:       () => null,
}));

vi.mock('@/components/ErrorBoundary', () => ({
  SectionErrorBoundary: ({ children }) => <>{children}</>,
}));

vi.mock('./AdminQuickLinks',          () => ({ default: () => null }));
vi.mock('./AdminDraftServedSubjects', () => ({ default: () => null }));
vi.mock('./AlertReasonsRow',          () => ({ default: () => null }));
vi.mock('./BotCachePanel',            () => ({ default: () => null }));
vi.mock('./AudioTrimPreview',         () => ({ default: () => null }));
vi.mock('./analytics/CloudflareAnalyticsBanner', () => ({ default: () => null }));

vi.mock('@/hooks/usePushNotifications', () => ({
  usePushNotifications: () => ({
    permission: 'default',
    subscribed: false,
    isSupported: false,
    loading: false,
    error: null,
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
  }),
}));

vi.mock('@/utils/logger', () => ({
  log: { error: vi.fn(), warn: vi.fn(), info: vi.fn(), debug: vi.fn() },
}));

const seoLiveBase = {
  status: 'ok',
  checked_at: '2026-05-06T00:00:00Z',
  summary: {
    valid_sitemaps: 3,
    total_sitemaps: 3,
    ok_url_checks: 12,
    total_url_checks: 12,
    url_check_success_rate: 100,
  },
  content_stats: { published_pages: 100 },
  sitemaps: [],
  d1_sync: {
    status: 'fresh',
    last_sync: '2026-05-06T00:00:00Z',
    row_count: 1234,
  },
};

vi.mock('@/utils/api', () => ({
  API_BASE: 'http://test.local/api',
  adminGetDashboard:        vi.fn(() => Promise.resolve({ data: {} })),
  adminGetCfOverview:       vi.fn(() => Promise.resolve({ data: {} })),
  seoPipelineStatus:        vi.fn(() => Promise.resolve({ data: {} })),
  adminSeoHealthHistory:    vi.fn(() => Promise.resolve({ data: {} })),
  adminSeoHealthSnapshotNow:vi.fn(() => Promise.resolve({ data: {} })),
  seoHealthLive:            vi.fn(() => Promise.resolve({ data: seoLiveBase })),
  seoHealthDeepScan:        vi.fn(() => Promise.resolve({ data: {} })),
  adminSeoDeepScanHistory:  vi.fn(() => Promise.resolve({ data: {} })),
  adminGetAlertCooldowns:   vi.fn(() => Promise.resolve({ data: { active_count: 0 } })),
}));

import AdminDashboard from './AdminDashboard';

const ADMIN_TOKEN = 'header.payload.signature';

function defaultAxiosGet() {
  return Promise.resolve({ data: {} });
}

async function flushEffects() {
  for (let i = 0; i < 8; i++) {
    await act(async () => { await Promise.resolve(); });
  }
}

async function mountAndWaitForButton() {
  render(<AdminDashboard adminToken={ADMIN_TOKEN} onNavigate={vi.fn()} />);
  await waitFor(() => {
    expect(screen.queryByText(/Loading dashboard/)).toBeNull();
  }, { timeout: 4000 });
  await flushEffects();
  await waitFor(() => {
    expect(screen.getByTestId('d1-sync-trigger')).toBeInTheDocument();
  }, { timeout: 4000 });
}

describe('AdminDashboard — D1 sync trigger + extended_mirror (Task #461)', () => {
  beforeEach(() => {
    axiosGet.mockImplementation(defaultAxiosGet);
    axiosPatch.mockResolvedValue({ data: {} });
    axiosPut.mockResolvedValue({ data: {} });
    axiosDelete.mockResolvedValue({ data: {} });
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('POSTs to /admin/d1-sync with adminHdr Bearer token on click', async () => {
    axiosPost.mockResolvedValueOnce({
      data: {
        success: true,
        row_counts: { boards: 5, classes: 10 },
        extended_mirror: {
          success: true,
          tables: ['seo_meta', 'audit_log', 'syllabus_map'],
          row_counts: { seo_meta: 42, audit_log: 7, syllabus_map: 99 },
        },
      },
    });
    await mountAndWaitForButton();

    fireEvent.click(screen.getByTestId('d1-sync-trigger'));

    await waitFor(() => {
      expect(axiosPost).toHaveBeenCalledWith(
        'http://test.local/api/admin/d1-sync',
        null,
        expect.objectContaining({
          headers: expect.objectContaining({
            Authorization: `Bearer ${ADMIN_TOKEN}`,
          }),
          withCredentials: true,
        }),
      );
    });
  });

  it('renders the extended_mirror sub-section with success badge + per-table row counts', async () => {
    axiosPost.mockResolvedValueOnce({
      data: {
        success: true,
        extended_mirror: {
          success: true,
          tables: ['seo_meta', 'audit_log', 'syllabus_map'],
          row_counts: { seo_meta: 42, audit_log: 7, syllabus_map: 99 },
        },
      },
    });
    await mountAndWaitForButton();

    fireEvent.click(screen.getByTestId('d1-sync-trigger'));

    await waitFor(() => {
      expect(screen.getByTestId('d1-sync-extended-mirror')).toBeInTheDocument();
    });
    expect(screen.getByTestId('d1-sync-extended-mirror-status')).toHaveTextContent(/ok/i);
    const rows = screen.getByTestId('d1-sync-extended-mirror-rows');
    expect(rows).toHaveTextContent(/seo_meta/);
    expect(rows).toHaveTextContent(/42/);
    expect(rows).toHaveTextContent(/audit_log/);
    expect(rows).toHaveTextContent(/7/);
    expect(rows).toHaveTextContent(/syllabus_map/);
    expect(rows).toHaveTextContent(/99/);
    expect(toastSuccess).toHaveBeenCalledWith('D1 sync complete');
  });

  it('surfaces the `reason` string when the extended_mirror fails', async () => {
    axiosPost.mockResolvedValueOnce({
      data: {
        success: true,
        extended_mirror: { success: false, reason: 'empty_payload' },
      },
    });
    await mountAndWaitForButton();

    fireEvent.click(screen.getByTestId('d1-sync-trigger'));

    await waitFor(() => {
      expect(screen.getByTestId('d1-sync-extended-mirror-reason'))
        .toHaveTextContent('empty_payload');
    });
    expect(screen.getByTestId('d1-sync-extended-mirror-status'))
      .toHaveTextContent(/failed/i);
    expect(toastError).toHaveBeenCalledWith('Extended mirror failed: empty_payload');
  });

  it('falls back to "unknown reason" when extended_mirror failure omits a reason', async () => {
    axiosPost.mockResolvedValueOnce({
      data: { extended_mirror: { success: false } },
    });
    await mountAndWaitForButton();

    fireEvent.click(screen.getByTestId('d1-sync-trigger'));

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith('Extended mirror failed: unknown reason');
    });
  });

  it('marks the result panel "failed" and toasts when the primary sync_full reports failure', async () => {
    axiosPost.mockResolvedValueOnce({
      data: {
        primary: { success: false, reason: 'edge_worker_500' },
        extended_mirror: { success: true, tables: [], row_counts: {} },
      },
    });
    await mountAndWaitForButton();

    fireEvent.click(screen.getByTestId('d1-sync-trigger'));

    await waitFor(() => {
      expect(screen.getByTestId('d1-sync-result-status'))
        .toHaveTextContent(/failed/i);
    });
    expect(toastError).toHaveBeenCalledWith('D1 sync failed: edge_worker_500');
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('surfaces the outer-level error detail when the POST itself fails', async () => {
    const err = new Error('boom');
    err.response = { status: 503, data: { detail: 'D1 sync not configured' } };
    axiosPost.mockRejectedValueOnce(err);
    await mountAndWaitForButton();

    fireEvent.click(screen.getByTestId('d1-sync-trigger'));

    await waitFor(() => {
      expect(screen.getByTestId('d1-sync-error'))
        .toHaveTextContent('D1 sync not configured');
    });
    expect(toastError).toHaveBeenCalledWith('D1 sync failed: D1 sync not configured');
  });
});
