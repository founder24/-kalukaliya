import { useState, useEffect, useCallback } from 'react';
import { Zap, Crown, Sparkles, Edit2, X, CheckCircle2, Loader2 } from 'lucide-react';
import AdminQuickLinks from './AdminQuickLinks';
import { toast } from 'sonner';
import { adminGetDashboard, adminGetPlanConfig, adminUpdatePlanConfig } from '@/utils/api';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
const PLAN_UI = {
  free:    { icon: Zap,      gradient: 'from-gray-500 to-gray-600',     docAccess: 'zero',    docLabel: 'Zero document access',    color: 'text-gray-600'   },
  starter: { icon: Crown,    gradient: 'from-violet-500 to-purple-600', docAccess: 'limited', docLabel: 'Limited document access', color: 'text-violet-600' },
  pro:     { icon: Sparkles, gradient: 'from-amber-500 to-orange-600',  docAccess: 'full',    docLabel: 'Full document access',    color: 'text-amber-600'  },
};

function PlanCard({ planKey, ui, config, dist, onSave, saving }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ price: config.price, credits: config.credits, validity: config.validity });
  const Icon = ui.icon;
  const count = dist[planKey] || 0;
  const revenue = planKey !== 'free' ? (count * Number(draft.price)) : 0;

  useEffect(() => {
    setDraft({ price: config.price, credits: config.credits, validity: config.validity });
  }, [config]);

  const handleSave = async () => {
    await onSave(planKey, draft);
    setEditing(false);
  };

  return (
    <div className="rounded-2xl overflow-hidden bg-white border border-gray-200 shadow-sm">
      <div className={`h-1.5 bg-gradient-to-r ${ui.gradient}`} />
      <div className="p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <div className={`w-9 h-9 rounded-xl flex items-center justify-center bg-gradient-to-br ${ui.gradient}`}>
              <Icon size={16} className="text-white" />
            </div>
            <div>
              <p className={`font-bold text-sm ${ui.color}`}>{planKey.charAt(0).toUpperCase() + planKey.slice(1)}</p>
              <p className="text-[10px] text-gray-400">{count} users</p>
            </div>
          </div>
          <button onClick={() => setEditing(!editing)} className="p-1.5 rounded-lg hover:bg-gray-100 text-gray-400 hover:text-gray-600">
            {editing ? <X size={14} /> : <Edit2 size={14} />}
          </button>
        </div>
        <div className="space-y-2">
          {[{k:'price',l:'Price (₹)',type:'number'},{k:'credits',l:'Credits',type:'number'},{k:'validity',l:'Validity',type:'text'}].map(({k,l,type}) => (
            <div key={k} className="flex items-center justify-between">
              <span className="text-xs text-gray-500">{l}</span>
              {editing ? (
                <input type={type} value={draft[k]} onChange={(e) => setDraft((d) => ({...d,[k]:e.target.value}))}
                  className="h-7 w-28 px-2 rounded-lg text-xs text-gray-900 text-right outline-none bg-gray-50 border border-gray-200 focus:border-violet-400" />
              ) : (
                <span className="text-xs text-gray-900 font-mono">{k === 'price' ? `₹${draft[k]}` : k === 'credits' ? Number(draft[k]).toLocaleString() : draft[k]}</span>
              )}
            </div>
          ))}
          <div className="flex items-center justify-between pt-1 border-t border-gray-100">
            <span className="text-xs text-gray-400">Document access</span>
            <span className={`text-xs font-medium ${ui.color}`}>{ui.docLabel}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-400">Est. revenue</span>
            <span className="text-xs text-gray-600 font-mono">₹{revenue.toLocaleString()}</span>
          </div>
        </div>
        {editing && (
          <button onClick={handleSave} disabled={saving}
            className="w-full mt-3 h-8 rounded-xl text-xs font-semibold text-white flex items-center justify-center bg-violet-600 hover:bg-violet-700 transition-colors">
            {saving ? <Loader2 size={12} className="animate-spin mr-1" /> : <CheckCircle2 size={12} className="inline mr-1" />} Save
          </button>
        )}
      </div>
    </div>
  );
}

