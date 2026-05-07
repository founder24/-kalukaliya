/**
 * Task #571 — Admin Observability cache hit-ratio panel.
 *
 * Polls `GET /api/health/cache` every 60 s (overridable via the
 * `refreshMs` prop for tests) and renders:
 *
 *   - Per-content-type AI-input-cache rows: hit-ratio, sets,
 *     unique_keys_24h cardinality, top miss-reason.
 *   - Per-layer rollups: ai_response_cache, rag_cache.
 *   - Per-L1 ring rollups: hits / misses / hit-rate / saturation.
 *   - Edge advisory targets parsed from monitored-urls.json.
 *
 * The panel renders red when any per-content-type hit-ratio drops
 * below the alarm floor returned in `alarm_thresholds.ai_cache_hit_ratio_floor`
 * (the same value the `cache-ai-hitratio-low` CloudWatch alarm uses)
 * so an operator who is on the page can confirm a paging alert
 * without flipping tabs.
 *
 * Auth: the parent admin dashboard already provides `adminToken`; we
 * pass it through the standard `Bearer` header. The endpoint is
 * `get_admin_user`-gated server-side.
 */
import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import { API_BASE } from '@/utils/api';

const DEFAULT_REFRESH_MS = 60_000;

function fmtPct(v) {
  if (v == null || Number.isNaN(v)) return '—';
  return `${(Math.max(0, Math.min(1, v)) * 100).toFixed(1)}%`;
}

function fmtInt(v) {
  if (v == null || Number.isNaN(v)) return '—';
  return Number(v).toLocaleString();
}

function topMissReason(reasons) {
  if (!reasons || typeof reasons !== 'object') return null;
  let best = null;
  let bestN = -1;
  for (const [k, n] of Object.entries(reasons)) {
    if (typeof n === 'number' && n > bestN) {
      best = k;
      bestN = n;
    }
  }
  return best ? { reason: best, count: bestN } : null;
}

