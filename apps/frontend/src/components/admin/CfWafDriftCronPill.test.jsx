import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import CfWafDriftCronPill from './CfWafDriftCronPill';

// Task #836 — lock down the firewall-drift cron pill that Task #833
// added to AdminHealth. The shared <CronHealthPill> component drives
// the colour mapping and heartbeat-age formatter, but the wrapper
// here owns the cf-waf-drift-specific copy: header text per status,
// the verify/aggregate exit-code suffix on `degraded`, and the
// optional "Last run" deep-link. A future refactor of any of these
// could silently break the pill until the cron next goes silent —
// these tests catch that the moment the build runs.
//
// Each case is exercised through the same `cf-waf-drift-cron-*`
// data-testid hooks the JSX renders so the assertions match what an
// admin (or the e2e suite) actually sees.

const TILE = 'cf-waf-drift-cron-tile';
const STATUS = 'cf-waf-drift-cron-status';
const PILL = 'cf-waf-drift-cron-pill';
const RUN_LINK = 'cf-waf-drift-cron-run-link';
const REFRESH = 'cf-waf-drift-cron-refresh';
const LAST_RUN_LINK = 'cf-waf-drift-cron-last-run-link';

const baseHealthy = {
  status: 'healthy',
  configured: true,
  lastHeartbeatTs: 1_700_000_000,
  lastHeartbeatAgeSeconds: 3600, // 1h ago
  lastStatus: 'ok',
  lastVerifyRc: 0,
  lastAggregateRc: 0,
  lastRunUrl: 'https://github.com/syrabit/syrabit/actions/runs/123',
  workflowUrl:
    'https://github.com/syrabit/syrabit/actions/workflows/cf-waf-drift-daily.yml',
  silentThresholdSeconds: 129600,
};

