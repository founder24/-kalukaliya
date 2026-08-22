import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock('axios', () => ({
  default: {
    get: vi.fn(() => Promise.resolve({ data: {} })),
    post: vi.fn(() => Promise.resolve({ data: {} })),
    delete: vi.fn(() => Promise.resolve({ data: {} })),
  },
}));

vi.mock('@/utils/api', () => ({
  adminGetDashboard: vi.fn(),
  adminGetCfOverview: vi.fn(),
  seoPipelineStatus: vi.fn(() => Promise.resolve({ data: null })),
  adminSeoHealthHistory: vi.fn(),
  adminSeoHealthSnapshotNow: vi.fn(),
  seoHealthLive: vi.fn(),
  seoHealthDeepScan: vi.fn(),
  adminSeoDeepScanHistory: vi.fn(),
  adminGetAlertCooldowns: vi.fn(),
  API_BASE: '',
}));

vi.mock('../AdminQuickLinks', () => ({ default: () => null }));
vi.mock('../AdminDraftServedSubjects', () => ({ default: () => null }));
vi.mock('../AlertReasonsRow', () => ({ default: () => null }));
vi.mock('../BotCachePanel', () => ({ default: () => null }));
vi.mock('../CacheHitRatioPanel', () => ({ default: () => null }));
vi.mock('../R2ColdStoragePanel', () => ({ default: () => null }));
vi.mock('../AudioTrimPreview', () => ({ default: () => null }));
vi.mock('../analytics/CloudflareAnalyticsBanner', () => ({ default: () => null }));

vi.mock('./shared', async () => {
  const actual = await vi.importActual('./shared');
  return {
    ...actual,
    PipelineWidget: () => null,
  };
});

import AiHealthWidget from './AiHealthWidget';
import TrafficWidget from './TrafficWidget';
import SeoWidget from './SeoWidget';
import ChatWidget from './ChatWidget';
import UserAnalyticsWidget from './UserAnalyticsWidget';
import ActivityWidget from './ActivityWidget';

describe('admin dashboard widgets with missing or null API props', () => {
  let consoleError;

  beforeEach(() => {
    consoleError = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    expect(consoleError).not.toHaveBeenCalled();
    consoleError.mockRestore();
  });

  it('shows an explicit empty state for AI health traffic', () => {
    render(<AiHealthWidget cfOverview={null} vs={null} />);

    expect(screen.getByTestId('ai-health-empty-state')).toHaveTextContent(
      'No Cloudflare traffic data yet',
    );
  });

  it('shows an explicit empty state for site traffic', () => {
    render(<TrafficWidget cfVisitors24h={null} cfCrawlControl={null} vs={null} />);

    expect(screen.getByTestId('traffic-empty-state')).toHaveTextContent(
      'No traffic data yet',
    );
  });

  it('treats an empty Cloudflare totals object as missing traffic data', () => {
    render(<TrafficWidget cfVisitors24h={{ totals: {} }} vs={{}} />);

    expect(screen.getByTestId('traffic-empty-state')).toHaveTextContent(
      'No traffic data yet',
    );
  });

  it('keeps the SEO widget readable while sitemap data is unavailable', () => {
    render(<SeoWidget alertHistory={null} seoHealth={null} seoLive={null} />);

    expect(screen.getByText('SEO Sitemap Health')).toBeInTheDocument();
    expect(screen.getByText('Loading sitemap probes…')).toBeInTheDocument();
  });

  it('shows readable no-data states for chat health', () => {
    render(
      <ChatWidget
        chatFallbacks={null}
        chatSpeedups={null}
        failedSections={null}
        vectorStats={null}
      />,
    );

    expect(screen.getByText('No query data yet')).toBeInTheDocument();
    expect(screen.getByText('No vector data')).toBeInTheDocument();
    expect(screen.getByText('No chat speed-up data yet')).toBeInTheDocument();
  });

  it('handles a chat fallback response with no daily series', () => {
    render(<ChatWidget chatFallbacks={{ has_data: true }} failedSections={[]} />);

    expect(screen.getByText('No query data yet')).toBeInTheDocument();
  });

  it('shows readable no-data states for user analytics', () => {
    render(
      <UserAnalyticsWidget
        anonQuotaDays={null}
        anonQuotaWall={null}
        coverage={null}
        latency={null}
        topQueries={null}
      />,
    );

    expect(screen.getByTestId('anon-quota-empty-state')).toHaveTextContent(
      'No wall hits in the last 14 days',
    );
    expect(screen.getByText('No latency data yet')).toBeInTheDocument();
    expect(screen.getByText('No subjects found')).toBeInTheDocument();
  });

  it('handles user analytics responses with missing nested collections', () => {
    render(
      <UserAnalyticsWidget
        coverage={{ has_data: true }}
        latency={{ has_data: true }}
        topQueries={{ has_data: true }}
        tokenSpend={{ has_data: true }}
      />,
    );

    expect(screen.getByText('No latency data yet')).toBeInTheDocument();
    expect(screen.getByText('No query data yet')).toBeInTheDocument();
    expect(screen.getByText('No token data yet')).toBeInTheDocument();
    expect(screen.getByText('No subjects found')).toBeInTheDocument();
  });

  it('shows a readable no-activity state', () => {
    render(<ActivityWidget quickActions={null} recentEvents={null} vs={null} />);

    expect(screen.getByTestId('recent-activity')).toHaveTextContent(
      'No activity yet',
    );
  });
});