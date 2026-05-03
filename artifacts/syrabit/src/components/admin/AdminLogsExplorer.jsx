/**
 * Task #944 — Unified Log Explorer admin panel.
 *
 * One pane of glass for every log-emitting surface in the stack:
 *   - source=edge       Cloudflare worker request samples
 *   - source=cloudflare GraphQL pull (CF-side cache + edge metadata)
 *   - source=backend    FastAPI per-request samples
 *   - source=cron       background job heartbeats / failures
 *   - source=pages      Cloudflare Pages function logs (when wired)
 *
 * Server-side TTL keeps the collection bounded and the API caps every
 * query (``MAX_QUERY_LIMIT``) so a misclick can never page in millions
 * of rows. The UI virtualises by simply rendering only the current
 * page; a "Load older" button grabs the next cursor-paginated page.
 */
import { Fragment, useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  RefreshCw, Download, Search, Pause, Play, Trash2, Link as LinkIcon,
  Activity, AlertTriangle, AlertOctagon, Info, X, KeyRound, Filter,
  Copy, ChevronDown, ChevronRight, Shield,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  adminLogsList, adminLogsStatus, adminLogsTrace, adminLogsPause,
  adminLogsResume, adminLogsRotateToken, adminLogsClear,
  adminLogsExportUrl, adminLogsDownloadExport,
} from '@/utils/api';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
// Task #296 — Admin Actions are folded into the Logs Explorer as an
// extra `source=admin-actions` filter. When that pseudo-source is
// active the explorer swaps its table for the AdminActivityLog view
// (which still talks to the dedicated /admin/activity-log endpoint).
import AdminActivityLog from './AdminActivityLog';

// `admin-actions` is a UI-only pseudo-source — it does NOT get sent to
// the unified-logs API. It signals "switch this pane to render the
// admin audit-trail UI instead of the unified-logs table".
export const ADMIN_ACTIONS_SOURCE = 'admin-actions';

const SOURCE_OPTIONS = [
  { id: 'edge',                 label: 'Edge worker' },
  { id: 'cloudflare',           label: 'Cloudflare GraphQL' },
  { id: 'backend',              label: 'Backend (FastAPI)' },
  { id: 'pages',                label: 'CF Pages' },
  { id: 'cron',                 label: 'Cron / jobs' },
  { id: ADMIN_ACTIONS_SOURCE,   label: 'Admin actions' },
];
const LEVEL_OPTIONS = ['debug', 'info', 'warn', 'error'];
const TIME_PRESETS = [
  { id: '15m', label: 'Last 15m', minutes: 15 },
  { id: '1h',  label: 'Last 1h',  minutes: 60 },
  { id: '6h',  label: 'Last 6h',  minutes: 360 },
  { id: '24h', label: 'Last 24h', minutes: 1440 },
  { id: '7d',  label: 'Last 7d',  minutes: 1440 * 7 },
];
const PAGE_SIZE = 200;
const LIVE_TAIL_INTERVAL_MS = 4000;

const SOURCE_BADGE = {
  edge:       'bg-amber-100 text-amber-800 border-amber-200',
  cloudflare: 'bg-orange-100 text-orange-800 border-orange-200',
  backend:    'bg-indigo-100 text-indigo-800 border-indigo-200',
  pages:      'bg-emerald-100 text-emerald-800 border-emerald-200',
  cron:       'bg-violet-100 text-violet-800 border-violet-200',
};
const LEVEL_ICON = {
  debug: { Icon: Info,         color: 'text-slate-500' },
  info:  { Icon: Info,         color: 'text-blue-600'  },
  warn:  { Icon: AlertTriangle,color: 'text-amber-600' },
  error: { Icon: AlertOctagon, color: 'text-red-600'   },
};

function timeWindowFromPreset(preset) {
  const now = new Date();
  const since = new Date(now.getTime() - preset.minutes * 60 * 1000);
  return { since: since.toISOString(), until: now.toISOString() };
}

function fmtTime(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso);
    return d.toLocaleString('en-IN', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      day: '2-digit', month: 'short',
    });
  } catch { return String(iso).slice(0, 19); }
}

function fmtMs(n) {
  if (n == null) return '—';
  if (n < 1000) return `${n}ms`;
  return `${(n / 1000).toFixed(2)}s`;
}

function statusClass(status) {
  if (status == null) return 'text-slate-500';
  if (status >= 500) return 'text-red-600 font-semibold';
  if (status >= 400) return 'text-amber-600 font-semibold';
  if (status >= 300) return 'text-blue-600';
  return 'text-emerald-700';
}

