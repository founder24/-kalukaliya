import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Loader2, RefreshCw, DollarSign, Eye, BarChart2, Upload, Trash2,
  TrendingUp, Plus, CheckCircle2, AlertCircle,
} from 'lucide-react';
import {
  AreaChart, Area, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, Legend,
} from 'recharts';
import { toast } from 'sonner';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import {
  adminGetAdsOverview, adminListAdEarnings, adminAddAdEarning,
  adminDeleteAdEarning, adminUploadAdEarningsCsv, adminGetAdsenseStatus,
  adminAdsenseSync,
} from '@/utils/api';

// Task #347 — Quge5 removed (popunders + adult creatives, off site-wide).
const NETWORKS = ['adsense', 'adpushup', 'adsterra', 'propellerads'];
const NETWORK_COLORS = {
  adsense:      '#4285f4',
  adpushup:     '#8b5cf6',
  adsterra:     '#10b981',
  propellerads: '#f59e0b',
};

const LIGHT_TOOLTIP = {
  contentStyle: {
    background: '#ffffff', border: '1px solid #e5e7eb', borderRadius: 12,
    color: '#374151', fontSize: 12, boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
  },
};

function MetricCard({ icon: Icon, label, value, color, prefix = '' }) {
  return (
    <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}>
          <Icon size={18} style={{ color }} />
        </div>
      </div>
      <p className="text-2xl font-bold text-gray-900">
        {prefix}{typeof value === 'number' ? value.toLocaleString() : value}
      </p>
      <p className="text-gray-500 text-xs mt-1">{label}</p>
    </div>
  );
}

