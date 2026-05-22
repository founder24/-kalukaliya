import { useCallback, useEffect, useState } from 'react';
import {
  Loader2, Activity, ArrowRight, Sparkles, Check, X, RotateCcw,
  RefreshCw, AlertTriangle, ShieldCheck, Send,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  adminSeoInternalLinksStatus, adminSeoInternalLinksPending,
  adminSeoInternalLinksHistory, adminSeoInternalLinksApprove,
  adminSeoInternalLinksReject, adminSeoInternalLinksRevert,
  adminSeoInternalLinksTrigger,
} from '@/utils/api';

// Task #939 — Action labels for the agentic linker history table.
// Mirror the ACTION_* constants in seo_internal_linker.py so a
// future rename trips both the frontend and the backend tests.
const LINKER_ACTION_LABELS = {
  auto_applied:        { label: 'Auto-applied',    color: '#10b981', bg: '#ecfdf5' },
  drafted:             { label: 'Drafted',         color: '#f59e0b', bg: '#fffbeb' },
  rejected:            { label: 'Rejected',        color: '#9ca3af', bg: '#f9fafb' },
  reverted:            { label: 'Reverted',        color: '#9ca3af', bg: '#f9fafb' },
  failed:              { label: 'Failed',          color: '#ef4444', bg: '#fef2f2' },
  skipped_duplicate:   { label: 'Skipped (dup)',   color: '#9ca3af', bg: '#f9fafb' },
  skipped_no_anchor:   { label: 'Skipped (anchor)', color: '#9ca3af', bg: '#f9fafb' },
};

function fmt(dt) {
  if (!dt) return '—';
  try { return new Date(dt).toLocaleString(); } catch { return dt; }
}

function LinkerActionPill({ action }) {
  const cfg = LINKER_ACTION_LABELS[action] || { label: action || '—', color: '#6b7280', bg: '#f3f4f6' };
  return (
    <span
      className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border"
      style={{ color: cfg.color, background: cfg.bg, borderColor: cfg.color + '33' }}
    >
      {cfg.label}
    </span>
  );
}