export default function AdminLogsExplorer({ adminToken, onNavigate, navContext }) {
  // Task #296 — seed initial sources from navContext so the legacy
  // `activitylog` deep-link (redirected to logs with
  // initialSources=['admin-actions']) lands on the Admin Actions view.
  const initialSources = Array.isArray(navContext?.initialSources)
    ? navContext.initialSources.filter((s) => typeof s === 'string')
    : [];
  const [filters, setFilters] = useState({
    sources: initialSources,
    levels:  [],
    route_prefix: '',
    correlation_id: '',
    q: '',
    status_min: '',
    status_max: '',
  });
  // Re-seed sources if navContext changes after mount (e.g. user clicks
  // the Admin Actions deep-link from the dashboard while already on Logs).
  useEffect(() => {
    if (Array.isArray(navContext?.initialSources)) {
      setFilters((f) => ({ ...f, sources: navContext.initialSources.filter((s) => typeof s === 'string') }));
    }
  }, [navContext?.initialSources]);

  // Active "admin-actions" pseudo-source short-circuits the unified-logs
  // pane and renders the activity-log audit trail instead.
  const showAdminActions = filters.sources.includes(ADMIN_ACTIONS_SOURCE);
  const [preset, setPreset] = useState('1h');
  const [logs, setLogs]       = useState([]);
  const [total, setTotal]     = useState(0);
  const [totalCapped, setTotalCapped] = useState(false);
  const [nextBefore, setNextBefore]   = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);
  const [status, setStatus]   = useState(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [liveTail, setLiveTail] = useState(false);
  const [traceModal, setTraceModal] = useState(null); // { cid, logs[], loading }
  const [confirmClearOpen, setConfirmClearOpen] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [clearing, setClearing] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [rotatedToken, setRotatedToken] = useState(null);
  const [pendingPause, setPendingPause] = useState(false);
  // Set of row _id strings that are currently expanded to show full
  // JSON payload. Using a Set keeps O(1) toggle/lookup even when the
  // page renders the full PAGE_SIZE rows.
  const [expandedIds, setExpandedIds] = useState(() => new Set());
  const [showSafeguards, setShowSafeguards] = useState(false);

  const rowKeyOf = (row) => row?._id || `${row?.timestamp}|${row?.correlation_id || ''}|${row?.message || ''}`;
  const toggleExpanded = (key) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };
  const copyToClipboard = async (text, label = 'value') => {
    if (!text) return;
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(String(text));
      } else {
        const ta = document.createElement('textarea');
        ta.value = String(text);
        ta.style.position = 'fixed';
        ta.style.left = '-1000px';
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
      }
      toast.success(`Copied ${label}`);
    } catch {
      toast.error(`Failed to copy ${label}`);
    }
  };

  const window_ = useMemo(() => {
    const p = TIME_PRESETS.find((x) => x.id === preset) || TIME_PRESETS[1];
    return timeWindowFromPreset(p);
  }, [preset]);
  // Strip the admin-actions pseudo-source before issuing API calls — the
  // unified-logs backend has no concept of it; it's UI-only routing.
  const fullFilters = useMemo(() => ({
    ...filters,
    sources: (filters.sources || []).filter((s) => s !== ADMIN_ACTIONS_SOURCE),
    ...window_,
  }), [filters, window_]);

  const load = useCallback(async ({ append = false, before = null } = {}) => {
    // Skip the unified-logs query entirely when the pane has been
    // swapped to the Admin Actions view. AdminActivityLog runs its own
    // fetch against /admin/activity-log.
    if (showAdminActions) {
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const r = await adminLogsList(adminToken, {
        filters: fullFilters, limit: PAGE_SIZE,
        before: before || undefined,
      });
      const data = r.data || {};
      const rows = Array.isArray(data.logs) ? data.logs : [];
      setLogs((prev) => append ? [...prev, ...rows] : rows);
      setTotal(data.total ?? rows.length);
      setTotalCapped(!!data.total_capped);
      setNextBefore(data.next_before || null);
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load logs');
      if (!append) setLogs([]);
    } finally {
      setLoading(false);
    }
  }, [adminToken, fullFilters]);

  const loadStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const r = await adminLogsStatus(adminToken);
      setStatus(r.data || null);
    } catch {
      setStatus(null);
    } finally {
      setStatusLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { loadStatus(); }, [loadStatus]);

  // Live tail — re-poll the FIRST page only (no cursor) every 4s.
  // Stops as soon as the user toggles it off or the tab is hidden.
  useEffect(() => {
    if (!liveTail) return undefined;
    const handle = setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      load();
    }, LIVE_TAIL_INTERVAL_MS);
    return () => clearInterval(handle);
  }, [liveTail, load]);

  const toggleArrayFilter = (field, value) => {
    setFilters((f) => {
      const cur = f[field] || [];
      const next = cur.includes(value)
        ? cur.filter((x) => x !== value)
        : [...cur, value];
      return { ...f, [field]: next };
    });
  };

  const handlePauseToggle = async () => {
    if (!status) return;
    setPendingPause(true);
    try {
      const fn = status.paused ? adminLogsResume : adminLogsPause;
      await fn(adminToken);
      toast.success(status.paused ? 'Ingest resumed' : 'Ingest paused');
      await loadStatus();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Failed to toggle pause');
    } finally {
      setPendingPause(false);
    }
  };

  const handleRotateToken = async () => {
    setRotating(true);
    try {
      const r = await adminLogsRotateToken(adminToken);
      setRotatedToken(r.data?.token || '');
      toast.success('Ingest token rotated — copy it now');
      await loadStatus();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Failed to rotate token');
    } finally {
      setRotating(false);
    }
  };

  const handleClearConfirmed = async () => {
    if (confirmText !== 'CLEAR' || clearing) return;
    setClearing(true);
    try {
      // Pseudo-source is UI-only; never send it to the unified-logs API.
      const cleanSources = (filters.sources || []).filter((s) => s !== ADMIN_ACTIONS_SOURCE);
      const r = await adminLogsClear(adminToken, cleanSources);
      toast.success(`Cleared ${r.data?.deleted ?? 0} entries — purge is logged in Activity Log`);
      setConfirmClearOpen(false);
      setConfirmText('');
      load();
      loadStatus();
    } catch (e) {
      toast.error(e?.response?.data?.detail || 'Failed to clear logs');
    } finally {
      setClearing(false);
    }
  };

  const openTrace = async (cid) => {
    if (!cid) return;
    setTraceModal({ cid, logs: [], loading: true });
    try {
      const r = await adminLogsTrace(adminToken, cid);
      setTraceModal({ cid, logs: r.data?.logs || [], loading: false });
    } catch (e) {
      setTraceModal({ cid, logs: [], loading: false,
        error: e?.response?.data?.detail || 'Failed to fetch trace' });
    }
  };

  // Authenticated downloads — go through the Bearer-authed blob helper
  // so the export works in environments where the admin only has a
  // JWT (no admin cookie). Falls back to a new-tab open if the
  // download itself errors (e.g. CORS) so the admin always has a way
  // to recover the file.
  const exportCsv = async () => {
    try {
      const fname = await adminLogsDownloadExport(adminToken, { filters: fullFilters, fmt: 'csv' });
      toast.success(`Downloaded ${fname}`);
    } catch (e) {
      const url = adminLogsExportUrl({ filters: fullFilters, fmt: 'csv' });
      window.open(url, '_blank', 'noopener');
      toast.error('Bearer download failed — opened legacy URL fallback');
    }
  };
  const exportNdjson = async () => {
    try {
      const fname = await adminLogsDownloadExport(adminToken, { filters: fullFilters, fmt: 'ndjson' });
      toast.success(`Downloaded ${fname}`);
    } catch (e) {
      const url = adminLogsExportUrl({ filters: fullFilters, fmt: 'ndjson' });
      window.open(url, '_blank', 'noopener');
      toast.error('Bearer download failed — opened legacy URL fallback');
    }
  };

  return (
    <SectionErrorBoundary section="Logs Explorer">
      <div className="p-4 md:p-6 space-y-4">
        {/* ── Header bar ─────────────────────────────────────────── */}
        <div className="flex flex-wrap items-center gap-2 justify-between">
          <div className="flex items-center gap-2">
            <Activity className="w-5 h-5 text-indigo-600" />
            <h2 className="text-xl font-semibold">Unified Logs</h2>
            {status && (
              <StatusPill status={status} statusLoading={statusLoading} />
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => load()}
              className="inline-flex items-center gap-1 px-3 py-1.5 text-sm border rounded hover:bg-slate-50"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
            <label className="inline-flex items-center gap-1 px-3 py-1.5 text-sm border rounded cursor-pointer hover:bg-slate-50">
              <input
                type="checkbox"
                className="accent-indigo-600"
                checked={liveTail}
                onChange={(e) => setLiveTail(e.target.checked)}
              />
              Live tail
            </label>
            <button
              type="button"
              onClick={handlePauseToggle}
              disabled={pendingPause || !status}
              className={`inline-flex items-center gap-1 px-3 py-1.5 text-sm border rounded ${
                status?.paused
                  ? 'bg-emerald-50 hover:bg-emerald-100 border-emerald-300 text-emerald-800'
                  : 'bg-amber-50 hover:bg-amber-100 border-amber-300 text-amber-800'
              } disabled:opacity-50`}
            >
              {status?.paused ? <Play className="w-4 h-4" /> : <Pause className="w-4 h-4" />}
              {status?.paused ? 'Resume ingest' : 'Pause ingest'}
            </button>
            <button
              type="button"
              onClick={exportCsv}
              className="inline-flex items-center gap-1 px-3 py-1.5 text-sm border rounded hover:bg-slate-50"
            >
              <Download className="w-4 h-4" />
              CSV
            </button>
            <button
              type="button"
              onClick={exportNdjson}
              className="inline-flex items-center gap-1 px-3 py-1.5 text-sm border rounded hover:bg-slate-50"
            >
              <Download className="w-4 h-4" />
              NDJSON
            </button>
            <button
              type="button"
              onClick={handleRotateToken}
              disabled={rotating}
              className="inline-flex items-center gap-1 px-3 py-1.5 text-sm border rounded hover:bg-slate-50 disabled:opacity-50"
              title="Generate a new ingest token. The plaintext is shown ONCE — copy it to the worker secret store before closing the dialog."
            >
              <KeyRound className="w-4 h-4" />
              Rotate token
            </button>
            <button
              type="button"
              onClick={() => { setConfirmText(''); setConfirmClearOpen(true); }}
              className="inline-flex items-center gap-1 px-3 py-1.5 text-sm border rounded text-red-700 border-red-300 hover:bg-red-50"
            >
              <Trash2 className="w-4 h-4" />
              Clear
            </button>
          </div>
        </div>

        {/* ── Task #952 — Saturation alert banner ────────────────────
            Surfaces the same `cf_pull_last_saturated_windows` /
            `cf_pull_saturated_minutes_24h` fields the admin pill +
            in-app notification fire on, so an operator opening the
            explorer sees the active incident inline (instead of
            having to scroll into the Safeguards card or wait on the
            in-app notification fan-out to land). */}
        <CfPullSaturationBanner status={status} />

        {/* ── Safeguards card (sampling, retention, rate caps) ───── */}
        {status && (
          <div className="border rounded bg-white">
            <button
              type="button"
              onClick={() => setShowSafeguards((v) => !v)}
              className="w-full flex items-center justify-between px-3 py-2 text-sm hover:bg-slate-50"
              aria-expanded={showSafeguards}
              data-testid="safeguards-toggle"
            >
              <span className="inline-flex items-center gap-2">
                <Shield className="w-4 h-4 text-slate-500" />
                <span className="font-medium">Ingest safeguards</span>
                <span className="text-xs text-slate-500">
                  retention {status.ttl_days ?? '—'}d · backend sample {Math.round((status.backend_sample_rate ?? 0) * 100)}% ·
                  batch cap {status.max_ingest_batch ?? '—'} · pull every {status.cf_pull_interval_s ?? '—'}s
                </span>
              </span>
              {showSafeguards
                ? <ChevronDown className="w-4 h-4 text-slate-400" />
                : <ChevronRight className="w-4 h-4 text-slate-400" />}
            </button>
            {showSafeguards && (
              <div className="px-3 py-3 border-t bg-slate-50/60 grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
                <SafeguardRow label="Retention TTL"
                  value={`${status.ttl_days ?? '—'} days`}
                  hint="Set via UNIFIED_LOGS_TTL_DAYS env. Mongo enforces server-side; rows past TTL are auto-deleted." />
                <SafeguardRow label="Backend sample rate"
                  value={`${Math.round((status.backend_sample_rate ?? 0) * 100)}%`}
                  hint="2xx requests sampled at this rate. 4xx/5xx and slow (>1.5s) requests are always kept." />
                <SafeguardRow label="Edge sample rate"
                  value={`env: ${status.edge_sample_rate_env || 'EDGE_LOG_SAMPLE_RATE'}`}
                  hint="Edge worker reads this from its own env binding. Same always-keep rule for errors / slow." />
                <SafeguardRow label="Max ingest batch"
                  value={`${status.max_ingest_batch ?? '—'} records`}
                  hint="Posts above this size are rejected with 413. Set via LOGS_INGEST_MAX_BATCH." />
                <SafeguardRow label="CF GraphQL pull"
                  value={`every ${status.cf_pull_interval_s ?? '—'}s`}
                  hint={`Last run: ${status.cf_pull_last_run ? fmtTime(status.cf_pull_last_run) : 'never'}`} />
                <SafeguardRow label="Ingest token"
                  value={status.ingest_token_configured ? 'configured' : 'missing'}
                  hint="Rotate via the KEY button above. Old token keeps working until the worker secret is updated." />
                {status.cf_pull_24h && (
                  <div className="md:col-span-2">
                    <CfPullCostWidget
                      agg={status.cf_pull_24h}
                      history={status.cf_pull_history_recent}
                    />
                  </div>
                )}
                {/* Task #952 — rolling 24h saturation count. Mirrors
                    the email/in-app alert text so an operator sees
                    the same number across surfaces. */}
                <SafeguardRow label="CF pull saturated minutes (24h)"
                  value={`${status.cf_pull_saturated_minutes_24h ?? 0} min`}
                  hint={
                    (status.cf_pull_saturated_minutes_24h ?? 0) > 0
                      ? 'A non-zero count means the GraphQL bucket cap (200 distinct buckets/minute) is being hit and some traffic was dropped from the explorer for those minutes. If this stays non-zero across days, drop `country` or `coloCode` from the dimension cut.'
                      : 'No 1-minute windows hit the GraphQL bucket cap (200 distinct buckets) in the last 24h.'
                  } />
              </div>
            )}
          </div>
        )}

        {/* ── Filter bar ──────────────────────────────────────────── */}
        <div className="bg-slate-50 border rounded p-3 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Filter className="w-4 h-4 text-slate-500" />
            <span className="text-xs uppercase tracking-wider text-slate-500">Time</span>
            {TIME_PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => setPreset(p.id)}
                className={`px-2 py-1 text-xs rounded border ${
                  preset === p.id
                    ? 'bg-indigo-600 text-white border-indigo-600'
                    : 'bg-white hover:bg-slate-100'
                }`}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs uppercase tracking-wider text-slate-500">Source</span>
            {SOURCE_OPTIONS.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => toggleArrayFilter('sources', s.id)}
                className={`px-2 py-1 text-xs rounded border ${
                  filters.sources.includes(s.id)
                    ? 'bg-indigo-600 text-white border-indigo-600'
                    : 'bg-white hover:bg-slate-100'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs uppercase tracking-wider text-slate-500">Level</span>
            {LEVEL_OPTIONS.map((lv) => (
              <button
                key={lv}
                type="button"
                onClick={() => toggleArrayFilter('levels', lv)}
                className={`px-2 py-1 text-xs rounded border capitalize ${
                  filters.levels.includes(lv)
                    ? 'bg-indigo-600 text-white border-indigo-600'
                    : 'bg-white hover:bg-slate-100'
                }`}
              >
                {lv}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-2">
            <input
              type="text"
              placeholder="Route prefix (e.g. /api/admin/)"
              value={filters.route_prefix}
              onChange={(e) => setFilters((f) => ({ ...f, route_prefix: e.target.value }))}
              className="px-3 py-2 text-sm border rounded"
            />
            <input
              type="text"
              placeholder="Correlation / ray id"
              value={filters.correlation_id}
              onChange={(e) => setFilters((f) => ({ ...f, correlation_id: e.target.value }))}
              className="px-3 py-2 text-sm border rounded"
            />
            <div className="relative">
              <Search className="w-4 h-4 absolute top-2.5 left-2 text-slate-400" />
              <input
                type="text"
                placeholder="Free text (message / route)"
                value={filters.q}
                onChange={(e) => setFilters((f) => ({ ...f, q: e.target.value }))}
                className="pl-7 pr-3 py-2 text-sm border rounded w-full"
              />
            </div>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <input
              type="number"
              min="0" max="999"
              placeholder="Status min (e.g. 400)"
              value={filters.status_min}
              onChange={(e) => setFilters((f) => ({ ...f, status_min: e.target.value }))}
              className="px-3 py-2 text-sm border rounded"
            />
            <input
              type="number"
              min="0" max="999"
              placeholder="Status max (e.g. 599)"
              value={filters.status_max}
              onChange={(e) => setFilters((f) => ({ ...f, status_max: e.target.value }))}
              className="px-3 py-2 text-sm border rounded"
            />
            <button
              type="button"
              onClick={() => load()}
              className="px-3 py-2 text-sm rounded bg-indigo-600 text-white hover:bg-indigo-700 col-span-2 md:col-span-1"
            >
              Apply filters
            </button>
            <button
              type="button"
              onClick={() => {
                setFilters({
                  sources: [], levels: [], route_prefix: '',
                  correlation_id: '', q: '', status_min: '', status_max: '',
                });
              }}
              className="px-3 py-2 text-sm rounded border hover:bg-slate-100 col-span-2 md:col-span-1"
            >
              Reset
            </button>
          </div>
        </div>

        {/* ── Admin Actions view (Task #296) ───────────────────────── */}
        {showAdminActions && (
          <div data-testid="admin-logs-actions-pane" className="border rounded bg-white p-4">
            <AdminActivityLog adminToken={adminToken} onNavigate={onNavigate} />
          </div>
        )}

        {/* ── Result count + error ────────────────────────────────── */}
        {!showAdminActions && (
        <div className="flex items-center justify-between text-sm text-slate-600">
          <span>
            Showing {logs.length}
            {total ? ` of ${total}${totalCapped ? '+' : ''}` : ''} entries
          </span>
          {error && <span className="text-red-600">{error}</span>}
        </div>
        )}

        {/* ── Table ──────────────────────────────────────────────── */}
        {!showAdminActions && (
        <div className="border rounded overflow-x-auto bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600 text-xs uppercase">
              <tr>
                <th className="px-2 py-2 w-6"></th>
                <th className="text-left px-3 py-2">Time</th>
                <th className="text-left px-3 py-2">Source</th>
                <th className="text-left px-3 py-2">Level</th>
                <th className="text-left px-3 py-2">Status</th>
                <th className="text-left px-3 py-2">Route</th>
                <th className="text-left px-3 py-2">Country / Colo</th>
                <th className="text-left px-3 py-2">Cache</th>
                <th className="text-right px-3 py-2">Duration</th>
                <th className="text-left px-3 py-2">Correlation</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((row, idx) => {
                const lvl = LEVEL_ICON[row.level] || LEVEL_ICON.info;
                const Icon = lvl.Icon;
                const key = `${rowKeyOf(row)}|${idx}`;
                const expandKey = rowKeyOf(row);
                const isExpanded = expandedIds.has(expandKey);
                return (
                  <Fragment key={key}>
                    <tr className="border-t hover:bg-slate-50">
                      <td className="px-2 py-1.5 align-top">
                        <button
                          type="button"
                          onClick={() => toggleExpanded(expandKey)}
                          className="text-slate-400 hover:text-slate-700"
                          title={isExpanded ? 'Collapse row' : 'Expand to see full JSON'}
                          aria-label={isExpanded ? 'Collapse row' : 'Expand row'}
                          aria-expanded={isExpanded}
                          data-testid={`row-expand-${idx}`}
                        >
                          {isExpanded
                            ? <ChevronDown className="w-4 h-4" />
                            : <ChevronRight className="w-4 h-4" />}
                        </button>
                      </td>
                      <td className="px-3 py-1.5 whitespace-nowrap font-mono text-xs">
                        {fmtTime(row.timestamp)}
                      </td>
                      <td className="px-3 py-1.5 whitespace-nowrap">
                        <span className={`inline-block px-2 py-0.5 text-xs rounded border ${SOURCE_BADGE[row.source] || 'bg-slate-100 text-slate-700'}`}>
                          {row.source}
                        </span>
                      </td>
                      <td className="px-3 py-1.5">
                        <span className={`inline-flex items-center gap-1 text-xs ${lvl.color}`}>
                          <Icon className="w-3 h-3" /> {row.level}
                        </span>
                      </td>
                      <td className={`px-3 py-1.5 font-mono text-xs ${statusClass(row.status)}`}>
                        {row.status ?? '—'}
                      </td>
                      <td className="px-3 py-1.5 max-w-md font-mono text-xs"
                          title={row.message || row.route}>
                        <div className="truncate">
                          <span className="text-slate-400 mr-1">{row.method || '—'}</span>
                          {row.route || '—'}
                        </div>
                        {row.message && row.message !== `${row.method} ${row.route} → ${row.status}` && (
                          <div className="text-slate-500 text-[10px] truncate mt-0.5">{row.message}</div>
                        )}
                      </td>
                      <td className="px-3 py-1.5 whitespace-nowrap text-xs">
                        {row.country || '—'}{row.colo ? ` / ${row.colo}` : ''}
                      </td>
                      <td className="px-3 py-1.5 text-xs">{row.cache || '—'}</td>
                      <td className="px-3 py-1.5 text-right font-mono text-xs">
                        {fmtMs(row.duration_ms)}
                      </td>
                      <td className="px-3 py-1.5">
                        {row.correlation_id ? (
                          <span className="inline-flex items-center gap-1">
                            <button
                              type="button"
                              onClick={() => openTrace(row.correlation_id)}
                              className="inline-flex items-center gap-1 text-xs text-indigo-600 hover:underline font-mono"
                              title="Open trace for this correlation id"
                            >
                              <LinkIcon className="w-3 h-3" />
                              {String(row.correlation_id).slice(0, 14)}
                            </button>
                            <button
                              type="button"
                              onClick={() => copyToClipboard(row.correlation_id, 'correlation id')}
                              className="text-slate-400 hover:text-slate-700"
                              title="Copy correlation id to clipboard"
                              aria-label="Copy correlation id"
                              data-testid={`copy-cid-${idx}`}
                            >
                              <Copy className="w-3 h-3" />
                            </button>
                          </span>
                        ) : '—'}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr className="border-t bg-slate-50/60">
                        <td colSpan={10} className="px-4 py-3">
                          <div className="flex items-center justify-between mb-2">
                            <div className="text-xs text-slate-500 font-mono">
                              Full JSON payload · _id={String(row._id || '—').slice(0, 32)}
                            </div>
                            <button
                              type="button"
                              onClick={() => copyToClipboard(JSON.stringify(row, null, 2), 'JSON payload')}
                              className="inline-flex items-center gap-1 text-xs text-slate-600 hover:text-slate-900 border rounded px-2 py-0.5 bg-white"
                              title="Copy full JSON payload"
                              data-testid={`copy-json-${idx}`}
                            >
                              <Copy className="w-3 h-3" /> Copy JSON
                            </button>
                          </div>
                          <pre className="text-[11px] leading-snug font-mono whitespace-pre-wrap break-all bg-white border rounded p-3 max-h-80 overflow-auto"
                               data-testid={`row-json-${idx}`}>
{JSON.stringify(row, null, 2)}
                          </pre>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
              {!loading && logs.length === 0 && (
                <tr><td colSpan={10} className="text-center py-10 text-slate-400">
                  No log entries match the current filters.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
        )}

        {!showAdminActions && nextBefore && (
          <div className="flex justify-center">
            <button
              type="button"
              onClick={() => load({ append: true, before: nextBefore })}
              disabled={loading}
              className="px-4 py-2 text-sm border rounded hover:bg-slate-50 disabled:opacity-50"
            >
              {loading ? 'Loading…' : 'Load older entries'}
            </button>
          </div>
        )}

        {/* ── Trace modal ────────────────────────────────────────── */}
        {traceModal && (
          <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
               onClick={() => setTraceModal(null)}>
            <div className="bg-white rounded-lg shadow-xl max-w-4xl w-full max-h-[80vh] overflow-hidden flex flex-col"
                 onClick={(e) => e.stopPropagation()}>
              <div className="flex items-center justify-between px-4 py-3 border-b">
                <div>
                  <h3 className="font-semibold">Trace</h3>
                  <p className="text-xs text-slate-500 font-mono">{traceModal.cid}</p>
                </div>
                <button
                  type="button"
                  onClick={() => setTraceModal(null)}
                  className="p-1 hover:bg-slate-100 rounded"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="overflow-auto p-4 space-y-2">
                {traceModal.loading && <p className="text-slate-500">Loading trace…</p>}
                {traceModal.error && <p className="text-red-600">{traceModal.error}</p>}
                {!traceModal.loading && !traceModal.error && traceModal.logs.length === 0 && (
                  <p className="text-slate-500">No entries share this correlation id.</p>
                )}
                {traceModal.logs.map((row, i) => (
                  <div key={i} className="border rounded p-2 text-xs font-mono">
                    <div className="flex items-center gap-2 mb-1">
                      <span className={`inline-block px-1.5 py-0.5 rounded border ${SOURCE_BADGE[row.source] || 'bg-slate-100'}`}>
                        {row.source}
                      </span>
                      <span className={statusClass(row.status)}>{row.status ?? '—'}</span>
                      <span className="text-slate-400">{fmtTime(row.timestamp)}</span>
                      <span className="ml-auto">{fmtMs(row.duration_ms)}</span>
                    </div>
                    <div className="text-slate-700">{row.method} {row.route}</div>
                    {row.message && row.message !== `${row.method} ${row.route} → ${row.status}` && (
                      <div className="text-slate-500 mt-1">{row.message}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ── Rotate-token disclosure ─────────────────────────────── */}
        {rotatedToken && (
          <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
               onClick={() => setRotatedToken(null)}>
            <div className="bg-white rounded-lg shadow-xl max-w-lg w-full p-4 space-y-3"
                 onClick={(e) => e.stopPropagation()}>
              <h3 className="font-semibold flex items-center gap-2">
                <KeyRound className="w-4 h-4 text-amber-600" />
                New ingest token
              </h3>
              <p className="text-sm text-slate-600">
                Copy this token now — it will not be shown again. Update the
                ``LOG_INGEST_TOKEN`` secret on the edge worker (and any other
                producer) to start posting under the new credential.
              </p>
              <textarea
                readOnly
                value={rotatedToken}
                className="w-full font-mono text-xs border rounded p-2"
                rows={3}
                onFocus={(e) => e.target.select()}
              />
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    navigator.clipboard?.writeText(rotatedToken);
                    toast.success('Token copied to clipboard');
                  }}
                  className="px-3 py-1.5 text-sm rounded bg-indigo-600 text-white hover:bg-indigo-700"
                >
                  Copy
                </button>
                <button
                  type="button"
                  onClick={() => setRotatedToken(null)}
                  className="px-3 py-1.5 text-sm rounded border hover:bg-slate-50"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        )}

        {/* ── Clear-with-typed-confirm ────────────────────────────── */}
        {confirmClearOpen && (
          <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
               onClick={() => !clearing && setConfirmClearOpen(false)}>
            <div className="bg-white rounded-lg shadow-xl max-w-md w-full p-4 space-y-3"
                 onClick={(e) => e.stopPropagation()}>
              <h3 className="font-semibold flex items-center gap-2 text-red-700">
                <Trash2 className="w-4 h-4" />
                Clear unified logs
              </h3>
              <p className="text-sm text-slate-600">
                {(filters.sources && filters.sources.length)
                  ? `This will delete every log entry whose source is one of: ${filters.sources.join(', ')}. `
                  : 'This will delete EVERY log entry across every source. '}
                The action is recorded in the Activity Log.
              </p>
              <p className="text-xs text-slate-500">Type <code className="bg-slate-100 px-1">CLEAR</code> to confirm:</p>
              <input
                type="text"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                className="w-full px-3 py-2 border rounded font-mono"
                autoFocus
              />
              <div className="flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => { setConfirmClearOpen(false); setConfirmText(''); }}
                  disabled={clearing}
                  className="px-3 py-1.5 text-sm rounded border hover:bg-slate-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={handleClearConfirmed}
                  disabled={confirmText !== 'CLEAR' || clearing}
                  className="px-3 py-1.5 text-sm rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
                >
                  {clearing ? 'Clearing…' : 'Clear logs'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </SectionErrorBoundary>
  );
}

/**
 * Task #952 — In-app banner for the Cloudflare-pull saturation alerter.
 *
 * Renders a red callout when:
 *   - the most recent CF pull tick reported one or more saturated
 *     1-minute windows (i.e. the >200-buckets cap was hit even at the
 *     pagination floor and some traffic was dropped from the
 *     explorer for those minutes), OR
 *   - the rolling 24h count of saturated minutes is non-zero (i.e.
 *     traffic was lost at some point in the last day even if the
 *     latest tick fit cleanly).
 *
 * The same fields are surfaced via in-app notification + email by
 * the saturation alerter; this banner is the "operator opens the
 * explorer" entry point so they don't have to wait on the inbox.
 */
function CfPullSaturationBanner({ status }) {
  if (!status) return null;
  const lastWindows = Array.isArray(status.cf_pull_last_saturated_windows)
    ? status.cf_pull_last_saturated_windows
    : [];
  const count24h = Number(status.cf_pull_saturated_minutes_24h ?? 0);
  if (lastWindows.length === 0 && count24h <= 0) return null;
  const sample = lastWindows.slice(0, 3).map((w) => {
    if (Array.isArray(w) && w[0]) return fmtTime(w[0]);
    if (typeof w === 'string') return fmtTime(w);
    return '—';
  });
  const more = lastWindows.length > sample.length
    ? `, +${lastWindows.length - sample.length} more`
    : '';
  return (
    <div
      className="border border-red-300 bg-red-50 rounded p-3 flex items-start gap-3"
      role="alert"
      data-testid="cf-pull-saturation-banner"
    >
      <AlertOctagon className="w-5 h-5 text-red-600 mt-0.5 shrink-0" />
      <div className="flex-1 text-sm">
        <div className="font-semibold text-red-800">
          Cloudflare pull saturation —{' '}
          {lastWindows.length > 0
            ? `${lastWindows.length} minute${lastWindows.length === 1 ? '' : 's'} hit the 200-bucket cap`
            : `${count24h} saturated minute${count24h === 1 ? '' : 's'} in last 24h`}
        </div>
        <div className="text-red-700 mt-1">
          The CF GraphQL pull truncates at {status.cf_pull_limit ?? 200} distinct
          (path, status, colo, host, country, cache, method) buckets per minute.
          For the listed minutes the cap was hit even at the 1-minute floor, so
          some edge traffic was dropped from the explorer and will not appear
          in any filter or export.
          {lastWindows.length > 0 && (
            <>
              {' '}
              Latest saturated windows:{' '}
              <span className="font-mono">{sample.join(', ')}{more}</span>.
            </>
          )}
        </div>
        <div className="text-red-700 mt-1">
          Saturated minutes in last 24h:{' '}
          <span className="font-mono font-semibold">{count24h}</span>.{' '}
          {count24h > 5
            ? 'Trend is structural — drop `country` or `coloCode` from the GraphQL group-by so buckets aggregate to a coarser cut that fits.'
            : 'If this stays at 0 across days, no action needed — a single saturated minute under traffic surge is expected.'}
        </div>
      </div>
    </div>
  );
}

function SafeguardRow({ label, value, hint }) {
  return (
    <div className="bg-white border rounded p-2">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-slate-500">{label}</span>
        <span className="font-mono text-slate-900">{value}</span>
      </div>
      {hint && <div className="mt-1 text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}

/**
 * Task #960 — Inline SVG sparkline of per-tick CF GraphQL `calls` (and
 * a faint subdivisions overlay) over the last few hours. The worst
 * tick is highlighted with a red dot so a sudden 1→50 calls/tick
 * fan-out drift jumps out at a glance — without forcing the operator
 * to leave the safeguards card.
 *
 * Each datapoint gets an SVG `<title>` element so hover reveals
 * timestamp + values; a sibling sr-only summary lists every point so
 * screen readers (and tests) don't depend on visual hover state.
 */
function CfPullSparkline({ points }) {
  if (!Array.isArray(points) || points.length < 2) return null;
  const width = 160;
  const height = 36;
  const pad = 3;
  const innerH = height - pad * 2;
  const innerW = width - pad * 2;
  const calls = points.map((p) => Math.max(0, Number(p.calls) || 0));
  const subs  = points.map((p) => Math.max(0, Number(p.subdivisions) || 0));
  const yMax  = Math.max(1, ...calls, ...subs);
  const xStep = (points.length - 1) > 0 ? innerW / (points.length - 1) : 0;
  const xAt   = (i) => pad + i * xStep;
  const yAt   = (v) => pad + innerH - (v / yMax) * innerH;
  const callsPath = calls.map((c, i) => `${i === 0 ? 'M' : 'L'}${xAt(i).toFixed(2)},${yAt(c).toFixed(2)}`).join(' ');
  const subsPath  = subs .map((s, i) => `${i === 0 ? 'M' : 'L'}${xAt(i).toFixed(2)},${yAt(s).toFixed(2)}`).join(' ');
  // Worst tick = max calls (ties → earliest, so the FIRST spike is
  // what gets highlighted, which matches "when did the drift start?").
  let worstIdx = 0;
  for (let i = 1; i < calls.length; i++) {
    if (calls[i] > calls[worstIdx]) worstIdx = i;
  }
  const worst = points[worstIdx];
  const ariaLabel =
    `CF pull cost sparkline: ${points.length} ticks, ` +
    `peak ${calls[worstIdx]} call${calls[worstIdx] === 1 ? '' : 's'} ` +
    `at ${worst.ts}`;
  const showSubs = subs.some((s) => s > 0);
  return (
    <span className="inline-flex items-center" data-testid="cf-pull-cost-sparkline-wrap">
      <svg
        width={width}
        height={height}
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={ariaLabel}
        data-testid="cf-pull-cost-sparkline"
        className="block"
      >
        <title>{ariaLabel}</title>
        {showSubs && (
          <path
            d={subsPath}
            fill="none"
            stroke="#f59e0b"
            strokeWidth="1"
            strokeDasharray="2,2"
            opacity="0.7"
            data-testid="cf-pull-cost-sparkline-subs"
          />
        )}
        <path
          d={callsPath}
          fill="none"
          stroke="#4f46e5"
          strokeWidth="1.5"
          data-testid="cf-pull-cost-sparkline-calls"
        />
        {points.map((p, i) => {
          const isWorst = i === worstIdx;
          const tip =
            `${p.ts} — ${p.calls} call${p.calls === 1 ? '' : 's'}` +
            `, ${p.subdivisions ?? 0} subdivision${(p.subdivisions ?? 0) === 1 ? '' : 's'}` +
            ((p.saturated ?? 0) > 0 ? `, ${p.saturated} saturated` : '');
          // Render a tiny visible dot for every datapoint (so hover
          // is actually discoverable — operators shouldn't have to
          // know an invisible target is there) plus an oversized
          // transparent hit-area to make the tooltip easy to grab
          // even when the sparkline is dense.
          return (
            <Fragment key={i}>
              <circle
                cx={xAt(i)}
                cy={yAt(calls[i])}
                r={isWorst ? 2.5 : 1.25}
                fill={isWorst ? '#dc2626' : '#4f46e5'}
                opacity={isWorst ? 1 : 0.55}
                data-testid={isWorst ? 'cf-pull-cost-sparkline-worst' : undefined}
              >
                <title>{tip}</title>
              </circle>
              {/* Oversized transparent overlay so hover discovers the
                  tooltip even on the dense interior datapoints. */}
              <circle
                cx={xAt(i)}
                cy={yAt(calls[i])}
                r={5}
                fill="transparent"
                style={{ pointerEvents: 'all' }}
              >
                <title>{tip}</title>
              </circle>
            </Fragment>
          );
        })}
      </svg>
      <span className="sr-only" data-testid="cf-pull-cost-sparkline-sr">
        Worst tick at {worst.ts}: {calls[worstIdx]} calls,{' '}
        {worst.subdivisions ?? 0} subdivisions
        {(worst.saturated ?? 0) > 0 ? `, ${worst.saturated} saturated minutes` : ''}.
      </span>
    </span>
  );
}

function CfPullCostWidget({ agg, history }) {
  if (!agg || typeof agg !== 'object') return null;
  const ticks = agg.ticks ?? 0;
  const totalCalls = agg.total_calls ?? 0;
  const totalSubs = agg.total_subdivisions ?? 0;
  const totalSat = agg.total_saturated ?? 0;
  const maxCalls = agg.max_calls ?? 0;
  const maxSubs = agg.max_subdivisions ?? 0;
  const subdividedPct = agg.subdivided_pct ?? 0;
  const windowS = agg.window_s ?? 0;
  const windowH = windowS > 0 ? Math.max(1, Math.round(windowS / 3600)) : 0;
  const windowLabel = windowH > 0
    ? (windowH >= 23 ? '24h' : `~${windowH}h`)
    : '<1h';
  const heavy = subdividedPct >= 50 || maxSubs >= 4 || totalSat > 0;
  const hasSparkline = Array.isArray(history) && history.length >= 2;
  return (
    <div
      className={`border rounded p-2 ${heavy ? 'bg-amber-50 border-amber-300' : 'bg-white'}`}
      data-testid="cf-pull-cost-widget"
    >
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-slate-500">CF pull cost ({windowLabel})</span>
        <div className="flex items-center gap-2 ml-auto">
          {hasSparkline && <CfPullSparkline points={history} />}
          <span className="font-mono text-slate-900" data-testid="cf-pull-cost-totals">
            {totalCalls.toLocaleString()} calls
            {totalSubs > 0 && <> · {totalSubs.toLocaleString()} subdivisions</>}
          </span>
        </div>
      </div>
      <div className="mt-1 text-[11px] text-slate-500 flex flex-wrap gap-x-3 gap-y-0.5">
        <span>{ticks} {ticks === 1 ? 'tick' : 'ticks'} aggregated</span>
        <span>peak: {maxCalls} calls / {maxSubs} subdivisions per tick</span>
        <span data-testid="cf-pull-cost-subdivided-pct">{subdividedPct}% of ticks paginated</span>
        {totalSat > 0 && (
          <span className="text-amber-700 font-semibold" data-testid="cf-pull-cost-saturated">
            {totalSat} saturated minute{totalSat === 1 ? '' : 's'} (data lost)
          </span>
        )}
        {hasSparkline && (
          <span className="text-slate-400">
            sparkline: last {history.length} {history.length === 1 ? 'tick' : 'ticks'} (calls
            {history.some((p) => (p.subdivisions ?? 0) > 0) && <> + subdivisions overlay</>})
          </span>
        )}
      </div>
    </div>
  );
}

function StatusPill({ status, statusLoading }) {
  if (statusLoading) {
    return <span className="text-xs text-slate-400 ml-2">loading status…</span>;
  }
  const paused = !!status?.paused;
  return (
    <div className="ml-2 inline-flex items-center gap-2 text-xs">
      <span className={`inline-block w-2 h-2 rounded-full ${paused ? 'bg-amber-500' : 'bg-emerald-500'}`} />
      <span className="text-slate-700">{paused ? 'Paused' : 'Active'}</span>
      {status?.ttl_days != null && (
        <span className="text-slate-400">· TTL {status.ttl_days}d</span>
      )}
      {status?.cf_pull_last_run && (
        <span className="text-slate-400">· last CF pull {fmtTime(status.cf_pull_last_run)}</span>
      )}
      {!status?.ingest_token_configured && (
        <span className="text-amber-600 font-semibold">· ingest token not set</span>
      )}
    </div>
  );
}
