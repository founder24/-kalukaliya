import { useState, useEffect, useCallback } from 'react';
import { Activity, Trash2, RefreshCw, Download, Search, Info, AlertTriangle, AlertOctagon } from 'lucide-react';
import AdminQuickLinks from './AdminQuickLinks';
import { toast } from 'sonner';
import { adminGetActivityLog, API_BASE } from '@/utils/api';
import axios from 'axios';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
const adminHeaders = (token) => {
  const isRealJwt = token && typeof token === 'string' && token.split('.').length === 3;
  return isRealJwt ? { Authorization: `Bearer ${token}` } : {};
};

const LEVEL_CONFIG = {
  info:    { icon: Info,         color: 'text-blue-600',   dot: 'bg-blue-500',   border: 'border-blue-200'  },
  warning: { icon: AlertTriangle,color: 'text-amber-600',  dot: 'bg-amber-500',  border: 'border-amber-200' },
  danger:  { icon: AlertOctagon, color: 'text-red-600',    dot: 'bg-red-500',    border: 'border-red-200'   },
};

function groupByDate(logs) {
  const groups = {};
  logs.forEach((log) => {
    const rawDate = log.created_at || log.timestamp || log.date;
    const date = rawDate
      ? new Date(rawDate).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })
      : 'Unknown';
    if (!groups[date]) groups[date] = [];
    groups[date].push(log);
  });
  return groups;
}

