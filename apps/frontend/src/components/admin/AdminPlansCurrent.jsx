import { useCallback, useEffect, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import AdminQuickLinks from './AdminQuickLinks';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import { adminGetMonetizationFunnel } from '@/utils/api';

const TIERS = [
  { key: 'free', label: 'Free', color: 'text-slate-600' },
  { key: 'starter', label: 'Starter', color: 'text-violet-600' },
  { key: 'pro', label: 'Pro / premium', color: 'text-amber-600' },
];

export default function AdminPlansCurrent({ adminToken, onNavigate }) {
  const [distribution, setDistribution] = useState(null);
  const [sourceNote, setSourceNote] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const response = await adminGetMonetizationFunnel(adminToken);
      const counts = Object.fromEntries((response.data?.funnel ?? []).map(item => [
        item.stage === 'Free-tier records' ? 'free'
          : item.stage === 'Starter-tier records' ? 'starter'
            : 'pro',
        Number(item.count ?? 0),
      ]));
      setDistribution(counts);
      setSourceNote(response.data?.note ?? '');
    } catch {
      toast.error('Account tier counts could not be loaded');
      setDistribution(null);
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { load(); }, [load]);

  const total = distribution
    ? Object.values(distribution).reduce((sum, count) => sum + Number(count ?? 0), 0)
    : 0;

  return (
    <SectionErrorBoundary name="Plans">
      <div className="space-y-5 p-6" data-testid="admin-plan-tiers">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-bold text-gray-900">Plan account records</h2>
            <p className="mt-1 text-sm text-gray-500">
              Current D1 account labels only. Payment and plan-editing routes are not active in this Worker.
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

        {loading && !distribution ? (
          <div className="flex justify-center p-10"><Loader2 size={22} className="animate-spin text-violet-500" /></div>
        ) : (
          <>
            <div className="rounded-2xl border border-gray-200 bg-white p-5">
              <p className="text-2xl font-bold text-gray-900">{total.toLocaleString()}</p>
              <p className="mt-1 text-xs text-gray-500">Active account records by stored tier</p>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              {TIERS.map(tier => (
                <div key={tier.key} className="rounded-2xl border border-gray-200 bg-white p-5">
                  <p className={`text-sm font-semibold ${tier.color}`}>{tier.label}</p>
                  <p className="mt-2 text-2xl font-bold text-gray-900">
                    {distribution ? Number(distribution[tier.key] ?? 0).toLocaleString() : 'Unavailable'}
                  </p>
                  <p className="mt-1 text-xs text-gray-500">Stored subscription_tier count</p>
                </div>
              ))}
            </div>
          </>
        )}

        {sourceNote && <p className="text-xs text-gray-500">{sourceNote}</p>}
        <p className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-xs text-amber-900">
          Tier counts do not prove a payment was collected. No estimated plan value is presented as revenue.
        </p>
        <AdminQuickLinks links={['monetization', 'ads', 'users', 'dashboard']} onNavigate={onNavigate} />
      </div>
    </SectionErrorBoundary>
  );
}
