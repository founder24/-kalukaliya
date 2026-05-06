import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Database, Zap, CreditCard, RefreshCw, ShieldCheck, AlertTriangle, Wifi, Copy, Check, Users, Activity, MessageSquare, TrendingUp, DollarSign, BarChart2, RotateCw, Clock, Undo2, Star, ExternalLink } from 'lucide-react';
import CronHealthPill, { SlackConfigBadge } from './CronHealthPill';
import CfWafDriftCronPill from './CfWafDriftCronPill';
import TrustpilotRefreshCronPill from './TrustpilotRefreshCronPill';
import EdgeProxyDeployCronPill from './EdgeProxyDeployCronPill';
import UnifiedLogsCfPullCronPill from './UnifiedLogsCfPullCronPill';
import CfAuditCard from './CfAuditCard';
import AiGatewayCacheByModelTile from './AiGatewayCacheByModelTile';
// Phase 4 — Task #332. SQS+Lambda worker tier health (replaces the
// GCP Cloud Tasks tile inside AdminGcpPanel) and the Azure Container
// Apps Jobs cron health table (replaces the Cloud Scheduler source
// previously consumed by CronHealthPill). Both cards live under the
// Infrastructure tab so the on-call has a single pane of glass.
import AdminAwsInfraCard from './AdminAwsInfraCard';
import AdminCronJobsCard from './AdminCronJobsCard';
import AdminMemoryBrainTile from './AdminMemoryBrainTile';
// Phase 5b — Task #338. Azure-native AI features panel (Azure OpenAI,
// AI Speech, Translator, Document Intelligence, AI Vision, Content
// Safety, AI Language, AI Search, Anomaly Detector, Personalizer).
// Renders below the AWS + Azure cron tiles so the admin sees the
// per-feature throttle / latency / spend together with the rest of
// the migrated infrastructure.
import AdminAzureAiPanel from './AdminAzureAiPanel';
import { toast } from 'sonner';
import AdminQuickLinks from './AdminQuickLinks';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, BarChart, Bar, LineChart, Line } from 'recharts';
import axios from 'axios';
import { computeHeavyFreshness, computeThrottleFreshness } from '@/utils/metricsFreshness';
import { llmCosts, API_BASE } from '@/utils/api';
import { buildHighlightedSegments } from '@/utils/highlightSegments';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import EdgeMetricsPanel from './EdgeMetricsPanel';
const adminHeaders = (token) => {
  const isRealJwt = token && typeof token === 'string' && token.split('.').length === 3;
  return isRealJwt ? { Authorization: `Bearer ${token}` } : {};
};

function LatencyBadge({ ms }) {
  if (!ms && ms !== 0) return <span className="text-xs text-gray-400">—</span>;
  const color = ms < 200 ? 'text-emerald-600' : ms < 600 ? 'text-amber-600' : 'text-red-600';
  return <span className={`text-xs font-mono ${color}`}>{ms}ms</span>;
}

function PeakBadge({ label, value, color = 'violet' }) {
  const colors = {
    violet: 'bg-violet-50 text-violet-600 border-violet-200',
    emerald: 'bg-emerald-50 text-emerald-600 border-emerald-200',
    amber: 'bg-amber-50 text-amber-600 border-amber-200',
    blue: 'bg-blue-50 text-blue-600 border-blue-200',
  };
  return (
    <div className={`rounded-xl border px-4 py-3 ${colors[color]} bg-white`}>
      <p className="text-[10px] uppercase tracking-wider opacity-60 mb-1">{label}</p>
      <p className="text-2xl font-bold font-mono" data-testid={`peak-${label.replace(/\s+/g, '-').toLowerCase()}`}>{value}</p>
    </div>
  );
}

const TOOLTIP_STYLE = { background: '#ffffff', border: '1px solid #e5e7eb', borderRadius: '12px', color: '#374151', fontSize: 12, boxShadow: '0 4px 16px rgba(0,0,0,0.08)' };

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={TOOLTIP_STYLE} className="p-3">
      <p className="text-xs text-gray-400 mb-1">{label}</p>
      {payload.map((p, i) => (
        <p key={i} className="text-xs" style={{ color: p.color }}>
          {p.name}: <span className="font-mono font-bold">{p.value}</span>
        </p>
      ))}
    </div>
  );
}

