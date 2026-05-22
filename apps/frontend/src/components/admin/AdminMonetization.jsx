import { useState, useEffect, useCallback } from 'react';
import AdminQuickLinks from './AdminQuickLinks';
import {
  Loader2, DollarSign, Users, TrendingUp, CreditCard,
  RefreshCw, ArrowUp, ArrowDown, Gift, Percent,
  BarChart2, Wallet, Crown, Star, Edit2, Check, X,
} from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar, Cell,
} from 'recharts';
import axios from 'axios';
import { API_BASE, adminGetPlanConfig } from '@/utils/api';
import { toast } from 'sonner';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import CurrencyProvenanceCaption from './analytics/CurrencyProvenanceCaption';
const LIGHT_TOOLTIP = {
  contentStyle: {
    background: '#ffffff',
    border: '1px solid #e5e7eb',
    borderRadius: 12,
    color: '#374151',
    fontSize: 12,
    boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
  },
};

const PLAN_COLORS = { free: '#64748b', starter: '#8b5cf6', pro: '#f59e0b' };

function MetricCard({ icon: Icon, label, value, change, color, prefix = '' }) {
  return (
    <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
      <div className="flex items-center justify-between mb-3">
        <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}>
          <Icon size={18} style={{ color }} />
        </div>
        {change !== undefined && (
          <div className={`flex items-center gap-1 text-xs font-medium ${change >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
            {change >= 0 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}
            {Math.abs(change)}%
          </div>
        )}
      </div>
      <p className="text-2xl font-bold text-gray-900">{prefix}{typeof value === 'number' ? value.toLocaleString() : value}</p>
      <p className="text-gray-500 text-xs mt-1">{label}</p>
    </div>
  );
}

export default function AdminMonetization({ adminToken, onNavigate }) {
  const [overview, setOverview] = useState(null);
  const [revenue, setRevenue] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [predictor, setPredictor] = useState(null);
  const [referralCfg, setReferralCfg] = useState(null);
  const [planTiers, setPlanTiers] = useState(null);
  const [editingTier, setEditingTier] = useState(null);
  const [tierEdits, setTierEdits] = useState({});
  const [savingTier, setSavingTier] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [tab, setTab] = useState('overview');
  const [savingRef, setSavingRef] = useState(false);

  const headers = { withCredentials: true };

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const [ovRes, revRes, funRes, predRes, refRes, tiersRes] = await Promise.allSettled([
        axios.get(`${API_BASE}/admin/monetization/overview`, headers),
        axios.get(`${API_BASE}/admin/analytics/revenue?days=30`, headers),
        axios.get(`${API_BASE}/admin/analytics/funnel`, headers),
        axios.get(`${API_BASE}/admin/analytics/predictor`, headers),
        axios.get(`${API_BASE}/admin/monetization/referral-config`, headers),
        adminGetPlanConfig(adminToken),
      ]);
      if (ovRes.status === 'fulfilled') setOverview(ovRes.value.data);
      else { setLoadError(true); toast.error('Failed to load monetization overview'); }
      if (revRes.status === 'fulfilled') setRevenue(revRes.value.data);
      if (funRes.status === 'fulfilled') setFunnel(funRes.value.data);
      if (predRes.status === 'fulfilled') setPredictor(predRes.value.data);
      if (refRes.status === 'fulfilled') setReferralCfg(refRes.value.data);
      if (tiersRes.status === 'fulfilled') setPlanTiers(tiersRes.value.data);
      else toast.error('Failed to load pricing tiers');
    } catch (e) {
      setLoadError(true);
      toast.error('Monetization data failed to load');
    }
    finally { setLoading(false); }
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  const saveReferralConfig = async () => {
    setSavingRef(true);
    try {
      await axios.put(`${API_BASE}/admin/monetization/referral-config`, referralCfg, headers);
      toast.success('Referral config saved');
    } catch {
      toast.error('Failed to save referral config');
    }
    finally { setSavingRef(false); }
  };

  const startEditTier = (planKey, tierData) => {
    setEditingTier(planKey);
    setTierEdits({ price: tierData.price ?? '', credits: tierData.credits ?? '' });
  };

  const saveTier = async (planKey) => {
    setSavingTier(true);
    try {
      const payload = {
        price: parseInt(tierEdits.price, 10) || 0,
        credits: parseInt(tierEdits.credits, 10) || 0,
      };
      await axios.patch(`${API_BASE}/admin/plan-config/${planKey}`, payload, { withCredentials: true });
      setPlanTiers(prev => ({ ...prev, [planKey]: { ...(prev?.[planKey] || {}), ...payload } }));
      setEditingTier(null);
      toast.success(`${planKey} tier saved`);
    } catch {
      toast.error('Failed to save tier');
    } finally { setSavingTier(false); }
  };

  if (loading) return (
    <div className="flex justify-center p-10">
      <Loader2 size={24} className="animate-spin text-violet-500" />
    </div>
  );

  if (loadError && !overview) return (
    <div className="p-6 text-center">
      <p className="text-gray-500 text-sm mb-3">Failed to load monetization data. Check backend connectivity.</p>
      <button onClick={load} className="px-4 py-2 rounded-xl text-sm text-white transition-all hover:opacity-90 bg-violet-600">Retry</button>
    </div>
  );

  const TABS = [
    { id: 'overview', label: 'Overview' },
    { id: 'revenue', label: 'Revenue' },
    { id: 'funnel', label: 'Funnel' },
    { id: 'referrals', label: 'Referrals' },
  ];

  const cohortData = revenue?.cohorts
    ? Object.entries(revenue.cohorts).map(([name, value]) => ({ name: name.charAt(0).toUpperCase() + name.slice(1), value, fill: PLAN_COLORS[name] || '#64748b' }))
    : [];

  return (
    <SectionErrorBoundary name="Monetization">
      <div className="p-6 space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-gray-900 font-bold text-lg flex items-center gap-2">
              <Crown size={18} className="text-amber-500" />
              Monetization
            </h2>
            <p className="text-gray-500 text-sm mt-1">Revenue analytics, cohorts, referrals, and pricing</p>
          </div>
          <button
            onClick={load}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs text-gray-500 hover:text-gray-700 transition-colors bg-white border border-gray-200 shadow-sm"
          >
            <RefreshCw size={12} /> Refresh
          </button>
        </div>

        <div className="flex gap-1 rounded-xl p-1 w-fit bg-gray-100">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                tab === t.id
                  ? 'bg-violet-600 text-white shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'overview' && overview && (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <MetricCard icon={DollarSign} label="Revenue (30d)" value={overview.revenue_30d_inr} prefix="₹" color="#10b981" />
              <MetricCard icon={Wallet} label="Revenue (7d)" value={overview.revenue_7d_inr} prefix="₹" color="#3b82f6" />
              <MetricCard icon={Users} label="Paid Users" value={overview.total_paid_users} color="#8b5cf6" />
              <MetricCard icon={Percent} label="Conversion Rate" value={overview.conversion_rate + '%'} color="#f59e0b" />
            </div>
            {/* Task #731 S3 + S9, Task #740 — shared provenance caption so
                Monetization, Funnel, Revenue, and Conversions tabs all
                display the same wording. CurrencyProvenanceCaption uses
                the new currency_breakdown payload to render real numbers
                ("Razorpay ₹X + Stripe $Y → ₹Z"), gracefully shortening
                to "Includes Razorpay only" when usd_native is 0. */}
            {overview.currency_breakdown ? (
              <div className="-mt-1 pl-1">
                <CurrencyProvenanceCaption breakdown={overview.currency_breakdown} />
                {typeof overview.total_lifetime_revenue_inr === 'number' && (
                  <p className="text-[11px] text-gray-500 leading-snug">
                    Lifetime: ₹{overview.total_lifetime_revenue_inr.toLocaleString('en-IN', { maximumFractionDigits: 2 })}.
                  </p>
                )}
              </div>
            ) : overview.revenue_includes_stripe && (
              <div className="text-[11px] text-gray-500 -mt-1 pl-1 leading-snug">
                Includes Razorpay (INR) + Stripe (USD→INR @ rate captured at payment time).
                {typeof overview.total_lifetime_revenue_inr === 'number' && (
                  <> Lifetime: ₹{overview.total_lifetime_revenue_inr.toLocaleString('en-IN', { maximumFractionDigits: 2 })}.</>
                )}
              </div>
            )}

            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
              <MetricCard icon={Star} label="ARPU (INR)" value={overview.arpu_inr} prefix="₹" color="#ec4899" />
              <MetricCard icon={CreditCard} label="Starter Users" value={overview.starter_users} color="#8b5cf6" />
              <MetricCard icon={Crown} label="Pro Users" value={overview.pro_users} color="#f59e0b" />
              <MetricCard icon={Users} label="Free Users" value={overview.total_free_users} color="#64748b" />
            </div>

            {overview.recent_transactions?.length > 0 && (
              <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
                <h3 className="text-gray-500 text-sm font-medium mb-4">Recent Transactions <span className="text-gray-400 font-normal">(click to view user)</span></h3>
                <div className="space-y-2 max-h-64 overflow-y-auto">
                  {overview.recent_transactions.map((txn, i) => (
                    <button
                      key={i}
                      onClick={() => onNavigate?.('users', { search: txn.user_email || txn.user_id || '' })}
                      className="w-full flex items-center gap-3 p-2.5 rounded-lg text-left transition-colors cursor-pointer bg-gray-50 border border-gray-100 hover:bg-gray-100"
                      title={txn.user_email ? `View user ${txn.user_email}` : 'View user'}
                    >
                      <DollarSign size={14} className="text-emerald-500 flex-shrink-0" />
                      <span className="text-gray-500 text-xs font-mono flex-shrink-0">{txn.user_id?.slice(0, 8)}...</span>
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        txn.plan === 'pro' ? 'bg-amber-50 text-amber-600' : 'bg-violet-50 text-violet-600'
                      }`}>
                        {txn.plan}
                      </span>
                      {/* Task #731 S3 — primary INR + small original-currency caption.
                          For Stripe rows (currency_original=USD) admins see both
                          ₹X.XX (the unified rollup figure) and "$Y.YY" (the receipt
                          of record), so the row reconciles with Stripe's dashboard. */}
                      <span className="ml-auto flex flex-col items-end leading-tight">
                        <span className="text-gray-900 text-sm font-medium">
                          ₹{Number(txn.amount_inr ?? txn.amount ?? 0).toLocaleString('en-IN', { maximumFractionDigits: 2 })}
                        </span>
                        {txn.currency_original && txn.currency_original !== 'INR' && txn.amount_original ? (() => {
                          // Per-row stale detection mirrors isFxStale() in
                          // CurrencyProvenanceCaption: trust backend's
                          // "<provider>_stale" suffix on fx_source, treat
                          // missing/old fx_fetched_at as stale too. Keeps the
                          // staleness contract identical to the aggregate
                          // caption above so admins don't see contradictory
                          // signals.
                          const src = String(txn.fx_source || '');
                          let stale = !txn.fx_rate || src.endsWith('_stale');
                          if (!stale && txn.fx_fetched_at) {
                            const ts = new Date(txn.fx_fetched_at).getTime();
                            stale = Number.isNaN(ts) || (Date.now() - ts) / 36e5 > 24;
                          } else if (!stale && !txn.fx_fetched_at) {
                            stale = true;
                          }
                          const baseTitle = txn.fx_rate
                            ? `FX rate ${Number(txn.fx_rate).toFixed(4)} (${txn.fx_source || 'unknown source'})`
                            : '';
                          const title = stale && baseTitle
                            ? `${baseTitle} — FX RATE STALE, refresh upstream provider`
                            : baseTitle;
                          return (
                            <span className={`text-[10px] ${stale ? 'text-amber-700' : 'text-gray-400'}`} title={title}>
                              {stale && (
                                <span aria-label="Foreign exchange rate is stale" className="mr-1">⚠</span>
                              )}
                              {txn.currency_original === 'USD' ? '$' : ''}{Number(txn.amount_original).toFixed(2)} {txn.currency_original}
                            </span>
                          );
                        })() : null}
                      </span>
                      <span className="text-gray-400 text-xs">{txn.date}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {predictor && (
              <div className="rounded-2xl p-5 bg-gradient-to-r from-violet-50 to-amber-50 border border-violet-200">
                <div className="flex items-center gap-2 mb-3">
                  <TrendingUp size={16} className="text-violet-600" />
                  <h3 className="text-gray-900 font-semibold text-sm">30-Day Predictor</h3>
                </div>
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                  <div>
                    <p className="text-gray-500 text-xs">Current MRR</p>
                    <p className="text-gray-900 font-bold text-lg">₹{predictor.current_mrr_inr}</p>
                  </div>
                  <div>
                    <p className="text-gray-500 text-xs">Predicted MRR</p>
                    <p className="text-emerald-600 font-bold text-lg">₹{predictor.predicted_mrr_inr}</p>
                  </div>
                  <div>
                    <p className="text-gray-500 text-xs">Growth Rate</p>
                    <p className={`font-bold text-lg ${predictor.growth_rate_pct >= 0 ? 'text-emerald-600' : 'text-red-500'}`}>
                      {predictor.growth_rate_pct >= 0 ? '+' : ''}{predictor.growth_rate_pct}%
                    </p>
                  </div>
                  <div>
                    <p className="text-gray-500 text-xs">Signups This Month</p>
                    <p className="text-gray-900 font-bold text-lg">{predictor.signups_this_month}</p>
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        {tab === 'revenue' && revenue && (
          <>
            {revenue.daily_revenue?.length > 0 ? (
              <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
                <h3 className="text-gray-500 text-sm font-medium mb-4">Daily Revenue (Last 30 Days)</h3>
                <ResponsiveContainer width="100%" height={250}>
                  <AreaChart data={revenue.daily_revenue} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                    <XAxis dataKey="date" tick={{ fill: '#9ca3af', fontSize: 11 }} tickFormatter={d => d?.slice(5)} />
                    <YAxis tick={{ fill: '#9ca3af', fontSize: 11 }} />
                    <Tooltip {...LIGHT_TOOLTIP} />
                    <Area type="monotone" dataKey="revenue_inr" name="Revenue (₹)" stroke="#10b981" fill="rgba(16,185,129,0.15)" strokeWidth={2} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <div className="rounded-2xl p-8 text-center bg-white border border-gray-200">
                <DollarSign size={32} className="text-gray-200 mx-auto mb-3" />
                <p className="text-gray-500 text-sm">No revenue data yet</p>
              </div>
            )}

            {cohortData.length > 0 && (
              <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
                <h3 className="text-gray-500 text-sm font-medium mb-4">User Cohorts by Plan</h3>
                <div className="flex items-center justify-center">
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart data={cohortData} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                      <XAxis dataKey="name" tick={{ fill: '#9ca3af', fontSize: 11 }} />
                      <YAxis tick={{ fill: '#9ca3af', fontSize: 11 }} />
                      <Tooltip {...LIGHT_TOOLTIP} />
                      <Bar dataKey="value" name="Users" radius={[4, 4, 0, 0]}>
                        {cohortData.map((entry, i) => (
                          <Cell key={i} fill={entry.fill} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}
          </>
        )}

        {tab === 'funnel' && funnel && (
          <div className="space-y-4">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-gray-500 text-sm font-medium mb-4">Conversion Funnel</h3>
              <div className="space-y-3">
                {funnel.funnel?.map((stage, i) => {
                  const nextStage = funnel.funnel[i + 1];
                  const dropOffPct = nextStage && stage.count > 0
                    ? Math.round(((stage.count - nextStage.count) / stage.count) * 100)
                    : null;
                  const gradients = [
                    'linear-gradient(90deg,#3b82f6,#60a5fa)',
                    'linear-gradient(90deg,#8b5cf6,#a78bfa)',
                    'linear-gradient(90deg,#10b981,#34d399)',
                    'linear-gradient(90deg,#f59e0b,#fbbf24)',
                  ];
                  return (
                    <div key={i}>
                      <div className="relative">
                        <div className="flex items-center justify-between mb-1">
                          <span className="text-gray-700 text-sm font-medium">{stage.stage}</span>
                          <span className="text-gray-500 text-sm">{stage.count?.toLocaleString()} ({stage.pct}%)</span>
                        </div>
                        <div className="h-8 rounded-lg overflow-hidden bg-gray-100">
                          <div
                            className="h-full rounded-lg transition-all duration-500"
                            style={{ width: `${stage.pct}%`, background: gradients[i % gradients.length] }}
                          />
                        </div>
                      </div>
                      {dropOffPct !== null && (
                        <div className="flex items-center gap-1.5 mt-1 mb-1 pl-2">
                          <div className="w-px h-3 bg-gray-200" />
                          <span className="text-[11px] font-medium"
                            style={{ color: dropOffPct > 50 ? '#ef4444' : dropOffPct > 25 ? '#f59e0b' : '#10b981' }}>
                            ↓ {dropOffPct}% drop-off
                          </span>
                          <span className="text-[10px] text-gray-400">
                            ({(stage.count - nextStage.count)?.toLocaleString()} lost)
                          </span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <MetricCard icon={DollarSign} label="Revenue per Paid User" value={funnel.revenue_per_user} prefix="₹" color="#10b981" />
              <MetricCard icon={Percent} label="Overall Conversion" value={funnel.conversion_rate + '%'} color="#8b5cf6" />
            </div>
          </div>
        )}

        {tab === 'referrals' && (
          <div className="space-y-4">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-gray-900 font-semibold text-sm mb-4 flex items-center gap-2">
                <Gift size={16} className="text-pink-500" />
                Referral Program Configuration
              </h3>
              {referralCfg && (
                <div className="space-y-4">
                  <div className="flex items-center gap-3">
                    <label className="text-gray-500 text-sm w-32">Enabled</label>
                    <button
                      onClick={() => setReferralCfg(prev => ({ ...prev, enabled: !prev.enabled }))}
                      className={`w-12 h-6 rounded-full transition-colors ${referralCfg.enabled ? 'bg-emerald-500' : 'bg-gray-200'}`}
                    >
                      <div className={`w-5 h-5 bg-white rounded-full shadow transition-transform ${referralCfg.enabled ? 'translate-x-6' : 'translate-x-0.5'}`} />
                    </button>
                  </div>
                  <div className="flex items-center gap-3">
                    <label className="text-gray-500 text-sm w-32">Reward (credits)</label>
                    <input
                      type="number"
                      value={referralCfg.reward_credits}
                      onChange={(e) => setReferralCfg(prev => ({ ...prev, reward_credits: parseInt(e.target.value) || 0 }))}
                      className="rounded-xl px-3 py-2 text-sm text-gray-900 w-24 outline-none border border-gray-200 bg-gray-50 focus:bg-white focus:ring-2 focus:ring-violet-500/20 focus:border-violet-400"
                    />
                  </div>
                  <div className="flex items-center gap-3">
                    <label className="text-gray-500 text-sm w-32">Referrer (credits)</label>
                    <input
                      type="number"
                      value={referralCfg.referrer_credits}
                      onChange={(e) => setReferralCfg(prev => ({ ...prev, referrer_credits: parseInt(e.target.value) || 0 }))}
                      className="rounded-xl px-3 py-2 text-sm text-gray-900 w-24 outline-none border border-gray-200 bg-gray-50 focus:bg-white focus:ring-2 focus:ring-violet-500/20 focus:border-violet-400"
                    />
                  </div>
                  <button
                    onClick={saveReferralConfig}
                    disabled={savingRef}
                    className="flex items-center gap-2 disabled:opacity-50 text-white rounded-xl px-4 py-2 text-sm font-medium transition-all hover:opacity-90 bg-violet-600 shadow-sm"
                  >
                    {savingRef ? <Loader2 size={14} className="animate-spin" /> : null}
                    Save Configuration
                  </button>
                </div>
              )}
            </div>

            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-gray-900 font-semibold text-sm mb-3">Pricing Tiers</h3>
              <div className="grid grid-cols-3 gap-3">
                {[
                  { key: 'free',    label: 'Free',    color: '#64748b', defaultPrice: 0,   defaultCredits: 20   },
                  { key: 'starter', label: 'Starter', color: '#8b5cf6', defaultPrice: 99,  defaultCredits: 300  },
                  { key: 'pro',     label: 'Pro',     color: '#f59e0b', defaultPrice: 999, defaultCredits: 4000 },
                ].map(({ key, label, color, defaultPrice, defaultCredits }) => {
                  const tierData = planTiers?.[key] || {};
                  const price = tierData.price ?? defaultPrice;
                  const credits = tierData.credits ?? defaultCredits;
                  const isEditing = editingTier === key;
                  return (
                    <div key={key} className="p-4 rounded-xl bg-gray-50 border border-gray-200">
                      <div className="flex items-center justify-between mb-2">
                        <p className="font-bold text-sm" style={{ color }}>{label}</p>
                        {key !== 'free' && !isEditing && (
                          <button onClick={() => startEditTier(key, { price, credits })}
                            className="p-1 rounded-lg text-gray-400 hover:text-gray-600 transition-colors hover:bg-gray-200">
                            <Edit2 size={12} />
                          </button>
                        )}
                        {isEditing && (
                          <div className="flex gap-1">
                            <button onClick={() => saveTier(key)} disabled={savingTier}
                              className="p-1 rounded-lg bg-emerald-50 text-emerald-600 hover:bg-emerald-100 transition-colors">
                              <Check size={12} />
                            </button>
                            <button onClick={() => setEditingTier(null)} disabled={savingTier}
                              className="p-1 rounded-lg bg-red-50 text-red-500 hover:bg-red-100 transition-colors">
                              <X size={12} />
                            </button>
                          </div>
                        )}
                      </div>
                      {isEditing ? (
                        <div className="space-y-2">
                          <div>
                            <label className="text-[10px] text-gray-400">Price (₹)</label>
                            <input type="number" value={tierEdits.price}
                              onChange={e => setTierEdits(p => ({ ...p, price: e.target.value }))}
                              className="w-full rounded-lg px-2 py-1.5 text-sm text-gray-900 outline-none mt-0.5 bg-white border border-gray-200 focus:border-violet-400" />
                          </div>
                          <div>
                            <label className="text-[10px] text-gray-400">Credits</label>
                            <input type="number" value={tierEdits.credits}
                              onChange={e => setTierEdits(p => ({ ...p, credits: e.target.value }))}
                              className="w-full rounded-lg px-2 py-1.5 text-sm text-gray-900 outline-none mt-0.5 bg-white border border-gray-200 focus:border-violet-400" />
                          </div>
                        </div>
                      ) : (
                        <>
                          <p className="text-gray-900 text-xl font-bold">₹{price.toLocaleString()}</p>
                          <p className="text-gray-500 text-xs mt-1">{credits.toLocaleString()} credits</p>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        )}
        <AdminQuickLinks links={['plans','analytics','users','dashboard']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
