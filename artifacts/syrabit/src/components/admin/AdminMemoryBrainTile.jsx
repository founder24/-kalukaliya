/**
 * Task #417 — memory_brain hot-path observability tile.
 *
 * Polls `/api/admin/memory-brain/metrics` every 30s and renders three
 * counters (writes / reads / failures) plus a 24h sparkline so the
 * operator can tell at a glance whether the best-effort try/except
 * wrappers in `memory_brain_chat.py` are hiding a Voyage / Mongo
 * outage.
 *
 * The companion alert lives in `metrics._alerting_loop` and pages
 * on `memory_brain_failure_rate_pct`. The banner here mirrors that
 * threshold visually so the dashboard turns red the same instant
 * the alert would fire.
 */
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { RefreshCw, AlertTriangle, ShieldCheck, Brain } from 'lucide-react';
import { API_BASE } from '@/utils/api';
// Default banner threshold + min sample. The backend response also
// carries the live operator-tuned values under `alert_threshold` so
// the banner stays in lockstep with what would actually page on-call.
const DEFAULT_FAILURE_RATE_PCT = 25;
const DEFAULT_FAILURE_MIN_SAMPLE = 20;
const POLL_MS = 30_000;

function _fmtTs(ts) {
  if (!ts) return '—';
  try { return new Date(ts * 1000).toLocaleTimeString(); } catch { return '—'; }
}

