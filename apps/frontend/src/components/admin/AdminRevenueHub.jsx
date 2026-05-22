import { lazy, Suspense, useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';

const AdminMonetization = lazy(() => import('./AdminMonetization'));
const AdminPlans        = lazy(() => import('./AdminPlans'));
const AdminAds          = lazy(() => import('./AdminAds'));

const TABS = [
  { id: 'monetization', label: 'Monetization' },
  { id: 'plans',        label: 'Plans & Credits' },
  { id: 'ads',          label: 'Ad Revenue' },
];

export default function AdminRevenueHub({ adminToken, onNavigate, navContext }) {
  const initial = TABS.some((t) => t.id === navContext?.tab) ? navContext.tab : 'monetization';
  const [tab, setTab] = useState(initial);

  useEffect(() => {
    if (TABS.some((t) => t.id === navContext?.tab)) setTab(navContext.tab);
  }, [navContext?.tab]);

  return (
    <SectionErrorBoundary name="Revenue">
      <div className="flex flex-col h-full">
        <div className="flex gap-1.5 px-1 pb-3 items-center" data-testid="admin-revenue-hub-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              data-testid={`admin-revenue-hub-tab-${t.id}`}
              className={`px-3.5 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                tab === t.id
                  ? 'bg-violet-600 text-white shadow-sm'
                  : 'bg-gray-100 text-gray-500 hover:text-gray-700'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <div className="flex-1 min-h-0">
          <Suspense fallback={
            <div className="flex items-center justify-center h-40 gap-3">
              <Loader2 className="w-5 h-5 animate-spin text-violet-500" />
              <span className="text-sm text-gray-400">Loading…</span>
            </div>
          }>
            {tab === 'monetization' && <AdminMonetization adminToken={adminToken} onNavigate={onNavigate} />}
            {tab === 'plans'        && <AdminPlans        adminToken={adminToken} onNavigate={onNavigate} />}
            {tab === 'ads'          && <AdminAds          adminToken={adminToken} onNavigate={onNavigate} />}
          </Suspense>
        </div>
      </div>
    </SectionErrorBoundary>
  );
}
