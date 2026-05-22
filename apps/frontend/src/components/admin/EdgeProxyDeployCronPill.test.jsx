import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import EdgeProxyDeployCronPill from './EdgeProxyDeployCronPill';

// Task #882 — lock down the edge-proxy-deploy CI cron pill (a sibling
// of CfWafDriftCronPill / TrustpilotRefreshCronPill in AdminHealth).
// The shared <CronHealthPill> component drives the colour mapping;
// this wrapper owns the edge-proxy-specific copy: header text per
// status, a "Last run Xh ago" caption with a status-dependent suffix
// (failure / stale-threshold / in_progress fallback), and the
// optional "Last run" deep-link. A future refactor of any of these
// could silently break the pill until the next CI failure — these
// tests catch that the moment the build runs.
//
// Each case is exercised through the same `edge-proxy-deploy-cron-*`
// data-testid hooks the JSX renders (the convention block in
// replit.md) so the assertions match what an admin (or the e2e
// suite) actually sees.

const TILE = 'edge-proxy-deploy-cron-tile';
const STATUS = 'edge-proxy-deploy-cron-status';
const PILL = 'edge-proxy-deploy-cron-pill';
const RUN_LINK = 'edge-proxy-deploy-cron-run-link';
const REFRESH = 'edge-proxy-deploy-cron-refresh';
const LAST_RUN_LINK = 'edge-proxy-deploy-cron-last-run-link';

const baseHealthy = {
  configured: true,
  status: 'healthy',
  conclusion: 'success',
  html_url: 'https://github.com/syrabit/syrabit/actions/runs/777',
  lastRunUrl: 'https://github.com/syrabit/syrabit/actions/runs/777',
  updated_at: '2026-04-25T10:00:00Z',
  ageSeconds: 3600, // 1h
  runStatus: 'completed',
  workflowUrl:
    'https://github.com/syrabit/syrabit/actions/workflows/edge-proxy-deploy.yml',
  staleThresholdSeconds: 7 * 86400,
  error: null,
};

