import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within, fireEvent } from '@testing-library/react';
import D1MirrorLagPill from './D1MirrorLagPill';

// Task #508 — lock down the D1 mirror lag pill. The shared
// <CronHealthPill> drives the colour mapping; this wrapper owns the
// d1-mirror-specific copy: the status-vocabulary adaptation
// (breached → silent, not_enabled → not_configured), the
// "Lag Xh / threshold Yh · streak N/M · in-process …, lease …"
// caption, the tooltip with full ISO timestamps, and the
// healthUrl-based "Runs" link target. A future refactor of any of
// these could silently break the pill until the alerter next pages
// — these tests catch that the moment the build runs.

const TILE = 'd1-mirror-lag-tile';
const STATUS = 'd1-mirror-lag-status';
const PILL = 'd1-mirror-lag-pill';
const RUN_LINK = 'd1-mirror-lag-run-link';
const REFRESH = 'd1-mirror-lag-refresh';
const CAPTION = 'd1-mirror-lag-caption';
const HISTORY_TOGGLE = 'd1-mirror-lag-history-toggle';
const HISTORY_PANEL = 'd1-mirror-lag-history-panel';

const NOW_S = 1_750_000_000;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(NOW_S * 1000));
});

afterEach(() => {
  vi.useRealTimers();
});

const baseHealthy = {
  enabled: true,
  status: 'healthy',
  lagSeconds: 6 * 3600,
  lagThresholdSeconds: 36 * 3600,
  requiredStreak: 2,
  consecutiveBreachCount: 0,
  inProcessLastSyncTs: NOW_S - 3 * 3600,
  leaseLastFiredTs: NOW_S - 6 * 3600,
  lastSyncOk: true,
  lastSyncError: null,
  consecutiveFailures: 0,
  rowCounts: { syllabus_map: 1234 },
  healthUrl: '/admin/cf-health',
  alertState: { present: false },
};

