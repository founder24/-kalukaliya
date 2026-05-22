import { lazy, Suspense, useState, useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';

const AdminGoogleAuth  = lazy(() => import('./AdminGoogleAuth'));
const AdminRateLimits  = lazy(() => import('./AdminRateLimits'));
const AdminBotSecurity = lazy(() => import('./AdminBotSecurity'));
const AdminEduBrowser  = lazy(() => import('./AdminEduBrowser'));

const TABS = [
  { id: 'auth',        label: 'Auth Providers' },
  { id: 'ratelimits',  label: 'Rate Limits' },
  { id: 'botsecurity', label: 'Bot Security' },
  { id: 'edubrowser',  label: 'Edu Mode' },
];

export default function AdminAccessSecurity({ adminToken, onNavigate, navContext }) {
  const initial = TABS.some((t) => t.id === navContext?.tab) ? navContext.tab : 'auth';
  const [tab, setTab] = useState(initial);

  useEffect(() => {
    if (TABS.some((t) => t.id === navContext?.tab)) setTab(navContext.tab);
  }, [navContext?.tab]);

  // navContext for botsecurity sub-panels (alert-settings, alert-cooldowns)
  // is forwarded only when that tab is active.
  const childNavContext = tab === 'botsecurity' ? navContext : null;

  return (
    <SectionErrorBoundary name="Access & Security">
      <div className="flex flex-col h-full">
        <div className="flex flex-wrap gap-1.5 px-1 pb-3 items-center" data-testid="admin-security-hub-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              data-testid={`admin-security-hub-tab-${t.id}`}
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
            {tab === 'auth'        && <AdminGoogleAuth  adminToken={adminToken} onNavigate={onNavigate} />}
            {tab === 'ratelimits'  && <AdminRateLimits  adminToken={adminToken} onNavigate={onNavigate} />}
            {tab === 'botsecurity' && <AdminBotSecurity adminToken={adminToken} onNavigate={onNavigate} navContext={childNavContext} />}
            {tab === 'edubrowser'  && <AdminEduBrowser  adminToken={adminToken} onNavigate={onNavigate} />}
          </Suspense>
        </div>
      </div>
    </SectionErrorBoundary>
  );
}
