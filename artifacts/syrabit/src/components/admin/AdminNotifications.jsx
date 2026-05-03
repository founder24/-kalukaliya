import { useState, useEffect, useCallback, useMemo } from 'react';
import { Bell, Send, Clock, Trash2, Users, Loader2, Info, CheckCircle2, AlertTriangle, XCircle, Zap, Plus, BarChart3, Smartphone, RefreshCw, ChevronDown, ChevronUp, Eye } from 'lucide-react';
import AdminQuickLinks from './AdminQuickLinks';
import { toast } from 'sonner';
import axios from 'axios';
import { getNotificationTriggers, createNotificationTrigger, updateNotificationTrigger, deleteNotificationTrigger, API_BASE } from '@/utils/api';
import { useSyraFilters, useSyraVisibleError } from '@/components/admin/syra/SyraContext';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
const adminHeaders = (token) => {
  const isRealJwt = token && typeof token === 'string' && token.split('.').length === 3;
  return isRealJwt ? { Authorization: `Bearer ${token}` } : {};
};

const TRIGGER_EVENTS = [
  { id: 'signup',       label: 'User Signed Up' },
  { id: 'inactive_3d',  label: 'Inactive 3 Days' },
  { id: 'inactive_7d',  label: 'Inactive 7 Days' },
  { id: 'plan_upgrade', label: 'Plan Upgraded' },
  { id: 'low_credits',  label: 'Low Credits (< 2)' },
];

const TRIGGER_CHANNELS = [
  { id: 'push',  label: 'Push' },
  { id: 'email', label: 'Email' },
  { id: 'both',  label: 'Both' },
];

const NOTIF_TYPES = [
  { id: 'info',    icon: Info,          color: 'text-blue-600',    bg: 'bg-blue-50',    border: 'border-blue-200'    },
  { id: 'success', icon: CheckCircle2,  color: 'text-emerald-600', bg: 'bg-emerald-50', border: 'border-emerald-200' },
  { id: 'warning', icon: AlertTriangle, color: 'text-amber-600',   bg: 'bg-amber-50',   border: 'border-amber-200'   },
  { id: 'error',   icon: XCircle,       color: 'text-red-600',     bg: 'bg-red-50',     border: 'border-red-200'     },
];

const AUDIENCES = [
  { id: 'all',     label: 'All Users',    icon: Users },
  { id: 'free',    label: 'Free Plan',    icon: Bell  },
  { id: 'starter', label: 'Starter Plan', icon: Bell  },
  { id: 'pro',     label: 'Pro Plan',     icon: Bell  },
];

