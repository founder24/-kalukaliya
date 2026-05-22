import { useEffect, useState, useCallback } from 'react';
import {
  Loader2, RefreshCw, AlertTriangle, ShieldCheck, Zap, Send,
  ArrowUpRight, RotateCcw, ExternalLink,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  adminSeoRemediationStatus,
  adminSeoRemediationHistory,
  adminSeoRemediationPromote,
  adminSeoRemediationTrigger,
  adminSeoRemediationCircuitReset,
} from '@/utils/api';

const ACTION_LABELS = {
  auto_republished:        { label: 'Auto-republished',  color: '#10b981', bg: '#ecfdf5' },
  drafted:                 { label: 'Drafted',           color: '#f59e0b', bg: '#fffbeb' },
  skipped_no_improvement:  { label: 'Skipped (regression)', color: '#9ca3af', bg: '#f9fafb' },
  skipped_budget:          { label: 'Skipped (budget)',  color: '#9ca3af', bg: '#f9fafb' },
  skipped_circuit_open:    { label: 'Skipped (circuit)', color: '#9ca3af', bg: '#f9fafb' },
  skipped_page_not_found:  { label: 'Page not found',    color: '#9ca3af', bg: '#f9fafb' },
  failed:                  { label: 'Failed',            color: '#ef4444', bg: '#fef2f2' },
};

// Kinds with active producers in the alerter (see
// routes/bot_discovery.py::_seo_health_alert_loop and the manual
// trigger admin endpoint). Keep this list in lockstep with
// seo_remediation_service.VALID_SIGNAL_KINDS — the backend rejects
// any signal whose kind is not in that allow-list.
const SIGNAL_LABELS = {
  url_404_spike:        '404 spike',
  seo_health_degraded:  'Health degraded',
  seo_health_critical:  'Health critical',
  manual_trigger:       'Manual trigger',
};

function fmt(dt) {
  if (!dt) return '—';
  try { return new Date(dt).toLocaleString(); } catch { return dt; }
}

function ActionPill({ action }) {
  const cfg = ACTION_LABELS[action] || { label: action || '—', color: '#6b7280', bg: '#f3f4f6' };
  return (
    <span
      className="inline-block px-2 py-0.5 rounded-full text-[10px] font-bold border"
      style={{ color: cfg.color, background: cfg.bg, borderColor: cfg.color + '33' }}
    >
      {cfg.label}
    </span>
  );
}

function DeltaCell({ before, after, delta }) {
  if (delta == null) return <span className="text-gray-300 text-[11px]">—</span>;
  const sign = delta > 0 ? '+' : '';
  const color = delta > 0 ? '#10b981' : delta < 0 ? '#ef4444' : '#6b7280';
  return (
    <div className="flex items-center gap-1.5 text-[11px]">
      <span className="text-gray-500 tabular-nums">{before ?? 0}</span>
      <span className="text-gray-300">→</span>
      <span className="text-gray-700 tabular-nums">{after ?? 0}</span>
      <span className="font-bold tabular-nums" style={{ color }}>
        ({sign}{delta})
      </span>
    </div>
  );
}

