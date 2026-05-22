import { lazy, Suspense, useState, useEffect } from 'react';
import { Loader2, Key, Cpu, BarChart2, Network, Zap } from 'lucide-react';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';

const AdminApiConfig    = lazy(() => import('./AdminApiConfig'));
const AdminVertexPanel  = lazy(() => import('./AdminVertexPanel'));
const AdminIntelligence = lazy(() => import('./AdminIntelligence'));
const AdminAutomation   = lazy(() => import('./AdminAutomation'));

const TABS = [
  { id: 'providers', label: 'Providers & Keys' },
  { id: 'routing',   label: 'Routing & Pools' },
  { id: 'jobs',      label: 'Jobs & Crons' },
];

const SUB_TABS = [
  { id: 'apiconfig',    label: 'API Config',   icon: Key },
  { id: 'vertex',       label: 'AI Studio',    icon: Cpu },
  { id: 'intelligence', label: 'Model Stats',  icon: BarChart2 },
];

function isValidTab(id) { return TABS.some((t) => t.id === id); }
function isValidSub(id) { return SUB_TABS.some((s) => s.id === id); }

export default function AdminAiHub({ adminToken, onNavigate, navContext }) {
  const initialTab = isValidTab(navContext?.tab) ? navContext.tab : 'providers';
  const initialSub = isValidSub(navContext?.subTab) ? navContext.subTab : 'apiconfig';
  const [tab, setTab] = useState(initialTab);
  const [sub, setSub] = useState(initialSub);

  useEffect(() => {
    if (isValidTab(navContext?.tab)) setTab(navContext.tab);
    if (isValidSub(navContext?.subTab)) setSub(navContext.subTab);
  }, [navContext?.tab, navContext?.subTab]);

  return (
    <SectionErrorBoundary name="AI & Automation">
      <div className="flex flex-col h-full">
        <div className="flex gap-1.5 px-1 pb-3 items-center" data-testid="admin-ai-hub-tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              data-testid={`admin-ai-hub-tab-${t.id}`}
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
            {tab === 'providers' && (
              <div className="space-y-4">
                <div className="flex gap-1.5 items-center" data-testid="admin-ai-hub-subtabs">
                  {SUB_TABS.map(({ id, label, icon: Icon }) => (
                    <button
                      key={id}
                      onClick={() => setSub(id)}
                      data-testid={`admin-ai-hub-subtab-${id}`}
                      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-all border ${
                        sub === id
                          ? 'border-violet-200 bg-violet-50 text-violet-700'
                          : 'border-gray-200 bg-white text-gray-500 hover:text-gray-700'
                      }`}
                    >
                      <Icon size={11} /> {label}
                    </button>
                  ))}
                </div>
                <div>
                  {sub === 'apiconfig'    && <AdminApiConfig    adminToken={adminToken} onNavigate={onNavigate} />}
                  {sub === 'vertex'       && <AdminVertexPanel  adminToken={adminToken} onNavigate={onNavigate} />}
                  {sub === 'intelligence' && <AdminIntelligence adminToken={adminToken} onNavigate={onNavigate} />}
                </div>
              </div>
            )}

            {tab === 'routing' && (
              <div className="rounded-2xl p-8 bg-white border border-gray-200 shadow-sm text-center">
                <Network size={32} className="mx-auto mb-3 text-gray-300" />
                <p className="text-sm font-semibold text-gray-700">Routing & Pools</p>
                <p className="text-xs text-gray-400 mt-1 max-w-md mx-auto">
                  Per-route model selection, fallback chains, and pool weighting will live here.
                  For now, configure providers under <strong>Providers &amp; Keys</strong>.
                </p>
              </div>
            )}

            {tab === 'jobs' && (
              <div className="space-y-2">
                <div className="flex items-center gap-2 px-1">
                  <Zap size={14} className="text-violet-500" />
                  <p className="text-sm font-semibold text-gray-700">Background Jobs &amp; Cron Workflows</p>
                </div>
                <AdminAutomation adminToken={adminToken} onNavigate={onNavigate} />
              </div>
            )}
          </Suspense>
        </div>
      </div>
    </SectionErrorBoundary>
  );
}
