import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, RefreshCw, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';
import AdminQuickLinks from './AdminQuickLinks';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import { adminGetAdsRevenue, adminGetMonetizationFunnel } from '@/utils/api';

const rupees = paise => `₹${(Number(paise ?? 0) / 100).toLocaleString('en-IN', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})}`;

export default function AdminMonetizationVerified({ adminToken, onNavigate }) {
  const [ads, setAds] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    const [adsResult, funnelResult] = await Promise.allSettled([
      adminGetAdsRevenue(adminToken, 30),
      adminGetMonetizationFunnel(adminToken),
    ]);
    if (adsResult.status === 'fulfilled') setAds(adsResult.value.data);
    else setError('Provider revenue could not be loaded.');
    if (funnelResult.status === 'fulfilled') setFunnel(funnelResult.value.data);
    else {
      setError(current => current ? `${current} Plan-tier counts could not be loaded.` : 'Plan-tier counts could not be loaded.');
      toast.error('Plan-tier counts could not be loaded');
    }
    setLoading(false);
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  const planCounts = Object.fromEntries((funnel?.funnel ?? []).map(item => [item.stage, Number(item.count ?? 0)]));
  const totalAccounts = Number(funnel?.total_count ?? 0);

  return (
    <SectionErrorBoundary name="Monetization">
      <div className="space-y-5 p-6" data-testid="admin-monetization">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold text-gray-900">Monetization evidence</h2>
            <p className="mt-1 text-sm text-gray-500">
              Provider receipts and account-tier counts are shown as separate data sources.
            </p>
          </div>
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 text-xs text-gray-600 disabled:opacity-50"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Refresh
          </button>
        </div>

        {error && <div role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>}

        <div className="grid gap-3 sm:grid-cols-2">
          <section className="rounded-2xl border border-gray-200 bg-white p-5">
            <p className="text-xs text-gray-500">Finalized AdSense net receipts · last 30 days</p>
            <p className="mt-2 text-2xl font-bold text-gray-900">
              {loading ? '…' : ads ? rupees(ads.totals?.net_revenue_paise) : 'Unavailable'}
            </p>
            <p className="mt-2 text-xs text-gray-500">
              {ads?.receipts?.length ?? 0} provider report(s). Estimates and historical payment labels are excluded.
            </p>
          </section>
          <section className="rounded-2xl border border-gray-200 bg-white p-5">
            <p className="text-xs text-gray-500">Account tier labels</p>
            <p className="mt-2 text-2xl font-bold text-gray-900">{loading ? '…' : totalAccounts.toLocaleString()}</p>
            <p className="mt-2 text-xs text-gray-500">
              D1 account records grouped by stored subscription tier; not payment or conversion evidence.
            </p>
          </section>
        </div>

        {ads && !ads.endpoint_configured && (
          <div role="alert" className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
            <AlertCircle size={16} className="mt-0.5 shrink-0" />
            <span>The production AdSense report endpoint is not configured, so the receipt total may not include new finalized reports.</span>
          </div>
        )}

        {ads?.reconciliation && (ads.reconciliation.failures?.length > 0 || ['failed', 'skipped', 'running'].includes(ads.reconciliation.status)) && (
          <div role="alert" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
            <div className="flex items-center gap-2 font-semibold"><ShieldAlert size={16} />Latest reconciliation: {ads.reconciliation.status}</div>
            {(ads.reconciliation.failures ?? []).length > 0 && (
              <p className="mt-2 text-xs">{ads.reconciliation.failures.join(' · ')}</p>
            )}
          </div>
        )}

        <section className="rounded-2xl border border-gray-200 bg-white p-5">
          <h3 className="text-sm font-semibold text-gray-900">Stored subscription tiers</h3>
          <p className="mt-1 text-xs text-gray-500">{funnel?.note ?? 'Loading source notes…'}</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-3">
            {['Free-tier records', 'Starter-tier records', 'Pro / premium-tier records'].map(label => (
              <div key={label} className="rounded-xl bg-gray-50 p-4">
                <p className="text-xl font-bold text-gray-900">{loading ? '…' : Number(planCounts[label] ?? 0).toLocaleString()}</p>
                <p className="mt-1 text-xs text-gray-500">{label}</p>
              </div>
            ))}
          </div>
        </section>
        <AdminQuickLinks links={['plans', 'ads', 'users', 'dashboard']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