export default function AdminAds({ adminToken }) {
  const [days, setDays] = useState(30);
  const [overview, setOverview] = useState(null);
  const [earnings, setEarnings] = useState([]);
  const [adsenseStatus, setAdsenseStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState('overview');
  const [syncing, setSyncing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadNetwork, setUploadNetwork] = useState('adsense');
  const fileRef = useRef(null);
  const [newEntry, setNewEntry] = useState({
    network: 'adsense', date: new Date().toISOString().slice(0, 10),
    revenue_inr: '', impressions: '', placement: '',
  });
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [ovRes, earnRes, asRes] = await Promise.allSettled([
        adminGetAdsOverview(adminToken, days),
        adminListAdEarnings(adminToken, days),
        adminGetAdsenseStatus(adminToken),
      ]);
      if (ovRes.status === 'fulfilled') setOverview(ovRes.value.data);
      else toast.error('Failed to load ads overview');
      if (earnRes.status === 'fulfilled') setEarnings(earnRes.value.data?.entries || []);
      if (asRes.status === 'fulfilled') setAdsenseStatus(asRes.value.data);
    } finally { setLoading(false); }
  }, [adminToken, days]);

  useEffect(() => { load(); }, [load]);

  const onSyncAdsense = async () => {
    setSyncing(true);
    try {
      const res = await adminAdsenseSync(adminToken, Math.min(days, 30));
      toast.success(`Synced ${res.data?.rows_synced || 0} day(s) from AdSense`);
      await load();
    } catch (e) {
      toast.error(e.response?.data?.detail || 'AdSense sync failed');
    } finally { setSyncing(false); }
  };

  const onUploadCsv = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const res = await adminUploadAdEarningsCsv(adminToken, uploadNetwork, file);
      toast.success(`Uploaded ${res.data?.inserted || 0} new + ${res.data?.updated || 0} updated rows`);
      await load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'CSV upload failed');
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const onAddEntry = async () => {
    const rev = parseFloat(newEntry.revenue_inr);
    if (!isFinite(rev) || rev < 0) { toast.error('Revenue must be a non-negative number'); return; }
    setAdding(true);
    try {
      await adminAddAdEarning(adminToken, {
        network: newEntry.network,
        date: newEntry.date,
        revenue_inr: rev,
        impressions: newEntry.impressions ? parseInt(newEntry.impressions, 10) : null,
        placement: newEntry.placement || null,
      });
      toast.success('Earning added');
      setNewEntry({ ...newEntry, revenue_inr: '', impressions: '', placement: '' });
      await load();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Failed to add earning');
    } finally { setAdding(false); }
  };

  const onDeleteEntry = async (id) => {
    if (!window.confirm('Delete this earnings entry?')) return;
    try {
      await adminDeleteAdEarning(adminToken, id);
      toast.success('Deleted');
      await load();
    } catch {
      toast.error('Failed to delete');
    }
  };

  if (loading && !overview) return (
    <div className="flex justify-center p-10">
      <Loader2 size={24} className="animate-spin text-violet-500" />
    </div>
  );

  const totals = overview?.totals || { impressions: 0, revenue_inr: 0 };
  const overallRpm = totals.impressions > 0
    ? Math.round((totals.revenue_inr / totals.impressions) * 1000 * 100) / 100
    : 0;

  const TABS = [
    { id: 'overview', label: 'Overview' },
    { id: 'placements', label: 'Per Placement' },
    { id: 'earnings', label: 'Earnings & Sync' },
  ];

  return (
    <SectionErrorBoundary name="Ads">
      <div className="p-6 space-y-5">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div>
            <h2 className="text-gray-900 font-bold text-lg flex items-center gap-2">
              <BarChart2 size={18} className="text-violet-500" />
              Ad Revenue
            </h2>
            <p className="text-gray-500 text-sm mt-1">
              Cross-network earnings, fill rate, RPM, and viewability
            </p>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={days}
              onChange={(e) => setDays(parseInt(e.target.value, 10))}
              className="px-3 py-2 rounded-xl text-xs bg-white border border-gray-200 text-gray-700"
            >
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
            </select>
            <button
              onClick={load}
              className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs text-gray-500 hover:text-gray-700 transition-colors bg-white border border-gray-200 shadow-sm"
            >
              <RefreshCw size={12} /> Refresh
            </button>
          </div>
        </div>

        <div className="flex gap-1 rounded-xl p-1 w-fit bg-gray-100">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                tab === t.id ? 'bg-violet-600 text-white shadow-sm' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'overview' && overview && (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <MetricCard icon={DollarSign} label={`Revenue (${days}d)`} value={totals.revenue_inr} prefix="₹" color="#10b981" />
              <MetricCard icon={Eye} label={`Viewable Impressions (${days}d)`} value={totals.impressions} color="#3b82f6" />
              <MetricCard icon={TrendingUp} label="Viewability-adj RPM" value={overallRpm} prefix="₹" color="#8b5cf6" />
              <MetricCard
                icon={CheckCircle2}
                label="AdSense API"
                value={overview.adsense_configured ? 'Connected' : 'Not configured'}
                color={overview.adsense_configured ? '#10b981' : '#f59e0b'}
              />
            </div>

            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-gray-500 text-sm font-medium mb-4">Daily Revenue & Impressions</h3>
              {overview.daily?.length ? (
                <ResponsiveContainer width="100%" height={260}>
                  <AreaChart data={overview.daily} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                    <XAxis dataKey="date" tick={{ fill: '#9ca3af', fontSize: 11 }} tickFormatter={d => d?.slice(5)} />
                    <YAxis yAxisId="rev" tick={{ fill: '#9ca3af', fontSize: 11 }} />
                    <YAxis yAxisId="imp" orientation="right" tick={{ fill: '#9ca3af', fontSize: 11 }} />
                    <Tooltip {...LIGHT_TOOLTIP} />
                    <Legend />
                    <Area yAxisId="rev" type="monotone" dataKey="revenue_inr" name="Revenue (₹)" stroke="#10b981" fill="rgba(16,185,129,0.15)" strokeWidth={2} />
                    <Area yAxisId="imp" type="monotone" dataKey="impressions" name="Impressions" stroke="#3b82f6" fill="rgba(59,130,246,0.10)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="text-center text-sm text-gray-400 py-8">No data yet</div>
              )}
            </div>

            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-gray-500 text-sm font-medium mb-4">Revenue by Network</h3>
              {overview.networks?.length ? (
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={overview.networks} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                    <XAxis dataKey="network" tick={{ fill: '#9ca3af', fontSize: 11 }} />
                    <YAxis tick={{ fill: '#9ca3af', fontSize: 11 }} />
                    <Tooltip {...LIGHT_TOOLTIP} />
                    <Bar dataKey="revenue_inr" name="Revenue (₹)" radius={[4, 4, 0, 0]}>
                      {overview.networks.map((n, i) => (
                        <Cell key={i} fill={NETWORK_COLORS[n.network] || '#64748b'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="text-center text-sm text-gray-400 py-8">No data yet</div>
              )}

              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-gray-400 text-xs uppercase tracking-wide">
                      <th className="py-2 pr-3">Network</th>
                      <th className="py-2 pr-3">Impressions</th>
                      <th className="py-2 pr-3">Revenue (₹)</th>
                      <th className="py-2 pr-3">RPM (₹)</th>
                      <th className="py-2 pr-3">Fill rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.networks?.map((n) => (
                      <tr key={n.network} className="border-t border-gray-100">
                        <td className="py-2 pr-3 font-medium" style={{ color: NETWORK_COLORS[n.network] || '#374151' }}>
                          {n.network}
                        </td>
                        <td className="py-2 pr-3 text-gray-700">{Number(n.impressions ?? 0).toLocaleString()}</td>
                        <td className="py-2 pr-3 text-gray-900 font-semibold">₹{Number(n.revenue_inr ?? 0).toLocaleString()}</td>
                        <td className="py-2 pr-3 text-gray-700">₹{Number(n.rpm_inr ?? 0).toLocaleString()}</td>
                        <td className="py-2 pr-3 text-gray-700">
                          {n.fill_rate_pct != null ? `${n.fill_rate_pct}%` : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </>
        )}

        {tab === 'placements' && overview && (
          <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
            <h3 className="text-gray-500 text-sm font-medium mb-4">Per Placement</h3>
            {overview.placements?.length ? (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-gray-400 text-xs uppercase tracking-wide">
                      <th className="py-2 pr-3">Placement</th>
                      <th className="py-2 pr-3">Network</th>
                      <th className="py-2 pr-3">Impressions</th>
                      <th className="py-2 pr-3">Revenue (₹)</th>
                      <th className="py-2 pr-3">RPM (₹)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {overview.placements.map((p) => (
                      <tr key={`${p.network}:${p.placement}`} className="border-t border-gray-100">
                        <td className="py-2 pr-3 text-gray-900 font-mono text-xs">{p.placement}</td>
                        <td className="py-2 pr-3" style={{ color: NETWORK_COLORS[p.network] || '#374151' }}>{p.network}</td>
                        <td className="py-2 pr-3 text-gray-700">{Number(p.impressions ?? 0).toLocaleString()}</td>
                        <td className="py-2 pr-3 text-gray-900 font-semibold">₹{Number(p.revenue_inr ?? 0).toLocaleString()}</td>
                        <td className="py-2 pr-3 text-gray-700">₹{Number(p.rpm_inr ?? 0).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="text-[11px] text-gray-400 mt-3">
                  Revenue rows only appear when an earnings entry includes a <code>placement</code> column;
                  otherwise revenue rolls up at the network level on the Overview tab.
                </p>
              </div>
            ) : (
              <div className="text-center text-sm text-gray-400 py-8">No placement data yet</div>
            )}
          </div>
        )}

        {tab === 'earnings' && (
          <div className="space-y-4">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-gray-900 font-semibold text-sm mb-3 flex items-center gap-2">
                <CheckCircle2 size={14} className={adsenseStatus?.configured ? 'text-emerald-500' : 'text-gray-300'} />
                AdSense Management API
              </h3>
              {adsenseStatus?.configured ? (
                <div className="space-y-2">
                  <p className="text-xs text-gray-500">
                    Account: <span className="font-mono">{adsenseStatus.account_id}</span>
                  </p>
                  {adsenseStatus.sync && (
                    <div
                      className="text-[11px] rounded-lg px-3 py-2 border"
                      style={
                        adsenseStatus.sync.last_status === 'ok'
                          ? { background: 'rgba(16,185,129,0.06)', borderColor: 'rgba(16,185,129,0.2)', color: '#047857' }
                          : adsenseStatus.sync.last_status === 'error'
                            ? { background: 'rgba(239,68,68,0.06)', borderColor: 'rgba(239,68,68,0.2)', color: '#b91c1c' }
                            : { background: '#f9fafb', borderColor: '#e5e7eb', color: '#4b5563' }
                      }
                    >
                      <div className="font-semibold mb-0.5">
                        {adsenseStatus.sync.last_status === 'ok'    && '✓ Last sync OK'}
                        {adsenseStatus.sync.last_status === 'error' && '✕ Last sync failed'}
                        {!adsenseStatus.sync.last_status            && 'No sync attempted yet'}
                      </div>
                      {adsenseStatus.sync.last_success_at && (
                        <div>Last success: {new Date(adsenseStatus.sync.last_success_at).toLocaleString()}</div>
                      )}
                      {adsenseStatus.sync.last_attempted_at && adsenseStatus.sync.last_status === 'error' && (
                        <div>Last attempt: {new Date(adsenseStatus.sync.last_attempted_at).toLocaleString()}</div>
                      )}
                      {adsenseStatus.sync.last_error_message && (
                        <div className="font-mono text-[10px] mt-1 opacity-80">{adsenseStatus.sync.last_error_message}</div>
                      )}
                    </div>
                  )}
                  <button
                    onClick={onSyncAdsense}
                    disabled={syncing}
                    className="px-4 py-2 rounded-xl text-sm bg-violet-600 text-white hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
                  >
                    {syncing ? <Loader2 size={14} className="animate-spin" /> : <RefreshCw size={14} />}
                    Sync last {Math.min(days, 30)} days
                  </button>
                </div>
              ) : (
                <div className="text-xs text-gray-500 space-y-2">
                  <div className="flex items-start gap-2">
                    <AlertCircle size={14} className="text-amber-500 flex-shrink-0 mt-0.5" />
                    <span>
                      AdSense API not configured. Set the env vars below to enable automatic earnings sync,
                      or upload the daily CSV from the AdSense console as a v0 fallback.
                    </span>
                  </div>
                  <ul className="ml-6 list-disc text-gray-400">
                    {(adsenseStatus?.missing_env || []).map((k) => (
                      <li key={k}><code>{k}</code></li>
                    ))}
                  </ul>
                </div>
              )}
            </div>

            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-gray-900 font-semibold text-sm mb-3 flex items-center gap-2">
                <Upload size={14} /> CSV upload
              </h3>
              <p className="text-xs text-gray-500 mb-3">
                Columns: <code>date,revenue_inr,impressions,placement</code> (impressions + placement optional).
                Re-uploads upsert by (network, date, placement).
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={uploadNetwork}
                  onChange={(e) => setUploadNetwork(e.target.value)}
                  className="px-3 py-2 rounded-xl text-sm bg-gray-50 border border-gray-200"
                >
                  {NETWORKS.map(n => <option key={n} value={n}>{n}</option>)}
                </select>
                <input
                  ref={fileRef}
                  type="file"
                  accept=".csv,text/csv"
                  onChange={onUploadCsv}
                  disabled={uploading}
                  className="text-xs"
                />
                {uploading && <Loader2 size={14} className="animate-spin text-violet-500" />}
              </div>
            </div>

            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-gray-900 font-semibold text-sm mb-3 flex items-center gap-2">
                <Plus size={14} /> Add single entry
              </h3>
              <div className="grid grid-cols-2 lg:grid-cols-5 gap-2">
                <select
                  value={newEntry.network}
                  onChange={(e) => setNewEntry({ ...newEntry, network: e.target.value })}
                  className="px-3 py-2 rounded-xl text-sm bg-gray-50 border border-gray-200"
                >
                  {NETWORKS.map(n => <option key={n} value={n}>{n}</option>)}
                </select>
                <input
                  type="date"
                  value={newEntry.date}
                  onChange={(e) => setNewEntry({ ...newEntry, date: e.target.value })}
                  className="px-3 py-2 rounded-xl text-sm bg-gray-50 border border-gray-200"
                />
                <input
                  type="number" step="0.01" placeholder="Revenue (₹)"
                  value={newEntry.revenue_inr}
                  onChange={(e) => setNewEntry({ ...newEntry, revenue_inr: e.target.value })}
                  className="px-3 py-2 rounded-xl text-sm bg-gray-50 border border-gray-200"
                />
                <input
                  type="number" placeholder="Impressions (opt)"
                  value={newEntry.impressions}
                  onChange={(e) => setNewEntry({ ...newEntry, impressions: e.target.value })}
                  className="px-3 py-2 rounded-xl text-sm bg-gray-50 border border-gray-200"
                />
                <input
                  type="text" placeholder="Placement (opt)"
                  value={newEntry.placement}
                  onChange={(e) => setNewEntry({ ...newEntry, placement: e.target.value })}
                  className="px-3 py-2 rounded-xl text-sm bg-gray-50 border border-gray-200"
                />
              </div>
              <button
                onClick={onAddEntry}
                disabled={adding}
                className="mt-3 px-4 py-2 rounded-xl text-sm bg-violet-600 text-white hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
              >
                {adding ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />} Add
              </button>
            </div>

            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-gray-900 font-semibold text-sm mb-3">
                Recent earnings entries ({earnings.length})
              </h3>
              {earnings.length ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-left text-gray-400 text-xs uppercase tracking-wide">
                        <th className="py-2 pr-3">Date</th>
                        <th className="py-2 pr-3">Network</th>
                        <th className="py-2 pr-3">Placement</th>
                        <th className="py-2 pr-3">Revenue (₹)</th>
                        <th className="py-2 pr-3">Impressions</th>
                        <th className="py-2 pr-3">Fill %</th>
                        <th className="py-2 pr-3">Source</th>
                        <th className="py-2 pr-3"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {earnings.map((e) => (
                        <tr key={e._id} className="border-t border-gray-100">
                          <td className="py-2 pr-3 text-gray-700">{e.date}</td>
                          <td className="py-2 pr-3" style={{ color: NETWORK_COLORS[e.network] || '#374151' }}>{e.network}</td>
                          <td className="py-2 pr-3 text-gray-500 text-xs font-mono">{e.placement || '—'}</td>
                          <td className="py-2 pr-3 text-gray-900 font-semibold">₹{Number(e.revenue_inr || 0).toLocaleString()}</td>
                          <td className="py-2 pr-3 text-gray-700">{e.impressions ? Number(e.impressions).toLocaleString() : '—'}</td>
                          <td className="py-2 pr-3 text-gray-700">{e.fill_rate_pct != null ? `${e.fill_rate_pct}%` : '—'}</td>
                          <td className="py-2 pr-3">
                            {(() => {
                              const src = e.source || 'manual';
                              const styles = {
                                adsense_api: { bg: 'rgba(16,185,129,0.10)', fg: '#047857', label: 'API' },
                                csv:         { bg: 'rgba(59,130,246,0.10)', fg: '#1d4ed8', label: 'CSV' },
                                manual:      { bg: 'rgba(245,158,11,0.10)', fg: '#b45309', label: 'manual' },
                              }[src] || { bg: '#f3f4f6', fg: '#4b5563', label: src };
                              return (
                                <span
                                  className="px-2 py-0.5 rounded-md text-[10px] font-semibold uppercase tracking-wide"
                                  style={{ background: styles.bg, color: styles.fg }}
                                  title={
                                    e.currency_original && e.currency_original !== 'INR'
                                      ? `${e.currency_original} ${e.revenue_usd ?? ''} → ₹${e.revenue_inr} @ ${e.fx_rate ?? '?'} (${e.fx_source || 'unknown'})`
                                      : src === 'adsense_api' ? 'Auto-synced from AdSense Management API' :
                                        src === 'csv' ? 'Imported via CSV upload' : 'Manually entered'
                                  }
                                >
                                  {styles.label}
                                </span>
                              );
                            })()}
                          </td>
                          <td className="py-2 pr-3">
                            <button
                              onClick={() => onDeleteEntry(e._id)}
                              className="p-1.5 rounded-lg text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
                              title="Delete entry"
                            >
                              <Trash2 size={13} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-center text-sm text-gray-400 py-8">No entries yet</div>
              )}
            </div>
          </div>
        )}
      </div>
    </SectionErrorBoundary>
  );
}
