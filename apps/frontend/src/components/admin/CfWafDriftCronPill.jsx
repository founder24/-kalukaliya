import React from 'react';
import { ExternalLink } from 'lucide-react';
import CronHealthPill from './CronHealthPill';
import { captionLine, joinCaptionParts } from './cronCaptionHelpers';

// Task #833 — sibling pill for the daily cf-waf-drift-daily workflow
// heartbeat (Task #831). Same shape as the Trustpilot refresh-cron
// pill, with one addition: a "Last run" deep-link when the heartbeat
// carries one, since jumping straight to the offending GitHub Actions
// run is the first thing an admin wants when the pill turns red.
// Endpoint: /admin/health/cf-waf-drift/cron — status keys mirror the
// Trustpilot endpoint. Task #835 — the visual pill is now the shared
// <CronHealthPill> component. Task #836 — extracted from AdminHealth.jsx
// so the colour mapping, heartbeat-age caption, and conditional
// verify/aggregate-RC text can be unit-tested in isolation.

const HEADER_TEXT_BY_STATUS = {
  healthy: 'Firewall drift cron — checking in',
  silent: 'Firewall drift cron — silent',
  degraded: 'Firewall drift cron — last run flagged',
  never_observed: 'Firewall drift cron — no heartbeat yet',
  not_configured: 'Firewall drift cron — not configured',
  unknown: 'Firewall drift cron — status unknown',
};

const DEFAULT_WORKFLOW_URL =
  'https://github.com/syrabit/syrabit/actions/workflows/cf-waf-drift-daily.yml';

const renderSubText = ({ data, isDegraded, ageLabel: fmt }) => {
  const anyLbl = fmt(data?.lastHeartbeatAgeSeconds);
  // The endpoint reports last verify/aggregate exit codes — when
  // degraded we surface them so the admin knows whether it was the
  // verify gate or the aggregate gate that tripped before they click
  // through to the run.
  const verifyRc = data?.lastVerifyRc;
  const aggregateRc = data?.lastAggregateRc;
  const lastStatusRaw = (data?.lastStatus || '').toString();

  const primary = captionLine('Last heartbeat', anyLbl, 'No heartbeat recorded yet');
  const degradedSuffix = isDegraded && (verifyRc != null || aggregateRc != null || lastStatusRaw)
    ? `${lastStatusRaw || 'failure'}`
      + (verifyRc != null ? ` (verify=${verifyRc}` : '')
      + (verifyRc != null && aggregateRc != null ? `, aggregate=${aggregateRc})` : '')
      + (verifyRc != null && aggregateRc == null ? ')' : '')
      + (verifyRc == null && aggregateRc != null ? ` (aggregate=${aggregateRc})` : '')
    : '';
  return joinCaptionParts([primary, degradedSuffix]);
};

const renderExtraActions = ({ data }) => {
  const lastRunUrl = data?.lastRunUrl;
  if (!lastRunUrl) return null;
  return (
    <a
      href={lastRunUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="text-[11px] text-violet-600 hover:text-violet-700 inline-flex items-center gap-1"
      data-testid="cf-waf-drift-cron-last-run-link"
      title="Open the most recent workflow run"
    >
      Last run <ExternalLink size={11} />
    </a>
  );
};

export default function CfWafDriftCronPill({
  data, loading, onRefresh,
  // Task #902 — alerter-state lock-doc snapshot from
  // /admin/health/cf-waf-drift/cron/alert-state. Optional; see
  // EdgeProxyDeployCronPill for the same prop's behaviour.
  alertState,
  // Task #918 — paged-on-call audit log from
  // /admin/health/cf-waf-drift/cron/alert-history. Optional; see
  // EdgeProxyDeployCronPill for the same prop's behaviour.
  alertHistory, onLoadAlertHistory,
  // Task #974 — per-env missing-Slack-webhook nag snapshot for
  // CF_WAF_DRIFT_SLACK_WEBHOOK, sourced from
  // /admin/health/slack-webhook-missing/CF_WAF_DRIFT_SLACK_WEBHOOK/alert-state.
  // Forwarded as-is to CronHealthPill; see SlackConfigBadge for how
  // the "· paged Nh ago" decoration is rendered.
  slackMissingAlertState,
  // Task #979 — per-env missing-Slack-webhook page-history snapshot
  // (~10 events) sourced from
  // /admin/health/slack-webhook-missing/CF_WAF_DRIFT_SLACK_WEBHOOK/alert-history.
  // Forwarded as-is to ``CronHealthPill``'s SlackConfigBadge so a
  // red badge can expand a "Recent pages" disclosure next to the
  // "· paged Nh ago" decoration.
  slackMissingAlertHistory,
  // Task #980 — async ``(envName, untilHours) => Promise<void>``
  // forwarded as-is to ``CronHealthPill``'s SlackConfigBadge. When
  // wired by AdminHealth, the badge renders a "Snooze 7d" affordance
  // next to the "· paged Nh ago" tail so an admin can mute the
  // missing-webhook nag without leaving the dashboard.
  onSnoozeSlackMissing,
}) {
  return (
    <CronHealthPill
      data={data}
      loading={loading}
      onRefresh={onRefresh}
      testId="cf-waf-drift-cron"
      defaultWorkflowUrl={DEFAULT_WORKFLOW_URL}
      headerTextByStatus={HEADER_TEXT_BY_STATUS}
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