describe('EdgeProxyDeployCronPill', () => {
  it('renders the healthy state in green with the success conclusion suffix and the heartbeat-age caption', () => {
    render(<EdgeProxyDeployCronPill data={baseHealthy} loading={false} onRefresh={() => {}} />);

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-emerald-50/);
    expect(tile.className).toMatch(/border-emerald-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Edge-proxy deploy CI — passing');
    expect(status.className).toMatch(/text-emerald-600/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('CI HEALTHY');
    expect(pill.className).toMatch(/bg-emerald-100/);
    expect(pill.className).toMatch(/text-emerald-700/);

    // 3600s -> "1h"; suffix shows "success" so a green pill still
    // says what just happened.
    expect(tile).toHaveTextContent('Last run 1h ago · success');

    // The "Last run" deep-link points at the GitHub run.
    const lastRun = within(tile).getByTestId(LAST_RUN_LINK);
    expect(lastRun).toHaveAttribute('href', baseHealthy.html_url);

    // The Runs link always points at the workflow page.
    const runsLink = within(tile).getByTestId(RUN_LINK);
    expect(runsLink).toHaveAttribute('href', baseHealthy.workflowUrl);

    // Convention check: every cron pill must expose
    // `<prefix>-{tile,status,pill,run-link,refresh}` per the
    // AdminHealth cron-pill testId convention block in replit.md.
    // The other testIds are exercised above; lock down `-refresh`
    // here so a future CronHealthPill refactor that drops the
    // primary testId is caught immediately.
    expect(within(tile).getByTestId(REFRESH)).toBeTruthy();
  });

  it('renders the silent (failure) state in red with the failure conclusion suffix', () => {
    render(
      <EdgeProxyDeployCronPill
        data={{
          ...baseHealthy,
          status: 'silent',
          conclusion: 'failure',
          ageSeconds: 600, // 10m
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-red-50/);
    expect(tile.className).toMatch(/border-red-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Edge-proxy deploy CI — last run failed');
    expect(status.className).toMatch(/text-red-600/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('CI FAILED');
    expect(pill.className).toMatch(/bg-red-100/);
    expect(pill.className).toMatch(/text-red-700/);

    // 600s -> "10m"; suffix shows the failure conclusion verbatim.
    expect(tile).toHaveTextContent('Last run 10m ago · failure');
  });

  it('renders the degraded (stale) state in amber with the >7d threshold suffix', () => {
    render(
      <EdgeProxyDeployCronPill
        data={{
          ...baseHealthy,
          status: 'degraded',
          conclusion: 'success',
          ageSeconds: 9 * 86400, // 9d
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-amber-50/);
    expect(tile.className).toMatch(/border-amber-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Edge-proxy deploy CI — last run is stale');
    expect(status.className).toMatch(/text-amber-600/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('RUN STALE');
    expect(pill.className).toMatch(/bg-amber-100/);
    expect(pill.className).toMatch(/text-amber-700/);

    // 9 days -> "9d"; suffix shows the stale-threshold reminder.
    expect(tile).toHaveTextContent('Last run 9d ago · stale (>7d)');
  });

  it('falls back to the runStatus when conclusion is null but the run is in progress', () => {
    // Mid-deploy: GitHub returns status="in_progress" with a null
    // conclusion. The pill should still be green (no failure) and
    // surface the in-progress signal rather than going blank.
    render(
      <EdgeProxyDeployCronPill
        data={{
          ...baseHealthy,
          status: 'healthy',
          conclusion: null,
          runStatus: 'in_progress',
          ageSeconds: 30, // 30s
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );
    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-emerald-50/);
    expect(tile).toHaveTextContent('Last run 30s ago · in_progress');
  });

  it('renders the never_observed state with the gray container and the no-run fallback caption', () => {
    render(
      <EdgeProxyDeployCronPill
        data={{
          configured: true,
          status: 'never_observed',
          conclusion: null,
          html_url: null,
          lastRunUrl: null,
          updated_at: null,
          ageSeconds: null,
          workflowUrl:
            'https://github.com/syrabit/syrabit/actions/workflows/edge-proxy-deploy.yml',
          staleThresholdSeconds: 7 * 86400,
          error: null,
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-gray-50/);
    expect(tile.className).toMatch(/border-gray-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Edge-proxy deploy CI — no run yet');
    expect(status.className).toMatch(/text-gray-500/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('NEVER RUN');
    expect(pill.className).toMatch(/bg-gray-100/);

    // No "Last run" link when html_url is missing.
    expect(within(tile).queryByTestId(LAST_RUN_LINK)).toBeNull();
    // Fallback caption.
    expect(tile).toHaveTextContent('No run recorded yet');
  });

  it('renders the not_configured state with the gray container and the "NOT CONFIGURED" pill', () => {
    render(
      <EdgeProxyDeployCronPill
        data={{
          configured: false,
          status: 'not_configured',
          conclusion: null,
          html_url: null,
          lastRunUrl: null,
          updated_at: null,
          ageSeconds: null,
          workflowUrl:
            'https://github.com/syrabit/syrabit/actions/workflows/edge-proxy-deploy.yml',
          staleThresholdSeconds: 7 * 86400,
          error: null,
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-gray-50/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Edge-proxy deploy CI — not configured');

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('NOT CONFIGURED');

    // No "Last run" link when no run URL has ever arrived.
    expect(within(tile).queryByTestId(LAST_RUN_LINK)).toBeNull();
    // Fallback caption.
    expect(tile).toHaveTextContent('No run recorded yet');
  });

  it('omits the "Last run" link on healthy when html_url is not set', () => {
    // Guards the conditional rendering of the deep-link independently
    // of status — even healthy pills should hide it when the run has
    // not produced an html_url yet.
    const { html_url: _omitHtml, lastRunUrl: _omitLast, ...withoutRunUrl } = baseHealthy;
    render(<EdgeProxyDeployCronPill data={withoutRunUrl} loading={false} onRefresh={() => {}} />);

    const tile = screen.getByTestId(TILE);
    expect(within(tile).queryByTestId(LAST_RUN_LINK)).toBeNull();
    // The always-on Runs link remains.
    expect(within(tile).getByTestId(RUN_LINK)).toBeTruthy();
  });
});