export default function AdminMemoryBrainTile({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState(null);

  const load = useCallback(async () => {
    if (!adminToken) return;
    setLoading(true);
    try {
      const r = await axios.get(`${API_BASE}/admin/memory-brain/metrics`, {
        headers: { Authorization: `Bearer ${adminToken}` },
        params: { window_seconds: 24 * 3600, hours: 24 },
        timeout: 8000,
      });
      setData(r.data);
      setErr(null);
    } catch (e) {
      setErr(e?.response?.data?.detail || e?.message || 'fetch failed');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!adminToken) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load, adminToken]);

  const stats = data?.stats || {};
  const buckets = data?.buckets || [];
  const enabled = data?.feature_enabled !== false;
  const failPct = Number(stats.failure_rate_pct || 0);
  const bannerPct = Number(data?.alert_threshold?.failure_rate_pct ?? DEFAULT_FAILURE_RATE_PCT);
  const minSample = Number(data?.alert_threshold?.failure_min_sample ?? DEFAULT_FAILURE_MIN_SAMPLE);
  const tripped = enabled && stats.total >= minSample && failPct > bannerPct;

  const chartData = buckets.map(b => ({
    label: new Date(b.hour_start_ts * 1000).getHours() + 'h',
    writes: (b.writes_ok || 0) + (b.writes_fail || 0),
    reads: (b.reads_ok || 0) + (b.reads_fail || 0),
    failures: (b.writes_fail || 0) + (b.reads_fail || 0),
  }));

  const writeStats = stats.by_op?.write || { ok: 0, fail: 0, total: 0 };
  const readStats = stats.by_op?.read || { ok: 0, fail: 0, total: 0 };
  const qa = stats.by_kind?.qa || { ok: 0, fail: 0, total: 0 };
  const fact = stats.by_kind?.fact || { ok: 0, fail: 0, total: 0 };

  return (
    <div
      className={`rounded-2xl border p-4 ${
        !enabled ? 'border-gray-200 bg-gray-50' :
        tripped ? 'border-red-200 bg-red-50' :
        'border-violet-100 bg-white'
      }`}
      data-testid="memory-brain-tile"
    >
      <div className="flex items-center gap-2 mb-3">
        <Brain size={16} className="text-violet-500" />
        <h3 className="text-sm font-semibold text-gray-700">Memory Brain (last 24h)</h3>
        {!enabled && (
          <span className="ml-2 text-[10px] uppercase tracking-wide text-gray-400 px-2 py-0.5 bg-gray-100 rounded-full">
            disabled
          </span>
        )}
        {tripped && (
          <span className="ml-2 inline-flex items-center gap-1 text-[10px] uppercase tracking-wide text-red-600 font-semibold">
            <AlertTriangle size={11} /> failure rate high
          </span>
        )}
        {!tripped && enabled && stats.total > 0 && (
          <span className="ml-2 inline-flex items-center gap-1 text-[10px] uppercase tracking-wide text-emerald-600 font-semibold">
            <ShieldCheck size={11} /> healthy
          </span>
        )}
        <button
          onClick={load}
          disabled={loading}
          className="ml-auto p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100"
          data-testid="memory-brain-tile-refresh"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {err && (
        <p className="text-xs text-red-500 mb-2">Failed to load: {String(err)}</p>
      )}

      <div className="grid grid-cols-3 gap-3 mb-3">
        <div className="rounded-lg bg-gray-50 px-3 py-2">
          <p className="text-[10px] uppercase tracking-wide text-gray-400">Writes</p>
          <p className="text-lg font-semibold text-gray-700" data-testid="memory-brain-writes">
            {writeStats.total}
          </p>
          <p className="text-[10px] text-gray-400">
            qa {qa.total} · fact {fact.total}
          </p>
        </div>
        <div className="rounded-lg bg-gray-50 px-3 py-2">
          <p className="text-[10px] uppercase tracking-wide text-gray-400">Reads</p>
          <p className="text-lg font-semibold text-gray-700" data-testid="memory-brain-reads">
            {readStats.total}
          </p>
          <p className="text-[10px] text-gray-400">queries</p>
        </div>
        <div className="rounded-lg bg-gray-50 px-3 py-2">
          <p className="text-[10px] uppercase tracking-wide text-gray-400">Failures</p>
          <p className={`text-lg font-semibold ${tripped ? 'text-red-600' : 'text-gray-700'}`} data-testid="memory-brain-failures">
            {stats.failures || 0}
          </p>
          <p className={`text-[10px] ${tripped ? 'text-red-500' : 'text-gray-400'}`}>
            {failPct.toFixed(1)}% rate
          </p>
        </div>
      </div>

      {chartData.length > 0 && (
        <div className="h-24 -mx-1">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={chartData}>
              <defs>
                <linearGradient id="mb-writes" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.4} />
                  <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="mb-reads" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#10b981" stopOpacity={0.4} />
                  <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="mb-fail" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#ef4444" stopOpacity={0.5} />
                  <stop offset="100%" stopColor="#ef4444" stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="label" hide />
              <YAxis hide />
              <Tooltip
                contentStyle={{ fontSize: 11, borderRadius: 8 }}
                labelStyle={{ fontSize: 10, color: '#888' }}
              />
              <Area type="monotone" dataKey="writes" stroke="#8b5cf6" strokeWidth={1.5} fill="url(#mb-writes)" />
              <Area type="monotone" dataKey="reads"  stroke="#10b981" strokeWidth={1.5} fill="url(#mb-reads)" />
              <Area type="monotone" dataKey="failures" stroke="#ef4444" strokeWidth={1.5} fill="url(#mb-fail)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {(stats.top_failure_reasons || []).length > 0 && (
        <div className="mt-3">
          <p className="text-[10px] uppercase tracking-wide text-gray-400 mb-1">Top failure reasons</p>
          <ul className="text-xs text-gray-600 space-y-0.5">
            {stats.top_failure_reasons.map(r => (
              <li key={r.reason} className="flex justify-between">
                <span className="font-mono text-[11px]">{r.reason}</span>
                <span className="text-gray-400">{r.count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="text-[10px] text-gray-400 mt-3">
        worker pid {data?.worker_pid ?? '—'} · per-worker counts ·
        last ok {_fmtTs(stats.last_success_ts)} ·
        last fail {_fmtTs(stats.last_failure_ts)}
      </p>
    </div>
  );
}
