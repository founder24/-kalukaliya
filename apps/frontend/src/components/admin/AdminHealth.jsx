import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { toast } from 'sonner';
import axios from 'axios';
import { llmCosts, API_BASE } from '@/utils/api';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import { adminHeaders } from './health/shared';

import LlmTab from './health/LlmTab';
import PrerenderTab from './health/PrerenderTab';
import AsmTab from './health/AsmTab';
import WorkersAiTab from './health/WorkersAiTab';
import InfraTab from './health/InfraTab';
import RagTab from './health/RagTab';

export default function AdminHealth({ adminToken, onNavigate }) {
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [metricsData, setMetricsData] = useState(null);
  const [metricsLoading, setMetricsLoading] = useState(true);
  const [timeRange, setTimeRange] = useState(60);
  const [llmData, setLlmData] = useState(null);
  const [llmLoading, setLlmLoading] = useState(false);
  const [llmDays, setLlmDays] = useState(7);
  const [healthTab, setHealthTab] = useState('infra');
  const [prerender, setPrerender] = useState(null);
  const [prerenderLoading, setPrerenderLoading] = useState(false);
  const [prerenderTriggering, setPrerenderTriggering] = useState(false);

  // Task #214 — Chat pipeline probe: surfaces streaming_assamese_probe.first_chunk_latency_ms
  // and the assamese_probe latency on the Workers AI tab so on-call staff can spot a
  // Gemini TTFB regression without manually hitting /health/chat-pipeline.
  const [chatPipelineProbe, setChatPipelineProbe] = useState(null);
  const [chatPipelineLoading, setChatPipelineLoading] = useState(false);

  // Task #750 — Trustpilot AggregateRating JSON-LD verifier report.
  // Polled on the same cadence as other infra widgets so a regression
  // (build-time inject + daily prod re-check) shows up here without
  // ops/marketing having to read GitHub Actions failure email.
  const [tpJsonldReport, setTpJsonldReport] = useState(null);
  const [tpJsonldLoading, setTpJsonldLoading] = useState(false);

  // Task #754 — 30-day pass-rate history backing the sparkline shown
  // beside the per-URL table. Polled less frequently than the latest
  // report (which moves on every verifier run) since the trend only
  // changes once per scheduled run anyway.
  const [tpJsonldHistory, setTpJsonldHistory] = useState(null);

  // Task #758 — last N regression / recovery / streak alert events
  // from the notifications store, rendered as a compact history strip
  // inside the Trustpilot JSON-LD tile so ops can spot a flappy URL
  // that single-fire email dedup would hide.
  const [tpJsonldAlerts, setTpJsonldAlerts] = useState(null);

  // Task #755 — refresh-cron heartbeat snapshot. Surfaces whether the
  // daily GitHub Actions cron (.github/workflows/trustpilot-aggregate-
  // refresh.yml) is still checking in. Endpoint added in Task #751;
  // this just renders its status alongside the other Trustpilot tiles
  // so a silent cron is visible at a glance instead of waiting for the
  // email/in-app alert to fire.
  const [tpCronHealth, setTpCronHealth] = useState(null);
  const [tpCronLoading, setTpCronLoading] = useState(false);

  const loadTpCronHealth = useCallback(() => {
    setTpCronLoading(true);
    axios.get(`${API_BASE}/admin/health/trustpilot/refresh-cron`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setTpCronHealth(r.data))
      .catch(() => setTpCronHealth({ _error: true }))
      .finally(() => setTpCronLoading(false));
  }, [adminToken]);

  // Task #833 — cf-waf-drift daily cron heartbeat snapshot. Mirrors the
  // Trustpilot refresh-cron pill above so admins can spot a silent
  // firewall-drift cron at a glance instead of waiting for the >36h
  // silence email/in-app notification (Task #831). Endpoint shape:
  // /admin/health/cf-waf-drift/cron — status ∈ {healthy, silent,
  // degraded, never_observed, not_configured} plus lastHeartbeatAge,
  // lastVerifyRc/lastAggregateRc, lastRunUrl, workflowUrl.
  const [cfDriftCronHealth, setCfDriftCronHealth] = useState(null);
  const [cfDriftCronLoading, setCfDriftCronLoading] = useState(false);

  const loadChatPipelineProbe = useCallback(() => {
    setChatPipelineLoading(true);
    axios.get(`${API_BASE}/admin/health/chat-pipeline-probe`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setChatPipelineProbe(r.data))
      .catch(() => setChatPipelineProbe({ _error: true }))
      .finally(() => setChatPipelineLoading(false));
  }, [adminToken]);

  const loadCfDriftCronHealth = useCallback(() => {
    setCfDriftCronLoading(true);
    axios.get(`${API_BASE}/admin/health/cf-waf-drift/cron`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setCfDriftCronHealth(r.data))
      .catch(() => setCfDriftCronHealth({ _error: true }))
      .finally(() => setCfDriftCronLoading(false));
  }, [adminToken]);

  // Task #882 — edge-proxy-deploy CI cron snapshot. Mirrors the
  // cf-waf-drift pill above but the data source is the GitHub
  // Actions REST API rather than a workflow-posted heartbeat (this
  // workflow doesn't post one — see routes/admin_health.py for the
  // full reasoning). Endpoint shape: /admin/health/edge-proxy-deploy/
  // cron — status ∈ {healthy, silent, degraded, never_observed,
  // not_configured, unknown} plus conclusion, html_url/lastRunUrl,
  // updated_at, ageSeconds, runStatus, workflowUrl. The pill goes
  // red on conclusion: "failure", amber on runs older than 7 days
  // (deploys this rare are themselves suspicious — the workflow
  // only fires on workers/edge-proxy/** pushes), green otherwise.
  const [edgeProxyDeployCronHealth, setEdgeProxyDeployCronHealth] = useState(null);
  const [edgeProxyDeployCronLoading, setEdgeProxyDeployCronLoading] = useState(false);

  const loadEdgeProxyDeployCronHealth = useCallback(() => {
    setEdgeProxyDeployCronLoading(true);
    axios.get(`${API_BASE}/admin/health/edge-proxy-deploy/cron`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setEdgeProxyDeployCronHealth(r.data))
      .catch(() => setEdgeProxyDeployCronHealth({ _error: true }))
      .finally(() => setEdgeProxyDeployCronLoading(false));
  }, [adminToken]);

  // Task #956 — unified-logs Cloudflare GraphQL pull silence health
  // (Task #951 endpoint). Mirrors the cf-waf-drift / edge-proxy-deploy
  // pills above; the data source is a backend cron loop polling
  // db.job_locks[unified_logs_cf_pull_lock] rather than a GitHub
  // Actions workflow, so the pill points its "Runs" link at the
  // JSON status snapshot the backend exposes via ``statusUrl``.
  // Endpoint shape: /admin/health/unified-logs/cf-pull/cron — status
  // ∈ {healthy, silent, never_observed, not_configured} plus
  // lastUpdatedAgeSeconds, leaseOwner, leaseExpiresAt, cursor,
  // silentThresholdSeconds, statusUrl. The pill goes red on
  // status: "silent" (cursor stale past threshold), gray on
  // never_observed / not_configured, green otherwise.
  const [unifiedLogsCfPullCronHealth, setUnifiedLogsCfPullCronHealth] = useState(null);
  const [unifiedLogsCfPullCronLoading, setUnifiedLogsCfPullCronLoading] = useState(false);

  const loadUnifiedLogsCfPullCronHealth = useCallback(() => {
    setUnifiedLogsCfPullCronLoading(true);
    axios.get(`${API_BASE}/admin/health/unified-logs/cf-pull/cron`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setUnifiedLogsCfPullCronHealth(r.data))
      .catch(() => setUnifiedLogsCfPullCronHealth({ _error: true }))
      .finally(() => setUnifiedLogsCfPullCronLoading(false));
  }, [adminToken]);

  // Task #133 — Cloudflare weekly audit card.  Fetches the latest
  // cloudflare-weekly-audit.yml run via GitHub API and, when available,
  // downloads and parses the cf-audit-report artifact ZIP to show
  // per-status item counts (PASS / WARN / FAIL / PLAN_REQUIRED).
  // Endpoint: /admin/health/cf-audit/latest.
  const [cfAuditData, setCfAuditData] = useState(null);
  const [cfAuditLoading, setCfAuditLoading] = useState(false);

  const loadCfAudit = useCallback(() => {
    setCfAuditLoading(true);
    axios.get(`${API_BASE}/admin/health/cf-audit/latest`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setCfAuditData(r.data))
      .catch(() => setCfAuditData({ _error: true, status: 'unknown', error: 'Request failed' }))
      .finally(() => setCfAuditLoading(false));
  }, [adminToken]);

  // Task #419 — unified /admin/cf-health snapshot. We only consume the
  // ai_gateway.cache_by_model slice today (top-models cache hit ratio
  // tile), but holding the whole snapshot here keeps the door open for
  // sibling CF workstream tiles to share the same fetch.
  const [cfHealthData, setCfHealthData] = useState(null);
  const [cfHealthLoading, setCfHealthLoading] = useState(false);

  const loadCfHealth = useCallback(() => {
    setCfHealthLoading(true);
    axios.get(`${API_BASE}/admin/cf-health`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setCfHealthData(r.data))
      .catch(() => setCfHealthData({ _error: true }))
      .finally(() => setCfHealthLoading(false));
  }, [adminToken]);

  // Task #902 — alerter-state lock-doc snapshots for the three cron
  // pills above. The pill data answers "is the workflow currently
  // red?"; the alert-state data answers "have we paged on-call about
  // that yet?" by surfacing each alerter's persisted dedup state
  // (last paged when, against which run, currently inside the 24h
  // re-page debounce or not). Endpoints — all admin-gated, all
  // 200-or-200, returning ``present: false`` when the alerter
  // hasn't fired yet or Mongo is unavailable:
  //   * /admin/health/edge-proxy-deploy/cron/alert-state
  //     (Task #893 alerter, lock _id="edge_proxy_deploy_cron_alert_state")
  //   * /admin/health/cf-waf-drift/cron/alert-state
  //     (Task #831 alerter, lock _id="cf_waf_drift_cron_alert_state")
  //   * /admin/health/trustpilot/refresh-cron/alert-state
  //     (Task #751 alerter, lock _id="trustpilot_refresh_cron_alert_state")
  // Each pill renders the snapshot inline as a small "last paged Xh
  // ago · in debounce ~Yh remaining" caption.
  const [edgeProxyDeployCronAlertState, setEdgeProxyDeployCronAlertState] = useState(null);
  const [cfDriftCronAlertState, setCfDriftCronAlertState] = useState(null);
  const [tpCronAlertState, setTpCronAlertState] = useState(null);
  // Task #956 — alerter-state for the unified-logs CF pull silence
  // alerter (Task #951). Same contract as the sibling alert-states
  // above, sourced from
  // /admin/health/unified-logs/cf-pull/cron/alert-state.
  const [unifiedLogsCfPullCronAlertState, setUnifiedLogsCfPullCronAlertState] = useState(null);

  const loadEdgeProxyDeployCronAlertState = useCallback(() => {
    axios.get(`${API_BASE}/admin/health/edge-proxy-deploy/cron/alert-state`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setEdgeProxyDeployCronAlertState(r.data))
      .catch(() => setEdgeProxyDeployCronAlertState(null));
  }, [adminToken]);

  const loadCfDriftCronAlertState = useCallback(() => {
    axios.get(`${API_BASE}/admin/health/cf-waf-drift/cron/alert-state`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setCfDriftCronAlertState(r.data))
      .catch(() => setCfDriftCronAlertState(null));
  }, [adminToken]);

  const loadTpCronAlertState = useCallback(() => {
    axios.get(`${API_BASE}/admin/health/trustpilot/refresh-cron/alert-state`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setTpCronAlertState(r.data))
      .catch(() => setTpCronAlertState(null));
  }, [adminToken]);

  // Task #485 — alerter-state for the per-model AI Gateway guardrail
  // spike alerter. Same lazy-load + null-on-failure contract as the
  // sibling cron alert-state fetches above; the
  // AiGatewayGuardrailByModelTile reads `models[*]` and decorates
  // each row with a "paged Xh ago" caption + "in debounce" tag.
  const [aigGuardrailAlertState, setAigGuardrailAlertState] = useState(null);
  const loadAigGuardrailAlertState = useCallback(() => {
    axios.get(`${API_BASE}/admin/health/ai-gateway/guardrail-alerts/state`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setAigGuardrailAlertState(r.data))
      .catch(() => setAigGuardrailAlertState(null));
  }, [adminToken]);

  const loadUnifiedLogsCfPullCronAlertState = useCallback(() => {
    axios.get(`${API_BASE}/admin/health/unified-logs/cf-pull/cron/alert-state`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setUnifiedLogsCfPullCronAlertState(r.data))
      .catch(() => setUnifiedLogsCfPullCronAlertState(null));
  }, [adminToken]);

  // Task #974 — alerter-state for the missing-Slack-webhook nag
  // (routes/admin_slack_webhook_missing_alerts.py, Task #970), one
  // entry per monitored env. Fans out three GETs against
  // /admin/health/slack-webhook-missing/<env>/alert-state in parallel
  // and stashes the response keyed by env name so each cron pill
  // can pick out its matching slot to feed the SlackConfigBadge's
  // "· paged Nh ago" decoration. Best-effort: a missing entry just
  // means the badge renders the pre-Task #974 shape (no decoration).
  // Names are pinned here (not imported from a shared module) to
  // keep the wiring obvious next to the pills they're rendered on;
  // the source of truth lives in routes/slack_alerter_config.py and
  // is mirrored into _MONITORED_ENV_NAMES on the alerter module.
  const SLACK_WEBHOOK_MISSING_ENVS = useMemo(() => [
    'UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK',
    'CF_WAF_DRIFT_SLACK_WEBHOOK',
    'EDGE_PROXY_DEPLOY_SLACK_WEBHOOK',
  ], []);
  const [slackWebhookMissingAlertStates, setSlackWebhookMissingAlertStates] = useState({});
  const loadSlackWebhookMissingAlertStates = useCallback(() => {
    Promise.all(SLACK_WEBHOOK_MISSING_ENVS.map((env) =>
      axios.get(
        `${API_BASE}/admin/health/slack-webhook-missing/${env}/alert-state`,
        { headers: adminHeaders(adminToken), withCredentials: true },
      )
        .then((r) => [env, r.data])
        .catch(() => [env, null])
    )).then((pairs) => {
      // Replace wholesale instead of merging so a per-env transition
      // from "paged" to "recovered" (which clears `last_alert_at`)
      // doesn't leave a stale `lastAlertAgeSeconds` lingering on the
      // badge. The 60s polling cadence picks this up automatically.
      setSlackWebhookMissingAlertStates(Object.fromEntries(pairs));
    });
  }, [adminToken, SLACK_WEBHOOK_MISSING_ENVS]);

  // Task #979 — per-env audit log of pages issued by the
  // missing-Slack-webhook nag, sourced from
  // /admin/health/slack-webhook-missing/<env>/alert-history (the
  // endpoint Task #974 already shipped). Polled on the same 60s
  // cadence as the alert-state above — NOT lazy like the Task #918
  // disclosure on the cron pills, because the "Recent pages"
  // affordance on the SlackConfigBadge needs the event count up
  // front to decide whether to render the disclosure at all (an
  // empty-history env should show only the existing "· paged Nh
  // ago" badge, no extra clutter — see task spec).
  //
  // Cap at ~10 events per env: that's the max the badge's
  // disclosure renders (the wider Task #918 disclosure on the
  // sibling cron pills uses 20, but here we're decorating a small
  // inline badge, not a tile, so we keep the payload tight).
  const [slackWebhookMissingAlertHistories, setSlackWebhookMissingAlertHistories] = useState({});
  const loadSlackWebhookMissingAlertHistories = useCallback(() => {
    Promise.all(SLACK_WEBHOOK_MISSING_ENVS.map((env) =>
      axios.get(
        `${API_BASE}/admin/health/slack-webhook-missing/${env}/alert-history?limit=10`,
        { headers: adminHeaders(adminToken), withCredentials: true },
      )
        .then((r) => [env, r.data])
        // Best-effort: a 4xx/5xx (e.g. Mongo down) just means the
        // disclosure stays hidden for this env; the badge itself is
        // unaffected. Mirrors the alert-state catch above so a
        // transient backend hiccup doesn't break the dashboard.
        .catch(() => [env, { events: [] }])
    )).then((pairs) => {
      // Wholesale replace, same reason as alert-state above: a
      // per-env transition from "had pages" to "events trimmed by
      // the audit-log retention policy" must not leave stale rows
      // on the badge.
      setSlackWebhookMissingAlertHistories(Object.fromEntries(pairs));
    });
  }, [adminToken, SLACK_WEBHOOK_MISSING_ENVS]);

  // Task #980 — POST a snooze for one missing-Slack-webhook env and
  // refresh the per-env alert-state so the SlackConfigBadge's
  // tooltip + decoration update atomically without waiting for the
  // next 60s polling tick. The snooze endpoint already echoes back
  // the ``/alert-state`` shape, so the optimistic update can just
  // patch the relevant slot in ``slackWebhookMissingAlertStates``;
  // the next polling tick reconciles in the (vanishingly rare) case
  // where two admins click the button from two tabs at once.
  const snoozeSlackWebhookMissing = useCallback(async (envName, untilHours) => {
    if (!envName || !untilHours) return;
    try {
      const r = await axios.post(
        `${API_BASE}/admin/health/slack-webhook-missing/${envName}/snooze`,
        { untilHours },
        { headers: adminHeaders(adminToken), withCredentials: true },
      );
      if (r && r.data) {
        setSlackWebhookMissingAlertStates((prev) => ({
          ...prev,
          [envName]: r.data,
        }));
      }
    } catch (_e) {
      // Best-effort — the badge's local "snoozing…" flag clears on
      // its finally branch, and the next 60s polling tick re-fetches
      // canonical state. A persistent failure (e.g. Mongo down ⇒ 503)
      // simply leaves the badge in its current shape; the admin can
      // retry.
    }
  }, [adminToken]);

  // Task #255 — GCP credit burn panel row (merged into Task #263 below).

  // Task #263 — CF paid add-on migration status panel.
  const [cfAddons, setCfAddons] = useState(null);
  const [cfAddonsLoading, setCfAddonsLoading] = useState(false);

  const loadCfAddons = useCallback(() => {
    setCfAddonsLoading(true);
    axios.get(`${API_BASE}/admin/credits/cf-addons`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setCfAddons(r.data))
      .catch(() => setCfAddons({ _error: true }))
      .finally(() => setCfAddonsLoading(false));
  }, [adminToken]);

  // Task #263 — AWS Activate credit burn panel.
  const [awsCredits, setAwsCredits] = useState(null);
  const [awsCreditsLoading, setAwsCreditsLoading] = useState(false);

  const loadAwsCredits = useCallback(() => {
    setAwsCreditsLoading(true);
    axios.get(`${API_BASE}/admin/billing/aws-activate`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setAwsCredits(r.data))
      .catch((err) => setAwsCredits(err?.response?.status === 404 ? { configured: false } : { _error: true }))
      .finally(() => setAwsCreditsLoading(false));
  }, [adminToken]);

  // Task #263 — GCP credit burn panel.
  const [gcpCredits, setGcpCredits] = useState(null);
  const [gcpCreditsLoading, setGcpCreditsLoading] = useState(false);

  const loadGcpCredits = useCallback(() => {
    setGcpCreditsLoading(true);
    axios.get(`${API_BASE}/admin/billing/gcp-credits`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setGcpCredits(r.data))
      .catch((err) => setGcpCredits(err?.response?.status === 404 ? { configured: false } : { _error: true }))
      .finally(() => setGcpCreditsLoading(false));
  }, [adminToken]);

  // Task #263 — Axiom startup-tier usage panel.
  const [axiomCredits, setAxiomCredits] = useState(null);
  const [axiomCreditsLoading, setAxiomCreditsLoading] = useState(false);

  const loadAxiomCredits = useCallback(() => {
    setAxiomCreditsLoading(true);
    axios.get(`${API_BASE}/admin/billing/axiom`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setAxiomCredits(r.data))
      .catch((err) => setAxiomCredits(err?.response?.status === 404 ? { configured: false } : { _error: true }))
      .finally(() => setAxiomCreditsLoading(false));
  }, [adminToken]);

  // Task #263 — Sentry startup-tier usage panel.
  const [sentryCredits, setSentryCredits] = useState(null);
  const [sentryCreditsLoading, setSentryCreditsLoading] = useState(false);

  const loadSentryCredits = useCallback(() => {
    setSentryCreditsLoading(true);
    axios.get(`${API_BASE}/admin/billing/sentry`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setSentryCredits(r.data))
      .catch((err) => setSentryCredits(err?.response?.status === 404 ? { configured: false } : { _error: true }))
      .finally(() => setSentryCreditsLoading(false));
  }, [adminToken]);

  // Task #918 — paged-on-call audit log per pill, sourced from
  //   * /admin/health/edge-proxy-deploy/cron/alert-history
  //   * /admin/health/cf-waf-drift/cron/alert-history
  //   * /admin/health/trustpilot/refresh-cron/alert-history
  // Lazy-fetched on first toggle of the pill's "Show paged history"
  // disclosure (NOT included in the 60s polling above) so the
  // page-load payload doesn't carry N×20 history events nobody
  // asked for. Once an admin opens the panel, the data sticks until
  // the next page reload — the 60s polling cadence above is the
  // canonical refresh path; admins click the pill's RefreshCw to
  // force a manual refresh of the rest, and the loader below also
  // re-fires on every disclosure open so a long-open panel reflects
  // the latest events without a full page reload.
  const [edgeProxyDeployCronAlertHistory, setEdgeProxyDeployCronAlertHistory] = useState(null);
  const [cfDriftCronAlertHistory, setCfDriftCronAlertHistory] = useState(null);
  const [tpCronAlertHistory, setTpCronAlertHistory] = useState(null);
  // Task #956 — paged-on-call audit log for the unified-logs CF pull
  // silence alerter. Same lazy contract as the sibling alert-history
  // states above.
  const [unifiedLogsCfPullCronAlertHistory, setUnifiedLogsCfPullCronAlertHistory] = useState(null);

  const loadEdgeProxyDeployCronAlertHistory = useCallback(() => {
    axios.get(`${API_BASE}/admin/health/edge-proxy-deploy/cron/alert-history`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setEdgeProxyDeployCronAlertHistory(r.data))
      .catch(() => setEdgeProxyDeployCronAlertHistory({ events: [] }));
  }, [adminToken]);

  const loadCfDriftCronAlertHistory = useCallback(() => {
    axios.get(`${API_BASE}/admin/health/cf-waf-drift/cron/alert-history`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setCfDriftCronAlertHistory(r.data))
      .catch(() => setCfDriftCronAlertHistory({ events: [] }));
  }, [adminToken]);

  const loadTpCronAlertHistory = useCallback(() => {
    axios.get(`${API_BASE}/admin/health/trustpilot/refresh-cron/alert-history`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setTpCronAlertHistory(r.data))
      .catch(() => setTpCronAlertHistory({ events: [] }));
  }, [adminToken]);

  const loadUnifiedLogsCfPullCronAlertHistory = useCallback(() => {
    axios.get(`${API_BASE}/admin/health/unified-logs/cf-pull/cron/alert-history`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setUnifiedLogsCfPullCronAlertHistory(r.data))
      .catch(() => setUnifiedLogsCfPullCronAlertHistory({ events: [] }));
  }, [adminToken]);

  // Task #508 — D1 mirror lag alerter (Task #460) snapshot + lazy
  // paged-history loader. Same shape as the sibling cron pills above
  // so the AdminHealth dashboard can render the lag pill alongside
  // them; the endpoint already bundles the lock-doc projection onto
  // its response so a single GET feeds both the pill colour and the
  // shared "last paged Xh ago" caption.
  const [d1MirrorLagHealth, setD1MirrorLagHealth] = useState(null);
  const [d1MirrorLagLoading, setD1MirrorLagLoading] = useState(false);
  const [d1MirrorLagAlertHistory, setD1MirrorLagAlertHistory] = useState(null);

  const loadD1MirrorLagHealth = useCallback(() => {
    setD1MirrorLagLoading(true);
    axios.get(`${API_BASE}/admin/health/d1-mirror/lag`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setD1MirrorLagHealth(r.data))
      .catch(() => setD1MirrorLagHealth({ _error: true }))
      .finally(() => setD1MirrorLagLoading(false));
  }, [adminToken]);

  const loadD1MirrorLagAlertHistory = useCallback(() => {
    axios.get(`${API_BASE}/admin/health/d1-mirror/lag/alert-history`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setD1MirrorLagAlertHistory(r.data))
      .catch(() => setD1MirrorLagAlertHistory({ events: [] }));
  }, [adminToken]);

  const loadTpJsonldReport = useCallback(() => {
    setTpJsonldLoading(true);
    axios.get(`${API_BASE}/admin/trustpilot-jsonld/report`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setTpJsonldReport(r.data))
      .catch(() => setTpJsonldReport({ _error: true }))
      .finally(() => setTpJsonldLoading(false));
  }, [adminToken]);

  const loadTpJsonldHistory = useCallback(() => {
    axios.get(`${API_BASE}/admin/trustpilot-jsonld/history`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setTpJsonldHistory(r.data))
      .catch(() => setTpJsonldHistory({ points: [], _error: true }));
  }, [adminToken]);

  const loadTpJsonldAlerts = useCallback(() => {
    // Last 10 is enough to spot a flappy URL at a glance without
    // blowing the tile height; user can deep-link into the full
    // notifications page for more.
    axios.get(`${API_BASE}/admin/trustpilot-jsonld/alerts?limit=10`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setTpJsonldAlerts(r.data))
      .catch(() => setTpJsonldAlerts({ events: [], _error: true }));
  }, [adminToken]);

  useEffect(() => {
    if (!adminToken) return;
    loadChatPipelineProbe();
    loadTpJsonldReport();
    loadTpJsonldHistory();
    loadTpJsonldAlerts();
    loadTpCronHealth();
    loadCfDriftCronHealth();
    loadEdgeProxyDeployCronHealth();
    // Task #956 — unified-logs CF pull silence pill polls on the
    // same 60s cadence as the sibling cron pills so a freshly
    // silent ingest shows up next to cf-waf-drift / edge-proxy-
    // deploy without a page reload.
    loadUnifiedLogsCfPullCronHealth();
    // Task #508 — D1 mirror lag pill polls on the same 60s cadence
    // so a freshly-breached mirror shows up next to the sibling
    // cron pills without a page reload.
    loadD1MirrorLagHealth();
    // Task #902 — pull alerter-state alongside the pill snapshots so
    // the "last paged Xh ago · in debounce ~Yh" caption stays in
    // sync with the pill's colour. Same 60s cadence as the rest;
    // the lock-doc reads are tiny (single Mongo find by _id).
    loadEdgeProxyDeployCronAlertState();
    loadCfDriftCronAlertState();
    loadTpCronAlertState();
    loadUnifiedLogsCfPullCronAlertState();
    // Task #485 — per-model AI Gateway guardrail-spike alerter state
    // for the AiGatewayGuardrailByModelTile inline "paged Xh ago"
    // caption. Same 60s cadence as the sibling alert-state loaders.
    loadAigGuardrailAlertState();
    // Task #974 — per-env missing-Slack-webhook nag state, batched
    // into a single loader so the three GETs fire in parallel and
    // the badges' "· paged Nh ago" decoration stays in lockstep
    // with the rest of the 60s polling cadence.
    loadSlackWebhookMissingAlertStates();
    // Task #979 — pair the per-env alert-state load with a per-env
    // alert-history load on the same 60s cadence; the disclosure
    // under the SlackConfigBadge needs the event count up front to
    // decide whether to render at all.
    loadSlackWebhookMissingAlertHistories();
    // Task #133 — Cloudflare weekly audit card.  The run changes at
    // most once a week, but we still poll on the 60s cadence so a
    // manual re-trigger shows up without a page reload.  The backend
    // Redis-caches the artifact summary per run_id for 4 hours so the
    // artifact ZIP is not re-downloaded on every poll.
    loadCfAudit();
    // Task #419 — unified CF Health snapshot (ai_gateway cache-by-model tile).
    loadCfHealth();
    // Task #255 — GCP credit burn panel row.
    loadGcpCredits();
    // Task #263 — CF add-on migration panel + per-provider credit burn panels.
    loadCfAddons();
    loadAwsCredits();
    loadGcpCredits();
    loadAxiomCredits();
    loadSentryCredits();
    const id = setInterval(() => {
      loadChatPipelineProbe();
      loadTpJsonldReport();
      loadTpJsonldHistory();
      loadTpJsonldAlerts();
      loadTpCronHealth();
      loadCfDriftCronHealth();
      loadEdgeProxyDeployCronHealth();
      loadUnifiedLogsCfPullCronHealth();
      loadD1MirrorLagHealth();
      loadEdgeProxyDeployCronAlertState();
      loadCfDriftCronAlertState();
      loadTpCronAlertState();
      loadUnifiedLogsCfPullCronAlertState();
      // Task #485 — keep the per-model guardrail alerter caption fresh.
      loadAigGuardrailAlertState();
      loadSlackWebhookMissingAlertStates();
      loadSlackWebhookMissingAlertHistories();
      loadCfAudit();
      loadCfHealth();
      loadGcpCredits();
      loadCfAddons();
      loadAwsCredits();
      loadGcpCredits();
      loadAxiomCredits();
      loadSentryCredits();
    }, 60000);
    return () => clearInterval(id);
  }, [adminToken, loadChatPipelineProbe, loadTpJsonldReport, loadTpJsonldHistory,
      loadTpJsonldAlerts, loadTpCronHealth, loadCfDriftCronHealth,
      loadEdgeProxyDeployCronHealth, loadUnifiedLogsCfPullCronHealth,
      loadD1MirrorLagHealth,
      loadEdgeProxyDeployCronAlertState, loadCfDriftCronAlertState,
      loadTpCronAlertState, loadUnifiedLogsCfPullCronAlertState,
      loadAigGuardrailAlertState,
      loadSlackWebhookMissingAlertStates,
      loadSlackWebhookMissingAlertHistories, loadCfAudit, loadCfHealth, loadGcpCredits,
      loadCfAddons, loadAwsCredits, loadGcpCredits, loadAxiomCredits, loadSentryCredits]);

  // Task #609 — managed AI response cache stats + admin purge controls.
  const [aiCacheStats, setAiCacheStats] = useState(null);
  const [aiCacheLoading, setAiCacheLoading] = useState(false);
  const [aiCachePurging, setAiCachePurging] = useState(false);

  const loadAiCacheStats = useCallback(() => {
    setAiCacheLoading(true);
    axios.get(`${API_BASE}/admin/ai/cache/stats`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setAiCacheStats(r.data))
      .catch(() => setAiCacheStats(null))
      .finally(() => setAiCacheLoading(false));
  }, [adminToken]);

  const purgeAiCache = useCallback(async () => {
    if (!window.confirm('Purge all AI response cache entries? Active users will see one slow LLM call before the cache repopulates.')) {
      return;
    }
    setAiCachePurging(true);
    try {
      const r = await axios.post(`${API_BASE}/admin/ai/cache/purge`, null, {
        params: { pattern: '*' },
        headers: adminHeaders(adminToken), withCredentials: true,
      });
      const d = r.data || {};
      if (d.ok === false) {
        toast.error(`Purge failed: ${d.error || 'unknown error'}`);
      } else {
        toast.success(`Purged ${d.deleted ?? 0} cache entries (L1: ${d.l1_cleared ?? 0})`);
      }
      loadAiCacheStats();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Purge failed');
    } finally {
      setAiCachePurging(false);
    }
  }, [adminToken, loadAiCacheStats]);

  useEffect(() => {
    if (!adminToken) return;
    loadAiCacheStats();
    const id = setInterval(loadAiCacheStats, 30000);
    return () => clearInterval(id);
  }, [adminToken, loadAiCacheStats]);

  // Task #207 — Pinecone index health card.
  const [pineconeHealth, setPineconeHealth] = useState(null);
  const [pineconeLoading, setPineconeLoading] = useState(false);
  const [pineconeSwitch, setPineconeSwitch] = useState('');

  const loadPineconeHealth = useCallback(() => {
    setPineconeLoading(true);
    axios.get(`${API_BASE}/admin/health/pinecone`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setPineconeHealth(r.data))
      .catch(() => setPineconeHealth({ _error: true }))
      .finally(() => setPineconeLoading(false));
  }, [adminToken]);

  const switchPineconeRetriever = useCallback(async (name) => {
    setPineconeSwitch(name);
    try {
      await axios.put(`${API_BASE}/admin/retriever/config`, { active: name }, {
        headers: adminHeaders(adminToken), withCredentials: true,
      });
      toast.success(`Active retriever switched to "${name}"`);
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Switch failed');
    } finally {
      setPineconeSwitch('');
    }
  }, [adminToken]);

  useEffect(() => {
    if (!adminToken) return;
    loadPineconeHealth();
    const id = setInterval(loadPineconeHealth, 60000);
    return () => clearInterval(id);
  }, [adminToken, loadPineconeHealth]);

  // Task #636 — Workers AI fallback admin panel state. Polled every
  // 30s on the same cadence as the other health widgets. The
  // kill-switch toggles are per-capability so an outage in one model
  // doesn't force us to disable the entire safety net.
  const [waiStatus, setWaiStatus] = useState(null);
  const [waiToggling, setWaiToggling] = useState('');
  // Task #78 — 429 burst pressure gauge (burst_60s, burst_180s,
  // throttled, alert_threshold) from GET /admin/dashboard/metrics.
  // Piggybacked on the 30s workers-ai poll so no extra interval is needed.
  const [waiThrottle, setWaiThrottle] = useState(null);
  // Tasks #85/#90 — Groq and Gemini 429 burst gauges, same shape as waiThrottle.
  // groqThrottle retained for the legacy backend `groq_throttle` payload
  // surfaced by /admin/dashboard/metrics; not rendered as a card after Task #297.
  const [groqThrottle, setGroqThrottle] = useState(null);
  const [geminiThrottle, setGeminiThrottle] = useState(null);
  // Task #378 — Azure OpenAI and Deepgram 429 burst gauges.  Same shape
  // as the other *Throttle gauges so the existing burst-tile component
  // renders them without changes.  Their alert thresholds were already
  // wired into the alerting pipeline by Task #373 — this surfaces the
  // live burst counts so admins spot a building burst BEFORE on-call
  // gets paged.
  const [azureOpenaiThrottle, setAzureOpenaiThrottle] = useState(null);
  const [deepgramThrottle, setDeepgramThrottle] = useState(null);
  // Task #374 — Assamese chat "both rails red" indicator.  Same shape as
  // the *Throttle gauges so the existing burst-tile component renders it.
  // ``throttled === true`` means both the strict Sarvam → Vertex/Gemini
  // chain (Task #291) AND the Workers-AI Phase-2 fallback have failed
  // enough times within the alerting window to suggest a real outage.
  const [assameseUnavailable, setAssameseUnavailable] = useState(null);
  // Task #396 — freshness indicator for /admin/dashboard/metrics.
  // Backend piggybacks `_meta: {heavy_cached_at, throttle_fresh_at}`
  // (unix seconds) on every response. Throttle tiles are recomputed
  // every poll (Task #388), heavy fields are cached for ~5s (Task
  // #395), so admins can't tell from the numbers alone which half is
  // live vs cached. We render a tiny "Throttle: live • Heavy: Xs ago"
  // strip above the burst tiles using these timestamps. The 1s tick
  // below keeps the "Xs ago" label updating between the 30s polls so
  // the cache age advances visibly while the panel sits idle — without
  // it the label would stay frozen and admins would still mistake a
  // 25s-old number for "just refreshed".
  const [metricsMeta, setMetricsMeta] = useState(null);
  const [, setMetricsMetaTick] = useState(0);
  useEffect(() => {
    if (!metricsMeta) return undefined;
    const id = setInterval(() => {
      setMetricsMetaTick((t) => (t + 1) % 1_000_000);
    }, 1000);
    return () => clearInterval(id);
  }, [metricsMeta]);
  // Task #379 — expand/collapse state for the Assamese tile's recent-events
  // list. Auto-expands while the rail is throttled so on-call sees the
  // failing leg + error excerpt without an extra click; operators can
  // collapse it again to reclaim screen real-estate.
  const [assameseRecentExpanded, setAssameseRecentExpanded] = useState(false);
  // Task #297 — locked provider chain surfacing (deepgram, workers_ai_indic,
  // mongodb_atlas) sourced from GET /admin/routing-config.
  const [routingConfig, setRoutingConfig] = useState(null);
  // Task #379 — auto-expand the Assamese recent-events panel as soon as
  // the rail flips to "throttled" so on-call sees the failing leg + error
  // excerpt the moment the alert fires (without an extra click).
  useEffect(() => {
    if (assameseUnavailable?.throttled) setAssameseRecentExpanded(true);
  }, [assameseUnavailable?.throttled]);
  // Task #93 — embed 429 cooldown stats from GET /admin/llm/pool-stats.
  const [embedBurst, setEmbedBurst] = useState(null);
  // Task #98 — live countdown display for the embed cooldown timer.
  const [embedCooldownDisplay, setEmbedCooldownDisplay] = useState(0);
  const embedCooldownRef = useRef(null);
  useEffect(() => {
    if (embedCooldownRef.current) {
      clearInterval(embedCooldownRef.current);
      embedCooldownRef.current = null;
    }
    if (embedBurst?.cooldown) {
      setEmbedCooldownDisplay(Math.ceil(embedBurst.remainingS));
      embedCooldownRef.current = setInterval(() => {
        setEmbedCooldownDisplay(prev => Math.max(0, prev - 1));
      }, 1000);
    } else {
      setEmbedCooldownDisplay(0);
    }
    return () => {
      if (embedCooldownRef.current) {
        clearInterval(embedCooldownRef.current);
        embedCooldownRef.current = null;
      }
    };
  }, [embedBurst]);
  const loadWorkersAi = useCallback(() => {
    Promise.allSettled([
      axios.get(`${API_BASE}/admin/workers-ai/status`, {
        headers: adminHeaders(adminToken), withCredentials: true,
      }),
      axios.get(`${API_BASE}/admin/dashboard/metrics`, {
        headers: adminHeaders(adminToken), withCredentials: true,
      }),
      axios.get(`${API_BASE}/admin/llm/pool-stats`, {
        headers: adminHeaders(adminToken), withCredentials: true,
      }),
    ]).then(([statusRes, metricsRes, poolRes]) => {
      if (statusRes.status === 'fulfilled') setWaiStatus(statusRes.value.data);
      else setWaiStatus(null);
      if (metricsRes.status === 'fulfilled') {
        const md = metricsRes.value.data;
        setWaiThrottle(md?.workers_ai_throttle ?? null);
        setGroqThrottle(md?.groq_throttle ?? null);
        setGeminiThrottle(md?.gemini_throttle ?? null);
        // Task #378 — Azure OpenAI + Deepgram burst tiles.
        setAzureOpenaiThrottle(md?.azure_openai_throttle ?? null);
        setDeepgramThrottle(md?.deepgram_throttle ?? null);
        // Task #374 — "both rails red" indicator for Assamese chat.
        setAssameseUnavailable(md?.assamese_chat_unavailable ?? null);
        // Task #396 — freshness indicator (heavy_cached_at, throttle_fresh_at).
        setMetricsMeta(md?._meta ?? null);
      } else {
        setWaiThrottle(null);
        setGroqThrottle(null);
        setGeminiThrottle(null);
        setAzureOpenaiThrottle(null);
        setDeepgramThrottle(null);
        setAssameseUnavailable(null);
        setMetricsMeta(null);
      }
      if (poolRes.status === 'fulfilled')
        setEmbedBurst({
          burst:       poolRes.value.data?.embed_429_burst ?? 0,
          cooldown:    poolRes.value.data?.embed_cooldown_active ?? false,
          remainingS:  poolRes.value.data?.embed_cooldown_remaining_s ?? 0,
          threshold:   poolRes.value.data?.embed_429_threshold ?? 3,
          durationS:   poolRes.value.data?.embed_cooldown_duration_s ?? 60,
        });
      else setEmbedBurst(false);
    });
  }, [adminToken]);
  const toggleWorkersAi = useCallback(async (capability, enabled) => {
    setWaiToggling(capability);
    try {
      await axios.post(`${API_BASE}/admin/workers-ai/kill-switch`,
        { capability, enabled },
        { headers: adminHeaders(adminToken), withCredentials: true });
      toast.success(`Workers AI ${capability}: ${enabled ? 'enabled' : 'disabled'}`);
      loadWorkersAi();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Toggle failed');
    } finally {
      setWaiToggling('');
    }
  }, [adminToken, loadWorkersAi]);
  useEffect(() => {
    if (!adminToken) return;
    loadWorkersAi();
    const id = setInterval(loadWorkersAi, 30000);
    return () => clearInterval(id);
  }, [adminToken, loadWorkersAi]);

  // Task #422 — Assamese purity admin override controls.
  const [asmCfg, setAsmCfg] = useState(null);
  const [asmLoading, setAsmLoading] = useState(false);
  const [asmSaving, setAsmSaving] = useState(false);
  const [asmTesting, setAsmTesting] = useState(false);
  const [asmDraft, setAsmDraft] = useState({ behaviour: '', threshold: '' });
  const [asmTestResult, setAsmTestResult] = useState(null);
  const [asmTestSample, setAsmTestSample] = useState('');
  // Task #423 — sanitiser-run stats (rolling 24h / 7d).
  const [asmStats, setAsmStats] = useState(null);
  const [asmStatsLoading, setAsmStatsLoading] = useState(false);
  const [asmStatsWindow, setAsmStatsWindow] = useState('24h');
  // Task #424 — append-only audit log of override edits.
  const [asmAudit, setAsmAudit] = useState(null);
  const [asmAuditLoading, setAsmAuditLoading] = useState(false);
  // Task #430 — search/paginate the audit history. `since`/`until` are
  // bound to <input type="datetime-local"> values so they're naive
  // strings; we send them as-is and the backend treats naive as UTC.
  const ASM_AUDIT_PAGE = 20;
  const [asmAuditFilters, setAsmAuditFilters] = useState({
    admin_email: '', since: '', until: '',
  });
  const [asmAuditOffset, setAsmAuditOffset] = useState(0);
  // Task #431 — id of the audit row currently being reverted (so we can
  // disable just that row's button instead of the whole table).
  const [asmRevertingId, setAsmRevertingId] = useState(null);
  // Task #441 — row being previewed in the side-by-side revert modal.
  // Holding the row (not just the id) means we can render the snapshot
  // even after the user navigates the audit page underneath the modal.
  const [asmRevertPreview, setAsmRevertPreview] = useState(null);
  // Task #428 — per-run audit log of individual sanitiser cleanups.
  const [asmRuns, setAsmRuns] = useState(null);
  const [asmRunsLoading, setAsmRunsLoading] = useState(false);
  const [asmRunsActionFilter, setAsmRunsActionFilter] = useState('');
  const [asmRunsExpanded, setAsmRunsExpanded] = useState({});

  // NOTE: callers always pass {offset, filters} overrides for paging /
  // filtering so this callback can stay free of state deps. Keeping
  // it stable also stops the tab-open effect from re-firing on every
  // filter keystroke (which would race the user's typing).
  const loadAsmAudit = useCallback((overrides = {}) => {
    const offset = overrides.offset !== undefined ? overrides.offset : 0;
    const filters = overrides.filters !== undefined
      ? overrides.filters
      : { admin_email: '', since: '', until: '' };
    setAsmAuditLoading(true);
    const params = { limit: ASM_AUDIT_PAGE, offset };
    if (filters.admin_email?.trim()) params.admin_email = filters.admin_email.trim();
    if (filters.since) params.since = new Date(filters.since).toISOString();
    if (filters.until) params.until = new Date(filters.until).toISOString();
    axios.get(`${API_BASE}/admin/assamese-purity/audit`, {
      params,
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setAsmAudit(r.data))
      .catch((e) => {
        const msg = e?.response?.data?.detail || 'Failed to load audit log';
        toast.error(msg);
      })
      .finally(() => setAsmAuditLoading(false));
  }, [adminToken]);

  // Task #441 — open the side-by-side preview instead of the legacy
  // window.confirm. The actual POST is fired from `confirmAsmRevert`
  // once the admin OKs the diff in the modal.
  const revertAsmAuditRow = useCallback((row) => {
    if (!row?.id) {
      toast.error('This audit row predates revert support — no id to target.');
      return;
    }
    setAsmRevertPreview(row);
  }, []);

  // NOTE: `loadAsmCfg` MUST be declared before `confirmAsmRevert`
  // (and any other useCallback that captures it). It's a `const`
  // declaration so it lives in the temporal dead zone until this
  // line runs — referencing it earlier in component-body order
  // (even inside a useCallback body that won't actually invoke
  // until later) crashes the whole AdminHealth component with
  // "Cannot access 'loadAsmCfg' before initialization" the moment
  // React executes the body, which trips the
  // <SectionErrorBoundary> wrapper and replaces the entire Health
  // tab with the "failed to load" card. Do not move this back
  // below `confirmAsmRevert`.
  const loadAsmCfg = useCallback(() => {
    setAsmLoading(true);
    axios.get(`${API_BASE}/admin/assamese-purity`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => {
        setAsmCfg(r.data);
        const cfg = r.data?.config || {};
        setAsmDraft({
          behaviour: cfg.behaviour || '',
          threshold: cfg.threshold != null ? String(cfg.threshold) : '',
          indic_provider: cfg.indic_provider || '',
        });
        setAsmTestSample(r.data?.test_sample || '');
      })
      .catch((e) => {
        const msg = e?.response?.data?.detail || 'Failed to load purity config';
        toast.error(msg);
      })
      .finally(() => setAsmLoading(false));
  }, [adminToken]);

  const confirmAsmRevert = useCallback(async () => {
    const row = asmRevertPreview;
    if (!row?.id) return;
    setAsmRevertingId(row.id);
    try {
      await axios.post(
        `${API_BASE}/admin/assamese-purity/audit/${encodeURIComponent(row.id)}/revert`,
        null,
        { headers: adminHeaders(adminToken), withCredentials: true },
      );
      toast.success('Reverted — applied immediately');
      setAsmRevertPreview(null);
      loadAsmCfg();
      loadAsmAudit({
        offset: asmAudit?.offset ?? asmAuditOffset,
        filters: asmAuditFilters,
      });
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Revert failed';
      toast.error(msg);
    } finally {
      setAsmRevertingId(null);
    }
  }, [adminToken, asmRevertPreview, loadAsmCfg, loadAsmAudit, asmAudit, asmAuditOffset, asmAuditFilters]);

  const loadAsmRuns = useCallback((actionFilter) => {
    const a = actionFilter !== undefined ? actionFilter : asmRunsActionFilter;
    setAsmRunsLoading(true);
    const params = { limit: 50 };
    if (a) params.action = a;
    axios.get(`${API_BASE}/admin/assamese-purity/runs`, {
      params,
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setAsmRuns(r.data))
      .catch((e) => {
        const msg = e?.response?.data?.detail || 'Failed to load recent cleanups';
        toast.error(msg);
      })
      .finally(() => setAsmRunsLoading(false));
  }, [adminToken, asmRunsActionFilter]);

  const loadAsmStats = useCallback((win) => {
    const w = win || asmStatsWindow;
    setAsmStatsLoading(true);
    axios.get(`${API_BASE}/admin/assamese-purity/stats`, {
      params: { window: w },
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setAsmStats(r.data))
      .catch((e) => {
        const msg = e?.response?.data?.detail || 'Failed to load purity stats';
        toast.error(msg);
      })
      .finally(() => setAsmStatsLoading(false));
  }, [adminToken, asmStatsWindow]);

  const saveAsmOverride = useCallback(async () => {
    const body = {};
    const cfgNow = asmCfg?.config || {};
    if (asmDraft.behaviour && asmDraft.behaviour !== cfgNow.behaviour) {
      body.behaviour = asmDraft.behaviour;
    }
    const t = asmDraft.threshold === '' ? null : Number(asmDraft.threshold);
    if (t != null && Number.isFinite(t) && t !== cfgNow.threshold) {
      body.threshold = t;
    }
    if (asmDraft.indic_provider && asmDraft.indic_provider !== cfgNow.indic_provider) {
      body.indic_provider = asmDraft.indic_provider;
    }
    if (!Object.keys(body).length) {
      toast.info('No changes to save');
      return;
    }
    setAsmSaving(true);
    try {
      await axios.patch(`${API_BASE}/admin/assamese-purity`, body, {
        headers: adminHeaders(adminToken), withCredentials: true,
      });
      toast.success('Override saved — applied immediately');
      loadAsmCfg();
      // Preserve the admin's active filters/page so the new audit row
      // appears in context rather than yanking them back to "all rows".
      loadAsmAudit({
        offset: asmAudit?.offset ?? asmAuditOffset,
        filters: asmAuditFilters,
      });
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Failed to save override';
      toast.error(msg);
    } finally {
      setAsmSaving(false);
    }
  }, [adminToken, asmDraft, asmCfg, loadAsmCfg, loadAsmAudit, asmAudit, asmAuditOffset, asmAuditFilters]);

  const clearAsmOverride = useCallback(async () => {
    setAsmSaving(true);
    try {
      await axios.delete(`${API_BASE}/admin/assamese-purity`, {
        headers: adminHeaders(adminToken), withCredentials: true,
      });
      toast.success('Override cleared — env vars now in effect');
      setAsmTestResult(null);
      loadAsmCfg();
      loadAsmAudit({
        offset: asmAudit?.offset ?? asmAuditOffset,
        filters: asmAuditFilters,
      });
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Failed to clear override';
      toast.error(msg);
    } finally {
      setAsmSaving(false);
    }
  }, [adminToken, loadAsmCfg, loadAsmAudit, asmAudit, asmAuditOffset, asmAuditFilters]);

  const fireAsmTest = useCallback(async () => {
    setAsmTesting(true);
    setAsmTestResult(null);
    try {
      const r = await axios.post(
        `${API_BASE}/admin/assamese-purity/test`,
        asmTestSample ? { sample: asmTestSample } : {},
        { headers: adminHeaders(adminToken), withCredentials: true },
      );
      setAsmTestResult(r.data);
    } catch (e) {
      const msg = e?.response?.data?.detail || 'Test fire failed';
      toast.error(msg);
    } finally {
      setAsmTesting(false);
    }
  }, [adminToken, asmTestSample]);

  const loadPrerender = useCallback(() => {
    setPrerenderLoading(true);
    axios.get(`${API_BASE}/admin/prerender/status`, {
      headers: adminHeaders(adminToken),
      withCredentials: true,
    })
      .then((r) => setPrerender(r.data))
      .catch((e) => setPrerender({ _error: e?.response?.data?.detail || 'Failed to load prerender status' }))
      .finally(() => setPrerenderLoading(false));
  }, [adminToken]);

  const triggerPrerender = useCallback(() => {
    setPrerenderTriggering(true);
    axios.post(`${API_BASE}/admin/prerender/refresh?immediate=true`, null, {
      headers: adminHeaders(adminToken),
      withCredentials: true,
    })
      .then((r) => {
        setPrerender(r.data?.status || null);
        toast.success(r.data?.queued ? 'Cloudflare Pages rebuild queued' : 'Refresh requested (not queued)');
      })
      .catch((e) => {
        const msg = e?.response?.data?.detail || 'Failed to trigger refresh';
        toast.error(msg);
      })
      .finally(() => {
        setPrerenderTriggering(false);
        setTimeout(loadPrerender, 800);
      });
  }, [adminToken, loadPrerender]);

  useEffect(() => { if (healthTab === 'prerender') loadPrerender(); }, [healthTab, loadPrerender]);
  useEffect(() => {
    if (healthTab === 'asm') {
      loadAsmCfg();
      loadAsmStats();
      loadAsmAudit();
      loadAsmRuns();
    }
  }, [healthTab, loadAsmCfg, loadAsmStats, loadAsmAudit, loadAsmRuns]);

  const loadHealth = () => {
    setLoading(true);
    axios.get(`${API_BASE.replace('/api','')}/api/health`)
      .then((r) => setHealth(r.data))
      .catch(() => setHealth({ status: 'error', dependencies: {} }))
      .finally(() => setLoading(false));
  };

  const loadMetrics = useCallback(() => {
    setMetricsLoading(true);
    axios.get(`${API_BASE}/metrics/history?minutes=${timeRange}`, {
      headers: adminHeaders(adminToken),
      withCredentials: true,
    })
      .then((r) => setMetricsData(r.data))
      .catch(() => setMetricsData(null))
      .finally(() => setMetricsLoading(false));
  }, [adminToken, timeRange]);

  const loadLlmCosts = useCallback(async () => {
    setLlmLoading(true);
    try {
      const r = await llmCosts(adminToken, llmDays);
      setLlmData(r.data);
    } catch (err) { console.warn('AdminHealth: llmCosts() failed:', err); } finally { setLlmLoading(false); }
  }, [adminToken, llmDays]);

  // Task #279 — provider speed bench (latest run from bench_results/latest.json).
  const [benchLatest, setBenchLatest] = useState(null);
  const [benchLoading, setBenchLoading] = useState(false);
  const loadBenchLatest = useCallback(() => {
    setBenchLoading(true);
    axios.get(`${API_BASE}/admin/bench/latest`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setBenchLatest(r.data))
      .catch(() => setBenchLatest({ ok: false, has_results: false }))
      .finally(() => setBenchLoading(false));
  }, [adminToken]);

  useEffect(() => { loadHealth(); }, []);
  useEffect(() => { loadMetrics(); }, [loadMetrics]);
  useEffect(() => { if (healthTab === 'llm') loadLlmCosts(); }, [healthTab, loadLlmCosts]);
  useEffect(() => { if (healthTab === 'infra') loadBenchLatest(); }, [healthTab, loadBenchLatest]);

  useEffect(() => {
    const interval = setInterval(loadMetrics, 60000);
    return () => clearInterval(interval);
  }, [loadMetrics]);

  const healthUrl = `${import.meta.env.VITE_BACKEND_URL || ''}/health`;
  const handleCopy = () => {
    navigator.clipboard.writeText(healthUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const deps = health?.dependencies || {};
  const allOk = Object.values(deps).every((d) => d.status === 'ok' || d.status === 'not_configured' || d.status === 'unavailable');
  const hasError = Object.values(deps).some((d) => d.status === 'error' || d.status === 'not_configured');

  const chartData = (metricsData?.history || []).map((s) => ({
    ...s,
    time: s.t ? new Date(s.t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '',
  }));

  const peaks = metricsData?.peaks || {};
  const current = metricsData?.current || {};

  return (
    <SectionErrorBoundary name="Health" resetKeys={[healthTab]}>
      <div className="space-y-5 max-w-4xl">
        <div className="flex gap-1 p-1 rounded-xl w-fit bg-gray-100">
          {[
            { id: 'infra',      label: 'Infrastructure' },
            { id: 'llm',        label: 'LLM Cost Tracker' },
            { id: 'prerender',  label: 'Prerender Refresh' },
            { id: 'asm',        label: 'Sarvam Purity' },
            { id: 'workers-ai', label: 'Workers AI Fallback' },
            { id: 'rag',        label: 'RAG / Vectorize' },
          ].map(t => (
            <button key={t.id} onClick={() => setHealthTab(t.id)}
              className={`px-4 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                healthTab === t.id
                  ? 'bg-violet-600 text-white shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
              }`}>
              {t.label}
            </button>
          ))}
        </div>

        {healthTab === 'llm' && (
          <LlmTab
            adminToken={adminToken}
            llmData={llmData} llmLoading={llmLoading} llmDays={llmDays}
            setLlmDays={setLlmDays} setLlmLoading={setLlmLoading} setLlmData={setLlmData}
            loadLlmCosts={loadLlmCosts}
          />
        )}
        {healthTab === 'prerender' && (
          <PrerenderTab
            prerender={prerender} prerenderLoading={prerenderLoading}
            prerenderTriggering={prerenderTriggering}
            loadPrerender={loadPrerender} triggerPrerender={triggerPrerender}
          />
        )}
        {healthTab === 'asm' && (
          <AsmTab
            adminToken={adminToken}
            asmStats={asmStats} asmStatsLoading={asmStatsLoading}
            asmStatsWindow={asmStatsWindow} setAsmStatsWindow={setAsmStatsWindow}
            loadAsmStats={loadAsmStats}
            asmLoading={asmLoading} asmDraft={asmDraft} setAsmDraft={setAsmDraft}
            asmCfg={asmCfg} asmSaving={asmSaving}
            asmRuns={asmRuns} asmRunsLoading={asmRunsLoading}
            asmRunsActionFilter={asmRunsActionFilter} setAsmRunsActionFilter={setAsmRunsActionFilter}
            asmRunsExpanded={asmRunsExpanded} setAsmRunsExpanded={setAsmRunsExpanded}
            loadAsmRuns={loadAsmRuns}
            asmAudit={asmAudit} asmAuditLoading={asmAuditLoading}
            asmAuditFilters={asmAuditFilters} setAsmAuditFilters={setAsmAuditFilters}
            asmAuditOffset={asmAuditOffset} setAsmAuditOffset={setAsmAuditOffset}
            loadAsmAudit={loadAsmAudit}
            asmRevertingId={asmRevertingId} asmRevertPreview={asmRevertPreview}
            setAsmRevertPreview={setAsmRevertPreview}
            revertAsmAuditRow={revertAsmAuditRow} confirmAsmRevert={confirmAsmRevert}
            asmTesting={asmTesting} asmTestResult={asmTestResult} setAsmTestResult={setAsmTestResult}
            asmTestSample={asmTestSample} setAsmTestSample={setAsmTestSample}
            fireAsmTest={fireAsmTest}
            loadAsmCfg={loadAsmCfg} saveAsmOverride={saveAsmOverride} clearAsmOverride={clearAsmOverride}
            ASM_AUDIT_PAGE={ASM_AUDIT_PAGE}
          />
        )}
        {healthTab === 'workers-ai' && (
          <WorkersAiTab
            adminToken={adminToken}
            waiStatus={waiStatus} waiToggling={waiToggling}
            toggleWorkersAi={toggleWorkersAi} loadWorkersAi={loadWorkersAi}
            waiThrottle={waiThrottle} groqThrottle={groqThrottle}
            geminiThrottle={geminiThrottle} azureOpenaiThrottle={azureOpenaiThrottle}
            deepgramThrottle={deepgramThrottle}
            assameseUnavailable={assameseUnavailable}
            assameseRecentExpanded={assameseRecentExpanded}
            setAssameseRecentExpanded={setAssameseRecentExpanded}
            routingConfig={routingConfig} setRoutingConfig={setRoutingConfig}
            embedBurst={embedBurst} embedCooldownDisplay={embedCooldownDisplay}
            metricsMeta={metricsMeta}
            chatPipelineProbe={chatPipelineProbe}
            chatPipelineLoading={chatPipelineLoading}
            loadChatPipelineProbe={loadChatPipelineProbe}
          />
        )}
        {healthTab === 'rag' && (
          <RagTab adminToken={adminToken} />
        )}
        {healthTab === 'infra' && (
          <InfraTab
            adminToken={adminToken} onNavigate={onNavigate}
            health={health} loading={loading} deps={deps} allOk={allOk} hasError={hasError}
            chartData={chartData} peaks={peaks} current={current}
            metricsLoading={metricsLoading} timeRange={timeRange} setTimeRange={setTimeRange}
            loadMetrics={loadMetrics} loadHealth={loadHealth}
            benchLatest={benchLatest} benchLoading={benchLoading} loadBenchLatest={loadBenchLatest}
            cfAddons={cfAddons} cfAddonsLoading={cfAddonsLoading} loadCfAddons={loadCfAddons}
            awsCredits={awsCredits} awsCreditsLoading={awsCreditsLoading} loadAwsCredits={loadAwsCredits}
            gcpCredits={gcpCredits} gcpCreditsLoading={gcpCreditsLoading} loadGcpCredits={loadGcpCredits}
            axiomCredits={axiomCredits} axiomCreditsLoading={axiomCreditsLoading} loadAxiomCredits={loadAxiomCredits}
            sentryCredits={sentryCredits} sentryCreditsLoading={sentryCreditsLoading} loadSentryCredits={loadSentryCredits}
            cfAuditData={cfAuditData} cfAuditLoading={cfAuditLoading} loadCfAudit={loadCfAudit}
            cfHealthData={cfHealthData} cfHealthLoading={cfHealthLoading} loadCfHealth={loadCfHealth}
            edgeProxyDeployCronHealth={edgeProxyDeployCronHealth}
            edgeProxyDeployCronLoading={edgeProxyDeployCronLoading}
            loadEdgeProxyDeployCronHealth={loadEdgeProxyDeployCronHealth}
            cfDriftCronHealth={cfDriftCronHealth} cfDriftCronLoading={cfDriftCronLoading}
            loadCfDriftCronHealth={loadCfDriftCronHealth}
            tpCronHealth={tpCronHealth} tpCronLoading={tpCronLoading} loadTpCronHealth={loadTpCronHealth}
            unifiedLogsCfPullCronHealth={unifiedLogsCfPullCronHealth}
            unifiedLogsCfPullCronLoading={unifiedLogsCfPullCronLoading}
            loadUnifiedLogsCfPullCronHealth={loadUnifiedLogsCfPullCronHealth}
            edgeProxyDeployCronAlertState={edgeProxyDeployCronAlertState}
            cfDriftCronAlertState={cfDriftCronAlertState}
            tpCronAlertState={tpCronAlertState}
            unifiedLogsCfPullCronAlertState={unifiedLogsCfPullCronAlertState}
            aigGuardrailAlertState={aigGuardrailAlertState}
            slackWebhookMissingAlertStates={slackWebhookMissingAlertStates}
            slackWebhookMissingAlertHistories={slackWebhookMissingAlertHistories}
            snoozeSlackWebhookMissing={snoozeSlackWebhookMissing}
            edgeProxyDeployCronAlertHistory={edgeProxyDeployCronAlertHistory}
            cfDriftCronAlertHistory={cfDriftCronAlertHistory}
            tpCronAlertHistory={tpCronAlertHistory}
            unifiedLogsCfPullCronAlertHistory={unifiedLogsCfPullCronAlertHistory}
            loadEdgeProxyDeployCronAlertHistory={loadEdgeProxyDeployCronAlertHistory}
            loadCfDriftCronAlertHistory={loadCfDriftCronAlertHistory}
            loadTpCronAlertHistory={loadTpCronAlertHistory}
            loadUnifiedLogsCfPullCronAlertHistory={loadUnifiedLogsCfPullCronAlertHistory}
            d1MirrorLagHealth={d1MirrorLagHealth} d1MirrorLagLoading={d1MirrorLagLoading}
            loadD1MirrorLagHealth={loadD1MirrorLagHealth}
            d1MirrorLagAlertHistory={d1MirrorLagAlertHistory}
            loadD1MirrorLagAlertHistory={loadD1MirrorLagAlertHistory}
            tpJsonldReport={tpJsonldReport} tpJsonldLoading={tpJsonldLoading}
            tpJsonldHistory={tpJsonldHistory} tpJsonldAlerts={tpJsonldAlerts}
            loadTpJsonldReport={loadTpJsonldReport}
            loadTpJsonldHistory={loadTpJsonldHistory}
            loadTpJsonldAlerts={loadTpJsonldAlerts}
            aiCacheStats={aiCacheStats} aiCacheLoading={aiCacheLoading}
            aiCachePurging={aiCachePurging} loadAiCacheStats={loadAiCacheStats}
            purgeAiCache={purgeAiCache}
            pineconeHealth={pineconeHealth} pineconeLoading={pineconeLoading}
            pineconeSwitch={pineconeSwitch} switchPineconeRetriever={switchPineconeRetriever}
            loadPineconeHealth={loadPineconeHealth}
            healthUrl={healthUrl} copied={copied} handleCopy={handleCopy}
            SLACK_WEBHOOK_MISSING_ENVS={SLACK_WEBHOOK_MISSING_ENVS}
          />
        )}
      </div>
    </SectionErrorBoundary>
  );
}