describe('CfWafDriftCronPill', () => {
  it('renders the healthy state with the green container class, healthy header, "CRON HEALTHY" pill, and the heartbeat-age caption', () => {
    render(<CfWafDriftCronPill data={baseHealthy} loading={false} onRefresh={() => {}} />);

    const tile = screen.getByTestId(TILE);
    // Container colour mapping — emerald for healthy.
    expect(tile.className).toMatch(/bg-emerald-50/);
    expect(tile.className).toMatch(/border-emerald-200/);

    // Header copy + colour.
    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Firewall drift cron — checking in');
    expect(status.className).toMatch(/text-emerald-600/);

    // Pill label + colour.
    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('CRON HEALTHY');
    expect(pill.className).toMatch(/bg-emerald-100/);
    expect(pill.className).toMatch(/text-emerald-700/);

    // Heartbeat-age caption: 3600s -> "1h".
    expect(tile).toHaveTextContent('Last heartbeat 1h ago');
    // No degraded suffix on healthy.
    expect(tile).not.toHaveTextContent('verify=');
    expect(tile).not.toHaveTextContent('aggregate=');

    // "Last run" deep-link is present when lastRunUrl is set.
    const lastRun = within(tile).getByTestId(LAST_RUN_LINK);
    expect(lastRun).toHaveAttribute('href', baseHealthy.lastRunUrl);

    // The Runs link always points at the workflow page.
    const runsLink = within(tile).getByTestId(RUN_LINK);
    expect(runsLink).toHaveAttribute('href', baseHealthy.workflowUrl);

    // Task #882 — convention check: the refresh button must be
    // queryable as `<prefix>-refresh` per the AdminHealth cron-pill
    // testId convention block in replit.md.
    expect(within(tile).getByTestId(REFRESH)).toBeTruthy();
  });

  it('renders the silent state in red with the silent header, "CRON SILENT" pill, and a stale heartbeat caption (days)', () => {
    render(
      <CfWafDriftCronPill
        data={{
          ...baseHealthy,
          status: 'silent',
          lastHeartbeatAgeSeconds: 200_000, // ~2.3 days
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-red-50/);
    expect(tile.className).toMatch(/border-red-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Firewall drift cron — silent');
    expect(status.className).toMatch(/text-red-600/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('CRON SILENT');
    expect(pill.className).toMatch(/bg-red-100/);
    expect(pill.className).toMatch(/text-red-700/);

    // 200000s -> floor(200000/86400) = 2d.
    expect(tile).toHaveTextContent('Last heartbeat 2d ago');

    // No verify/aggregate suffix even if RCs are present — only `degraded` shows them.
    expect(tile).not.toHaveTextContent('verify=');
    expect(tile).not.toHaveTextContent('aggregate=');
  });

  it('renders the degraded state in amber with the verify+aggregate exit-code suffix', () => {
    render(
      <CfWafDriftCronPill
        data={{
          ...baseHealthy,
          status: 'degraded',
          lastHeartbeatAgeSeconds: 90, // 1m
          lastStatus: 'drift',
          lastVerifyRc: 0,
          lastAggregateRc: 7,
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-amber-50/);
    expect(tile.className).toMatch(/border-amber-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Firewall drift cron — last run flagged');
    expect(status.className).toMatch(/text-amber-600/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('CRON DEGRADED');
    expect(pill.className).toMatch(/bg-amber-100/);
    expect(pill.className).toMatch(/text-amber-700/);

    // 90s -> "1m" (floor(90/60)).
    expect(tile).toHaveTextContent('Last heartbeat 1m ago');
    // Both RCs present + lastStatus -> "drift (verify=0, aggregate=7)".
    expect(tile).toHaveTextContent('drift (verify=0, aggregate=7)');
  });

  it('on degraded with only an aggregate RC, the suffix shows only the aggregate exit code', () => {
    render(
      <CfWafDriftCronPill
        data={{
          ...baseHealthy,
          status: 'degraded',
          lastHeartbeatAgeSeconds: 5, // 5s — sub-minute formatting
          lastStatus: 'failure',
          lastVerifyRc: null,
          lastAggregateRc: 3,
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );
    const tile = screen.getByTestId(TILE);
    // 5s -> "5s" (sub-minute).
    expect(tile).toHaveTextContent('Last heartbeat 5s ago');
    expect(tile).toHaveTextContent('failure (aggregate=3)');
    expect(tile).not.toHaveTextContent('verify=');
  });

  it('renders the never_observed state with the gray container, "NEVER OBSERVED" pill, and the no-heartbeat fallback caption', () => {
    render(
      <CfWafDriftCronPill
        data={{
          status: 'never_observed',
          configured: true,
          lastHeartbeatAgeSeconds: null,
          // No lastRunUrl, no RCs.
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-gray-50/);
    expect(tile.className).toMatch(/border-gray-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Firewall drift cron — no heartbeat yet');
    expect(status.className).toMatch(/text-gray-500/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('NEVER OBSERVED');
    expect(pill.className).toMatch(/bg-gray-100/);
    expect(pill.className).toMatch(/text-gray-600/);

    // Fallback caption when no heartbeat age available.
    expect(tile).toHaveTextContent('No heartbeat recorded yet');
    expect(tile).not.toHaveTextContent('Last heartbeat');

    // No "Last run" link when lastRunUrl is missing.
    expect(within(tile).queryByTestId(LAST_RUN_LINK)).toBeNull();

    // Even though lastRunUrl is absent, the workflow Runs link still
    // renders against the default workflow URL.
    const runsLink = within(tile).getByTestId(RUN_LINK);
    expect(runsLink.getAttribute('href')).toContain(
      'cf-waf-drift-daily.yml',
    );
  });

  it('renders the not_configured state with the gray container and the "NOT CONFIGURED" pill', () => {
    render(
      <CfWafDriftCronPill
        data={{
          status: 'not_configured',
          configured: false,
          lastHeartbeatAgeSeconds: null,
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-gray-50/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Firewall drift cron — not configured');

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('NOT CONFIGURED');
    expect(pill.className).toMatch(/bg-gray-100/);

    // No heartbeat caption when the secret isn't configured.
    expect(tile).toHaveTextContent('No heartbeat recorded yet');

    // No "Last run" link when lastRunUrl is missing.
    expect(within(tile).queryByTestId(LAST_RUN_LINK)).toBeNull();
  });

  it('omits the "Last run" link on healthy when lastRunUrl is not set', () => {
    // Guards the conditional rendering of the deep-link independently
    // of status — even healthy pills should hide it when the heartbeat
    // hasn't carried a run URL yet.
    const { lastRunUrl: _omit, ...withoutRunUrl } = baseHealthy;
    render(<CfWafDriftCronPill data={withoutRunUrl} loading={false} onRefresh={() => {}} />);

    const tile = screen.getByTestId(TILE);
    expect(within(tile).queryByTestId(LAST_RUN_LINK)).toBeNull();
    // The always-on Runs link remains.
    expect(within(tile).getByTestId(RUN_LINK)).toBeTruthy();
  });
});