export default function AdminPlans({ adminToken, onNavigate }) {
  const [dist, setDist] = useState({ free: 0, starter: 0, pro: 0 });
  const [planConfig, setPlanConfig] = useState({
    free:    { price: 0,   credits: 20,   validity: 'daily reset' },
    starter: { price: 99,  credits: 500,  validity: 'daily reset' },
    pro:     { price: 999, credits: 4000, validity: 'daily reset' },
  });
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const fetchData = useCallback(async () => {
    try {
      const [dashRes, configRes] = await Promise.all([
        adminGetDashboard(adminToken),
        adminGetPlanConfig(adminToken),
      ]);
      const dashData = dashRes.data;
      if (dashData.plan_distribution) {
        setDist(dashData.plan_distribution);
      }
      const cfgData = configRes.data;
      if (cfgData && typeof cfgData === 'object') {
        setPlanConfig(prev => ({ ...prev, ...cfgData }));
      }
    } catch (err) {
      toast.error('Failed to load plan data');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const handleSave = async (planKey, draft) => {
    setSaving(true);
    try {
      const updated = { ...planConfig, [planKey]: { price: Number(draft.price), credits: Number(draft.credits), validity: draft.validity } };
      await adminUpdatePlanConfig(adminToken, updated);
      setPlanConfig(updated);
      toast.success(`${planKey.charAt(0).toUpperCase() + planKey.slice(1)} plan updated`);
    } catch (err) {
      toast.error('Failed to save plan config');
    } finally {
      setSaving(false);
    }
  };

  const totalUsers = Object.values(dist).reduce((a, b) => a + b, 0);
  const paidUsers = (dist.starter || 0) + (dist.pro || 0);
  const estRevenue = (dist.starter || 0) * Number(planConfig.starter.price) + (dist.pro || 0) * Number(planConfig.pro.price);
  const creditsIssued = (dist.starter || 0) * Number(planConfig.starter.credits) + (dist.pro || 0) * Number(planConfig.pro.credits);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 size={24} className="animate-spin text-violet-500" />
      </div>
    );
  }

  return (
    <SectionErrorBoundary name="Plans">
      <div className="space-y-6 max-w-4xl">
        <div>
          <h2 className="text-lg font-bold text-gray-900">Plans & Credits</h2>
          <p className="text-sm text-gray-400 mt-0.5">Configure subscription plans and review revenue metrics</p>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[['Est. Revenue',`₹${estRevenue.toLocaleString()}`,'text-emerald-600'],['Total Users',totalUsers.toLocaleString(),'text-gray-900'],['Paid Users',paidUsers.toLocaleString(),'text-violet-600'],['Credits Issued',creditsIssued.toLocaleString(),'text-amber-600']].map(([label,val,color]) => (
            <div key={label} className="rounded-xl p-3 bg-white border border-gray-200 shadow-sm">
              <p className={`text-xl font-bold ${color}`}>{val}</p>
              <p className="text-[10px] text-gray-400 mt-0.5">{label}</p>
            </div>
          ))}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {Object.entries(PLAN_UI).map(([k, ui]) => (
            <PlanCard key={k} planKey={k} ui={ui} config={planConfig[k]} dist={dist} onSave={handleSave} saving={saving} />
          ))}
        </div>
        <div className="rounded-xl p-4 bg-violet-50 border border-violet-200">
          <p className="text-sm text-violet-700 font-medium">Enable live payments</p>
          <p className="text-xs text-gray-500 mt-1">Configure Razorpay or Stripe credentials in the API Config section to activate real payment checkout and automatic plan upgrades.</p>
        </div>
        <AdminQuickLinks links={['monetization','analytics','users','apiconfig']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
