import React, { useCallback, useEffect, useState } from 'react';
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

  const load = useCallback(async () => {
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
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  const overallOk = !!data?.ok;
  const tone = overallOk
    ? { tile: 'bg-emerald-50 border-emerald-200', icon: 'bg-emerald-100 text-emerald-500', heading: 'text-emerald-700' }
    : { tile: 'bg-rose-50 border-rose-200',       icon: 'bg-rose-100 text-rose-500',       heading: 'text-rose-700' };

  const pills = {
    embed:  data?.embed,
    rerank: data?.rerank,
    memory: data?.memory,
  };

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
          onClick={load}
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
    </div>
  );
}
