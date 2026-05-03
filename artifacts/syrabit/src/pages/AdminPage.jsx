import { useState, useEffect, useCallback, useRef, lazy, Suspense } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import {
  LayoutDashboard, BookOpen, Users,
  MessageSquare, TrendingUp, Bell, Settings, HeartPulse, LogOut,
  ChevronLeft, ChevronRight, Loader2, Globe,
  Crown, Cpu, Activity, ShieldAlert, Cloud,
  ExternalLink,
} from 'lucide-react';
import axios from 'axios';
import { adminVerify, adminLogout, adminGetSettings, adminGetUnacknowledgedAlertCount, API_BASE } from '@/utils/api';
import { toast } from 'sonner';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import BreakGlassBanner from '@/components/admin/BreakGlassBanner';

const AdminDashboard       = lazy(() => import('@/components/admin/AdminDashboard'));
const AdminRoadmap         = lazy(() => import('@/components/admin/AdminRoadmap'));
const AdminContentHub      = lazy(() => import('@/components/admin/AdminContentHub'));
const AdminUsers           = lazy(() => import('@/components/admin/AdminUsers'));
const AdminConversations   = lazy(() => import('@/components/admin/AdminConversations'));
const AdminAnalytics       = lazy(() => import('@/components/admin/AdminAnalytics'));
const AdminNotifications   = lazy(() => import('@/components/admin/AdminNotifications'));
const AdminSettings        = lazy(() => import('@/components/admin/AdminSettings'));
const AdminHealth          = lazy(() => import('@/components/admin/AdminHealth'));
const AdminSeoManager      = lazy(() => import('@/components/admin/AdminSeoManager'));
// Task #296 — consolidated wrappers fold the previous fragmented panels
// into tabbed parents (AI & Automation, Revenue, Access & Security, Logs).
const AdminAiHub           = lazy(() => import('@/components/admin/AdminAiHub'));
const AdminRevenueHub      = lazy(() => import('@/components/admin/AdminRevenueHub'));
const AdminAccessSecurity  = lazy(() => import('@/components/admin/AdminAccessSecurity'));
// Logs Explorer now hosts Admin Actions inline as a `source=admin-actions`
// pseudo-filter (Task #296) — no separate hub wrapper needed.
const AdminLogsExplorer    = lazy(() => import('@/components/admin/AdminLogsExplorer'));
const AdminGcpPanel        = lazy(() => import('@/components/admin/AdminGcpPanel'));
const SyraAssistant        = lazy(() => import('@/components/admin/SyraAssistant'));
import { SyraProvider } from '@/components/admin/syra/SyraContext';

// Task #296 — flattened sidebar: 12 sections in 4 groups (was 24 in 6).
const SECTIONS = [
  { id: 'dashboard',     icon: LayoutDashboard, label: 'Dashboard',         group: 'main'       },
  { id: 'contenthub',    icon: BookOpen,        label: 'Content Editor',    group: 'main'       },
  { id: 'seomanager',    icon: Globe,           label: 'SEO Manager',       group: 'main'       },
  { id: 'users',         icon: Users,           label: 'Users',             group: 'audience'   },
  { id: 'conversations', icon: MessageSquare,   label: 'Conversations',     group: 'audience'   },
  { id: 'notifications', icon: Bell,            label: 'Notifications',     group: 'audience'   },
  { id: 'ai',            icon: Cpu,             label: 'AI & Automation',   group: 'operations' },
  { id: 'revenue',       icon: Crown,           label: 'Revenue',           group: 'operations' },
  { id: 'analytics',     icon: TrendingUp,      label: 'Analytics',         group: 'operations' },
  { id: 'security',      icon: ShieldAlert,     label: 'Access & Security', group: 'system'     },
  { id: 'logs',          icon: Activity,        label: 'Logs',              group: 'system'     },
  { id: 'health',        icon: HeartPulse,      label: 'Health / Uptime',   group: 'system'     },
  { id: 'gcp',           icon: Cloud,           label: 'GCP Integrations',  group: 'system'     },
  { id: 'settings',      icon: Settings,        label: 'Site Settings',     group: 'system'     },
];

