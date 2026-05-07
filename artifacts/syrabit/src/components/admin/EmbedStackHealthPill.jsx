import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import { Activity, RefreshCw, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { API_BASE } from '@/utils/api';

// Task #436 — surface the embed-stack health pill on the admin
// dashboard. Each of the three legs (embed / rerank / memory_brain)
// shows the live ok/down state plus an "N/3 consecutive failures"
// badge driven by the Task #412 watchdog counters in metrics.py.
// The badge turns red the moment the watchdog crosses the paging
// threshold so on-call sees the warm-up before the page lands and
// can confirm recovery without waiting for the recovery alert.
//
// Endpoint: GET /admin/health/embed-stack
// New fields consumed (Task #436):
//   * embed.consecutive_failures, embed.firing, embed.alert_threshold
//   * rerank.{same}, memory.{same}
//   * alert_state.{threshold, legs}

const adminHeaders = (token) => {
  const h = { 'Content-Type': 'application/json' };
  if (token) h['X-Admin-Token'] = token;
  return h;
};

const LEGS = [
  { key: 'embed',  label: 'Embed',        sub: 'Workers-AI custom worker' },
  { key: 'rerank', label: 'Rerank',       sub: 'Pinecone /rerank' },
  { key: 'memory', label: 'Memory brain', sub: 'Voyage + Atlas' },
];

// Task #438 — one row per embed-worker environment (production + staging,
// plus any future env). Staging failures render in amber instead of red
// because the staging worker is a canary and does NOT page on-call.
function EmbedEnvRow({ envInfo }) {
  const env       = envInfo?.env || 'unknown';
  const label     = envInfo?.label || env;
  const ok        = !!envInfo?.ok;
  const configured = envInfo?.configured !== false;
  const pages     = !!envInfo?.pages;
  const dims      = envInfo?.dims;
  const modelVer  = envInfo?.model_version || envInfo?.version || '';
  const latency   = envInfo?.latency_ms;
  const status    = envInfo?.status_code;
  const reason    = envInfo?.reason;
  const url       = envInfo?.url;

  // Tone:
  //  • not configured → slate (neutral, "not wired yet")
  //  • ok            → emerald
  //  • failing prod  → red (pages)
  //  • failing canary → amber (does not page)
  let tone = 'bg-emerald-50 border-emerald-200 text-emerald-700';
  let badgeTone = 'bg-emerald-100 text-emerald-700 border-emerald-200';
  let badgeText = 'OK';
  if (!configured) {
    tone = 'bg-slate-50 border-slate-200 text-slate-600';
    badgeTone = 'bg-slate-100 text-slate-600 border-slate-200';
    badgeText = 'Not configured';
  } else if (!ok) {
    if (pages) {
      tone = 'bg-rose-50 border-rose-200 text-rose-700';
      badgeTone = 'bg-rose-100 text-rose-700 border-rose-300';
      badgeText = 'DOWN';
    } else {
      tone = 'bg-amber-50 border-amber-200 text-amber-700';
      badgeTone = 'bg-amber-100 text-amber-700 border-amber-300';
      badgeText = 'CANARY DOWN';
    }
  }

  return (
    <div
      className={`rounded-lg border px-3 py-2 ${tone}`}
      data-testid={`embed-env-row-${env}`}
    >
      <div className="flex items-center gap-2">
        <p className="text-xs font-semibold flex-1 min-w-0 truncate">
          {label}
          {!pages && configured && (
            <span className="ml-1 text-[9px] uppercase tracking-wide text-amber-600 font-bold">
              canary
            </span>
          )}
        </p>
        <span
          className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border tabular-nums ${badgeTone}`}
          data-testid={`embed-env-row-${env}-status`}
          title={reason || (status ? `HTTP ${status}` : '')}
        >
          {badgeText}
        </span>
      </div>
      <div className="mt-1 grid grid-cols-3 gap-2 text-[10px] text-gray-600 tabular-nums">
        <span title="vector dimension"               data-testid={`embed-env-row-${env}-dims`}>
          dims: <span className="font-semibold">{dims ?? '—'}</span>
        </span>
        <span title="model version reported by /health" data-testid={`embed-env-row-${env}-model-version`}>
          ver: <span className="font-semibold truncate">{modelVer || '—'}</span>
        </span>
        <span title="last probe latency"             data-testid={`embed-env-row-${env}-latency`}>
          {latency != null ? `${latency} ms` : '— ms'}
        </span>
      </div>
      {(reason || url) && (
        <p className="mt-1 text-[10px] text-gray-500 truncate" title={url || ''}>
          {reason ? `${reason}` : url}
        </p>
      )}
    </div>
  );
}

// Task #523 — staging-vs-production drift watchdog badge. Surfaces the
// in-memory consecutive-drift counter from `metrics.get_embed_stack_
// drift_snapshot()` so on-call sees "we're 2/3 ticks into a drift"
// before the Slack alert fires. Tones:
//   • clean (consecutive=0, not firing)              → slate
//   • warm-up window (1..threshold-1, not firing)    → amber
//   • firing (>=threshold or `firing` latched)       → red
function DriftBadge({ drift }) {
  if (!drift || typeof drift !== 'object') return null;
  const threshold = Number(drift.threshold || 3);
  const consecutive = Number(drift.consecutive || 0);
  const firing = !!drift.firing;
  const fields = drift.last_payload?.drift_fields;

  let tone = 'bg-slate-100 text-slate-600 border-slate-200';
  let label = 'no drift';
  if (firing) {
    tone = 'bg-rose-100 text-rose-700 border-rose-300';
    label = `${consecutive}/${threshold} drift probes — firing`;
  } else if (consecutive > 0) {
    tone = 'bg-amber-100 text-amber-700 border-amber-300';
    label = `${consecutive}/${threshold} drift probes`;
  } else {
    label = `0/${threshold} drift probes`;
  }

  const titleParts = [
    firing
      ? `Drift watchdog is firing — ${consecutive} consecutive divergent probes (threshold ${threshold}). Slack alert has been dispatched.`
      : consecutive > 0
        ? `Staging has drifted from production for ${consecutive} of ${threshold} consecutive probes. Slack alert fires at ${threshold}.`
        : `Staging matches production. Slack alert fires after ${threshold} consecutive divergent probes.`,
  ];
  if (Array.isArray(fields) && fields.length > 0) {
    titleParts.push(`Diverging fields: ${fields.join(', ')}.`);
  }

  return (
    <span
      className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border tabular-nums shrink-0 ${tone}`}
      data-testid="embed-stack-drift-badge"
      title={titleParts.join(' ')}
    >
      {label}
    </span>
  );
}

function LegPill({ leg, pill }) {
  const ok = !!pill?.ok;
  const failures = Number(pill?.consecutive_failures || 0);
  const threshold = Number(pill?.alert_threshold || 3);
  const firing = !!pill?.firing;
  const reason = pill?.reason || pill?.error;
  const latency = pill?.latency_ms;

  // Badge tone:
  //  • firing → red
  //  • partial failures (1..threshold-1) → amber (warm-up window)
  //  • clean → emerald
  let badgeTone = 'bg-emerald-100 text-emerald-700 border-emerald-200';
  if (firing) {
    badgeTone = 'bg-rose-100 text-rose-700 border-rose-300';
  } else if (failures > 0) {
    badgeTone = 'bg-amber-100 text-amber-700 border-amber-300';
  }

  const headerTone = ok && !firing
    ? 'bg-emerald-50 border-emerald-200'
    : firing
      ? 'bg-rose-50 border-rose-300'
      : 'bg-amber-50 border-amber-200';

  const Icon = ok && !firing ? CheckCircle2 : AlertTriangle;
  const iconTone = ok && !firing
    ? 'text-emerald-500'
    : firing ? 'text-rose-500' : 'text-amber-500';

  return (
    <div
      className={`rounded-xl border px-3 py-2 ${headerTone}`}
      data-testid={`embed-stack-leg-${leg.key}`}
    >
      <div className="flex items-center gap-2">
        <Icon size={16} className={`shrink-0 ${iconTone}`} />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-gray-900 truncate">
            {leg.label}
          </p>
          <p className="text-[10px] text-gray-500 truncate">{leg.sub}</p>
        </div>
        <span
          className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border tabular-nums ${badgeTone}`}
          data-testid={`embed-stack-leg-${leg.key}-failures`}
          title={firing
            ? `Watchdog is firing — ${failures} consecutive failures (threshold ${threshold}).`
            : failures > 0
              ? `Probe has failed ${failures} of ${threshold} consecutive times. Watchdog pages at ${threshold}.`
              : `No recent probe failures (threshold ${threshold}).`}
        >
          {failures}/{threshold} consecutive failures
        </span>
      </div>
      {(reason || latency != null) && (
        <p className="mt-1 text-[10px] text-gray-500 truncate">
          {latency != null ? `${latency} ms` : ''}
          {latency != null && reason ? ' · ' : ''}
          {reason || ''}
        </p>
      )}
    </div>
  );
}

export default function EmbedStackHealthPill({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const inFlightRef = useRef(false);

  const load = useCallback(async () => {
    // Guard against overlapping requests — if a poll is still in flight
    // when the next interval tick (or visibility resume) fires, skip it.
    if (inFlightRef.current) return;
    inFlightRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(
        `${API_BASE}/admin/health/embed-stack`,
        { headers: adminHeaders(adminToken), withCredentials: true },
      );
      setData(res.data || null);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to load');
    } finally {
      inFlightRef.current = false;
      setLoading(false);
    }
  }, [adminToken]);

  // Task #470 — auto-refresh every ~30s while the dashboard tab is
  // visible so on-call sees recovery (or fresh failures) without
  // clicking the refresh icon. Polling pauses when the tab is hidden
  // (Page Visibility API) to avoid burning admin requests, and the
  // manual refresh button still works and resets the timer.
  const POLL_MS = 30_000;
  const timerRef = useRef(null);

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const startTimer = useCallback(() => {
    clearTimer();
    if (typeof document !== 'undefined' && document.hidden) return;
    timerRef.current = setInterval(() => { load(); }, POLL_MS);
  }, [clearTimer, load]);

  const refreshNow = useCallback(() => {
    load();
    startTimer();
  }, [load, startTimer]);

  useEffect(() => {
    load();
    startTimer();
    const onVis = () => {
      if (typeof document !== 'undefined' && document.hidden) {
        clearTimer();
      } else {
        load();
        startTimer();
      }
    };
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', onVis);
    }
    return () => {
      clearTimer();
      if (typeof document !== 'undefined') {
        document.removeEventListener('visibilitychange', onVis);
      }
    };
  }, [load, startTimer, clearTimer]);

  const overallOk = !!data?.ok;
  const tone = overallOk
    ? { tile: 'bg-emerald-50 border-emerald-200', icon: 'bg-emerald-100 text-emerald-500', heading: 'text-emerald-700' }
    : { tile: 'bg-rose-50 border-rose-200',       icon: 'bg-rose-100 text-rose-500',       heading: 'text-rose-700' };

  const pills = {
    embed:  data?.embed,
    rerank: data?.rerank,
    memory: data?.memory,
  };

  // Task #438 — staging worker side-by-side with production. Falls back
  // to a single production-only entry built from the legacy `embed`
  // field if the backend hasn't shipped `embed_environments` yet.
  const embedEnvs = Array.isArray(data?.embed_environments) && data.embed_environments.length > 0
    ? data.embed_environments
    : (data?.embed
        ? [{ ...data.embed, env: 'production', label: 'Production', pages: true }]
        : []);

  return (
    <div
      className={`rounded-2xl p-4 border ${tone.tile}`}
      data-testid="embed-stack-health-tile"
    >
      <div className="flex items-center gap-3 mb-3">
        <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${tone.icon}`}>
          <Activity size={17} />
        </div>
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold ${tone.heading}`}>
            Embed stack — health pill (Task #436)
          </p>
          <p className="text-[11px] text-gray-500 mt-0.5">
            Live watchdog state for the embed / rerank / memory_brain legs.
          </p>
        </div>
        <button
          onClick={refreshNow}
          disabled={loading}
          className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-white"
          data-testid="button-refresh-embed-stack-health"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {error && (
        <p className="text-xs text-rose-600" data-testid="embed-stack-health-error">
          {error}
        </p>
      )}

      {!error && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {LEGS.map((leg) => (
            <LegPill key={leg.key} leg={leg} pill={pills[leg.key]} />
          ))}
        </div>
      )}

      {!error && embedEnvs.length > 0 && (
        <div
          className="mt-3 pt-3 border-t border-white/60"
          data-testid="embed-stack-environments"
        >
          <div className="flex items-center justify-between mb-2 gap-2">
            <p className="text-[11px] font-semibold text-gray-700">
              Embed workers — by environment
              <span className="ml-2 text-[10px] font-normal text-gray-500">
                (Task #438 — staging canary visible alongside production)
              </span>
            </p>
            <DriftBadge drift={data?.drift_state} />
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {embedEnvs.map((envInfo) => (
              <EmbedEnvRow key={envInfo.env || envInfo.label} envInfo={envInfo} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
