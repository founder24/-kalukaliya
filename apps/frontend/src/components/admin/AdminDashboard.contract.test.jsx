/**
 * Dashboard response-contract integration coverage.
 *
 * Widget empty-state tests cover absent optional props. This test instead
 * mounts the real dashboard with populated endpoint envelopes and proves the
 * overview and heavy-metrics values reach the operator-facing UI.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

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
  pro_users: 89,
  free_users: 232,
  system_health: 'ok',
  signups_today: 14,
  feedback: { total: 17, positive: 14, positive_rate: 0.824 },
};

const METRICS_RESPONSE = {
  source: 'd1',
  response_time_ms: 48,
  users: { total: 321, pro: 89, free: 232 },
  seo: { published_pages: 45, topics: 38 },
  dependencies: { d1: { status: 'ok', latency: 48 } },
  visitor_stats: {
    source: 'analytics_events',
    window_days: 30,
    has_data: true,
    page_views: 876,
    page_views_today: 24,
    sessions: 184,
    sessions_today: 17,
    daily: [{ date: '2026-08-21', page_views: 876, sessions: 184 }],
  },
};

describe('AdminDashboard dashboard response contract', () => {
  it('renders populated overview and metrics responses without a failed-load fallback', async () => {
    api.adminGetDashboard.mockResolvedValue({ data: DASHBOARD_RESPONSE });
    api.seoPipelineStatus.mockResolvedValue({ data: {
      source: 'chapters',
      total_topics: 38,
      pages_total: 45,
      published: 45,
      has_content: 38,
      with_assamese_notes: 12,
      needs_english_notes: 0,
    } });
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
      if (String(url).endsWith('/admin/notification-prefs')) {
        return Promise.resolve({ data: { sound_enabled: true, push_enabled: false } });
      }
      return Promise.resolve({ data: {} });
    });
    axiosPost.mockResolvedValue({ data: {} });
    axiosPatch.mockResolvedValue({ data: {} });
    axiosPut.mockResolvedValue({ data: {} });
    axiosDelete.mockResolvedValue({ data: {} });

    const onNavigate = vi.fn();
    render(<AdminDashboard adminToken="cookie" onNavigate={onNavigate} />);

    await waitFor(() => {
      expect(screen.getAllByText('321').length).toBeGreaterThan(0);
      expect(screen.getAllByText('876').length).toBeGreaterThan(0);
      expect(api.seoPipelineStatus).toHaveBeenCalledWith('cookie');
    });

    expect(screen.getAllByText('876').length).toBeGreaterThan(0);
    expect(screen.getByText('Content Pipeline')).toBeInTheDocument();
    expect(screen.getByText('(38 topics · 45 pages)')).toBeInTheDocument();
    expect(screen.getByText('Sessions (30d)')).toBeInTheDocument();
    expect(screen.getByText('184')).toBeInTheDocument();
    expect(screen.getByText('Positive Chat Feedback')).toBeInTheDocument();
    expect(screen.queryByText(/Some widgets failed to load/)).not.toBeInTheDocument();
    expect(screen.queryByTestId('ai-health-empty-state')).not.toBeInTheDocument();
    expect(screen.queryByTestId('traffic-empty-state')).not.toBeInTheDocument();
    expect(api.adminGetCfOverview).not.toHaveBeenCalled();
    expect(api.seoHealthLive).not.toHaveBeenCalled();

    const unsupportedInitialRequests = axiosGet.mock.calls
      .map(([url]) => String(url))
      .filter(url => /cf-overview|r2-storage-health|kv-health|chat\/speedups|anon-quota-exhausted|admin\/alerts|seo\/deep-scan-history|seo\/health-live/i.test(url));
    expect(unsupportedInitialRequests).toEqual([]);

    expect(screen.queryByTestId('r2-cold-storage-watchdog-indicator')).not.toBeInTheDocument();
    expect(axiosGet.mock.calls.map(([url]) => String(url)).some(url => url.includes('/admin/r2-storage-health'))).toBe(false);
    expect(screen.getByRole('button', { name: 'Preferences' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Preferences' }));
    expect(screen.getByTestId('notification-preferences-panel')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Alert sounds' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Alert settings' }));
    expect(onNavigate).toHaveBeenCalledWith('botsecurity', { panel: 'alert-settings' });
  });
});