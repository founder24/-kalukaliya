import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('recharts', () => ({
  AreaChart: ({ children }) => <div>{children}</div>,
  Area: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  Legend: () => null,
  BarChart: ({ children }) => <div>{children}</div>,
  Bar: () => null,
  LineChart: ({ children }) => <div>{children}</div>,
  Line: () => null,
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('@/components/ErrorBoundary', () => ({
  SectionErrorBoundary: ({ children }) => <>{children}</>,
}));

function emptyComponent() {
  return { default: () => null };
}

vi.mock('@/components/admin/CronHealthPill', () => ({
  default: () => null,
  SlackConfigBadge: () => null,
}));
vi.mock('@/components/admin/CfWafDriftCronPill', emptyComponent);
vi.mock('@/components/admin/D1MirrorLagPill', emptyComponent);
vi.mock('@/components/admin/TrustpilotRefreshCronPill', emptyComponent);
vi.mock('@/components/admin/EdgeProxyDeployCronPill', emptyComponent);
vi.mock('@/components/admin/UnifiedLogsCfPullCronPill', emptyComponent);
vi.mock('@/components/admin/EmbedBackfillPill', emptyComponent);
vi.mock('@/components/admin/EmbedStackHealthPill', emptyComponent);
vi.mock('@/components/admin/CfAuditCard', emptyComponent);
vi.mock('@/components/admin/AiGatewayCacheByModelTile', emptyComponent);
vi.mock('@/components/admin/AiGatewayGuardrailByModelTile', emptyComponent);
vi.mock('@/components/admin/AdminAwsInfraCard', emptyComponent);
vi.mock('@/components/admin/AdminCronJobsCard', emptyComponent);
vi.mock('@/components/admin/AdminMemoryBrainTile', emptyComponent);
vi.mock('@/components/admin/AdminAzureAiPanel', emptyComponent);
vi.mock('@/components/admin/AdminQuickLinks', emptyComponent);
vi.mock('@/components/admin/AssameseBackfillPanel', emptyComponent);
vi.mock('@/components/admin/EdgeMetricsPanel', emptyComponent);
vi.mock('@/components/admin/health/ProviderLatencyBench', emptyComponent);

import InfraTab from '../InfraTab';

const noop = vi.fn();

function renderInfra(rateLimitCleanup) {
  return render(
    <InfraTab
      adminToken="test-token"
      edgeHealth={rateLimitCleanup === undefined ? {} : { rate_limit_cleanup: rateLimitCleanup }}
      health={{ version: 'test', workers: 1, uptime_seconds: 60 }}
      loading={false}
      deps={{}}
      allOk={true}
      hasError={false}
      chartData={[]}
      peaks={{}}
      current={{}}
      metricsLoading={false}
      timeRange="1h"
      setTimeRange={noop}
      loadMetrics={noop}
      loadHealth={noop}
      slackWebhookMissingAlertStates={{}}
      slackWebhookMissingAlertHistories={{}}
      SLACK_WEBHOOK_MISSING_ENVS={[]}
    />,
  );
}

function localTimestamp(value) {
  return new Date(value).toLocaleString();
}

describe('InfraTab chat-limit cleanup incident history card', () => {
  it.each([
    ['Healthy', false, 0],
    ['Degraded', true, 4],
  ])('renders a %s cleanup snapshot with its rolling count', (label, degraded, count) => {
    renderInfra({
      degraded,
      history_window_hours: 24,
      rolling_incident_count: count,
      latest_failure_at: null,
      latest_recovery_at: null,
      recent_transitions: [],
    });

    expect(screen.getByTestId('chat-limit-cleanup-health')).toHaveTextContent(
      `Chat-limit cleanup: ${label}`,
    );
    expect(screen.getByTestId('chat-limit-cleanup-incident-count')).toHaveTextContent(
      String(count),
    );
    expect(screen.getByText('Incidents · 24h')).toBeInTheDocument();
  });

  it('renders transition labels and timestamps newest first', () => {
    const oldest = '2026-09-10T08:00:00.000Z';
    const middle = '2026-09-10T08:05:00.000Z';
    const newest = '2026-09-10T08:10:00.000Z';

    renderInfra({
      degraded: false,
      rolling_incident_count: 2,
      recent_transitions: [
        { event: 'failed', occurred_at: oldest },
        { event: 'recovered', occurred_at: middle },
        { event: 'failed', occurred_at: newest },
      ],
    });

    const transitions = within(screen.getByTestId('chat-limit-cleanup-history'))
      .getAllByTestId(/chat-limit-cleanup-transition-/);

    expect(transitions).toHaveLength(3);
    expect(transitions[0]).toHaveTextContent(`Failure${localTimestamp(newest)}`);
    expect(transitions[1]).toHaveTextContent(`Recovery${localTimestamp(middle)}`);
    expect(transitions[2]).toHaveTextContent(`Failure${localTimestamp(oldest)}`);
  });

  it('keeps empty and unavailable history states readable', () => {
    const { rerender } = renderInfra({
      degraded: false,
      rolling_incident_count: 0,
      recent_transitions: [],
    });

    expect(screen.getByTestId('chat-limit-cleanup-history-empty')).toHaveTextContent(
      'No transitions in this window',
    );

    rerender(
      <InfraTab
        edgeHealth={{}}
        health={{}}
        loading={false}
        deps={{}}
        chartData={[]}
        peaks={{}}
        current={{}}
        loadMetrics={noop}
        loadHealth={noop}
        slackWebhookMissingAlertStates={{}}
        slackWebhookMissingAlertHistories={{}}
        SLACK_WEBHOOK_MISSING_ENVS={[]}
      />,
    );

    expect(screen.getByTestId('chat-limit-cleanup-health')).toHaveTextContent(
      'Chat-limit cleanup: Unknown',
    );
    expect(screen.queryByTestId('chat-limit-cleanup-history')).not.toBeInTheDocument();
  });

  it('does not render opaque incident, student, or bucket identifiers', () => {
    const privateValues = [
      'incident-opaque-7f3a',
      'student-opaque-91bc',
      'bucket-opaque-22dd',
    ];

    renderInfra({
      degraded: true,
      rolling_incident_count: 1,
      incident_id: privateValues[0],
      student_id: privateValues[1],
      bucket_id: privateValues[2],
      recent_transitions: [{
        event: 'failed',
        occurred_at: '2026-09-10T08:10:00.000Z',
        incident_id: privateValues[0],
        student_id: privateValues[1],
        bucket_id: privateValues[2],
      }],
    });

    const cardText = screen.getByTestId('chat-limit-cleanup-health').textContent;
    privateValues.forEach((value) => expect(cardText).not.toContain(value));
  });
});