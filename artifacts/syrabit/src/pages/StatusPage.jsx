import { useState, useEffect, useCallback } from 'react';
import { PublicLayout } from '@/components/layout/PublicLayout';
import PageMeta from '@/components/seo/PageMeta';
import { API_BASE, WORKER_API } from '@/utils/api';
import axios from 'axios';
import { CheckCircle2, XCircle, AlertTriangle, RefreshCw, Activity, EyeOff } from 'lucide-react';

/** Task #321 — fallback "watchdog blind" threshold mirrored from
 *  workers/edge-proxy/src/r2-storage-class-alert.ts
 *  (DEFAULT_QUERY_FAIL_THRESHOLD). The backend usually returns
 *  ``query_fail_threshold`` so this is only used if the field is
 *  missing (older worker / proxy outage). */
const R2_WATCHDOG_DEFAULT_THRESHOLD = 2;
const R2_WATCHDOG_RUNBOOK_URL =
  'https://github.com/syrabit/syrabit/blob/main/docs/cloudflare-monthly-cost-review.md#step-5';

const SERVICE_CHECKS = [
  { key: 'api', label: 'Backend API', description: 'Core backend services' },
  { key: 'postgresql', label: 'PostgreSQL', description: 'Primary data store' },
  { key: 'mongodb', label: 'RAG Index', description: 'Content & search database' },
  { key: 'redis', label: 'Redis Cache', description: 'Session & response cache' },
  { key: 'llm', label: 'LLM Pool', description: 'AI response generation' },
  { key: 'cdn', label: 'Frontend', description: 'Web application delivery' },
];

function StatusIcon({ status }) {
  if (status === 'operational') return <CheckCircle2 size={18} className="text-emerald-500" />;
  if (status === 'degraded') return <AlertTriangle size={18} className="text-amber-500" />;
  return <XCircle size={18} className="text-red-500" />;
}

function statusLabel(s) {
  if (s === 'operational') return 'Operational';
  if (s === 'degraded') return 'Degraded';
  return 'Down';
}

function statusColor(s) {
  if (s === 'operational') return 'text-emerald-500';
  if (s === 'degraded') return 'text-amber-500';
  return 'text-red-500';
}