const GROUP_LABELS = {
  main:       '',
  audience:   'AUDIENCE',
  operations: 'OPERATIONS',
  system:     'SYSTEM',
};

const GROUPS = ['main', 'audience', 'operations', 'system'];

const SECTION_COMPONENTS = {
  dashboard:     AdminDashboard,
  contenthub:    AdminContentHub,
  seomanager:    AdminSeoManager,
  users:         AdminUsers,
  conversations: AdminConversations,
  notifications: AdminNotifications,
  ai:            AdminAiHub,
  revenue:       AdminRevenueHub,
  analytics:     AdminAnalytics,
  security:      AdminAccessSecurity,
  logs:          AdminLogsExplorer,
  health:        AdminHealth,
  gcp:           AdminGcpPanel,
  settings:      AdminSettings,
  // Roadmap is no longer in the sidebar but stays mounted as a reachable
  // route via the `roadmap` deep-link redirect / dashboard quick link.
  roadmap:       AdminRoadmap,
};

// Task #296 — backwards-compat redirect map for legacy section ids.
// Every old `setActiveSection('apiconfig')` style call (deep links from
// emails, bookmarks, AdminQuickLinks, SyraAssistant intents, etc.) is
// rewritten into the new merged section + an initial tab/sub-tab so
// users land on exactly the right pane.
//
// Shape:  { section: 'ai', tab: 'providers', subTab: 'apiconfig' }
//
// Forwarders preserve any extra navContext keys (panel, channel, etc.)
// the caller passed alongside (used by botsecurity alert deep links).
export const SECTION_REDIRECTS = {
  // AI & Automation
  apiconfig:    { section: 'ai',       tab: 'providers', subTab: 'apiconfig'    },
  vertex:       { section: 'ai',       tab: 'providers', subTab: 'vertex'       },
  intelligence: { section: 'ai',       tab: 'providers', subTab: 'intelligence' },
  automation:   { section: 'ai',       tab: 'jobs' },
  // Revenue
  monetization: { section: 'revenue',  tab: 'monetization' },
  plans:        { section: 'revenue',  tab: 'plans' },
  ads:          { section: 'revenue',  tab: 'ads' },
  // Access & Security
  googleauth:   { section: 'security', tab: 'auth' },
  ratelimits:   { section: 'security', tab: 'ratelimits' },
  botsecurity:  { section: 'security', tab: 'botsecurity' },
  edubrowser:   { section: 'security', tab: 'edubrowser' },
  // Logs — Admin Actions is now an in-explorer source filter, not a tab.
  logsexplorer: { section: 'logs' },
  activitylog:  { section: 'logs', initialSources: ['admin-actions'] },
  // Conversations Feedback tab
  feedback:     { section: 'conversations', tab: 'feedback' },
  // Roadmap was retired from the sidebar but stays mounted; explicit
  // entry here documents the passthrough for old deep-links.
  roadmap:      { section: 'roadmap' },
};

export function resolveSectionRedirect(section, ctx = null) {
  // Special case retained from earlier behaviour: 'blog' opens the
  // ContentHub on its blog tab.
  if (section === 'blog') {
    return { section: 'contenthub', navContext: { ...(ctx || {}), initialTab: 'blog' } };
  }
  const redirect = SECTION_REDIRECTS[section];
  if (!redirect) {
    return { section, navContext: ctx };
  }
  const merged = { ...(ctx || {}) };
  if (redirect.tab && merged.tab === undefined) merged.tab = redirect.tab;
  if (redirect.subTab && merged.subTab === undefined) merged.subTab = redirect.subTab;
  if (redirect.initialSources && merged.initialSources === undefined) {
    merged.initialSources = redirect.initialSources;
  }
  return { section: redirect.section, navContext: merged };
}

