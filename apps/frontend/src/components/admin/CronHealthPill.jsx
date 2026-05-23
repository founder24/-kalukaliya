import React, { useState, useCallback } from 'react';
import {
  AlertTriangle, ShieldCheck, Clock, RefreshCw, ExternalLink,
  ChevronDown, ChevronUp, History, MessageSquare, BellOff,
} from 'lucide-react';
import { formatAlertStateCaption } from './cronCaptionHelpers';

// Task #964 — small badge that surfaces whether the alerter's Slack
// fan-out has its webhook env var set on the backend. The intent is
// to make a deploy-without-Slack-coverage gap visible at a glance,
// without leaking the webhook URL itself (the backend only publishes
// the boolean — see the cron health endpoints in
// routes/admin_logs_cf_pull_silence_alerts.py,
// routes/admin_cf_waf_drift_cron_alerts.py and
// routes/admin_health.py). When the field is absent (e.g. older
// backend that hasn't shipped Task #964 yet) we render nothing so
// the pill still looks correct against an in-flight rollout.
//
// Task #974 — when the badge is red AND the missing-Slack-webhook
// nag (routes/admin_slack_webhook_missing_alerts.py) has paged
// on-call about *this* env, decorate the badge with a tiny
// "· paged Nh ago" suffix so admins can tell "we know, on-call has
// been paged" apart from "this just rolled out and the nag's 24h
// grace window hasn't elapsed yet". The decoration is intentionally
// gated on the badge being red so a recovered/healthy alerter
// doesn't carry a confusing stale paged-Nh-ago tail.
// Task #980 — default snooze duration in hours. One week matches
// the backend's ``_SNOOZE_MAX_HOURS`` ceiling and lines up with a
// typical on-call rotation; the button label and POST body are
// derived from this constant so changing the cadence in one place
// updates the badge UI + the audit-log entry the backend writes.
const DEFAULT_SNOOZE_HOURS = 168;

