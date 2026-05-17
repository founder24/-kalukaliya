import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import {
  AlertTriangle, CheckCircle2, Languages, Loader2,
  RefreshCw, RotateCcw, Play, ChevronDown, ChevronUp,
} from 'lucide-react';
import { API_BASE } from '@/utils/api';

/**
 * AssameseBackfillPanel — full interactive admin panel for bulk Assamese
 * content regeneration from English pages.
 *
 * Endpoints used:
 *   GET  /api/health/corpus/assamese         — coverage stats + last-run report
 *   GET  /api/admin/corpus/assamese/progress — per-collection running state (fast)
 *   POST /api/admin/corpus/assamese/backfill — trigger a pass
 *
 * The panel polls /progress every 6 s while a job is in flight and refreshes
 * /health every 30 s. A full refresh is also triggered when the job finishes.
 */

const ALL_COLLECTIONS = ['chapters', 'subjects', 'seo_pages', 'pyq_html_pages'];

const COLL_LABELS = {
  chapters:       'Chapter notes',
  subjects:       'Subject descriptions',
  seo_pages:      'SEO pages',
  pyq_html_pages: 'PYQ HTML pages',
};

const fmtPct  = (r) => (typeof r === 'number' && isFinite(r)) ? `${(r * 100).toFixed(1)}%` : '—';
const fmtNum  = (n) => (typeof n === 'number' && isFinite(n)) ? n.toLocaleString() : '—';
const fmtDur  = (s) => {
  if (!s && s !== 0) return '—';
  if (s < 60) return `${Math.round(s)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
};

function ProgressBar({ ratio, target = 0.85, floor = 0.80 }) {
  const pct = Math.min(100, Math.max(0, (ratio ?? 0) * 100));
  const color = ratio >= target
    ? 'bg-emerald-500'
    : ratio >= floor
      ? 'bg-amber-400'
      : 'bg-rose-400';
  return (
    <div className="relative h-2 w-full rounded-full bg-gray-100 overflow-hidden">
      <div
        className={`h-full rounded-full transition-all duration-500 ${color}`}
        style={{ width: `${pct}%` }}
      />
      <div
        className="absolute top-0 bottom-0 w-px bg-gray-400 opacity-50"
        style={{ left: `${target * 100}%` }}
        title={`Target ${fmtPct(target)}`}
      />
    </div>
  );
}

function StatusBadge({ running }) {
  if (running) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-violet-700 bg-violet-50 border border-violet-200 rounded-full px-2 py-0.5">
        <Loader2 size={10} className="animate-spin" /> Running
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-gray-500 bg-gray-50 border border-gray-200 rounded-full px-2 py-0.5">
      Idle
    </span>
  );
}

export default function AssameseBackfillPanel({ adminToken }) {
  const [health, setHealth]     = useState(null);
  const [progress, setProgress] = useState(null);
  const [loadingH, setLoadingH] = useState(false);
  const [loadingP, setLoadingP] = useState(false);
  const [triggering, setTriggering] = useState(false);
  const [lastTrigger, setLastTrigger] = useState(null);
  const [error, setError]       = useState(null);
  const [showLastRun, setShowLastRun] = useState(false);

  const [selectedColls, setSelectedColls] = useState([...ALL_COLLECTIONS]);
  const [maxDocs, setMaxDocs]   = useState(500);
  const [batchSize, setBatchSize] = useState(5);
  const [force, setForce]       = useState(false);

  const pollRef   = useRef(null);
  const healthRef = useRef(null);

  const headers = useCallback(() => {
    const h = { 'Content-Type': 'application/json' };
    if (adminToken) h['X-Admin-Token'] = adminToken;
    return h;
  }, [adminToken]);

  const fetchHealth = useCallback(async () => {
    setLoadingH(true);
    try {
      const res = await axios.get(`${API_BASE}/health/corpus/assamese`, {
        headers: headers(), withCredentials: true,
      });
      setHealth(res.data);
      setError(null);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to load coverage');
    } finally {
      setLoadingH(false);
    }
  }, [headers]);

  const fetchProgress = useCallback(async () => {
    setLoadingP(true);
    try {
      const res = await axios.get(`${API_BASE}/admin/corpus/assamese/progress`, {
        headers: headers(), withCredentials: true,
      });
      setProgress(res.data);
    } catch {
    } finally {
      setLoadingP(false);
    }
  }, [headers]);

  const isRunning = progress?.lock_held
    || Object.values(progress?.collections || {}).some((c) => c.running);

  const stopPolling = useCallback(() => {
    if (pollRef.current)   { clearInterval(pollRef.current);   pollRef.current = null; }
    if (healthRef.current) { clearInterval(healthRef.current); healthRef.current = null; }
  }, []);

  const startPolling = useCallback(() => {
    stopPolling();
    pollRef.current   = setInterval(fetchProgress, 6000);
    healthRef.current = setInterval(fetchHealth,  30000);
  }, [fetchProgress, fetchHealth, stopPolling]);

  useEffect(() => {
    fetchHealth();
    fetchProgress();
    return stopPolling;
  }, [fetchHealth, fetchProgress, stopPolling]);

  useEffect(() => {
    if (isRunning) {
      startPolling();
    } else {
      stopPolling();
    }
  }, [isRunning, startPolling, stopPolling]);

  const trigger = useCallback(async () => {
    setTriggering(true);
    setError(null);
    try {
      const res = await axios.post(
        `${API_BASE}/admin/corpus/assamese/backfill`,
        {
          collections: selectedColls.length === ALL_COLLECTIONS.length ? null : selectedColls,
          max_docs:   maxDocs,
          batch_size: batchSize,
          force,
        },
        { headers: headers(), withCredentials: true },
      );
      setLastTrigger(res.data);
      await fetchProgress();
      startPolling();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Trigger failed');
    } finally {
      setTriggering(false);
    }
  }, [selectedColls, maxDocs, batchSize, force, headers, fetchProgress, startPolling]);

  const toggleColl = (c) =>
    setSelectedColls((prev) =>
      prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c],
    );

  const coverage   = health?.coverage || {};
  const rows       = (coverage.collections || []).filter((r) => r.status !== 'ai_input_cache_only');
  const target     = health?.target_ratio ?? 0.85;
  const floor      = health?.alarm_floor  ?? 0.80;
  const overall    = coverage.overall_ratio ?? 0;
  const lastRun    = health?.last_run || null;
  const lastResults = (lastRun?.results || []);

  const overallTone = overall >= target ? 'emerald' : overall >= floor ? 'amber' : 'rose';
  const tileColor   = { emerald: 'border-emerald-200 bg-emerald-50', amber: 'border-amber-200 bg-amber-50', rose: 'border-rose-200 bg-rose-50' }[overallTone];
  const headColor   = { emerald: 'text-emerald-700', amber: 'text-amber-700', rose: 'text-rose-700' }[overallTone];
  const iconColor   = { emerald: 'bg-emerald-100 text-emerald-500', amber: 'bg-amber-100 text-amber-600', rose: 'bg-rose-100 text-rose-500' }[overallTone];

  const preflight   = lastTrigger?.preflight_warnings || [];

  return (
    <div className={`rounded-2xl border p-5 space-y-5 ${tileColor}`} data-testid="assamese-backfill-panel">

      {/* Header */}
      <div className="flex items-start gap-3">
        <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 ${iconColor}`}>
          <Languages size={17} />
        </div>
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold ${headColor}`}>Assamese content — bulk regeneration</p>
          <p className="text-[11px] text-gray-500 mt-0.5">
            Translates English pages → Assamese via IndicTrans2 + Gemini polish.
            Target script-ratio ≥ {fmtPct(target)} across 4 collections.
          </p>
        </div>
        <div className="flex gap-1">
          <button
            onClick={() => { fetchHealth(); fetchProgress(); }}
            disabled={loadingH || loadingP}
            className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-white"
            title="Refresh"
          >
            <RefreshCw size={14} className={(loadingH || loadingP) ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded-xl px-3 py-2">
          <AlertTriangle size={13} /> {error}
        </div>
      )}

      {/* Overall coverage */}
      <div>
        <div className="flex items-baseline gap-3 mb-2">
          <span className="text-2xl font-bold text-gray-900" data-testid="backfill-overall">
            {fmtPct(overall)}
          </span>
          <span className="text-xs text-gray-500">
            overall · target {fmtPct(target)} · floor {fmtPct(floor)}
          </span>
          {isRunning && <StatusBadge running />}
        </div>

        {/* Per-collection rows */}
        <div className="space-y-2.5" data-testid="backfill-collection-rows">
          {rows.map((r) => {
            const collProgress = progress?.collections?.[r.collection];
            const remaining    = collProgress?.remaining ?? r.remaining ?? null;
            const total        = collProgress?.total ?? r.total_docs ?? null;
            const running      = collProgress?.running || false;
            return (
              <div key={r.collection} data-testid={`backfill-row-${r.collection}`}>
                <div className="flex items-center justify-between text-xs mb-1">
                  <span className="flex items-center gap-1.5 font-mono text-gray-700">
                    {running && <Loader2 size={10} className="animate-spin text-violet-500" />}
                    {COLL_LABELS[r.collection] || r.collection}
                    <span className="text-gray-400 font-normal">({r.collection})</span>
                  </span>
                  <span className="tabular-nums text-gray-600">
                    <span className={
                      r.ratio >= target ? 'text-emerald-700 font-semibold'
                      : r.ratio >= floor ? 'text-amber-700 font-semibold'
                      : 'text-rose-700 font-semibold'
                    }>
                      {fmtPct(r.ratio)}
                    </span>
                    {' '}
                    <span className="text-gray-400">
                      ({fmtNum(r.translated_docs)}/{fmtNum(total ?? r.total_docs)})
                      {remaining != null && remaining > 0 && (
                        <span className="ml-1 text-amber-600">{fmtNum(remaining)} remaining</span>
                      )}
                    </span>
                  </span>
                </div>
                <ProgressBar ratio={r.ratio} target={target} floor={floor} />
              </div>
            );
          })}
        </div>
      </div>

      {/* Trigger form */}
      <div className="bg-white/70 rounded-xl border border-white p-4 space-y-3">
        <p className="text-xs font-semibold text-gray-700 uppercase tracking-wider">Run a pass</p>

        {/* Collection selector */}
        <div>
          <p className="text-[11px] text-gray-500 mb-1.5">Collections</p>
          <div className="flex flex-wrap gap-1.5">
            {ALL_COLLECTIONS.map((c) => (
              <button
                key={c}
                onClick={() => toggleColl(c)}
                className={`text-xs rounded-lg px-2.5 py-1 border transition-colors ${
                  selectedColls.includes(c)
                    ? 'bg-violet-100 border-violet-300 text-violet-800'
                    : 'bg-white border-gray-200 text-gray-500'
                }`}
              >
                {COLL_LABELS[c] || c}
              </button>
            ))}
            <button
              onClick={() => setSelectedColls([...ALL_COLLECTIONS])}
              className="text-[11px] text-gray-400 hover:text-gray-600 px-1"
            >
              All
            </button>
          </div>
        </div>

        {/* max_docs + batch_size */}
        <div className="flex gap-3">
          <label className="flex-1">
            <p className="text-[11px] text-gray-500 mb-1">Max docs per collection</p>
            <input
              type="number"
              min={1}
              max={5000}
              value={maxDocs}
              onChange={(e) => setMaxDocs(Math.max(1, Math.min(5000, Number(e.target.value))))}
              className="w-full text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-violet-400"
            />
          </label>
          <label className="w-24">
            <p className="text-[11px] text-gray-500 mb-1">Batch size</p>
            <input
              type="number"
              min={1}
              max={50}
              value={batchSize}
              onChange={(e) => setBatchSize(Math.max(1, Math.min(50, Number(e.target.value))))}
              className="w-full text-xs border border-gray-200 rounded-lg px-2.5 py-1.5 bg-white focus:outline-none focus:ring-1 focus:ring-violet-400"
            />
          </label>
        </div>

        {/* Force toggle */}
        <label className="flex items-center gap-2.5 cursor-pointer select-none group">
          <div
            onClick={() => setForce((f) => !f)}
            className={`relative w-9 h-5 rounded-full transition-colors ${force ? 'bg-amber-500' : 'bg-gray-200'}`}
          >
            <span
              className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${force ? 'translate-x-4' : ''}`}
            />
          </div>
          <span className="text-xs text-gray-700">
            <span className="font-semibold">Force regenerate</span>
            <span className="text-gray-400 ml-1">— re-translate pages that already have Assamese</span>
          </span>
        </label>

        {force && (
          <div className="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2">
            <AlertTriangle size={12} className="mt-0.5 flex-shrink-0" />
            Force mode overwrites existing Assamese content. All {selectedColls.join(', ')} docs up to {fmtNum(maxDocs)} per collection will be re-translated.
          </div>
        )}

        {/* Preflight warnings from last trigger */}
        {preflight.length > 0 && (
          <div className="space-y-1.5">
            {preflight.map((w, i) => (
              <div key={i} className="flex items-start gap-2 text-xs text-rose-700 bg-rose-50 border border-rose-200 rounded-xl px-3 py-2">
                <AlertTriangle size={12} className="mt-0.5 flex-shrink-0" />
                {w}
              </div>
            ))}
          </div>
        )}
        {lastTrigger?.preflight_ok === true && preflight.length === 0 && (
          <div className="flex items-center gap-2 text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-xl px-3 py-2">
            <CheckCircle2 size={12} /> All credentials present — translation chain ready.
          </div>
        )}

        {/* Trigger button */}
        <div className="flex gap-2 pt-1">
          <button
            onClick={trigger}
            disabled={triggering || selectedColls.length === 0}
            data-testid="backfill-trigger-btn"
            className="flex items-center gap-2 bg-violet-600 hover:bg-violet-700 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-medium rounded-xl px-4 py-2 transition-colors"
          >
            {triggering
              ? <><Loader2 size={13} className="animate-spin" /> Starting…</>
              : force
                ? <><RotateCcw size={13} /> Regenerate Assamese</>
                : <><Play size={13} /> Run backfill</>
            }
          </button>
          {lastTrigger && (
            <span className="text-[11px] text-gray-500 self-center">
              {lastTrigger.already_running ? '⚠ queued behind existing run' : lastTrigger.note}
            </span>
          )}
        </div>
      </div>

      {/* Last run report */}
      {lastRun && (
        <div>
          <button
            onClick={() => setShowLastRun((s) => !s)}
            className="flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-700 mb-2"
          >
            {showLastRun ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            Last run report
            <span className="ml-1 text-gray-400">
              {lastRun.finished_at
                ? new Date(lastRun.finished_at).toLocaleString()
                : ''}
            </span>
          </button>

          {showLastRun && (
            <div className="bg-white/70 rounded-xl border border-white p-3 space-y-3">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                {lastResults.map((r) => (
                  <div key={r.collection} className="text-xs">
                    <p className="font-mono text-gray-500 mb-1 truncate">{r.collection}</p>
                    <p className="text-gray-800">
                      <span className="text-emerald-700 font-semibold">{fmtNum(r.translated)}</span> translated
                    </p>
                    <p className="text-gray-500">{fmtNum(r.skipped)} skipped</p>
                    <p className="text-rose-600">{fmtNum(r.failed)} failed</p>
                    <p className="text-gray-400">{fmtDur(r.duration_s)}</p>
                    {r.remaining > 0 && (
                      <p className="text-amber-600">{fmtNum(r.remaining)} left</p>
                    )}
                  </div>
                ))}
              </div>

              {/* Accept/reject breakdown */}
              {lastResults.some((r) => r.reject_reasons && Object.keys(r.reject_reasons).length > 0) && (
                <div>
                  <p className="text-[11px] text-gray-500 uppercase tracking-wider mb-1.5">
                    Reject reasons
                  </p>
                  {(() => {
                    const agg = {};
                    for (const r of lastResults) {
                      for (const [k, v] of Object.entries(r.reject_reasons || {})) {
                        agg[k] = (agg[k] || 0) + Number(v || 0);
                      }
                    }
                    return (
                      <ul className="text-xs space-y-0.5">
                        {Object.entries(agg)
                          .sort((a, b) => b[1] - a[1])
                          .map(([reason, n]) => (
                            <li key={reason} className="flex justify-between gap-3 font-mono text-gray-600">
                              <span>{reason}</span>
                              <span className="tabular-nums text-rose-600">{fmtNum(n)}</span>
                            </li>
                          ))}
                      </ul>
                    );
                  })()}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
