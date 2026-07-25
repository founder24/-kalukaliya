import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Loader2, RefreshCw, CheckCircle, XCircle, Clock,
  Copy, Check, AlertTriangle, ChevronDown, ChevronUp,
  Play, Minus, Languages, FileText, Zap,
} from 'lucide-react';
import axios from 'axios';
import { toast } from 'sonner';
import { API, authHeaders } from '@/utils/adminHelpers';

const POLL_MS = 5000;

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Intl.DateTimeFormat('en-IN', {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function fmtDuration(startIso, endIso) {
  if (!startIso) return '—';
  const end = endIso ? new Date(endIso) : new Date();
  const secs = Math.round((end - new Date(startIso)) / 1000);
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return `${m}m ${s}s`;
}

function StatusBadge({ status, running }) {
  if (running || status === 'running') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-violet-50 text-violet-600 border border-violet-200">
        <Loader2 size={9} className="animate-spin" />
        Running
      </span>
    );
  }
  if (status === 'completed') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-50 text-emerald-600 border border-emerald-200">
        <CheckCircle size={9} />
        Completed
      </span>
    );
  }
  if (status === 'error') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-red-50 text-red-600 border border-red-200">
        <XCircle size={9} />
        Error
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-100 text-gray-500 border border-gray-200">
      <Minus size={9} />
      {status || 'Unknown'}
    </span>
  );
}

function RunTypeBadge({ runType }) {
  if (runType === 'assamese') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-amber-50 text-amber-700 border border-amber-200">
        <Languages size={9} />
        Assamese
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-indigo-50 text-indigo-700 border border-indigo-200">
      <FileText size={9} />
      Notes (EN+AS)
    </span>
  );
}

function CopyButton({ value, label = 'Copy', title }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(
        Array.isArray(value) ? JSON.stringify(value) : String(value)
      );
      setCopied(true);
      toast.success(title ? `${title} copied` : 'Copied to clipboard');
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error('Failed to copy');
    }
  };
  return (
    <button
      onClick={handleCopy}
      title={title || 'Copy to clipboard'}
      className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-medium transition-colors bg-gray-100 hover:bg-gray-200 text-gray-600 border border-gray-200"
    >
      {copied ? <Check size={9} className="text-emerald-500" /> : <Copy size={9} />}
      {copied ? 'Copied!' : label}
    </button>
  );
}

// ── Run row ───────────────────────────────────────────────────────────────────