function formatRelative(epochSec) {
  if (!epochSec) return 'never';
  const d = new Date(epochSec * 1000);
  const diff = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// Backend contract from artifacts/syrabit-backend/pages_deploy.py status():
//   configured: bool
//   last_triggered_at: float epoch seconds | null
//   last_status: "ok" | "http_NNN" | "error" | "not_configured" | null
//   last_reason: string | null
//   last_error: string | null
//   pending_reasons: string[]
//   pending: bool
//   trigger_count: int
//   coalesce_window_sec / min_interval_sec / nightly_interval_sec: int
function ProviderLatencyBench({ benchLatest, benchLoading, loadBenchLatest }) {
  const [sortBy, setSortBy] = React.useState('ttft_warm_p50_ms');
  const [sortDir, setSortDir] = React.useState('asc');
  const [openSuite, setOpenSuite] = React.useState(null);

  const latest = benchLatest?.latest;
  const hasResults = benchLatest?.has_results && latest?.suites;
  const generatedAt = latest?.generated_at;
  const generatedSec = generatedAt ? Math.floor(new Date(generatedAt).getTime() / 1000) : null;
  const suiteLabels = {
    english_chat: 'English chat',
    assamese_chat: 'Assamese chat',
    long_form: 'Long-form (~1500w)',
  };

  function sortRows(rows) {
    const dir = sortDir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      if (a[1].skipped && !b[1].skipped) return 1;
      if (b[1].skipped && !a[1].skipped) return -1;
      const av = a[1][sortBy];
      const bv = b[1][sortBy];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av - bv) * dir;
    });
  }
  function clickSort(col) {
    if (sortBy === col) setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    else { setSortBy(col); setSortDir(col === 'tokens_per_sec_p50' ? 'desc' : 'asc'); }
  }
  const SortHeader = ({ col, children }) => (
    <th
      className="text-right px-2 py-1 font-medium text-gray-500 cursor-pointer hover:text-gray-800 select-none"
      onClick={() => clickSort(col)}
      data-testid={`sort-${col}`}
    >
      {children}{sortBy === col ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
    </th>
  );

  const methodologyTip = (
    "Each provider runs warm-up calls (TTFT cold) followed by sampled runs (TTFT warm). " +
    "TTFT = time to first streamed token. Total = end-to-end including decode. " +
    "Same prompts/system messages used across providers for fair comparison. " +
    "Source: artifacts/syrabit-backend/scripts/bench_llm_providers.py"
  );

  return (
    <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
            Provider Latency (TTFT)
            <span
              className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-gray-100 text-gray-500 text-[9px] font-bold cursor-help border border-gray-200"
              title={methodologyTip}
              data-testid="bench-methodology-tooltip"
            >
              ?
            </span>
            <a
              href="https://github.com/syrabit/syrabit/blob/main/artifacts/syrabit-backend/scripts/bench_llm_providers.py"
              target="_blank" rel="noreferrer"
              className="text-[10px] font-normal text-blue-500 hover:underline"
            >
              How was this measured?
            </a>
          </h3>
          <p className="text-xs text-gray-400 mt-0.5">
            Head-to-head LLM speed bench · Task #279
            {generatedSec ? ` · last benchmarked ${formatRelative(generatedSec)}` : ''}
            {hasResults ? ` · ${latest.runs_per_suite} run${latest.runs_per_suite === 1 ? '' : 's'}/suite, ${latest.warmups} warm-up${latest.warmups === 1 ? '' : 's'}` : ''}
          </p>
        </div>
        <button
          onClick={loadBenchLatest}
          disabled={benchLoading}
          className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100"
          data-testid="button-refresh-bench-latest"
        >
          <RefreshCw size={14} className={benchLoading ? 'animate-spin' : ''} />
        </button>
      </div>
      {benchLoading ? (
        <div className="flex justify-center p-6"><RefreshCw size={18} className="animate-spin text-gray-300" /></div>
      ) : !hasResults ? (
        <div className="text-center py-8 text-gray-400">
          <p className="text-sm">No benchmark runs yet.</p>
          <p className="text-xs mt-1">
            Run <code className="bg-gray-50 px-1 rounded border border-gray-200">python -m scripts.bench_llm_providers --runs 10 --warm 2</code> to populate this tile.
          </p>
        </div>
      ) : Object.keys(latest.suites).length === 0 ? (
        <div className="text-center py-8 text-gray-400 text-sm">
          Benchmark file present but contains no suites.
        </div>
      ) : (
        <div className="space-y-5">
          {Object.entries(latest.suites).map(([suiteId, suite]) => {
            const rows = sortRows(Object.entries(suite.providers || {}));
            return (
              <div key={suiteId} data-testid={`bench-suite-${suiteId}`}>
                <div className="flex items-baseline justify-between mb-1.5">
                  <p className="text-xs font-semibold text-gray-700">
                    {suiteLabels[suiteId] || suite.label || suiteId}
                  </p>
                  {suite.winner && (
                    <p className="text-[10px] text-emerald-600 font-medium">
                      ⚡ fastest: <span className="font-mono">{suite.winner.provider}</span> ({Math.round(suite.winner.value)}ms)
                    </p>
                  )}
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="border-b border-gray-100">
                        <th className="text-left px-2 py-1 font-medium text-gray-500">Provider</th>
                        <SortHeader col="ttft_cold_p50_ms">TTFT cold p50</SortHeader>
                        <SortHeader col="ttft_warm_p50_ms">TTFT warm p50</SortHeader>
                        <SortHeader col="ttft_warm_p95_ms">TTFT warm p95</SortHeader>
                        <SortHeader col="total_p50_ms">Total p50</SortHeader>
                        <SortHeader col="tokens_per_sec_p50">tok/s</SortHeader>
                        <SortHeader col="success_rate">Succ.</SortHeader>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map(([pid, r]) => (
                        <tr key={pid} className="border-b border-gray-50 last:border-0" data-testid={`bench-row-${suiteId}-${pid}`}>
                          <td className="px-2 py-1.5 font-mono text-gray-700" title={r.model}>
                            {pid}
                            <div className="text-[9px] text-gray-300 font-mono truncate max-w-[180px]">{r.model}</div>
                          </td>
                          {r.skipped ? (
                            <td colSpan={6} className="px-2 py-1.5 text-[10px] text-gray-400 italic" title={r.reason}>
                              skipped — {String(r.reason || '').slice(0, 80)}
                            </td>
                          ) : r.samples > 0 ? (
                            <>
                              <td className="px-2 py-1.5 text-right"><LatencyBadge ms={r.ttft_cold_p50_ms != null ? Math.round(r.ttft_cold_p50_ms) : null} /></td>
                              <td className="px-2 py-1.5 text-right"><LatencyBadge ms={Math.round(r.ttft_warm_p50_ms ?? r.ttft_p50_ms)} /></td>
                              <td className="px-2 py-1.5 text-right"><LatencyBadge ms={Math.round(r.ttft_warm_p95_ms ?? r.ttft_p95_ms)} /></td>
                              <td className="px-2 py-1.5 text-right text-gray-500 font-mono">{Math.round(r.total_p50_ms)}ms</td>
                              <td className="px-2 py-1.5 text-right text-gray-500 font-mono">{r.tokens_per_sec_p50}</td>
                              <td className="px-2 py-1.5 text-right text-gray-500 font-mono">{Math.round((r.success_rate || 0) * 100)}%</td>
                            </>
                          ) : (
                            <td colSpan={6} className="px-2 py-1.5 text-[10px] text-gray-400 italic">no samples</td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function classifyStatus(s) {
  if (s === 'ok') return 'ok';
  if (s === null || s === undefined) return 'idle';
  // "error", "not_configured", or any "http_NNN" — all failure modes.
  return 'fail';
}

function PrerenderStatusBody({ status }) {
  const {
    configured,
    last_triggered_at: last,
    last_status: lastStatus,
    last_reason: lastReason,
    last_error: lastError,
    pending_reasons: pendingReasons = [],
    pending,
    trigger_count: triggerCount,
    coalesce_window_sec: coalesceSec,
    min_interval_sec: minIntervalSec,
    nightly_interval_sec: nightlySec,
  } = status;

  const klass = classifyStatus(lastStatus);
  const statusColor =
    klass === 'ok'   ? 'text-emerald-600 bg-emerald-50 border-emerald-200'
    : klass === 'fail' ? 'text-red-600 bg-red-50 border-red-200'
    : 'text-gray-500 bg-gray-50 border-gray-200';
  const statusLabel = lastStatus ?? 'never fired';

  return (
    <div className="space-y-3">
      {configured === false && (
        <div className="flex items-start gap-2 p-3 rounded-xl bg-amber-50 border border-amber-200 text-xs text-amber-700">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span><span className="font-mono font-semibold">CF_PAGES_DEPLOY_HOOK_URL</span> is not configured on the backend. Refresh requests will fail.</span>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="rounded-xl p-3 border border-gray-200 bg-white">
          <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1 flex items-center gap-1">
            <Clock size={10} /> Last fired
          </p>
          <p className="text-sm font-mono font-semibold text-gray-900" data-testid="prerender-last-fired">
            {formatRelative(last)}
          </p>
          {last && (
            <p className="text-[10px] text-gray-400 mt-0.5">
              {new Date(last * 1000).toLocaleString()}
            </p>
          )}
        </div>

        <div className={`rounded-xl p-3 border ${statusColor}`}>
          <p className="text-[10px] uppercase tracking-wider opacity-60 mb-1">Last status</p>
          <p className="text-sm font-mono font-semibold" data-testid="prerender-last-status">{statusLabel}</p>
          {lastReason && <p className="text-[10px] opacity-70 mt-0.5 truncate" title={lastReason}>{lastReason}</p>}
        </div>

        <div className="rounded-xl p-3 border border-gray-200 bg-white">
          <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Pending reasons</p>
          <p className="text-sm font-mono font-semibold text-gray-900" data-testid="prerender-queued-count">
            {pendingReasons.length}{pending ? ' · queued' : ''}
          </p>
          {pendingReasons.length > 0 && (
            <p className="text-[10px] text-gray-400 mt-0.5 truncate" title={pendingReasons.join(', ')}>
              {pendingReasons.slice(0, 3).join(', ')}{pendingReasons.length > 3 ? '…' : ''}
            </p>
          )}
        </div>
      </div>

      {lastError && (
        <div className="flex items-start gap-2 p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-700">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold mb-0.5">Last error</p>
            <p className="font-mono break-all">{String(lastError)}</p>
          </div>
        </div>
      )}

      <div className="text-[11px] text-gray-400 leading-relaxed border-t border-gray-100 pt-3 mt-1">
        Total triggers: <span className="font-mono text-gray-600">{triggerCount ?? 0}</span>
        {' · '}
        coalesce window <span className="font-mono text-gray-600">{coalesceSec ?? '?'}s</span>
        {' · '}
        cooldown <span className="font-mono text-gray-600">{minIntervalSec ?? '?'}s</span>
        {' · '}
        nightly safety-net every <span className="font-mono text-gray-600">{nightlySec ?? '?'}s</span>.
      </div>
    </div>
  );
}

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

  // Task #255 — GCP credit burn panel row.
  const [gcpCredits, setGcpCredits] = useState(null);
  const [gcpCreditsLoading, setGcpCreditsLoading] = useState(false);

  const loadGcpCredits = useCallback(() => {
    setGcpCreditsLoading(true);
    axios.get(`${API_BASE}/admin/vertex/gcp-credits`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setGcpCredits(r.data))
      .catch(() => setGcpCredits({ _error: true }))
      .finally(() => setGcpCreditsLoading(false));
  }, [adminToken]);

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

  // Task #263 — Azure for Startups credit burn panel.
  const [azureCredits, setAzureCredits] = useState(null);
  const [azureCreditsLoading, setAzureCreditsLoading] = useState(false);

  const loadAzureCredits = useCallback(() => {
    setAzureCreditsLoading(true);
    axios.get(`${API_BASE}/admin/billing/azure-startups`, {
      headers: adminHeaders(adminToken), withCredentials: true,
    })
      .then((r) => setAzureCredits(r.data))
      .catch((err) => setAzureCredits(err?.response?.status === 404 ? { configured: false } : { _error: true }))
      .finally(() => setAzureCreditsLoading(false));
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
    // Task #902 — pull alerter-state alongside the pill snapshots so
    // the "last paged Xh ago · in debounce ~Yh" caption stays in
    // sync with the pill's colour. Same 60s cadence as the rest;
    // the lock-doc reads are tiny (single Mongo find by _id).
    loadEdgeProxyDeployCronAlertState();
    loadCfDriftCronAlertState();
    loadTpCronAlertState();
    loadUnifiedLogsCfPullCronAlertState();
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
    loadAzureCredits();
    loadAxiomCredits();
    loadSentryCredits();
    const id = setInterval(() => {
      loadTpJsonldReport();
      loadTpJsonldHistory();
      loadTpJsonldAlerts();
      loadTpCronHealth();
      loadCfDriftCronHealth();
      loadEdgeProxyDeployCronHealth();
      loadUnifiedLogsCfPullCronHealth();
      loadEdgeProxyDeployCronAlertState();
      loadCfDriftCronAlertState();
      loadTpCronAlertState();
      loadUnifiedLogsCfPullCronAlertState();
      loadSlackWebhookMissingAlertStates();
      loadSlackWebhookMissingAlertHistories();
      loadCfAudit();
      loadCfHealth();
      loadGcpCredits();
      loadCfAddons();
      loadAwsCredits();
      loadAzureCredits();
      loadAxiomCredits();
      loadSentryCredits();
    }, 60000);
    return () => clearInterval(id);
  }, [adminToken, loadTpJsonldReport, loadTpJsonldHistory,
      loadTpJsonldAlerts, loadTpCronHealth, loadCfDriftCronHealth,
      loadEdgeProxyDeployCronHealth, loadUnifiedLogsCfPullCronHealth,
      loadEdgeProxyDeployCronAlertState, loadCfDriftCronAlertState,
      loadTpCronAlertState, loadUnifiedLogsCfPullCronAlertState,
      loadSlackWebhookMissingAlertStates,
      loadSlackWebhookMissingAlertHistories, loadCfAudit, loadCfHealth, loadGcpCredits,
      loadCfAddons, loadAwsCredits, loadAzureCredits, loadAxiomCredits, loadSentryCredits]);

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

  const healthUrl = `${import.meta.env.VITE_BACKEND_URL || ''}/health`;

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
            { id: 'infra',     label: 'Infrastructure' },
            { id: 'llm',       label: 'LLM Cost Tracker' },
            { id: 'prerender', label: 'Prerender Refresh' },
            { id: 'asm',       label: 'Sarvam Purity' },
            { id: 'workers-ai',label: 'Workers AI Fallback' },
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
          <SectionErrorBoundary name="LLM Cost Tracker" resetKeys={[healthTab]}>
          <div className="space-y-4">
            <div className="flex items-center gap-2 mb-2">
              {[7, 14, 30].map(d => (
                <button key={d} onClick={() => { setLlmDays(d); setLlmLoading(true); llmCosts(adminToken, d).then(r => setLlmData(r.data)).catch(() => {}).finally(() => setLlmLoading(false)); }}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all border ${
                    llmDays === d ? 'bg-violet-50 border-violet-200 text-violet-600' : 'border-gray-200 text-gray-400 hover:text-gray-600'
                  }`}>
                  {d}d
                </button>
              ))}
              <button onClick={loadLlmCosts} disabled={llmLoading}
                className="ml-2 px-3 py-1.5 rounded-lg text-xs border border-gray-200 text-gray-400 hover:text-gray-600">
                {llmLoading ? 'Loading…' : '↻ Refresh'}
              </button>
            </div>
            {llmLoading ? (
              <div className="flex justify-center p-10"><RefreshCw size={20} className="animate-spin text-gray-300" /></div>
            ) : llmData ? (
              <>
                <SectionErrorBoundary name="LLM Cost Stats">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {[
                    { label: `Total Cost (${llmDays}d)`, value: `$${llmData?.total_cost_usd || '0.000000'}`, color: 'amber' },
                    { label: 'Total Cost (INR)', value: `₹${llmData?.total_cost_inr || '0.0000'}`, color: 'emerald' },
                    { label: 'Total Tokens', value: Number(llmData?.total_tokens || 0).toLocaleString(), color: 'violet' },
                    { label: 'Cost/Page', value: `$${llmData?.cost_per_published_page_usd || '0.000000'}`, color: 'blue' },
                  ].map(s => <PeakBadge key={s.label} label={s.label} value={s.value} color={s.color} />)}
                </div>
                </SectionErrorBoundary>

                {(llmData?.by_model?.length > 0) && (
                  <SectionErrorBoundary name="Cost by Model">
                  <div className="rounded-xl p-5 bg-white border border-gray-200 shadow-sm">
                    <h3 className="text-sm font-semibold text-gray-900 mb-4">Cost by Model</h3>
                    <div className="space-y-3">
                      {llmData.by_model.map(m => {
                        const pct = llmData.total_cost_usd > 0 ? Math.round(m.cost_usd / llmData.total_cost_usd * 100) : 0;
                        return (
                          <div key={m.model}>
                            <div className="flex justify-between mb-1">
                              <span className="text-xs text-gray-600 font-mono">{m.model}</span>
                              <span className="text-xs text-violet-600">${m.cost_usd} ({m.calls} calls)</span>
                            </div>
                            <div className="h-1 rounded-full overflow-hidden bg-gray-100">
                              <div style={{ width: `${pct}%`, height: '100%', background: 'linear-gradient(90deg,#7c3aed,#a78bfa)', borderRadius: 2 }} />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                  </SectionErrorBoundary>
                )}

                {(llmData?.daily?.length > 0) && (
                  <SectionErrorBoundary name="Daily LLM Spend">
                  <div className="rounded-xl p-5 bg-white border border-gray-200 shadow-sm">
                    <h3 className="text-sm font-semibold text-gray-900 mb-4">Daily LLM Spend</h3>
                    <ResponsiveContainer width="100%" height={160}>
                      <BarChart data={llmData.daily}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                        <XAxis dataKey="date" tick={{ fill: '#9ca3af', fontSize: 10 }} tickFormatter={d => d?.slice(5)} axisLine={false} tickLine={false} />
                        <YAxis tick={{ fill: '#9ca3af', fontSize: 10 }} axisLine={false} tickLine={false} />
                        <Tooltip content={<CustomTooltip />} />
                        <Bar dataKey="cost_usd" name="Cost (USD)" fill="#7c3aed" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  </SectionErrorBoundary>
                )}

                {llmData?.total_calls === 0 && (
                  <div className="text-center py-12 text-gray-400">
                    <DollarSign size={32} className="mx-auto mb-3 opacity-30" />
                    <p className="text-sm">No LLM calls recorded yet — costs will appear here as content is generated</p>
                  </div>
                )}
              </>
            ) : null}
          </div>
          </SectionErrorBoundary>
        )}

        {healthTab === 'prerender' && (
          <SectionErrorBoundary name="Prerender Refresh" resetKeys={[healthTab]}>
          <div className="space-y-4">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <RotateCw size={14} className="text-violet-500" />
                    Cloudflare Pages prerender refresh
                  </h3>
                  <p className="text-xs text-gray-500 mt-1">
                    Rebuilds the prerendered subject &amp; chapter HTML so admin edits go live for crawlers and first paint.
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <button
                    onClick={loadPrerender}
                    disabled={prerenderLoading}
                    className="px-3 py-1.5 rounded-lg text-xs border border-gray-200 text-gray-500 hover:text-gray-700 disabled:opacity-50"
                    data-testid="button-prerender-reload"
                  >
                    <RefreshCw size={12} className={`inline mr-1 ${prerenderLoading ? 'animate-spin' : ''}`} />
                    Reload
                  </button>
                  <button
                    onClick={triggerPrerender}
                    disabled={prerenderTriggering}
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
                    data-testid="button-prerender-refresh-now"
                  >
                    <RotateCw size={12} className={`inline mr-1 ${prerenderTriggering ? 'animate-spin' : ''}`} />
                    {prerenderTriggering ? 'Queueing…' : 'Refresh now'}
                  </button>
                </div>
              </div>

              {prerenderLoading && !prerender ? (
                <div className="flex justify-center p-8">
                  <RefreshCw size={20} className="animate-spin text-gray-300" />
                </div>
              ) : prerender?._error ? (
                <div className="flex items-start gap-2 p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-700">
                  <AlertTriangle size={14} className="mt-0.5 shrink-0" />
                  <span>{prerender._error}</span>
                </div>
              ) : prerender ? (
                <PrerenderStatusBody status={prerender} />
              ) : (
                <p className="text-xs text-gray-400">No status loaded.</p>
              )}
            </div>

            <p className="text-[11px] text-gray-400 leading-relaxed">
              Admin edits trigger debounced refreshes automatically. “Refresh now” bypasses the debounce/cooldown and fires the Cloudflare Pages deploy hook immediately.
            </p>
          </div>
          </SectionErrorBoundary>
        )}

        {healthTab === 'asm' && (
          <SectionErrorBoundary name="Sarvam Purity" resetKeys={[healthTab]}>
          <div className="space-y-4" data-testid="asm-purity-tab">
            {/* Task #423 — sanitiser-run stats so admins can see whether the
                override they just set is actually changing live behaviour. */}
            <SectionErrorBoundary name="ASM Cleanup Activity">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm" data-testid="asm-stats-card">
              <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <Activity size={16} className="text-blue-500" />
                    Cleanup activity
                  </h3>
                  <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                    How often the sanitiser fired against real Sarvam Indic chat replies, what action it took, and how leaky those replies were.
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  {['24h', '7d'].map((w) => (
                    <button
                      key={w}
                      onClick={() => { setAsmStatsWindow(w); loadAsmStats(w); }}
                      className={`px-3 py-1 rounded-lg text-[11px] font-semibold transition-all ${
                        asmStatsWindow === w
                          ? 'bg-blue-600 text-white shadow-sm'
                          : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                      }`}
                      data-testid={`button-asm-window-${w}`}
                    >
                      {w}
                    </button>
                  ))}
                  <button
                    onClick={() => loadAsmStats()}
                    disabled={asmStatsLoading}
                    className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100"
                    data-testid="button-refresh-asm-stats"
                    title="Refresh stats"
                  >
                    <RefreshCw size={14} className={asmStatsLoading ? 'animate-spin' : ''} />
                  </button>
                </div>
              </div>

              {asmStats && asmStats.ok === false && (
                <div className="mb-3 p-3 rounded-xl bg-red-50 border border-red-200 flex items-start gap-2" data-testid="asm-stats-error">
                  <AlertTriangle size={14} className="text-red-500 mt-0.5 flex-shrink-0" />
                  <div className="text-[11px] text-red-700 leading-relaxed">
                    <span className="font-semibold">Stats backend unavailable.</span>{' '}
                    {asmStats.error || 'Aggregation failed — see api logs.'} Numbers below default to zero and are not authoritative.
                  </div>
                </div>
              )}
              {asmStatsLoading && !asmStats ? (
                <div className="flex justify-center py-10"><RefreshCw size={20} className="animate-spin text-gray-300" /></div>
              ) : asmStats ? (
                asmStats.total === 0 ? (
                  <p className="text-xs text-gray-400 py-6 text-center" data-testid="asm-stats-empty">
                    No sanitiser runs recorded in the last {asmStatsWindow}. Stats appear once Indic chat traffic flows through the sanitiser.
                  </p>
                ) : (
                  <>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                      <PeakBadge label="Total runs" value={asmStats.total.toLocaleString()} color="blue" />
                      <PeakBadge label="Cleanup fired" value={`${asmStats.active.toLocaleString()} (${asmStats.total ? Math.round(100 * asmStats.active / asmStats.total) : 0}%)`} color="amber" />
                      <PeakBadge label="Avg leakage" value={(asmStats.avg_ratio || 0).toFixed(4)} color="violet" />
                      <PeakBadge label="p95 leakage" value={(asmStats.p95_ratio || 0).toFixed(4)} color="emerald" />
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div>
                        <p className="text-[10px] uppercase tracking-wider text-gray-500 font-bold mb-2">Action breakdown</p>
                        <div className="space-y-1.5" data-testid="asm-stats-actions">
                          {Object.entries(asmStats.actions || {}).sort((a, b) => b[1] - a[1]).map(([action, count]) => {
                            const pct = asmStats.total ? Math.round(100 * count / asmStats.total) : 0;
                            return (
                              <div key={action} className="flex items-center gap-2">
                                <span className="text-xs font-mono text-gray-700 w-32 truncate" title={action}>{action}</span>
                                <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                                  <div className="h-full bg-blue-400" style={{ width: `${pct}%` }} />
                                </div>
                                <span className="text-[11px] text-gray-500 font-mono w-20 text-right">{count.toLocaleString()} · {pct}%</span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase tracking-wider text-gray-500 font-bold mb-2">Behaviour split</p>
                        <div className="space-y-1.5" data-testid="asm-stats-behaviours">
                          {Object.entries(asmStats.behaviours || {}).sort((a, b) => b[1] - a[1]).map(([beh, count]) => {
                            const pct = asmStats.total ? Math.round(100 * count / asmStats.total) : 0;
                            return (
                              <div key={beh} className="flex items-center gap-2">
                                <span className="text-xs font-mono text-gray-700 w-32 truncate" title={beh}>{beh}</span>
                                <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                                  <div className="h-full bg-violet-400" style={{ width: `${pct}%` }} />
                                </div>
                                <span className="text-[11px] text-gray-500 font-mono w-20 text-right">{count.toLocaleString()} · {pct}%</span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </div>

                    {(asmStats.translated > 0 || asmStats.regenerated > 0) && (
                      <p className="text-[11px] text-gray-400 mt-3 leading-relaxed">
                        <span className="font-semibold text-gray-500">Translate-fix:</span> {asmStats.translated.toLocaleString()} runs ·{' '}
                        <span className="font-semibold text-gray-500">Regenerate:</span> {asmStats.regenerated.toLocaleString()} runs
                      </p>
                    )}
                  </>
                )
              ) : (
                <p className="text-xs text-gray-400 py-6 text-center">Stats unavailable.</p>
              )}
            </div>
            </SectionErrorBoundary>

            {/* Task #428 — drill into individual sanitiser runs so admins
                can see the exact replies that got translated/stripped/
                regenerated and tune the threshold from real evidence. */}
            <SectionErrorBoundary name="ASM Recent Runs">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm" data-testid="asm-runs-card">
              <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <MessageSquare size={16} className="text-amber-500" />
                    Recent cleanups
                  </h3>
                  <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                    Last 50 sanitiser runs (newest first) with the original vs cleaned text. Snippets are truncated to 600 chars and PII (emails, phone numbers, long digit IDs) is scrubbed before persisting. Noop runs are still recorded for traceability but omit the original/cleaned snippets.
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  <select
                    value={asmRunsActionFilter}
                    onChange={(e) => { setAsmRunsActionFilter(e.target.value); loadAsmRuns(e.target.value); }}
                    className="text-[11px] font-mono px-2 py-1 rounded-lg border border-gray-200 focus:border-amber-300 focus:ring-1 focus:ring-amber-200 outline-none"
                    data-testid="select-asm-runs-action"
                    title="Filter by action"
                  >
                    <option value="">All actions</option>
                    <option value="stripped">stripped</option>
                    <option value="translated">translated</option>
                    <option value="translated+stripped">translated+stripped</option>
                    <option value="regenerated">regenerated</option>
                    <option value="regenerated+translated">regenerated+translated</option>
                    <option value="regenerated+stripped">regenerated+stripped</option>
                    <option value="regenerated+translated+stripped">regenerated+translated+stripped</option>
                    <option value="noop">noop</option>
                  </select>
                  <button
                    onClick={() => loadAsmRuns()}
                    disabled={asmRunsLoading}
                    className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100"
                    data-testid="button-refresh-asm-runs"
                    title="Refresh recent cleanups"
                  >
                    <RefreshCw size={14} className={asmRunsLoading ? 'animate-spin' : ''} />
                  </button>
                </div>
              </div>

              {asmRuns && asmRuns.ok === false && (
                <div className="mb-3 p-3 rounded-xl bg-red-50 border border-red-200 flex items-start gap-2" data-testid="asm-runs-error">
                  <AlertTriangle size={14} className="text-red-500 mt-0.5 flex-shrink-0" />
                  <div className="text-[11px] text-red-700 leading-relaxed">
                    <span className="font-semibold">Recent cleanups unavailable.</span>{' '}
                    {asmRuns.error || 'Mongo read failed — see api logs.'}
                  </div>
                </div>
              )}

              {asmRunsLoading && !asmRuns ? (
                <div className="flex justify-center py-10"><RefreshCw size={20} className="animate-spin text-gray-300" /></div>
              ) : asmRuns?.entries?.length ? (
                <ul className="space-y-2" data-testid="asm-runs-list">
                  {asmRuns.entries.map((row, idx) => {
                    const expanded = !!asmRunsExpanded[idx];
                    const ratioLabel = `${(row.ratio || 0).toFixed(4)} → ${(row.post_ratio || 0).toFixed(4)}`;
                    const actionColor = row.action === 'noop'
                      ? 'bg-gray-50 text-gray-500 border-gray-200'
                      : row.action?.includes('regenerated')
                        ? 'bg-blue-50 text-blue-600 border-blue-200'
                        : row.action?.includes('translated')
                          ? 'bg-violet-50 text-violet-600 border-violet-200'
                          : 'bg-amber-50 text-amber-600 border-amber-200';
                    return (
                      <li key={idx} className="rounded-xl border border-gray-200 bg-white" data-testid={`asm-run-row-${idx}`}>
                        <button
                          type="button"
                          onClick={() => setAsmRunsExpanded(prev => ({ ...prev, [idx]: !prev[idx] }))}
                          className="w-full flex items-center gap-2 p-3 text-left hover:bg-gray-50 rounded-xl"
                        >
                          <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold border ${actionColor}`}>
                            {row.action || '—'}
                          </span>
                          <span className="text-[11px] font-mono text-gray-500 truncate">
                            {row.behaviour || '—'} · {ratioLabel}
                          </span>
                          <span className="text-[11px] text-gray-400 ml-auto font-mono whitespace-nowrap">
                            {row.ts ? new Date(row.ts).toLocaleString() : '—'}
                          </span>
                        </button>
                        {expanded && (
                          <div className="px-3 pb-3 space-y-2" data-testid={`asm-run-detail-${idx}`}>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                              <div className="rounded-lg border border-gray-200 p-2 bg-gray-50">
                                <div className="flex items-center justify-between mb-1">
                                  <p className="text-[10px] uppercase tracking-wider text-gray-500 font-bold">Original</p>
                                  {Array.isArray(row.suspicious_tokens) && row.suspicious_tokens.length > 0 && (
                                    <span
                                      className="text-[9px] uppercase tracking-wider text-amber-700 font-semibold"
                                      data-testid={`asm-run-token-count-${idx}`}
                                    >
                                      {row.suspicious_tokens.length} flagged
                                    </span>
                                  )}
                                </div>
                                <p
                                  className="text-xs text-gray-800 font-mono whitespace-pre-wrap break-words"
                                  data-testid={`asm-run-original-${idx}`}
                                >
                                  {row.raw_snippet ? (
                                    buildHighlightedSegments(row.raw_snippet, row.suspicious_tokens).map((seg, i) =>
                                      seg.highlight ? (
                                        <mark
                                          key={i}
                                          className="bg-amber-200 text-amber-900 rounded px-0.5"
                                          data-testid={`asm-run-token-${idx}-${i}`}
                                        >
                                          {seg.text}
                                        </mark>
                                      ) : (
                                        <span key={i}>{seg.text}</span>
                                      ),
                                    )
                                  ) : (
                                    <span className="text-gray-400">(not persisted)</span>
                                  )}
                                </p>
                              </div>
                              <div className="rounded-lg border border-emerald-200 p-2 bg-emerald-50">
                                <p className="text-[10px] uppercase tracking-wider text-emerald-700 font-bold mb-1">Cleaned</p>
                                <p className="text-xs text-gray-800 font-mono whitespace-pre-wrap break-words">
                                  {row.cleaned_snippet || <span className="text-gray-400">(not persisted)</span>}
                                </p>
                              </div>
                            </div>
                            <div className="text-[10px] font-mono text-gray-500 flex flex-wrap gap-x-3 gap-y-1">
                              <span>threshold: {(row.threshold || 0).toFixed(3)}</span>
                              <span>translated: {String(!!row.translated)}</span>
                              <span>regenerated: {String(!!row.regenerated)}</span>
                              <span>has_assamese: {String(row.has_assamese !== false)}</span>
                            </div>
                            {(row.conversation_id || row.user_id) && (
                              <div
                                className="text-[10px] font-mono text-gray-600 flex flex-wrap gap-x-3 gap-y-1 pt-1 border-t border-gray-100"
                                data-testid={`asm-run-trace-${idx}`}
                              >
                                {row.conversation_id && (
                                  <span data-testid={`asm-run-conv-${idx}`}>
                                    conversation: <span className="text-gray-800">{row.conversation_id}</span>
                                  </span>
                                )}
                                {row.user_id && (
                                  <span data-testid={`asm-run-user-${idx}`}>
                                    user: <span className="text-gray-800">{row.user_id}</span>
                                  </span>
                                )}
                              </div>
                            )}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-xs text-gray-400 py-6 text-center" data-testid="asm-runs-empty">
                  {asmRunsActionFilter
                    ? `No recent cleanups match action="${asmRunsActionFilter}".`
                    : 'No sanitiser runs recorded yet. Entries appear once Indic chat traffic flows through cleanup.'}
                </p>
              )}
            </div>
            </SectionErrorBoundary>

            <SectionErrorBoundary name="ASM Configuration">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <Zap size={16} className="text-violet-500" />
                    Assamese Purity Override
                  </h3>
                  <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                    Live behaviour and threshold for Sarvam Assamese leakage cleanup. Changes apply immediately and survive restarts (persisted in <code className="font-mono text-[11px] text-gray-600">db.api_config</code>).
                  </p>
                </div>
                <button
                  onClick={loadAsmCfg}
                  disabled={asmLoading}
                  className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100"
                  data-testid="button-refresh-asm"
                  title="Refresh"
                >
                  <RefreshCw size={14} className={asmLoading ? 'animate-spin' : ''} />
                </button>
              </div>

              {asmLoading && !asmCfg ? (
                <div className="flex justify-center py-10"><RefreshCw size={20} className="animate-spin text-gray-300" /></div>
              ) : asmCfg ? (
                <>
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-5">
                    <PeakBadge label="Active behaviour" value={asmCfg.config?.behaviour || '—'} color="violet" />
                    <PeakBadge label="Active threshold" value={asmCfg.config?.threshold != null ? Number(asmCfg.config.threshold).toFixed(3) : '—'} color="emerald" />
                    <PeakBadge label="Indic provider" value={asmCfg.config?.indic_provider || '—'} color={asmCfg.config?.indic_provider === 'vertex' ? 'amber' : 'blue'} />
                    <PeakBadge label="Behaviour source" value={asmCfg.config?.behaviour_source || '—'} color={asmCfg.config?.behaviour_source === 'override' ? 'amber' : 'blue'} />
                    <PeakBadge label="Threshold source" value={asmCfg.config?.threshold_source || '—'} color={asmCfg.config?.threshold_source === 'override' ? 'amber' : 'blue'} />
                    <PeakBadge label="Provider source" value={asmCfg.config?.indic_provider_source || '—'} color={asmCfg.config?.indic_provider_source === 'override' ? 'amber' : 'blue'} />
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Behaviour</label>
                      <select
                        value={asmDraft.behaviour}
                        onChange={(e) => setAsmDraft(d => ({ ...d, behaviour: e.target.value }))}
                        className="w-full text-sm font-mono px-3 py-2 rounded-lg border border-gray-200 focus:border-violet-300 focus:ring-1 focus:ring-violet-200 outline-none"
                        data-testid="select-asm-behaviour"
                      >
                        {(asmCfg.config?.valid_behaviours || []).map(b => (
                          <option key={b} value={b}>{b}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Threshold (0–1)</label>
                      <input
                        type="number"
                        min="0.001"
                        max="0.999"
                        step="0.005"
                        value={asmDraft.threshold}
                        onChange={(e) => setAsmDraft(d => ({ ...d, threshold: e.target.value }))}
                        className="w-full text-sm font-mono px-3 py-2 rounded-lg border border-gray-200 focus:border-violet-300 focus:ring-1 focus:ring-violet-200 outline-none"
                        data-testid="input-asm-threshold"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">
                        Indic provider
                        <span className="ml-2 text-[10px] font-normal text-amber-600 normal-case tracking-normal">A/B</span>
                      </label>
                      <select
                        value={asmDraft.indic_provider}
                        onChange={(e) => setAsmDraft(d => ({ ...d, indic_provider: e.target.value }))}
                        className="w-full text-sm font-mono px-3 py-2 rounded-lg border border-gray-200 focus:border-violet-300 focus:ring-1 focus:ring-violet-200 outline-none"
                        data-testid="select-asm-indic-provider"
                        title="sarvam = existing hedged Sarvam pool · vertex = Gemini Flash fast-path (auto-falls back to sarvam on Vertex failure)"
                      >
                        {(asmCfg.config?.valid_indic_providers || ['sarvam', 'vertex']).map(p => (
                          <option key={p} value={p}>{p}</option>
                        ))}
                      </select>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      onClick={saveAsmOverride}
                      disabled={asmSaving}
                      className="px-4 py-2 rounded-lg bg-violet-600 text-white text-xs font-semibold shadow-sm hover:bg-violet-700 disabled:opacity-50"
                      data-testid="button-save-asm"
                    >
                      {asmSaving ? 'Saving…' : 'Save override'}
                    </button>
                    <button
                      onClick={clearAsmOverride}
                      disabled={asmSaving || !asmCfg.persisted}
                      className="px-4 py-2 rounded-lg border border-gray-200 text-gray-500 text-xs font-semibold hover:bg-gray-50 disabled:opacity-40"
                      data-testid="button-clear-asm"
                      title={asmCfg.persisted ? 'Drop the override and revert to env vars' : 'No override to clear'}
                    >
                      Clear override
                    </button>
                    {asmCfg.persisted?.updated_at && (
                      <span className="text-[11px] text-gray-400 ml-auto font-mono">
                        Last edit by {asmCfg.persisted.updated_by || 'admin'} · {new Date(asmCfg.persisted.updated_at).toLocaleString()}
                      </span>
                    )}
                  </div>

                  <p className="text-[11px] text-gray-400 leading-relaxed mt-4">
                    Defaults: behaviour <code className="font-mono">{asmCfg.config?.default_behaviour}</code> · threshold <code className="font-mono">{asmCfg.config?.default_threshold}</code>. <span className="text-amber-600">Override</span> beats env vars; env vars beat defaults. Source columns above tell you what's currently winning.
                  </p>
                </>
              ) : null}
            </div>
            </SectionErrorBoundary>

            <SectionErrorBoundary name="ASM Trial Sentence">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-sm font-semibold text-gray-900 mb-1 flex items-center gap-2">
                <ShieldCheck size={16} className="text-emerald-500" />
                Test fire
              </h3>
              <p className="text-xs text-gray-500 mb-3 leading-relaxed">
                Sends the sample below through the LIVE sanitiser using the currently active behaviour. Use this to validate a new override before letting real users hit it.
              </p>
              {asmCfg?.config?.behaviour && (asmCfg.config.behaviour === 'regenerate' || asmCfg.config.behaviour === 'translate+regenerate') && (
                <div className="mb-3 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-[11px] text-amber-800 leading-relaxed" data-testid="asm-regenerate-warning">
                  <strong>Heads up:</strong> the active behaviour includes <code className="font-mono">regenerate</code>, but the test-fire route does not have a real chat context, so the regenerate step will be skipped here (you'll see <code className="font-mono">regenerated: false</code> in the diagnostic). Translate / strip behaviour IS exercised. Use a real chat query in Assamese to fully validate regenerate end-to-end.
                </div>
              )}

              <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Sample (Assamese with English leakage)</label>
              <textarea
                value={asmTestSample}
                onChange={(e) => setAsmTestSample(e.target.value)}
                rows={3}
                className="w-full text-sm font-mono px-3 py-2 rounded-lg border border-gray-200 focus:border-violet-300 focus:ring-1 focus:ring-violet-200 outline-none mb-3"
                data-testid="input-asm-sample"
              />

              <button
                onClick={fireAsmTest}
                disabled={asmTesting || !asmTestSample.trim()}
                className="px-4 py-2 rounded-lg bg-emerald-600 text-white text-xs font-semibold shadow-sm hover:bg-emerald-700 disabled:opacity-50"
                data-testid="button-fire-asm"
              >
                {asmTesting ? 'Running…' : 'Fire test'}
              </button>

              {asmTestResult && (
                <div className="mt-4 space-y-3" data-testid="asm-test-result">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div className="rounded-xl border border-gray-200 p-3 bg-gray-50">
                      <p className="text-[10px] uppercase tracking-wider text-gray-500 font-bold mb-2">Raw input</p>
                      <p className="text-xs text-gray-800 font-mono whitespace-pre-wrap break-words">{asmTestResult.raw}</p>
                    </div>
                    <div className="rounded-xl border border-emerald-200 p-3 bg-emerald-50">
                      <p className="text-[10px] uppercase tracking-wider text-emerald-700 font-bold mb-2">Cleaned output</p>
                      <p className="text-xs text-gray-800 font-mono whitespace-pre-wrap break-words">{asmTestResult.cleaned}</p>
                    </div>
                  </div>
                  <div className="rounded-xl border border-gray-200 p-3 bg-white">
                    <p className="text-[10px] uppercase tracking-wider text-gray-500 font-bold mb-2">Diagnostic</p>
                    <pre className="text-[11px] font-mono text-gray-700 overflow-x-auto">{JSON.stringify(asmTestResult.diag, null, 2)}</pre>
                  </div>
                </div>
              )}
            </div>
            </SectionErrorBoundary>

            {/* Task #424 — append-only audit trail of override edits.
                Read-only here; writes happen via PATCH/DELETE handlers.
                Task #430 — filter by admin email + date range, paginate. */}
            <SectionErrorBoundary name="ASM Audit Log">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm" data-testid="asm-audit-card">
              <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <Clock size={16} className="text-gray-500" />
                    Recent override changes
                  </h3>
                  <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                    Append-only log of who edited the Sarvam purity override and what changed.
                    {' '}{(() => {
                      const total = asmAudit?.total ?? 0;
                      const off = asmAudit?.offset ?? 0;
                      const shown = asmAudit?.entries?.length ?? 0;
                      if (!shown) return `0 entries match the current filters.`;
                      return `Showing ${off + 1}–${off + shown} of ${total} (newest first).`;
                    })()}
                  </p>
                </div>
                <button
                  onClick={() => loadAsmAudit({
                    offset: asmAudit?.offset ?? asmAuditOffset,
                    filters: asmAuditFilters,
                  })}
                  disabled={asmAuditLoading}
                  className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100"
                  data-testid="button-refresh-asm-audit"
                  title="Refresh audit log"
                >
                  <RefreshCw size={14} className={asmAuditLoading ? 'animate-spin' : ''} />
                </button>
              </div>

              {/* Task #430 — filter row. Apply on submit so each keystroke
                  doesn't fire a request; Reset clears every filter and the
                  paging cursor in one click. */}
              <form
                className="grid grid-cols-1 sm:grid-cols-4 gap-2 mb-4"
                onSubmit={(e) => {
                  e.preventDefault();
                  setAsmAuditOffset(0);
                  loadAsmAudit({ offset: 0, filters: asmAuditFilters });
                }}
                data-testid="asm-audit-filters"
              >
                <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-gray-500">
                  Admin email
                  <input
                    type="text"
                    value={asmAuditFilters.admin_email}
                    onChange={(e) => setAsmAuditFilters((f) => ({ ...f, admin_email: e.target.value }))}
                    placeholder="ops@syrabit.ai"
                    className="px-3 py-1.5 rounded-lg border border-gray-200 text-xs font-mono text-gray-700 focus:outline-none focus:border-violet-300"
                    data-testid="input-asm-audit-email"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-gray-500">
                  From
                  <input
                    type="datetime-local"
                    value={asmAuditFilters.since}
                    onChange={(e) => setAsmAuditFilters((f) => ({ ...f, since: e.target.value }))}
                    className="px-3 py-1.5 rounded-lg border border-gray-200 text-xs font-mono text-gray-700 focus:outline-none focus:border-violet-300"
                    data-testid="input-asm-audit-since"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-gray-500">
                  To
                  <input
                    type="datetime-local"
                    value={asmAuditFilters.until}
                    onChange={(e) => setAsmAuditFilters((f) => ({ ...f, until: e.target.value }))}
                    className="px-3 py-1.5 rounded-lg border border-gray-200 text-xs font-mono text-gray-700 focus:outline-none focus:border-violet-300"
                    data-testid="input-asm-audit-until"
                  />
                </label>
                <div className="flex items-end gap-2">
                  <button
                    type="submit"
                    disabled={asmAuditLoading}
                    className="flex-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
                    data-testid="button-apply-asm-audit"
                  >
                    Apply
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const cleared = { admin_email: '', since: '', until: '' };
                      setAsmAuditFilters(cleared);
                      setAsmAuditOffset(0);
                      loadAsmAudit({ offset: 0, filters: cleared });
                    }}
                    disabled={asmAuditLoading}
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-gray-200 text-gray-500 hover:text-gray-700 hover:bg-gray-50"
                    data-testid="button-reset-asm-audit"
                  >
                    Reset
                  </button>
                </div>
              </form>

              {asmAudit && asmAudit.ok === false && (
                <div className="mb-3 p-3 rounded-xl bg-red-50 border border-red-200 flex items-start gap-2" data-testid="asm-audit-error">
                  <AlertTriangle size={14} className="text-red-500 mt-0.5 flex-shrink-0" />
                  <div className="text-[11px] text-red-700 leading-relaxed">
                    <span className="font-semibold">Audit log unavailable.</span>{' '}
                    {asmAudit.error || 'Mongo read failed — see api logs.'}
                  </div>
                </div>
              )}

              {asmAuditLoading && !asmAudit ? (
                <div className="flex justify-center py-10"><RefreshCw size={20} className="animate-spin text-gray-300" /></div>
              ) : asmAudit?.entries?.length ? (
                <div className="overflow-x-auto" data-testid="asm-audit-table">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-[10px] uppercase tracking-wider text-gray-500 border-b border-gray-100">
                        <th className="py-2 pr-3 font-bold">When</th>
                        <th className="py-2 pr-3 font-bold">Action</th>
                        <th className="py-2 pr-3 font-bold">Admin</th>
                        <th className="py-2 pr-3 font-bold">Before</th>
                        <th className="py-2 pr-3 font-bold">After</th>
                        <th className="py-2 font-bold text-right">Revert</th>
                      </tr>
                    </thead>
                    <tbody>
                      {asmAudit.entries.map((row, idx) => {
                        const fmtSide = (side) => {
                          if (!side) return <span className="text-gray-400">—</span>;
                          const beh = side.behaviour;
                          const thr = side.threshold;
                          return (
                            <span className="font-mono text-[11px] text-gray-700">
                              {beh != null ? beh : '·'} / {thr != null ? Number(thr).toFixed(3) : '·'}
                            </span>
                          );
                        };
                        return (
                          <tr key={idx} className="border-b border-gray-50 hover:bg-gray-50" data-testid={`asm-audit-row-${idx}`}>
                            <td className="py-2 pr-3 text-gray-500 font-mono text-[11px] whitespace-nowrap">
                              {row.ts ? new Date(row.ts).toLocaleString() : '—'}
                            </td>
                            <td className="py-2 pr-3">
                              <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                                row.action === 'delete'
                                  ? 'bg-red-50 text-red-600 border border-red-200'
                                  : row.action === 'revert'
                                  ? 'bg-amber-50 text-amber-700 border border-amber-200'
                                  : 'bg-violet-50 text-violet-600 border border-violet-200'
                              }`}>
                                {row.action || '—'}
                              </span>
                            </td>
                            <td className="py-2 pr-3 text-gray-700 truncate max-w-[180px]" title={row.admin_email || row.admin_id || ''}>
                              {row.admin_email || row.admin_id || <span className="text-gray-400">unknown</span>}
                            </td>
                            <td className="py-2 pr-3">{fmtSide(row.before)}</td>
                            <td className="py-2 pr-3">{fmtSide(row.after)}</td>
                            <td className="py-2 text-right">
                              {row.action === 'revert' ? (
                                <span
                                  className="text-[10px] text-gray-400"
                                  title={row.source_audit_id ? `Reverted from ${row.source_audit_id}` : ''}
                                >
                                  ↩ revert
                                </span>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => revertAsmAuditRow(row)}
                                  disabled={!row.id || asmRevertingId === row.id}
                                  title={row.id ? 'Re-apply this row\'s before-state' : 'No id — predates revert support'}
                                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-amber-200 bg-amber-50 text-amber-700 text-[11px] font-semibold hover:bg-amber-100 disabled:opacity-40 disabled:cursor-not-allowed"
                                  data-testid={`button-revert-asm-audit-${idx}`}
                                >
                                  <Undo2 size={11} className={asmRevertingId === row.id ? 'animate-spin' : ''} />
                                  {asmRevertingId === row.id ? 'Reverting…' : 'Revert'}
                                </button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  <p className="text-[10px] text-gray-400 mt-3 leading-relaxed">
                    Format is <code className="font-mono">behaviour / threshold</code>. Dash means the field was unset. Audit rows are append-only and persist across mongo restarts.
                  </p>
                </div>
              ) : (
                <p className="text-xs text-gray-400 py-6 text-center" data-testid="asm-audit-empty">
                  {(asmAudit?.total ?? 0) > 0
                    ? 'No entries on this page — try Prev or relax the filters.'
                    : 'No override edits match. The first PATCH or DELETE on this tab will appear here.'}
                </p>
              )}

              {/* Task #430 — pagination. Prev/Next operate on the current
                  offset; the backend reports total so we can disable Next
                  when we've shown the last page. */}
              {(asmAudit?.total ?? 0) > ASM_AUDIT_PAGE && (
                <div className="flex items-center justify-between gap-3 mt-4 pt-3 border-t border-gray-100" data-testid="asm-audit-pager">
                  <p className="text-[11px] text-gray-400 font-mono">
                    Page {Math.floor((asmAudit?.offset ?? 0) / ASM_AUDIT_PAGE) + 1}
                    {' / '}
                    {Math.max(1, Math.ceil((asmAudit?.total ?? 0) / ASM_AUDIT_PAGE))}
                  </p>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        const next = Math.max(0, (asmAudit?.offset ?? 0) - ASM_AUDIT_PAGE);
                        setAsmAuditOffset(next);
                        loadAsmAudit({ offset: next, filters: asmAuditFilters });
                      }}
                      disabled={asmAuditLoading || (asmAudit?.offset ?? 0) <= 0}
                      className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                      data-testid="button-asm-audit-prev"
                    >
                      ← Prev
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        const next = (asmAudit?.offset ?? 0) + ASM_AUDIT_PAGE;
                        setAsmAuditOffset(next);
                        loadAsmAudit({ offset: next, filters: asmAuditFilters });
                      }}
                      disabled={
                        asmAuditLoading ||
                        ((asmAudit?.offset ?? 0) + (asmAudit?.entries?.length ?? 0)) >= (asmAudit?.total ?? 0)
                      }
                      className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                      data-testid="button-asm-audit-next"
                    >
                      Next →
                    </button>
                  </div>
                </div>
              )}
            </div>
            </SectionErrorBoundary>

            {/* Task #441 — side-by-side revert preview. Renders the
                currently persisted override against the source row's
                `before` snapshot so an admin can confirm provenance
                before re-applying an old value. */}
            {asmRevertPreview && (
              <SectionErrorBoundary name="ASM Revert Preview">
                {(() => {
              const row = asmRevertPreview;
              const current = asmCfg?.persisted || null;
              const target = row.before || null;
              const reverting = asmRevertingId === row.id;
              const fmtVal = (v, digits = 3) =>
                v == null || v === ''
                  ? <span className="text-gray-400">·</span>
                  : <span className="font-mono text-gray-800">{typeof v === 'number' ? v.toFixed(digits) : v}</span>;
              const Side = ({ heading, accent, snapshot, footer }) => (
                <div className={`flex-1 rounded-xl border ${accent} p-4 min-w-0`}>
                  <p className="text-[10px] uppercase tracking-wider font-bold text-gray-500 mb-3">{heading}</p>
                  <dl className="space-y-2 text-xs">
                    <div className="flex justify-between gap-3">
                      <dt className="text-gray-500">Behaviour</dt>
                      <dd>{fmtVal(snapshot?.behaviour, 0)}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-gray-500">Threshold</dt>
                      <dd>{fmtVal(snapshot?.threshold)}</dd>
                    </div>
                  </dl>
                  {footer && <div className="mt-3 pt-3 border-t border-gray-100 text-[10px] text-gray-500 leading-relaxed">{footer}</div>}
                </div>
              );
              return (
                <div
                  className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
                  role="dialog"
                  aria-modal="true"
                  aria-labelledby="asm-revert-modal-title"
                  onClick={() => { if (!reverting) setAsmRevertPreview(null); }}
                  data-testid="asm-revert-modal"
                >
                  <div
                    className="bg-white rounded-2xl shadow-2xl border border-gray-200 max-w-2xl w-full p-6"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div className="flex items-start gap-3 mb-4">
                      <div className="w-9 h-9 rounded-full bg-amber-50 border border-amber-200 flex items-center justify-center flex-shrink-0">
                        <Undo2 size={16} className="text-amber-600" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <h3 id="asm-revert-modal-title" className="text-base font-semibold text-gray-900">Revert Sarvam purity?</h3>
                        <p className="text-xs text-gray-500 mt-0.5 leading-relaxed">
                          Compare the live override with the snapshot you're about to re-apply. This action is logged as a new <code className="font-mono">revert</code> row.
                        </p>
                      </div>
                    </div>

                    <div className="flex flex-col sm:flex-row gap-3 mb-4">
                      <Side
                        heading="Current (live)"
                        accent="bg-gray-50 border-gray-200"
                        snapshot={current}
                        footer={
                          current?.updated_at
                            ? <>Last edit by <span className="font-mono text-gray-700">{current.updated_by || 'admin'}</span> · {new Date(current.updated_at).toLocaleString()}</>
                            : <span className="text-gray-400">No persisted override (env vars in effect).</span>
                        }
                      />
                      <div className="hidden sm:flex items-center text-gray-300 text-xl px-1" aria-hidden="true">→</div>
                      <Side
                        heading="Target (revert to)"
                        accent="bg-amber-50 border-amber-200"
                        snapshot={target}
                        footer={
                          <>
                            Source row by <span className="font-mono text-gray-700">{row.admin_email || row.admin_id || 'unknown'}</span>
                            {row.ts && <> · {new Date(row.ts).toLocaleString()}</>}
                            {!target && <div className="mt-1 text-amber-700">Snapshot is empty — this will clear the override.</div>}
                          </>
                        }
                      />
                    </div>

                    <div className="flex items-center justify-end gap-2 pt-3 border-t border-gray-100">
                      <button
                        type="button"
                        onClick={() => setAsmRevertPreview(null)}
                        disabled={reverting}
                        className="px-4 py-2 rounded-lg border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50 disabled:opacity-40"
                        data-testid="button-asm-revert-cancel"
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={confirmAsmRevert}
                        disabled={reverting}
                        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-amber-500 text-white text-xs font-semibold hover:bg-amber-600 disabled:opacity-40"
                        data-testid="button-asm-revert-confirm"
                      >
                        <Undo2 size={12} className={reverting ? 'animate-spin' : ''} />
                        {reverting ? 'Reverting…' : 'Confirm revert'}
                      </button>
                    </div>
                  </div>
                </div>
              );
                })()}
              </SectionErrorBoundary>
            )}
          </div>
          </SectionErrorBoundary>
        )}

        {healthTab === 'workers-ai' && (
          <SectionErrorBoundary name="Workers AI Fallback">
            <div className="space-y-4">

              {/* Task #297 — locked provider chain surfacing.
                  Reads /admin/routing-config (pools is an ARRAY of
                  {feature, providers, strict_primary_lock}) and renders
                  one card per Task #297 surfaced provider with every pool
                  membership + role/share + credential presence. */}
              <div className="rounded-2xl p-4 border border-emerald-200 bg-emerald-50/40 shadow-sm" data-testid="locked-provider-chain">
                <div className="flex items-center justify-between mb-2">
                  <div>
                    <p className="text-xs font-semibold text-emerald-800">Locked Provider Chain (Task #297)</p>
                    <p className="text-[10px] text-emerald-700/70">Deepgram (speech), workers_ai_indic (IndicTrans2) and MongoDB Atlas (vector_search fallback) — sourced from <code className="font-mono">GET /admin/routing-config</code>.</p>
                  </div>
                  <button
                    onClick={() => {
                      axios.get(`${API_BASE}/admin/routing-config`, {
                        headers: adminHeaders(adminToken), withCredentials: true,
                      })
                        .then((r) => setRoutingConfig(r.data))
                        .catch(() => setRoutingConfig({ _error: true }));
                    }}
                    className="px-2.5 py-1 rounded-md text-[11px] border border-emerald-300 text-emerald-700 hover:bg-emerald-100"
                    data-testid="btn-refresh-routing-config"
                  >↻ Load</button>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-3">
                  {[
                    { key: 'deepgram',         label: 'Deepgram',         note: 'Speech-to-text + voice (primary)' },
                    { key: 'workers_ai_indic', label: 'workers_ai_indic', note: 'IndicTrans2 — Indic translation primary' },
                    { key: 'mongodb_atlas',    label: 'MongoDB Atlas',    note: 'vector_search fallback (weight-0)' },
                  ].map((p) => {
                    const poolsArr = Array.isArray(routingConfig?.pools) ? routingConfig.pools : [];
                    // Every pool this provider participates in (real
                    // feature names from PROVIDER_PRIORITY, not guesses).
                    const memberships = poolsArr
                      .map((pool) => {
                        const slot = pool.providers?.find?.((x) => x.name === p.key);
                        return slot ? { feature: pool.feature, ...slot } : null;
                      })
                      .filter(Boolean);
                    const present = memberships.length > 0;
                    const keyStatus = routingConfig?.key_status?.[p.key];
                    const credConfigured = keyStatus?.configured;
                    const dotColor = routingConfig === null
                      ? 'bg-gray-300'
                      : routingConfig?._error
                        ? 'bg-red-500'
                        : present && credConfigured
                          ? 'bg-emerald-500'
                          : present
                            ? 'bg-amber-400'
                            : 'bg-gray-300';
                    return (
                      <div key={p.key} className="rounded-lg p-3 bg-white border border-emerald-100" data-testid={`provider-card-${p.key}`}>
                        <div className="flex items-center gap-2 mb-1">
                          <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />
                          <span className="text-xs font-semibold text-gray-700">{p.label}</span>
                          {keyStatus && (
                            <span
                              className={`ml-auto text-[9px] font-semibold px-1.5 py-0.5 rounded ${credConfigured ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}
                              title={`source: ${keyStatus.source}`}
                              data-testid={`provider-cred-${p.key}`}
                            >
                              {credConfigured ? 'Configured' : 'Missing'}
                            </span>
                          )}
                        </div>
                        <div className="text-[10px] text-gray-500 mb-1.5">{p.note}</div>
                        {present ? (
                          <div className="space-y-0.5">
                            {memberships.map((m) => (
                              <div key={m.feature} className="text-[11px] font-mono text-gray-600 flex justify-between gap-2">
                                <span>
                                  <span className="text-emerald-700">{m.feature}</span>
                                  <span className="text-gray-400"> · {m.role}</span>
                                </span>
                                <span className="text-gray-500 tabular-nums">{m.share_pct}%</span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-[11px] text-gray-400 italic">
                            {routingConfig === null ? 'Click ↻ Load to fetch' : 'Not present in any pool'}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
                {routingConfig?._error && (
                  <p className="mt-2 text-[11px] text-red-600">Failed to load routing config — check admin token / api workflow.</p>
                )}
              </div>

              {/* Task #396 — freshness indicator. Backend (cms_sarvam_health.py
                  admin_dashboard_metrics) piggybacks `_meta` on every response
                  so admins can tell which numbers below are seconds-fresh
                  (throttle tiles, recomputed every poll per Task #388) vs
                  cached (heavy users / revenue / SEO / deps block, cached for
                  ~5s per Task #395). Without this strip, recovery from a
                  burst feels indistinguishable from a stale "still throttled"
                  view, and a stale heavy-block could mislead an admin during
                  a deploy. Sits directly above the burst tiles below so the
                  "live" label is visually adjacent to the data it describes. */}
              {metricsMeta && (() => {
                // Shared formatter + thresholds — see
                // src/utils/metricsFreshness.js. Centralised so the
                // AdminDashboard badge (Task #398) renders the exact
                // same wording for the same `heavy_cached_at` value;
                // a tuning change here updates both panels in lockstep.
                const heavyAt = Number(metricsMeta.heavy_cached_at);
                const throttleAt = Number(metricsMeta.throttle_fresh_at);
                const { label: heavyLabel } = computeHeavyFreshness(heavyAt);
                const { label: throttleLabel } = computeThrottleFreshness(throttleAt);
                // The four heavy sections (users, revenue, SEO, deps) all
                // share a single `heavy_cached_at` because they're computed
                // and cached together as one block (see admin_dashboard_metrics
                // cache-miss path in cms_sarvam_health.py). We still list
                // them individually here — matching the task spec example
                // "Throttle: live • Users: 14s ago • Deps: 14s ago" — so
                // each tile on AdminDashboard / AdminHealth has a clearly
                // attributed freshness label, and a future split into
                // per-collection TTLs can wire each label to its own
                // timestamp without changing the visual layout.
                return (
                  <div
                    className="text-[10px] text-gray-400 flex flex-wrap items-center gap-x-2 gap-y-0.5 px-1"
                    data-testid="metrics-freshness"
                    title={`heavy_cached_at=${heavyAt} · throttle_fresh_at=${throttleAt}`}
                  >
                    <span>
                      Throttle:{' '}
                      <span className="text-gray-600 font-medium" data-testid="metrics-freshness-throttle">
                        {throttleLabel}
                      </span>
                    </span>
                    {[
                      { label: 'Users',   testid: 'metrics-freshness-users' },
                      { label: 'Revenue', testid: 'metrics-freshness-revenue' },
                      { label: 'SEO',     testid: 'metrics-freshness-seo' },
                      { label: 'Deps',    testid: 'metrics-freshness-deps' },
                    ].map(({ label, testid }) => (
                      <React.Fragment key={label}>
                        <span aria-hidden="true">•</span>
                        <span>
                          {label}:{' '}
                          <span className="text-gray-600 font-medium" data-testid={testid}>
                            {heavyLabel}
                          </span>
                        </span>
                      </React.Fragment>
                    ))}
                  </div>
                );
              })()}

              {/* Tasks #85/#90/#374/#378 — reusable burst gauge for any provider.
                  ``assamese-chat`` reuses the same shape but counts
                  ``assamese_unavailable`` events (both rails red) instead of
                  429s — see backend ``record_assamese_unavailable``.
                  Azure OpenAI and Deepgram added per Task #378 so admins
                  can spot a building burst before on-call is paged. */}
              {[
                { key: 'workers-ai',    label: 'Workers AI',                thr: waiThrottle,         unit: '429s' },
                { key: 'gemini',        label: 'Gemini',                    thr: geminiThrottle,      unit: '429s' },
                { key: 'azure-openai',  label: 'Azure OpenAI',              thr: azureOpenaiThrottle, unit: '429s' },
                { key: 'deepgram',      label: 'Deepgram',                  thr: deepgramThrottle,    unit: '429s' },
                { key: 'assamese-chat', label: 'Assamese Chat (both rails)', thr: assameseUnavailable, unit: 'outage events' },
              ].map(({ key, label, thr: _thr, unit }) => (
                <div key={key}>
                  {(() => {
                    const thr = _thr;
                    const isLoading = thr === null;
                    const burst60 = thr?.burst_60s ?? 0;
                    const burst180 = thr?.burst_180s ?? 0;
                    const limit = thr?.alert_threshold ?? 5;
                    const throttled = !isLoading && (thr?.throttled ?? false);
                    const approaching = !isLoading && !throttled && burst60 >= Math.ceil(limit * 0.6);
                    const dotColor = isLoading ? 'bg-gray-300' : throttled ? 'bg-red-500' : approaching ? 'bg-amber-400' : 'bg-emerald-500';
                    const statusLabel = isLoading ? 'Loading\u2026' : throttled ? 'Throttled' : approaching ? 'Approaching' : 'OK';
                    const statusText = isLoading ? 'text-gray-400' : throttled ? 'text-red-600' : approaching ? 'text-amber-600' : 'text-emerald-600';
                    return (
                      <div className={`rounded-2xl p-4 border shadow-sm ${throttled ? 'bg-red-50 border-red-200' : approaching ? 'bg-amber-50 border-amber-200' : 'bg-white border-gray-200'}`}>
                        {throttled && (
                          <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-red-100 border border-red-200">
                            <AlertTriangle size={14} className="text-red-600 shrink-0" />
                            <span className="text-xs font-semibold text-red-700">
                              {key === 'assamese-chat'
                                ? `${label} — both rails red: ${burst180} ${unit} in the last 180 s (threshold: ${limit})`
                                : `${label} is throttled — ${burst60} ${unit} in the last 60 s (threshold: ${limit})`}
                            </span>
                          </div>
                        )}
                        <div className="flex items-center gap-2">
                          <Zap size={14} className={isLoading ? 'text-gray-300' : throttled ? 'text-red-500' : approaching ? 'text-amber-500' : 'text-gray-400'} />
                          <span className="text-xs font-semibold text-gray-700">
                            {key === 'assamese-chat'
                              ? `${label} — Outage Burst Pressure`
                              : `${label} — 429 Burst Pressure`}
                          </span>
                          <span className={`flex items-center gap-1 text-[11px] font-semibold ${statusText}`}>
                            <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />
                            {statusLabel}
                          </span>
                        </div>
                        {!isLoading && (
                          <div className="mt-3 grid grid-cols-3 gap-3">
                            <div className="rounded-lg p-2.5 bg-white/70 border border-gray-100">
                              <div className="text-[10px] uppercase text-gray-400 font-semibold mb-0.5">60 s (this worker)</div>
                              <div className={`text-base font-bold tabular-nums ${burst60 >= limit ? 'text-red-600' : burst60 >= Math.ceil(limit * 0.6) ? 'text-amber-600' : 'text-emerald-600'}`}>
                                {burst60}<span className="text-xs font-normal text-gray-400"> / {limit}</span>
                              </div>
                            </div>
                            <div className="rounded-lg p-2.5 bg-white/70 border border-gray-100">
                              <div className="text-[10px] uppercase text-gray-400 font-semibold mb-0.5">180 s (all workers)</div>
                              <div className={`text-base font-bold tabular-nums ${key === 'assamese-chat' && burst180 >= limit ? 'text-red-600' : 'text-gray-700'}`}>{burst180}</div>
                            </div>
                            <div className="rounded-lg p-2.5 bg-white/70 border border-gray-100">
                              <div className="text-[10px] uppercase text-gray-400 font-semibold mb-0.5">Alert threshold</div>
                              <div className="text-base font-bold tabular-nums text-gray-700">{limit}</div>
                            </div>
                          </div>
                        )}
                        {/* Task #379 — recent outage events for the Assamese
                            tile only. The other burst tiles aggregate
                            generic 429s where per-event detail isn't
                            useful; the Assamese rail is a P0 outage where
                            knowing *which leg* failed first speeds triage. */}
                        {key === 'assamese-chat' && !isLoading && (
                          <div className="mt-3 pt-3 border-t border-gray-100">
                            <button
                              type="button"
                              onClick={() => setAssameseRecentExpanded(v => !v)}
                              className="flex items-center justify-between w-full text-[11px] font-semibold text-gray-600 hover:text-gray-900"
                              aria-expanded={assameseRecentExpanded}
                              data-testid="assamese-recent-toggle"
                            >
                              <span>
                                Recent outage events
                                <span className="ml-1 text-gray-400 font-normal">
                                  ({(thr?.recent ?? []).length})
                                </span>
                              </span>
                              <span className="text-gray-400">{assameseRecentExpanded ? '▾' : '▸'}</span>
                            </button>
                            {assameseRecentExpanded && (
                              <div className="mt-2" data-testid="assamese-recent-list">
                                {(thr?.recent ?? []).length === 0 ? (
                                  <p className="text-[11px] text-gray-400 italic py-2">
                                    No recent outage events recorded — the rail has been calm for the last 180 s.
                                  </p>
                                ) : (
                                  <ul className="space-y-1.5">
                                    {(thr?.recent ?? []).slice(0, 5).map((ev, idx) => {
                                      const ts = typeof ev?.ts === 'number'
                                        ? new Date(ev.ts * 1000)
                                        : null;
                                      const tsStr = ts && !isNaN(ts.getTime())
                                        ? ts.toLocaleTimeString([], { hour12: false })
                                        : '—';
                                      const legLabels = {
                                        sarvam_vertex_chain: 'Sarvam → Vertex/Gemini',
                                        workers_ai_unavailable: 'Workers-AI Phase-2 unavailable',
                                        workers_ai_phase2: 'Workers-AI Phase-2 errored',
                                      };
                                      const legLabel = legLabels[ev?.failing_leg] || (ev?.failing_leg || 'unknown');
                                      const errSummary = (ev?.error_summary || '').trim();
                                      const convHash = (ev?.conversation_id_hash || '').trim();
                                      return (
                                        <li
                                          key={`${ev?.ts ?? 'na'}-${idx}`}
                                          className="rounded-md px-2.5 py-1.5 bg-white/80 border border-gray-100 text-[11px]"
                                          data-testid="assamese-recent-event"
                                        >
                                          <div className="flex items-baseline gap-2">
                                            <span className="font-mono text-gray-500 tabular-nums shrink-0">{tsStr}</span>
                                            <span className="font-semibold text-gray-700">{legLabel}</span>
                                            {convHash && (
                                              <span className="ml-auto text-[10px] text-gray-400 font-mono">conv {convHash}</span>
                                            )}
                                          </div>
                                          {errSummary && (
                                            <div className="mt-0.5 text-[10.5px] text-gray-500 break-words" title={errSummary}>
                                              {errSummary.length > 140 ? `${errSummary.slice(0, 139)}…` : errSummary}
                                            </div>
                                          )}
                                        </li>
                                      );
                                    })}
                                  </ul>
                                )}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                </div>
              ))}

              <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
                <div className="flex items-start justify-between mb-4">
                  <div>
                    <h3 className="text-sm font-semibold text-gray-900">Workers AI Fallback</h3>
                    <p className="text-xs text-gray-500 mt-1">
                      Cloudflare Workers AI auto-fallback for chat / embed / TTS / STT.
                      Activates only after the primary provider fails with a retryable
                      error (timeout / 5xx / 429 / quota). 4xx bad-input failures
                      always surface to the caller.
                    </p>
                  </div>
                  <button onClick={loadWorkersAi}
                    className="px-3 py-1.5 rounded-lg text-xs border border-gray-200 text-gray-500 hover:text-gray-700">
                    ↻ Refresh
                  </button>
                </div>

                {!waiStatus ? (
                  <div className="text-xs text-gray-400 py-4">Loading…</div>
                ) : !waiStatus.ok ? (
                  <div className="text-xs text-red-500 py-4">
                    Status unavailable: {waiStatus.error || 'unknown'}
                  </div>
                ) : (
                  <>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
                      <div className="rounded-lg p-3 bg-gray-50 border border-gray-100">
                        <div className="text-[10px] uppercase text-gray-400 font-semibold">Master switch</div>
                        <div className={`text-sm font-semibold ${waiStatus.enabled_globally ? 'text-emerald-600' : 'text-gray-400'}`}>
                          {waiStatus.enabled_globally ? 'Enabled' : 'Disabled'}
                        </div>
                      </div>
                      <div className="rounded-lg p-3 bg-gray-50 border border-gray-100">
                        <div className="text-[10px] uppercase text-gray-400 font-semibold">Shared secret</div>
                        <div className={`text-sm font-semibold ${waiStatus.secret_configured ? 'text-emerald-600' : 'text-red-500'}`}>
                          {waiStatus.secret_configured ? 'Configured' : 'Missing'}
                        </div>
                      </div>
                      <div className="rounded-lg p-3 bg-gray-50 border border-gray-100 col-span-2 sm:col-span-1">
                        <div className="text-[10px] uppercase text-gray-400 font-semibold">Edge URL</div>
                        <div className="text-xs font-mono text-gray-600 truncate" title={waiStatus.edge_url}>
                          {waiStatus.edge_url || '—'}
                        </div>
                      </div>
                    </div>

                    {/* Task #96 — Workers AI embed 429 cooldown indicator */}
                    {(() => {
                      const eb = embedBurst;
                      const isLoading     = eb === null;
                      const isUnavailable = eb === false;
                      if (isUnavailable) return (
                        <div className="rounded-2xl p-4 border border-gray-200 bg-white shadow-sm mb-4">
                          <div className="flex items-center gap-2">
                            <Zap size={14} className="text-gray-300" />
                            <span className="text-xs font-semibold text-gray-700">Workers AI — Embed 429 Cooldown</span>
                            <span className="flex items-center gap-1 text-[11px] font-semibold text-gray-400">
                              <span className="inline-block w-2 h-2 rounded-full bg-gray-300" />
                              Unavailable
                            </span>
                          </div>
                          <p className="mt-2 text-[11px] text-gray-400">Pool stats endpoint unreachable — retry will happen in 30 s.</p>
                        </div>
                      );
                      const burst      = eb?.burst ?? 0;
                      const cooldown   = eb?.cooldown ?? false;
                      const remainingS = eb?.remainingS ?? 0;
                      const threshold  = eb?.threshold ?? 3;
                      const durationS  = eb?.durationS ?? 60;
                      const approaching = !isLoading && !cooldown && burst >= Math.ceil(threshold * 0.6);
                      const dotColor = isLoading
                        ? 'bg-gray-300'
                        : cooldown
                        ? 'bg-red-500'
                        : approaching
                        ? 'bg-amber-400'
                        : 'bg-emerald-500';
                      const statusLabel = isLoading
                        ? 'Loading\u2026'
                        : cooldown ? 'Cooldown Active' : approaching ? 'Approaching' : 'OK';
                      const statusText = isLoading
                        ? 'text-gray-400'
                        : cooldown
                        ? 'text-red-600'
                        : approaching
                        ? 'text-amber-600'
                        : 'text-emerald-600';
                      return (
                        <div className={`rounded-2xl p-4 border shadow-sm mb-4 ${
                          cooldown
                            ? 'bg-red-50 border-red-200'
                            : approaching
                            ? 'bg-amber-50 border-amber-200'
                            : 'bg-white border-gray-200'
                        }`}>
                          {cooldown && (
                            <div className={`flex items-center gap-2 mb-3 px-3 py-2 rounded-lg border transition-colors ${
                              embedCooldownDisplay <= 5
                                ? 'bg-amber-100 border-amber-300'
                                : 'bg-red-100 border-red-200'
                            }`}>
                              <AlertTriangle size={14} className={`shrink-0 transition-colors ${embedCooldownDisplay <= 5 ? 'text-amber-600' : 'text-red-600'}`} />
                              <span className={`text-xs font-semibold transition-colors ${embedCooldownDisplay <= 5 ? 'text-amber-700' : 'text-red-700'}`}>
                                Embed cooldown active — Workers AI embed skipped for {embedCooldownDisplay}s
                                ({burst} of {threshold} hits in last {durationS}s)
                              </span>
                            </div>
                          )}
                          {approaching && !cooldown && (
                            <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-amber-100 border border-amber-200">
                              <AlertTriangle size={14} className="text-amber-600 shrink-0" />
                              <span className="text-xs font-semibold text-amber-700">
                                Approaching cooldown — {burst}/{threshold} embed 429s recorded
                              </span>
                            </div>
                          )}
                          <div className="flex items-center gap-2">
                            <Zap size={14} className={isLoading ? 'text-gray-300' : cooldown ? 'text-red-500' : approaching ? 'text-amber-500' : 'text-gray-400'} />
                            <span className="text-xs font-semibold text-gray-700">Workers AI — Embed 429 Cooldown</span>
                            <span className={`flex items-center gap-1 text-[11px] font-semibold ${statusText}`}>
                              <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />
                              {statusLabel}
                            </span>
                          </div>
                          {!isLoading && (
                            <div className="mt-3 grid grid-cols-3 gap-3">
                              <div className="rounded-lg p-2.5 bg-white/70 border border-gray-100">
                                <div className="text-[10px] uppercase text-gray-400 font-semibold mb-0.5">429 hits (60 s)</div>
                                <div className={`text-base font-bold tabular-nums ${burst >= threshold ? 'text-red-600' : burst >= Math.ceil(threshold * 0.6) ? 'text-amber-600' : 'text-emerald-600'}`}>
                                  {burst}
                                  <span className="text-xs font-normal text-gray-400"> / {threshold}</span>
                                </div>
                              </div>
                              {/* Two-stage urgency: red+pulse (6-10s), amber bg + orange text (0-5s) */}
                              <div className={`rounded-lg p-2.5 border transition-colors ${
                                cooldown && embedCooldownDisplay <= 5
                                  ? 'bg-amber-50 border-amber-300'
                                  : 'bg-white/70 border-gray-100'
                              }`}>
                                <div className={`text-[10px] uppercase font-semibold mb-0.5 ${
                                  cooldown && embedCooldownDisplay <= 5 ? 'text-amber-600' : 'text-gray-400'
                                }`}>Cooldown clears in</div>
                                <div className={`text-base font-bold tabular-nums ${
                                  cooldown && embedCooldownDisplay <= 5
                                    ? 'text-orange-500 animate-pulse'
                                    : cooldown && embedCooldownDisplay <= 10
                                    ? 'text-red-600 animate-pulse'
                                    : cooldown
                                    ? 'text-red-600'
                                    : 'text-gray-400'
                                }`}>
                                  {cooldown ? `${embedCooldownDisplay} s` : '—'}
                                </div>
                              </div>
                              <div className="rounded-lg p-2.5 bg-white/70 border border-gray-100">
                                <div className="text-[10px] uppercase text-gray-400 font-semibold mb-0.5">Skip window</div>
                                <div className="text-base font-bold tabular-nums text-gray-700">{durationS} s</div>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })()}

                    <div className="overflow-hidden rounded-xl border border-gray-100">
                      <table className="w-full text-xs">
                        <thead className="bg-gray-50 text-gray-500">
                          <tr>
                            <th className="text-left px-3 py-2 font-semibold">Capability</th>
                            <th className="text-left px-3 py-2 font-semibold">Last fallback</th>
                            <th className="text-left px-3 py-2 font-semibold">24h ok / fail</th>
                            <th className="text-left px-3 py-2 font-semibold">Last reason</th>
                            <th className="text-right px-3 py-2 font-semibold">Action</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(waiStatus.capabilities || {}).map(([cap, c]) => (
                            <tr key={cap} className="border-t border-gray-100">
                              <td className="px-3 py-2 font-mono text-gray-700">{cap}</td>
                              <td className="px-3 py-2 text-gray-500">
                                {c.last_fallback_at
                                  ? new Date(c.last_fallback_at * 1000).toLocaleString()
                                  : <span className="text-gray-300">never</span>}
                              </td>
                              <td className="px-3 py-2">
                                <span className="text-emerald-600 font-semibold">{c.successes_24h ?? 0}</span>
                                <span className="text-gray-300"> / </span>
                                <span className="text-red-500 font-semibold">{c.failures_24h ?? 0}</span>
                              </td>
                              <td className="px-3 py-2 text-gray-500 font-mono">
                                {c.last_primary_error || <span className="text-gray-300">—</span>}
                              </td>
                              <td className="px-3 py-2 text-right">
                                <button
                                  onClick={() => toggleWorkersAi(cap, !c.enabled)}
                                  disabled={waiToggling === cap}
                                  className={`px-3 py-1 rounded-md text-[11px] font-semibold ${
                                    c.enabled
                                      ? 'bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100'
                                      : 'bg-gray-100 text-gray-500 border border-gray-200 hover:bg-gray-200'
                                  } disabled:opacity-50`}
                                >
                                  {waiToggling === cap ? '…' : (c.enabled ? 'Enabled' : 'Disabled')}
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </div>
            </div>
          </SectionErrorBoundary>
        )}

        {healthTab === 'infra' && (<>
        {/*
          Phase 4 — Task #332. AWS workers + Azure cron tiles render
          first inside the Infrastructure tab so any in-progress
          incident on the migrated tiers is visible above the older
          (GCP-era) panels without scrolling.
        */}
        <SectionErrorBoundary name="AWS Infra (SQS + Lambda workers)">
          <AdminAwsInfraCard adminToken={adminToken} />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Cron (Azure Container Apps Jobs)">
          <AdminCronJobsCard adminToken={adminToken} />
        </SectionErrorBoundary>

        {/* Task #417 — memory_brain hot-path counters + 24h sparkline. */}
        <SectionErrorBoundary name="Memory Brain hot-path">
          <AdminMemoryBrainTile adminToken={adminToken} />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Azure-native AI features">
          <AdminAzureAiPanel token={adminToken} />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Provider Latency Bench">
        <ProviderLatencyBench
          benchLatest={benchLatest}
          benchLoading={benchLoading}
          loadBenchLatest={loadBenchLatest}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="System Status Banner">
        <div className={`rounded-2xl p-4 flex items-center gap-3 ${
          loading ? 'bg-gray-50 border border-gray-200' : hasError ? 'bg-red-50 border border-red-200' : 'bg-emerald-50 border border-emerald-200'
        }`}>
          {loading ? <Wifi size={20} className="text-gray-400 animate-pulse" /> :
           hasError ? <AlertTriangle size={20} className="text-red-500" /> :
           <ShieldCheck size={20} className="text-emerald-500" />}
          <div className="flex-1">
            <p className={`text-sm font-semibold ${
              loading ? 'text-gray-500' : hasError ? 'text-red-600' : 'text-emerald-600'
            }`}>
              {loading ? 'Running health probes...' : hasError ? 'Degraded — Check Dependencies' : 'All Systems Operational'}
            </p>
            {health && (
              <p className="text-xs text-gray-400 mt-0.5">
                v{health.version || '1.0.0'} · {health.workers} workers · uptime {Math.floor((health.uptime_seconds || 0) / 60)}m
              </p>
            )}
          </div>
          <button onClick={() => { loadHealth(); loadMetrics(); }} className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100" data-testid="button-refresh-health">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Trustpilot JSON-LD Coverage">
        {(() => {
          // Task #750 — pass/fail per URL from the daily verifier
          // (.github/workflows/trustpilot-jsonld-prod.yml). Tile turns
          // red when ANY URL failed the latest scheduled run, so a
          // SERP-star regression surfaces here, not just in CI email.
          const data = tpJsonldReport && !tpJsonldReport._error
            ? tpJsonldReport
            : null;
          const configured = !!data?.configured;
          const report = data?.report || null;
          const failed = report?.failed ?? 0;
          const total = report?.totalUrls ?? (report?.results?.length || 0);
          const tileFailed = configured && report && (failed > 0 || report.ok === false);
          const tileUnknown = !configured || !report;
          const containerCls = tileFailed
            ? 'bg-red-50 border-red-200'
            : tileUnknown
              ? 'bg-gray-50 border-gray-200'
              : 'bg-emerald-50 border-emerald-200';
          const headerColor = tileFailed
            ? 'text-red-600'
            : tileUnknown
              ? 'text-gray-500'
              : 'text-emerald-600';
          let timestampLabel = 'never';
          if (report?.generatedAt) {
            try {
              const ts = new Date(report.generatedAt);
              const diff = Math.max(0, Math.floor((Date.now() - ts.getTime()) / 1000));
              if (diff < 60) timestampLabel = `${diff}s ago`;
              else if (diff < 3600) timestampLabel = `${Math.floor(diff / 60)}m ago`;
              else if (diff < 86400) timestampLabel = `${Math.floor(diff / 3600)}h ago`;
              else timestampLabel = `${Math.floor(diff / 86400)}d ago`;
            } catch { /* keep default */ }
          }
          return (
            <div className={`rounded-2xl p-4 border ${containerCls}`} data-testid="trustpilot-jsonld-tile">
              <div className="flex items-center gap-3 mb-3">
                {tileFailed
                  ? <AlertTriangle size={18} className="text-red-500" />
                  : tileUnknown
                    ? <Star size={18} className="text-gray-400" />
                    : <Star size={18} className="text-emerald-500" />}
                <div className="flex-1 min-w-0">
                  <p className={`text-sm font-semibold ${headerColor}`} data-testid="trustpilot-jsonld-status">
                    {tileUnknown
                      ? 'Trustpilot JSON-LD coverage — no verifier run yet'
                      : tileFailed
                        ? `Trustpilot JSON-LD coverage — ${failed}/${total} URL${failed === 1 ? '' : 's'} failed`
                        : `Trustpilot JSON-LD coverage — all ${total} URL${total === 1 ? '' : 's'} pass`}
                  </p>
                  <p className="text-[11px] text-gray-500 mt-0.5">
                    Last run {timestampLabel}
                    {report?.target ? ` · target=${report.target}` : ''}
                    {report?.origin ? ` · ${report.origin}` : ''}
                  </p>
                </div>
                {/* Task #968 — surface the dedicated Slack fan-out
                    configuration health (SLACK_TRUSTPILOT_WEBHOOK_URL)
                    next to the per-event verifier alerter, mirroring
                    the badge Task #964 added to the three cron pills.
                    Renders nothing when the backend hasn't published
                    the field yet (in-flight rollout safe). */}
                <SlackConfigBadge
                  configured={tpJsonldReport?.slackConfigured}
                  envName={tpJsonldReport?.slackWebhookEnv}
                  testId="trustpilot-jsonld"
                />
                {report?.runUrl && (
                  <a
                    href={report.runUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[11px] text-violet-600 hover:text-violet-700 inline-flex items-center gap-1"
                    data-testid="trustpilot-jsonld-run-link"
                    title="Open the GitHub Actions run that produced this report"
                  >
                    Run <ExternalLink size={11} />
                  </a>
                )}
                <button
                  onClick={loadTpJsonldReport}
                  disabled={tpJsonldLoading}
                  className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                  data-testid="button-refresh-trustpilot-jsonld"
                  title="Refresh"
                >
                  <RefreshCw size={13} className={tpJsonldLoading ? 'animate-spin' : ''} />
                </button>
              </div>
              {(() => {
                // Task #754 — 30-day pass-rate sparkline. Rendered above
                // the per-URL table so ops sees a slow-moving regression
                // (e.g. the line drifting from 100% to 80% over a week)
                // without having to compare table snapshots day to day.
                const points = (tpJsonldHistory?.points || [])
                  .filter((p) => p && p.passRate != null)
                  .map((p) => ({
                    ts: p.ts,
                    label: p.ts ? new Date(p.ts).toLocaleDateString() : '',
                    passRatePct: Math.round((p.passRate ?? 0) * 1000) / 10,
                    avgRating: p.avgRatingValue,
                    passed: p.passed,
                    failed: p.failed,
                    total: p.totalUrls,
                  }));
                if (points.length < 2) return null;
                const passColor = tileFailed ? '#dc2626' : '#10b981';
                return (
                  <div className="mb-3" data-testid="trustpilot-jsonld-sparkline">
                    <div className="flex items-center justify-between mb-1">
                      <p className="text-[10px] uppercase tracking-wider text-gray-500">
                        Pass-rate · last {points.length} run{points.length === 1 ? '' : 's'} (30d TTL)
                      </p>
                      <p className="text-[10px] text-gray-400 font-mono">
                        latest {points[points.length - 1].passRatePct}%
                      </p>
                    </div>
                    <ResponsiveContainer width="100%" height={48}>
                      <LineChart data={points} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
                        <YAxis hide domain={[0, 100]} />
                        <Tooltip
                          contentStyle={TOOLTIP_STYLE}
                          formatter={(v, name) => {
                            if (name === 'passRatePct') return [`${v}%`, 'pass-rate'];
                            return [v, name];
                          }}
                          labelFormatter={(_, payload) => {
                            const p = payload?.[0]?.payload;
                            if (!p) return '';
                            const bits = [p.label];
                            if (p.passed != null && p.total != null) {
                              bits.push(`${p.passed}/${p.total} pass`);
                            }
                            if (p.avgRating != null) {
                              bits.push(`avg ★ ${Number(p.avgRating).toFixed(2)}`);
                            }
                            return bits.join(' · ');
                          }}
                        />
                        <Line
                          type="monotone"
                          dataKey="passRatePct"
                          stroke={passColor}
                          strokeWidth={2}
                          dot={false}
                          isAnimationActive={false}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                    {(() => {
                      // Task #760 — second sparkline: 30-day average
                      // ratingValue trend. Pass-rate alone can't catch a
                      // slow drift in the actual star rating (e.g. 4.7★
                      // → 4.5★ over a fortnight); this chart does.
                      // Points without avgRating (pre-Task-#754 rows or
                      // an all-fail run with no numeric ratings) are
                      // filtered out so the line doesn't fake zeros.
                      const ratingPoints = points.filter(
                        (p) => p.avgRating != null && Number.isFinite(Number(p.avgRating)),
                      ).map((p) => ({
                        ...p,
                        avgRatingNum: Number(p.avgRating),
                      }));
                      if (ratingPoints.length < 2) return null;
                      // Tighten Y domain around the observed range so
                      // sub-0.2★ drift is actually visible on a 48px
                      // chart. Clamped to a sane Trustpilot band.
                      const values = ratingPoints.map((p) => p.avgRatingNum);
                      const minV = Math.max(0, Math.min(...values) - 0.1);
                      const maxV = Math.min(5, Math.max(...values) + 0.1);
                      const latest = ratingPoints[ratingPoints.length - 1].avgRatingNum;
                      return (
                        <div className="mt-2" data-testid="trustpilot-jsonld-rating-sparkline">
                          <div className="flex items-center justify-between mb-1">
                            <p className="text-[10px] uppercase tracking-wider text-gray-500">
                              Avg ratingValue · last {ratingPoints.length} run{ratingPoints.length === 1 ? '' : 's'}
                            </p>
                            <p className="text-[10px] text-gray-400 font-mono">
                              latest ★ {latest.toFixed(2)}
                            </p>
                          </div>
                          <ResponsiveContainer width="100%" height={48}>
                            <LineChart
                              data={ratingPoints}
                              margin={{ top: 2, right: 2, bottom: 2, left: 2 }}
                            >
                              <YAxis hide domain={[minV, maxV]} />
                              <Tooltip
                                contentStyle={TOOLTIP_STYLE}
                                formatter={(v, name) => {
                                  if (name === 'avgRatingNum') {
                                    return [`★ ${Number(v).toFixed(2)}`, 'avg rating'];
                                  }
                                  return [v, name];
                                }}
                                labelFormatter={(_, payload) => {
                                  const p = payload?.[0]?.payload;
                                  if (!p) return '';
                                  const bits = [p.label];
                                  if (p.avgRating != null) {
                                    bits.push(`★ ${Number(p.avgRating).toFixed(2)}`);
                                  }
                                  return bits.join(' · ');
                                }}
                              />
                              <Line
                                type="monotone"
                                dataKey="avgRatingNum"
                                stroke="#f59e0b"
                                strokeWidth={2}
                                dot={false}
                                isAnimationActive={false}
                              />
                            </LineChart>
                          </ResponsiveContainer>
                        </div>
                      );
                    })()}
                  </div>
                );
              })()}
              {(() => {
                // Task #758 — recent regression / recovery / streak
                // alert events. Reads from the notifications the
                // dispatcher already writes, so a flappy URL (alerted,
                // recovered, re-alerted within a week) stands out at a
                // glance — something single-fire email dedup hides.
                const events = tpJsonldAlerts?.events || [];
                if (!events.length) return null;
                const stateStyles = {
                  regression: 'bg-red-50 text-red-700 border-red-200',
                  streak: 'bg-amber-50 text-amber-700 border-amber-200',
                  recovery: 'bg-emerald-50 text-emerald-700 border-emerald-200',
                };
                const stateLabels = {
                  regression: 'REGRESSION',
                  streak: 'STREAK',
                  recovery: 'RECOVERY',
                };
                const fmtAge = (iso) => {
                  if (!iso) return '';
                  const t = new Date(iso).getTime();
                  if (!Number.isFinite(t)) return '';
                  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
                  if (s < 60) return `${s}s ago`;
                  if (s < 3600) return `${Math.round(s / 60)}m ago`;
                  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
                  return `${Math.round(s / 86400)}d ago`;
                };
                return (
                  <div
                    className="mb-3 pt-2 border-t border-gray-100"
                    data-testid="trustpilot-jsonld-alert-history"
                  >
                    <div className="flex items-center justify-between mb-1.5">
                      <p className="text-[10px] uppercase tracking-wider text-gray-500">
                        Recent alerts · last {events.length}
                      </p>
                      <p className="text-[10px] text-gray-400">
                        auto-refreshes · 60s
                      </p>
                    </div>
                    <ul className="space-y-1 max-h-40 overflow-y-auto pr-1">
                      {events.map((e) => (
                        <li
                          key={e.id || `${e.created_at}-${e.title}`}
                          className="flex items-start gap-2 text-[11px] leading-snug"
                          data-testid={`trustpilot-jsonld-alert-${e.state}`}
                        >
                          <span
                            className={`shrink-0 mt-0.5 inline-block px-1.5 py-0.5 rounded border font-bold text-[9px] tracking-wider ${stateStyles[e.state] || stateStyles.regression}`}
                            title={e.state}
                          >
                            {stateLabels[e.state] || e.state?.toUpperCase() || 'ALERT'}
                          </span>
                          <span className="flex-1 min-w-0">
                            <span
                              className="block text-gray-700 truncate"
                              title={e.title}
                            >
                              {e.title}
                            </span>
                            {Array.isArray(e.urls) && e.urls.length > 0 ? (
                              // Render the per-URL bullets backend
                              // parsed out of the alert body so ops can
                              // spot a flappy URL at a glance. Capped
                              // at 5 with a "+N more" suffix so one
                              // giant alert can't push the strip off
                              // screen.
                              <span
                                className="block mt-0.5 text-[10px] font-mono text-gray-600"
                                data-testid={`trustpilot-jsonld-alert-urls-${e.id || e.created_at}`}
                              >
                                {e.urls.slice(0, 5).map((u, i) => (
                                  <span
                                    key={`${u}-${i}`}
                                    className="block truncate"
                                    title={u}
                                  >
                                    · {u}
                                  </span>
                                ))}
                                {e.urls.length > 5 ? (
                                  <span className="block text-gray-400">
                                    · +{e.urls.length - 5} more
                                  </span>
                                ) : null}
                              </span>
                            ) : null}
                            <span className="block text-[10px] text-gray-400 font-mono">
                              {fmtAge(e.created_at)}
                              {e.created_at ? ` · ${new Date(e.created_at).toLocaleString()}` : ''}
                            </span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })()}
              {tileUnknown ? (
                <p className="text-[11px] text-gray-500 leading-relaxed">
                  The daily <code className="font-mono">trustpilot-jsonld-prod</code> workflow will publish per-URL pass/fail here once it runs (06:00 UTC). Until then, treat the build-time inject step as the source of truth.
                </p>
              ) : (
                <div className="overflow-x-auto" data-testid="trustpilot-jsonld-table">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-[10px] uppercase tracking-wider text-gray-500 border-b border-gray-100">
                        <th className="py-1.5 pr-3 font-bold">URL</th>
                        <th className="py-1.5 pr-3 font-bold">HTTP</th>
                        <th className="py-1.5 pr-3 font-bold">Pass</th>
                        <th className="py-1.5 font-bold">Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(report.results || []).map((r, idx) => (
                        <tr key={`${r.url}-${idx}`} className="border-b border-gray-50" data-testid={`trustpilot-jsonld-row-${idx}`}>
                          <td className="py-1.5 pr-3 font-mono text-gray-700">{r.url}</td>
                          <td className="py-1.5 pr-3 font-mono text-gray-500">{r.status ?? '—'}</td>
                          <td className="py-1.5 pr-3">
                            <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold ${
                              r.pass
                                ? 'bg-emerald-50 text-emerald-600 border border-emerald-200'
                                : 'bg-red-50 text-red-600 border border-red-200'
                            }`}>
                              {r.pass ? 'PASS' : 'FAIL'}
                            </span>
                          </td>
                          <td className="py-1.5 text-gray-600 font-mono text-[11px]">
                            {r.pass
                              ? (r.ratingValue != null && r.reviewCount != null
                                  ? `${r.ratingValue}★ · ${r.reviewCount} reviews`
                                  : '—')
                              : (r.reason || 'fail')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })()}
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Trustpilot Refresh Cron">
        {/*
          Task #755 — surface the daily refresh-cron heartbeat next to
          the existing Trustpilot data tile so admins can spot a silent
          cron at a glance instead of waiting for the email. Endpoint
          shape comes from /admin/health/trustpilot/refresh-cron (Task
          #751). Task #835 — the visual pill is the shared
          <CronHealthPill> component. Task #838 — the configuration
          (header text per status, two-line success/any heartbeat
          caption, default workflow URL) was extracted into
          <TrustpilotRefreshCronPill> so its colour mapping and
          dual-heartbeat caption can be unit-tested in isolation
          (see TrustpilotRefreshCronPill.test.jsx). testId moved from
          "trustpilot-cron" to "trustpilot-refresh-cron" to align
          with the cf-waf-drift pill's naming convention.
        */}
        <TrustpilotRefreshCronPill
          data={tpCronHealth}
          loading={tpCronLoading}
          onRefresh={loadTpCronHealth}
          alertState={tpCronAlertState}
          alertHistory={tpCronAlertHistory}
          onLoadAlertHistory={loadTpCronAlertHistory}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Cloudflare WAF Drift Cron">
        {/*
          Task #833 — sibling pill for the daily cf-waf-drift-daily
          workflow heartbeat (Task #831). Same shape as the Trustpilot
          refresh-cron pill above, with one addition: a "Last run"
          deep-link when the heartbeat carries one, since jumping
          straight to the offending GitHub Actions run is the first
          thing an admin wants when the pill turns red. Endpoint:
          /admin/health/cf-waf-drift/cron — status keys mirror the
          Trustpilot endpoint. Task #835 — the visual pill is the
          shared <CronHealthPill> component. Task #836 — the
          configuration was extracted into <CfWafDriftCronPill> so
          its colour mapping, heartbeat-age caption, and conditional
          verify/aggregate-RC text can be unit-tested in isolation
          (see CfWafDriftCronPill.test.jsx).
        */}
        <CfWafDriftCronPill
          data={cfDriftCronHealth}
          loading={cfDriftCronLoading}
          onRefresh={loadCfDriftCronHealth}
          alertState={cfDriftCronAlertState}
          alertHistory={cfDriftCronAlertHistory}
          onLoadAlertHistory={loadCfDriftCronAlertHistory}
          slackMissingAlertState={slackWebhookMissingAlertStates['CF_WAF_DRIFT_SLACK_WEBHOOK']}
          slackMissingAlertHistory={slackWebhookMissingAlertHistories['CF_WAF_DRIFT_SLACK_WEBHOOK']}
          onSnoozeSlackMissing={snoozeSlackWebhookMissing}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Edge-Proxy Deploy CI">
        {/*
          Task #882 — surface the latest `edge-proxy-deploy` GitHub
          Actions run next to the other cron pills. The workflow runs
          unattended on every push to master that touches
          workers/edge-proxy/**; its `smoke-preview` job is the
          canonical signal that the latest worker build still passes
          the burst / D1 / KV / bot-cache checks. A red badge there
          previously only lived in the GitHub Actions UI — this pill
          puts it on the AdminHealth dashboard on-call already
          watches. Endpoint: /admin/health/edge-proxy-deploy/cron.
        */}
        <EdgeProxyDeployCronPill
          data={edgeProxyDeployCronHealth}
          loading={edgeProxyDeployCronLoading}
          onRefresh={loadEdgeProxyDeployCronHealth}
          alertState={edgeProxyDeployCronAlertState}
          alertHistory={edgeProxyDeployCronAlertHistory}
          onLoadAlertHistory={loadEdgeProxyDeployCronAlertHistory}
          slackMissingAlertState={slackWebhookMissingAlertStates['EDGE_PROXY_DEPLOY_SLACK_WEBHOOK']}
          slackMissingAlertHistory={slackWebhookMissingAlertHistories['EDGE_PROXY_DEPLOY_SLACK_WEBHOOK']}
          onSnoozeSlackMissing={snoozeSlackWebhookMissing}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Cloudflare Log Ingest">
        {/*
          Task #956 — surface the unified-logs Cloudflare GraphQL pull
          silence alerter (Task #951) on the AdminHealth dashboard
          alongside the other cron pills. Until this pill shipped, the
          only signal that ingest had stalled was the on-call page or
          the cf_pull_last_run timestamp on /api/admin/logs/status
          quietly growing old. The pill turns red when the lock doc's
          updated_at is older than ~3× the configured pull interval
          (default 5 min floor), shows the lease owner and last
          successful cursor advance inline, and exposes the same
          "last paged Xh ago · in debounce ~Yh" caption + paged
          history disclosure as its siblings.
          Endpoint: /admin/health/unified-logs/cf-pull/cron.
          Tasks #957 / #963 — the alerter pages on three channels
          (in-app + email + Slack), matching the cf-waf-drift /
          edge-proxy-deploy pills. Slack is the third best-effort
          channel and is gated on `UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK`
          being set on the backend; the shared SlackConfigBadge
          inside <CronHealthPill> renders a "Slack ✓ / ✗" indicator
          next to this pill so a deploy-without-Slack-coverage gap is
          visible at a glance. See §8.7.7 of CLOUDFLARE_ZERO_TRUST.md
          for the sibling-webhook table.
        */}
        <UnifiedLogsCfPullCronPill
          data={unifiedLogsCfPullCronHealth}
          loading={unifiedLogsCfPullCronLoading}
          onRefresh={loadUnifiedLogsCfPullCronHealth}
          alertState={unifiedLogsCfPullCronAlertState}
          alertHistory={unifiedLogsCfPullCronAlertHistory}
          onLoadAlertHistory={loadUnifiedLogsCfPullCronAlertHistory}
          slackMissingAlertState={slackWebhookMissingAlertStates['UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK']}
          slackMissingAlertHistory={slackWebhookMissingAlertHistories['UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK']}
          onSnoozeSlackMissing={snoozeSlackWebhookMissing}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Cloudflare Audit Card">
        {/*
          Task #133 — surface the latest cloudflare-weekly-audit.yml run
          (19-item full audit, Phases 1–6) on the admin health panel so
          on-call sees the pass/warn/fail breakdown without navigating
          to GitHub Actions.  The card fetches via
          /admin/health/cf-audit/latest which:
            • queries GitHub API for the latest run conclusion + age,
            • downloads the cf-audit-report artifact ZIP,
            • parses the embedded JSON for PASS/WARN/FAIL/PLAN_REQUIRED counts,
            • caches the summary in Redis per run_id for 4 hours.
          The card turns amber ("stale") when the last run is >8 days old
          and red when conclusion=failure.  Clicking "View run" deep-links
          to the specific GitHub Actions run.
        */}
        <CfAuditCard
          data={cfAuditData}
          loading={cfAuditLoading}
          onRefresh={loadCfAudit}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="AI Gateway Cache by Model">
        {/*
          Task #419 — surface "top models by cache hit ratio" from the
          unified /admin/cf-health snapshot. Until this tile shipped,
          on-call could only see aggregate hit/miss totals on the CF
          panel and had to slice ai_gateway.recent_samples by hand to
          tell whether the cache was actually doing its job for the
          high-volume models (e.g. llama-3.3-70b-instruct-fp8-fast vs
          gpt-oss-120b). A model with no cache telemetry in the window
          renders as "—" rather than 0% so it isn't mistaken for a
          100% miss outlier.
        */}
        <AiGatewayCacheByModelTile
          data={cfHealthData?.ai_gateway}
          loading={cfHealthLoading}
          onRefresh={loadCfHealth}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Signup Throttle">
        {/*
          Task #430 — Task #407 added a per-IP signup rate gate on
          /auth/signup that flows through do_chat.rate_check, but
          blocked attempts only bumped the shared
          do_chat.rate_check_blocked counter. That counter mixes
          chat throttling and signup throttling on the same number,
          so on-call had no way to tell whether a spike was a
          bot-signup wave or normal chat traffic. The backend now
          exposes per-prefix breakdowns
          (rate_check_blocked_by_prefix / rate_check_total_by_prefix)
          and this tile renders the "signup" slice — process-lifetime
          counts that reset on pod restart, the same lifecycle as the
          rest of do_chat's counters.
        */}
        {(() => {
          const doChat = cfHealthData?.do_chat;
          if (!doChat || doChat.error) return null;
          const blockedByPrefix = doChat.rate_check_blocked_by_prefix || {};
          const totalByPrefix = doChat.rate_check_total_by_prefix || {};
          const signupBlocked = blockedByPrefix.signup || 0;
          const signupTotal = totalByPrefix.signup || 0;
          const ratio = signupTotal > 0 ? signupBlocked / signupTotal : 0;
          const tone = signupBlocked > 0 ? 'amber' : 'emerald';
          const colors = tone === 'amber'
            ? { tile: 'bg-amber-50 border-amber-200', icon: 'bg-amber-100 text-amber-500', heading: 'text-amber-600' }
            : { tile: 'bg-emerald-50 border-emerald-200', icon: 'bg-emerald-100 text-emerald-500', heading: 'text-emerald-600' };
          return (
            <div
              className={`rounded-2xl p-4 border ${colors.tile}`}
              data-testid="signup-throttle-tile"
            >
              <div className="flex items-center gap-3 mb-3">
                <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${colors.icon}`}>
                  <ShieldCheck size={17} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className={`text-sm font-semibold ${colors.heading}`}>
                    Signup throttle (do_chat)
                  </p>
                  <p className="text-[11px] text-gray-500 mt-0.5">
                    Per-IP signup rate gate · /auth/signup · Task #407
                  </p>
                </div>
                <button
                  onClick={loadCfHealth}
                  disabled={cfHealthLoading}
                  className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-white"
                  data-testid="button-refresh-signup-throttle"
                >
                  <RefreshCw size={14} className={cfHealthLoading ? 'animate-spin' : ''} />
                </button>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div className="rounded-xl p-3 border border-gray-200 bg-white">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">
                    Blocked signups
                  </p>
                  <p
                    className="text-2xl font-bold font-mono text-gray-900"
                    data-testid="signup-throttle-blocked"
                  >
                    {signupBlocked}
                  </p>
                  <p className="text-[10px] text-gray-400 mt-0.5">
                    since pod start
                  </p>
                </div>
                <div className="rounded-xl p-3 border border-gray-200 bg-white">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">
                    Total checks
                  </p>
                  <p
                    className="text-2xl font-bold font-mono text-gray-900"
                    data-testid="signup-throttle-total"
                  >
                    {signupTotal}
                  </p>
                  <p className="text-[10px] text-gray-400 mt-0.5">
                    signup-scoped only
                  </p>
                </div>
                <div className="rounded-xl p-3 border border-gray-200 bg-white">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">
                    Block ratio
                  </p>
                  <p
                    className="text-2xl font-bold font-mono text-gray-900"
                    data-testid="signup-throttle-ratio"
                  >
                    {signupTotal > 0 ? `${Math.round(ratio * 100)}%` : '—'}
                  </p>
                  <p className="text-[10px] text-gray-400 mt-0.5">
                    blocked ÷ total
                  </p>
                </div>
              </div>
            </div>
          );
        })()}
        </SectionErrorBoundary>

        <SectionErrorBoundary name="GCP Credit Panel">
        {(() => {
          const gc = gcpCredits && !gcpCredits._error ? gcpCredits : null;
          const saConfigured = gc?.service_account_configured ?? false;
          const creditsLow = gc?.credits_low ?? false;
          const liveSpend = gc?.live_spend_data ?? false;
          const liveBudget = gc?.live_budget_data ?? false;
          const isUnconfigured = !gcpCreditsLoading && (!gc || !saConfigured);
          const tileCls = creditsLow
            ? 'bg-red-50 border-red-200'
            : isUnconfigured
              ? 'bg-gray-50 border-gray-200'
              : 'bg-emerald-50 border-emerald-200';
          const headerCls = creditsLow
            ? 'text-red-600'
            : isUnconfigured
              ? 'text-gray-500'
              : 'text-emerald-600';
          return (
            <div className={`rounded-2xl p-4 border ${tileCls}`} data-testid="gcp-credit-panel">
              <div className="flex items-center gap-3 mb-3">
                <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${
                  creditsLow ? 'bg-red-100' : isUnconfigured ? 'bg-gray-100' : 'bg-emerald-100'
                }`}>
                  <DollarSign size={17} className={creditsLow ? 'text-red-500' : isUnconfigured ? 'text-gray-400' : 'text-emerald-500'} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className={`text-sm font-semibold ${headerCls}`} data-testid="gcp-credit-heading">
                      Google Cloud Credits
                    </p>
                    {creditsLow && (
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-600 border border-red-300 uppercase tracking-wide" data-testid="gcp-credits-low-badge">
                        Credits Low
                      </span>
                    )}
                    {gc && (liveSpend || liveBudget) && (
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
                        liveSpend
                          ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                          : 'bg-amber-50 text-amber-600 border-amber-200'
                      }`} data-testid="gcp-data-source-badge">
                        {liveSpend ? 'Live · BigQuery' : 'Live · Budget API'}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-gray-500 mt-0.5">
                    {isUnconfigured
                      ? 'GCP service account not configured'
                      : gc?.billing_account_name
                        ? gc.billing_account_name
                        : gc?.billing_account_id
                          ? `Account: ${gc.billing_account_id}`
                          : 'GCP Billing'}
                  </p>
                </div>
                <button
                  onClick={loadGcpCredits}
                  disabled={gcpCreditsLoading}
                  className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                  data-testid="button-refresh-gcp-credits"
                  title="Refresh GCP credit data"
                >
                  <RefreshCw size={13} className={gcpCreditsLoading ? 'animate-spin' : ''} />
                </button>
              </div>

              {gcpCreditsLoading && !gc && (
                <div className="flex justify-center py-4">
                  <RefreshCw size={16} className="animate-spin text-gray-300" />
                </div>
              )}

              {gcpCredits?._error && (
                <p className="text-xs text-red-500 mt-1">Failed to load GCP credit data — check backend logs.</p>
              )}

              {isUnconfigured && !gcpCredits?._error && (
                <div className="mt-2 space-y-1.5">
                  <p className="text-xs text-gray-600 font-medium">Setup instructions:</p>
                  <ol className="space-y-1">
                    {[
                      'Create a GCP service account with roles/billing.viewer',
                      'Download its JSON key and set GOOGLE_APPLICATION_CREDENTIALS_JSON in your env',
                      'Set GOOGLE_BILLING_ACCOUNT_ID to enable live budget data',
                      'Enable BigQuery Billing Export for real per-service spend figures',
                    ].map((s, i) => (
                      <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                        <span className="w-4 h-4 rounded-full bg-blue-50 flex items-center justify-center text-[9px] font-bold text-blue-600 flex-shrink-0 mt-0.5">{i + 1}</span>
                        {s}
                      </li>
                    ))}
                  </ol>
                </div>
              )}

              {gc && saConfigured && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-1">
                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Grant Total</p>
                    <p className="text-sm font-bold font-mono text-gray-900" data-testid="gcp-grant-usd">
                      ${gc.grant_usd != null ? Number(gc.grant_usd).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }) : '—'}
                    </p>
                  </div>
                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Spend MTD</p>
                    <p className={`text-sm font-bold font-mono ${creditsLow ? 'text-red-600' : 'text-gray-900'}`} data-testid="gcp-spend-mtd">
                      ${gc.spend_mtd_usd != null ? Number(gc.spend_mtd_usd).toFixed(2) : '—'}
                    </p>
                  </div>
                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Remaining</p>
                    <p className={`text-sm font-bold font-mono ${creditsLow ? 'text-red-600' : 'text-emerald-600'}`} data-testid="gcp-remaining">
                      ${gc.estimated_remaining_usd != null ? Number(gc.estimated_remaining_usd).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}
                    </p>
                  </div>
                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Runway</p>
                    <p className={`text-sm font-bold font-mono ${creditsLow ? 'text-red-600' : 'text-gray-900'}`} data-testid="gcp-runway">
                      {gc.months_runway != null
                        ? (gc.months_runway >= 999 ? '∞' : `${Number(gc.months_runway).toFixed(1)} mo`)
                        : '—'}
                    </p>
                  </div>
                </div>
              )}

              {gc && gc.billing_alert_active && (
                <div className="flex items-start gap-2 p-2 mt-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-700">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                  <span>GCP billing alert is active — a budget threshold has been breached.</span>
                </div>
              )}

              {gc && gc.billing_api_error && (
                <p className="text-[11px] text-amber-600 mt-2 font-mono break-all">{gc.billing_api_error}</p>
              )}
            </div>
          );
        })()}
        </SectionErrorBoundary>

        {/* Task #263 — CF paid add-on migration status panel */}
        <SectionErrorBoundary name="CF Add-on Migration">
        {(() => {
          const data = cfAddons && !cfAddons._error ? cfAddons : null;
          const counts = data?.status_counts ?? { pending: 0, in_progress: 0, complete: 0 };
          const totalPending = data?.monthly_savings_pending_usd ?? 0;
          const totalSaved  = data?.monthly_savings_realised_usd ?? 0;
          const allDone     = data && counts.pending === 0 && counts.in_progress === 0;
          const anyInProgress = counts.in_progress > 0;
          const tileCls = allDone
            ? 'bg-emerald-50 border-emerald-200'
            : anyInProgress
              ? 'bg-blue-50 border-blue-200'
              : 'bg-amber-50 border-amber-200';
          const headerCls = allDone
            ? 'text-emerald-700'
            : anyInProgress
              ? 'text-blue-700'
              : 'text-amber-700';
          const STATUS_STYLE = {
            complete:    'bg-emerald-100 text-emerald-700 border-emerald-200',
            in_progress: 'bg-blue-100 text-blue-700 border-blue-200',
            pending:     'bg-amber-100 text-amber-700 border-amber-200',
          };
          const STATUS_LABEL = {
            complete:    '✅ Complete',
            in_progress: '🔵 In Progress',
            pending:     '🟡 Pending',
          };
          return (
            <div className={`rounded-2xl p-4 border ${tileCls}`} data-testid="cf-addon-migration-panel">
              <div className="flex items-center gap-3 mb-3">
                <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${
                  allDone ? 'bg-emerald-100' : anyInProgress ? 'bg-blue-100' : 'bg-amber-100'
                }`}>
                  <DollarSign size={17} className={allDone ? 'text-emerald-500' : anyInProgress ? 'text-blue-500' : 'text-amber-500'} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className={`text-sm font-semibold ${headerCls}`} data-testid="cf-addon-heading">
                      CF Add-on Migration
                    </p>
                    {data && (
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
                        allDone
                          ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                          : 'bg-amber-50 text-amber-700 border-amber-200'
                      }`} data-testid="cf-addon-savings-badge">
                        {allDone ? `$${totalSaved}/mo saved` : `$${totalPending}/mo remaining`}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-gray-500 mt-0.5">
                    Replace paid CF add-ons with startup-credit-covered alternatives — Task #263
                  </p>
                </div>
                <button
                  onClick={loadCfAddons}
                  disabled={cfAddonsLoading}
                  className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                  data-testid="button-refresh-cf-addons"
                  title="Refresh CF add-on migration status"
                >
                  <RefreshCw size={13} className={cfAddonsLoading ? 'animate-spin' : ''} />
                </button>
              </div>

              {cfAddonsLoading && !data && (
                <div className="flex justify-center py-4">
                  <RefreshCw size={16} className="animate-spin text-gray-300" />
                </div>
              )}

              {cfAddons?._error && (
                <p className="text-xs text-red-500 mt-1">Failed to load CF add-on data — check backend logs.</p>
              )}

              {data && (
                <>
                  {/* Status summary counts */}
                  <div className="flex gap-2 flex-wrap mb-3">
                    {[
                      { key: 'complete',    label: 'Complete' },
                      { key: 'in_progress', label: 'In Progress' },
                      { key: 'pending',     label: 'Pending' },
                    ].map(({ key, label }) => counts[key] > 0 && (
                      <span key={key} className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${STATUS_STYLE[key]}`}>
                        {counts[key]} {label}
                      </span>
                    ))}
                    {totalSaved > 0 && (
                      <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full border bg-emerald-50 text-emerald-700 border-emerald-200">
                        ${totalSaved}/mo saved so far
                      </span>
                    )}
                  </div>

                  {/* Add-on rows */}
                  <div className="space-y-2">
                    {(data.addons || []).map((addon, i) => (
                      <div key={i} className="rounded-xl p-3 bg-white/70 border border-white/80 text-xs">
                        <div className="flex items-start justify-between gap-2 mb-1">
                          <div className="flex items-center gap-2 flex-wrap min-w-0">
                            <span className="font-semibold text-gray-900 shrink-0">{addon.service}</span>
                            <span className="font-mono text-gray-500 shrink-0">${addon.monthly_cost_usd}/mo</span>
                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full border shrink-0 ${STATUS_STYLE[addon.status] ?? STATUS_STYLE.pending}`}>
                              {STATUS_LABEL[addon.status] ?? addon.status}
                            </span>
                          </div>
                        </div>
                        <p className="text-gray-600 mb-0.5">
                          <span className="text-gray-400">→ </span>{addon.migration_target}
                        </p>
                        <p className="text-gray-400">
                          Covered by: <span className="text-gray-600">{addon.credit_programme}</span>
                        </p>
                        {addon.notes && (
                          <p className="text-gray-400 mt-1 leading-snug">{addon.notes}</p>
                        )}
                      </div>
                    ))}
                  </div>

                  {data.runbook_url && (
                    <a
                      href={data.runbook_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 mt-3 text-[11px] text-blue-500 hover:text-blue-700 hover:underline"
                      data-testid="cf-addon-runbook-link"
                    >
                      <ExternalLink size={11} />
                      View migration runbook
                    </a>
                  )}
                </>
              )}
            </div>
          );
        })()}
        </SectionErrorBoundary>
        {/* Task #263 — Startup credit burn panels: AWS Activate, Azure, Axiom, Sentry */}
        <SectionErrorBoundary name="Startup Credit Panels">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">

          {/* ── AWS Activate ─────────────────────────────────────────────── */}
          {(() => {
            const d = awsCredits && !awsCredits._error ? awsCredits : null;
            const low = d?.credits_low ?? false;
            const unconfigured = !awsCreditsLoading && (!d || !d.configured);
            const tile = low ? 'bg-red-50 border-red-200' : unconfigured ? 'bg-gray-50 border-gray-200' : 'bg-orange-50 border-orange-200';
            const hdr = low ? 'text-red-600' : unconfigured ? 'text-gray-500' : 'text-orange-600';
            return (
              <div className={`rounded-2xl p-4 border ${tile}`} data-testid="aws-credit-panel">
                <div className="flex items-center gap-3 mb-3">
                  <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${low ? 'bg-red-100' : unconfigured ? 'bg-gray-100' : 'bg-orange-100'}`}>
                    <DollarSign size={17} className={low ? 'text-red-500' : unconfigured ? 'text-gray-400' : 'text-orange-500'} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className={`text-sm font-semibold ${hdr}`} data-testid="aws-credit-heading">AWS Activate</p>
                      {low && <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-600 border border-red-300 uppercase tracking-wide">Credits Low</span>}
                      {d?.services?.length > 0 && (
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full border bg-orange-50 text-orange-600 border-orange-200">
                          Lambda · SES · Route 53 · CloudFront · Bedrock
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5">
                      {unconfigured ? 'AWS cost explorer not configured' : d?.account_alias ?? 'AWS Activate (Portfolio)'}
                    </p>
                  </div>
                  <button onClick={loadAwsCredits} disabled={awsCreditsLoading}
                    className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                    data-testid="button-refresh-aws-credits" title="Refresh AWS credit data">
                    <RefreshCw size={13} className={awsCreditsLoading ? 'animate-spin' : ''} />
                  </button>
                </div>

                {awsCreditsLoading && !d && <div className="flex justify-center py-4"><RefreshCw size={16} className="animate-spin text-gray-300" /></div>}
                {awsCredits?._error && <p className="text-xs text-red-500 mt-1">Failed to load AWS credit data — check backend logs.</p>}

                {unconfigured && !awsCredits?._error && (
                  <div className="mt-2 space-y-1.5">
                    <p className="text-xs text-gray-600 font-medium">Setup instructions:</p>
                    <ol className="space-y-1">
                      {[
                        'Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY with ce:GetCostAndUsage permission',
                        'Set AWS_ACTIVATE_GRANT_USD to the Activate programme grant total',
                        'Set AWS_ACTIVATE_EXPIRY to the credit expiry date (YYYY-MM-DD)',
                      ].map((s, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                          <span className="w-4 h-4 rounded-full bg-orange-50 flex items-center justify-center text-[9px] font-bold text-orange-600 flex-shrink-0 mt-0.5">{i + 1}</span>
                          {s}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}

                {d && d.configured && (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-1">
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Grant Total</p>
                      <p className="text-sm font-bold font-mono text-gray-900" data-testid="aws-grant-usd">
                        ${d.grant_usd != null ? Number(d.grant_usd).toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'}
                      </p>
                    </div>
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Spend MTD</p>
                      <p className={`text-sm font-bold font-mono ${low ? 'text-red-600' : 'text-gray-900'}`} data-testid="aws-spend-mtd">
                        ${d.spend_mtd_usd != null ? Number(d.spend_mtd_usd).toFixed(2) : '—'}
                      </p>
                    </div>
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Remaining</p>
                      <p className={`text-sm font-bold font-mono ${low ? 'text-red-600' : 'text-orange-600'}`} data-testid="aws-remaining">
                        ${d.estimated_remaining_usd != null ? Number(d.estimated_remaining_usd).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}
                      </p>
                    </div>
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Runway</p>
                      <p className={`text-sm font-bold font-mono ${low ? 'text-red-600' : 'text-gray-900'}`} data-testid="aws-runway">
                        {d.months_runway != null ? (d.months_runway >= 999 ? '∞' : `${Number(d.months_runway).toFixed(1)} mo`) : '—'}
                      </p>
                    </div>
                  </div>
                )}
                {d && d.days_until_expiry != null && (
                  <p className="text-[11px] text-gray-500 mt-2">
                    Credits expire: <span className="font-mono font-semibold">{d.expiry_date ?? '—'}</span>
                    {' '}(<span className={d.days_until_expiry < 60 ? 'text-red-500 font-semibold' : 'text-gray-600'}>{d.days_until_expiry}d remaining</span>)
                  </p>
                )}
              </div>
            );
          })()}

          {/* ── Azure for Startups ───────────────────────────────────────── */}
          {(() => {
            const d = azureCredits && !azureCredits._error ? azureCredits : null;
            const low = d?.credits_low ?? false;
            const unconfigured = !azureCreditsLoading && (!d || !d.configured);
            const tile = low ? 'bg-red-50 border-red-200' : unconfigured ? 'bg-gray-50 border-gray-200' : 'bg-blue-50 border-blue-200';
            const hdr = low ? 'text-red-600' : unconfigured ? 'text-gray-500' : 'text-blue-600';
            return (
              <div className={`rounded-2xl p-4 border ${tile}`} data-testid="azure-credit-panel">
                <div className="flex items-center gap-3 mb-3">
                  <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${low ? 'bg-red-100' : unconfigured ? 'bg-gray-100' : 'bg-blue-100'}`}>
                    <DollarSign size={17} className={low ? 'text-red-500' : unconfigured ? 'text-gray-400' : 'text-blue-500'} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className={`text-sm font-semibold ${hdr}`} data-testid="azure-credit-heading">Azure for Startups</p>
                      {low && <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-600 border border-red-300 uppercase tracking-wide">Credits Low</span>}
                      {d?.configured && (
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full border bg-blue-50 text-blue-600 border-blue-200">
                          Front Door · Cosmos DB · DDoS · Monitor
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5">
                      {unconfigured ? 'Azure Cost Management not configured' : d?.subscription_name ?? 'Azure for Startups ($5 000)'}
                    </p>
                  </div>
                  <button onClick={loadAzureCredits} disabled={azureCreditsLoading}
                    className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                    data-testid="button-refresh-azure-credits" title="Refresh Azure credit data">
                    <RefreshCw size={13} className={azureCreditsLoading ? 'animate-spin' : ''} />
                  </button>
                </div>

                {azureCreditsLoading && !d && <div className="flex justify-center py-4"><RefreshCw size={16} className="animate-spin text-gray-300" /></div>}
                {azureCredits?._error && <p className="text-xs text-red-500 mt-1">Failed to load Azure credit data — check backend logs.</p>}

                {unconfigured && !azureCredits?._error && (
                  <div className="mt-2 space-y-1.5">
                    <p className="text-xs text-gray-600 font-medium">Setup instructions:</p>
                    <ol className="space-y-1">
                      {[
                        'Create an Azure service principal with Billing Reader role',
                        'Set AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID, AZURE_SUBSCRIPTION_ID',
                        'Set AZURE_ACTIVATE_GRANT_USD and AZURE_ACTIVATE_EXPIRY (YYYY-MM-DD)',
                      ].map((s, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                          <span className="w-4 h-4 rounded-full bg-blue-50 flex items-center justify-center text-[9px] font-bold text-blue-600 flex-shrink-0 mt-0.5">{i + 1}</span>
                          {s}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}

                {d && d.configured && (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-1">
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Grant Total</p>
                      <p className="text-sm font-bold font-mono text-gray-900" data-testid="azure-grant-usd">
                        ${d.grant_usd != null ? Number(d.grant_usd).toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'}
                      </p>
                    </div>
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Spend MTD</p>
                      <p className={`text-sm font-bold font-mono ${low ? 'text-red-600' : 'text-gray-900'}`} data-testid="azure-spend-mtd">
                        ${d.spend_mtd_usd != null ? Number(d.spend_mtd_usd).toFixed(2) : '—'}
                      </p>
                    </div>
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Remaining</p>
                      <p className={`text-sm font-bold font-mono ${low ? 'text-red-600' : 'text-blue-600'}`} data-testid="azure-remaining">
                        ${d.estimated_remaining_usd != null ? Number(d.estimated_remaining_usd).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}
                      </p>
                    </div>
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Runway</p>
                      <p className={`text-sm font-bold font-mono ${low ? 'text-red-600' : 'text-gray-900'}`} data-testid="azure-runway">
                        {d.months_runway != null ? (d.months_runway >= 999 ? '∞' : `${Number(d.months_runway).toFixed(1)} mo`) : '—'}
                      </p>
                    </div>
                  </div>
                )}
                {d && d.days_until_expiry != null && (
                  <p className="text-[11px] text-gray-500 mt-2">
                    Credits expire: <span className="font-mono font-semibold">{d.expiry_date ?? '—'}</span>
                    {' '}(<span className={d.days_until_expiry < 60 ? 'text-red-500 font-semibold' : 'text-gray-600'}>{d.days_until_expiry}d remaining</span>)
                  </p>
                )}
              </div>
            );
          })()}

          {/* ── Axiom startup tier ───────────────────────────────────────── */}
          {(() => {
            const d = axiomCredits && !axiomCredits._error ? axiomCredits : null;
            const overLimit = d?.over_limit ?? false;
            const unconfigured = !axiomCreditsLoading && (!d || !d.configured);
            const tile = overLimit ? 'bg-red-50 border-red-200' : unconfigured ? 'bg-gray-50 border-gray-200' : 'bg-violet-50 border-violet-200';
            const hdr = overLimit ? 'text-red-600' : unconfigured ? 'text-gray-500' : 'text-violet-600';
            const ingestPct = d?.ingest_gb != null && d?.ingest_limit_gb != null
              ? Math.min(100, Math.round((d.ingest_gb / d.ingest_limit_gb) * 100))
              : null;
            return (
              <div className={`rounded-2xl p-4 border ${tile}`} data-testid="axiom-credit-panel">
                <div className="flex items-center gap-3 mb-3">
                  <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${overLimit ? 'bg-red-100' : unconfigured ? 'bg-gray-100' : 'bg-violet-100'}`}>
                    <BarChart2 size={17} className={overLimit ? 'text-red-500' : unconfigured ? 'text-gray-400' : 'text-violet-500'} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className={`text-sm font-semibold ${hdr}`} data-testid="axiom-credit-heading">Axiom Log Explorer</p>
                      {overLimit && <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-600 border border-red-300 uppercase tracking-wide">Over Limit</span>}
                      {d?.configured && !overLimit && (
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full border bg-violet-50 text-violet-600 border-violet-200">Startup Tier · 500 GB/mo</span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5">
                      {unconfigured ? 'Axiom API token not configured' : 'Replaces Cloudflare Log Explorer'}
                    </p>
                  </div>
                  <button onClick={loadAxiomCredits} disabled={axiomCreditsLoading}
                    className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                    data-testid="button-refresh-axiom-credits" title="Refresh Axiom usage">
                    <RefreshCw size={13} className={axiomCreditsLoading ? 'animate-spin' : ''} />
                  </button>
                </div>

                {axiomCreditsLoading && !d && <div className="flex justify-center py-4"><RefreshCw size={16} className="animate-spin text-gray-300" /></div>}
                {axiomCredits?._error && <p className="text-xs text-red-500 mt-1">Failed to load Axiom usage data — check backend logs.</p>}

                {unconfigured && !axiomCredits?._error && (
                  <div className="mt-2 space-y-1.5">
                    <p className="text-xs text-gray-600 font-medium">Setup instructions:</p>
                    <ol className="space-y-1">
                      {[
                        'Create an Axiom API token with Query + Ingest permissions',
                        'Set AXIOM_API_TOKEN and AXIOM_ORG_ID on the backend',
                        'Configure Cloudflare Logpush to POST to api.axiom.co/v1/datasets/cf-logs/ingest',
                      ].map((s, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                          <span className="w-4 h-4 rounded-full bg-violet-50 flex items-center justify-center text-[9px] font-bold text-violet-600 flex-shrink-0 mt-0.5">{i + 1}</span>
                          {s}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}

                {d && d.configured && (
                  <div className="space-y-2 mt-1">
                    <div className="grid grid-cols-3 gap-2">
                      <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                        <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Ingest MTD</p>
                        <p className={`text-sm font-bold font-mono ${overLimit ? 'text-red-600' : 'text-gray-900'}`} data-testid="axiom-ingest-gb">
                          {d.ingest_gb != null ? `${Number(d.ingest_gb).toFixed(1)} GB` : '—'}
                        </p>
                      </div>
                      <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                        <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Limit</p>
                        <p className="text-sm font-bold font-mono text-gray-900">
                          {d.ingest_limit_gb != null ? `${d.ingest_limit_gb} GB` : '500 GB'}
                        </p>
                      </div>
                      <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                        <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Retention</p>
                        <p className="text-sm font-bold font-mono text-violet-600">
                          {d.retention_days != null ? `${d.retention_days}d` : '30d'}
                        </p>
                      </div>
                    </div>
                    {ingestPct != null && (
                      <div>
                        <div className="flex justify-between mb-1">
                          <span className="text-[10px] text-gray-500">Monthly ingest usage</span>
                          <span className={`text-[10px] font-semibold ${ingestPct > 80 ? 'text-red-500' : ingestPct > 60 ? 'text-amber-500' : 'text-violet-600'}`}>{ingestPct}%</span>
                        </div>
                        <div className="h-1.5 rounded-full overflow-hidden bg-gray-100">
                          <div
                            style={{ width: `${ingestPct}%`, background: ingestPct > 80 ? '#ef4444' : ingestPct > 60 ? '#f59e0b' : '#7c3aed' }}
                            className="h-full rounded-full transition-all duration-500"
                          />
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })()}

          {/* ── Sentry startup tier ──────────────────────────────────────── */}
          {(() => {
            const d = sentryCredits && !sentryCredits._error ? sentryCredits : null;
            const overLimit = d?.over_limit ?? false;
            const unconfigured = !sentryCreditsLoading && (!d || !d.configured);
            const tile = overLimit ? 'bg-red-50 border-red-200' : unconfigured ? 'bg-gray-50 border-gray-200' : 'bg-indigo-50 border-indigo-200';
            const hdr = overLimit ? 'text-red-600' : unconfigured ? 'text-gray-500' : 'text-indigo-600';
            const errorPct = d?.errors_used != null && d?.errors_limit != null
              ? Math.min(100, Math.round((d.errors_used / d.errors_limit) * 100))
              : null;
            return (
              <div className={`rounded-2xl p-4 border ${tile}`} data-testid="sentry-credit-panel">
                <div className="flex items-center gap-3 mb-3">
                  <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${overLimit ? 'bg-red-100' : unconfigured ? 'bg-gray-100' : 'bg-indigo-100'}`}>
                    <AlertTriangle size={17} className={overLimit ? 'text-red-500' : unconfigured ? 'text-gray-400' : 'text-indigo-500'} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className={`text-sm font-semibold ${hdr}`} data-testid="sentry-credit-heading">Sentry</p>
                      {overLimit && <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-600 border border-red-300 uppercase tracking-wide">Quota Exceeded</span>}
                      {d?.configured && !overLimit && (
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full border bg-indigo-50 text-indigo-600 border-indigo-200">
                          Startup · {d?.plan ?? 'Team'} plan
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5">
                      {unconfigured ? 'Sentry auth token not configured' : 'Error tracking · Perf monitoring'}
                    </p>
                  </div>
                  <button onClick={loadSentryCredits} disabled={sentryCreditsLoading}
                    className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                    data-testid="button-refresh-sentry-credits" title="Refresh Sentry usage">
                    <RefreshCw size={13} className={sentryCreditsLoading ? 'animate-spin' : ''} />
                  </button>
                </div>

                {sentryCreditsLoading && !d && <div className="flex justify-center py-4"><RefreshCw size={16} className="animate-spin text-gray-300" /></div>}
                {sentryCredits?._error && <p className="text-xs text-red-500 mt-1">Failed to load Sentry usage data — check backend logs.</p>}

                {unconfigured && !sentryCredits?._error && (
                  <div className="mt-2 space-y-1.5">
                    <p className="text-xs text-gray-600 font-medium">Setup instructions:</p>
                    <ol className="space-y-1">
                      {[
                        'Generate a Sentry auth token with org:read and project:read scopes',
                        'Set SENTRY_AUTH_TOKEN, SENTRY_ORG, SENTRY_PROJECT on the backend',
                        'Apply for Sentry for Startups at sentry.io/for/startups/',
                      ].map((s, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                          <span className="w-4 h-4 rounded-full bg-indigo-50 flex items-center justify-center text-[9px] font-bold text-indigo-600 flex-shrink-0 mt-0.5">{i + 1}</span>
                          {s}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}

                {d && d.configured && (
                  <div className="space-y-2 mt-1">
                    <div className="grid grid-cols-3 gap-2">
                      <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                        <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Errors MTD</p>
                        <p className={`text-sm font-bold font-mono ${overLimit ? 'text-red-600' : 'text-gray-900'}`} data-testid="sentry-errors-used">
                          {d.errors_used != null ? Number(d.errors_used).toLocaleString() : '—'}
                        </p>
                      </div>
                      <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                        <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Quota</p>
                        <p className="text-sm font-bold font-mono text-gray-900">
                          {d.errors_limit != null ? Number(d.errors_limit).toLocaleString() : '—'}
                        </p>
                      </div>
                      <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                        <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Plan Expires</p>
                        <p className={`text-sm font-bold font-mono ${d?.days_until_expiry != null && d.days_until_expiry < 60 ? 'text-red-500' : 'text-indigo-600'}`}>
                          {d.expiry_date ?? '—'}
                        </p>
                      </div>
                    </div>
                    {errorPct != null && (
                      <div>
                        <div className="flex justify-between mb-1">
                          <span className="text-[10px] text-gray-500">Monthly error quota</span>
                          <span className={`text-[10px] font-semibold ${errorPct > 80 ? 'text-red-500' : errorPct > 60 ? 'text-amber-500' : 'text-indigo-600'}`}>{errorPct}%</span>
                        </div>
                        <div className="h-1.5 rounded-full overflow-hidden bg-gray-100">
                          <div
                            style={{ width: `${errorPct}%`, background: errorPct > 80 ? '#ef4444' : errorPct > 60 ? '#f59e0b' : '#6366f1' }}
                            className="h-full rounded-full transition-all duration-500"
                          />
                        </div>
                      </div>
                    )}
                    {d?.perf_transactions_used != null && (
                      <p className="text-[11px] text-gray-500">
                        Perf transactions: <span className="font-mono text-gray-700">{Number(d.perf_transactions_used).toLocaleString()}</span>
                        {d.perf_transactions_limit ? ` / ${Number(d.perf_transactions_limit).toLocaleString()}` : ''}
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })()}

        </div>

        {/* Startup credits summary row */}
        <div className="rounded-2xl p-4 bg-gradient-to-r from-violet-50 via-blue-50 to-orange-50 border border-violet-100">
          <p className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3 flex items-center gap-2">
            <DollarSign size={13} className="text-violet-500" />
            Startup Credit Programmes — Coverage Summary
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
            {[
              { label: 'GCP Activate', amount: '$100 000', expiry: 'Mar 2027', color: 'text-emerald-600', covers: 'Cloud Run · CDN · Storage · Logging' },
              { label: 'AWS Activate', amount: '$100 000', expiry: 'Jan 2027', color: 'text-orange-600', covers: 'Lambda · SES · Route 53 · CloudFront · Bedrock' },
              { label: 'Azure Startups', amount: '$5 000', expiry: 'Jan 2027', color: 'text-blue-600', covers: 'Front Door · Cosmos DB · DDoS · Monitor' },
              { label: 'Axiom + Sentry', amount: 'Free tiers', expiry: 'Ongoing', color: 'text-violet-600', covers: 'Logs · Errors · Perf · Replays' },
            ].map((p) => (
              <div key={p.label} className="rounded-xl p-3 bg-white/70 border border-white">
                <p className={`text-sm font-bold ${p.color}`}>{p.amount}</p>
                <p className="text-[11px] font-semibold text-gray-700 mt-0.5">{p.label}</p>
                <p className="text-[10px] text-gray-400 mt-0.5">expires {p.expiry}</p>
                <p className="text-[10px] text-gray-500 mt-1 leading-tight">{p.covers}</p>
              </div>
            ))}
          </div>
          <p className="text-[11px] text-gray-400 mt-3 text-center">
            Cloudflare Enterprise zone retained for WAF · Turnstile · mTLS · Zero Trust · Pages
            {' '}·{' '}
            <a href="/docs/infra/startup-credits-migration.md" className="underline hover:text-violet-600 transition-colors" target="_blank" rel="noopener noreferrer">
              View migration runbook →
            </a>
          </p>
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Live Traffic Stats">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <PeakBadge label="Active Now (5m)" value={current?.active_5m ?? 0} color="emerald" />
          <PeakBadge label="Peak Users (5m)" value={peaks?.active_users_5m ?? 0} color="violet" />
          <PeakBadge label="Current RPS" value={current?.rps ?? 0} color="blue" />
          <PeakBadge label="Peak RPS" value={peaks?.rps ?? 0} color="amber" />
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Activity Counters">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <PeakBadge label="Active (15m)" value={current?.active_15m ?? 0} color="emerald" />
          <PeakBadge label="Active (60m)" value={current?.active_60m ?? 0} color="emerald" />
          <PeakBadge label="Total Requests" value={current?.requests ?? 0} color="blue" />
          <PeakBadge label="AI Chats" value={current?.chats ?? 0} color="violet" />
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Active Users Over Time">
        <div className="rounded-xl p-5 bg-white border border-gray-200 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Users size={16} className="text-violet-500" />
              <h3 className="text-sm font-semibold text-gray-900">Active Users Over Time</h3>
            </div>
            <div className="flex gap-1">
              {[
                { label: '1h', val: 60 },
                { label: '6h', val: 360 },
                { label: '24h', val: 1440 },
              ].map(({ label, val }) => (
                <button
                  key={val}
                  onClick={() => setTimeRange(val)}
                  className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                    timeRange === val
                      ? 'bg-violet-50 text-violet-600 border border-violet-200'
                      : 'text-gray-400 hover:text-gray-600 hover:bg-gray-50 border border-transparent'
                  }`}
                  data-testid={`button-range-${label}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {metricsLoading ? (
            <div className="flex justify-center py-10">
              <RefreshCw size={20} className="animate-spin text-gray-300" />
            </div>
          ) : chartData.length < 2 ? (
            <div className="flex flex-col items-center justify-center py-10 text-gray-400">
              <Activity size={32} className="mb-2 opacity-40" />
              <p className="text-sm">Collecting data... Graph will appear after 2+ minutes.</p>
              <p className="text-xs mt-1 text-gray-300">Snapshots are taken every 60 seconds.</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="grad5m" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#7c3aed" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="grad15m" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.1} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="grad60m" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.1} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                <XAxis dataKey="time" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} allowDecimals={false} />
                <Tooltip content={<CustomTooltip />} />
                <Legend
                  wrapperStyle={{ fontSize: 11, color: '#6b7280', paddingTop: 8 }}
                  iconType="circle"
                  iconSize={8}
                />
                <Area type="monotone" dataKey="active_5m" name="Active (5m)" stroke="#7c3aed" fill="url(#grad5m)" strokeWidth={2} dot={false} />
                <Area type="monotone" dataKey="active_15m" name="Active (15m)" stroke="#10b981" fill="url(#grad15m)" strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
                <Area type="monotone" dataKey="active_60m" name="Active (60m)" stroke="#3b82f6" fill="url(#grad60m)" strokeWidth={1.5} dot={false} strokeDasharray="6 3" />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Requests Per Second">
        <div className="rounded-xl p-5 bg-white border border-gray-200 shadow-sm">
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp size={16} className="text-blue-500" />
            <h3 className="text-sm font-semibold text-gray-900">Requests Per Second</h3>
          </div>
          {metricsLoading ? (
            <div className="flex justify-center py-10">
              <RefreshCw size={20} className="animate-spin text-gray-300" />
            </div>
          ) : chartData.length < 2 ? (
            <div className="flex flex-col items-center justify-center py-8 text-gray-400">
              <p className="text-sm">Waiting for data points...</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="gradRps" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                <XAxis dataKey="time" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                <Tooltip content={<CustomTooltip />} />
                <Area type="monotone" dataKey="rps" name="RPS" stroke="#f59e0b" fill="url(#gradRps)" strokeWidth={2} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="AI Response Cache">
        <div className="rounded-xl p-4 bg-white border border-gray-200 shadow-sm" data-testid="ai-cache-panel">
          <div className="flex items-center justify-between mb-3">
            <div>
              <p className="text-xs font-bold text-gray-500 uppercase tracking-wider inline-flex items-center gap-2">
                AI Response Cache
                <span
                  data-testid="ai-cache-breaker-status"
                  className={`text-[10px] px-1.5 py-0.5 rounded-full font-mono normal-case tracking-normal ${
                    aiCacheStats?.managed?.breaker_open
                      ? 'bg-red-50 text-red-600 border border-red-200'
                      : 'bg-emerald-50 text-emerald-600 border border-emerald-200'
                  }`}
                >
                  Breaker: {aiCacheStats?.managed?.breaker_open ? 'OPEN' : 'CLOSED'}
                </span>
              </p>
              <p className="text-[11px] text-gray-400 mt-0.5">
                Backend: <span className="font-mono text-gray-600">{aiCacheStats?.managed?.backend || '—'}</span>
                {' · '}TTL: <span className="font-mono text-gray-600">{aiCacheStats?.managed?.ttl_seconds ?? '—'}s</span>
                {' · '}Max entry: <span className="font-mono text-gray-600">{aiCacheStats?.managed?.max_entry_bytes ?? '—'}B</span>
                {' · '}Namespace: <span className="font-mono text-gray-600">{aiCacheStats?.managed?.namespace || '—'}</span>
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={loadAiCacheStats}
                disabled={aiCacheLoading}
                className="text-xs px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-50 inline-flex items-center gap-1.5"
                data-testid="button-ai-cache-refresh"
              >
                <RotateCw size={12} className={aiCacheLoading ? 'animate-spin' : ''} /> Refresh
              </button>
              <button
                onClick={purgeAiCache}
                disabled={aiCachePurging}
                className="text-xs px-3 py-1.5 rounded-lg bg-red-50 text-red-600 hover:bg-red-100 border border-red-200 disabled:opacity-50"
                data-testid="button-ai-cache-purge"
              >
                {aiCachePurging ? 'Purging…' : 'Purge all'}
              </button>
            </div>
          </div>
          {aiCacheStats?.managed?.breaker_open && (
            <div className="mb-3 text-xs px-3 py-2 rounded-lg bg-red-50 text-red-700 border border-red-200 inline-flex items-center gap-2">
              <AlertTriangle size={12} /> Circuit breaker OPEN — cache temporarily disabled. Last error:
              <span className="font-mono">{aiCacheStats?.managed?.last_error || 'unknown'}</span>
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: 'Hit rate', value: aiCacheStats?.managed?.hit_rate != null ? `${(aiCacheStats.managed.hit_rate * 100).toFixed(1)}%` : '—' },
              { label: 'Hits', value: aiCacheStats?.managed?.hits ?? '—' },
              { label: 'Misses', value: aiCacheStats?.managed?.misses ?? '—' },
              { label: 'Errors', value: aiCacheStats?.managed?.errors ?? '—' },
              { label: 'Bytes stored', value: aiCacheStats?.managed?.bytes_stored ?? '—' },
              { label: 'Oversize skipped', value: aiCacheStats?.managed?.entries_skipped_oversize ?? '—' },
              { label: 'Avg saved / hit (ms)', value: aiCacheStats?.managed?.avg_saved_latency_ms ?? '—' },
              { label: 'Total saved (s)', value: aiCacheStats?.managed?.estimated_total_saved_ms != null
                  ? (aiCacheStats.managed.estimated_total_saved_ms / 1000).toFixed(1)
                  : '—' },
            ].map((m) => (
              <div key={m.label} className="rounded-lg bg-gray-50 border border-gray-100 p-2">
                <div className="text-[10px] text-gray-400 uppercase tracking-wider">{m.label}</div>
                <div className="text-sm font-semibold text-gray-800 font-mono">{m.value}</div>
              </div>
            ))}
          </div>
          <div className="mt-2 text-[10px] text-gray-400">
            L1 in-memory: <span className="font-mono">{aiCacheStats?.l1?.size ?? 0}/{aiCacheStats?.l1?.maxsize ?? '—'}</span>
            {' · '}Last purge: <span className="font-mono">{aiCacheStats?.managed?.purge_count ?? 0}×</span>
          </div>
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Pinecone Index Health">
        {(() => {
          const fetchFailed = pineconeHealth?._error === true;
          const ph = pineconeHealth && !fetchFailed ? pineconeHealth : null;
          const configured = ph ? ph.configured : false;
          const status = ph?.status ?? 'unknown';
          const isReady = status === 'ready';
          const isUnconfigured = !fetchFailed && (!configured || status === 'not_configured');
          const isEmpty = configured && isReady && ph?.total_vectors === 0;
          const hasWarning = isUnconfigured || isEmpty;
          const containerCls = fetchFailed
            ? 'bg-gray-50 border-gray-200'
            : isUnconfigured
              ? 'bg-gray-50 border-gray-200'
              : isEmpty
                ? 'bg-amber-50 border-amber-200'
                : isReady
                  ? 'bg-emerald-50 border-emerald-200'
                  : 'bg-amber-50 border-amber-200';
          const headerColor = fetchFailed
            ? 'text-gray-400'
            : isUnconfigured
              ? 'text-gray-500'
              : isEmpty
                ? 'text-amber-600'
                : isReady
                  ? 'text-emerald-600'
                  : 'text-amber-600';

          return (
            <div className={`rounded-2xl p-4 border ${containerCls}`} data-testid="pinecone-health-tile">
              <div className="flex items-center gap-3 mb-3">
                <Database size={18} className={fetchFailed || isUnconfigured ? 'text-gray-400' : isReady && !isEmpty ? 'text-emerald-500' : 'text-amber-500'} />
                <div className="flex-1 min-w-0">
                  <p className={`text-sm font-semibold ${headerColor}`} data-testid="pinecone-health-status">
                    Pinecone vector index
                    {fetchFailed && ' — health check unavailable'}
                    {!fetchFailed && isUnconfigured && ' — not configured'}
                    {!fetchFailed && !isUnconfigured && ` — ${ph?.index_name ?? '—'}`}
                  </p>
                  <p className="text-[11px] text-gray-500 mt-0.5">
                    {fetchFailed
                      ? 'Could not reach the health endpoint — check backend logs'
                      : isUnconfigured
                        ? 'Set PINECONE_KEY + PINECONE_INDEX to enable'
                        : `${ph?.dimensions ?? '—'}-dim cosine · serverless`}
                  </p>
                </div>

                {!fetchFailed && hasWarning && (
                  <span className="flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 border border-amber-200">
                    <AlertTriangle size={11} />
                    {isUnconfigured ? 'Unconfigured' : 'Empty index'}
                  </span>
                )}
                {fetchFailed && (
                  <span className="flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 border border-gray-200">
                    <AlertTriangle size={11} />
                    Unavailable
                  </span>
                )}

                <button
                  onClick={loadPineconeHealth}
                  disabled={pineconeLoading}
                  className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                  data-testid="button-refresh-pinecone"
                  title="Refresh Pinecone health"
                >
                  <RefreshCw size={13} className={pineconeLoading ? 'animate-spin' : ''} />
                </button>
              </div>

              {ph && !isUnconfigured && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Status</p>
                    <p className={`text-sm font-bold font-mono capitalize ${isReady ? 'text-emerald-600' : 'text-amber-600'}`} data-testid="pinecone-status-value">
                      {status}
                    </p>
                  </div>

                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Vectors</p>
                    <p className="text-sm font-bold font-mono text-gray-900" data-testid="pinecone-vector-count">
                      {ph.total_vectors != null ? ph.total_vectors.toLocaleString() : '—'}
                    </p>
                  </div>

                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Query latency</p>
                    <p className="text-sm font-bold font-mono" data-testid="pinecone-latency">
                      {ph.latency_ms != null
                        ? <LatencyBadge ms={ph.latency_ms} />
                        : <span className="text-gray-400">—</span>}
                    </p>
                  </div>

                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Index name</p>
                    <p className="text-sm font-bold font-mono text-gray-700 truncate" title={ph.index_name}>
                      {ph.index_name ?? '—'}
                    </p>
                  </div>
                </div>
              )}

              {ph?.error && (
                <div className="flex items-start gap-2 p-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-700 mb-3">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                  <span className="font-mono break-all">{ph.error}</span>
                </div>
              )}

              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[11px] text-gray-500 font-medium">Switch active retriever:</span>
                {['pinecone_vector', 'mongodb_vector'].map((name) => (
                  <button
                    key={name}
                    onClick={() => switchPineconeRetriever(name)}
                    disabled={!!pineconeSwitch}
                    data-testid={`pinecone-switch-${name}`}
                    className={`text-[11px] px-2.5 py-1 rounded-lg border font-mono font-semibold transition-colors ${
                      pineconeSwitch === name
                        ? 'bg-violet-100 text-violet-700 border-violet-300 animate-pulse'
                        : 'bg-white text-violet-600 border-violet-200 hover:bg-violet-50'
                    }`}
                  >
                    {pineconeSwitch === name ? 'Switching…' : name}
                  </button>
                ))}
              </div>
            </div>
          );
        })()}
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Dependency Status">
        <div className="space-y-3">
          {(() => {
            const KNOWN_SERVICES = [
              { key: 'mongodb',  icon: Database, label: 'Syrabit DB (MongoDB)', desc: 'User data, sessions, content, rate limits' },
              { key: 'redis',    icon: Wifi,     label: 'Redis Cache (Upstash)', desc: 'Shared content cache & session store' },
              { key: 'llm',      icon: Zap,      label: 'AI Provider Pool',      desc: 'Multi-provider SLM pool — Groq, Cerebras, Sarvam, OpenRouter, Fireworks' },
              { key: 'supabase', icon: Database, label: 'Supabase',              desc: 'Auth, user profiles, persistent storage' },
            ];
            const knownKeys = new Set(KNOWN_SERVICES.map(s => s.key));
            const extraKeys = Object.keys(deps).filter(k => !knownKeys.has(k));
            const allServices = [
              ...KNOWN_SERVICES,
              ...extraKeys.map(k => ({ key: k, icon: Activity, label: k.charAt(0).toUpperCase() + k.slice(1), desc: '' })),
            ];
            return allServices.map(({ key, icon: Icon, label, desc }) => {
              const dep = deps[key] || {};
              const isOk = dep.status === 'ok';
              const isNotConfigured = dep.status === 'not_configured';
              const isError = dep.status === 'error';
              return (
                <div key={key} className={`rounded-xl p-4 flex items-center gap-3 bg-white border border-gray-200 shadow-sm`} data-testid={`dep-${key}`} data-syra={`${key}-latency`}>
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${
                    isOk ? 'bg-emerald-50' : isNotConfigured ? 'bg-gray-100' : isError ? 'bg-red-50' : 'bg-amber-50'
                  }`}>
                    <Icon size={18} className={isOk ? 'text-emerald-500' : isNotConfigured ? 'text-gray-400' : isError ? 'text-red-500' : 'text-amber-500'} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900">{label}</p>
                    {desc && <p className="text-xs text-gray-400">{desc}</p>}
                    {dep.error && <p className="text-xs text-red-500 mt-0.5">{dep.error}</p>}
                  </div>
                  <div className="flex items-center gap-2">
                    <LatencyBadge ms={dep.latencyMs} />
                    <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${
                      isOk ? 'bg-emerald-50 text-emerald-600' :
                      isNotConfigured ? 'bg-gray-100 text-gray-500' :
                      isError ? 'bg-red-50 text-red-600' :
                      'bg-amber-50 text-amber-600 animate-pulse'
                    }`}>
                      {loading ? 'PROBING...' : dep.status?.toUpperCase().replace('_', ' ') || 'UNKNOWN'}
                    </span>
                  </div>
                </div>
              );
            });
          })()}
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Health Endpoint URL">
        <div className="rounded-xl p-4 bg-white border border-gray-200 shadow-sm">
          <p className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Health Endpoint URL</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-xs font-mono text-gray-600 bg-gray-50 px-3 py-2 rounded-lg truncate border border-gray-200">{healthUrl}</code>
            <button onClick={handleCopy} className="p-2 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 flex-shrink-0" data-testid="button-copy-url">
              {copied ? <Check size={14} className="text-emerald-500" /> : <Copy size={14} />}
            </button>
          </div>
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="UptimeRobot Setup">
        <div className="rounded-xl p-4 bg-white border border-gray-200 shadow-sm">
          <p className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3">UptimeRobot Setup</p>
          <ol className="space-y-2">
            {['Create free UptimeRobot account at uptimerobot.com','Add new HTTP(s) monitor','Paste the health URL above','Enable keyword monitoring: \'"status":"ok"\'','Configure alert contacts (email/Slack)','Save — you\'ll get 5-minute uptime checks'].map((s, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                <span className="w-5 h-5 rounded-full bg-violet-50 flex items-center justify-center text-[10px] font-bold text-violet-600 flex-shrink-0 mt-0.5">{i+1}</span>{s}
              </li>
            ))}
          </ol>
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Edge Metrics">
          <EdgeMetricsPanel token={adminToken} />
        </SectionErrorBoundary>
        </>)}
        <AdminQuickLinks links={['apiconfig','settings','dashboard','ratelimits']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
