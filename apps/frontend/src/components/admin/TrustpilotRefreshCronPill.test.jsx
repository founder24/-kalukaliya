import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import TrustpilotRefreshCronPill from './TrustpilotRefreshCronPill';

// Task #838 — lock down the Trustpilot refresh-cron pill that has
// rendered above the cf-waf-drift pill in AdminHealth since Task #755.
// The shared <CronHealthPill> drives the colour/icon mapping and the
// ageLabel formatter (covered by Task #837). The wrapper here owns
// the Trustpilot-specific copy: header text per status, the
// dual-heartbeat caption ("Last successful heartbeat …" optionally
// followed by " · last heartbeat (any) …"), the default workflow URL,
// and the testId namespace ("trustpilot-refresh-cron-*"). A future
// refactor of any of these would silently break the pill until the
// cron next went silent — these tests catch it the moment the build
// runs.
//
// Cases mirror CfWafDriftCronPill.test.jsx (Task #836) so every cron
// pill in AdminHealth has identical wrapper-level coverage.

const TILE = 'trustpilot-refresh-cron-tile';
const STATUS = 'trustpilot-refresh-cron-status';
const PILL = 'trustpilot-refresh-cron-pill';
const RUN_LINK = 'trustpilot-refresh-cron-run-link';

const baseHealthy = {
  status: 'healthy',
  configured: true,
  lastHeartbeatTs: 1_700_000_000,
  // Both heartbeats present and equal — the "any" suffix should NOT
  // render in this case (it's only there to differentiate flapping
  // crons from happy ones).
  lastSuccessHeartbeatAgeSeconds: 3600, // 1h ago
  lastHeartbeatAgeSeconds: 3600,
  workflowUrl:
    'https://github.com/syrabit/syrabit/actions/workflows/trustpilot-aggregate-refresh.yml',
  silentThresholdSeconds: 129600,
};

