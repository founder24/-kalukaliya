import React, { useCallback, useEffect, useRef, useState } from 'react';
import axios from 'axios';
import {
  AlertTriangle, CheckCircle2, Languages, Loader2,
  RefreshCw, RotateCcw, Play,
} from 'lucide-react';
import { API_BASE } from '@/utils/api';
import { authHeaders } from '@/utils/adminHelpers';

/**
 * AssameseBackfillPanel — full interactive admin panel for bulk Assamese
 * content regeneration from English pages.
 *
 * Uses only staff-authorized, Worker-native chapter coverage and seed-job routes.
 */

const fmtPct  = (r) => (typeof r === 'number' && isFinite(r)) ? `${(r * 100).toFixed(1)}%` : '—';
const fmtNum  = (n) => (typeof n === 'number' && isFinite(n)) ? n.toLocaleString() : '—';
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
  const [maxDocs, setMaxDocs]   = useState(200);
  const [force, setForce]       = useState(false);

  const pollRef   = useRef(null);

  const fetchHealth = useCallback(async () => {
    setLoadingH(true);
    try {
      const res = await axios.get(`${API_BASE}/admin/content/assamese/coverage`, authHeaders(adminToken));
      setHealth(res.data);
      setError(null);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to load coverage');
    } finally {
      setLoadingH(false);
    }
  }, [adminToken]);

  const fetchProgress = useCallback(async () => {
    setLoadingP(true);
    try {
      const res = await axios.get(`${API_BASE}/admin/content/assamese/progress`, authHeaders(adminToken));
      setProgress(res.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed to load job progress');
    } finally {
      setLoadingP(false);
    }
  }, [adminToken]);

  const isRunning = Boolean(progress?.running);

  const stopPolling = useCallback(() => {
    if (pollRef.current)   { clearInterval(pollRef.current);   pollRef.current = null; }
  }, []);

  const startPolling = useCallback(() => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      await Promise.all([fetchProgress(), fetchHealth()]);
    }, 6000);
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
        `${API_BASE}/admin/content/assamese/backfill`,
        {
          limit: maxDocs,
          force,
        },
        authHeaders(adminToken),
      );
      setLastTrigger(res.data);
      await fetchProgress();
      startPolling();
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Trigger failed');
    } finally {
      setTriggering(false);
    }
  }, [maxDocs, force, adminToken, fetchProgress, startPolling]);

  const target     = 0.85;
  const floor      = 0.80;
  const overall    = health?.ratio ?? 0;
  const run        = progress?.run;

  const overallTone = overall >= target ? 'emerald' : overall >= floor ? 'amber' : 'rose';
  const tileColor   = { emerald: 'border-emerald-200 bg-emerald-50', amber: 'border-amber-200 bg-amber-50', rose: 'border-rose-200 bg-rose-50' }[overallTone];
  const headColor   = { emerald: 'text-emerald-700', amber: 'text-amber-700', rose: 'text-rose-700' }[overallTone];
  const iconColor   = { emerald: 'bg-emerald-100 text-emerald-500', amber: 'bg-amber-100 text-amber-600', rose: 'bg-rose-100 text-rose-500' }[overallTone];

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
            Translates chapter notes with English source content using Workers AI.
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

        <div className="space-y-2.5" data-testid="backfill-collection-rows">
          <div data-testid="backfill-row-chapters">
            <div className="flex items-center justify-between text-xs mb-1">
              <span className="font-medium text-gray-700">Chapter notes</span>
              <span className="tabular-nums text-gray-500">
                {fmtNum(health?.translated)}/{fmtNum(health?.total)}
                {health?.missing > 0 && <span className="ml-1 text-amber-600">{fmtNum(health.missing)} remaining</span>}
              </span>
            </div>
            <ProgressBar ratio={overall} target={target} floor={floor} />
          </div>
        </div>
      </div>

      {/* Trigger form */}
      <div className="bg-white/70 rounded-xl border border-white p-4 space-y-3">
        <p className="text-xs font-semibold text-gray-700 uppercase tracking-wider">Run a pass</p>

        <div>
          <label className="flex-1">
            <p className="text-[11px] text-gray-500 mb-1">Maximum chapters for this run</p>
            <input
              type="number"
              min={1}
              max={200}
              value={maxDocs}
              onChange={(e) => setMaxDocs(Math.max(1, Math.min(200, Number(e.target.value))))}
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
            Force mode overwrites existing Assamese content for up to {fmtNum(maxDocs)} chapters.
          </div>
        )}

        {/* Trigger button */}
        <div className="flex gap-2 pt-1">
          <button
            onClick={trigger}
            disabled={triggering || isRunning}
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
              {lastTrigger.job === 'nothing_to_do' ? lastTrigger.message : `${lastTrigger.total_queued} chapters queued`}
            </span>
          )}
        </div>
      </div>

      {run && (
        <div className="bg-white/70 rounded-xl border border-white p-3 text-xs space-y-1">
          <div className="flex items-center gap-2 font-semibold text-gray-700">
            {run.status === 'completed' ? <CheckCircle2 size={13} className="text-emerald-600" /> : null}
            Latest run: {run.status}
          </div>
          <p className="text-gray-500">
            {fmtNum(run.completed)} completed · {fmtNum(run.queued)} queued · {fmtNum(run.failed)} failed
          </p>
          {run.finished_at && <p className="text-gray-400">Finished {new Date(run.finished_at).toLocaleString()}</p>}
        </div>
      )}
    </div>
  );
}
