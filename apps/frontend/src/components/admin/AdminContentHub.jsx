import { useState, useEffect, useCallback, lazy, Suspense } from 'react';
import {
  PenTool, FileText, ArrowRight,
  Loader2, Globe,
} from 'lucide-react';
import { toast } from 'sonner';
import axios from 'axios';
import { API_BASE } from '@/utils/api';
import { authHeaders } from '@/utils/adminHelpers';

import { SectionErrorBoundary } from '@/components/ErrorBoundary';
const AdminContentEditor = lazy(() => import('./AdminContentEditor'));
const AdminCmsDocEditor  = lazy(() => import('./AdminCmsDocEditor'));
const BlogPublishWizard  = lazy(() => import('./BlogPublishWizard'));


const API = API_BASE;

const TABS = [
  { id: 'editor',   label: 'Content Editor',  icon: PenTool,     color: 'violet',  desc: 'Write & edit chapter-level markdown content' },
  { id: 'cms',      label: 'CMS / Docs',      icon: FileText,    color: 'emerald', desc: 'Manage published pages, SEO docs & blog posts' },
  { id: 'blog',     label: 'Blog Publisher',  icon: Globe,       color: 'sky',     desc: 'SEO & GEO-rich 5-step blog publish wizard' },
];

const FLOW = [
  { label: 'Editor',        sub: 'Write content',      tab: 'editor',   arrow: true  },
  { label: 'CMS / Docs',   sub: 'Manage docs',        tab: 'cms',      arrow: true  },
  { label: 'Blog Publisher', sub: 'SEO & publish',     tab: 'blog',     arrow: false },
];

const COLOR_MAP = {
  indigo:  { active: 'border-indigo-500 text-indigo-600',  dot: 'bg-indigo-500', badge: 'bg-indigo-50 text-indigo-600' },
  violet:  { active: 'border-violet-500 text-violet-600',  dot: 'bg-violet-500', badge: 'bg-violet-50 text-violet-600' },
  amber:   { active: 'border-amber-500 text-amber-600',    dot: 'bg-amber-500',  badge: 'bg-amber-50 text-amber-600'  },
  emerald: { active: 'border-emerald-500 text-emerald-600',dot: 'bg-emerald-500',badge: 'bg-emerald-50 text-emerald-600'},
  rose:    { active: 'border-rose-500 text-rose-600',      dot: 'bg-rose-500',   badge: 'bg-rose-50 text-rose-600'    },
  sky:     { active: 'border-sky-500 text-sky-600',        dot: 'bg-sky-500',    badge: 'bg-sky-50 text-sky-600'      },
};

const EMPTY_CTX = {
  boardId: '', boardName: '',
  classId: '', className: '',
  streamId: '', streamName: '',
  subjectId: '', subjectName: '',
};

const HUB_CTX_KEY = 'syrabit_hub_ctx';

function loadPersistedCtx() {
  try {
    const raw = localStorage.getItem(HUB_CTX_KEY);
    if (!raw) return EMPTY_CTX;
    const parsed = JSON.parse(raw);
    if (Date.now() - (parsed._ts || 0) > 2 * 60 * 60 * 1000) {
      localStorage.removeItem(HUB_CTX_KEY);
      return EMPTY_CTX;
    }
    const { _ts, ...ctx } = parsed;
    return ctx;
  } catch { return EMPTY_CTX; }
}

const INTERNAL_TABS = new Set(['editor', 'cms', 'blog']);

