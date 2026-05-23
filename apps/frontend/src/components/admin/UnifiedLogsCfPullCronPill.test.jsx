import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import UnifiedLogsCfPullCronPill from './UnifiedLogsCfPullCronPill';

// Task #956 — lock down the unified-logs Cloudflare GraphQL pull
// silence pill. The shared <CronHealthPill> drives the colour
// mapping; this wrapper owns the unified-logs-specific copy:
// header text per status, the "Last cursor advance Xh ago" caption
// with the lease-owner suffix and the on-red "no successful pull"
// suffix, and the optional "Status JSON" deep-link. A future
// refactor of any of these could silently break the pill until
// the cron next goes silent — these tests catch that the moment
// the build runs.
//
// Each case is exercised through the same
// `unified-logs-cf-pull-cron-*` data-testid hooks the JSX renders
// (the convention block in replit.md) so the assertions match
// what an admin (or the e2e suite) actually sees.

const TILE = 'unified-logs-cf-pull-cron-tile';
const STATUS = 'unified-logs-cf-pull-cron-status';
const PILL = 'unified-logs-cf-pull-cron-pill';
const RUN_LINK = 'unified-logs-cf-pull-cron-run-link';
const REFRESH = 'unified-logs-cf-pull-cron-refresh';
const STATUS_LINK = 'unified-logs-cf-pull-cron-status-link';

const baseHealthy = {
  configured: true,
  status: 'healthy',
  lastUpdatedTs: 1_700_000_000,
  lastUpdatedAt: '2026-04-26T05:00:00Z',
  lastUpdatedAgeSeconds: 3600, // 1h
  leaseOwner: 'replica-A',
  leaseExpiresAt: '2026-04-26T05:30:00Z',
  cursor: 'cursor-xyz',
  silentThresholdSeconds: 900,
  statusUrl: '/api/admin/logs/status',
};