describe('TrustpilotRefreshCronPill', () => {
  it('renders the healthy state with the green container, healthy header, "CRON HEALTHY" pill, and the single-line success heartbeat caption', () => {
    render(<TrustpilotRefreshCronPill data={baseHealthy} loading={false} onRefresh={() => {}} />);

    const tile = screen.getByTestId(TILE);
    // Container colour mapping — emerald for healthy.
    expect(tile.className).toMatch(/bg-emerald-50/);
    expect(tile.className).toMatch(/border-emerald-200/);

    // Header copy + colour.
    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Trustpilot refresh cron — checking in');
    expect(status.className).toMatch(/text-emerald-600/);

    // Pill label + colour (sourced from CronHealthPill's defaults
    // since this wrapper does not override pillLabelByStatus).
    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('CRON HEALTHY');
    expect(pill.className).toMatch(/bg-emerald-100/);
    expect(pill.className).toMatch(/text-emerald-700/);

    // Heartbeat caption: 3600s -> "1h", and since success === any
    // (both 3600s), the " · last heartbeat (any) …" suffix is omitted.
    expect(tile).toHaveTextContent('Last successful heartbeat 1h ago');
    expect(tile).not.toHaveTextContent('last heartbeat (any)');

    // The Runs link uses the heartbeat-supplied workflow URL.
    const runsLink = within(tile).getByTestId(RUN_LINK);
    expect(runsLink).toHaveAttribute('href', baseHealthy.workflowUrl);
  });

  it('appends the " · last heartbeat (any) …" suffix when the latest any-status heartbeat is more recent than the latest success', () => {
    // Simulates a flapping cron: the most recent run failed (so any=5m)
    // but a successful run did happen earlier (success=2h). Both lines
    // must render so the admin sees both.
    render(
      <TrustpilotRefreshCronPill
        data={{
          ...baseHealthy,
          status: 'degraded',
          lastSuccessHeartbeatAgeSeconds: 7200, // 2h
          lastHeartbeatAgeSeconds: 300,         // 5m (fresher = failure)
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile).toHaveTextContent('Last successful heartbeat 2h ago');
    expect(tile).toHaveTextContent('last heartbeat (any) 5m ago');
  });

  it('renders the silent state in red with the silent header, "CRON SILENT" pill, and a stale heartbeat caption (days)', () => {
    render(
      <TrustpilotRefreshCronPill
        data={{
          ...baseHealthy,
          status: 'silent',
          // Both stale at the same age — single-line caption only.
          lastSuccessHeartbeatAgeSeconds: 200_000, // ~2.3 days
          lastHeartbeatAgeSeconds: 200_000,
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-red-50/);
    expect(tile.className).toMatch(/border-red-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Trustpilot refresh cron — silent');
    expect(status.className).toMatch(/text-red-600/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('CRON SILENT');
    expect(pill.className).toMatch(/bg-red-100/);
    expect(pill.className).toMatch(/text-red-700/);

    // 200000s -> floor(200000/86400) = 2d.
    expect(tile).toHaveTextContent('Last successful heartbeat 2d ago');
    // Suffix omitted because any === success.
    expect(tile).not.toHaveTextContent('last heartbeat (any)');
  });

  it('renders the degraded state in amber with the degraded header and "CRON DEGRADED" pill', () => {
    render(
      <TrustpilotRefreshCronPill
        data={{
          ...baseHealthy,
          status: 'degraded',
          lastSuccessHeartbeatAgeSeconds: 3600,
          lastHeartbeatAgeSeconds: 90, // 1m
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-amber-50/);
    expect(tile.className).toMatch(/border-amber-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Trustpilot refresh cron — last run failed');
    expect(status.className).toMatch(/text-amber-600/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('CRON DEGRADED');
    expect(pill.className).toMatch(/bg-amber-100/);
    expect(pill.className).toMatch(/text-amber-700/);

    // Both heartbeats: success=1h, any=1m — both lines render.
    expect(tile).toHaveTextContent('Last successful heartbeat 1h ago');
    expect(tile).toHaveTextContent('last heartbeat (any) 1m ago');
  });

  it('renders the never_observed state with the gray container, "NEVER OBSERVED" pill, and the no-success-heartbeat fallback caption', () => {
    render(
      <TrustpilotRefreshCronPill
        data={{
          status: 'never_observed',
          configured: true,
          lastSuccessHeartbeatAgeSeconds: null,
          lastHeartbeatAgeSeconds: null,
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-gray-50/);
    expect(tile.className).toMatch(/border-gray-200/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Trustpilot refresh cron — no heartbeat yet');
    expect(status.className).toMatch(/text-gray-500/);

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('NEVER OBSERVED');
    expect(pill.className).toMatch(/bg-gray-100/);
    expect(pill.className).toMatch(/text-gray-600/);

    // Fallback caption when no successful heartbeat has been seen.
    expect(tile).toHaveTextContent('No successful heartbeat recorded');
    // No "any" suffix when both heartbeats are absent.
    expect(tile).not.toHaveTextContent('last heartbeat (any)');

    // Even though the heartbeat row is empty, the workflow Runs link
    // still renders against the wrapper's defaultWorkflowUrl.
    const runsLink = within(tile).getByTestId(RUN_LINK);
    expect(runsLink.getAttribute('href')).toContain(
      'trustpilot-aggregate-refresh.yml',
    );
  });

  it('renders the not_configured state with the gray container and the "NOT CONFIGURED" pill', () => {
    render(
      <TrustpilotRefreshCronPill
        data={{
          status: 'not_configured',
          configured: false,
          lastSuccessHeartbeatAgeSeconds: null,
          lastHeartbeatAgeSeconds: null,
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile.className).toMatch(/bg-gray-50/);

    const status = within(tile).getByTestId(STATUS);
    expect(status).toHaveTextContent('Trustpilot refresh cron — not configured');

    const pill = within(tile).getByTestId(PILL);
    expect(pill).toHaveTextContent('NOT CONFIGURED');
    expect(pill.className).toMatch(/bg-gray-100/);

    // No heartbeat caption when the secret isn't configured — falls
    // back to the no-success message.
    expect(tile).toHaveTextContent('No successful heartbeat recorded');

    // Default workflow URL still drives the Runs link even when
    // the heartbeat doesn't carry one.
    const runsLink = within(tile).getByTestId(RUN_LINK);
    expect(runsLink.getAttribute('href')).toContain(
      'trustpilot-aggregate-refresh.yml',
    );
  });

  it('emits the Task #843 backwards-compat alias for every legacy trustpilot-cron-* testId so out-of-repo selector-existence checks keep passing', () => {
    // Lock down the hidden alias element added in Task #843 so it
    // cannot be silently removed before the 2026-07-24 delete-by date
    // without an explicit follow-up. Selector-existence is the ONLY
    // contract preserved (visibility-strict assertions are explicitly
    // not — see the comment in the wrapper file).
    render(<TrustpilotRefreshCronPill data={baseHealthy} loading={false} onRefresh={() => {}} />);

    const aliasSuffixes = ['tile', 'status', 'pill', 'run-link', 'refresh'];
    for (const suffix of aliasSuffixes) {
      // Use queryAllByTestId because the live element + alias may both
      // exist when the suffix overlaps (the new namespace uses
      // `trustpilot-refresh-cron-*` so there is no collision today,
      // but use the defensive query anyway).
      const matches = screen.queryAllByTestId(`trustpilot-cron-${suffix}`);
      expect(matches.length).toBeGreaterThan(0);
    }

    // Sanity check: the alias parent carries the documented marker
    // attribute so a future grep for "data-legacy-alias-for" finds it.
    const aliasParent = document.querySelector(
      '[data-legacy-alias-for="trustpilot-refresh-cron"]',
    );
    expect(aliasParent).not.toBeNull();
    // And the alias parent is `hidden` / `aria-hidden` so it does not
    // leak into the accessible tree or visual layout — this is the
    // documented trade-off (selector-existence preserved, visibility
    // semantics not).
    expect(aliasParent.hasAttribute('hidden')).toBe(true);
    expect(aliasParent.getAttribute('aria-hidden')).toBe('true');
  });

  it('shows only the no-success message when only the any-heartbeat is missing AND no success has been observed', () => {
    // Edge case: cron has never succeeded but also has no any-status
    // heartbeat (e.g. the very first observation window). Caption
    // collapses to the single fallback line — the " · last heartbeat
    // (any) …" suffix MUST NOT render with an empty time label.
    render(
      <TrustpilotRefreshCronPill
        data={{
          status: 'never_observed',
          configured: true,
          lastSuccessHeartbeatAgeSeconds: null,
          lastHeartbeatAgeSeconds: null,
        }}
        loading={false}
        onRefresh={() => {}}
      />,
    );

    const tile = screen.getByTestId(TILE);
    expect(tile).toHaveTextContent('No successful heartbeat recorded');
    expect(tile).not.toHaveTextContent('last heartbeat (any)');
    // Guard the join character too — no orphan " · " left over.
    expect(tile.textContent).not.toMatch(/recorded\s*·/);
  });
});