function StatusPill({ status, config }) {
  const budget = status?.budget;
  const circuit = status?.circuit;
  const enabled = status?.enabled;

  const isCircuitOpen = circuit?.is_open;
  const isOverBudget = budget && (
    budget.auto_used >= budget.auto_cap && budget.draft_used >= budget.draft_cap
  );

  const containerCls = !enabled
    ? 'bg-gray-50 border-gray-200'
    : isCircuitOpen
      ? 'bg-red-50 border-red-200'
      : isOverBudget
        ? 'bg-amber-50 border-amber-200'
        : 'bg-emerald-50 border-emerald-200';
  const iconColor = !enabled
    ? 'text-gray-400'
    : isCircuitOpen
      ? 'text-red-500'
      : isOverBudget
        ? 'text-amber-500'
        : 'text-emerald-500';
  const Icon = isCircuitOpen ? AlertTriangle : ShieldCheck;
  const headerText = !enabled
    ? 'Remediation — disabled (env)'
    : isCircuitOpen
      ? 'Remediation — circuit OPEN'
      : isOverBudget
        ? 'Remediation — daily caps reached'
        : 'Remediation — healthy';

  return (
    <div className={`rounded-2xl p-4 border ${containerCls}`} data-testid="remediation-status-tile">
      <div className="flex items-start gap-3">
        <Icon size={18} className={iconColor} />
        <div className="flex-1 min-w-0">
          <p className={`text-sm font-semibold ${isCircuitOpen ? 'text-red-600' : isOverBudget ? 'text-amber-600' : enabled ? 'text-emerald-600' : 'text-gray-500'}`}>
            {headerText}
          </p>
          {budget && (
            <p className="text-[11px] text-gray-500 mt-0.5">
              Today {budget.date}: auto {budget.auto_used}/{budget.auto_cap} ·
              {' '}drafted {budget.draft_used}/{budget.draft_cap}
            </p>
          )}
          {circuit && (
            <p className="text-[11px] mt-0.5" style={{ color: isCircuitOpen ? '#dc2626' : '#6b7280' }}>
              Circuit: last {circuit.recent_total}/{circuit.window_size} attempts ·
              {' '}{Math.round((circuit.recent_ratio || 0) * 100)}% drafted-or-worse
              {isCircuitOpen && circuit.disabled_until
                ? ` · disabled until ${fmt(circuit.disabled_until)}`
                : ''}
            </p>
          )}
          {config && (
            <p className="text-[11px] text-gray-400 mt-0.5">
              Min Δ to auto-publish: +{config.minImprovementDelta} ·
              {' '}Fan-out cap: {config.fanoutCapPerEvent}/event ·
              {' '}Trip ratio: {Math.round((config.circuitTripRatio || 0) * 100)}% over {config.circuitWindowSize}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function ManualTriggerForm({ adminToken, onTriggered }) {
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      await adminSeoRemediationTrigger(adminToken, { url: trimmed });
      toast.success('Signal enqueued — refresh history in ~30s to see the result');
      setUrl('');
      onTriggered?.();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Failed to enqueue signal');
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="flex items-center gap-2">
      <input
        type="text"
        value={url}
        onChange={(e) => setUrl(e.target.value)}
        placeholder="/board/class/subject/topic[/page-type]"
        className="flex-1 h-8 px-3 rounded-lg text-xs border border-gray-200 focus:outline-none focus:border-violet-400"
        data-testid="remediation-trigger-url"
      />
      <button
        type="submit"
        disabled={busy || !url.trim()}
        className="h-8 px-3 rounded-lg text-xs font-semibold bg-violet-50 border border-violet-200 hover:bg-violet-100 transition-colors text-violet-600 inline-flex items-center gap-1.5 disabled:opacity-50"
        data-testid="remediation-trigger-submit"
      >
        {busy ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />}
        Trigger
      </button>
    </form>
  );
}

export default function RemediationTab({ adminToken }) {
  const [status, setStatus] = useState(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [items, setItems] = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [actionFilter, setActionFilter] = useState('');
  const [days, setDays] = useState(7);
  const [promotingId, setPromotingId] = useState(null);
  const [resettingCircuit, setResettingCircuit] = useState(false);

  const loadStatus = useCallback(async () => {
    setStatusLoading(true);
    try {
      const res = await adminSeoRemediationStatus(adminToken);
      setStatus(res.data);
    } catch {
      toast.error('Failed to load remediation status');
    } finally {
      setStatusLoading(false);
    }
  }, [adminToken]);

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true);
    try {
      const res = await adminSeoRemediationHistory(adminToken, {
        days,
        limit: 100,
        action: actionFilter || null,
      });
      setItems(res.data?.items || []);
    } catch {
      toast.error('Failed to load remediation history');
    } finally {
      setHistoryLoading(false);
    }
  }, [adminToken, days, actionFilter]);

  useEffect(() => { loadStatus(); }, [loadStatus]);
  useEffect(() => { loadHistory(); }, [loadHistory]);

  const handlePromote = async (rec) => {
    setPromotingId(rec.id);
    try {
      await adminSeoRemediationPromote(adminToken, rec.id);
      toast.success(`Promoted ${rec.topicTitle || rec.pageId}`);
      await loadHistory();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Promote failed');
    } finally {
      setPromotingId(null);
    }
  };

  const handleResetCircuit = async () => {
    setResettingCircuit(true);
    try {
      await adminSeoRemediationCircuitReset(adminToken);
      toast.success('Circuit breaker reset');
      await loadStatus();
    } catch (err) {
      toast.error(err?.response?.data?.detail || 'Reset failed');
    } finally {
      setResettingCircuit(false);
    }
  };

  const isCircuitOpen = status?.circuit?.is_open;

  return (
    <div className="space-y-4" data-testid="remediation-tab">
      <div className="flex items-start gap-3">
        <div className="flex-1">
          <StatusPill status={status} config={status?.config} />
        </div>
        <div className="flex flex-col gap-2 pt-1">
          <button
            onClick={() => { loadStatus(); loadHistory(); }}
            disabled={statusLoading || historyLoading}
            className="h-8 px-3 rounded-lg text-xs font-semibold border border-gray-200 hover:bg-gray-50 transition-colors text-gray-500 inline-flex items-center gap-1.5"
            data-testid="remediation-refresh"
          >
            <RefreshCw size={12} className={(statusLoading || historyLoading) ? 'animate-spin' : ''} />
            Refresh
          </button>
          {isCircuitOpen && (
            <button
              onClick={handleResetCircuit}
              disabled={resettingCircuit}
              className="h-8 px-3 rounded-lg text-xs font-semibold bg-red-50 border border-red-200 hover:bg-red-100 transition-colors text-red-600 inline-flex items-center gap-1.5"
              data-testid="remediation-reset-circuit"
            >
              {resettingCircuit ? <Loader2 size={12} className="animate-spin" /> : <RotateCcw size={12} />}
              Reset circuit
            </button>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-gray-200 p-3 bg-white">
        <div className="flex items-center gap-2 mb-2">
          <Zap size={13} className="text-violet-500" />
          <p className="text-xs font-semibold text-gray-700">Manual trigger</p>
          <span className="text-[10px] text-gray-400">— enqueues a signal for testing or a one-off re-run</span>
        </div>
        <ManualTriggerForm adminToken={adminToken} onTriggered={loadHistory} />
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <label className="text-xs text-gray-500">Days:</label>
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="h-7 px-2 rounded-lg text-xs border border-gray-200"
          data-testid="remediation-days-filter"
        >
          {[1, 3, 7, 14, 30].map((d) => <option key={d} value={d}>{d}d</option>)}
        </select>
        <label className="text-xs text-gray-500 ml-2">Action:</label>
        <select
          value={actionFilter}
          onChange={(e) => setActionFilter(e.target.value)}
          className="h-7 px-2 rounded-lg text-xs border border-gray-200"
          data-testid="remediation-action-filter"
        >
          <option value="">All</option>
          {Object.entries(ACTION_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v.label}</option>
          ))}
        </select>
        <span className="text-[11px] text-gray-400 ml-auto">
          {items.length} attempt{items.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="rounded-xl border border-gray-200 overflow-hidden bg-white">
        <table className="w-full text-xs">
          <thead className="bg-gray-50 text-gray-500">
            <tr>
              <th className="text-left px-3 py-2 font-semibold">When</th>
              <th className="text-left px-3 py-2 font-semibold">Trigger</th>
              <th className="text-left px-3 py-2 font-semibold">Page</th>
              <th className="text-left px-3 py-2 font-semibold">Run</th>
              <th className="text-left px-3 py-2 font-semibold">Score Δ</th>
              <th className="text-left px-3 py-2 font-semibold">Action</th>
              <th className="text-left px-3 py-2 font-semibold">Reason</th>
              <th className="text-right px-3 py-2 font-semibold"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {historyLoading && items.length === 0 ? (
              <tr><td colSpan={8} className="px-3 py-6 text-center text-gray-400">
                <Loader2 size={14} className="inline animate-spin mr-2" /> Loading…
              </td></tr>
            ) : items.length === 0 ? (
              <tr><td colSpan={8} className="px-3 py-6 text-center text-gray-400" data-testid="remediation-empty">
                No remediation attempts in this window.
              </td></tr>
            ) : items.map((row) => (
              <tr key={row.id} className="hover:bg-gray-50" data-testid={`remediation-row-${row.id}`}>
                <td className="px-3 py-2 text-gray-500 whitespace-nowrap">{fmt(row.attemptedAt)}</td>
                <td className="px-3 py-2">
                  <span className="text-gray-700">{SIGNAL_LABELS[row.signalKind] || row.signalKind || '—'}</span>
                  {row.signalUrl && (
                    <a
                      href={row.signalUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="ml-1 text-violet-500 hover:text-violet-700 inline-flex items-center"
                      title={row.signalUrl}
                    >
                      <ExternalLink size={10} />
                    </a>
                  )}
                </td>
                <td className="px-3 py-2 text-gray-700">
                  <div className="font-medium">{row.topicTitle || '—'}</div>
                  <div className="text-[10px] text-gray-400">{row.pageType || ''}</div>
                </td>
                <td className="px-3 py-2 text-[10px] text-gray-400 font-mono whitespace-nowrap" title={row.pipelineRunId || ''} data-testid={`remediation-runid-${row.id}`}>
                  {row.pipelineRunId || '—'}
                </td>
                <td className="px-3 py-2">
                  <DeltaCell before={row.scoreBefore} after={row.scoreAfter} delta={row.scoreDelta} />
                </td>
                <td className="px-3 py-2"><ActionPill action={row.action} /></td>
                <td className="px-3 py-2 text-gray-500 text-[11px] max-w-[260px] truncate" title={row.reason}>
                  {row.reason || '—'}
                </td>
                <td className="px-3 py-2 text-right">
                  {row.action === 'drafted' && !row.promotedAt && (
                    <button
                      onClick={() => handlePromote(row)}
                      disabled={promotingId === row.id}
                      className="h-7 px-2 rounded-lg text-[11px] font-semibold bg-emerald-50 border border-emerald-200 hover:bg-emerald-100 text-emerald-600 inline-flex items-center gap-1 disabled:opacity-50"
                      data-testid={`remediation-promote-${row.id}`}
                    >
                      {promotingId === row.id
                        ? <Loader2 size={11} className="animate-spin" />
                        : <ArrowUpRight size={11} />}
                      Promote
                    </button>
                  )}
                  {row.promotedAt && (
                    <span className="text-[10px] text-emerald-600 font-semibold">
                      Promoted
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