describe('UnifiedLogsCfPullCronPill', () => {
  it('renders the healthy state in green with the cursor-age caption and the lease-owner suffix', () => {
    render(<UnifiedLogsCfPullCronPill data={baseHealthy} loading={false} onRefresh={() => {}} />);

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-emerald-50/);
    expect(tile.className).toMatch(/border-emerald-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Cloudflare log ingest — flowing');
    expect(status.className).toMatch(/text-emerald-600/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('INGEST HEALTHY');
    expect(pill.className).toMatch(/bg-emerald-100/);
    expect(pill.className).toMatch(/text-emerald-700/);

    // 3600s -> "1h"; lease owner appended on healthy too.
    expect(tile).toHaveTextContent('Last cursor advance 1h ago · lease: replica-A');
    // No "no successful pull" suffix on healthy.
    expect(tile).not.toHaveTextContent('no successful pull');

    // The Status JSON deep-link points at the backend statusUrl.
    const statusLink = within(tile).getByTestId(STATUS_LINK);
    expect(statusLink).toHaveAttribute('href', baseHealthy.statusUrl);

    // The Runs link falls back to the same statusUrl since this
    // cron has no GitHub Actions workflow page.
    const runsLink = within(tile).getByTestId(RUN_LINK);
    expect(runsLink).toHaveAttribute('href', baseHealthy.statusUrl);

    // Convention check: every cron pill must expose
    // `<prefix>-{tile,status,pill,run-link,refresh}` per the
    // AdminHealth cron-pill testId convention block in replit.md.
    expect(within(tile).getByTestId(REFRESH)).toBeTruthy();
  });

  it('renders the silent state in red with the "no successful pull" suffix and the lease owner', () => {
    render(
      <UnifiedLogsCfPullCronPill
        data={{
          ...baseHealthy,
          status: 'silent',
          lastUpdatedAgeSeconds: 200_000, // ~2.3 days
          leaseOwner: 'replica-zombie',
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-red-50/);
    expect(tile.className).toMatch(/border-red-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Cloudflare log ingest — silent');
    expect(status.className).toMatch(/text-red-600/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('INGEST SILENT');
    expect(pill.className).toMatch(/bg-red-100/);
    expect(pill.className).toMatch(/text-red-700/);

    // 200000s -> floor(200000/86400) = 2d.
    expect(tile).toHaveTextContent(
      'Last cursor advance 2d ago · no successful pull · lease: replica-zombie',
    );
  });

  it('renders the never_observed state in gray with the no-pull fallback caption and no lease suffix', () => {
    render(
      <UnifiedLogsCfPullCronPill
        data={{
          configured: true,
          status: 'never_observed',
          lastUpdatedTs: null,
          lastUpdatedAt: null,
          lastUpdatedAgeSeconds: null,
          leaseOwner: null,
          leaseExpiresAt: null,
          cursor: null,
          silentThresholdSeconds: 900,
          statusUrl: '/api/admin/logs/status',
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-gray-50/);
    expect(tile.className).toMatch(/border-gray-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Cloudflare log ingest — no pull yet');
    expect(status.className).toMatch(/text-gray-500/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('NEVER OBSERVED');
    expect(pill.className).toMatch(/bg-gray-100/);
    expect(pill.className).toMatch(/text-gray-600/);

    // Fallback caption when no successful pull recorded yet,
    // and no lease-owner suffix appended when the lock doc has
    // not yet been claimed by any replica.
    expect(tile).toHaveTextContent('No successful pull recorded yet');
    expect(tile).not.toHaveTextContent('lease:');
  });

  it('renders the not_configured state in gray with the "NOT CONFIGURED" pill and no Status JSON link when statusUrl is absent', () => {
    render(
      <UnifiedLogsCfPullCronPill
        data={{
          configured: false,
          status: 'not_configured',
          lastUpdatedAgeSeconds: null,
          leaseOwner: null,
          // statusUrl intentionally omitted to assert the
          // optional deep-link is conditional, while the always-on
          // Runs link still renders against the safety-net default.
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-gray-50/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Cloudflare log ingest — not configured');

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('NOT CONFIGURED');

    expect(tile).toHaveTextContent('No successful pull recorded yet');
    expect(tile).not.toHaveTextContent('lease:');

    // No optional Status JSON deep-link when the backend hasn't
    // supplied a statusUrl on the response.
    expect(within(tile).queryByTestId(STATUS_LINK)).toBeNull();
    // The always-on Runs link still resolves to the safety-net
    // default so admins always have a target to click.
    const runsLink = within(tile).getByTestId(RUN_LINK);
    expect(runsLink.getAttribute('href')).toContain('/api/admin/logs/status');
  });

  it('omits the lease suffix when the lease owner is blank/whitespace', () => {
    render(
      <UnifiedLogsCfPullCronPill
        data={{
          ...baseHealthy,
          leaseOwner: '   ',
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );
    const tile = screen.getByTestId(TILE);
    // Whitespace-only lease owner collapses out of the caption.
    expect(tile).toHaveTextContent('Last cursor advance 1h ago');
    expect(tile).not.toHaveTextContent('lease:');
  });

  // Task #979 — wrapper-level forwarding test for the per-env
  // missing-Slack-webhook page-history disclosure. The badge is
  // rendered inside the shared <CronHealthPill>, but reaches it
  // *only* through this wrapper. If the wrapper drops the
  // `slackMissingAlertHistory` prop on the floor, AdminHealth's
  // 60s polling load would still happen but the disclosure would
  // never appear for this env. This test pins the passthrough so a
  // refactor that renames or forgets the prop fails loudly here.
  it('forwards slackMissingAlertHistory so the SlackConfigBadge renders the "Recent pages" disclosure when the env is red', () => {
    render(
      <UnifiedLogsCfPullCronPill
        data={{
          ...baseHealthy,
          status: 'silent',
          slackConfigured: false,
          slackWebhookEnv: 'UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK',
        }}
        loading={false}
        onRefresh={() => {}}
        slackMissingAlertHistory={{
          lockId: 'slack_webhook_missing/UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK',
          limit: 10,
          events: [
            {
              id: 'evt-1',
              pagedAt: '2026-04-26T10:00:00.000Z',
              kind: 'broken',
              subKind: 'missing',
            },
            {
              id: 'evt-2',
              pagedAt: '2026-04-26T08:00:00.000Z',
              kind: 'recovered',
            },
          ],
        }}
      />,
    );
    const tile = screen.getByTestId(TILE);
    // The disclosure toggle uses the shared
    // `<prefix>-slack-config-history-toggle` test-id convention.
    const toggle = within(tile).getByTestId('unified-logs-cf-pull-cron-slack-config-history-toggle');
    expect(toggle).toHaveTextContent('Recent pages (2)');
  });
});