describe('D1MirrorLagPill', () => {
  it('renders the healthy state in green with the lag/threshold caption', () => {
    render(<D1MirrorLagPill data={baseHealthy} loading={false} onRefresh={() => {}} />);
    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-emerald-50/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('D1 mirror lag — fresh');

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('MIRROR FRESH');
    expect(pill.className).toMatch(/bg-emerald-100/);

    expect(within(tile).getByTestId(CAPTION)).toHaveTextContent(
      'Lag 6.0h / threshold 36.0h · in-process 3h ago, lease 6h ago',
    );
    // Streak suffix omitted on a clean green pill.
    expect(tile).not.toHaveTextContent(/streak \d+\/\d+/);

    // Runs link points at the backend-supplied healthUrl.
    expect(within(tile).getByTestId(RUN_LINK)).toHaveAttribute('href', '/admin/cf-health');
    expect(within(tile).getByTestId(REFRESH)).toBeTruthy();
  });

  it('renders the breached state in red with the streak suffix and "LAG BREACHED" pill', () => {
    render(
      <D1MirrorLagPill
        data={{
          ...baseHealthy,
          status: 'breached',
          lagSeconds: 40 * 3600,
          consecutiveBreachCount: 1,
          inProcessLastSyncTs: null,
          leaseLastFiredTs: NOW_S - 40 * 3600,
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );
    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-red-50/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('D1 mirror lag — over threshold');

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('LAG BREACHED');
    expect(pill.className).toMatch(/bg-red-100/);

    const caption = within(tile).getByTestId(CAPTION);
    expect(caption).toHaveTextContent(
      'Lag 40.0h / threshold 36.0h · streak 1/2 · lease 1d ago',
    );
    // Tooltip carries the precise streak + ISO timestamps.
    expect(caption.getAttribute('title')).toMatch(/Consecutive breach streak: 1 of 2/);
    expect(caption.getAttribute('title')).toMatch(
      /In-process last sync: never \(this replica\)/,
    );
    expect(caption.getAttribute('title')).toMatch(
      /Cross-replica lease last fired: \d{4}-\d{2}-\d{2}T/,
    );
  });

  it('renders the never_observed state in gray with the no-sync fallback caption', () => {
    render(
      <D1MirrorLagPill
        data={{
          enabled: true,
          status: 'never_observed',
          lagSeconds: null,
          lagThresholdSeconds: 36 * 3600,
          requiredStreak: 2,
          consecutiveBreachCount: 0,
          inProcessLastSyncTs: null,
          leaseLastFiredTs: null,
          alertState: { present: false },
          healthUrl: '/admin/cf-health',
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );
    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-gray-50/);

    expect(within(tile).getByTestId(STATUS)).toHaveTextContent(
      'D1 mirror lag — no sync observed yet',
    );
    expect(within(tile).getByTestId(PILL)).toHaveTextContent('NEVER OBSERVED');
    expect(within(tile).getByTestId(CAPTION)).toHaveTextContent(
      'No sync observed yet · threshold 36.0h',
    );
  });

  it('renders the not_enabled state in gray with the "NOT ENABLED" pill', () => {
    render(
      <D1MirrorLagPill
        data={{
          enabled: false,
          status: 'not_enabled',
          lagSeconds: null,
          lagThresholdSeconds: 36 * 3600,
          requiredStreak: 2,
          consecutiveBreachCount: 0,
          alertState: { present: false },
          healthUrl: '/admin/cf-health',
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );
    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-gray-50/);
    expect(within(tile).getByTestId(STATUS)).toHaveTextContent(
      'D1 mirror lag — not enabled',
    );
    expect(within(tile).getByTestId(PILL)).toHaveTextContent('NOT ENABLED');
  });

  it('renders the inline "last paged Xh ago · in debounce ~Yh remaining" caption from alertState', () => {
    render(
      <D1MirrorLagPill
        data={{
          ...baseHealthy,
          status: 'breached',
          lagSeconds: 50 * 3600,
          consecutiveBreachCount: 3,
          alertState: {
            present: true,
            lastState: 'breached',
            lastAlertAt: '2026-04-26T00:00:00Z',
            lastAlertAgeSeconds: 2 * 3600,
            consecutiveBreachCount: 3,
            inDebounce: true,
            debounceRemainingSeconds: 22 * 3600,
            realertIntervalSeconds: 24 * 3600,
          },
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );
    const tile = screen.getByTestId(TILE);
    const alertCaption = within(tile).getByTestId('d1-mirror-lag-alert-state');
    expect(alertCaption).toHaveTextContent('last paged 2h ago · in debounce ~22h remaining');
    expect(alertCaption.className).toMatch(/text-amber-600/);
  });

  it('exposes the paged-history disclosure when the loader is wired', () => {
    const onLoad = vi.fn();
    render(
      <D1MirrorLagPill
        data={{ ...baseHealthy, status: 'breached', lagSeconds: 50 * 3600 }}
        loading={false}
        onRefresh={() => {}}
        alertHistory={{
          lockId: 'd1_mirror_lag_alert_state',
          limit: 20,
          events: [
            {
              id: 'evt-1',
              pagedAt: '2026-04-26T10:00:00.000Z',
              kind: 'breached',
            },
            {
              id: 'evt-2',
              pagedAt: '2026-04-25T10:00:00.000Z',
              kind: 'recovered',
            },
          ],
        }}
        onLoadAlertHistory={onLoad}
      />,
    );
    const tile = screen.getByTestId(TILE);
    const toggle = within(tile).getByTestId(HISTORY_TOGGLE);
    expect(toggle).toHaveTextContent('Show paged history (2)');
    fireEvent.click(toggle);
    expect(onLoad).toHaveBeenCalledTimes(1);
    expect(within(tile).getByTestId(HISTORY_PANEL)).toBeTruthy();
  });
});
