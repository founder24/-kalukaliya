import React from 'react';
import { ExternalLink } from 'lucide-react';
import CronHealthPill from './CronHealthPill';
import { captionLine, joinCaptionParts } from './cronCaptionHelpers';

// Task #882 — sibling pill for the unattended `edge-proxy-deploy`
// GitHub Actions workflow (.github/workflows/edge-proxy-deploy.yml).
// Same shape as the cf-waf-drift wrapper next door, with one subtle
// twist in the caption: this cron does not post a heartbeat to the
// backend (the GitHub Actions REST API is the source of truth for
// "did the workflow run and what did it conclude"), so the caption
// reads "Last run X ago — <conclusion>" instead of the
// heartbeat-centric phrasing the Trustpilot / cf-waf-drift pills
// use. The colour mapping is driven by the backend translating
// GitHub's `conclusion` and run age into the shared CronHealthPill
// status keys (healthy / silent / degraded / never_observed /
// not_configured). Endpoint: /admin/health/edge-proxy-deploy/cron.
//
// testIds follow the AdminHealth cron-pill convention (replit.md
// § "AdminHealth cron-pill testId convention"):
//   edge-proxy-deploy-cron-{tile,status,pill,run-link,refresh}
// plus the pill's own edge-proxy-deploy-cron-last-run-link when the
// latest run carries an html_url (it almost always does — only a
// brand-new workflow with zero runs produces null).

const HEADER_TEXT_BY_STATUS = {
  healthy: 'Edge-proxy deploy CI — passing',
  silent: 'Edge-proxy deploy CI — last run failed',
  degraded: 'Edge-proxy deploy CI — last run is stale',
  never_observed: 'Edge-proxy deploy CI — no run yet',
  not_configured: 'Edge-proxy deploy CI — not configured',
  unknown: 'Edge-proxy deploy CI — status unknown',
};

const PILL_LABEL_BY_STATUS = {
  healthy: 'CI HEALTHY',
  silent: 'CI FAILED',
  degraded: 'RUN STALE',
  never_observed: 'NEVER RUN',
  not_configured: 'NOT CONFIGURED',
};

const DEFAULT_WORKFLOW_URL =
  'https://github.com/syrabit/syrabit/actions/workflows/edge-proxy-deploy.yml';

const renderSubText = ({ data, status, isFailed, isDegraded, ageLabel: fmt }) => {
  // The backend exposes the GitHub `updated_at` as `ageSeconds` and
  // the GitHub `conclusion` verbatim. Compose the caption out of the
  // same primitives the other cron pills use so the visual rhythm
  // matches: "Last run Xh ago" for the primary line, with a suffix
  // that depends on what mattered for the colour:
  //   * On red (failure)   → suffix = "failure" so admins know why
  //                          even before they hover.
  //   * On amber (stale)   → suffix = "stale (>7d)" so the threshold
  //                          is visible without reading the docs.
  //   * Otherwise (green)  → suffix = the GitHub `conclusion`
  //                          ("success", "in_progress", …) so a green
  //                          pill still tells you whether the latest
  //                          run completed or is queued/running.
  const ageLbl = fmt(data?.ageSeconds);
  const conclusionRaw = (data?.conclusion || '').toString();
  const runStatusRaw = (data?.runStatus || '').toString();

  const primary = captionLine('Last run', ageLbl, 'No run recorded yet');

  let suffix = '';
  if (isFailed) {
    suffix = conclusionRaw || 'failure';
  } else if (isDegraded) {
    suffix = `stale (>${Math.floor((data?.staleThresholdSeconds || 7 * 86400) / 86400)}d)`;
  } else if (status === 'healthy') {
    // Prefer the conclusion ("success") when present; otherwise fall
    // back to the run status ("in_progress", "queued") so a green
    // pill mid-deploy still conveys what's actually happening.
    suffix = conclusionRaw || runStatusRaw;
  }
  return joinCaptionParts([primary, suffix]);
};

const renderExtraActions = ({ data }) => {
  const lastRunUrl = data?.lastRunUrl || data?.html_url;
  if (!lastRunUrl) return null;
  return (
    <a
      href={lastRunUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="text-[11px] text-violet-600 hover:text-violet-700 inline-flex items-center gap-1"
      data-testid="edge-proxy-deploy-cron-last-run-link"
      title="Open the most recent workflow run"
    >
      Last run <ExternalLink size={11} />
    </a>
  );
};

export default function EdgeProxyDeployCronPill({
  data, loading, onRefresh,
  // Task #902 — alerter-state lock-doc snapshot from
  // /admin/health/edge-proxy-deploy/cron/alert-state. Optional;
  // when present the shared <CronHealthPill> renders an extra
  // "last paged Xh ago · in debounce ~Yh remaining" caption so
  // admins can tell apart "red but nobody's been paged yet" from
  // "red but we're already in the 24h debounce window after the
  // last page".
  alertState,
  // Task #918 — paged-on-call audit log from
  // /admin/health/edge-proxy-deploy/cron/alert-history. Optional;
  // when both are passed the shared <CronHealthPill> renders a
  // "Show paged history" disclosure that lazy-fetches via
  // `onLoadAlertHistory` on first open. Decoupled from
  // `alertState` so a future caller can wire only one of the two
  // without coupling the contracts.
  alertHistory, onLoadAlertHistory,
  // Task #974 — per-env missing-Slack-webhook nag snapshot for
  // EDGE_PROXY_DEPLOY_SLACK_WEBHOOK, sourced from
  // /admin/health/slack-webhook-missing/EDGE_PROXY_DEPLOY_SLACK_WEBHOOK/alert-state.
  // Forwarded as-is to CronHealthPill; see SlackConfigBadge for how
  // the "· paged Nh ago" decoration is rendered.
  slackMissingAlertState,
  // Task #979 — per-env missing-Slack-webhook page-history snapshot
  // (~10 events) sourced from
  // /admin/health/slack-webhook-missing/EDGE_PROXY_DEPLOY_SLACK_WEBHOOK/alert-history.
  // Forwarded as-is to ``CronHealthPill``'s SlackConfigBadge so a
  // red badge can expand a "Recent pages" disclosure next to the
  // "· paged Nh ago" decoration.
  slackMissingAlertHistory,
  // Task #980 — async ``(envName, untilHours) => Promise<void>``
  // forwarded as-is to ``CronHealthPill``'s SlackConfigBadge so the
  // dashboard's "Snooze 7d" button can mute the missing-webhook nag
  // for ``EDGE_PROXY_DEPLOY_SLACK_WEBHOOK`` directly from this pill.
  onSnoozeSlackMissing,
}) {
  return (
    <CronHealthPill
      data={data}
      loading={loading}
      onRefresh={onRefresh}
      testId="edge-proxy-deploy-cron"
      defaultWorkflowUrl={DEFAULT_WORKFLOW_URL}
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
