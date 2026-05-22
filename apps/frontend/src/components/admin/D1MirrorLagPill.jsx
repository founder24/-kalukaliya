import React from 'react';
import CronHealthPill, { ageLabel } from './CronHealthPill';
import { joinCaptionParts } from './cronCaptionHelpers';

// Task #508 — sibling pill for the D1 mirror lag alerter (Task #460).
// The alerter pages on-call when the cross-replica nightly mirror
// lease's `last_fired_at` (or the in-process snapshot, whichever is
// fresher) ages past the configured threshold. The data source is
// the existing `/admin/health/d1-mirror/lag` endpoint, which returns
// the alerter's own status vocabulary (`not_enabled`, `never_observed`,
// `breached`, `healthy`) plus the lag/threshold/streak triple and a
// projection of the lock-doc state. We adapt that vocabulary onto
// the shared <CronHealthPill> status keys so the colour mapping
// matches the sibling cron pills next to it on the dashboard:
//
//   * breached       → silent           (red)
//   * healthy        → healthy          (green)
//   * never_observed → never_observed   (gray)
//   * not_enabled    → not_configured   (gray)
//
// Caption shape: "Lag Xh / threshold Yh · streak N/M · in-process Xh,
// lease Yh ago". The streak suffix only renders when there's an
// active streak (so a clean green pill doesn't carry "streak 0/2"
// noise). The in-process / lease ages decorate the small grey caption
// AND the tooltip on the wrapping span so an admin who hovers / taps
// the line sees the precise timestamps spelled out without leaving
// the dashboard — that's what the task spec means by "hovering shows
// last sync timestamps".
//
// testIds follow the AdminHealth cron-pill convention (replit.md
// § "AdminHealth cron-pill testId convention"):
//   d1-mirror-lag-{tile,status,pill,run-link,refresh}.

const HEADER_TEXT_BY_STATUS = {
  healthy: 'D1 mirror lag — fresh',
  silent: 'D1 mirror lag — over threshold',
  never_observed: 'D1 mirror lag — no sync observed yet',
  not_configured: 'D1 mirror lag — not enabled',
  unknown: 'D1 mirror lag — status unknown',
};

const PILL_LABEL_BY_STATUS = {
  healthy: 'MIRROR FRESH',
  silent: 'LAG BREACHED',
  never_observed: 'NEVER OBSERVED',
  not_configured: 'NOT ENABLED',
};

// `/admin/cf-health` carries the broader D1 mirror snapshot
// (`d1_mirror.lag_seconds` + the row counts the alerter cross-references)
// so the always-on "Runs" link points there — it's the closest
// analogue to a deep-link for an alerter that doesn't have its own
// GitHub Actions workflow page. The backend echoes the same URL on
// `healthUrl` so the UI uses the live value when present.
const DEFAULT_HEALTH_URL = '/admin/cf-health';

// Map the alerter's vocabulary onto the shared CronHealthPill status
// keys so the colour cascade in the parent component picks the
// right palette without us having to reach into its internals.
function mapStatus(rawStatus) {
  switch (rawStatus) {
    case 'breached':
      return 'silent';
    case 'healthy':
      return 'healthy';
    case 'never_observed':
      return 'never_observed';
    case 'not_enabled':
      return 'not_configured';
    default:
      return 'unknown';
  }
}

function hoursLabel(seconds) {
  if (seconds == null || Number.isNaN(Number(seconds))) return null;
  const h = Number(seconds) / 3600;
  if (h < 0.1) return '<0.1h';
  return `${h.toFixed(1)}h`;
}

