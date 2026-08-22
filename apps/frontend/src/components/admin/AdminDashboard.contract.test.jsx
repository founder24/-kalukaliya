/**
 * Dashboard response-contract integration coverage.
 *
 * Widget empty-state tests cover absent optional props. This test instead
 * mounts the real dashboard with populated endpoint envelopes and proves the
 * overview and heavy-metrics values reach the operator-facing UI.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

const { axiosGet, axiosPost, axiosPatch, axiosPut, axiosDelete } = vi.hoisted(() => ({
  axiosGet: vi.fn(),
  axiosPost: vi.fn(),
  axiosPatch: vi.fn(),
  axiosPut: vi.fn(),
  axiosDelete: vi.fn(),
}));

const api = vi.hoisted(() => ({
  adminGetDashboard: vi.fn(),
  adminGetCfOverview: vi.fn(),
  seoPipelineStatus: vi.fn(),
  adminSeoHealthHistory: vi.fn(),
  adminSeoHealthSnapshotNow: vi.fn(),
  seoHealthLive: vi.fn(),
  seoHealthDeepScan: vi.fn(),
  adminSeoDeepScanHistory: vi.fn(),
  adminGetAlertCooldowns: vi.fn(),
}));

vi.mock('axios', () => ({
  default: {
    get: axiosGet,
    post: axiosPost,
    patch: axiosPatch,
    put: axiosPut,
    delete: axiosDelete,
  },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), message: vi.fn() },
}));

vi.mock('recharts', () => ({
  AreaChart: () => null,
  BarChart: () => null,
  LineChart: () => null,
  Area: () => null,
  Bar: () => null,
  Line: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Legend: () => null,
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  ReferenceLine: () => null,
}));

vi.mock('@/components/ErrorBoundary', () => ({
  SectionErrorBoundary: ({ children }) => <>{children}</>,
}));
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
vi.mock('./AdminQuickLinks', () => ({ default: () => null }));
vi.mock('./AdminDraftServedSubjects', () => ({ default: () => null }));
vi.mock('./AlertReasonsRow', () => ({ default: () => null }));
vi.mock('./BotCachePanel', () => ({ default: () => null }));
vi.mock('./CacheHitRatioPanel', () => ({ default: () => null }));
vi.mock('./R2ColdStoragePanel', () => ({ default: () => null }));
vi.mock('./AudioTrimPreview', () => ({ default: () => null }));
vi.mock('./analytics/CloudflareAnalyticsBanner', () => ({ default: () => null }));

vi.mock('@/utils/api', () => ({
  API_BASE: 'http://test.local/api',
  ...api,
}));

import AdminDashboard from './AdminDashboard';

const DASHBOARD_RESPONSE = {
  total_users: 321,
  active_today: 72,
  total_messages: 876,
  messages_today: 54,
  revenue_total: 12345,
  revenue_month: 3456,
  pro_users: 89,
  free_users: 232,
  system_health: 'ok',
  signups_today: 14,
  feedback: { total: 17, positive: 14, positive_rate: 0.824 },
  vector_stats: { source: 'unavailable' },
  token_spend: { source: 'unavailable' },
  top_queries: { source: 'unavailable' },
  chat_fallbacks: { source: 'unavailable' },
};

const METRICS_RESPONSE = {
  response_time_ms: 48,
  revenue: { total_inr: 12345, mrr_inr: 3456 },
  users: { paid: 89, free: 232 },
  seo: { published_pages: 45, topics: 38 },
  bot_render: { total_requests: 120, by_page_type: {} },
  dependencies: {},
  _meta: { heavy_cached_at: Date.now() / 1000, source: 'mongodb' },
};

// Mirrors the populated GET /admin/analytics/cf-overview envelope.
const CF_OVERVIEW_RESPONSE = {
  connected: true,
  range: '7d',
  period_label: 'Previous 7 days',
  bucket: 'day',
  totals: { requests: 1200, bytes: 2048, visitors: 321, page_views: 876, threats: 0 },
  series: [{ date: '2026-08-21', requests: 1200, bytes: 2048, visitors: 321, page_views: 876, threats: 0 }],
  source: 'cloudflare_graphql',
  requests_24h: 1200,
  bandwidth_bytes_24h: 2048,
  threats_24h: 0,
  page_views_24h: 876,
};

const CF_24H_OVERVIEW_RESPONSE = {
  ...CF_OVERVIEW_RESPONSE,
  range: '24h',
  period_label: 'Previous 24 hours',
  bucket: 'hour',
  totals: { requests: 1200, bytes: 2048, visitors: 184, page_views: 876, threats: 0 },
  series: [
    { date: '2026-08-21T10:00:00Z', requests: 600, bytes: 1024, visitors: 22, page_views: 400, threats: 0 },
    { date: '2026-08-21T11:00:00Z', requests: 600, bytes: 1024, visitors: 27, page_views: 476, threats: 0 },
  ],
};

describe('AdminDashboard dashboard response contract', () => {
  it('renders populated overview and metrics responses without a failed-load fallback', async () => {
    api.adminGetDashboard.mockResolvedValue({ data: DASHBOARD_RESPONSE });
    api.adminGetCfOverview.mockImplementation((_token, range) => Promise.resolve({
      data: range === '24h' ? CF_24H_OVERVIEW_RESPONSE : CF_OVERVIEW_RESPONSE,
    }));
    api.seoPipelineStatus.mockResolvedValue({ data: {} });
    api.adminSeoHealthHistory.mockResolvedValue({ data: { history: [] } });
    api.adminGetAlertCooldowns.mockResolvedValue({ data: { active_count: 0 } });
    api.adminSeoHealthSnapshotNow.mockResolvedValue({ data: {} });
    api.seoHealthLive.mockResolvedValue({ data: {} });
    api.seoHealthDeepScan.mockResolvedValue({ data: {} });
    api.adminSeoDeepScanHistory.mockResolvedValue({ data: { history: [] } });

    axiosGet.mockImplementation((url) => {
      if (String(url).endsWith('/admin/dashboard/metrics')) {
        return Promise.resolve({ data: METRICS_RESPONSE });
      }
      return Promise.resolve({ data: {} });
    });
    axiosPost.mockResolvedValue({ data: {} });
    axiosPatch.mockResolvedValue({ data: {} });
    axiosPut.mockResolvedValue({ data: {} });
    axiosDelete.mockResolvedValue({ data: {} });

    render(<AdminDashboard adminToken="cookie" onNavigate={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getAllByText('321').length).toBeGreaterThan(0);
      expect(screen.getByText('₹12,345')).toBeInTheDocument();
    });

    expect(screen.getAllByText('876').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Unique Visitors').length).toBeGreaterThan(0);
    expect(screen.getByText('184')).toBeInTheDocument();
    expect(screen.getByText('27')).toBeInTheDocument();
    expect(screen.queryByText(/Some widgets failed to load/)).not.toBeInTheDocument();
    expect(screen.queryByTestId('ai-health-empty-state')).not.toBeInTheDocument();
    expect(screen.queryByTestId('traffic-empty-state')).not.toBeInTheDocument();
  });
});