export default function AdminPage() {
  const navigate = useNavigate();
  const [activeSection, setActiveSection] = useState('dashboard');
  const [navContext, setNavContext]        = useState(null);
  const handleNavigate = useCallback((section, ctx = null) => {
    const resolved = resolveSectionRedirect(section, ctx);
    setNavContext(resolved.navContext);
    setActiveSection(resolved.section);
  }, []);
  const [collapsed, setCollapsed]         = useState(false);
  const [verifying, setVerifying]         = useState(true);
  const [sysStatus, setSysStatus]         = useState('ok');

  const [adminEmail, setAdminEmail] = useState('');
  const [adminName,  setAdminName]  = useState('Admin');
  const [adminToken, setAdminToken] = useState(null);
  const [unackAlertCount, setUnackAlertCount] = useState(0);
  const alertPollRef = useRef(null);

  useEffect(() => {
    if (!adminToken || verifying) return;
    const fetchCount = () => {
      adminGetUnacknowledgedAlertCount(adminToken)
        .then((res) => setUnackAlertCount(res.data?.count || 0))
        .catch(() => {});
    };
    fetchCount();
    alertPollRef.current = setInterval(fetchCount, 60_000);
    return () => clearInterval(alertPollRef.current);
  }, [adminToken, verifying]);

  // Cookie-only admin auth (see prior comments) — adminToken is the
  // sentinel; the httponly cookie does the actual auth.
  useEffect(() => {
    adminVerify()
      .then((res) => {
        if (res.data?.name) setAdminName(res.data.name);
        if (res.data?.email) setAdminEmail(res.data.email);
        setAdminToken('verified');
        setVerifying(false);
      })
      .catch(() => {
        navigate('/admin/login');
      });
  }, [navigate]);

  useEffect(() => {
    if (verifying) return;
    const id = setInterval(() => {
      adminVerify()
        .catch(() => {
          setAdminToken(null);
          toast.error('Session expired. Please log in again.');
          navigate('/admin/login');
        });
    }, 12 * 60 * 60 * 1000);
    return () => clearInterval(id);
  }, [verifying, navigate]);

  useEffect(() => {
    if (verifying) return;
    const checkStatus = async () => {
      try {
        const [healthRes, settingsRes] = await Promise.allSettled([
          axios.get(`${API_BASE}/health`, { withCredentials: true }),
          adminGetSettings(adminToken),
        ]);

        const settingsData = settingsRes.status === 'fulfilled' ? settingsRes.value?.data : null;
        if (settingsData?.maintenance_mode) {
          setSysStatus('maintenance');
          return;
        }

        const healthData = healthRes.status === 'fulfilled' ? healthRes.value?.data : null;
        if (!healthData || healthRes.status === 'rejected') {
          setSysStatus('warn');
          return;
        }
        const deps = healthData.dependencies || {};
        const hasError = Object.values(deps).some((v) => v?.status === 'error' || v?.status === 'not_configured');
        setSysStatus(hasError ? 'warn' : 'ok');
      } catch {
        setSysStatus('warn');
      }
    };
    checkStatus();
  }, [verifying, adminToken]);

  const handleLogout = async () => {
    await adminLogout().catch(() => {});
    setAdminToken(null);
    toast.success('Logged out');
    navigate('/admin/login');
  };

  if (verifying) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center bg-gray-50">
        <Loader2 className="w-8 h-8 animate-spin text-violet-500 mb-3" />
        <p className="text-sm text-gray-400 mt-4">Verifying admin session...</p>
      </div>
    );
  }

  const ActiveComponent = SECTION_COMPONENTS[activeSection] || AdminDashboard;
  const activeLabel = SECTIONS.find((s) => s.id === activeSection)?.label
    || (activeSection === 'roadmap' ? 'Roadmap' : 'Admin');

  const statusConfig = {
    ok:          { label: 'All Systems Operational', dot: 'bg-emerald-500', text: 'text-emerald-700', border: 'border-emerald-200', bg: 'bg-emerald-50' },
    warn:        { label: 'Setup Required',          dot: 'bg-amber-500',   text: 'text-amber-700',   border: 'border-amber-200',   bg: 'bg-amber-50'   },
    maintenance: { label: 'Maintenance Mode',        dot: 'bg-red-500',     text: 'text-red-700',     border: 'border-red-200',     bg: 'bg-red-50'     },
  };
  const sc = statusConfig[sysStatus];

  // Sections that consume navContext are now the merged hubs (which all
  // accept a `tab`/`subTab` initial-state) plus the few legacy panels
  // that already used it directly (users, contenthub, dashboard).
  const SECTIONS_WITH_CONTEXT = new Set([
    'users', 'contenthub', 'dashboard', 'conversations',
    'ai', 'revenue', 'security', 'logs',
  ]);

  return (
    <SyraProvider activeSection={activeSection} adminToken={adminToken} adminEmail={adminEmail}>
    <div className="min-h-screen flex bg-[#f8f9fc]" data-testid="admin-dashboard">
      <aside
        className="flex flex-col h-screen sticky top-0 transition-all duration-300 flex-shrink-0 z-20 bg-white"
        style={{
          width: collapsed ? 68 : 252,
          borderRight: '1px solid #e5e7eb',
        }}
      >
        <div className="flex items-center px-4 border-b border-gray-100" style={{ height: 60 }}>
          {collapsed ? (
            <div className="w-9 h-9 rounded-xl flex items-center justify-center mx-auto bg-violet-50">
              <img src="/logo-56.webp" alt="S" width="24" height="24" className="w-6 h-6 rounded-lg object-cover" />
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl flex items-center justify-center bg-violet-50">
                <img src="/logo-56.webp" alt="Syrabit.ai" width="24" height="24" className="w-6 h-6 rounded-lg object-cover" />
              </div>
              <div>
                <p className="text-sm font-bold text-gray-900 tracking-tight" style={{ lineHeight: 1.2 }}>Syrabit.ai</p>
                <p className="text-[9px] font-semibold tracking-[0.15em] text-violet-500 uppercase">
                  Control Center
                </p>
              </div>
            </div>
          )}
        </div>

        <nav className="flex-1 overflow-y-auto py-3 px-2.5 space-y-0.5 scrollbar-thin">
          {GROUPS.map((group) => {
            const groupSections = SECTIONS.filter((s) => s.group === group);
            const label = GROUP_LABELS[group];
            return (
              <div key={group}>
                {label && !collapsed && (
                  <div className="flex items-center gap-2 px-3 py-2 mt-3 mb-0.5">
                    <div className="h-px flex-1 bg-gray-100" />
                    <p className="text-[9px] font-bold tracking-[0.15em] text-gray-400 flex-shrink-0">
                      {label}
                    </p>
                    <div className="h-px flex-1 bg-gray-100" />
                  </div>
                )}
                {collapsed && label && <div className="h-px mx-3 my-2 bg-gray-100" />}
                {groupSections.map(({ id, icon: Icon, label: sectionLabel }) => {
                  const isActive = activeSection === id;
                  return (
                    <button
                      key={id}
                      onClick={() => handleNavigate(id)}
                      className={`relative w-full flex items-center gap-3 px-3 py-2 rounded-xl transition-all duration-200 text-left group ${
                        isActive
                          ? 'bg-violet-50 text-violet-700 font-semibold'
                          : 'text-gray-500 hover:bg-gray-50 hover:text-gray-700'
                      }`}
                      data-testid={`admin-nav-${id}`}
                    >
                      {isActive && (
                        <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-5 rounded-r-full bg-violet-500" />
                      )}
                      <div className={`w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 transition-all duration-200 ${
                        isActive ? 'bg-violet-100' : ''
                      }`}>
                        <Icon size={15} className={`flex-shrink-0 ${isActive ? 'text-violet-600' : ''}`} />
                      </div>
                      {!collapsed && (
                        <span className="text-[13px] truncate">{sectionLabel}</span>
                      )}
                      {id === 'security' && unackAlertCount > 0 && (
                        <span className="ml-auto flex-shrink-0 min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-red-500 text-white text-[10px] font-bold px-1">
                          {unackAlertCount > 99 ? '99+' : unackAlertCount}
                        </span>
                      )}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </nav>

        <div className="border-t border-gray-100 px-2.5 py-3 space-y-1">
          {!collapsed && (
            <div className="flex items-center gap-2.5 px-3 py-2 mb-1 rounded-xl bg-violet-50">
              <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 bg-violet-600">
                <span className="text-xs font-bold text-white">{adminName?.charAt(0)?.toUpperCase() || 'A'}</span>
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-xs text-gray-700 font-medium truncate">{adminName}</p>
                <p className="text-[10px] text-gray-400 truncate">{adminEmail || 'Active session'}</p>
              </div>
            </div>
          )}
          <Link to="/library">
            <button className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs text-gray-400 hover:text-gray-600 hover:bg-gray-50 transition-all duration-200">
              <ExternalLink size={13} className="flex-shrink-0" />
              {!collapsed && <span>Student View</span>}
            </button>
          </Link>
          <button
            onClick={handleLogout}
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-xl text-xs text-red-400 hover:text-red-600 hover:bg-red-50 transition-all duration-200"
          >
            <LogOut size={13} className="flex-shrink-0" />
            {!collapsed && <span>Logout</span>}
          </button>
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="w-full flex items-center justify-center py-1.5 rounded-xl text-gray-300 hover:text-gray-500 hover:bg-gray-50 transition-all duration-200"
          >
            {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
          </button>
        </div>
      </aside>

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header
          className="flex items-center justify-between px-6 border-b border-gray-200 flex-shrink-0 z-10 bg-white"
          style={{ height: 60 }}
        >
          <div className="flex items-center gap-3">
            <h1 className="text-sm font-semibold text-gray-900">{activeLabel}</h1>
            <span className="text-gray-200">|</span>
            <span className="text-xs text-gray-400 flex items-center gap-1.5">
              <img src="/logo-56.webp" alt="" width="14" height="14" className="w-3.5 h-3.5 rounded-sm inline-block opacity-60" />
              Syrabit.ai
            </span>
          </div>

          <div className={`flex items-center gap-2 px-3.5 py-1.5 rounded-full text-xs font-medium border ${sc.text} ${sc.border} ${sc.bg}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${sc.dot} animate-pulse`} />
            <span className="text-[11px]">{sc.label}</span>
          </div>
        </header>

        <BreakGlassBanner adminToken={adminToken} />

        <main className={`flex-1 overflow-hidden flex flex-col ${activeSection === 'contenthub' ? '' : 'overflow-y-auto p-3 sm:p-4 md:p-6'}`}>
          <SectionErrorBoundary key={activeSection} name={activeLabel}>
            <Suspense fallback={
              <div className="flex items-center justify-center h-40 gap-3">
                <Loader2 className="w-5 h-5 animate-spin text-violet-500" />
                <span className="text-sm text-gray-400">Loading section...</span>
              </div>
            }>
              <ActiveComponent
                adminToken={adminToken}
                adminName={adminName}
                onNavigate={handleNavigate}
                navContext={SECTIONS_WITH_CONTEXT.has(activeSection) ? navContext : null}
              />
            </Suspense>
          </SectionErrorBoundary>
        </main>
      </div>

      <Suspense fallback={null}>
        <SyraAssistant
          activeSection={activeSection}
          onNavigate={handleNavigate}
          adminToken={adminToken}
          adminEmail={adminEmail}
        />
      </Suspense>
    </div>
    </SyraProvider>
  );
}
