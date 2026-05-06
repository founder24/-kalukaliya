import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { Database, RefreshCw } from 'lucide-react';
import { API_BASE } from '@/utils/api';

// Task #433 — small dashboard tile for the legacy → workers_ai_custom
// embed backfill (Task #411). Surfaces the overall percent-complete
// number plus a per-old-provider breakdown of the chunks that are
// still pending (e.g. cohere=12k, voyage=800, (missing)=50). Without
// the breakdown the dashboard treats every legacy chunk as one opaque
// bucket and on-call can't tell whether the remaining backlog is
// mostly Cohere (safe to defer) or something more drift-prone.
//
// Endpoint: GET /admin/embed/backfill/progress
// Reuses the same payload the run-trigger endpoint poll relies on, so
// no new backend surface is needed.

const adminHeaders = (token) => {
  const h = { 'Content-Type': 'application/json' };
  if (token) h['X-Admin-Token'] = token;
  return h;
};

const fmtNum = (n) => {
  if (typeof n !== 'number' || !isFinite(n)) return '—';
  return n.toLocaleString();
};

// Task #466 — render an ETA in a human-friendly form. The backend gives
// us seconds; we collapse to "Xd Yh", "Xh Ym", "Xm", or "<1m" depending
// on magnitude so the pill stays compact even when the backlog is days
// out.
const fmtEta = (seconds) => {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return null;
  if (seconds === 0) return 'done';
  if (seconds < 60) return '<1m';
  const mins = Math.round(seconds / 60);
  if (mins < 60) return `${mins}m`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  if (hours < 24) {
    return remMins ? `${hours}h ${remMins}m` : `${hours}h`;
  }
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return remHours ? `${days}d ${remHours}h` : `${days}d`;
};

const fmtRate = (cpm) => {
  if (typeof cpm !== 'number' || !isFinite(cpm) || cpm <= 0) return null;
  if (cpm >= 100) return `${Math.round(cpm).toLocaleString()} chunks/min`;
  return `${cpm.toFixed(1)} chunks/min`;
};

export default function EmbedBackfillPill({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(
        `${API_BASE}/admin/embed/backfill/progress`,
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

  const remainingBySource = (data && data.remaining_by_source) || {};
  const sourceEntries = Object.entries(remainingBySource)
    .sort((a, b) => (b[1] || 0) - (a[1] || 0));
  const remaining = data?.remaining ?? 0;
  const total = data?.total_chunks ?? 0;
  const pct = typeof data?.percent === 'number' ? data.percent : 0;
  const cpm = data?.throughput?.chunks_per_min;
  const rateLabel = fmtRate(cpm);
  const etaLabel = fmtEta(data?.eta_seconds);
  const tone = remaining === 0 && total > 0 ? 'emerald' : 'sky';
  const colors = tone === 'emerald'
    ? { tile: 'bg-emerald-50 border-emerald-200', icon: 'bg-emerald-100 text-emerald-500', heading: 'text-emerald-600' }
    : { tile: 'bg-sky-50 border-sky-200', icon: 'bg-sky-100 text-sky-500', heading: 'text-sky-700' };

  return (
    <div
      className={`rounded-2xl p-4 border ${colors.tile}`}
      data-testid="embed-backfill-tile"
    >
      <div className="flex items-center gap-3 mb-3">
        <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${colors.icon}`}>
          <Database size={17} />
        </div>
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold ${colors.heading}`}>
            Embed backfill — workers_ai_custom (Task #411)
          </p>
          <p className="text-[11px] text-gray-500 mt-0.5">
            Re-embeds legacy Cohere / Voyage chunks through the new Workers-AI custom worker.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-white"
          data-testid="button-refresh-embed-backfill"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {error && (
        <p className="text-xs text-rose-600" data-testid="embed-backfill-error">
          {error}
        </p>
      )}

      {!error && (
        <>
          <div className="flex items-baseline gap-3 flex-wrap">
            <span
              className="text-2xl font-semibold text-gray-900"
              data-testid="embed-backfill-percent"
            >
              {pct.toFixed(1)}%
            </span>
            {(rateLabel || etaLabel) && (
              <span
                className="text-xs font-medium text-gray-700"
                data-testid="embed-backfill-throughput"
              >
                {rateLabel && (
                  <span data-testid="embed-backfill-rate">{rateLabel}</span>
                )}
                {rateLabel && etaLabel && ' · '}
                {etaLabel && (
                  <>
                    ETA{' '}
                    <span data-testid="embed-backfill-eta">{etaLabel}</span>
                  </>
                )}
              </span>
            )}
            {!rateLabel && !etaLabel && remaining > 0 && (
              <span
                className="text-xs text-gray-400 italic"
                data-testid="embed-backfill-throughput-pending"
              >
                throughput pending…
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500 mt-1">
            {fmtNum(data?.re_embedded ?? 0)} / {fmtNum(total)} chunks re-embedded
            {' · '}
            <span data-testid="embed-backfill-remaining">{fmtNum(remaining)}</span> pending
          </p>

          <div className="mt-3" data-testid="embed-backfill-by-source">
            <p className="text-[11px] uppercase tracking-wider text-gray-500 mb-1">
              Pending by old source
            </p>
            {sourceEntries.length === 0 ? (
              <p className="text-xs text-gray-500" data-testid="embed-backfill-by-source-empty">
                {remaining === 0
                  ? 'All chunks migrated.'
                  : '(breakdown unavailable)'}
              </p>
            ) : (
              <ul className="text-xs text-gray-700 space-y-1">
                {sourceEntries.map(([src, n]) => (
                  <li
                    key={src}
                    className="flex justify-between gap-3"
                    data-testid={`embed-backfill-source-${src}`}
                  >
                    <span className="font-mono">{src}</span>
                    <span className="tabular-nums">{fmtNum(n)}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}
