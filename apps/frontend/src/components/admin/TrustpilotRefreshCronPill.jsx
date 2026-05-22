import React from 'react';
import CronHealthPill from './CronHealthPill';
import { captionLine, joinCaptionParts } from './cronCaptionHelpers';

// Task #838 — sibling pill for the daily Trustpilot
// aggregate-refresh GitHub Actions cron (Task #751,
// /admin/health/trustpilot/refresh-cron). Same shape as the
// firewall-drift wrapper next to it, but with Trustpilot-specific
// header copy and a TWO-line caption that surfaces both the most
// recent successful heartbeat (the one that actually proves the
// cron ran end-to-end) AND the most recent any-status heartbeat
// (so a flapping cron that pings successfully every other run is
// visible without clicking through to GitHub Actions).
//
// Extracted from AdminHealth.jsx so the colour mapping, header
// copy, and the dual-heartbeat caption can be unit-tested in
// isolation (see TrustpilotRefreshCronPill.test.jsx). The visual
// output is unchanged from the prior inline <CronHealthPill ...>
// block; only the testId moved from "trustpilot-cron" to
// "trustpilot-refresh-cron" so it lines up with the cf-waf-drift
// pill's naming convention and reads correctly in DOM dumps
// (e.g. "trustpilot-refresh-cron-pill" vs "trustpilot-cron-pill").

const HEADER_TEXT_BY_STATUS = {
  healthy: 'Trustpilot refresh cron — checking in',
  silent: 'Trustpilot refresh cron — silent',
  degraded: 'Trustpilot refresh cron — last run failed',
  never_observed: 'Trustpilot refresh cron — no heartbeat yet',
  not_configured: 'Trustpilot refresh cron — not configured',
  unknown: 'Trustpilot refresh cron — status unknown',
};

const DEFAULT_WORKFLOW_URL =
  'https://github.com/syrabit/syrabit/actions/workflows/trustpilot-aggregate-refresh.yml';

const renderSubText = ({ data, ageLabel: fmt }) => {
  const successLbl = fmt(data?.lastSuccessHeartbeatAgeSeconds);
  const anyLbl = fmt(data?.lastHeartbeatAgeSeconds);
  // Two-line caption logic (extracted unchanged from AdminHealth.jsx):
  //   - Always show the last successful heartbeat (or a "no successful
  //     heartbeat recorded" fallback if the cron has never finished
  //     successfully).
  //   - When a more recent ANY-status heartbeat exists (or no
  //     successful one is known), append " · last heartbeat (any) Xm
  //     ago" so admins can tell apart "cron is silent" from "cron
  //     keeps running but keeps failing".
  const primary = captionLine(
    'Last successful heartbeat',
    successLbl,
    'No successful heartbeat recorded',
  );
  const anySuffix = anyLbl && (!successLbl || anyLbl !== successLbl)
    ? captionLine('last heartbeat (any)', anyLbl, '')
    : '';
  return joinCaptionParts([primary, anySuffix]);
};

// Task #843 — backwards-compat aliases for the pre-#838 testId
// namespace. Task #838 renamed the Trustpilot pill's testId from
// "trustpilot-cron-*" to "trustpilot-refresh-cron-*" so it lines up
// with the cf-waf-drift pill. The in-repo sweep was clean, but
// out-of-repo surfaces (an external Playwright suite, a Cloudflare
// Browser Rendering uptime probe, a Sentry visual-regression
// baseline, an admin runbook screenshot) cannot be verified from
// this repo. To prevent silent breakage of any selector-existence
// check using the old prefix, we render a `hidden` sibling element
// per legacy testId. Notes on the deliberate trade-off:
//
//   * Selector-existence assertions (querySelector,
//     `page.getByTestId('trustpilot-cron-tile')`, `locator(...).count()`)
//     will keep finding an element with the old prefix.
//   * Visibility-strict assertions (`toBeVisible()`, screenshot
//     diffing) are NOT preserved because the alias element is
//     `hidden` / `aria-hidden`. Any consumer relying on those
//     semantics still needs to migrate to `trustpilot-refresh-cron-*`.
//
// DELETE BY 2026-07-24 (90 days from the rename). Tracking task: see
// the follow-up for "Remove the trustpilot-cron-* backwards-compat
// alias once external selector migration is complete".
const LEGACY_TESTID_SUFFIXES = ['tile', 'status', 'pill', 'run-link', 'refresh'];

export default function TrustpilotRefreshCronPill({
  data, loading, onRefresh,
  // Task #902 — alerter-state lock-doc snapshot from
  // /admin/health/trustpilot/refresh-cron/alert-state. Optional;
  // see EdgeProxyDeployCronPill for the same prop's behaviour.
  alertState,
  // Task #918 — paged-on-call audit log from
  // /admin/health/trustpilot/refresh-cron/alert-history. Optional;
  // see EdgeProxyDeployCronPill for the same prop's behaviour.
  alertHistory, onLoadAlertHistory,
}) {
  return (
    <>
      <CronHealthPill
        data={data}
        loading={loading}
        onRefresh={onRefresh}
        testId="trustpilot-refresh-cron"
        defaultWorkflowUrl={DEFAULT_WORKFLOW_URL}
        headerTextByStatus={HEADER_TEXT_BY_STATUS}
        renderSubText={renderSubText}
        alertState={alertState}
        alertHistory={alertHistory}
        onLoadAlertHistory={onLoadAlertHistory}
      />
      <div hidden aria-hidden="true" data-legacy-alias-for="trustpilot-refresh-cron">
        {LEGACY_TESTID_SUFFIXES.map((suffix) => (
          <span key={suffix} data-testid={`trustpilot-cron-${suffix}`} />
        ))}
      </div>
    </>
  );
}