export default function AdminActivityLog({ adminToken, onNavigate }) {
  const [logs, setLogs]     = useState([]);
  const [search, setSearch] = useState('');
  const [level, setLevel]   = useState('all');
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState(null);
  // Confirmation panel state (Audit #9): the trash button now reveals
  // an inline danger panel that requires the admin to type "CLEAR" to
  // arm the destructive action, instead of a single-keystroke browser
  // confirm() dialog. ``confirmText`` mirrors the input value so we can
  // gate the submit button on an exact-match comparison.
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [confirmText, setConfirmText] = useState('');
  const [clearing, setClearing] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    adminGetActivityLog(adminToken)
      .then((r) => {
        const data = r.data;
        const logsArray = Array.isArray(data) ? data : (data?.logs || []);
        setLogs(logsArray);
      })
      .catch(() => {
        setError('Failed to load activity log');
        setLogs([]);
      })
      .finally(() => setLoading(false));
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  // Audit #9: open the typed-confirmation panel instead of triggering
  // an immediate destructive action behind a one-keystroke browser
  // confirm(). The actual delete now lives in handleClearConfirmed().
  const handleClear = () => {
    setConfirmText('');
    setConfirmOpen(true);
  };

  const handleClearCancel = () => {
    setConfirmOpen(false);
    setConfirmText('');
  };

  const handleClearConfirmed = async () => {
    if (confirmText !== 'CLEAR' || clearing) return;
    setClearing(true);
    try {
      const res = await axios.delete(`${API_BASE}/admin/activity-log`, {
        headers: adminHeaders(adminToken),
        withCredentials: true,
      });
      const cleared = Number.isFinite(res?.data?.cleared) ? res.data.cleared : null;
      // The backend immediately inserts a self-audit "activity_log_cleared"
      // entry attributed to this admin, so we reload rather than blanking
      // the list — the next render will show that single danger-level
      // entry on top, which doubles as visual proof the purge ran.
      load();
      setConfirmOpen(false);
      setConfirmText('');
      toast.success(
        cleared != null
          ? `Cleared ${cleared} ${cleared === 1 ? 'entry' : 'entries'} — purge has been logged`
          : 'Activity log cleared — purge has been logged'
      );
    } catch {
      toast.error('Failed to clear log');
    } finally {
      setClearing(false);
    }
  };

  const handleExport = () => {
    if (!logs.length) { toast.error('No logs to export'); return; }
    const csv = [
      'Timestamp,Action,Details,Admin,Level',
      ...logs.map((l) => `"${l.created_at || l.timestamp || ''}","${l.action || ''}","${l.details || ''}","${l.admin_name || ''}","${l.level || 'info'}"`)
    ];
    const blob = new Blob([csv.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'activity_log.csv'; a.click();
    URL.revokeObjectURL(url);
  };

  const filtered = logs.filter((l) => {
    const matchSearch = !search ||
      l.action?.toLowerCase().includes(search.toLowerCase()) ||
      l.details?.toLowerCase().includes(search.toLowerCase()) ||
      l.admin_name?.toLowerCase().includes(search.toLowerCase());
    const matchLevel = level === 'all' || l.level === level;
    return matchSearch && matchLevel;
  });

  const groups = groupByDate(filtered);

  return (
    <SectionErrorBoundary name="Activity Log">
      <div className="space-y-4 max-w-3xl">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-bold text-gray-900">Activity Log</h2>
            <p className="text-sm text-gray-400 mt-0.5">Admin action audit trail · {logs.length} entries</p>
          </div>
          <div className="flex gap-2">
            <button onClick={load} className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100 border border-gray-200">
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            </button>
            <button onClick={handleExport} disabled={!logs.length} className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100 border border-gray-200 disabled:opacity-30">
              <Download size={14} />
            </button>
            <button onClick={handleClear} disabled={!logs.length} className="p-2 rounded-xl text-red-400 hover:text-red-600 hover:bg-red-50 border border-red-200 disabled:opacity-30">
              <Trash2 size={14} />
            </button>
          </div>
        </div>

        {error && (
          <div className="p-3 rounded-xl bg-red-50 border border-red-200 text-sm text-red-600">{error}</div>
        )}

        {confirmOpen && (
          <div className="p-4 rounded-xl bg-red-50 border-2 border-red-300 space-y-3" role="alertdialog" aria-labelledby="clear-log-title">
            <div className="flex items-start gap-2">
              <AlertOctagon size={18} className="text-red-600 flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <h3 id="clear-log-title" className="text-sm font-bold text-red-900">
                  Permanently delete the activity log?
                </h3>
                <p className="text-xs text-red-700 mt-1 leading-relaxed">
                  This will erase <strong>every entry</strong> from the admin audit trail (currently showing {logs.length}; the GET endpoint is paged at 200 so the true count may be higher). This cannot be undone. A single replacement entry attributing the purge to you will be created automatically so the action itself stays audited.
                </p>
              </div>
            </div>
            <div className="space-y-1.5">
              <label htmlFor="clear-log-confirm" className="text-xs font-medium text-red-900">
                Type <code className="px-1 py-0.5 bg-white rounded border border-red-200 font-mono">CLEAR</code> to confirm:
              </label>
              <input
                id="clear-log-confirm"
                type="text"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                disabled={clearing}
                autoFocus
                placeholder="CLEAR"
                className="w-full h-9 px-3 rounded-xl text-sm font-mono text-gray-900 outline-none bg-white border border-red-200 focus:border-red-400 focus:ring-2 focus:ring-red-500/20 disabled:opacity-50"
              />
            </div>
            <div className="flex items-center gap-2 justify-end">
              <button
                type="button"
                onClick={handleClearCancel}
                disabled={clearing}
                className="px-3 py-1.5 rounded-xl text-xs font-medium text-gray-600 hover:text-gray-900 hover:bg-white border border-gray-200 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleClearConfirmed}
                disabled={confirmText !== 'CLEAR' || clearing}
                className="px-3 py-1.5 rounded-xl text-xs font-bold text-white bg-red-600 hover:bg-red-700 disabled:opacity-30 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
              >
                {clearing && <RefreshCw size={12} className="animate-spin" />}
                {clearing ? 'Clearing…' : 'Permanently clear log'}
              </button>
            </div>
          </div>
        )}

        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search actions..."
              className="w-full h-9 pl-8 pr-3 rounded-xl text-sm text-gray-900 outline-none bg-gray-50 border border-gray-200 focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20"
            />
          </div>
          {['all','info','warning','danger'].map((l) => (
            <button
              key={l}
              onClick={() => setLevel(l)}
              className={`px-3 py-1.5 rounded-xl text-xs font-medium transition-all border ${
                level === l ? 'bg-violet-50 border-violet-200 text-violet-700' : 'border-gray-200 text-gray-400 hover:text-gray-600'
              }`}
            >
              {l.charAt(0).toUpperCase() + l.slice(1)}
            </button>
          ))}
          <span className="text-xs text-gray-400">{filtered.length}</span>
        </div>

        {loading ? (
          <div className="text-center py-16 text-gray-300">
            <RefreshCw size={24} className="mx-auto mb-3 animate-spin opacity-40" />
            <p className="text-sm">Loading logs...</p>
          </div>
        ) : Object.keys(groups).length === 0 ? (
          <div className="text-center py-16 text-gray-300">
            <Activity size={32} className="mx-auto mb-3 opacity-30" />
            <p className="text-sm">No admin actions recorded yet</p>
            <p className="text-xs mt-1 opacity-60">Actions will appear here as admins use the panel</p>
          </div>
        ) : (
          Object.entries(groups).map(([date, entries]) => (
            <div key={date}>
              <div className="flex items-center gap-3 my-3">
                <div className="flex-1 h-px bg-gray-200" />
                <span className="text-xs text-gray-400 px-2">{date}</span>
                <div className="flex-1 h-px bg-gray-200" />
              </div>
              {entries.map((log, idx) => {
                const lc = LEVEL_CONFIG[log.level] || LEVEL_CONFIG.info;
                const Icon = lc.icon;
                const rawDate = log.created_at || log.timestamp;
                return (
                  <div
                    key={log.id || idx}
                    className={`flex items-start gap-3 p-3 rounded-xl mb-1.5 border ${lc.border} bg-white`}
                  >
                    <div className={`w-1.5 h-1.5 rounded-full ${lc.dot} mt-1.5 flex-shrink-0`} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm text-gray-900 font-medium">{log.action || 'Action'}</span>
                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-gray-100 ${lc.color}`}>{log.level || 'info'}</span>
                        <span className="text-xs text-gray-400 ml-auto">by {log.admin_name || 'Admin'}</span>
                      </div>
                      {log.details && (
                        <p className="text-xs text-gray-500 mt-0.5 truncate">
                          {typeof log.details === 'string' ? log.details : JSON.stringify(log.details)}
                        </p>
                      )}
                    </div>
                    {rawDate && (
                      <span className="text-[10px] text-gray-400 font-mono flex-shrink-0">
                        {new Date(rawDate).toLocaleTimeString('en-US', { hour12: false })}
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
          ))
        )}
        <AdminQuickLinks links={['users','conversations','dashboard','ratelimits']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