function LinkerStatusPill({ status }) {
  if (!status) return null;
  const budget = status.budget || {};
  const enabled = status.enabled;
  const usedFrac = budget.auto_cap ? budget.auto_used / budget.auto_cap : 0;
  const heavy = usedFrac >= 0.9;
  const containerCls = !enabled
    ? 'bg-gray-50 border-gray-200'
    : heavy
      ? 'bg-amber-50 border-amber-200'
      : 'bg-emerald-50 border-emerald-200';
  const Icon = !enabled ? AlertTriangle : ShieldCheck;
  const headerText = !enabled
    ? 'Internal-linker — disabled (env)'
    : heavy
      ? 'Internal-linker — auto-cap nearly reached'
      : 'Internal-linker — healthy';
  return (
    <div className={`rounded-2xl p-4 border ${containerCls}`} data-testid="linker-status-tile">
      <div className="flex items-start gap-3">
        <Icon size={18} className={enabled ? (heavy ? 'text-amber-500' : 'text-emerald-500') : 'text-gray-400'} />
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold ${heavy ? 'text-amber-600' : enabled ? 'text-emerald-600' : 'text-gray-500'}`}>
            {headerText}
          </p>
          <p className="text-[11px] text-gray-500 mt-0.5">
            Today {budget.date}: auto {budget.auto_used ?? 0}/{budget.auto_cap ?? 0} ·
            {' '}pending {status.pendingCount ?? 0} ·
            {' '}auto-applied last 24h {status.recentAutoApplied24h ?? 0}
          </p>
          {status.config && (
            <p className="text-[11px] text-gray-400 mt-0.5">
              Threshold: {status.config.autoApplyThreshold} ·
              {' '}Links/target: {status.config.minLinksPerTarget}-{status.config.maxLinksPerTarget} ·
              {' '}Pool: {status.config.candidatePoolSize} ·
              {' '}Nightly top-N: {status.config.nightlyTopN}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function DiffPreview({ before, after }) {
  if (!before && !after) return <span className="text-[10px] text-gray-300">no preview</span>;
  return (
    <div className="text-[11px] space-y-1">
      <div className="rounded bg-red-50 border border-red-100 px-2 py-1 text-gray-600 font-mono whitespace-pre-wrap break-words">
        − {before || ''}
      </div>
      <div className="rounded bg-emerald-50 border border-emerald-100 px-2 py-1 text-gray-700 font-mono whitespace-pre-wrap break-words">
        + {after || ''}
      </div>
    </div>
  );
}

function LinkerAgentPanel({ adminToken }) {
  const [status, setStatus]           = useState(null);
  const [pending, setPending]         = useState([]);
  const [history, setHistory]         = useState([]);
  const [statusLoading, setStatusLoading] = useState(false);
  const [pendingLoading, setPendingLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [actionId, setActionId]       = useState(null);
  const [triggerPageId, setTriggerPageId] = useState('');
  const [triggering, setTriggering]   = useState(false);
  const [historyDays, setHistoryDays] = useState(7);
  const [actionFilter, setActionFilter] = useState('');

  const loadStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const res = await adminSeoInternalLinksStatus(adminToken);
      setStatus(res.data);
    } catch { toast.error('Failed to load linker status'); }
    finally { setStatusLoading(false); }
  }, [adminToken]);

  const loadPending = useCallback(async () => {
    setPendingLoading(true);
    try {
      const res = await adminSeoInternalLinksPending(adminToken, { limit: 50 });
      setPending(res.data?.items || []);
    } catch { toast.error('Failed to load pending suggestions'); }
    finally { setPendingLoading(false); }
  }, [adminToken]);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const res = await adminSeoInternalLinksHistory(adminToken, {
        days: historyDays, limit: 100, action: actionFilter || null,
      });
      setHistory(res.data?.items || []);
    } catch { toast.error('Failed to load linker history'); }
    finally { setHistoryLoading(false); }
  }, [adminToken, historyDays, actionFilter]);

  useEffect(() => { loadStatus(); }, [loadStatus]);
  useEffect(() => { loadPending(); }, [loadPending]);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  const refreshAll = () => { loadStatus(); loadPending(); loadHistory(); };

  const handleApprove = async (rec) => {
    setActionId(rec.id);
    try {
      await adminSeoInternalLinksApprove(adminToken, rec.id);
      toast.success(`Approved → ${rec.sourceTopicTitle || rec.sourcePageId}`);
      await Promise.all([loadPending(), loadHistory(), loadStatus()]);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Approve failed');
    } finally { setActionId(null); }
  };

  const handleReject = async (rec) => {
    setActionId(rec.id);
    try {
      await adminSeoInternalLinksReject(adminToken, rec.id);
      toast.success('Rejected');
      await Promise.all([loadPending(), loadHistory()]);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Reject failed');
    } finally { setActionId(null); }
  };

  const handleRevert = async (rec) => {
    if (!confirm(`Remove the inserted link from "${rec.sourceTopicTitle || rec.sourcePageId}"?`)) return;
    setActionId(rec.id);
    try {
      await adminSeoInternalLinksRevert(adminToken, rec.id);
      toast.success('Reverted');
      await Promise.all([loadHistory(), loadStatus()]);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Revert failed');
    } finally { setActionId(null); }
  };

  const handleTrigger = async (e) => {
    e.preventDefault();
    const pid = triggerPageId.trim();
    if (!pid) return;
    setTriggering(true);
    try {
      const res = await adminSeoInternalLinksTrigger(adminToken, { page_id: pid });
      toast.success(`Created ${res.data?.rows_created ?? 0} suggestion(s)`);
      setTriggerPageId('');
      await Promise.all([loadPending(), loadHistory(), loadStatus()]);
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Trigger failed');
    } finally { setTriggering(false); }
  };

  return (
    <div className="space-y-4" data-testid="linker-agent-panel">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <LinkerStatusPill status={status} />
        </div>
        <button onClick={refreshAll}
          disabled={statusLoading || pendingLoading || historyLoading}
          className="h-8 px-3 rounded-lg text-xs font-semibold border border-gray-200 hover:bg-gray-50 transition-colors text-gray-500 inline-flex items-center gap-1.5"
          data-testid="linker-refresh">
          <RefreshCw size={12} className={(statusLoading || pendingLoading || historyLoading) ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      <div className="rounded-xl border border-gray-200 p-3 bg-white">
        <div className="flex items-center gap-2 mb-2">
          <Sparkles size={13} className="text-violet-500" />
          <p className="text-xs font-semibold text-gray-700">Manual trigger</p>
          <span className="text-[10px] text-gray-400">— re-run the linker against a target page id</span>
        </div>
        <form onSubmit={handleTrigger} className="flex items-center gap-2">
          <input
            type="text"
            value={triggerPageId}
            onChange={(e) => setTriggerPageId(e.target.value)}
            placeholder="page_id (e.g. p-abc123)"
            className="flex-1 h-8 px-3 rounded-lg text-xs border border-gray-200 focus:outline-none focus:border-violet-400 font-mono"
            data-testid="linker-trigger-input"
          />
          <button type="submit" disabled={triggering || !triggerPageId.trim()}
            className="h-8 px-3 rounded-lg text-xs font-semibold bg-violet-50 border border-violet-200 hover:bg-violet-100 transition-colors text-violet-600 inline-flex items-center gap-1.5 disabled:opacity-50"
            data-testid="linker-trigger-submit">
            {triggering ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
            Trigger
          </button>
        </form>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white">
        <div className="px-3 py-2 border-b border-gray-100 flex items-center gap-2">
          <p className="text-xs font-semibold text-gray-700">Pending suggestions</p>
          <span className="text-[10px] text-gray-400">({pending.length})</span>
        </div>
        {pendingLoading && pending.length === 0 ? (
          <div className="px-3 py-6 text-center text-gray-400 text-xs">
            <Loader2 size={14} className="inline animate-spin mr-2" /> Loading…
          </div>
        ) : pending.length === 0 ? (
          <div className="px-3 py-6 text-center text-gray-400 text-xs" data-testid="linker-pending-empty">
            No pending suggestions awaiting review.
          </div>
        ) : (
          <ul className="divide-y divide-gray-100">
            {pending.map((rec) => (
              <li key={rec.id} className="p-3 space-y-2" data-testid={`linker-pending-${rec.id}`}>
                <div className="flex items-start gap-2 text-xs">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-gray-700 font-semibold truncate">{rec.sourceTopicTitle || rec.sourcePageId || '—'}</span>
                      <ArrowRight size={11} className="text-gray-300 flex-shrink-0" />
                      <span className="text-violet-600 truncate">{rec.targetTopicTitle || rec.targetPageId || '—'}</span>
                    </div>
                    <div className="text-[10px] text-gray-400 mt-0.5 truncate">
                      anchor: <span className="font-mono">"{rec.anchorText}"</span> ·
                      {' '}confidence {Math.round((rec.confidence || 0) * 100)}% ·
                      {' '}{rec.reason || '—'}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button onClick={() => handleApprove(rec)} disabled={actionId === rec.id}
                      className="h-7 px-2 rounded-lg text-[11px] font-semibold bg-emerald-50 border border-emerald-200 hover:bg-emerald-100 text-emerald-600 inline-flex items-center gap-1 disabled:opacity-50"
                      data-testid={`linker-approve-${rec.id}`}>
                      {actionId === rec.id ? <Loader2 size={11} className="animate-spin" /> : <Check size={11} />}
                      Approve
                    </button>
                    <button onClick={() => handleReject(rec)} disabled={actionId === rec.id}
                      className="h-7 px-2 rounded-lg text-[11px] font-semibold bg-gray-50 border border-gray-200 hover:bg-gray-100 text-gray-600 inline-flex items-center gap-1 disabled:opacity-50"
                      data-testid={`linker-reject-${rec.id}`}>
                      <X size={11} /> Reject
                    </button>
                  </div>
                </div>
                <DiffPreview before={rec.diff?.beforeExcerpt} after={rec.diff?.afterExcerpt} />
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="text-xs font-semibold text-gray-700">Recent activity</p>
          <label className="text-[11px] text-gray-500 ml-2">Days:</label>
          <select value={historyDays} onChange={(e) => setHistoryDays(Number(e.target.value))}
            className="h-7 px-2 rounded-lg text-xs border border-gray-200" data-testid="linker-days">
            {[1, 3, 7, 14, 30].map((d) => <option key={d} value={d}>{d}d</option>)}
          </select>
          <label className="text-[11px] text-gray-500 ml-2">Action:</label>
          <select value={actionFilter} onChange={(e) => setActionFilter(e.target.value)}
            className="h-7 px-2 rounded-lg text-xs border border-gray-200" data-testid="linker-action-filter">
            <option value="">All</option>
            {Object.entries(LINKER_ACTION_LABELS).map(([k, v]) => (
              <option key={k} value={k}>{v.label}</option>
            ))}
          </select>
          <span className="text-[11px] text-gray-400 ml-auto">{history.length} row{history.length === 1 ? '' : 's'}</span>
        </div>
        <div className="rounded-xl border border-gray-200 overflow-hidden bg-white">
          <table className="w-full text-xs">
            <thead className="bg-gray-50 text-gray-500">
              <tr>
                <th className="text-left px-3 py-2 font-semibold">When</th>
                <th className="text-left px-3 py-2 font-semibold">Source → Target</th>
                <th className="text-left px-3 py-2 font-semibold">Anchor</th>
                <th className="text-left px-3 py-2 font-semibold">Conf.</th>
                <th className="text-left px-3 py-2 font-semibold">Action</th>
                <th className="text-right px-3 py-2 font-semibold"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {historyLoading && history.length === 0 ? (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-400">
                  <Loader2 size={14} className="inline animate-spin mr-2" /> Loading…
                </td></tr>
              ) : history.length === 0 ? (
                <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-400" data-testid="linker-history-empty">
                  No linker activity in this window.
                </td></tr>
              ) : history.map((row) => (
                <tr key={row.id} className="hover:bg-gray-50" data-testid={`linker-row-${row.id}`}>
                  <td className="px-3 py-2 text-gray-500 whitespace-nowrap">{fmt(row.createdAt)}</td>
                  <td className="px-3 py-2 text-gray-700">
                    <span className="truncate inline-block max-w-[160px] align-middle">{row.sourceTopicTitle || '—'}</span>
                    <ArrowRight size={10} className="inline text-gray-300 mx-1 align-middle" />
                    <span className="truncate inline-block max-w-[160px] text-violet-600 align-middle">{row.targetTopicTitle || '—'}</span>
                  </td>
                  <td className="px-3 py-2 font-mono text-[10px] text-gray-600 max-w-[140px] truncate" title={row.anchorText}>
                    {row.anchorText || '—'}
                  </td>
                  <td className="px-3 py-2 text-[11px] text-gray-500 tabular-nums">
                    {Math.round((row.confidence || 0) * 100)}%
                  </td>
                  <td className="px-3 py-2"><LinkerActionPill action={row.action} /></td>
                  <td className="px-3 py-2 text-right">
                    {row.action === 'auto_applied' && (
                      <button onClick={() => handleRevert(row)} disabled={actionId === row.id}
                        className="h-7 px-2 rounded-lg text-[11px] font-semibold bg-gray-50 border border-gray-200 hover:bg-amber-50 hover:border-amber-200 hover:text-amber-600 text-gray-600 inline-flex items-center gap-1 disabled:opacity-50"
                        data-testid={`linker-revert-${row.id}`}>
                        {actionId === row.id ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />}
                        Revert
                      </button>
                    )}
                    {row.action === 'reverted' && (
                      <span className="text-[10px] text-gray-400">reverted</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

export default function LinksTab({
  adminToken,
  linksData, linksLoading, handleLinksAnalyze,
  injectSlug, setInjectSlug, injecting, handleLinksInject,
}) {
  return (
    <div className="space-y-5">
      {adminToken && <LinkerAgentPanel adminToken={adminToken} />}

      <div className="rounded-xl border p-5 space-y-4" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm font-semibold text-gray-900">Internal Link Analysis</p>
            <p className="text-xs mt-0.5" style={{ color: '#9ca3af' }}>Analyzes all published pages and maps semantic link opportunities</p>
          </div>
          <button onClick={handleLinksAnalyze} disabled={linksLoading}
            className="flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold disabled:opacity-40"
            style={{ background: '#7c3aed', color: '#fff' }}>
            {linksLoading ? <Loader2 size={14} className="animate-spin" /> : <Activity size={14} />}
            {linksLoading ? 'Analyzing…' : 'Analyze Links'}
          </button>
        </div>
        {linksData && (
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: 'Pages Analyzed', val: linksData.pages_analyzed },
              { label: 'Opportunities', val: linksData.total_opportunities },
              { label: 'High Priority', val: linksData.high_priority },
            ].map(s => (
              <div key={s.label} className="rounded-lg p-3 text-center border" style={{ background: 'rgba(124,58,237,0.08)', borderColor: 'rgba(124,58,237,0.20)' }}>
                <p className="text-xl font-bold text-gray-900">{s.val ?? '—'}</p>
                <p className="text-[11px] mt-0.5" style={{ color: '#6b7280' }}>{s.label}</p>
              </div>
            ))}
          </div>
        )}
        {linksData?.top_opportunities?.length > 0 && (
          <div>
            <p className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color: '#9ca3af' }}>Top Link Opportunities</p>
            <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
              {linksData.top_opportunities.slice(0, 20).map((op, i) => (
                <div key={i} className="flex items-center gap-3 p-2.5 rounded-lg" style={{ background: '#f9fafb' }}>
                  <span className="text-[10px] font-mono px-1.5 py-0.5 rounded" style={{ background: 'rgba(124,58,237,0.15)', color: '#a78bfa' }}>
                    {(op.score * 100).toFixed(0)}%
                  </span>
                  <span className="text-xs flex-1 truncate" style={{ color: '#4b5563' }}>{op.source_slug}</span>
                  <ArrowRight size={11} style={{ color: '#9ca3af', flexShrink: 0 }} />
                  <span className="text-xs flex-1 truncate text-right" style={{ color: '#6b7280' }}>{op.target_slug}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="rounded-xl border p-5 space-y-3" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
        <p className="text-sm font-semibold text-gray-900">Inject Links into a Page</p>
        <div className="flex gap-2">
          <input value={injectSlug} onChange={e => setInjectSlug(e.target.value)}
            placeholder="page-slug (e.g. ahsec/class-11/physics/motion/notes)"
            className="flex-1 h-9 px-3 rounded-xl text-sm outline-none font-mono"
            style={{ background: '#f3f4f6', border: '1px solid #e5e7eb', color: '#374151' }} />
          <button onClick={handleLinksInject} disabled={injecting || !injectSlug.trim()}
            className="px-4 h-9 rounded-xl text-sm font-semibold disabled:opacity-40"
            style={{ background: '#059669', color: '#fff' }}>
            {injecting ? <Loader2 size={14} className="animate-spin" /> : 'Inject'}
          </button>
        </div>
        <p className="text-[11px]" style={{ color: '#9ca3af' }}>
          Injects contextually-relevant internal links into the specified page using semantic similarity.
        </p>
      </div>
    </div>
  );
}
