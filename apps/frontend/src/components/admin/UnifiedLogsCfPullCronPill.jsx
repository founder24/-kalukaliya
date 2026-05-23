import React from 'react';
import { ExternalLink } from 'lucide-react';
import CronHealthPill from './CronHealthPill';
import { captionLine, joinCaptionParts } from './cronCaptionHelpers';

// Task #956 — sibling pill for the unified-logs Cloudflare GraphQL
// pull silence alerter (Task #951). Same shape as the cf-waf-drift /
// edge-proxy-deploy / Trustpilot refresh-cron pills, with two
// twists relative to those:
//
//   * The data source is a backend cron loop polling
//     ``db.job_locks[unified_logs_cf_pull_lock]`` — there is NO
//     GitHub Actions workflow behind this pill (the cf-waf-drift /
//     edge-proxy-deploy pills both link out to a workflow runs
//     page). The "Runs" link therefore points at the JSON status
//     endpoint (``/api/admin/logs/status`` — the same URL the
//     backend reports as ``statusUrl`` on its health response) so
//     admins can inspect the lease + cursor snapshot when the pill
//     turns red.
//   * The status keys are ``healthy / silent / never_observed /
//     not_configured`` — there's no ``degraded`` state because the
//     classifier is binary ("did the cursor advance within the
//     threshold?"). The shared <CronHealthPill> handles the missing
//     ``degraded`` branch as gray; we just don't surface a header
//     copy for it.
//
// The caption follows the same rhythm as the other pills:
//   * "Last cursor advance Xh ago" — primary line, derived from
//     ``lastUpdatedAgeSeconds``;
//   * suffix on red: "no successful pull" so admins know why the
//     pill is silent before they hover;
//   * lease owner appended in parentheses when present so on-call
//     can see which replica (or zombie) is holding the Mongo lock
//     without having to open the JSON status endpoint.
//
// testIds follow the AdminHealth cron-pill convention (replit.md
// § "AdminHealth cron-pill testId convention"):
//   unified-logs-cf-pull-cron-{tile,status,pill,run-link,refresh}.
//
// Notification channels (Tasks #957 / #963):
//   The alerter behind this pill pages on three channels —
//   in-app notification + admin email + best-effort Slack
//   fan-out — matching the cf-waf-drift / edge-proxy-deploy
//   sibling pills. Slack is gated on the backend env var
//   `UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK`; when unset, the
//   email + in-app channels still fire and the shared
//   <SlackConfigBadge> next to this pill renders "Slack ✗"
//   so on-call can spot the missing webhook at a glance.
//   See CLOUDFLARE_ZERO_TRUST.md §8.7.7 for the sibling-webhook
//   table that documents all three env vars together.

const HEADER_TEXT_BY_STATUS = {
  healthy: 'Cloudflare log ingest — flowing',
  silent: 'Cloudflare log ingest — silent',
  never_observed: 'Cloudflare log ingest — no pull yet',
  not_configured: 'Cloudflare log ingest — not configured',
  unknown: 'Cloudflare log ingest — status unknown',
};

const PILL_LABEL_BY_STATUS = {
  healthy: 'INGEST HEALTHY',
  silent: 'INGEST SILENT',
  never_observed: 'NEVER OBSERVED',
  not_configured: 'NOT CONFIGURED',
};

// Backend default. Real responses always carry ``statusUrl`` —
// this is just a safety net for snapshot tests / partial fixtures
// so the always-on "Runs" link doesn't render with an empty href.
const DEFAULT_STATUS_URL = '/api/admin/logs/status';

const renderSubText = ({ data, status, isFailed, ageLabel: fmt }) => {
  const ageLbl = fmt(data?.lastUpdatedAgeSeconds);
  const leaseOwnerRaw = (data?.leaseOwner || '').toString().trim();

  const primary = captionLine(
    'Last cursor advance', ageLbl, 'No successful pull recorded yet',
  );

  let suffix = '';
  if (isFailed) {
    // On red the primary line still reads "Last cursor advance Xh
    // ago" (or the no-pull fallback) — the suffix marks WHY the
    // pill went red so a glance is enough to triage. Mirrors the
    // edge-proxy-deploy "failure" suffix and the cf-waf-drift
    // verify/aggregate-RC suffix.
    suffix = 'no successful pull';
  }
  // Append the lease owner whenever we know it, on every status —
  // healthy and silent both benefit from showing which replica is
  // currently holding the Mongo lock. Falsy / blank owners (e.g.
  // not_configured, never_observed before the first lease claim)
  // collapse the suffix away via joinCaptionParts.
  const leaseSuffix = leaseOwnerRaw ? `lease: ${leaseOwnerRaw}` : '';

  return joinCaptionParts([primary, suffix, leaseSuffix]);
};