export default function CacheHitRatioPanel({
  adminToken,
  refreshMs = DEFAULT_REFRESH_MS,
}) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [lastFetchAt, setLastFetchAt] = useState(null);

  useEffect(() => {
    if (!adminToken) return undefined;
    let cancelled = false;
    const load = async () => {
      try {
        const res = await axios.get(`${API_BASE}/health/cache`, {
          headers: { Authorization: `Bearer ${adminToken}` },
          timeout: 8000,
        });
        if (cancelled) return;
        setData(res.data);
        setError(null);
        setLastFetchAt(Date.now());
      } catch (e) {
        if (cancelled) return;
        setError(e?.response?.data?.detail || e?.message || 'fetch failed');
      }
    };
    load();
    const t = setInterval(load, refreshMs);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [adminToken, refreshMs]);

  const floor = data?.alarm_thresholds?.ai_cache_hit_ratio_floor ?? 0.3;
  const aic = data?.ai_input_cache;
  const totals = aic?.totals;
  const cts = aic?.content_types || {};

  const ctRows = useMemo(
    () => Object.entries(cts).sort((a, b) => a[0].localeCompare(b[0])),
    [cts],
  );

  if (!adminToken) {
    return (
      <div data-testid="cache-hit-ratio-panel" className="text-[10px] text-gray-400">
        Cache panel requires admin auth.
      </div>
    );
  }

  if (data === null && !error) {
    return (
      <div data-testid="cache-hit-ratio-panel" className="text-[10px] text-gray-400">
        Loading cache health…
      </div>
    );
  }

  return (
    <div
      data-testid="cache-hit-ratio-panel"
      className="mb-3 pb-3 border-b border-gray-200"
    >
      <div className="flex items-center justify-between mb-1.5">
        <label className="text-[10px] text-gray-500 font-medium">
          Cache hit-ratio (Task #571)
        </label>
        <span className="text-[10px] text-gray-400">
          refresh {Math.round(refreshMs / 1000)}s · floor {fmtPct(floor)}
          {lastFetchAt && (
            <> · {new Date(lastFetchAt).toLocaleTimeString()}</>
          )}
        </span>
      </div>

      {error && (
        <div
          className="text-[10px] text-red-600 mb-2"
          data-testid="cache-hit-ratio-panel-error"
        >
          Cache health fetch failed: {String(error)}
        </div>
      )}

      {/* Total rollup */}
      {totals && (
        <div
          className="rounded-md ring-1 ring-gray-200 bg-gray-50 px-2 py-1.5 mb-2"
          data-testid="cache-panel-totals"
        >
          <div className="flex items-baseline gap-3">
            <span className="text-[10px] text-gray-500">AI-input cache total</span>
            <span
              className={`text-lg font-semibold tabular-nums ${
                (totals.hit_ratio ?? 0) < floor ? 'text-red-600' : 'text-emerald-700'
              }`}
              data-testid="cache-panel-totals-hitratio"
            >
              {fmtPct(totals.hit_ratio)}
            </span>
            <span className="text-[10px] text-gray-500">
              hits {fmtInt(totals.hits)} · misses {fmtInt(totals.misses)} ·
              sets {fmtInt(totals.sets)} · keys24h {fmtInt(totals.unique_keys_24h)}
            </span>
          </div>
        </div>
      )}

      {/* Per content type */}
      {ctRows.length > 0 && (
        <table
          className="w-full text-[10px] text-gray-700 mb-2"
          data-testid="cache-panel-ct-table"
        >
          <thead>
            <tr className="text-gray-500">
              <th className="text-left font-normal pr-2">Content type</th>
              <th className="text-right font-normal pr-2">Hit-ratio</th>
              <th className="text-right font-normal pr-2">Hits</th>
              <th className="text-right font-normal pr-2">Misses</th>
              <th className="text-right font-normal pr-2">Sets</th>
              <th className="text-right font-normal pr-2">Keys 24h</th>
              <th className="text-left font-normal">Top miss</th>
            </tr>
          </thead>
          <tbody>
            {ctRows.map(([ct, row]) => {
              const hr = row.hit_ratio ?? 0;
              const tone = hr < floor ? 'text-red-600 font-medium' : '';
              const top = topMissReason(row.miss_reasons);
              return (
                <tr
                  key={ct}
                  data-testid={`cache-panel-ct-${ct}`}
                  className="border-t border-gray-100"
                >
                  <td className="pr-2 py-0.5 font-mono">{ct}</td>
                  <td className={`text-right tabular-nums pr-2 ${tone}`}>
                    {fmtPct(hr)}
                  </td>
                  <td className="text-right tabular-nums pr-2">{fmtInt(row.hits)}</td>
                  <td className="text-right tabular-nums pr-2">{fmtInt(row.misses)}</td>
                  <td className="text-right tabular-nums pr-2">{fmtInt(row.sets)}</td>
                  <td className="text-right tabular-nums pr-2">
                    {fmtInt(row.unique_keys_24h)}
                  </td>
                  <td className="text-gray-500">
                    {top ? `${top.reason} (${top.count})` : '—'}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {/* Per-layer rollup */}
      <div className="grid grid-cols-2 gap-2 mb-2">
        <LayerCard
          title="ai_response_cache"
          row={data?.ai_response_cache}
          floor={floor}
        />
        <LayerCard title="rag_cache" row={data?.rag_cache} floor={floor} />
      </div>

      {/* L1 rings */}
      {data?.l1_inproc && Object.keys(data.l1_inproc).length > 0 && (
        <details
          className="text-[10px] text-gray-600"
          data-testid="cache-panel-l1"
        >
          <summary className="cursor-pointer text-gray-500">
            L1 in-process rings ({Object.keys(data.l1_inproc).length})
          </summary>
          <table className="w-full mt-1">
            <thead>
              <tr className="text-gray-500">
                <th className="text-left font-normal pr-2">Ring</th>
                <th className="text-right font-normal pr-2">Hit-rate</th>
                <th className="text-right font-normal pr-2">Hits</th>
                <th className="text-right font-normal pr-2">Misses</th>
                <th className="text-right font-normal pr-2">Saturation</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.l1_inproc).map(([name, row]) => {
                const cap = row.maxsize || 0;
                const sat = cap ? (row.currsize || 0) / cap : 0;
                return (
                  <tr
                    key={name}
                    className="border-t border-gray-100"
                    data-testid={`cache-panel-l1-${name}`}
                  >
                    <td className="pr-2 py-0.5 font-mono">{name}</td>
                    <td className="text-right tabular-nums pr-2">
                      {row.hit_rate == null ? '—' : fmtPct(row.hit_rate)}
                    </td>
                    <td className="text-right tabular-nums pr-2">{fmtInt(row.hits)}</td>
                    <td className="text-right tabular-nums pr-2">
                      {fmtInt(row.misses)}
                    </td>
                    <td
                      className={`text-right tabular-nums pr-2 ${
                        sat > 0.95 ? 'text-amber-600' : ''
                      }`}
                    >
                      {fmtPct(sat)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </details>
      )}

      {/* Edge advisory targets — read-only */}
      {Array.isArray(data?.edge_targets) && data.edge_targets.length > 0 && (
        <details
          className="text-[10px] text-gray-600 mt-1"
          data-testid="cache-panel-edge-targets"
        >
          <summary className="cursor-pointer text-gray-500">
            Edge advisory targets ({data.edge_targets.length})
          </summary>
          <table className="w-full mt-1">
            <thead>
              <tr className="text-gray-500">
                <th className="text-left font-normal pr-2">Path</th>
                <th className="text-right font-normal pr-2">TTL (s)</th>
                <th className="text-right font-normal pr-2">Target</th>
                <th className="text-right font-normal pr-2">Live (CF)</th>
                <th className="text-left font-normal">User-keyed</th>
              </tr>
            </thead>
            <tbody>
              {data.edge_targets.map((t) => (
                <tr key={t.path} className="border-t border-gray-100">
                  <td className="pr-2 py-0.5 font-mono">{t.path}</td>
                  <td className="text-right tabular-nums pr-2">
                    {fmtInt(t.ttl_seconds)}
                  </td>
                  <td className="text-right tabular-nums pr-2">
                    {fmtPct(t.cache_hit_ratio_target)}
                  </td>
                  <td
                    className={`text-right tabular-nums pr-2 ${
                      t.live_hit_rate != null &&
                      t.cache_hit_ratio_target != null &&
                      t.live_hit_rate < t.cache_hit_ratio_target
                        ? 'text-red-600 font-medium'
                        : ''
                    }`}
                    data-testid={`cache-panel-edge-live-${t.path}`}
                  >
                    {t.live_hit_rate == null ? '—' : fmtPct(t.live_hit_rate)}
                  </td>
                  <td>{t.user_keyed ? 'yes' : 'no'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}
    </div>
  );
}

function LayerCard({ title, row, floor }) {
  if (!row) {
    return (
      <div
        className="rounded ring-1 ring-gray-200 bg-gray-50 px-2 py-1.5"
        data-testid={`cache-panel-layer-${title}`}
      >
        <div className="text-[10px] text-gray-500">{title}</div>
        <div className="text-[10px] text-gray-400">unavailable</div>
      </div>
    );
  }
  const hr = row.hit_rate ?? 0;
  const tone = hr < floor ? 'text-red-600' : 'text-emerald-700';
  return (
    <div
      className="rounded ring-1 ring-gray-200 bg-white px-2 py-1.5"
      data-testid={`cache-panel-layer-${title}`}
    >
      <div className="flex items-baseline justify-between">
        <span className="text-[10px] text-gray-500">{title}</span>
        <span
          className={`text-base font-semibold tabular-nums ${tone}`}
          data-testid={`cache-panel-layer-${title}-rate`}
        >
          {fmtPct(hr)}
        </span>
      </div>
      <div className="text-[10px] text-gray-500">
        hits {fmtInt(row.hits)} · misses {fmtInt(row.misses)}
      </div>
    </div>
  );
}