function RunRow({ run, onUseFailedIds }) {
  const [expanded, setExpanded] = useState(false);
  const pct = run.total > 0
    ? Math.round(((run.completed + run.failed + run.skipped) / run.total) * 100)
    : 0;
  const hasFailedIds = run.failed_ids?.length > 0;
  const hasErrors    = run.errors?.length > 0;

  return (
    <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
      <div className="px-4 py-3 flex flex-col sm:flex-row sm:items-center gap-3">
        {/* Left: type + status + date */}
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <RunTypeBadge runType={run.run_type} />
          <StatusBadge status={run.status} running={run.running} />
          <div className="min-w-0">
            <p className="text-xs font-semibold text-gray-800 truncate">
              {fmtDate(run.started_at)}
            </p>
            <p className="text-[10px] text-gray-400 mt-0.5">
              {run.running
                ? `Running · ${fmtDuration(run.started_at, null)} elapsed`
                : `Finished in ${fmtDuration(run.started_at, run.finished_at)}`
              }
              {run.force && (
                <span className="ml-2 px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 border border-amber-200 text-[9px] font-semibold">
                  FORCE
                </span>
              )}
            </p>
          </div>
        </div>

        {/* Center: counters */}
        <div className="flex items-center gap-3 flex-shrink-0">
          {[
            { label: 'Total',   value: run.total,     color: 'text-gray-600' },
            { label: 'Done',    value: run.completed,  color: 'text-emerald-600' },
            { label: 'Failed',  value: run.failed,     color: run.failed > 0 ? 'text-red-500' : 'text-gray-400' },
            { label: 'Skipped', value: run.skipped,    color: 'text-amber-500' },
          ].map(({ label, value, color }) => (
            <div key={label} className="text-center">
              <p className={`text-sm font-bold ${color}`}>{value ?? 0}</p>
              <p className="text-[9px] text-gray-400 uppercase tracking-wide">{label}</p>
            </div>
          ))}
        </div>

        {/* Right: actions */}
        <div className="flex items-center gap-2 flex-shrink-0">
          {hasFailedIds && (
            <button
              onClick={() => onUseFailedIds(run.failed_ids, run.run_type)}
              title="Populate retry modal with these failed IDs"
              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-red-50 text-red-600 border border-red-200 hover:bg-red-100 transition-colors"
            >
              <Play size={9} />
              Retry ({run.failed_ids.length})
            </button>
          )}
          <button
            onClick={() => setExpanded(e => !e)}
            className="p-1.5 rounded-lg bg-gray-50 hover:bg-gray-100 transition-colors text-gray-400"
            title={expanded ? 'Collapse' : 'Expand details'}
          >
            {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
        </div>
      </div>

      {/* Progress bar */}
      {(run.running || (run.total > 0 && pct < 100)) && (
        <div className="px-4 pb-2">
          <div className="h-1 rounded-full bg-gray-100 overflow-hidden">
            <div
              className="h-1 rounded-full transition-all duration-500"
              style={{
                width: `${run.running ? Math.min(pct, 99) : pct}%`,
                background: run.failed > 0 ? '#f87171' : run.run_type === 'assamese' ? '#f59e0b' : '#34d399',
              }}
            />
          </div>
          {run.running && run.current && (
            <p className="text-[10px] text-gray-400 mt-1 truncate">Processing: {run.current}</p>
          )}
        </div>
      )}

      {/* Expanded details */}
      {expanded && (
        <div className="border-t border-gray-100 px-4 py-3 space-y-3">
          <div className="flex flex-wrap gap-4 text-[11px] text-gray-500">
            <span><span className="font-medium text-gray-700">Run ID:</span> <span className="font-mono">{run.run_id}</span></span>
            {run.run_type !== 'assamese' && (
              <span><span className="font-medium text-gray-700">Topics seeded:</span> {run.topics_seeded ?? 0}</span>
            )}
            <span><span className="font-medium text-gray-700">Concurrency:</span> {run.concurrency ?? '—'}</span>
          </div>
          {hasFailedIds && (
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <p className="text-[10px] font-semibold text-red-600 uppercase tracking-wide flex items-center gap-1">
                  <AlertTriangle size={10} />
                  Failed IDs ({run.failed_ids.length})
                </p>
                <div className="flex gap-1.5">
                  <CopyButton value={run.failed_ids} label="Copy JSON" title="Failed chapter IDs" />
                  <CopyButton value={run.failed_ids.join(',')} label="Copy CSV" title="Failed IDs (CSV)" />
                </div>
              </div>
              <div className="bg-red-50 rounded-lg p-2.5 max-h-24 overflow-y-auto">
                <p className="text-[10px] font-mono text-red-700 break-all leading-relaxed">
                  {run.failed_ids.join(', ')}
                </p>
              </div>
            </div>
          )}
          {hasErrors && (
            <div>
              <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-1.5">
                Error Details ({run.errors.length})
              </p>
              <div className="space-y-1 max-h-40 overflow-y-auto">
                {run.errors.map((err, i) => (
                  <div key={i} className="flex items-start gap-2 px-2.5 py-1.5 rounded-lg bg-gray-50 border border-gray-100">
                    <XCircle size={10} className="text-red-400 flex-shrink-0 mt-0.5" />
                    <div className="min-w-0">
                      <p className="text-[10px] font-medium text-gray-700 truncate">{err.title || err.chapter_id}</p>
                      <p className="text-[10px] text-gray-400 truncate">{err.error}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          {!hasFailedIds && !hasErrors && (
            <p className="text-[11px] text-gray-400 italic">No errors recorded for this run.</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Launch card ───────────────────────────────────────────────────────────────

function LaunchCard({ title, description, color, icon: Icon, onLaunch, loading, disabled, stats }) {
  const colors = {
    indigo: {
      border: 'border-indigo-200', bg: 'bg-indigo-50', text: 'text-indigo-700',
      btn: 'bg-indigo-600 hover:bg-indigo-700 text-white',
      icon: 'text-indigo-500',
    },
    amber: {
      border: 'border-amber-200', bg: 'bg-amber-50', text: 'text-amber-700',
      btn: 'bg-amber-500 hover:bg-amber-600 text-white',
      icon: 'text-amber-500',
    },
  }[color] || {};

  return (
    <div className={`rounded-xl border ${colors.border} ${colors.bg} p-4`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className={`mt-0.5 ${colors.icon}`}>
            <Icon size={18} />
          </div>
          <div>
            <p className={`text-sm font-semibold ${colors.text}`}>{title}</p>
            <p className="text-xs text-gray-500 mt-0.5 max-w-xs">{description}</p>
            {stats && (
              <p className="text-[10px] text-gray-400 mt-1.5 font-mono">{stats}</p>
            )}
          </div>
        </div>
        <button
          onClick={onLaunch}
          disabled={disabled || loading}
          className={`flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${colors.btn} disabled:opacity-50 disabled:cursor-not-allowed`}
        >
          {loading ? <Loader2 size={11} className="animate-spin" /> : <Zap size={11} />}
          {loading ? 'Launching…' : 'Launch'}
        </button>
      </div>
    </div>
  );
}

// ── Main panel ────────────────────────────────────────────────────────────────

export default function SeederHistoryPanel({ adminToken, onRetryWithIds }) {
  const [runs, setRuns]               = useState([]);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState(null);
  const [launchingNotes, setLaunchingNotes] = useState(false);
  const [launchingAs, setLaunchingAs] = useState(false);
  const pollRef                       = useRef(null);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await axios.get(
        `${API}/admin/content/seed-notes/history?limit=20`,
        authHeaders(adminToken),
      );
      setRuns(res.data?.runs || []);
      setError(null);
      return res.data?.runs || [];
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.message || 'Failed to load seeder history';
      setError(msg);
      return [];
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => {
    const hasRunning = (list) => list.some(r => r.running || r.status === 'running');
    const tick = async () => {
      const list = await fetchHistory();
      if (hasRunning(list)) {
        pollRef.current = setTimeout(tick, POLL_MS);
      }
    };
    tick();
    return () => { if (pollRef.current) clearTimeout(pollRef.current); };
  }, [fetchHistory]);

  const handleRefresh = () => { setLoading(true); fetchHistory(); };

  const handleUseFailedIds = (failedIds, runType) => {
    if (onRetryWithIds) {
      onRetryWithIds(failedIds, runType);
    } else {
      navigator.clipboard.writeText(JSON.stringify(failedIds))
        .then(() => toast.success(`${failedIds.length} failed IDs copied to clipboard`))
        .catch(() => toast.error('Failed to copy'));
    }
  };

  // Launch seed-notes (EN+AS full generation for chapters missing content)
  const handleLaunchNotes = async () => {
    const anyRunning = runs.some(r => r.running || r.status === 'running');
    if (anyRunning) {
      toast.error('A seeder job is already running. Wait for it to finish.');
      return;
    }
    setLaunchingNotes(true);
    try {
      const res = await axios.post(
        `${API}/admin/content/seed-notes`,
        { concurrency: 2 },
        authHeaders(adminToken),
      );
      toast.success(`Seed-notes launched — ${res.data.total_queued} chapters queued`);
      setTimeout(() => { fetchHistory(); setLaunchingNotes(false); }, 1500);
      // Start polling
      pollRef.current = setTimeout(async function tick() {
        const list = await fetchHistory();
        if (list.some(r => r.running || r.status === 'running')) {
          pollRef.current = setTimeout(tick, POLL_MS);
        }
      }, POLL_MS);
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.message || 'Failed to launch';
      toast.error(msg);
      setLaunchingNotes(false);
    }
  };

  // Launch seed-assamese (AS-only translation for chapters with EN but no AS)
  const handleLaunchAssamese = async () => {
    const anyRunning = runs.some(r => r.running || r.status === 'running');
    if (anyRunning) {
      toast.error('A seeder job is already running. Wait for it to finish.');
      return;
    }
    setLaunchingAs(true);
    try {
      const res = await axios.post(
        `${API}/admin/content/seed-assamese`,
        { concurrency: 2 },
        authHeaders(adminToken),
      );
      toast.success(`Seed-assamese launched — ${res.data.total_queued} chapters queued`);
      setTimeout(() => { fetchHistory(); setLaunchingAs(false); }, 1500);
      pollRef.current = setTimeout(async function tick() {
        const list = await fetchHistory();
        if (list.some(r => r.running || r.status === 'running')) {
          pollRef.current = setTimeout(tick, POLL_MS);
        }
      }, POLL_MS);
    } catch (err) {
      const msg = err?.response?.data?.detail || err?.message || 'Failed to launch';
      toast.error(msg);
      setLaunchingAs(false);
    }
  };

  const anyJobRunning = runs.some(r => r.running || r.status === 'running');

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-gray-900 font-bold text-lg flex items-center gap-2">
            <Clock size={18} className="text-violet-500" />
            Seeder Control
          </h2>
          <p className="text-gray-500 text-sm mt-1">
            Launch generation jobs and review past runs
          </p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs text-gray-500 hover:text-gray-700 transition-colors bg-white border border-gray-200 shadow-sm disabled:opacity-50"
        >
          {loading ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          Refresh
        </button>
      </div>

      {/* Launch cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <LaunchCard
          title="Seed Notes (EN + AS)"
          description="Generate English notes + Assamese translation for all chapters missing content_en. Includes auto-publish."
          color="indigo"
          icon={FileText}
          onLaunch={handleLaunchNotes}
          loading={launchingNotes}
          disabled={anyJobRunning}
          stats="Target: 210 draft chapters"
        />
        <LaunchCard
          title="Seed Assamese Only"
          description="Translate content_en → content_as for all published chapters that already have English but are missing Assamese."
          color="amber"
          icon={Languages}
          onLaunch={handleLaunchAssamese}
          loading={launchingAs}
          disabled={anyJobRunning}
          stats="Target: ~84 published EN-only chapters"
        />
      </div>

      {anyJobRunning && (
        <div className="rounded-xl px-4 py-3 bg-violet-50 border border-violet-200 flex items-center gap-2">
          <Loader2 size={13} className="animate-spin text-violet-500" />
          <p className="text-xs text-violet-700 font-medium">
            A job is running — other launchers are disabled until it completes.
          </p>
        </div>
      )}

      {/* Divider */}
      <div className="border-t border-gray-100 pt-2">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Run History</p>
      </div>

      {/* States */}
      {loading && runs.length === 0 && (
        <div className="flex items-center justify-center py-12 gap-3">
          <Loader2 size={20} className="animate-spin text-violet-400" />
          <span className="text-sm text-gray-400">Loading run history…</span>
        </div>
      )}
      {!loading && error && runs.length === 0 && (
        <div className="rounded-xl p-5 bg-red-50 border border-red-200 flex items-start gap-3">
          <AlertTriangle size={16} className="text-red-400 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-red-700">Failed to load history</p>
            <p className="text-xs text-red-500 mt-0.5">{error}</p>
          </div>
        </div>
      )}
      {!loading && !error && runs.length === 0 && (
        <div className="rounded-xl p-10 bg-white border border-gray-200 text-center">
          <Clock size={32} className="mx-auto text-gray-200 mb-3" />
          <p className="text-sm font-medium text-gray-500">No seeder runs yet</p>
          <p className="text-xs text-gray-400 mt-1">Launch a job above to see history here.</p>
        </div>
      )}
      {runs.length > 0 && (
        <div className="space-y-3">
          {runs.map((run, i) => (
            <RunRow
              key={run.run_id || i}
              run={run}
              onUseFailedIds={handleUseFailedIds}
            />
          ))}
        </div>
      )}
      {runs.length > 0 && (
        <p className="text-[10px] text-gray-400 text-center">
          Showing up to 20 most recent runs · Live runs auto-refresh every {POLL_MS / 1000}s
        </p>
      )}
    </div>
  );
}