const renderSubText = ({ data }) => {
  if (!data) return null;
  const lagH = hoursLabel(data.lagSeconds);
  const thresholdH = hoursLabel(data.lagThresholdSeconds);
  const streakCur = Number(data.consecutiveBreachCount || 0);
  const streakReq = Number(data.requiredStreak || 0);
  const inProcAge = data.inProcessLastSyncTs
    ? Math.max(0, Math.floor(Date.now() / 1000 - Number(data.inProcessLastSyncTs)))
    : null;
  const leaseAge = data.leaseLastFiredTs
    ? Math.max(0, Math.floor(Date.now() / 1000 - Number(data.leaseLastFiredTs)))
    : null;
  const inProcLbl = ageLabel(inProcAge);
  const leaseLbl = ageLabel(leaseAge);

  // Primary: "Lag Xh / threshold Yh" or the no-sync fallback.
  const primary = (lagH && thresholdH)
    ? `Lag ${lagH} / threshold ${thresholdH}`
    : thresholdH
      ? `No sync observed yet · threshold ${thresholdH}`
      : 'No sync observed yet';
  // Streak: show whenever the alerter is counting (active breach or
  // a near-miss it hasn't reset yet). Hidden on a clean green pill
  // where streak=0/required so the caption stays compact.
  const streakSuffix = (streakCur > 0 && streakReq > 0)
    ? `streak ${streakCur}/${streakReq}`
    : '';
  // Per-source ages: omit each side when the timestamp is missing
  // (a freshly-promoted leader has a null in-process ts; a brand
  // new deployment has both null).
  const sourceAgeParts = [
    inProcLbl ? `in-process ${inProcLbl} ago` : '',
    leaseLbl ? `lease ${leaseLbl} ago` : '',
  ].filter(Boolean);
  const sourceSuffix = sourceAgeParts.length > 0
    ? sourceAgeParts.join(', ')
    : '';

  // Tooltip — duplicate the pieces in long-form so a hover reveals
  // the precise timestamps the visible caption truncates.
  const titleLines = [];
  if (lagH && thresholdH) {
    titleLines.push(`Current lag: ${lagH} (threshold: ${thresholdH})`);
  }
  if (streakCur > 0 || streakReq > 0) {
    titleLines.push(`Consecutive breach streak: ${streakCur} of ${streakReq} required to page`);
  }
  if (data.inProcessLastSyncTs) {
    const iso = new Date(Number(data.inProcessLastSyncTs) * 1000).toISOString();
    titleLines.push(`In-process last sync: ${iso}`);
  } else {
    titleLines.push('In-process last sync: never (this replica)');
  }
  if (data.leaseLastFiredTs) {
    const iso = new Date(Number(data.leaseLastFiredTs) * 1000).toISOString();
    titleLines.push(`Cross-replica lease last fired: ${iso}`);
  } else {
    titleLines.push('Cross-replica lease last fired: never');
  }
  if (data.lastSyncOk === false && data.lastSyncError) {
    titleLines.push(`Last in-process sync error: ${data.lastSyncError}`);
  }

  return (
    <span
      title={titleLines.join('\n')}
      data-testid="d1-mirror-lag-caption"
    >
      {joinCaptionParts([primary, streakSuffix, sourceSuffix])}
    </span>
  );
};

export default function D1MirrorLagPill({
  data, loading, onRefresh,
  // Task #508 — paged-on-call audit log from
  // /admin/health/d1-mirror/lag/alert-history, surfaced inline by
  // the shared <CronHealthPill> "Show paged history" disclosure.
  alertHistory,
  onLoadAlertHistory,
}) {
  // Adapt the alerter's status vocabulary onto the shared pill's
  // status keys without mutating the rest of the response shape so
  // the existing `/admin/health/d1-mirror/lag` consumers (the unit
  // tests in test_admin_d1_mirror_lag_alerts.py, the alerter loop's
  // own classifier) keep seeing their own vocabulary.
  const adaptedData = data && !data._error
    ? { ...data, status: mapStatus(data.status) }
    : data;
  const healthUrl = data?.healthUrl || DEFAULT_HEALTH_URL;
  // Pass the lock-doc projection straight through as `alertState`
  // so the shared "last paged Xh ago · in debounce ~Yh" caption
  // renders without a second fetch (the backend already bundles it
  // onto the pill response — see admin_d1_mirror_lag_alerts.py).
  const alertState = data?.alertState || null;
  return (
    <CronHealthPill
      data={adaptedData}
      loading={loading}
      onRefresh={onRefresh}
      testId="d1-mirror-lag"
      defaultWorkflowUrl={healthUrl}
      headerTextByStatus={HEADER_TEXT_BY_STATUS}
      pillLabelByStatus={PILL_LABEL_BY_STATUS}
      renderSubText={renderSubText}
      alertState={alertState}
      alertHistory={alertHistory}
      onLoadAlertHistory={onLoadAlertHistory}
    />
  );
}