export default function AdminNotifications({ adminToken, onNavigate }) {
  const [notifs, setNotifs]     = useState([]);
  const [title, setTitle]       = useState('');
  const [message, setMessage]   = useState('');
  const [type, setType]         = useState('info');
  const [audience, setAudience] = useState('all');
  const [sending, setSending]   = useState(false);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);
  const [mainTab, setMainTab]   = useState('broadcast');
  const [triggers, setTriggers] = useState([]);
  const [trigLoading, setTrigLoading] = useState(false);
  const [newTrig, setNewTrig]   = useState({ name: '', event: 'signup', channel: 'push', message: '', subject: '', enabled: true });
  const [deliveryStats, setDeliveryStats] = useState(null);
  const [deliveryLogs, setDeliveryLogs] = useState([]);
  const [deliveryLogsTotal, setDeliveryLogsTotal] = useState(0);
  const [deliveryLoading, setDeliveryLoading] = useState(false);
  const [subscriptions, setSubscriptions] = useState([]);
  const [subsTotal, setSubsTotal] = useState(0);
  const [pruneStatus, setPruneStatus] = useState(null);
  const [pruneRunning, setPruneRunning] = useState(false);
  const [expandedDispatch, setExpandedDispatch] = useState(null);
  const [dispatchDetail, setDispatchDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [statsDays, setStatsDays] = useState(7);

  // Task #298 — publish active filters + visible error so Syra can
  // contextualise replies on the Notifications screen. Memoised to
  // avoid creating a fresh object every render (which would re-trigger
  // SyraContext.setFilters and risk a re-render loop).
  const syraFilters = useMemo(() => ({
    tab: mainTab !== 'broadcast' ? mainTab : undefined,
    audience: audience !== 'all' ? audience : undefined,
    type: type !== 'info' ? type : undefined,
  }), [mainTab, audience, type]);
  useSyraFilters(syraFilters);
  useSyraVisibleError(error || null);

  useEffect(() => {
    axios.get(`${API_BASE}/admin/notifications`, {
      headers: adminHeaders(adminToken),
      withCredentials: true
    })
      .then((r) => {
        const data = r.data;
        const arr = Array.isArray(data) ? data : (data?.notifications || data?.items || []);
        setNotifs(arr);
      })
      .catch(() => {
        setError('Failed to load notifications');
        setNotifs([]);
      })
      .finally(() => setLoading(false));
    getNotificationTriggers(adminToken).then(r => setTriggers(r.data.triggers || [])).catch(() => {});
  }, [adminToken]);

  const handleSaveTrigger = async () => {
    if (!newTrig.name || !newTrig.message) { toast.error('Name and message required'); return; }
    setTrigLoading(true);
    try {
      const r = await createNotificationTrigger(adminToken, newTrig);
      setTriggers(prev => [...prev, r.data]);
      setNewTrig({ name: '', event: 'signup', channel: 'push', message: '', subject: '', enabled: true });
      toast.success('Trigger created!');
    } catch { toast.error('Failed to create trigger'); }
    finally { setTrigLoading(false); }
  };

  const handleToggleTrigger = async (id, enabled) => {
    try {
      await updateNotificationTrigger(adminToken, id, { enabled: !enabled });
      setTriggers(prev => prev.map(t => t.id === id ? { ...t, enabled: !enabled } : t));
    } catch { toast.error('Failed to toggle trigger'); }
  };

  const handleDeleteTrigger = async (id) => {
    try {
      await deleteNotificationTrigger(adminToken, id);
      setTriggers(prev => prev.filter(t => t.id !== id));
      toast.success('Deleted');
    } catch { toast.error('Failed to delete trigger'); }
  };

  const handleSend = async (status) => {
    if (!title.trim() || !message.trim()) { toast.error('Title and message required'); return; }
    setSending(true);
    try {
      const res = await axios.post(
        `${API_BASE}/admin/notifications`,
        { title, message, type, audience, status },
        { headers: adminHeaders(adminToken), withCredentials: true }
      );
      setNotifs((n) => [res.data, ...n]);
      setTitle(''); setMessage('');
      toast.success(status === 'sent' ? 'Notification sent!' : 'Draft saved');
    } catch { toast.error('Failed to send notification'); }
    finally { setSending(false); }
  };

  const handleDelete = async (id) => {
    try {
      await axios.delete(`${API_BASE}/admin/notifications/${id}`, {
        headers: adminHeaders(adminToken),
        withCredentials: true
      });
      setNotifs((n) => n.filter((x) => x.id !== id));
      toast.success('Deleted');
    } catch { toast.error('Failed to delete'); }
  };

  const handleMarkRead = async (id) => {
    try {
      await axios.patch(`${API_BASE}/admin/notifications/${id}/read`, {}, {
        headers: adminHeaders(adminToken),
        withCredentials: true,
      });
      setNotifs((prev) => prev.map((n) => n.id === id ? { ...n, read: true } : n));
    } catch { toast.error('Failed to mark as read'); }
  };

  const adminAxios = useCallback((url) => axios.get(url, {
    headers: adminHeaders(adminToken),
    withCredentials: true,
  }), [adminToken]);

  const loadDeliveryData = useCallback(async () => {
    setDeliveryLoading(true);
    try {
      const [statsRes, logsRes, subsRes, pruneRes] = await Promise.allSettled([
        adminAxios(`${API_BASE}/admin/push/delivery-stats?days=${statsDays}`),
        adminAxios(`${API_BASE}/admin/push/delivery-log?limit=50`),
        adminAxios(`${API_BASE}/admin/push/subscriptions`),
        adminAxios(`${API_BASE}/admin/push/prune-dead`),
      ]);
      if (statsRes.status === 'fulfilled') setDeliveryStats(statsRes.value.data);
      if (logsRes.status === 'fulfilled') {
        setDeliveryLogs(logsRes.value.data.logs || []);
        setDeliveryLogsTotal(logsRes.value.data.total || 0);
      }
      if (subsRes.status === 'fulfilled') {
        setSubscriptions(subsRes.value.data.subscriptions || []);
        setSubsTotal(subsRes.value.data.total || 0);
      }
      if (pruneRes.status === 'fulfilled') setPruneStatus(pruneRes.value.data);
    } catch (err) {
      // The Promise.allSettled chain absorbs per-request failures into
      // .status === 'rejected', so this catch only fires for code-level
      // bugs in the per-result handling above. Log loudly when it does.
      console.warn('AdminNotifications: post-allSettled handler threw:', err);
    }
    finally { setDeliveryLoading(false); }
  }, [adminAxios, statsDays]);

  const runPruneNow = useCallback(async () => {
    setPruneRunning(true);
    try {
      const res = await axios.post(
        `${API_BASE}/admin/push/prune-dead`,
        {},
        { headers: adminHeaders(adminToken), withCredentials: true },
      );
      const summary = res.data || {};
      toast.success(
        summary.deactivated
          ? `Pruned ${summary.deactivated} stale subscriber${summary.deactivated === 1 ? '' : 's'}`
          : 'No stale subscribers found',
      );
      await loadDeliveryData();
    } catch {
      toast.error('Failed to run prune');
    } finally {
      setPruneRunning(false);
    }
  }, [adminToken, loadDeliveryData]);

  useEffect(() => {
    if (mainTab === 'delivery') loadDeliveryData();
  }, [mainTab, loadDeliveryData]);

  const loadDispatchDetail = useCallback(async (dispatchId) => {
    if (expandedDispatch === dispatchId) {
      setExpandedDispatch(null);
      setDispatchDetail(null);
      return;
    }
    setExpandedDispatch(dispatchId);
    setDispatchDetail(null);
    setDetailLoading(true);
    try {
      const res = await adminAxios(`${API_BASE}/admin/push/delivery-log/${dispatchId}`);
      setDispatchDetail(res.data);
    } catch { toast.error('Failed to load dispatch details'); }
    finally { setDetailLoading(false); }
  }, [adminAxios, expandedDispatch]);

  const formatTime = (iso) => {
    if (!iso) return '';
    const d = new Date(iso);
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  const currentType = NOTIF_TYPES.find((t) => t.id === type) || NOTIF_TYPES[0];
  const TypeIcon = currentType.icon;

  const trigInputStyle = { padding: '8px 12px', borderRadius: 8, background: '#f9fafb', border: '1px solid #e5e7eb', color: '#111827', fontSize: 13, outline: 'none' };
  const trigSelectStyle = { padding: '8px 12px', borderRadius: 8, background: '#ffffff', border: '1px solid #e5e7eb', color: '#111827', fontSize: 13 };

  return (
    <SectionErrorBoundary name="Notifications">
      <div className="space-y-4 max-w-5xl">
        <div style={{ display: 'flex', gap: 4, padding: '4px', flexWrap: 'wrap' }}>
          {[
            { id: 'broadcast', label: 'Broadcast' },
            { id: 'triggers',  label: `Automation (${triggers.length})` },
            { id: 'delivery',  label: 'Push Delivery' },
          ].map(t => (
            <button key={t.id} onClick={() => setMainTab(t.id)}
              style={{ padding: '6px 16px', borderRadius: 8, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: mainTab === t.id ? '1px solid #c4b5fd' : '1px solid #e5e7eb', background: mainTab === t.id ? '#7c3aed' : '#ffffff', color: mainTab === t.id ? '#fff' : '#6b7280' }}>
              {t.label}
            </button>
          ))}
        </div>

        {mainTab === 'triggers' && (
          <div className="space-y-4">
            <div style={{ background: '#ffffff', border: '1px solid #e5e7eb', borderRadius: 16, padding: 20, boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
                <Zap size={15} color="#7c3aed" />
                <span style={{ fontWeight: 700, color: '#111827', fontSize: 14 }}>Create Automation Trigger</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
                <input value={newTrig.name} onChange={e => setNewTrig(p => ({ ...p, name: e.target.value }))} placeholder="Trigger name (e.g. Welcome Email)"
                  style={trigInputStyle} />
                <input value={newTrig.subject} onChange={e => setNewTrig(p => ({ ...p, subject: e.target.value }))} placeholder="Email subject (optional)"
                  style={trigInputStyle} />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
                <select value={newTrig.event} onChange={e => setNewTrig(p => ({ ...p, event: e.target.value }))}
                  style={trigSelectStyle}>
                  {TRIGGER_EVENTS.map(e => <option key={e.id} value={e.id}>{e.label}</option>)}
                </select>
                <select value={newTrig.channel} onChange={e => setNewTrig(p => ({ ...p, channel: e.target.value }))}
                  style={trigSelectStyle}>
                  {TRIGGER_CHANNELS.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
                </select>
              </div>
              <textarea value={newTrig.message} onChange={e => setNewTrig(p => ({ ...p, message: e.target.value }))} placeholder="Message body... (use {name} for personalisation)"
                rows={3} style={{ ...trigInputStyle, width: '100%', resize: 'none', boxSizing: 'border-box' }} />
              <button onClick={handleSaveTrigger} disabled={trigLoading}
                style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 6, padding: '8px 18px', borderRadius: 8, background: '#7c3aed', color: '#fff', fontWeight: 700, fontSize: 13, border: 'none', cursor: 'pointer' }}>
                {trigLoading ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />} Save Trigger
              </button>
            </div>

            {triggers.length === 0 ? (
              <div style={{ textAlign: 'center', padding: 48 }}>
                <Zap size={32} color="#d8b4fe" style={{ margin: '0 auto 12px' }} />
                <p style={{ color: '#9ca3af', fontSize: 13 }}>No triggers yet — create your first automation above</p>
              </div>
            ) : (
              <div className="space-y-2">
                {triggers.map(t => (
                  <div key={t.id} style={{ background: '#ffffff', border: `1px solid ${t.enabled ? '#e9d5ff' : '#e5e7eb'}`, borderRadius: 12, padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12, boxShadow: '0 1px 3px rgba(0,0,0,0.04)' }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 13, fontWeight: 700, color: '#111827' }}>{t.name}</span>
                        <span style={{ fontSize: 10, fontWeight: 700, padding: '2px 7px', borderRadius: 20, background: t.enabled ? '#ecfdf5' : '#f3f4f6', color: t.enabled ? '#10b981' : '#6b7280' }}>
                          {t.enabled ? 'Active' : 'Paused'}
                        </span>
                      </div>
                      <p style={{ fontSize: 11, color: '#9ca3af', marginTop: 2 }}>
                        {TRIGGER_EVENTS.find(e => e.id === t.event)?.label || t.event} → {t.channel} · {t.message?.slice(0, 60)}...
                      </p>
                    </div>
                    <div style={{ display: 'flex', gap: 6 }}>
                      <button onClick={() => handleToggleTrigger(t.id, t.enabled)}
                        style={{ padding: '5px 10px', borderRadius: 7, fontSize: 11, fontWeight: 600, cursor: 'pointer', border: '1px solid #e5e7eb', background: '#ffffff', color: t.enabled ? '#f59e0b' : '#10b981' }}>
                        {t.enabled ? 'Pause' : 'Resume'}
                      </button>
                      <button onClick={() => handleDeleteTrigger(t.id)}
                        style={{ padding: '5px 10px', borderRadius: 7, fontSize: 11, fontWeight: 600, cursor: 'pointer', border: '1px solid #fecaca', background: '#fef2f2', color: '#ef4444' }}>
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {mainTab === 'broadcast' && (
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        <div className="lg:col-span-7 rounded-2xl border border-gray-200 p-5 space-y-4 bg-white shadow-sm">
          <h2 className="text-base font-bold text-gray-900">Compose Notification</h2>

          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Notification title"
            className="w-full h-9 px-3 rounded-xl text-sm text-gray-900 outline-none bg-gray-50 border border-gray-200 focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20"
          />
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Message body..."
            rows={4}
            className="w-full p-3 rounded-xl text-sm text-gray-900 resize-none outline-none bg-gray-50 border border-gray-200 focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20"
          />

          <div>
            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">Type</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {NOTIF_TYPES.map(({ id, icon: Icon, color, bg, border }) => (
                <button
                  key={id}
                  onClick={() => setType(id)}
                  className={`flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-medium border transition-all ${
                    type === id ? `${bg} ${border} ${color}` : 'border-gray-200 text-gray-500'
                  }`}
                >
                  <Icon size={12} /> {id.charAt(0).toUpperCase() + id.slice(1)}
                </button>
              ))}
            </div>
          </div>

          <div>
            <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">Audience</p>
            <div className="space-y-1.5">
              {AUDIENCES.map(({ id, label }) => (
                <button
                  key={id}
                  onClick={() => setAudience(id)}
                  className={`w-full flex items-center gap-2 px-3 py-2 rounded-xl text-sm transition-all border ${
                    audience === id
                      ? 'border-violet-200 bg-violet-50 text-violet-700'
                      : 'border-gray-200 text-gray-500 hover:text-gray-700 hover:bg-gray-50'
                  }`}
                >
                  <div className={`w-3 h-3 rounded-full border ${audience === id ? 'border-violet-500 bg-violet-500' : 'border-gray-300'}`} />
                  {label}
                </button>
              ))}
            </div>
          </div>

          {title && (
            <div className={`p-3 rounded-xl border ${currentType.bg} ${currentType.border}`}>
              <div className="flex items-center gap-2 mb-1">
                <TypeIcon size={14} className={currentType.color} />
                <span className="text-sm font-medium text-gray-900">{title}</span>
              </div>
              <p className="text-xs text-gray-500">{message}</p>
            </div>
          )}

          <div className="flex gap-2">
            <button
              onClick={() => handleSend('draft')}
              disabled={sending}
              className="flex-1 h-9 rounded-xl text-xs font-medium text-gray-600 border border-gray-200 hover:bg-gray-50"
            >
              <Clock size={12} className="inline mr-1" /> Save Draft
            </button>
            <button
              onClick={() => handleSend('sent')}
              disabled={sending}
              className="flex-1 h-9 rounded-xl text-xs font-semibold text-white flex items-center justify-center gap-1.5 bg-violet-600 hover:bg-violet-700 transition-colors"
            >
              {sending ? <Loader2 size={12} className="animate-spin" /> : <Send size={12} />} Send Now
            </button>
          </div>
        </div>

        <div className="lg:col-span-5 rounded-2xl border border-gray-200 overflow-hidden bg-white shadow-sm">
          <div className="p-4 border-b border-gray-100 flex items-center justify-between">
            <p className="text-sm font-bold text-gray-900">Notifications</p>
            <div className="flex gap-1.5 text-[10px]">
              <span className="px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">{notifs.length} total</span>
              <span className="px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-600">
                {notifs.filter((n) => n.status === 'sent').length} sent
              </span>
            </div>
          </div>

          <div className="overflow-y-auto" style={{ maxHeight: 480 }}>
            {loading ? (
              <div className="flex justify-center py-12">
                <Loader2 size={20} className="animate-spin text-gray-300" />
              </div>
            ) : error ? (
              <div className="p-4 text-center text-sm text-red-500">{error}</div>
            ) : notifs.length === 0 ? (
              <div className="text-center py-12 space-y-2">
                <Bell size={28} className="mx-auto text-gray-200" />
                <p className="text-sm text-gray-400">No notifications yet</p>
                <p className="text-xs text-gray-300">Compose and send your first notification</p>
              </div>
            ) : notifs.map((n) => {
              const tc = NOTIF_TYPES.find((t) => t.id === n.type) || NOTIF_TYPES[0];
              const Icon = tc.icon;
              return (
                <div
                  key={n.id}
                  className={`p-3 border-b border-gray-100 ${n.status === 'draft' ? 'border-l-2 border-l-amber-400' : ''} group`}
                >
                  <div className="flex items-start gap-2.5">
                    <div className={`w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 ${tc.bg}`}>
                      <Icon size={14} className={tc.color} />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 mb-0.5">
                        <span className="text-xs font-medium text-gray-900">{n.title}</span>
                        <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded-full ${
                          n.status === 'sent' ? 'bg-emerald-50 text-emerald-600' : 'bg-amber-50 text-amber-600'
                        }`}>
                          {n.status}
                        </span>
                      </div>
                      <p className="text-[11px] text-gray-500 truncate">{n.message}</p>
                      <p className="text-[10px] text-gray-400 mt-0.5">→ {n.audience}</p>
                    </div>
                    {!n.read && (
                      <button
                        onClick={() => handleMarkRead(n.id)}
                        aria-label="Mark as read"
                        title="Mark as read"
                        className="opacity-0 group-hover:opacity-100 p-1 text-gray-300 hover:text-emerald-500 transition-all"
                      >
                        <Eye size={12} />
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(n.id)}
                      className="opacity-0 group-hover:opacity-100 p-1 text-gray-300 hover:text-red-500 transition-all"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
        )}

        {mainTab === 'delivery' && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <BarChart3 size={15} className="text-violet-500" />
                <span className="text-sm font-bold text-gray-900">Push Delivery Status</span>
              </div>
              <div className="flex items-center gap-2">
                <select
                  value={statsDays}
                  onChange={e => setStatsDays(Number(e.target.value))}
                  className="text-xs border border-gray-200 rounded-lg px-2 py-1 bg-white text-gray-700"
                >
                  <option value={7}>Last 7 days</option>
                  <option value={14}>Last 14 days</option>
                  <option value={30}>Last 30 days</option>
                </select>
                <button
                  onClick={loadDeliveryData}
                  disabled={deliveryLoading}
                  className="flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50"
                >
                  <RefreshCw size={11} className={deliveryLoading ? 'animate-spin' : ''} /> Refresh
                </button>
              </div>
            </div>

            {deliveryLoading && !deliveryStats ? (
              <div className="flex justify-center py-12">
                <Loader2 size={20} className="animate-spin text-gray-300" />
              </div>
            ) : (
              <>
                {deliveryStats && (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                      <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Dispatches</p>
                      <p className="text-xl font-bold text-gray-900 mt-1">{deliveryStats.total_dispatches}</p>
                    </div>
                    <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 shadow-sm">
                      <p className="text-[10px] font-bold text-emerald-500 uppercase tracking-wider">Sent</p>
                      <p className="text-xl font-bold text-emerald-700 mt-1">{deliveryStats.total_sent}</p>
                    </div>
                    <div className="rounded-xl border border-red-200 bg-red-50 p-4 shadow-sm">
                      <p className="text-[10px] font-bold text-red-400 uppercase tracking-wider">Failed</p>
                      <p className="text-xl font-bold text-red-700 mt-1">{deliveryStats.total_failed}</p>
                    </div>
                    <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 shadow-sm">
                      <p className="text-[10px] font-bold text-amber-500 uppercase tracking-wider">Expired</p>
                      <p className="text-xl font-bold text-amber-700 mt-1">{deliveryStats.total_expired}</p>
                    </div>
                  </div>
                )}

                {deliveryStats?.daily?.length > 0 && (
                  <div className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
                    <p className="text-xs font-bold text-gray-600 mb-3">Daily Breakdown</p>
                    <div className="overflow-x-auto">
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-gray-400 border-b border-gray-100">
                            <th className="text-left py-2 px-2 font-medium">Date</th>
                            <th className="text-right py-2 px-2 font-medium">Dispatches</th>
                            <th className="text-right py-2 px-2 font-medium">Sent</th>
                            <th className="text-right py-2 px-2 font-medium">Failed</th>
                            <th className="text-right py-2 px-2 font-medium">Expired</th>
                          </tr>
                        </thead>
                        <tbody>
                          {deliveryStats.daily.map(d => (
                            <tr key={d.date} className="border-b border-gray-50 hover:bg-gray-50">
                              <td className="py-2 px-2 text-gray-700 font-medium">{d.date}</td>
                              <td className="py-2 px-2 text-right text-gray-600">{d.dispatches}</td>
                              <td className="py-2 px-2 text-right text-emerald-600 font-medium">{d.sent}</td>
                              <td className="py-2 px-2 text-right text-red-500 font-medium">{d.failed}</td>
                              <td className="py-2 px-2 text-right text-amber-500 font-medium">{d.expired}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}

                <div className="rounded-2xl border border-gray-200 bg-white shadow-sm overflow-hidden">
                  <div className="p-4 border-b border-gray-100 flex items-center justify-between">
                    <p className="text-sm font-bold text-gray-900">Dispatch History</p>
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">
                      {deliveryLogsTotal} total
                    </span>
                  </div>
                  <div className="overflow-y-auto" style={{ maxHeight: 400 }}>
                    {deliveryLogs.length === 0 ? (
                      <div className="text-center py-12 space-y-2">
                        <Send size={24} className="mx-auto text-gray-200" />
                        <p className="text-sm text-gray-400">No push dispatches recorded yet</p>
                      </div>
                    ) : deliveryLogs.map(log => (
                      <div key={log.dispatch_id} className="border-b border-gray-50">
                        <button
                          onClick={() => loadDispatchDetail(log.dispatch_id)}
                          className="w-full p-3 flex items-center gap-3 hover:bg-gray-50 transition-colors text-left"
                        >
                          <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 bg-violet-50">
                            <Send size={12} className="text-violet-500" />
                          </div>
                          <div className="flex-1 min-w-0">
                            <p className="text-xs font-medium text-gray-900 truncate">
                              {log.payload_title || 'Push notification'}
                            </p>
                            <p className="text-[10px] text-gray-400 mt-0.5">
                              {formatTime(log.dispatched_at)} &middot; {log.target}
                            </p>
                          </div>
                          <div className="flex items-center gap-2 flex-shrink-0">
                            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-600">{log.sent} sent</span>
                            {log.failed > 0 && <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-red-50 text-red-500">{log.failed} fail</span>}
                            {log.expired > 0 && <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-500">{log.expired} exp</span>}
                            {expandedDispatch === log.dispatch_id ? <ChevronUp size={12} className="text-gray-400" /> : <ChevronDown size={12} className="text-gray-400" />}
                          </div>
                        </button>
                        {expandedDispatch === log.dispatch_id && (
                          <div className="px-4 pb-3">
                            {detailLoading ? (
                              <div className="flex justify-center py-4"><Loader2 size={14} className="animate-spin text-gray-300" /></div>
                            ) : dispatchDetail?.results?.length > 0 ? (
                              <div className="rounded-lg border border-gray-100 overflow-hidden">
                                <table className="w-full text-[11px]">
                                  <thead>
                                    <tr className="bg-gray-50 text-gray-400">
                                      <th className="text-left py-1.5 px-2 font-medium">User</th>
                                      <th className="text-left py-1.5 px-2 font-medium">Role</th>
                                      <th className="text-left py-1.5 px-2 font-medium">Status</th>
                                      <th className="text-left py-1.5 px-2 font-medium">Error</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {dispatchDetail.results.map((r, i) => (
                                      <tr key={i} className="border-t border-gray-50">
                                        <td className="py-1.5 px-2 text-gray-700 font-mono truncate" style={{ maxWidth: 120 }}>{r.user_id || '-'}</td>
                                        <td className="py-1.5 px-2 text-gray-500">{r.role}</td>
                                        <td className="py-1.5 px-2">
                                          <span className={`font-bold ${
                                            r.status === 'sent' ? 'text-emerald-600' :
                                            r.status === 'expired' ? 'text-amber-500' : 'text-red-500'
                                          }`}>{r.status}</span>
                                        </td>
                                        <td className="py-1.5 px-2 text-gray-400 truncate" style={{ maxWidth: 200 }}>{r.error || '-'}</td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            ) : (
                              <p className="text-xs text-gray-400 py-2">No per-subscription results available</p>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>

                <div className="rounded-2xl border border-gray-200 bg-white shadow-sm overflow-hidden">
                  <div className="p-4 border-b border-gray-100 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Smartphone size={14} className="text-gray-500" />
                      <p className="text-sm font-bold text-gray-900">Active Subscriptions</p>
                    </div>
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">
                      {subsTotal} total
                    </span>
                  </div>
                  <div className="overflow-y-auto" style={{ maxHeight: 300 }}>
                    {subscriptions.length === 0 ? (
                      <div className="text-center py-8">
                        <Smartphone size={24} className="mx-auto text-gray-200 mb-2" />
                        <p className="text-sm text-gray-400">No active push subscriptions</p>
                      </div>
                    ) : (
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-gray-400 border-b border-gray-100 bg-gray-50">
                            <th className="text-left py-2 px-3 font-medium">User ID</th>
                            <th className="text-left py-2 px-3 font-medium">Role</th>
                            <th className="text-left py-2 px-3 font-medium">Push Service</th>
                            <th className="text-left py-2 px-3 font-medium">Subscribed</th>
                          </tr>
                        </thead>
                        <tbody>
                          {subscriptions.map((s, i) => (
                            <tr key={i} className="border-b border-gray-50 hover:bg-gray-50">
                              <td className="py-2 px-3 text-gray-700 font-mono truncate" style={{ maxWidth: 140 }}>{s.user_id || '-'}</td>
                              <td className="py-2 px-3">
                                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
                                  s.role === 'admin' ? 'bg-violet-50 text-violet-600' : 'bg-gray-100 text-gray-500'
                                }`}>{s.role || 'unknown'}</span>
                              </td>
                              <td className="py-2 px-3 text-gray-500 truncate" style={{ maxWidth: 160 }}>{s.endpoint_domain || '-'}</td>
                              <td className="py-2 px-3 text-gray-400">{formatTime(s.subscribed_at)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>

                <div className="rounded-2xl border border-gray-200 bg-white shadow-sm overflow-hidden">
                  <div className="p-4 border-b border-gray-100 flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-2 min-w-0">
                      <Trash2 size={14} className="text-amber-500 shrink-0" />
                      <p className="text-sm font-bold text-gray-900">Stale Subscribers (auto-pruned)</p>
                      {pruneStatus && (
                        <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-50 text-amber-600 border border-amber-100">
                          {pruneStatus.inactive_subscriptions || 0} inactive
                        </span>
                      )}
                    </div>
                    <button
                      onClick={runPruneNow}
                      disabled={pruneRunning || deliveryLoading}
                      className="flex items-center gap-1 text-xs px-3 py-1.5 rounded-lg border border-violet-200 text-violet-700 bg-violet-50 hover:bg-violet-100 disabled:opacity-60 disabled:cursor-not-allowed"
                    >
                      {pruneRunning
                        ? <Loader2 size={11} className="animate-spin" />
                        : <Trash2 size={11} />}
                      Run prune now
                    </button>
                  </div>

                  {pruneStatus && (
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-4 border-b border-gray-100 bg-gray-50/60">
                      <div>
                        <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Total subs</p>
                        <p className="text-base font-bold text-gray-900 mt-0.5">{pruneStatus.total_subscriptions || 0}</p>
                      </div>
                      <div>
                        <p className="text-[10px] font-bold text-amber-500 uppercase tracking-wider">Inactive</p>
                        <p className="text-base font-bold text-amber-700 mt-0.5">{pruneStatus.inactive_subscriptions || 0}</p>
                      </div>
                      <div>
                        <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Threshold</p>
                        <p className="text-base font-bold text-gray-900 mt-0.5">
                          {pruneStatus.fail_threshold || 0}
                          <span className="text-[10px] font-medium text-gray-400 ml-1">fails / {pruneStatus.lookback_days || 0}d</span>
                        </p>
                      </div>
                      <div>
                        <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider">Last prune</p>
                        <p className="text-xs font-semibold text-gray-700 mt-0.5">
                          {pruneStatus.recent_inactive?.[0]?.deactivated_at
                            ? formatTime(pruneStatus.recent_inactive[0].deactivated_at)
                            : '—'}
                        </p>
                      </div>
                    </div>
                  )}

                  <div className="overflow-y-auto" style={{ maxHeight: 300 }}>
                    {!pruneStatus?.recent_inactive?.length ? (
                      <div className="text-center py-8">
                        <CheckCircle2 size={24} className="mx-auto text-emerald-200 mb-2" />
                        <p className="text-sm text-gray-400">No inactive subscribers — all endpoints healthy</p>
                      </div>
                    ) : (
                      <table className="w-full text-xs">
                        <thead>
                          <tr className="text-gray-400 border-b border-gray-100 bg-gray-50">
                            <th className="text-left py-2 px-3 font-medium">User ID</th>
                            <th className="text-left py-2 px-3 font-medium">Role</th>
                            <th className="text-left py-2 px-3 font-medium">Push Service</th>
                            <th className="text-right py-2 px-3 font-medium">Streak</th>
                            <th className="text-left py-2 px-3 font-medium">Deactivated</th>
                            <th className="text-left py-2 px-3 font-medium">Reason</th>
                          </tr>
                        </thead>
                        <tbody>
                          {pruneStatus.recent_inactive.map((s, i) => (
                            <tr key={i} className="border-b border-gray-50 hover:bg-gray-50">
                              <td className="py-2 px-3 text-gray-700 font-mono truncate" style={{ maxWidth: 140 }}>{s.user_id || '-'}</td>
                              <td className="py-2 px-3">
                                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${
                                  s.role === 'admin' ? 'bg-violet-50 text-violet-600' : 'bg-gray-100 text-gray-500'
                                }`}>{s.role || 'unknown'}</span>
                              </td>
                              <td className="py-2 px-3 text-gray-500 truncate" style={{ maxWidth: 160 }}>{s.endpoint_domain || '-'}</td>
                              <td className="py-2 px-3 text-right text-red-500 font-bold">{s.consecutive_failures_at_prune ?? '-'}</td>
                              <td className="py-2 px-3 text-gray-400">{formatTime(s.deactivated_at)}</td>
                              <td className="py-2 px-3 text-gray-500 truncate" style={{ maxWidth: 220 }} title={s.deactivation_reason}>{s.deactivation_reason || '-'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        )}
        <AdminQuickLinks links={['users','conversations','settings','activitylog']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
