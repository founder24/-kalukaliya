import React, { useCallback, useEffect, useState } from 'react';
import axios from 'axios';
import { Languages, RefreshCw } from 'lucide-react';
import { API_BASE } from '@/utils/api';

// Task #45 — admin tile for the Assamese corpus coverage gate.
// Endpoint: GET /api/health/corpus/assamese
//
// Renders the four largest collections (subjects, chapters, seo_pages,
// pyq_html_pages) with a target line at 0.85 plus the latest run's
// accept/reject breakdown so on-call can see WHY a collection's
// coverage isn't moving (translator returning low-ratio output vs
// translator timing out).

const adminHeaders = (token) => {
  const h = { 'Content-Type': 'application/json' };
  if (token) h['X-Admin-Token'] = token;
  return h;
};

const fmtNum = (n) => {
  if (typeof n !== 'number' || !isFinite(n)) return '—';
  return n.toLocaleString();
};

const fmtPct = (r) => {
  if (typeof r !== 'number' || !isFinite(r)) return '—';
  return `${(r * 100).toFixed(1)}%`;
};

export default function AssameseCorpusCoveragePill({ adminToken }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(
        `${API_BASE}/health/corpus/assamese`,
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

  const coverage = data?.coverage || {};
  const rows = coverage.collections || [];
  const target = data?.target_ratio ?? 0.85;
  const floor = data?.alarm_floor ?? 0.80;
  const overall = coverage.overall_ratio ?? 0;
  const lastRun = data?.last_run || null;
  const lastResults = (lastRun && lastRun.results) || [];
  const rejectReasonsAgg = {};
  let acceptedTotal = 0;
  let rejectedTotal = 0;
  for (const r of lastResults) {
    acceptedTotal += Number(r.accepted || 0);
    rejectedTotal += Number(
      (r.rejected_low_ratio || 0)
      + (r.rejected_empty || 0)
      + (r.rejected_exception || 0),
    );
    const reasons = r.reject_reasons || {};
    for (const [k, v] of Object.entries(reasons)) {
      rejectReasonsAgg[k] = (rejectReasonsAgg[k] || 0) + Number(v || 0);
    }
  }

  const overallTone = overall >= target
    ? 'emerald'
    : overall >= floor
      ? 'amber'
      : 'rose';
  const colors = {
    emerald: { tile: 'bg-emerald-50 border-emerald-200', icon: 'bg-emerald-100 text-emerald-500', heading: 'text-emerald-700' },
    amber:   { tile: 'bg-amber-50 border-amber-200', icon: 'bg-amber-100 text-amber-600', heading: 'text-amber-700' },
    rose:    { tile: 'bg-rose-50 border-rose-200', icon: 'bg-rose-100 text-rose-500', heading: 'text-rose-700' },
  }[overallTone];

  const rowTone = (ratio) => {
    if (ratio >= target) return 'text-emerald-700';
    if (ratio >= floor)  return 'text-amber-700';
    return 'text-rose-700';
  };

  return (
    <div
      className={`rounded-2xl p-4 border ${colors.tile}`}
      data-testid="assamese-corpus-tile"
    >
      <div className="flex items-center gap-3 mb-3">
        <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${colors.icon}`}>
          <Languages size={17} />
        </div>
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold ${colors.heading}`}>
            Assamese corpus coverage (Task #45)
          </p>
          <p className="text-[11px] text-gray-500 mt-0.5">
            Target {fmtPct(target)} of script ratio ≥ 0.85 across the 4 largest collections.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-white"
          data-testid="button-refresh-assamese-corpus"
        >
          <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
        </button>
      </div>

      {error && (
        <p className="text-xs text-rose-600" data-testid="assamese-corpus-error">
          {error}
        </p>
      )}

      {!error && (
        <>
          <div className="flex items-baseline gap-3 flex-wrap">
            <span
              className="text-2xl font-semibold text-gray-900"
              data-testid="assamese-corpus-overall"
            >
              {fmtPct(overall)}
            </span>
            <span className="text-xs text-gray-600">
              target <span data-testid="assamese-corpus-target">{fmtPct(target)}</span>
              {' · '}
              floor <span data-testid="assamese-corpus-floor">{fmtPct(floor)}</span>
            </span>
          </div>

          <div className="mt-3" data-testid="assamese-corpus-by-collection">
            <p className="text-[11px] uppercase tracking-wider text-gray-500 mb-1">
              Per-collection coverage
            </p>
            {rows.length === 0 ? (
              <p className="text-xs text-gray-500" data-testid="assamese-corpus-empty">
                No coverage data yet.
              </p>
            ) : (
              <ul className="text-xs space-y-1">
                {rows.map((r) => {
                  const aiCacheOnly = r.status === 'ai_input_cache_only';
                  return (
                    <li
                      key={r.collection}
                      className="flex justify-between gap-3"
                      data-testid={`assamese-corpus-row-${r.collection}`}
                    >
                      <span className="font-mono text-gray-700">
                        {r.collection}
                        <span className="text-gray-400">
                          {' '}· {aiCacheOnly ? 'ai_input_cache' : (r.field || '—')}
                        </span>
                      </span>
                      {aiCacheOnly ? (
                        <span
                          className="text-gray-500 italic"
                          data-testid={`assamese-corpus-ratio-${r.collection}`}
                        >
                          tracked via /api/health/cache
                        </span>
                      ) : (
                        <span className="tabular-nums text-gray-600">
                          <span
                            className={`font-semibold ${rowTone(r.ratio)}`}
                            data-testid={`assamese-corpus-ratio-${r.collection}`}
                          >
                            {fmtPct(r.ratio)}
                          </span>
                          {' '}({fmtNum(r.translated_docs)} / {fmtNum(r.total_docs)})
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <div className="mt-3" data-testid="assamese-corpus-last-run">
            <p className="text-[11px] uppercase tracking-wider text-gray-500 mb-1">
              Last backfill — accept / reject
            </p>
            {!lastRun ? (
              <p className="text-xs text-gray-500" data-testid="assamese-corpus-last-run-empty">
                No run report yet.
              </p>
            ) : (
              <>
                <p className="text-xs text-gray-700">
                  <span data-testid="assamese-corpus-accepted">{fmtNum(acceptedTotal)}</span>
                  {' accepted · '}
                  <span data-testid="assamese-corpus-rejected">{fmtNum(rejectedTotal)}</span>
                  {' rejected'}
                </p>
                {Object.keys(rejectReasonsAgg).length > 0 && (
                  <ul className="text-xs text-gray-600 mt-1 space-y-0.5">
                    {Object.entries(rejectReasonsAgg)
                      .sort((a, b) => (b[1] || 0) - (a[1] || 0))
                      .map(([reason, n]) => (
                        <li
                          key={reason}
                          className="flex justify-between gap-3"
                          data-testid={`assamese-corpus-reason-${reason}`}
                        >
                          <span className="font-mono">{reason}</span>
                          <span className="tabular-nums">{fmtNum(n)}</span>
                        </li>
                      ))}
                  </ul>
                )}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}