export default function StatusPage() {
  const [services, setServices] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastChecked, setLastChecked] = useState(null);
  const [latency, setLatency] = useState(null);
  // Task #321 — mirror the admin dashboard's "watchdog blind"
  // indicator (R2ColdStoragePanel) on the public status page so
  // on-call sees a single failed monthly evaluation without needing
  // admin credentials. `null` while loading; the public payload from
  // /api/r2-watchdog-status otherwise.
  const [r2Watchdog, setR2Watchdog] = useState(null);

  const checkStatus = useCallback(async () => {
    setLoading(true);
    const t0 = performance.now();
    try {
      const res = await axios.get(`${API_BASE}/health`);
      const ms = Math.round(performance.now() - t0);
      setLatency(ms);
      const data = res.data;
      const deps = data.dependencies || {};
      const mapStatus = (s) => {
        const v = (s || '').toLowerCase();
        if (v === 'ok' || v === 'configured') return 'operational';
        if (v === 'degraded') return 'degraded';
        if (v === 'not_connected' || v === 'not_configured') return 'degraded';
        if (v === 'unavailable' || v === 'error') return 'down';
        return 'down';
      };
      setServices({
        api: 'operational',
        postgresql: mapStatus(deps.postgresql?.status),
        mongodb: mapStatus(deps.mongodb?.status),
        redis: mapStatus(deps.redis?.status),
        llm: mapStatus(deps.llm?.status),
        cdn: 'operational',
      });
    } catch {
      setLatency(null);
      setServices({
        api: 'down',
        postgresql: 'down',
        mongodb: 'down',
        redis: 'down',
        llm: 'down',
        cdn: 'operational',
      });
    }
    // Task #321 — fetched in parallel-ish (after /health to keep the
    // happy-path render order). Failures degrade silently — the
    // indicator simply hides instead of turning the whole page red.
    try {
      const wd = await axios.get(`${API_BASE}/r2-watchdog-status`);
      setR2Watchdog(wd.data || null);
    } catch {
      setR2Watchdog(null);
    }
    setLastChecked(new Date());
    setLoading(false);
  }, []);

  useEffect(() => { checkStatus(); }, [checkStatus]);

  const allOperational = services && Object.values(services).every(s => s === 'operational');
  const anyDown = services && Object.values(services).some(s => s === 'down');

  return (
    <PublicLayout>
      <PageMeta
        title="System Status"
        description="Real-time health status of Syrabit.ai services — API, database, AI models, and frontend delivery. Check uptime and service availability."
        url="https://syrabit.ai/status"
      />
      <div className="min-h-screen pt-8 pb-24 px-4">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-center justify-between mb-8">
            <div>
              <h1 className="text-2xl font-semibold text-foreground flex items-center gap-2">
                <Activity size={22} className="text-primary" />
                System Status
              </h1>
              <p className="text-muted-foreground text-sm mt-1">
                Real-time health of Syrabit.ai services
              </p>
            </div>
            <button
              onClick={checkStatus}
              disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-muted transition-colors disabled:opacity-40"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
              Refresh
            </button>
          </div>

          <div className="rounded-xl border border-border/30 overflow-hidden mb-6 glass-card">
            <div className="px-5 py-4 border-b border-border/20 flex items-center justify-between">
              <div className="flex items-center gap-2">
                {services && (
                  <div className={`w-2.5 h-2.5 rounded-full ${allOperational ? 'bg-emerald-500' : anyDown ? 'bg-red-500' : 'bg-amber-500'}`}
                    style={{ boxShadow: `0 0 8px ${allOperational ? '#10b981' : anyDown ? '#ef4444' : '#f59e0b'}` }} />
                )}
                <span className="text-foreground font-medium">
                  {!services ? 'Checking…' : allOperational ? 'All Systems Operational' : anyDown ? 'Service Disruption Detected' : 'Partial Degradation'}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <R2WatchdogIndicator data={r2Watchdog} />
                {latency !== null && (
                  <span className="text-muted-foreground/50 text-xs">
                    {latency}ms response
                  </span>
                )}
              </div>
            </div>

            <div className="divide-y divide-border/15">
              {SERVICE_CHECKS.map(({ key, label, description }) => {
                const s = services?.[key];
                return (
                  <div key={key} className="px-5 py-3.5 flex items-center justify-between">
                    <div>
                      <p className="text-foreground text-sm font-medium">{label}</p>
                      <p className="text-muted-foreground/50 text-xs">{description}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      {s ? (
                        <>
                          <span className={`text-xs font-medium ${statusColor(s)}`}>{statusLabel(s)}</span>
                          <StatusIcon status={s} />
                        </>
                      ) : (
                        <span className="text-muted-foreground/40 text-xs">Checking…</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {lastChecked && (
            <p className="text-muted-foreground/40 text-xs text-center">
              Last checked: {lastChecked.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </p>
          )}
        </div>
      </div>
    </PublicLayout>
  );
}

/**
 * Task #321 — public-facing mirror of the admin dashboard's
 * "watchdog blind" indicator (see
 * artifacts/syrabit/src/components/admin/R2ColdStoragePanel.jsx
 * ``WatchdogBlindIndicator``). On-call responders typically work
 * from this status page rather than the admin dashboard, so we
 * surface ``consecutive_query_failures`` here too: a single failed
 * monthly evaluation flips the badge amber, the second failure (the
 * one that actually pages) flips it red.
 *
 * Hidden when the watchdog is unconfigured / the counter is 0 so
 * the status header stays uncluttered in the steady state. Tooltip
 * + link both deep-link into the same Step 5 runbook the admin tile
 * uses, so on-call has one source of truth either way.
 */
function R2WatchdogIndicator({ data }) {
  if (!data || data.configured === false) return null;
  const state = data.state || {};
  const count = Number(state.consecutive_query_failures || 0);
  if (!Number.isFinite(count) || count < 1) return null;
  const threshold = Number(
    data.query_fail_threshold ?? R2_WATCHDOG_DEFAULT_THRESHOLD,
  );
  const tripped = count >= threshold;
  const cls = tripped
    ? 'bg-red-500/15 text-red-500 ring-red-500/30'
    : 'bg-amber-500/15 text-amber-500 ring-amber-500/30';
  const lastFired = state.query_fail_last_fired_at;
  const remaining = Math.max(0, threshold - count);
  const runbookUrl = data.runbook_url || R2_WATCHDOG_RUNBOOK_URL;
  const tooltip =
    `R2 cold-storage watchdog: ${count} of ${threshold} consecutive ` +
    `monthly evaluations failed (~${count} month${count === 1 ? '' : 's'} blind). ` +
    (tripped
      ? 'Watchdog-blind page has fired — the primary IA-share + Logpush-cap alerts cannot fire while this is broken. '
      : `${remaining} more failed monthly evaluation${remaining === 1 ? '' : 's'} will trip the page. `) +
    (lastFired
      ? `Last fired: ${new Date(lastFired).toLocaleString()}. `
      : 'Never fired. ') +
    `Runbook: ${runbookUrl}`;
  return (
    <a
      href={runbookUrl}
      target="_blank"
      rel="noopener noreferrer"
      title={tooltip}
      aria-label={tooltip}
      data-testid="status-page-r2-watchdog-indicator"
      data-watchdog-state={tripped ? 'tripped' : 'warn'}
      data-watchdog-count={count}
      data-watchdog-threshold={threshold}
      className={`inline-flex items-center gap-1 text-[10px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded ring-1 ${cls} no-underline hover:brightness-110`}
    >
      <EyeOff size={10} />
      <span>R2 watchdog {count}/{threshold}</span>
    </a>
  );
}