const renderExtraActions = ({ data }) => {
  // Optional deep-link to the JSON status endpoint — same shape as
  // the cf-waf-drift / edge-proxy-deploy "Last run" deep-link, but
  // this one opens the live JSON snapshot of the lock doc so admins
  // can read the cursor + lease without leaving the browser. We
  // only render it when the backend has supplied a statusUrl
  // (always true on the real endpoint, but omitted on partial
  // fixtures used by the snapshot tests).
  const statusUrl = data?.statusUrl;
  if (!statusUrl) return null;
  return (
    <a
      href={statusUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="text-[11px] text-violet-600 hover:text-violet-700 inline-flex items-center gap-1"
      data-testid="unified-logs-cf-pull-cron-status-link"
      title="Open the JSON status snapshot for the CF pull lock"
    >
      Status JSON <ExternalLink size={11} />
    </a>
  );
};

export default function UnifiedLogsCfPullCronPill({
  data, loading, onRefresh,
  // Task #902 — alerter-state lock-doc snapshot from
  // /admin/health/unified-logs/cf-pull/cron/alert-state. Optional;
  // see EdgeProxyDeployCronPill for the same prop's behaviour.
  alertState,
  // Task #918 — paged-on-call audit log from
  // /admin/health/unified-logs/cf-pull/cron/alert-history. Optional;
  // see EdgeProxyDeployCronPill for the same prop's behaviour.
  alertHistory, onLoadAlertHistory,
  // Task #974 — per-env missing-Slack-webhook nag snapshot for
  // UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK, sourced from
  // /admin/health/slack-webhook-missing/UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK/alert-state.
  // Forwarded as-is to CronHealthPill; see SlackConfigBadge for how
  // the "· paged Nh ago" decoration is rendered.
  slackMissingAlertState,
  // Task #979 — per-env missing-Slack-webhook page-history snapshot
  // (~10 events) sourced from
  // /admin/health/slack-webhook-missing/UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK/alert-history.
  // Forwarded as-is to ``CronHealthPill``'s SlackConfigBadge so a
  // red badge can expand a "Recent pages" disclosure next to the
  // "· paged Nh ago" decoration.
  slackMissingAlertHistory,
  // Task #980 — async ``(envName, untilHours) => Promise<void>``
  // forwarded as-is to ``CronHealthPill``'s SlackConfigBadge so the
  // dashboard's "Snooze 7d" button can mute the missing-webhook nag
  // for ``UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK`` directly from this pill.
  onSnoozeSlackMissing,
}) {
  // The "Runs" link target falls back to the data's ``statusUrl``
  // (the only stable URL this cron exposes) before settling on the
  // hardcoded backend default. Identical fallback chain to the
  // sibling pills' workflowUrl resolution so the snapshot tests
  // stay symmetrical.
  const defaultWorkflowUrl = data?.statusUrl || DEFAULT_STATUS_URL;
  return (
    <CronHealthPill
      data={data}
      loading={loading}
      onRefresh={onRefresh}
      testId="unified-logs-cf-pull-cron"
      defaultWorkflowUrl={defaultWorkflowUrl}
      headerTextByStatus={HEADER_TEXT_BY_STATUS}
      pillLabelByStatus={PILL_LABEL_BY_STATUS}
      renderSubText={renderSubText}
      renderExtraActions={renderExtraActions}
      alertState={alertState}
      alertHistory={alertHistory}
      onLoadAlertHistory={onLoadAlertHistory}
      slackMissingAlertState={slackMissingAlertState}
      slackMissingAlertHistory={slackMissingAlertHistory}
      onSnoozeSlackMissing={onSnoozeSlackMissing}
    />
  );
}