export function SlackConfigBadge({
  configured,
  envName,
  testId,
  missingAlertState,
  // Task #979 — optional snapshot of recent missing-Slack-webhook
  // pages for this env, sourced from
  // /admin/health/slack-webhook-missing/<env>/alert-history. When
  // the badge is red AND this snapshot has at least one event, the
  // badge renders a "Recent pages (N)" disclosure under itself
  // listing up to ~10 events (most recent first). Empty history
  // shows only the existing "· paged Nh ago" decoration — no
  // disclosure UI — so an env that's never been paged about stays
  // visually quiet. Decoupled from ``missingAlertState`` so a parent
  // can opt into either the "· paged Nh ago" caption, the "Recent
  // pages" panel, or both. Shape:
  //   { events: [{ id, pagedAt, kind, subKind, lastRunUrl,
  //                lastConclusion, lastRunId, ... }], lockId, limit }
  // — see _build_alert_history_response in
  // routes/admin_slack_webhook_missing_alerts.py.
  missingAlertHistory,
  // Task #980 — async ``(envName, untilHours) => Promise<void>``
  // wired by the AdminHealth dashboard. When provided AND the badge
  // is red AND the missing-webhook nag has paged on-call (the same
  // gates the "· paged Nh ago" decoration uses), the badge renders a
  // small "Snooze 7d" button that POSTs to the snooze endpoint and
  // refreshes the alert-state. Decoupled from the badge's local
  // state so the dashboard owns the actual fetch + retry contract
  // (the badge only cares whether a snooze is currently in flight
  // for the disabled-button affordance).
  onSnooze,
}) {
  // Local "POST in flight" flag so the button stays disabled while
  // the backend is persisting the snooze. The dashboard's onSnooze
  // also triggers an alert-state re-fetch on success, but the
  // button can't depend on the parent's loading state without a
  // second prop — a local ref is enough since "another admin clicked
  // it from a second tab" is handled by the 60s polling.
  const [snoozing, setSnoozing] = useState(false);
  // Task #979 — disclosure open/closed flag. Starts collapsed so a
  // red badge stays visually compact; the admin opts in by clicking
  // the "Recent pages (N)" toggle. The events list itself is
  // already in props (the dashboard polls it on the same 60s cadence
  // as alert-state) so opening is instant — no lazy fetch needed.
  const [historyOpen, setHistoryOpen] = useState(false);

  if (configured == null) return null;
  const cls = configured
    ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
    : 'bg-gray-100 text-gray-500 border-gray-200';
  const label = configured ? 'Slack ✓' : 'Slack ✗';

  // Task #974 — only decorate the red badge with paged context when
  // the lock doc actually carries a `last_alert_at` (i.e. the nag
  // has fired at least once). The endpoint always returns
  // ``present: false`` until the first page lands, so checking
  // ``lastAlertAgeSeconds`` directly handles both "no doc yet" and
  // "doc exists but only carries first_observed_ts" without a
  // separate truthiness dance.
  const pagedAgeSecs = (
    !configured
    && missingAlertState
    && missingAlertState.lastAlertAgeSeconds != null
  ) ? missingAlertState.lastAlertAgeSeconds : null;
  const pagedAge = ageLabel(pagedAgeSecs);
  const inDebounce = pagedAge != null && !!missingAlertState?.inDebounce;
  const debounceRemaining = inDebounce
    ? ageLabel(missingAlertState?.debounceRemainingSeconds)
    : null;

  // Task #980 — surface an active snooze even when the missing-side
  // page hasn't fired yet: an admin who pre-emptively snoozes a
  // soon-to-be-paged env (e.g. they know a planned secret rotation
  // is about to land) should still see the "snoozed for Xh" caption
  // so they don't double-snooze on the next dashboard load. Gating
  // on ``!configured`` keeps the caption from lingering on a
  // recovered/healthy badge after the env was finally set —
  // re-snoozing a green badge would be confusing.
  const snoozeRemaining = (!configured && missingAlertState?.snoozeActive)
    ? Number(missingAlertState?.snoozeRemainingSeconds || 0)
    : 0;
  const snoozeRemainingLabel = snoozeRemaining > 0
    ? ageLabel(snoozeRemaining)
    : null;

  // Title is the only place we mention the env var name so an admin
  // who sees a missing badge can copy/paste the exact env var to
  // ask infra to set it. Falls back to a generic hint when the
  // backend didn't publish the env var name. Task #963 — also
  // make the "third channel alongside in-app + email" relationship
  // explicit so on-call understands that the email + in-app pages
  // still fire even when this badge is red. Task #974 — append the
  // missing-webhook nag's paged-on-call status to the red-badge
  // tooltip so an admin hovering the badge sees both "here's how to
  // fix it" and "here's whether on-call already knows" in one place.
  let title = configured
    ? `Slack fan-out is wired up — paged alongside in-app + email${envName ? ` (env: ${envName})` : ''}.`
    : envName
      ? `Slack fan-out is NOT wired up — in-app + email pages still fire; set the ${envName} env var on the backend to enable the third Slack channel.`
      : 'Slack fan-out is NOT wired up — in-app + email pages still fire; set the alerter\'s webhook env var on the backend to enable the third Slack channel.';
  if (pagedAge != null) {
    title += inDebounce && debounceRemaining
      ? ` On-call paged ${pagedAge} ago about this missing webhook; next nag suppressed for ~${debounceRemaining}.`
      : ` On-call last paged ${pagedAge} ago about this missing webhook.`;
  }
  // Task #980 — append snooze status to the tooltip so a hover
  // tells the admin both "here's how long the page is silenced" and
  // "the recovery page still fires", matching the behaviour the
  // backend implements. Recovery exception is in the docstring of
  // ``admin_slack_webhook_missing_snooze`` and intentionally NOT
  // gated on snooze in ``_check_and_alert_one_env``.
  if (snoozeRemainingLabel) {
    const snoozedBy = missingAlertState?.snoozedBy
      ? ` by ${missingAlertState.snoozedBy}`
      : '';
    title += ` Snoozed${snoozedBy} for ~${snoozeRemainingLabel} more — missing-side pages suppressed; the recovery page still fires when the env lands.`;
  }

  // Snooze button gate: only render when the dashboard wired
  // onSnooze AND the badge is red AND the nag has actually paged
  // on-call (so we don't offer "snooze" for an env we've never
  // paged about — the bootstrap grace window is doing its job).
  // While a snooze is already active we hide the button so an admin
  // can't stack multiple snoozes by clicking through the alert-state
  // 60s polling lag.
  const showSnoozeButton = (
    typeof onSnooze === 'function'
    && !configured
    && pagedAge != null
    && envName
    && snoozeRemaining <= 0
  );

  const onSnoozeClick = useCallback(async (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    if (!envName || typeof onSnooze !== 'function' || snoozing) return;
    setSnoozing(true);
    try {
      await onSnooze(envName, DEFAULT_SNOOZE_HOURS);
    } finally {
      setSnoozing(false);
    }
  }, [envName, onSnooze, snoozing]);

  // Task #979 — disclosure gating. Only render the "Recent pages"
  // toggle when (a) the badge is red (a green badge has nothing to
  // disclose), (b) the parent passed a history snapshot, and (c)
  // that snapshot has at least one event. An empty events list
  // means either the env was never paged about (bootstrap grace
  // window) or all pages were trimmed by the audit-log retention
  // policy — either way, surfacing an empty disclosure adds noise
  // without information, so we leave only the existing
  // "· paged Nh ago" decoration in that case.
  const historyEvents = (!configured && missingAlertHistory && Array.isArray(missingAlertHistory.events))
    ? missingAlertHistory.events
    : [];
  const historyEventCount = historyEvents.length;
  const showHistoryDisclosure = historyEventCount > 0;
  const onToggleHistory = useCallback((ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    setHistoryOpen((prev) => !prev);
  }, []);

  // The badge itself stays an inline pill, but the optional
  // "Recent pages" disclosure is a block element underneath, so
  // wrap both in an inline-block flow container to keep the badge
  // hugging its content while the panel stretches to the full
  // available width of the badge row.
  return (
    <span className="inline-flex flex-col items-start gap-0.5">
      <span
        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-semibold border ${cls}`}
        title={title}
        data-testid={testId ? `${testId}-slack-config` : undefined}
        data-slack-configured={configured ? 'true' : 'false'}
        data-slack-missing-paged-age-seconds={
          pagedAgeSecs != null ? String(pagedAgeSecs) : undefined
        }
        data-slack-snooze-remaining-seconds={
          snoozeRemaining > 0 ? String(snoozeRemaining) : undefined
        }
      >
        <MessageSquare size={10} aria-hidden />
        {label}
        {snoozeRemainingLabel ? (
          <span
            className="ml-0.5 font-normal opacity-80 inline-flex items-center gap-0.5"
            data-testid={testId ? `${testId}-slack-config-snoozed` : undefined}
          >
            <BellOff size={10} aria-hidden />
            snoozed {snoozeRemainingLabel}
          </span>
        ) : pagedAge != null && (
          <span
            className="ml-0.5 font-normal opacity-80"
            data-testid={testId ? `${testId}-slack-config-paged` : undefined}
          >
            · paged {pagedAge} ago
          </span>
        )}
        {showSnoozeButton && (
          <button
            type="button"
            onClick={onSnoozeClick}
            disabled={snoozing}
            className="ml-1 px-1 py-0 rounded border border-gray-300 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-50 inline-flex items-center gap-0.5 text-[9px] font-semibold"
            data-testid={testId ? `${testId}-slack-config-snooze` : undefined}
            title={`Suppress missing-webhook pages for ${envName} for 7 days. The recovery page still fires when the env is set.`}
            aria-label={`Snooze missing-webhook nag for ${envName} for 7 days`}
          >
            <BellOff size={9} aria-hidden />
            {snoozing ? '…' : 'Snooze 7d'}
          </button>
        )}
        {showHistoryDisclosure && (
          <button
            type="button"
            onClick={onToggleHistory}
            className="ml-1 px-1 py-0 rounded border border-gray-300 bg-white text-gray-600 hover:bg-gray-50 inline-flex items-center gap-0.5 text-[9px] font-semibold"
            data-testid={testId ? `${testId}-slack-config-history-toggle` : undefined}
            aria-expanded={historyOpen}
            title={historyOpen
              ? `Hide the recent missing-webhook pages for ${envName}`
              : `Show the ${historyEventCount} most recent missing-webhook pages for ${envName}`}
          >
            <History size={9} aria-hidden />
            {historyOpen
              ? 'Hide pages'
              : `Recent pages (${historyEventCount})`}
            {historyOpen
              ? <ChevronUp size={9} aria-hidden />
              : <ChevronDown size={9} aria-hidden />}
          </button>
        )}
      </span>
      {showHistoryDisclosure && historyOpen && (
        <div
          className="mt-1 rounded-lg border border-gray-200 bg-white/80 px-2 py-1 max-w-md"
          data-testid={testId ? `${testId}-slack-config-history-panel` : undefined}
        >
          <ul className="divide-y divide-gray-100">
            {historyEvents.map((ev, idx) => (
              <HistoryEventRow
                key={ev?.id || idx}
                event={ev}
                ageLabel={ageLabel}
                testId={testId ? `${testId}-slack-config` : 'slack-config'}
                index={idx}
              />
            ))}
          </ul>
        </div>
      )}
    </span>
  );
}

const DEFAULT_PILL_LABELS = {
  healthy: 'CRON HEALTHY',
  silent: 'CRON SILENT',
  degraded: 'CRON DEGRADED',
  never_observed: 'NEVER OBSERVED',
  not_configured: 'NOT CONFIGURED',
};

export const ageLabel = (secs) => {
  if (secs == null) return null;
  const s = Math.max(0, Math.floor(Number(secs)));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
};

// Task #918 — render a single audit-log row inside the history panel.
// One paged-on-call event ("paged Xh ago · failure · run #123") with
// a deep-link to the offending GitHub Actions run when present. Kept
// inline so the panel stays self-contained and the pill's wrappers
// don't need to know anything about the row layout.
function HistoryEventRow({ event, ageLabel: fmt, testId, index }) {
  const pagedAt = event?.pagedAt ? new Date(event.pagedAt) : null;
  // Recompute the "Xh ago" label client-side off the persisted
  // ISO timestamp so it stays accurate as the panel sits open
  // across the 60s polling interval (the backend can't predict
  // when the admin will look at it).
  const ageSecs = pagedAt
    ? Math.max(0, Math.floor((Date.now() - pagedAt.getTime()) / 1000))
    : null;
  const ageStr = fmt(ageSecs);
  const kindRaw = (event?.kind || '').toString();
  const kindLabel = kindRaw === 'recovered'
    ? 'recovered'
    : kindRaw === 'broken' || kindRaw === 'silent'
      ? 'paged'
      : kindRaw || 'event';
  const kindCls = kindRaw === 'recovered'
    ? 'bg-emerald-100 text-emerald-700 border-emerald-200'
    : kindRaw === 'broken' || kindRaw === 'silent'
      ? 'bg-red-100 text-red-700 border-red-200'
      : 'bg-gray-100 text-gray-600 border-gray-200';
  const subKindLabel = event?.subKind ? ` (${event.subKind})` : '';
  const conclusion = event?.lastConclusion;
  const runUrl = event?.lastRunUrl || event?.lastHtmlUrl;
  const runId = event?.lastRunId;
  const tooltipDate = pagedAt ? pagedAt.toISOString() : '';
  return (
    <li
      className="flex items-center gap-2 py-1 text-[11px] text-gray-600"
      data-testid={`${testId}-history-event`}
      data-history-event-index={index}
    >
      <span
        className={`px-1.5 py-0.5 rounded-full text-[10px] font-bold border ${kindCls}`}
      >
        {kindLabel.toUpperCase()}{subKindLabel}
      </span>
      <span title={tooltipDate} className="text-gray-500">
        {ageStr ? `${ageStr} ago` : 'just now'}
      </span>
      {conclusion && (
        <span className="text-gray-400">· {conclusion}</span>
      )}
      {runUrl && (
        <a
          href={runUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto text-violet-600 hover:text-violet-700 inline-flex items-center gap-1"
          title="Open the offending workflow run"
        >
          {runId ? `run #${runId}` : 'run'} <ExternalLink size={10} />
        </a>
      )}
    </li>
  );
}