export default function AdminContentHub({ adminToken, onNavigate: topNavigate, navContext }) {
  const [activeTab, setActiveTab] = useState(navContext?.initialTab || 'editor');
  const [boards, setBoards]       = useState([]);
  const [classes, setClasses]     = useState([]);
  const [streams, setStreams]     = useState([]);
  const [subjects, setSubjects]   = useState([]);
  const [loading, setLoading]     = useState(true);

  useEffect(() => {
    if (navContext?.initialTab && INTERNAL_TABS.has(navContext.initialTab)) {
      setActiveTab(navContext.initialTab);
    }
  }, [navContext]);

  const [hubContext, setHubContextRaw] = useState(loadPersistedCtx);

  const setHubContext = useCallback((ctxOrFn) => {
    setHubContextRaw(prev => {
      const next = typeof ctxOrFn === 'function'
        ? { ...EMPTY_CTX, ...prev, ...ctxOrFn(prev) }
        : { ...EMPTY_CTX, ...prev, ...ctxOrFn };
      // localStorage.setItem can throw on QuotaExceededError or in
      // private-mode browsers where storage is disabled. Caching the
      // hub context is best-effort — keep the in-memory state and log
      // so we notice if quota issues become persistent.
      try { localStorage.setItem(HUB_CTX_KEY, JSON.stringify({ ...next, _ts: Date.now() })); } catch (err) { console.warn('AdminContentHub: failed to persist hub context to localStorage:', err); }
      return next;
    });
  }, []);

  const navigate = useCallback((tab, ctxPatch) => {
    if (ctxPatch) setHubContext(ctxPatch);
    if (INTERNAL_TABS.has(tab)) {
      setActiveTab(tab);
    } else if (topNavigate) {
      topNavigate(tab);
    }
  }, [setHubContext, topNavigate]);

  const reloadHierarchy = useCallback(async () => {
    const cfg = authHeaders(adminToken);
    try {
      const [b, c, s, sub] = await Promise.all([
        axios.get(`${API}/admin/content/boards`, cfg),
        axios.get(`${API}/admin/content/classes`, cfg),
        axios.get(`${API}/admin/content/streams`, cfg),
        axios.get(`${API}/admin/content/subjects`, cfg),
      ]);
      setBoards(b.data || []);
      setClasses(c.data || []);
      setStreams(s.data || []);
      setSubjects(sub.data || []);
    } catch {
      toast.error('Failed to load content hierarchy');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { reloadHierarchy(); }, [reloadHierarchy]);

  const activeColor = COLOR_MAP[TABS.find(t => t.id === activeTab)?.color || 'violet'];

  return (
    <SectionErrorBoundary name="Content Hub">
      <div className="h-full flex flex-col" style={{ background: '#f8f9fc' }}>

        <div className="border-b px-4 py-1.5 flex items-center gap-1 flex-wrap"
          style={{ background: '#ffffff', borderColor: '#e5e7eb' }}>
          <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-widest mr-2">Workflow</span>
          {FLOW.map((step, i) => (
            <span key={i} className="flex items-center gap-1">
              <button
                onClick={() => setActiveTab(step.tab)}
                aria-label={`workflow-step-${step.tab}`}
                className={`flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold transition-all ${
                  activeTab === step.tab
                    ? COLOR_MAP[TABS.find(t => t.id === step.tab)?.color]?.badge
                    : 'text-gray-400 hover:text-gray-600'
                }`}
              >
                {step.label}
              </button>
              {step.arrow && <ArrowRight size={10} className="text-gray-300 flex-shrink-0" />}
            </span>
          ))}

          {hubContext.subjectName && (
            <span className="ml-auto flex items-center gap-1.5 flex-wrap">
              <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px]"
                style={{ background: '#f5f3ff', color: '#7c3aed' }}>
                <span className="text-gray-400">subject:</span>
                <span className="font-semibold truncate max-w-[120px]">{hubContext.subjectName}</span>
                <button
                  onClick={() => setHubContext(EMPTY_CTX)}
                  className="text-gray-400 hover:text-gray-600 ml-0.5"
                  title="Clear context"
                >×</button>
              </span>
            </span>
          )}
          {!hubContext.subjectName && (
            <span className="ml-auto text-[10px] text-gray-400">{boards.length} boards · {subjects.length} subjects</span>
          )}
        </div>

        <div className="border-b flex-shrink-0" style={{ borderColor: '#e5e7eb', background: '#ffffff' }}>
          <div className="flex px-4 gap-1 h-12 items-end">
            {TABS.map(tab => {
              const colors = COLOR_MAP[tab.color];
              const isActive = activeTab === tab.id;
              const Icon = tab.icon;
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-2 h-10 px-4 rounded-t-lg border-b-2 transition-all text-sm font-medium ${
                    isActive
                      ? `${colors.active} bg-gray-50`
                      : 'border-transparent text-gray-400 hover:text-gray-600 hover:bg-gray-50'
                  }`}
                >
                  <Icon size={14} />
                  <span className="hidden sm:inline">{tab.label}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div className="flex-1 overflow-hidden relative">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center z-10" style={{ background: 'rgba(248,249,252,0.80)' }}>
              <div className="flex items-center gap-2 text-gray-400 text-sm">
                <Loader2 size={16} className="animate-spin" /> Loading content data…
              </div>
            </div>
          )}

          <Suspense fallback={<div className="flex items-center justify-center py-12 text-gray-400 text-sm"><Loader2 size={16} className="animate-spin mr-2" />Loading…</div>}>
            {activeTab === 'editor' && (
              <div className="h-full overflow-hidden">
                <AdminContentEditor
                  adminToken={adminToken}
                  onNavigate={navigate}
                  hubContext={hubContext}
                  onHubContext={setHubContext}
                  onHierarchyChange={reloadHierarchy}
                />
              </div>
            )}

            {activeTab === 'cms' && (
              <div className="h-full overflow-hidden">
                <AdminCmsDocEditor
                  adminToken={adminToken}
                  onNavigate={navigate}
                  hubContext={hubContext}
                />
              </div>
            )}

            {activeTab === 'blog' && (
              <div className="h-full overflow-y-auto">
                <BlogPublishWizard
                  adminToken={adminToken}
                  onNavigate={navigate}
                  hubContext={hubContext}
                  onHubContext={setHubContext}
                />
              </div>
            )}
          </Suspense>
        </div>

      </div>
    </SectionErrorBoundary>
  );
}