export default function CronHealthPill({
  data: rawData,
  loading = false,
  onRefresh,
  testId,
  headerTextByStatus,
  pillLabelByStatus,
  defaultWorkflowUrl,
  renderSubText,
  renderExtraActions,
  // Task #902 — optional alerter-state lock-doc snapshot from
  // `/admin/health/<pill>/cron/alert-state`. When provided, the
  // pill renders a small "last paged Xh ago · in debounce ~Yh
  // remaining" line below subText so admins can distinguish "I'm
  // seeing red because nobody has been paged yet" from "I'm
  // seeing red because we already paged Nh ago and are in
  // debounce" without having to query Mongo. The shape is
  // documented on `formatAlertStateCaption` in cronCaptionHelpers.
  alertState,
  // Task #918 — optional paged-on-call audit log from
  // `/admin/health/<pill>/cron/alert-history`. When `onLoadAlertHistory`
  // is provided the pill renders a "Show paged history" disclosure
  // button under the alert-state caption; clicking it lazy-fetches
  // (via the parent's loader) and expands an inline panel listing
  // up to ~20 alerter events (page + recovery), most recent first.
  // Decoupled from `alertState` so the wrappers can opt into one,
  // both, or neither without coupling the two contracts. Shape:
  //   { events: [{ pagedAt, kind, subKind, lastRunUrl,
  //                lastConclusion, lastRunId, ... }], lockId, limit }
  // — see _build_alert_history_response in routes/admin_health.py.
  alertHistory,
  onLoadAlertHistory,
  // Task #974 — optional snapshot of the per-env missing-Slack-webhook
  // nag's lock doc, sourced from
  // `/admin/health/slack-webhook-missing/<env>/alert-state`. When the
  // wrapper passes it, the inline `SlackConfigBadge` decorates a red
  // "Slack ✗" with "· paged Nh ago" so admins can see at a glance
  // whether on-call has already been nagged about this env's missing
  // webhook. Decoupled from `alertState` because the `alertState`
  // above describes the cron's *own* silence/cron-failure alerter,
  // while this one describes a sibling alerter that pages on-call
  // when the cron's Slack webhook env stays unset post-deploy.
  slackMissingAlertState,
  // Task #979 — optional snapshot of recent missing-Slack-webhook
  // pages for this env, sourced from
  // `/admin/health/slack-webhook-missing/<env>/alert-history`.
  // Forwarded as-is to ``SlackConfigBadge`` which renders a "Recent
  // pages (N)" disclosure next to the badge when it's red and the
  // snapshot has at least one event. Decoupled from
  // ``slackMissingAlertState`` so a wrapper can opt into the
  // disclosure without exposing the read-only paged caption (or
  // vice versa) — both are best-effort and gated on the badge being
  // red, so passing one without the other is safe.
  slackMissingAlertHistory,
  // Task #980 — optional snooze handler forwarded down to
  // `SlackConfigBadge`. When provided AND the missing-Slack-webhook
  // nag is currently red+paged for this env, the badge surfaces a
  // "Snooze 7d" button that POSTs to
  // `/admin/health/slack-webhook-missing/<env>/snooze`. Decoupled
  // from `slackMissingAlertState` so a wrapper pill can render the
  // "· paged Nh ago" caption (read-only) without exposing the
  // mutation; the snooze action only appears when the wrapper opts
  // in by passing this callback. Signature: `(envName, hours) =>
  // Promise<AlertState>` — resolved value is patched optimistically
  // into the parent's state map so the caption flips to
  // "snoozed for Xh remaining" without waiting on the 60s poll.
  onSnoozeSlackMissing,
}) {
  const data = rawData && !rawData._error ? rawData : null;
  const status = data?.status || 'unknown';
  const isFailed = status === 'silent';
  const isDegraded = status === 'degraded';
  const isUnknown = status === 'never_observed' || status === 'not_configured' || !data;

  const containerCls = isFailed
    ? 'bg-red-50 border-red-200'
    : isDegraded
      ? 'bg-amber-50 border-amber-200'
      : isUnknown
        ? 'bg-gray-50 border-gray-200'
        : 'bg-emerald-50 border-emerald-200';
  const headerColor = isFailed
    ? 'text-red-600'
    : isDegraded
      ? 'text-amber-600'
      : isUnknown
        ? 'text-gray-500'
        : 'text-emerald-600';
  const pillCls = isFailed
    ? 'bg-red-100 text-red-700 border-red-200'
    : isDegraded
      ? 'bg-amber-100 text-amber-700 border-amber-200'
      : isUnknown
        ? 'bg-gray-100 text-gray-600 border-gray-200'
        : 'bg-emerald-100 text-emerald-700 border-emerald-200';

  const pillLabels = { ...DEFAULT_PILL_LABELS, ...(pillLabelByStatus || {}) };
  const pillLabel = pillLabels[status] || 'UNKNOWN';
  const headerText = (headerTextByStatus && headerTextByStatus[status])
    || (headerTextByStatus && headerTextByStatus.unknown)
    || 'Cron — status unknown';

  const workflowUrl = data?.workflowUrl || defaultWorkflowUrl;
  const ctx = { data, status, isFailed, isDegraded, isUnknown, ageLabel };
  const subText = renderSubText ? renderSubText(ctx) : null;
  const extraActions = renderExtraActions ? renderExtraActions(ctx) : null;
  // Task #902 — alerter-state caption (e.g. "last paged 2h ago ·
  // in debounce ~22h remaining"). Returns null when there's no
  // recorded page so we don't render an orphan line on a fresh
  // deployment with a healthy pill.
  const alertCaption = formatAlertStateCaption(alertState);
  // The caption colour follows whether we're inside the debounce
  // window: amber when on-call has been paged but the next re-page
  // is still suppressed (so admins don't expect a new email if the
  // pill stays red), gray otherwise (just informational —
  // e.g. recovery, or a broken state past the debounce so the next
  // poll can re-page).
  const alertCaptionCls = alertState && alertState.inDebounce
    ? 'text-amber-600'
    : 'text-gray-500';

  // Task #918 — paged-on-call audit-log disclosure. The toggle is
  // only rendered when the wrapper opted in by passing
  // `onLoadAlertHistory`; this keeps existing callers (and the
  // `<CronHealthPill />` snapshot tests above) untouched. The panel
  // is lazy — page load does NOT pre-fetch history (would carry
  // N×20 events nobody asked for) and the parent's 60s polling
  // intentionally skips it too. Instead the loader fires on every
  // open: admin opens → fresh data, admin closes (no fetch),
  // admin reopens → fresh data again. Closing is free; opening
  // is the user's explicit "show me the latest" gesture.
  const [historyOpen, setHistoryOpen] = useState(false);
  const events = alertHistory?.events || [];
  const eventCount = events.length;
  const historyEnabled = typeof onLoadAlertHistory === 'function';
  const onToggleHistory = useCallback(() => {
    setHistoryOpen((prev) => {
      const next = !prev;
      if (next && historyEnabled) {
        // No try/catch — the parent loader is contractually
        // responsible for swallowing its own fetch errors and
        // surfacing them via `alertHistory.error` / loading state.
        // Catching here would hide real bugs (TypeError on a
        // misnamed prop, etc.) and silently break the panel.
        onLoadAlertHistory();
      }
      return next;
    });
  }, [historyEnabled, onLoadAlertHistory]);

  return (
    <div className={`rounded-2xl p-4 border ${containerCls}`} data-testid={`${testId}-tile`}>
      <div className="flex items-center gap-3">
        {isFailed
          ? <AlertTriangle size={18} className="text-red-500" />
          : isDegraded
            ? <AlertTriangle size={18} className="text-amber-500" />
            : isUnknown
              ? <Clock size={18} className="text-gray-400" />
              : <ShieldCheck size={18} className="text-emerald-500" />}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <p className={`text-sm font-semibold ${headerColor}`} data-testid={`${testId}-status`}>
              {headerText}
            </p>
            <a
              href={workflowUrl}
              target="_blank"
              rel="noopener noreferrer"
              className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border ${pillCls} hover:opacity-80`}
              data-testid={`${testId}-pill`}
              title="Open the GitHub Actions runs page for this workflow"
            >
              {pillLabel}
            </a>
            {/*
              Task #964 — Slack-config indicator. Renders nothing
              when the backend hasn't published `slackConfigured`
              (older API or endpoints that don't fan out to Slack
              at all), so adding this prop is a no-op for any pill
              wrapper that doesn't pass it. The neutral grey "Slack
              ✗" makes a missing-webhook deploy obvious next to the
              status pill without changing the pill colour itself
              (Slack coverage is independent of cron health).
            */}
            <SlackConfigBadge
              configured={data?.slackConfigured}
              envName={data?.slackWebhookEnv}
              testId={testId}
              missingAlertState={slackMissingAlertState}
              missingAlertHistory={slackMissingAlertHistory}
              onSnooze={onSnoozeSlackMissing}
            />
          </div>
          {subText != null && (
            <p className="text-[11px] text-gray-500 mt-0.5">
              {subText}
            </p>
          )}
          {alertCaption && (
            <p
              className={`text-[11px] mt-0.5 ${alertCaptionCls}`}
              data-testid={`${testId}-alert-state`}
            >
              {alertCaption}
            </p>
          )}
          {historyEnabled && (
            <button
              type="button"
              onClick={onToggleHistory}
              className="mt-1 text-[11px] text-violet-600 hover:text-violet-700 inline-flex items-center gap-1"
              data-testid={`${testId}-history-toggle`}
              aria-expanded={historyOpen}
              title={historyOpen
                ? 'Hide the paged-on-call history for this cron'
                : 'Show the recent paged-on-call history for this cron'}
            >
              <History size={11} />
              {historyOpen
                ? 'Hide paged history'
                : (alertHistory && eventCount > 0
                    ? `Show paged history (${eventCount})`
                    : 'Show paged history')}
              {historyOpen
                ? <ChevronUp size={11} />
                : <ChevronDown size={11} />}
            </button>
          )}
          {historyEnabled && historyOpen && (
            <div
              className="mt-1.5 rounded-lg border border-gray-200 bg-white/60 px-2 py-1"
              data-testid={`${testId}-history-panel`}
            >
              {!alertHistory ? (
                <p
                  className="text-[11px] text-gray-400 py-1"
                  data-testid={`${testId}-history-loading`}
                >
                  Loading paged history…
                </p>
              ) : eventCount === 0 ? (
                <p
                  className="text-[11px] text-gray-400 py-1"
                  data-testid={`${testId}-history-empty`}
                >
                  No on-call pages recorded yet for this cron.
                </p>
              ) : (
                <ul className="divide-y divide-gray-100">
                  {events.map((ev, idx) => (
                    <HistoryEventRow
                      key={ev?.id || idx}
                      event={ev}
                      ageLabel={ageLabel}
                      testId={testId}
                      index={idx}
                    />
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
        {extraActions}
        <a
          href={workflowUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[11px] text-violet-600 hover:text-violet-700 inline-flex items-center gap-1"
          data-testid={`${testId}-run-link`}
          title="Open the GitHub Actions runs page for this workflow"
        >
          Runs <ExternalLink size={11} />
        </a>
        <button
          onClick={onRefresh}
          disabled={loading}
          className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
          // Task #882 — primary testId follows the AdminHealth cron-pill
          // convention documented in replit.md (`<prefix>-refresh`); the
          // `data-testid-legacy` attribute carries the historical
          // `button-refresh-<prefix>` form so any external selector
          // (Cloudflare Browser Rendering uptime probe, runbook
          // screenshots, etc.) that grew up against the old shape
          // before the convention block was added does not break.
          // Querying by `data-testid` (the React Testing Library
          // default) sees only the convention-correct primary form.
          data-testid={`${testId}-refresh`}
          data-testid-legacy={`button-refresh-${testId}`}
          title="Refresh"
        >
          <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>
    </div>
  );
}
