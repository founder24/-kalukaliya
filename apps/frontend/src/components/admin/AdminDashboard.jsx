import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import { log } from '@/utils/logger';
import AdminQuickLinks from './AdminQuickLinks';
import AdminDraftServedSubjects from './AdminDraftServedSubjects';
import AlertReasonsRow from './AlertReasonsRow';
import BotCachePanel from './BotCachePanel';
import CacheHitRatioPanel from './CacheHitRatioPanel';
import R2ColdStoragePanel from './R2ColdStoragePanel';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import { computeHeavyFreshness } from '@/utils/metricsFreshness';

const safeArr = (v) => (Array.isArray(v) ? v : []);
const safeObj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});
// Compact integer formatter — matches CF dashboard style ("7k", "6.02k",
// "1k") so the redesigned Cloudflare AI Crawl Control card reads the
// same way as Cloudflare's own Overview tab. Falls back to a plain
// localised string for values < 1,000 (where compact would just print
// the same digits) so small counts like "108" or "11" stay legible.
const _COMPACT_INT_FORMATTER = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 2 });
const formatCompactInt = (n) => {
  const num = Number(n) || 0;
  return num >= 1000 ? _COMPACT_INT_FORMATTER.format(num) : num.toLocaleString();
};
const normalizeChatFallbacks = (d) => (d ? { ...d, daily: safeArr(d.daily) } : null);
const normalizeLatency = (d) => (d ? { ...d, daily: safeArr(d.daily) } : null);
const normalizeTokenSpend = (d) => (d ? { ...d, daily: safeArr(d.daily), totals: safeObj(d.totals) } : null);
const normalizeTopQueries = (d) => (d ? { ...d, top_queries: safeArr(d.top_queries) } : null);
const normalizeChatSpeedups = (d) => (d ? { ...d, daily: safeArr(d.daily), warm_runs: safeArr(d.warm_runs), totals: safeObj(d.totals) } : null);
const normalizeVectorStats = (d) => (d ? { ...d, pages: safeObj(d.pages), chapters: safeObj(d.chapters) } : null);
import {
  Users, MessageSquare, BookOpen, Zap, Loader2, Activity,
  ArrowRight, PenTool, Settings, Eye, TrendingUp, RefreshCw,
  UserPlus, Globe, Search, Bot, BarChart2, Server, Clock,
  CheckCircle, AlertCircle, AlertTriangle, Wifi, Database, DollarSign, Crown,
  Layers, Link2, FileCheck, Target, Cpu, ShieldCheck, Smartphone,
  Volume2, VolumeX, Bell, BellOff, RotateCcw, Upload, Trash2, Music, X,
  ShieldAlert, UserCheck, Cloud,
} from 'lucide-react';
import AudioTrimPreview from './AudioTrimPreview';
import { usePushNotifications } from '@/hooks/usePushNotifications';
import axios from 'axios';
import { adminGetDashboard, adminGetCfOverview, seoPipelineStatus, adminSeoHealthHistory, adminSeoHealthSnapshotNow, seoHealthLive, seoHealthDeepScan, adminSeoDeepScanHistory, adminGetAlertCooldowns, API_BASE } from '@/utils/api';
import CloudflareAnalyticsBanner from './analytics/CloudflareAnalyticsBanner';
import { pushChannelTone } from '@/utils/pushChannelTone';
import { TODAY_BUCKET_CAPTION, UTC_MIDNIGHT_IN_IST } from '@/utils/time';
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, CartesianGrid, Legend,
  AreaChart, Area,
} from 'recharts';

function GlassCard({ children, className = '', glow, ...props }) {
  return (
    <div
      className={`relative rounded-2xl overflow-hidden bg-white border border-gray-200 shadow-sm ${className}`}
      {...props}
    >
      <div className="relative">{children}</div>
    </div>
  );
}

function StatCard({ label, value, icon: Icon, color, subLabel, subValue, pulse, onClick }) {
  const syraId = (label || '').toString().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
  return (
    <div
      className={`relative rounded-2xl p-5 overflow-hidden transition-all duration-300 group bg-white border border-gray-200 shadow-sm ${onClick ? 'cursor-pointer hover:shadow-md' : ''}`}
      onClick={onClick}
      data-testid="dashboard-stat-card"
      data-syra={syraId || undefined}
    >
      {pulse && (
        <span className="absolute top-3 right-3 flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-75" style={{ background: color }} />
          <span className="relative inline-flex rounded-full h-2 w-2" style={{ background: color }} />
        </span>
      )}
      <div className="flex items-center justify-between mb-3">
        <p className="text-gray-500 text-xs font-medium tracking-wide uppercase">{label}</p>
        <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${color}15` }}>
          <Icon size={16} style={{ color }} />
        </div>
      </div>
      <p className="text-2xl font-bold text-gray-900 tracking-tight">{typeof value === 'number' ? value.toLocaleString() : (value ?? 0)}</p>
      {subLabel && (
        <p className="text-xs text-gray-400 mt-1.5">
          {subLabel}: <span className="text-gray-600 font-medium">{typeof subValue === 'number' ? subValue.toLocaleString() : (subValue ?? 0)}</span>
        </p>
      )}
    </div>
  );
}

function formatTimeAgo(dateStr) {
  if (!dateStr) return '';
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now - date;
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHours = Math.floor(diffMins / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

const EVENT_ICONS = {
  signup:       { icon: UserPlus, color: '#10b981', bg: '#ecfdf5' },
  conversation: { icon: MessageSquare, color: '#8b5cf6', bg: '#f5f3ff' },
  search:       { icon: Search, color: '#60a5fa', bg: '#eff6ff' },
  subject_view: { icon: BookOpen, color: '#f59e0b', bg: '#fffbeb' },
  ai_click:     { icon: Bot, color: '#a78bfa', bg: '#f5f3ff' },
  page_view:    { icon: Eye, color: '#64748b', bg: '#f8fafc' },
};

function ActivityItem({ event, idx }) {
  const cfg = EVENT_ICONS[event.type] || EVENT_ICONS.page_view;
  const Icon = cfg.icon;
  return (
    <div
      key={event.timestamp + idx}
      className="flex items-center gap-3 py-2.5 px-3 rounded-xl transition-colors duration-200 hover:bg-gray-50 border border-gray-100"
    >
      <div className="w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0" style={{ background: cfg.bg }}>
        <Icon size={13} style={{ color: cfg.color }} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-sm text-gray-700 truncate">{event.message}</p>
        {event.details && <p className="text-xs text-gray-400 truncate">{event.details}</p>}
      </div>
      <span className="text-[11px] text-gray-400 flex-shrink-0 ml-2">{formatTimeAgo(event.timestamp)}</span>
    </div>
  );
}

const DEP_ICONS = { mongodb: Database, postgresql: Database, cloudflare_cache: Cloud, supabase: Database };
// Friendly display names so the underscored backend keys
// (e.g. ``cloudflare_cache``) don't render as "Cloudflare_cache" via
// the CSS ``capitalize`` rule. Keys not present here fall back to the
// raw ``name`` (still capitalize-d by CSS) so adding new probes is a
// no-op for the UI.
const DEP_LABELS = { mongodb: 'MongoDB', postgresql: 'PostgreSQL', cloudflare_cache: 'Cloudflare Cache', supabase: 'Supabase' };
const STATUS_COLORS = { ok: '#10b981', error: '#ef4444', not_configured: '#64748b', unknown: '#f59e0b' };

function DepStatusCard({ name, status, latency }) {
  const Icon = DEP_ICONS[name] || Server;
  const color = STATUS_COLORS[status] || STATUS_COLORS.unknown;
  const label = DEP_LABELS[name] || name;
  return (
    <div className="flex items-center gap-3 p-3 rounded-xl transition-all duration-200 hover:bg-gray-50 bg-gray-50 border border-gray-100" data-syra={`${name}-status`}>
      <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: `${color}15` }}>
        <Icon size={14} style={{ color }} />
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-gray-700 text-sm font-medium">{label}</p>
        <p className="text-xs" style={{ color }}>{status === 'ok' ? 'Connected' : status}</p>
      </div>
      {status === 'ok' && (
        <div className="text-right">
          <p className="text-gray-900 text-sm font-bold font-mono">{latency}ms</p>
          <div className="h-1.5 w-16 rounded-full overflow-hidden mt-1 bg-gray-100">
            <div
              className="h-full rounded-full transition-all"
              style={{
                width: `${Math.min(100, (latency / 500) * 100)}%`,
                background: latency < 100 ? '#10b981' : latency < 300 ? '#f59e0b' : '#ef4444',
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function PipelineWidget({ token }) {
  const [pipe, setPipe] = useState(null);
  useEffect(() => {
    seoPipelineStatus(token).then(r => setPipe(r.data)).catch(err => { console.warn('SEO pipeline status fetch failed:', err?.message || err); });
  }, [token]);
  if (!pipe) return null;
  const bars = [
    { label: 'Published', value: pipe.published, total: pipe.total_topics, color: '#10b981' },
    { label: 'Has Content', value: pipe.has_content, total: pipe.total_topics, color: '#7c3aed' },
    { label: 'Needs Schema', value: pipe.needs_schema, total: pipe.total_topics, color: '#f59e0b', invert: true },
    { label: 'Needs Links', value: pipe.needs_internal_links, total: pipe.total_topics, color: '#3b82f6', invert: true },
  ];
  return (
    <GlassCard className="p-5">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <Layers size={14} className="text-violet-500" />
          <h3 className="text-gray-600 font-semibold text-sm">Content Pipeline</h3>
          <span className="text-xs text-gray-400">({pipe.total_topics} topics · {pipe.pages_total} pages)</span>
        </div>
        {pipe.published_today > 0 && (
          <span className="text-[11px] font-bold px-2.5 py-0.5 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-600" title={TODAY_BUCKET_CAPTION}>
            +{pipe.published_today} today (UTC)
          </span>
        )}
      </div>
      <div className="space-y-3">
        {bars.map(b => {
          const pct = Math.round((b.value / Math.max(b.total, 1)) * 100);
          return (
            <div key={b.label}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs text-gray-400">{b.label}</span>
                <span className="text-xs font-mono" style={{ color: b.color }}>{b.value} ({pct}%)</span>
              </div>
              <div className="h-1.5 rounded-full overflow-hidden bg-gray-100">
                <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: b.color }} />
              </div>
            </div>
          );
        })}
      </div>
    </GlassCard>
  );
}

function alertColor(alert) {
  if (alert === 'red') return '#ef4444';
  if (alert === 'yellow') return '#f59e0b';
  return '#10b981';
}

function AlertBadge({ alert }) {
  const color = alertColor(alert);
  const label = alert === 'red' ? 'RED' : alert === 'yellow' ? 'YELLOW' : 'GREEN';
  return (
    <span
      className="text-[10px] font-bold px-2 py-0.5 rounded-full"
      style={{ background: `${color}12`, color, border: `1px solid ${color}25` }}
    >
      {label}
    </span>
  );
}

function RagAccuracyGauge({ accuracy }) {
  const pct = Math.min(100, Math.max(0, accuracy));
  const alert = pct < 95 ? 'red' : 'green';
  const color = alertColor(alert);
  const circumference = 2 * Math.PI * 40;
  const offset = circumference - (pct / 100) * circumference;
  return (
    <div className="flex flex-col items-center justify-center gap-2">
      <svg width="100" height="100" viewBox="0 0 100 100">
        <circle cx="50" cy="50" r="40" fill="none" stroke="#f3f4f6" strokeWidth="10" />
        <circle
          cx="50" cy="50" r="40"
          fill="none"
          stroke={color}
          strokeWidth="10"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          transform="rotate(-90 50 50)"
          style={{ transition: 'stroke-dashoffset 0.8s cubic-bezier(0.4,0,0.2,1)' }}
        />
        <text x="50" y="50" textAnchor="middle" fontSize="17" fontWeight="bold" fill="#111827" dominantBaseline="central">{pct.toFixed(1)}%</text>
        <text x="50" y="70" textAnchor="middle" fontSize="8" fill="#9ca3af">Target: 98%</text>
      </svg>
    </div>
  );
}

const TOOLTIP_STYLE = {
  background: '#ffffff',
  border: '1px solid #e5e7eb',
  borderRadius: 12,
  color: '#374151',
  fontSize: 12,
  boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
};

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={TOOLTIP_STYLE} className="p-3">
      <p className="text-[11px] text-gray-400 mb-1">{label}</p>
      {payload.map((p, i) => (
        <p key={i} className="text-xs" style={{ color: p.color }}>
          {p.name}: <span className="font-mono font-bold">{p.value}</span>
        </p>
      ))}
    </div>
  );
}

export default function AdminDashboard({ adminToken, onNavigate, navContext }) {
  const [data, setData] = useState(null);
  const [metrics, setMetrics] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  // Task #398 — render-only tick so the "Last updated Xs ago" badge on
  // the Revenue section advances visibly between the 60s polls. We
  // only flip this counter; the actual age math reads ``Date.now()``
  // at render so the value is always current. Mirrors the metricsMeta
  // tick pattern from AdminHealth.jsx (Task #396).
  const [, setMetricsMetaTick] = useState(0);

  const [ragAccuracy, setRagAccuracy] = useState(null);
  const [chatFallbacks, setChatFallbacks] = useState(null);
  const [vectorStats, setVectorStats] = useState(null);
  const [latency, setLatency] = useState(null);
  const [chatSpeedups, setChatSpeedups] = useState(null);
  const [speedupDays, setSpeedupDays] = useState(7);
  const [speedupLoading, setSpeedupLoading] = useState(false);
  // Task #810 — anonymous-quota wall card. Independent of the main
  // dashboard payload because the period picker and the
  // "Backfill today" action both re-fetch this card on demand
  // without touching the rest of the dashboard cache.
  const [anonQuotaWall, setAnonQuotaWall] = useState(null);
  const [anonQuotaDays, setAnonQuotaDays] = useState(7);
  const [anonQuotaLoading, setAnonQuotaLoading] = useState(false);
  const [anonQuotaBackfilling, setAnonQuotaBackfilling] = useState(false);
  const [topQueries, setTopQueries] = useState(null);
  const [tokenSpend, setTokenSpend] = useState(null);
  const [funnel, setFunnel] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [pwaStats, setPwaStats] = useState(null);
  const [botAnalytics, setBotAnalytics] = useState(null);
  // Cloudflare AI Crawl Control — sourced from CF GraphQL via the
  // /admin/analytics/cf-ai-crawl-control route. When CF analytics
  // credentials are missing the route returns `available: false` with a
  // reason so we render an empty-state card instead of hiding the section.
  const [cfCrawlControl, setCfCrawlControl] = useState(null);
  // Cloudflare Account Analytics overview — re-fetched whenever the
  // user clicks 24h / 7d / 30d on the Traffic card. Independent of the
  // dashboard payload so the selector responds instantly without
  // blowing the whole dashboard cache.
  const [cfRange, setCfRange] = useState('7d');
  const [cfOverview, setCfOverview] = useState(null);
  const [cfOverviewLoading, setCfOverviewLoading] = useState(false);
  // Task #13 — nightly SEO prewarm coverage tile. Sourced from
  // /admin/seo/prewarm-coverage which returns the most recent
  // aca_jobs.prewarm_seo_routes run summary (combined success_rate
  // + KV-only kv_success_rate so a degraded materialization path is
  // visible even when edge-only legs stay healthy).
  const [prewarmCoverage, setPrewarmCoverage] = useState(null);
  const [indexNowStats, setIndexNowStats] = useState(null);
  const [indexNowHistory, setIndexNowHistory] = useState(null);
  const [retryingEndpoint, setRetryingEndpoint] = useState(null);
  const [resubmittingIndexNow, setResubmittingIndexNow] = useState(false);
  const [resubmitMessage, setResubmitMessage] = useState('');
  const [alertHistory, setAlertHistory] = useState(null);
  // Task #991 — surface the persistent alert-cooldown active count
  // (from `/admin/alerts/cooldowns?only_active=true`) directly on the
  // dashboard so on-call admins can spot "alert is being suppressed"
  // without having to drill into Bot Security → Suppressed Alerts.
  // Polled every 60s on the same cadence as the rest of the dashboard
  // header counters (see AdminPage.jsx unack-alerts polling).
  const [cooldownActiveCount, setCooldownActiveCount] = useState(0);
  const [seoHealth, setSeoHealth] = useState(null);
  const [seoHealthRefreshing, setSeoHealthRefreshing] = useState(false);
  const [seoLive, setSeoLive] = useState(null);
  const [seoLiveLoading, setSeoLiveLoading] = useState(false);
  const [seoLiveError, setSeoLiveError] = useState(null);
  // Task #461 — manual D1 sync trigger + post-sync result rendering.
  // The backend (`POST /admin/d1-sync`, see admin_content.py) returns
  // the primary `sync_full` result merged with an `extended_mirror`
  // sub-block: `{ success, tables[], row_counts{}, reason? }`. Until
  // this state landed the UI dropped the extended-mirror block on the
  // floor, forcing operators to inspect the network tab to see if the
  // seo_meta / audit_log / syllabus_map mirror actually succeeded.
  const [d1SyncRunning, setD1SyncRunning] = useState(false);
  const [d1SyncResult, setD1SyncResult] = useState(null);
  const [d1SyncDurationMs, setD1SyncDurationMs] = useState(null);
  const [d1SyncError, setD1SyncError] = useState(null);
  // Task #299: which sitemap row is currently expanded to show its
  // failing URL list. Only one is open at a time to keep the card compact.
  const [expandedSitemap, setExpandedSitemap] = useState(null);
  // Task #345: per-sitemap deep-scan results, keyed by sitemap name.
  // Shape: { [name]: { loading, error, data } } where `data` is the
  // response from /admin/seo/sitemap-failing-urls (full failing list).
  const [sitemapDeepScans, setSitemapDeepScans] = useState({});
  // Task #350: auto-deep-scan summaries harvested by the alert loop
  // (Task #347) and persisted on db.alerts. Lets the on-call admin see
  // the true blast radius the moment they open the dashboard, with a
  // "fresh" indicator and a banner if any sitemap was auto-scanned in
  // the last hour.
  const [seoAutoDeepScans, setSeoAutoDeepScans] = useState(null);
  // Task #692 — alert filter selection persists in the URL query
  // string so admins can bookmark and share a focused view (e.g. drop
  // a `?alert_status=unacknowledged&alert_reason=foo` link into an
  // incident ticket). Initial state reads from the current URL so a
  // refresh restores the same view; a useEffect (below) syncs every
  // change back via history.replaceState (no extra entry in the back
  // stack — the dashboard isn't a navigable surface).
  const [alertFilter, setAlertFilter] = useState(() => {
    if (typeof window === 'undefined') return 'all';
    const v = new URLSearchParams(window.location.search).get('alert_status');
    return v === 'unacknowledged' || v === 'acknowledged' || v === 'all' ? v : 'all';
  });
  const [alertReasonFilter, setAlertReasonFilter] = useState(() => {
    if (typeof window === 'undefined') return '';
    return new URLSearchParams(window.location.search).get('alert_reason') || '';
  });
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const params = new URLSearchParams(window.location.search);
    if (alertFilter && alertFilter !== 'all') {
      params.set('alert_status', alertFilter);
    } else {
      params.delete('alert_status');
    }
    if (alertReasonFilter) {
      params.set('alert_reason', alertReasonFilter);
    } else {
      params.delete('alert_reason');
    }
    const qs = params.toString();
    const next = `${window.location.pathname}${qs ? `?${qs}` : ''}${window.location.hash}`;
    if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
      window.history.replaceState(window.history.state, '', next);
    }
  }, [alertFilter, alertReasonFilter]);
  // Task #426: hide synthetic test alerts (from "Test alert delivery" button)
  // by default; admins can opt in via the "Show test alerts" toggle.
  const [showSyntheticAlerts, setShowSyntheticAlerts] = useState(false);
  const [alertSettingsOpen, setAlertSettingsOpen] = useState(false);
  const [alertSettings, setAlertSettings] = useState(null);
  const [alertSettingsDraft, setAlertSettingsDraft] = useState(null);
  const [alertSettingsSaving, setAlertSettingsSaving] = useState(false);
  const [failedSections, setFailedSections] = useState([]);
  const [notifPrefs, setNotifPrefs] = useState(null);
  const [notifPrefsSaving, setNotifPrefsSaving] = useState(false);
  const [notifPrefsOpen, setNotifPrefsOpen] = useState(false);
  const [pushDeliverySummary, setPushDeliverySummary] = useState(null);
  // Task #474 — most-recent SEO daily-summary email dispatches, surfaced
  // under the SEO summary opt-in toggle so admins can see whether the last
  // scheduled run actually emailed them (or was suppressed by quiet hours).
  const [seoSummaryDispatches, setSeoSummaryDispatches] = useState(null);
  // Task #476 — Cloudflare Workers KV usage snapshot from the edge
  // worker. ``null`` while loading; ``{ configured: false, ... }`` when
  // the edge URL/secret aren't set; ``{ configured: true, snapshot }``
  // when the worker responded. Surfaced in the prefs modal so admins can
  // see read/write counters & quota % at a glance and react before a
  // KV outage starts dropping pages and the analytics beacon.
  const [kvHealth, setKvHealth] = useState(null);
  // Task #510 — operator-toggled expansion of the per-isolate
  // breakdown under each kv-health binding row. Map<binding, bool> so
  // multiple bindings can be expanded independently. Starts collapsed
  // because most operators only need the aggregate; the breakdown is
  // useful when burn spikes and we need to know if one isolate is
  // responsible for most of it.
  const [kvExpandedIsolates, setKvExpandedIsolates] = useState({});
  // Task #315 — R2 cold-storage watchdog snapshot from the edge worker.
  // ``null`` while loading; ``{ configured, state?, ... }`` once the
  // backend responds. Surfaced as a tile so admins can confirm the
  // monthly lifecycle / Logpush-cap watchdog (Task #314) is still
  // happy between cron ticks without opening the Cloudflare dashboard.
  const [r2Health, setR2Health] = useState(null);
  // Task #322 — pending state for the inline "Reset" button on the
  // R2 watchdog-blind indicator. Cleared in the finally block of the
  // handler below so the button re-enables even if the POST throws.
  const [r2ResettingWatchdog, setR2ResettingWatchdog] = useState(false);
  const [r2Reevaluating, setR2Reevaluating] = useState(false);
  // Task #689 — Cached state of the periodic Gemini health probe
  // (Task #677). ``null`` while loading; ``{ status, last_check_ts,
  // reason, consecutive_failures, ... }`` once the backend responds.
  // Surfaced as a tile so admins can see *current* probe state without
  // grepping logs and waiting for the email/Slack alert.
  const [vertexProbe, setVertexProbe] = useState(null);
  // Task #470 — Latest GitHub Actions run for the backend + frontend
  // workflows. ``null`` while loading; ``{ configured: false, ... }``
  // when GITHUB_REPO isn't set; ``{ configured: true, runs: {...} }``
  // when the API responded. Surfaced so the on-call admin sees red CI
  // without leaving the app.
  const [ciStatus, setCiStatus] = useState(null);
  const [ciRerunning, setCiRerunning] = useState(null);
  // Task #434 — last_success_at / last_error for the browser-push
  // channel from /admin/alert-settings (channel_status.push). Surfaced
  // inline in the notifications tile so admins notice a degraded push
  // pipeline without drilling into Bot Security → Alert Settings.
  const [pushChannelStatus, setPushChannelStatus] = useState(null);
  const prevAlertIdsRef = useRef(new Set());
  const audioCtxRef = useRef(null);
  const customAudioRef = useRef(null);
  const chimeFileInputRef = useRef(null);
  const [chimeUploading, setChimeUploading] = useState(false);
  const [pendingChimeFile, setPendingChimeFile] = useState(null);
  const pushNotif = usePushNotifications({
    serverPushEnabled: notifPrefs?.push_enabled,
  });

  const alertSoundEnabled = notifPrefs?.sound_enabled ?? true;
  const chimeTone = notifPrefs?.chime_tone ?? 'default';

  const CHIME_TONES = {
    default: { label: 'Default', freqs: [880, 1100, 880], type: 'sine', dur: 0.5 },
    soft: { label: 'Soft', freqs: [440, 550, 440], type: 'sine', dur: 0.6 },
    urgent: { label: 'Urgent', freqs: [1200, 900, 1200, 900], type: 'square', dur: 0.4 },
    bell: { label: 'Bell', freqs: [1047, 1319, 1568], type: 'sine', dur: 0.7 },
  };

  const ALERT_SEVERITY_LABELS = {
    high_error_rate: 'High Error Rate',
    high_latency: 'High Latency',
    spoofed_bot_surge: 'Bot Surge',
    high_fallback_rate: 'High Fallback Rate',
    endpoint_down: 'Endpoint Down',
    auto_block_expired: 'Auto-Block Expired',
  };

  const loadNotifPrefs = useCallback(async () => {
    const [prefsResult, statsResult, settingsResult, dispResult, kvResult, r2Result, ciResult, vpResult] = await Promise.allSettled([
      axios.get(`${API_BASE}/admin/notification-prefs`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/push/delivery-stats?days=7`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/alert-settings`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/seo/daily-summary-dispatches?limit=5`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/kv-health`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/r2-storage-health`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/ci-status`, adminHdr(adminToken)),
      axios.get(`${API_BASE}/admin/vertex/probe-status`, adminHdr(adminToken)),
    ]);

    // Process results
    if (prefsResult.status === 'fulfilled') {
      setNotifPrefs(prefsResult.value.data);
    } else {
      log.error('Failed to load notification prefs', { error: prefsResult.reason?.message });
      setNotifPrefs({ sound_enabled: true, push_enabled: false, chime_tone: 'default', sound_severities: ['high_error_rate', 'high_latency', 'spoofed_bot_surge', 'high_fallback_rate', 'endpoint_down', 'auto_block_expired'], push_severities: ['high_error_rate', 'spoofed_bot_surge', 'endpoint_down', 'auto_block_expired'] });
    }
    if (statsResult.status === 'fulfilled') {
      setPushDeliverySummary(statsResult.value.data);
    } else {
      console.warn('AdminDashboard: /admin/push/delivery-stats fetch failed:', statsResult.reason);
    }
    if (settingsResult.status === 'fulfilled') {
      setPushChannelStatus(settingsResult.value.data?.channel_status?.push || null);
    } else {
      setPushChannelStatus(null);
    }
    if (dispResult.status === 'fulfilled') {
      setSeoSummaryDispatches(dispResult.value.data?.dispatches || []);
    } else {
      setSeoSummaryDispatches([]);
    }
    if (kvResult.status === 'fulfilled') {
      setKvHealth(kvResult.value.data || null);
    } else {
      setKvHealth({ configured: false, reason: 'Backend unreachable' });
    }
    if (r2Result.status === 'fulfilled') {
      setR2Health(r2Result.value.data || null);
    } else {
      setR2Health({ configured: false, reason: 'Backend unreachable' });
    }
    if (ciResult.status === 'fulfilled') {
      setCiStatus(ciResult.value.data || null);
    } else {
      setCiStatus({ configured: false, reason: 'Backend unreachable' });
    }
    if (vpResult.status === 'fulfilled') {
      setVertexProbe(vpResult.value.data || null);
    } else {
      setVertexProbe({ status: 'unknown', reason: 'Backend unreachable' });
    }
  }, [adminToken]);

  const saveNotifPrefs = useCallback(async (updates) => {
    const merged = { ...notifPrefs, ...updates };
    setNotifPrefs(merged);
    setNotifPrefsSaving(true);
    try {
      const res = await axios.put(`${API_BASE}/admin/notification-prefs`, merged, adminHdr(adminToken));
      setNotifPrefs(res.data);
    } catch (e) {
      log.error('Failed to save notification prefs', { error: e.message });
    } finally {
      setNotifPrefsSaving(false);
    }
  }, [adminToken, notifPrefs]);

  const toggleAlertSound = useCallback(() => {
    saveNotifPrefs({ sound_enabled: !alertSoundEnabled });
  }, [saveNotifPrefs, alertSoundEnabled]);

  const playAlertChime = useCallback((tone) => {
    try {
      const activeTone = tone || chimeTone;
      if (activeTone === 'custom' && notifPrefs?.custom_chime_url) {
        if (customAudioRef.current) {
          customAudioRef.current.pause();
          customAudioRef.current.currentTime = 0;
        }
        const audio = new Audio(notifPrefs.custom_chime_url);
        audio.volume = 0.5;
        customAudioRef.current = audio;
        audio.play().catch(() => {});
        return;
      }
      if (!audioCtxRef.current) {
        audioCtxRef.current = new (window.AudioContext || window.webkitAudioContext)();
      }
      const ctx = audioCtxRef.current;
      const now = ctx.currentTime;
      const toneConfig = CHIME_TONES[activeTone] || CHIME_TONES.default;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = toneConfig.type;
      const step = toneConfig.dur / toneConfig.freqs.length;
      toneConfig.freqs.forEach((f, i) => osc.frequency.setValueAtTime(f, now + i * step));
      gain.gain.setValueAtTime(0.3, now);
      gain.gain.exponentialRampToValueAtTime(0.01, now + toneConfig.dur);
      osc.start(now);
      osc.stop(now + toneConfig.dur);
    } catch (err) {
      // WebAudio/Oscillator can throw when the AudioContext is
      // suspended (e.g. before the first user gesture) or the tone
      // config is malformed — both are best-effort UX nice-to-haves
      // (the chime preview), so degrade silently with a debug log.
      console.debug('AdminDashboard: chime preview tone failed:', err?.message);
    }
  }, [chimeTone, notifPrefs?.custom_chime_url]);

  const handleChimeFileSelect = useCallback((e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const validTypes = ['audio/mpeg', 'audio/wav', 'audio/wave', 'audio/x-wav', 'audio/mp3'];
    if (!validTypes.includes(file.type)) {
      toast.error('Only MP3 and WAV files are supported');
      if (chimeFileInputRef.current) chimeFileInputRef.current.value = '';
      return;
    }
    if (file.size > 500 * 1024) {
      toast.error('File must be under 500 KB');
      if (chimeFileInputRef.current) chimeFileInputRef.current.value = '';
      return;
    }
    setPendingChimeFile(file);
    if (chimeFileInputRef.current) chimeFileInputRef.current.value = '';
  }, []);

  const handleChimeUploadConfirm = useCallback(async (fileToUpload) => {
    if (fileToUpload.size > 500 * 1024) {
      toast.error('Trimmed file exceeds 500 KB limit');
      return;
    }
    setChimeUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', fileToUpload);
      const res = await axios.post(`${API_BASE}/admin/notification-prefs/upload-chime`, formData, {
        ...adminHdr(adminToken),
        headers: { ...adminHdr(adminToken).headers, 'Content-Type': 'multipart/form-data' },
      });
      setNotifPrefs(res.data);
      setPendingChimeFile(null);
      toast.success('Custom chime uploaded');
    } catch (err) {
      const msg = err.response?.data?.detail || 'Upload failed';
      toast.error(msg);
    } finally {
      setChimeUploading(false);
    }
  }, [adminToken]);

  const handleDeleteCustomChime = useCallback(async () => {
    try {
      const res = await axios.delete(`${API_BASE}/admin/notification-prefs/custom-chime`, adminHdr(adminToken));
      setNotifPrefs(res.data);
      toast.success('Custom chime removed');
    } catch {
      toast.error('Failed to remove custom chime');
    }
  }, [adminToken]);

  useEffect(() => {
    if (!alertHistory?.alerts || !alertSoundEnabled) return;
    const soundSeverities = new Set(notifPrefs?.sound_severities || []);
    const currentUnack = alertHistory.alerts.filter(a => !a.acknowledged);
    const currentIds = new Set(currentUnack.map(a => a._id));
    const prevIds = prevAlertIdsRef.current;
    const newAlerts = currentUnack.filter(a => !prevIds.has(a._id));
    if (newAlerts.length > 0 && prevIds.size > 0) {
      const shouldSound = newAlerts.some(a => soundSeverities.has(a.type));
      if (shouldSound) playAlertChime();
    }
    // Cap prevAlertIdsRef to prevent unbounded memory growth
    if (currentIds.size > 1000) {
      const recent = [...currentIds].slice(-500);
      prevAlertIdsRef.current = new Set(recent);
    } else {
      prevAlertIdsRef.current = currentIds;
    }
  }, [alertHistory, alertSoundEnabled, notifPrefs, playAlertChime]);

  const headers = { withCredentials: true };
  const adminHdr = (token) => {
    const isJwt = token && typeof token === 'string' && token.split('.').length === 3;
    return isJwt ? { headers: { Authorization: `Bearer ${token}` }, withCredentials: true } : { withCredentials: true };
  };

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    try {
      const [
        dashRes, metricsRes,
        ragAccRes, fallbackRes, vectorRes, latencyRes,
        queriesRes, tokenRes, funnelRes, coverageRes, pwaRes, botRes, cfCrawlRes, indexNowRes, indexNowHistRes,
        prewarmRes, alertHistRes, seoHealthRes,
      ] = await Promise.allSettled([
        adminGetDashboard(adminToken),
        axios.get(`${API_BASE}/admin/dashboard/metrics`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/rag/accuracy`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/chat/fallbacks`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/vector/stats`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/perf/latency`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/analytics/queries`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/billing/tokens`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/monetization/funnel`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/content/coverage`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/pwa/stats`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/analytics/bot-traffic?days=30`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/analytics/cf-ai-crawl-control?days=7`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/indexnow/stats`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/indexnow/history?limit=20`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/seo/prewarm-coverage`, adminHdr(adminToken)),
        axios.get(`${API_BASE}/admin/alerts?limit=50${showSyntheticAlerts ? '&include_synthetic=true' : ''}`, adminHdr(adminToken)),
        adminSeoHealthHistory(adminToken, 168),
      ]);
      const failed = [];
      if (dashRes.status === 'fulfilled') setData(dashRes.value.data); else { failed.push('overview'); setData(null); }
      if (metricsRes.status === 'fulfilled') setMetrics(metricsRes.value.data); else { failed.push('metrics'); setMetrics(null); }
      if (ragAccRes.status === 'fulfilled') setRagAccuracy(ragAccRes.value.data); else { failed.push('rag'); setRagAccuracy(null); }
      if (fallbackRes.status === 'fulfilled') setChatFallbacks(normalizeChatFallbacks(fallbackRes.value.data)); else { failed.push('fallbacks'); setChatFallbacks(null); }
      if (vectorRes.status === 'fulfilled') setVectorStats(normalizeVectorStats(vectorRes.value.data)); else { failed.push('vector'); setVectorStats(null); }
      if (latencyRes.status === 'fulfilled') setLatency(normalizeLatency(latencyRes.value.data)); else { failed.push('latency'); setLatency(null); }
      if (queriesRes.status === 'fulfilled') setTopQueries(normalizeTopQueries(queriesRes.value.data)); else { failed.push('queries'); setTopQueries(null); }
      if (tokenRes.status === 'fulfilled') setTokenSpend(normalizeTokenSpend(tokenRes.value.data)); else { failed.push('tokens'); setTokenSpend(null); }
      if (funnelRes.status === 'fulfilled') setFunnel(funnelRes.value.data); else { failed.push('funnel'); setFunnel(null); }
      if (coverageRes.status === 'fulfilled') setCoverage(coverageRes.value.data); else { failed.push('coverage'); setCoverage(null); }
      if (pwaRes.status === 'fulfilled') setPwaStats(pwaRes.value.data); else { failed.push('pwa'); setPwaStats(null); }
      if (botRes.status === 'fulfilled') setBotAnalytics(botRes.value.data); else { failed.push('bot-analytics'); setBotAnalytics(null); }
      if (cfCrawlRes.status === 'fulfilled') setCfCrawlControl(cfCrawlRes.value.data); else { failed.push('cf-ai-crawl-control'); setCfCrawlControl(null); }
      if (indexNowRes.status === 'fulfilled') setIndexNowStats(indexNowRes.value.data); else { failed.push('indexnow'); setIndexNowStats(null); }
      if (indexNowHistRes.status === 'fulfilled') setIndexNowHistory(indexNowHistRes.value.data); else setIndexNowHistory(null);
      if (alertHistRes.status === 'fulfilled') setAlertHistory(alertHistRes.value.data); else { failed.push('alerts'); setAlertHistory(null); }
      if (seoHealthRes.status === 'fulfilled') setSeoHealth(seoHealthRes.value.data); else { failed.push('seo-health'); setSeoHealth(null); }
      if (prewarmRes.status === 'fulfilled') setPrewarmCoverage(prewarmRes.value.data); else { failed.push('prewarm-coverage'); setPrewarmCoverage(null); }
      seoHealthLive()
        .then((r) => { setSeoLive(r.data); setSeoLiveError(null); })
        .catch((e) => { setSeoLive(null); setSeoLiveError(e?.message || 'Failed to load SEO health'); });
      // Task #350: piggy-back on the dashboard refresh — fetch the
      // most recent auto-deep-scan summary per sitemap so each row
      // can show the alert-loop's true blast-radius numbers without
      // the on-call admin having to re-click "Deep scan" per sitemap.
      adminSeoDeepScanHistory(adminToken)
        .then((r) => setSeoAutoDeepScans(r.data || null))
        .catch(() => setSeoAutoDeepScans(null));
      setFailedSections(failed);
      setLastRefresh(new Date());
    } catch (e) {
      log.error('Admin dashboard load failed', { error: e.message, status: e.response?.status });
      setFailedSections(['overview', 'metrics', 'rag', 'fallbacks', 'vector', 'latency', 'queries', 'tokens', 'funnel', 'coverage']);
    }
    finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [adminToken, showSyntheticAlerts]);

  useEffect(() => {
    load();
    loadNotifPrefs();
    const interval = setInterval(() => load(true), 60000);
    return () => clearInterval(interval);
  }, [load, loadNotifPrefs]);

  // Task #398 — 1s tick for the Revenue-section freshness badge.
  // Only runs while the metrics endpoint has actually returned a
  // ``_meta`` block (Task #396), so a failed /admin/dashboard/metrics
  // fetch doesn't leak a useless interval. Depends on a stable
  // boolean rather than the ``_meta`` object reference so the
  // interval isn't torn down and re-created on every successful
  // poll (which arrives as a new ``metrics`` object every 60s).
  const hasMetricsMeta = !!metrics?._meta;
  useEffect(() => {
    if (!hasMetricsMeta) return undefined;
    const id = setInterval(() => {
      setMetricsMetaTick((t) => (t + 1) % 1_000_000);
    }, 1000);
    return () => clearInterval(id);
  }, [hasMetricsMeta]);

  // Task #991 — poll the persistent alert-cooldown active count so the
  // Alert History header can surface a "N on hold" pill whenever the
  // 6h cross-worker cooldown is silencing alerts that would otherwise
  // fire. Cheap call (the route returns just `active_count` plus the
  // first row); we ask for limit=1 because the badge only needs the
  // count, never the body. Failures are swallowed — the badge simply
  // hides, matching the unack-count poll's behaviour in AdminPage.
  useEffect(() => {
    if (!adminToken) return undefined;
    const fetchCount = () => {
      adminGetAlertCooldowns(adminToken, { only_active: true, limit: 1 })
        .then((res) => setCooldownActiveCount(res.data?.active_count ?? 0))
        // On a transient failure, reset to 0 so a stale positive
        // count from an earlier successful poll doesn't keep the
        // badge visible and misleading. The next successful poll
        // will repopulate the real number.
        .catch(() => setCooldownActiveCount(0));
    };
    fetchCount();
    const id = setInterval(fetchCount, 60000);
    return () => clearInterval(id);
  }, [adminToken]);

  // Cloudflare Account Analytics overview — fetch on mount and whenever
  // the user clicks a different range pill on the Traffic card.
  const loadCfOverview = useCallback(async (range) => {
    if (!adminToken) return;
    setCfOverviewLoading(true);
    try {
      const r = await adminGetCfOverview(adminToken, range);
      setCfOverview(r.data || null);
    } catch (e) {
      log.error('Failed to load CF overview', { error: e.message });
      setCfOverview(null);
    } finally {
      setCfOverviewLoading(false);
    }
  }, [adminToken]);

  useEffect(() => {
    loadCfOverview(cfRange);
  }, [cfRange, loadCfOverview]);

  // NOTE: previously kept a separate "locked 30d" CF fetch and used it to
  // drive the Unique Visitors tile on the Traffic card. That intentionally
  // ignored the 24h / 7d / 30d range pills, which (correctly) read as a
  // bug — clicking 24h or 7d left the visitors number frozen at the 30d
  // value while the other three tiles updated. The visitors tile now
  // follows the active `cfRange` like every other tile, so this dedicated
  // fetch was removed.

  // Dedicated 24-hour unique-visitors fetch for the Unique Visitors stat card.
  // Uses the hourly CF dataset (`httpRequests1hGroups`) so the total is the
  // rolling 24-hour unique count and the "last hour" sub-value is the most
  // recent hourly bucket. Refreshes every 5 minutes.
  const [cfVisitors24h, setCfVisitors24h] = useState(null);
  useEffect(() => {
    if (!adminToken) return;
    let cancelled = false;
    const fetch24h = async () => {
      try {
        const r = await adminGetCfOverview(adminToken, '24h');
        if (!cancelled && r?.data?.totals?.visitors != null) {
          setCfVisitors24h(r.data);
        }
      } catch (e) {
        log.warn('CF 24h visitors fetch failed', { error: e.message });
      }
    };
    fetch24h();
    const interval = setInterval(fetch24h, 5 * 60 * 1000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [adminToken]);

  const loadChatSpeedups = useCallback(async (days) => {
    setSpeedupLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/admin/chat/speedups?days=${days}`, adminHdr(adminToken));
      setChatSpeedups(normalizeChatSpeedups(res.data));
    } catch (e) {
      log.error('Failed to load chat speedups', { error: e.message });
      setChatSpeedups(null);
    } finally {
      setSpeedupLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminToken]);

  useEffect(() => {
    loadChatSpeedups(speedupDays);
    const interval = setInterval(() => loadChatSpeedups(speedupDays), 60000);
    return () => clearInterval(interval);
  }, [loadChatSpeedups, speedupDays]);

  // Task #810 — pull the anonymous-quota wall stats produced by Task
  // #798's `chat.anon_quota_exhausted` counter. The endpoint can be
  // called with `?backfill=1` to seed today's chart from existing
  // Redis device-credit counters; we only do that on explicit click
  // so the auto-refresh path stays a cheap memory read.
  // We track fetch errors separately from `anonQuotaWall === null` so
  // the card can show an honest "couldn't load" banner instead of a
  // grid of zeros that would otherwise be indistinguishable from a
  // real "no devices hit the wall today" state.
  const [anonQuotaError, setAnonQuotaError] = useState(null);
  const loadAnonQuotaWall = useCallback(async (days, withBackfill = false) => {
    if (withBackfill) setAnonQuotaBackfilling(true);
    else setAnonQuotaLoading(true);
    try {
      const url = `${API_BASE}/admin/chat/anon-quota-exhausted?days=${days}${withBackfill ? '&backfill=1' : ''}`;
      const res = await axios.get(url, adminHdr(adminToken));
      setAnonQuotaWall(res.data);
      setAnonQuotaError(null);
      if (withBackfill) {
        const n = res.data?.backfilled_today ?? 0;
        toast.success(n > 0 ? `Backfilled ${n} device${n === 1 ? '' : 's'} for today` : 'Already up-to-date — nothing to backfill');
      }
    } catch (e) {
      log.error('Failed to load anon-quota wall stats', { error: e.message, status: e.response?.status });
      // Deliberately do NOT clear `anonQuotaWall` here — keep the
      // last-known-good payload visible behind the error banner so
      // the admin still has stale-but-real numbers to look at while
      // the retry resolves. The banner copy makes the staleness
      // explicit ("numbers below are not real" if there's no prior
      // payload, otherwise the banner reads as a refresh failure).
      setAnonQuotaError(e?.response?.status ? `HTTP ${e.response.status}` : (e?.message || 'Network error'));
      if (withBackfill) toast.error('Backfill failed — see logs');
    } finally {
      setAnonQuotaLoading(false);
      setAnonQuotaBackfilling(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminToken]);

  useEffect(() => {
    loadAnonQuotaWall(anonQuotaDays);
    const interval = setInterval(() => loadAnonQuotaWall(anonQuotaDays), 60000);
    return () => clearInterval(interval);
  }, [loadAnonQuotaWall, anonQuotaDays]);

  // Task #626 — Chat Model config tab deep-links here with
  // { scrollTo: 'chat-speedup-providers' } to land the admin on the
  // per-provider comparison. Wait a tick so the card has mounted
  // (Suspense/lazy can delay paint), then scroll it into view.
  useEffect(() => {
    const target = navContext?.scrollTo;
    if (!target || typeof document === 'undefined') return;
    const t = setTimeout(() => {
      const el = document.getElementById(target);
      if (el && typeof el.scrollIntoView === 'function') {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    }, 250);
    return () => clearTimeout(t);
  }, [navContext]);

  // Task #681 — the review-prompt funnel tile (in OverviewTab) renders
  // a baseline-noise legend whose "Tune sigma multiplier" link expects
  // to land the admin directly on the Reason CTR Sigma Multiplier
  // input. We listen on a window event (decoupled from OverviewTab's
  // props) and pop the Alert Settings panel + scroll-and-focus the
  // sigma input. Hoisted above the `if (loading)` early return below
  // so the hook order stays stable across loading → loaded transitions
  // (otherwise React throws "Rendered more hooks than during the
  // previous render" once `loading` flips to false).
  useEffect(() => {
    const onOpenSigma = () => {
      if (!alertSettings) loadAlertSettings();
      setAlertSettingsOpen(true);
      // Defer to the next paint so the panel is in the DOM before we
      // try to scroll/focus the input.
      setTimeout(() => {
        const el = document.getElementById('alert-reason-ctr-sigma-input');
        if (el) {
          try {
            el.scrollIntoView({ behavior: 'smooth', block: 'center' });
          } catch {
            el.scrollIntoView();
          }
          try { el.focus({ preventScroll: true }); } catch { el.focus(); }
        }
      }, 50);
    };
    window.addEventListener('syrabit:open-alert-sigma-setting', onOpenSigma);
    return () => window.removeEventListener('syrabit:open-alert-sigma-setting', onOpenSigma);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alertSettings]);

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center p-16 gap-3">
        <Loader2 size={24} className="animate-spin text-violet-500" />
        <span className="text-sm text-gray-400">Loading dashboard...</span>
      </div>
    );
  }

  const handleAcknowledgeAlert = async (alertId) => {
    try {
      await axios.patch(`${API_BASE}/admin/alerts/${alertId}/acknowledge`, {}, adminHdr(adminToken));
      setAlertHistory(prev => ({
        ...prev,
        alerts: prev.alerts.map(a => a._id === alertId ? { ...a, acknowledged: true } : a),
      }));
      toast.success('Alert acknowledged');
    } catch (e) {
      log.error('Failed to acknowledge alert', { error: e.message });
      toast.error(`Failed to acknowledge alert: ${e.response?.data?.error || e.message}`);
    }
  };

  const handleAcknowledgeAll = async () => {
    try {
      await axios.patch(`${API_BASE}/admin/alerts/acknowledge-all`, {}, adminHdr(adminToken));
      setAlertHistory(prev => ({
        ...prev,
        alerts: prev.alerts.map(a => ({ ...a, acknowledged: true })),
      }));
      toast.success('All alerts acknowledged');
    } catch (e) {
      log.error('Failed to acknowledge all alerts', { error: e.message });
      toast.error(`Failed to acknowledge alerts: ${e.response?.data?.error || e.message}`);
    }
  };

  const loadAlertSettings = async () => {
    try {
      const res = await axios.get(`${API_BASE}/admin/alert-settings`, adminHdr(adminToken));
      setAlertSettings(res.data);
      setAlertSettingsDraft({ thresholds: { ...res.data.thresholds }, expiration: { ...res.data.expiration } });
    } catch (e) {
      log.error('Failed to load alert settings', { error: e.message });
    }
  };

  const handleSaveAlertSettings = async () => {
    if (!alertSettingsDraft) return;
    setAlertSettingsSaving(true);
    try {
      await axios.put(`${API_BASE}/admin/alert-settings`, alertSettingsDraft, adminHdr(adminToken));
      setAlertSettings({ ...alertSettings, thresholds: { ...alertSettingsDraft.thresholds }, expiration: { ...alertSettingsDraft.expiration } });
      setAlertSettingsOpen(false);
      toast.success('Alert settings saved');
    } catch (e) {
      log.error('Failed to save alert settings', { error: e.message });
      toast.error(`Failed to save alert settings: ${e.response?.data?.error || e.message}`);
    } finally {
      setAlertSettingsSaving(false);
    }
  };

  const handleOpenAlertSettings = () => {
    if (!alertSettings) loadAlertSettings();
    setAlertSettingsOpen(prev => !prev);
  };

  const handleResetAlertSettings = () => {
    if (alertSettings?.defaults) {
      setAlertSettingsDraft({
        thresholds: { ...alertSettings.defaults.thresholds },
        expiration: { ...alertSettings.defaults.expiration },
      });
    }
  };

  const handleRetryEndpoint = async (endpoint) => {
    setRetryingEndpoint(endpoint);
    try {
      const retryRes = await axios.post(`${API_BASE}/admin/indexnow/endpoint/retry`, { endpoint }, adminHdr(adminToken));
      const requeued = Number(retryRes.data?.requeued ?? retryRes.data?.count ?? 0);
      toast.success(`Endpoint reset — ${requeued} URL${requeued === 1 ? '' : 's'} re-queued`);
      try {
        const statsRes = await axios.get(`${API_BASE}/admin/indexnow/stats`, adminHdr(adminToken));
        setIndexNowStats(statsRes.data);
      } catch (statsErr) {
        log.error('Stats refresh failed after retry', { endpoint, error: statsErr.message });
      }
    } catch (e) {
      log.error('Endpoint retry failed', { endpoint, error: e.message });
      toast.error(`Retry failed: ${e.response?.data?.error || e.message}`);
    } finally {
      setRetryingEndpoint(null);
    }
  };

  const handleCiRerun = async (runId, failedOnly = true) => {
    setCiRerunning(runId);
    try {
      await axios.post(
        `${API_BASE}/admin/ci-rerun`,
        { run_id: runId, failed_only: failedOnly },
        adminHdr(adminToken),
      );
      toast.success(`Re-run queued for run #${runId} — refresh in a moment to see it start`);
    } catch (e) {
      const detail = e.response?.data?.detail || e.message;
      toast.error(`Re-run failed: ${detail}`);
    } finally {
      setCiRerunning(null);
    }
  };

  const vs = data?.visitor_stats || {};
  const recentEvents = data?.recent_events || [];
  const deps = metrics?.dependencies || {};

  const ragAlert = failedSections.includes('rag') ? 'yellow' : (ragAccuracy?.alert || 'green');
  const fallbackAlert = failedSections.includes('fallbacks') ? 'yellow' : (chatFallbacks?.alert || 'green');
  const latencyAlert = failedSections.includes('latency') ? 'yellow' : (latency?.alert || 'green');
  const vectorAlert = failedSections.includes('vector') ? 'yellow'
    : (vectorStats?.overall_coverage_pct ?? 100) < 90 ? 'yellow' : 'green';
  // botAlert was used by the legacy "Bot Traffic Analytics" card,
  // which has been replaced by the Cloudflare AI Crawl Control card.
  // botAnalytics is still fetched (other consumers may rely on it),
  // but the alert badge for it is no longer rendered here.

  const hasRagIssue = ragAlert === 'red' || latencyAlert === 'red';

  const quickActions = [
    { id: 'users',     label: 'View Users',     icon: Users,    color: '#7c3aed' },
    { id: 'blog',      label: 'Blog Publisher', icon: PenTool,  color: '#3b82f6' },
    { id: 'analytics', label: 'Analytics',       icon: BarChart2, color: '#10b981' },
    { id: 'monetization', label: 'Monetization', icon: Crown,    color: '#f59e0b' },
  ];

  return (
    <div className="p-4 md:p-6 space-y-5 max-w-[1400px]" role="main" aria-label="Admin Dashboard">

      {failedSections.length > 0 && (
        <div className="flex items-center gap-3 p-3 rounded-xl bg-amber-50 border border-amber-200">
          <AlertTriangle size={14} className="text-amber-500 flex-shrink-0" />
          <p className="text-xs text-amber-700 flex-1">
            Some widgets failed to load ({failedSections.join(', ')}). Metrics may be stale.
          </p>
          <button onClick={() => load(true)} className="text-xs text-amber-700 hover:text-amber-900 px-2.5 py-1 rounded-lg transition-colors bg-amber-100">
            Retry
          </button>
        </div>
      )}

      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-gray-900 font-semibold text-lg tracking-tight">Overview</h2>
          {lastRefresh && (
            <p className="text-gray-400 text-xs mt-0.5">
              Updated {formatTimeAgo(lastRefresh.toISOString())} · auto-refreshes every 60s
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          {metrics?.response_time_ms && (
            <span className="text-xs text-gray-400 flex items-center gap-1">
              <Clock size={10} /> API: {metrics.response_time_ms}ms
            </span>
          )}
          <button
            onClick={() => load(true)}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl text-xs font-medium text-gray-500 hover:text-gray-700 transition-all disabled:opacity-40 bg-white border border-gray-200 shadow-sm"
          >
            <RefreshCw size={12} className={refreshing ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      <SectionErrorBoundary name="System Health">
      {Object.keys(deps).length > 0 && (
        <GlassCard className="p-5">
          <div className="flex items-center gap-2 mb-4">
            <Wifi size={14} className="text-violet-500" />
            <h3 className="text-gray-500 text-sm font-semibold">System Health</h3>
            <div className="ml-auto flex items-center gap-1.5">
              {Object.values(deps).every(d => d.status === 'ok') && !hasRagIssue ? (
                <>
                  <CheckCircle size={12} className="text-emerald-500" />
                  <span className="text-emerald-600 text-xs font-medium">All Systems Operational</span>
                </>
              ) : (
                <>
                  <AlertCircle size={12} className="text-amber-500" />
                  <span className="text-amber-600 text-xs font-medium">
                    {hasRagIssue ? 'RAG/Latency Issue Detected' : 'Degraded'}
                  </span>
                </>
              )}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(deps).map(([name, info]) => (
              <DepStatusCard
                key={name}
                name={name}
                status={info.status}
                latency={info.latency_ms}
              />
            ))}
          </div>
        </GlassCard>
      )}
      </SectionErrorBoundary>

      {data?.conversation_date_range?.oldest && (
        <div className="flex items-center gap-3 p-3 rounded-xl flex-wrap bg-emerald-50 border border-emerald-200">
          <span className="text-xs text-emerald-700 font-bold">Data Recovered</span>
          <span className="text-xs text-gray-500">
            Conversations since <strong className="text-gray-700">{data.conversation_date_range.oldest}</strong>
            {' · '}PG: <strong className="text-blue-600">{data.pg_conversations}</strong>
            {' + '}Supabase: <strong className="text-emerald-600">{data.supa_conversations}</strong>
            {' = '}<strong className="text-gray-700">{data.total_conversations}</strong> total
            {' · '}<strong className="text-gray-700">{data.conversations_with_messages}</strong> with messages
            {' · '}<strong className="text-gray-700">{data.unique_chatters}</strong> unique chatters
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <StatCard label="Total Users"     value={data?.total_users}          icon={Users}         color="#8b5cf6"
          subLabel="Chatted" subValue={data?.unique_chatters ?? 0} />
        <StatCard label="Conversations"   value={data?.total_conversations}  icon={MessageSquare} color="#3b82f6"
          subLabel="With messages" subValue={data?.conversations_with_messages ?? 0} />
        <StatCard label="Messages (All)"  value={data?.total_messages}       icon={Zap}           color="#10b981"
          subLabel="Since" subValue={data?.conversation_date_range?.oldest ?? '—'} />
        <StatCard label="Subjects"        value={data?.total_subjects}       icon={BookOpen}      color="#f59e0b" />
      </div>

      <SectionErrorBoundary name="Revenue">
      {metrics?.revenue && (
        <>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatCard
            label="Revenue (INR)"
            value={'₹' + Math.round(metrics.revenue.total_inr || 0).toLocaleString('en-IN')}
            icon={DollarSign}
            color="#10b981"
            subLabel="MRR"
            subValue={'₹' + Math.round(metrics.revenue.mrr_inr || 0).toLocaleString('en-IN')}
          />
          <StatCard label="Paid Users"      value={metrics.users?.paid || 0}     icon={Crown}  color="#f59e0b" />
          <StatCard label="Free Users"      value={metrics.users?.free || 0}     icon={Users}  color="#64748b" />
          <StatCard label="SEO Pages"       value={metrics.seo?.published_pages || 0} icon={Globe} color="#06b6d4"
            subLabel="Topics" subValue={metrics.seo?.topics || 0}
            onClick={() => onNavigate?.('seomanager')} />
          <StatCard label="Bot Renders"    value={metrics.bot_render?.total_requests || 0} icon={Bot} color="#8b5cf6"
            subLabel="Success Rate" subValue={metrics.bot_render?.success_rate_pct != null ? `${metrics.bot_render.success_rate_pct}%` : '—'} />
        </div>
        {/* Task #398 — freshness badge for the heavy users / revenue / SEO
            block. The numbers above (Revenue, Paid/Free Users, SEO Pages,
            Bot Renders) all read from `metrics`, which the backend caches
            for ~5 s (Task #395). Without this badge an admin staring at
            a revenue figure during an incident has no way to tell whether
            it was just computed or is up to 5 s stale. The 1 s tick
            useEffect above keeps the "Xs ago" label moving between the
            60 s polls so the cache age advances visibly while the panel
            sits idle. Anything beyond ~5 s flips the badge to amber so
            on-call notices when the heavy cache TTL has been blown
            (likely a wedged backend or a stuck refresh). The four heavy
            sections share one ``heavy_cached_at`` timestamp because they
            are computed and cached as a single block server-side. */}
        {metrics?._meta && (() => {
          // Shared formatter + stale boundary — see
          // src/utils/metricsFreshness.js. Centralising here means
          // the AdminHealth strip (Task #396) and this badge always
          // render the same wording for the same `heavy_cached_at`,
          // and a future change to `_METRICS_CACHE_TTL` (Task #395)
          // updates both panels in lockstep instead of drifting.
          const heavyAt = Number(metrics._meta.heavy_cached_at);
          const { label: heavyLabel, stale } = computeHeavyFreshness(heavyAt);
          return (
            <p
              className={`text-[11px] mt-2 px-1 ${stale ? 'text-amber-600 font-medium' : 'text-gray-400'}`}
              data-testid="dashboard-metrics-freshness"
              title={`heavy_cached_at=${heavyAt} (Task #396)`}
            >
              Last updated{' '}
              <span data-testid="dashboard-metrics-freshness-age">{heavyLabel}</span>
              {stale && (
                <span className="ml-1" data-testid="dashboard-metrics-freshness-stale">
                  — cache TTL (~5s) exceeded
                </span>
              )}
            </p>
          );
        })()}
        <p className="text-[11px] text-gray-400 mt-2 px-1">
          Revenue includes Razorpay (INR) + Stripe (USD→INR via daily ECB rate). All values stored as <code>amount_inr</code> on each payment row.
        </p>
        </>
      )}
      </SectionErrorBoundary>
      <SectionErrorBoundary name="Bot Render">
      {metrics?.bot_render?.by_page_type && Object.keys(metrics.bot_render.by_page_type).length > 0 && (() => {
        const raw = metrics.bot_render.by_page_type;
        const grouped = {};
        Object.entries(raw).forEach(([key, count]) => {
          const [type, status] = key.split(':');
          if (!grouped[type]) grouped[type] = { ok: 0, fail: 0 };
          if (status === 'ok') grouped[type].ok = count;
          else grouped[type].fail = count;
        });
        return (
        <div className="mt-4 bg-white border border-gray-200 rounded-2xl p-5 shadow-sm">
          <h3 className="text-sm font-semibold text-gray-700 mb-3 flex items-center gap-2"><Bot size={14} className="text-violet-500" /> Bot Render by Page Type</h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
            {Object.entries(grouped).map(([type, counts]) => (
              <div key={type} className="bg-gray-50 rounded-xl px-3 py-2 border border-gray-100">
                <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">{type.replace(/_/g, ' ')}</p>
                <p className="text-base font-bold font-mono text-gray-800">{counts.ok + counts.fail}</p>
                <p className="text-[10px] text-gray-400">{counts.ok} ok / {counts.fail} fail</p>
              </div>
            ))}
          </div>
        </div>
        );
      })()}
      </SectionErrorBoundary>

      <SectionErrorBoundary name="AI Health">
      <GlassCard className="p-5">
        <div className="flex items-center gap-2 mb-4 flex-wrap">
          <Globe size={14} style={{ color: '#0891b2' }} />
          <span className="text-xs font-bold text-cyan-700">Traffic (Cloudflare)</span>
          <a
            href="https://dash.cloudflare.com/?to=/:account/analytics"
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-cyan-600 hover:text-cyan-800 underline-offset-2 hover:underline"
          >
            Account analytics documentation
          </a>
          <span className="ml-auto text-[10px] text-gray-500">
            All sites for account · {cfOverview?.period_label || `Previous ${vs.cloudflare?.period_days ?? 7} days`}
          </span>
        </div>

        {/* Time-range selector — mirrors Cloudflare Account Analytics */}
        <div className="flex items-center gap-1 mb-3">
          {[
            { key: '24h', label: 'Previous 24 hours' },
            { key: '7d',  label: 'Previous 7 days' },
            { key: '30d', label: 'Previous 30 days' },
          ].map(opt => {
            const active = cfRange === opt.key;
            return (
              <button
                key={opt.key}
                type="button"
                onClick={() => setCfRange(opt.key)}
                disabled={cfOverviewLoading && active}
                className={`px-2.5 py-1 rounded-full text-[10px] font-semibold border transition-colors ${
                  active
                    ? 'bg-cyan-600 text-white border-cyan-600'
                    : 'bg-white text-gray-600 border-gray-200 hover:border-cyan-300 hover:text-cyan-700'
                }`}
                title={opt.label}
              >
                {opt.key.toUpperCase()}
              </button>
            );
          })}
          {cfOverviewLoading && (
            <span className="ml-2 text-[10px] text-gray-400">Loading…</span>
          )}
        </div>

        {data?.cf_connected === false && (
          <CloudflareAnalyticsBanner
            adminToken={adminToken}
            onRecheck={() => load(true)}
            className="mb-3"
          />
        )}

        {(() => {
          // Prefer the range-aware overview when loaded; fall back to the
          // dashboard payload (vs.cloudflare) for the very first paint so
          // the card never flashes empty on mount.
          const cf = vs.cloudflare || {};
          const useOverview = !!(cfOverview && cfOverview.connected !== false && cfOverview.totals);
          const totals = useOverview ? cfOverview.totals : {
            requests: cf.total_requests,
            bytes: cf.total_bytes,
            visitors: cf.total_visitors,
            page_views: cf.total_page_views,
          };
          const series = useOverview
            ? (cfOverview.series || [])
            : (Array.isArray(cf.daily_visitors) ? cf.daily_visitors : []);
          const lastBucket = useOverview && series.length ? series[series.length - 1] : null;
          const lastBucketLabel = useOverview
            ? (cfOverview.bucket === 'hour' ? 'Last hour' : 'Last day')
            : 'Today';
          const fmtBytes = (n) => {
            n = Number(n) || 0;
            if (n < 1024) return `${n} B`;
            if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
            if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
            if (n < 1024 ** 4) return `${(n / 1024 ** 3).toFixed(2)} GB`;
            return `${(n / 1024 ** 4).toFixed(2)} TB`;
          };
          const fmtNum = (n) => {
            n = Number(n) || 0;
            if (n < 1000) return String(n);
            if (n < 1e6) return `${(n / 1000).toFixed(2).replace(/\.?0+$/, '')}k`;
            if (n < 1e9) return `${(n / 1e6).toFixed(2).replace(/\.?0+$/, '')}M`;
            return `${(n / 1e9).toFixed(2)}B`;
          };
          // "Unique Visitors" — sourced from Cloudflare's `uniq.uniques`
          // (sum across the active range's daily/hourly buckets). Now
          // follows the active `cfRange` pill like every other tile so
          // clicking 24h / 7d / 30d actually updates the number.
          const visitorsToday = useOverview ? (lastBucket?.visitors ?? lastBucket?.uniques) : cf.visitors_today;
          const tiles = [
            { key: 'requests',   label: 'Interactions',     total: totals.requests,       today: useOverview ? lastBucket?.requests   : cf.requests_today,   fmt: fmtNum },
            { key: 'bytes',      label: 'Bandwidth',        total: totals.bytes,          today: useOverview ? lastBucket?.bytes      : cf.bytes_today,      fmt: fmtBytes },
            { key: 'visitors',   label: 'Unique Visitors',  total: totals.visitors,       today: visitorsToday,                                              fmt: fmtNum },
            { key: 'page_views', label: 'Page views',       total: totals.page_views,     today: useOverview ? lastBucket?.page_views : cf.page_views_today, fmt: fmtNum },
          ];
          const hasData = (useOverview ? series.length > 0 : (vs.cloudflare && series.length > 0));
          return (
            <>
            <p className="text-[10px] text-gray-400 mb-2" title="Daily 'Today' buckets reset at UTC midnight (5:30 AM IST). In early IST morning the bucket only covers a few hours.">{TODAY_BUCKET_CAPTION}</p>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-3">
              {tiles.map(t => (
                <div key={t.key} className="rounded-xl p-3 bg-white border border-gray-200">
                  <p className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">{t.label}</p>
                  <p className="text-gray-900 font-bold text-2xl leading-none">
                    {hasData && t.total != null ? t.fmt(t.total) : '—'}
                  </p>
                  <p className="text-[10px] text-gray-400 mt-1">
                    {lastBucketLabel}: {hasData && t.today != null ? t.fmt(t.today) : '—'}
                  </p>
                  <div className="h-10 mt-2 -mx-1">
                    {hasData && (
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={series} margin={{ top: 2, right: 2, left: 2, bottom: 0 }}>
                          <defs>
                            <linearGradient id={`cf-spark-${t.key}`} x1="0" y1="0" x2="0" y2="1">
                              <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.35} />
                              <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
                            </linearGradient>
                          </defs>
                          <Area
                            type="monotone"
                            dataKey={t.key}
                            stroke="#3b82f6"
                            strokeWidth={1.5}
                            fill={`url(#cf-spark-${t.key})`}
                            isAnimationActive={false}
                          />
                          <Tooltip
                            cursor={{ stroke: '#94a3b8', strokeWidth: 1 }}
                            formatter={(v) => [t.fmt(v), t.label]}
                            labelFormatter={(_, p) => p?.[0]?.payload?.date || ''}
                            contentStyle={{ fontSize: '11px', padding: '4px 6px', borderRadius: '6px' }}
                          />
                        </AreaChart>
                      </ResponsiveContainer>
                    )}
                  </div>
                </div>
              ))}
            </div>
            </>
          );
        })()}

        {vs.bot_traffic && (
          <div className="rounded-xl p-3 bg-amber-50 border border-amber-200 mb-3">
            <div className="flex items-center gap-1.5 mb-2">
              <Bot size={11} style={{ color: '#f59e0b' }} />
              <span className="text-[10px] font-bold text-amber-700 uppercase tracking-wider">Bot/Crawler Traffic (excluded above)</span>
              <span className="text-[9px] text-gray-400 ml-auto">separate</span>
            </div>
            <div className="flex gap-6 flex-wrap">
              <div>
                <p className="text-gray-900 font-bold text-lg">{(vs.bot_traffic?.unique_total ?? 0).toLocaleString()}</p>
                <p className="text-[10px] text-gray-400">Unique bots</p>
              </div>
              <div>
                <p className="text-gray-900 font-bold text-lg">{(vs.bot_traffic?.hits_today ?? 0).toLocaleString()}</p>
                <p className="text-[10px] text-gray-400">Today</p>
              </div>
              <div>
                <p className="text-gray-500 font-bold text-lg">{(vs.bot_traffic?.total_hits ?? 0).toLocaleString()}</p>
                <p className="text-[10px] text-gray-400">Total</p>
              </div>
            </div>
          </div>
        )}

        {vs.bot_traffic?.top_bots?.length > 0 && (
          <div className="mt-3">
            <div className="text-[10px] text-gray-400 font-semibold mb-1.5 uppercase tracking-wider">Top Crawlers</div>
            <div className="flex flex-wrap gap-1.5">
              {vs.bot_traffic.top_bots.slice(0, 8).map((b, i) => (
                <span key={i} className="text-[10px] px-2 py-0.5 rounded-md text-amber-700 bg-amber-50 border border-amber-200">
                  {b.bot}: {b.hits}
                </span>
              ))}
            </div>
          </div>
        )}
      </GlassCard>
      </SectionErrorBoundary>

      {/* Site-level page-view metrics row — moved here at the user's
          request so the at-a-glance traffic numbers sit directly
          under the Cloudflare account-wide traffic card and above
          the verified-bot Crawl Control card. The block was
          originally rendered much further down (above the Chat
          Health card); the move keeps all traffic-shaped cards
          adjacent so the dashboard tells one continuous story:
          account traffic → site totals → bot share. */}
      <div>
        <p className="text-[10px] text-gray-400 mb-2">{TODAY_BUCKET_CAPTION}</p>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <StatCard label="Page Views Today" value={vs.page_views_today ?? 0} icon={Eye}      color="#ec4899" pulse />
          {/* "Unique Visitors" — always shows the rolling 24-hour unique
              visitor count fetched from CF's hourly dataset. The sub-line
              shows the most recent hourly bucket. Falls back to
              `vs.visitors_today` (daily bucket) before the 24h fetch lands. */}
          {(() => {
            const lastHourBucket = cfVisitors24h?.series?.length
              ? cfVisitors24h.series[cfVisitors24h.series.length - 1]
              : null;
            const headline = cfVisitors24h?.totals?.visitors ?? vs?.visitors_today ?? 0;
            const lastHourValue = lastHourBucket?.visitors ?? lastHourBucket?.uniques ?? 0;
            return (
              <StatCard label="Unique Visitors"
                value={headline}
                icon={Users} color="#84cc16"
                subLabel="Last hour"
                subValue={lastHourValue} />
            );
          })()}
          <StatCard label="Bounce Rate"  value={vs.bounce_rate != null ? `${vs.bounce_rate}%` : '—'} icon={TrendingUp} color="#f59e0b" />
          <StatCard label="Avg Session"  value={vs.avg_session_duration != null ? `${vs.avg_session_duration}s` : '—'} icon={Clock} color="#a78bfa" />
        </div>
      </div>

      {/* Legacy "Bot Traffic Analytics" card removed — its content
          (bot vs human totals, top bots, per-bot pages) was sourced
          from local server logs and duplicated what the
          authoritative Cloudflare AI Crawl Control card below now
          shows. The CF card uses verified-bot data straight from
          Cloudflare's GraphQL feed (the same dataset CF's own
          dashboard reads), so it's the canonical source of truth.
          The `botAnalytics` API endpoint is intentionally still
          fetched in case other components or alerts depend on it. */}

      <SectionErrorBoundary name="CF AI Crawl Control">
      {cfCrawlControl && (
        <GlassCard className="p-5">
          <div className="flex items-center gap-2 mb-4">
            <ShieldCheck size={16} className="text-orange-500" />
            <h3 className="text-gray-700 font-semibold">Cloudflare Search Crawler Activity</h3>
            <div className="ml-auto flex items-center gap-2">
              {cfCrawlControl.available ? (
                <span className="text-[10px] text-gray-400">{cfCrawlControl.period_days}-day window</span>
              ) : (
                <span className="text-[10px] px-2 py-0.5 rounded-md text-amber-700 bg-amber-50 border border-amber-200">
                  CF analytics unavailable
                </span>
              )}
            </div>
          </div>

          {!cfCrawlControl.available && (
            <div className="rounded-lg p-3 bg-amber-50 border border-amber-200 text-xs text-amber-800 flex items-start gap-2">
              <AlertTriangle size={13} className="mt-0.5 flex-shrink-0" />
              <span>{cfCrawlControl.reason || 'Cloudflare GraphQL API did not return verified-bot data.'}</span>
            </div>
          )}

          {cfCrawlControl.available && (
            <>
              {/* ─── METRICS section (top of CF "AI Crawl Control →
                  Overview"). Four headline stats stacked above a
                  full-width sparkline showing daily request volume.
                  Stats use the verified-bot totals from CF GraphQL;
                  the sparkline sums all per-bot daily counts so the
                  shape matches what CF's own dashboard renders. ─── */}
              <div className="rounded-xl border border-gray-200 bg-white px-4 pt-4 pb-2 mb-4">
                <div className="text-[11px] text-gray-500 font-medium mb-3">Metrics</div>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-3">
                  <div>
                    <p className="text-[11px] text-gray-500 underline decoration-dotted decoration-gray-300 underline-offset-4 mb-1">
                      Total requests
                    </p>
                    <p className="text-gray-800 font-bold text-xl tabular-nums">
                      {formatCompactInt(cfCrawlControl.totals?.requests ?? 0)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[11px] text-gray-500 underline decoration-dotted decoration-gray-300 underline-offset-4 mb-1">
                      Allowed requests
                    </p>
                    <p className="text-gray-800 font-bold text-xl tabular-nums">
                      {formatCompactInt(cfCrawlControl.allowed_total ?? 0)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[11px] text-gray-500 underline decoration-dotted decoration-gray-300 underline-offset-4 mb-1">
                      Unsuccessful requests
                    </p>
                    <p className="text-gray-800 font-bold text-xl tabular-nums">
                      {formatCompactInt(cfCrawlControl.unsuccessful_total ?? 0)}
                    </p>
                  </div>
                  <div>
                    <p className="text-[11px] text-gray-500 underline decoration-dotted decoration-gray-300 underline-offset-4 mb-1">
                      Total referrals
                    </p>
                    <p className="text-gray-800 font-bold text-xl tabular-nums">
                      {formatCompactInt(cfCrawlControl.total_referrals ?? 0)}
                    </p>
                  </div>
                </div>
                {/* Sparkline of total daily requests — sums every per-bot
                    column in daily_series.rows so the line matches CF's
                    "requests over time" sparkline on the Overview tab.
                    Filtered to last 30 days max for a clean shape. */}
                {cfCrawlControl.daily_series?.rows?.length > 0 && (() => {
                  const rows = cfCrawlControl.daily_series.rows.slice(-30).map(row => {
                    const total = Object.entries(row).reduce((acc, [k, v]) => (
                      k === 'date' ? acc : acc + (Number(v) || 0)
                    ), 0);
                    return { date: row.date, total };
                  });
                  return (
                    <div style={{ width: '100%', height: 56 }}>
                      <ResponsiveContainer>
                        <AreaChart data={rows} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
                          <defs>
                            <linearGradient id="cfMetricsSparkFill" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.35} />
                              <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
                            </linearGradient>
                          </defs>
                          <Tooltip
                            contentStyle={{ fontSize: 11, padding: '4px 8px' }}
                            labelFormatter={v => v}
                            formatter={(val) => [Number(val).toLocaleString(), 'Requests']}
                          />
                          <Area
                            type="monotone"
                            dataKey="total"
                            stroke="#3b82f6"
                            strokeWidth={1.4}
                            fill="url(#cfMetricsSparkFill)"
                            dot={false}
                            isAnimationActive={false}
                          />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  );
                })()}
              </div>

              {/* ─── CRAWLERS section. Grid of operator tiles, one per
                  company (Google, Microsoft, Apple, OpenAI, etc.). Each
                  tile shows: operator name, contributing-bot pills, and
                  a two-column footer with "Allowed requests" + "Total
                  referrals" — same layout as CF's own Overview tab. The
                  unified ``crawlers_grid`` is built server-side from the
                  full unfiltered per_bot list, so AI operators (whose
                  bots get 403'd at our edge) still appear in the grid
                  for visibility — just like they do in CF's UI. ─── */}
              {cfCrawlControl.crawlers_grid?.length > 0 && (
                <div className="rounded-xl border border-gray-200 bg-white px-4 pt-4 pb-4">
                  <div className="text-[11px] text-gray-500 font-medium mb-3">Crawlers</div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                    {cfCrawlControl.crawlers_grid.map((op) => {
                      const headBots = (op.bots || []).slice(0, 1);
                      const extraBots = Math.max(0, (op.bots?.length || 0) - 1);
                      return (
                        <div
                          key={op.operator}
                          className="rounded-lg border border-gray-200 bg-white p-3 flex flex-col gap-2 hover:shadow-sm transition-shadow"
                        >
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-gray-800 font-semibold text-sm" title={op.operator}>
                              {op.operator}
                            </span>
                            {headBots.map(b => (
                              <span
                                key={b.name}
                                className="text-[10px] px-2 py-0.5 rounded bg-gray-100 text-gray-600 truncate max-w-[110px]"
                                title={b.name}
                              >
                                {b.name}
                              </span>
                            ))}
                            {extraBots > 0 && (
                              <span
                                className="text-[10px] px-2 py-0.5 rounded bg-gray-100 text-gray-600"
                                title={(op.bots || []).slice(1).map(b => `${b.name}: ${(b.requests ?? 0).toLocaleString()}`).join('\n')}
                              >
                                +{extraBots}
                              </span>
                            )}
                          </div>
                          <div className="grid grid-cols-2 gap-3 mt-1">
                            <div>
                              <p className="text-[11px] text-gray-500 mb-0.5">Allowed requests</p>
                              <p className="text-gray-800 font-bold text-base tabular-nums">
                                {formatCompactInt(op.allowed ?? 0)}
                              </p>
                            </div>
                            <div>
                              <p className="text-[11px] text-gray-500 mb-0.5">Total referrals</p>
                              <p className="text-gray-800 font-bold text-base tabular-nums">
                                {formatCompactInt(op.referrals ?? 0)}
                              </p>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="text-[10px] text-gray-400 mt-3">
                    Source: Cloudflare GraphQL{cfCrawlControl.zone_id ? ` · zone ${cfCrawlControl.zone_id.slice(0, 8)}…` : ''} · same dataset as Cloudflare's AI Crawl Control dashboard · referrals sourced from page-view Referer headers (distinct visitors per operator)
                  </div>
                </div>
              )}

              {cfCrawlControl.crawlers_grid?.length === 0 && (
                <div className="mt-4 rounded-lg p-3 bg-gray-50 border border-gray-200 text-xs text-gray-600 text-center">
                  No verified-bot traffic in the last {cfCrawlControl.period_days} days.
                </div>
              )}
            </>
          )}
        </GlassCard>
      )}
      </SectionErrorBoundary>

      <SectionErrorBoundary name="SEO Health Banner">
      {seoHealth?.banner && (
        <div
          className={`rounded-xl border-2 p-4 flex items-start gap-3 ${
            seoHealth.banner.severity === 'critical'
              ? 'bg-red-50 border-red-300 text-red-800'
              : 'bg-amber-50 border-amber-300 text-amber-800'
          }`}
          role="alert"
        >
          <AlertTriangle size={20} className={seoHealth.banner.severity === 'critical' ? 'text-red-600' : 'text-amber-600'} />
          <div className="flex-1 min-w-0">
            <div className="font-semibold">
              SEO health is {seoHealth.banner.severity.toUpperCase()}
              {seoHealth.banner.consecutive >= 2 && (
                <span className="ml-2 text-xs font-normal opacity-80">
                  ({seoHealth.banner.consecutive} consecutive checks · alert email sent)
                </span>
              )}
            </div>
            <div className="text-xs mt-1 opacity-90">
              Sitemaps valid: {seoHealth.banner.summary?.valid_sitemaps ?? 0}/{seoHealth.banner.summary?.total_sitemaps ?? 0}
              {' · '}URL spot-checks OK: {seoHealth.banner.summary?.ok_url_checks ?? 0}/{seoHealth.banner.summary?.total_url_checks ?? 0}
              {' ('}{seoHealth.banner.summary?.url_check_success_rate ?? 0}%{')'}
              {seoHealth.banner.checked_at && ` · last checked ${new Date(seoHealth.banner.checked_at).toLocaleTimeString()}`}
            </div>
          </div>
          <button
            onClick={async () => {
              setSeoHealthRefreshing(true);
              try {
                await adminSeoHealthSnapshotNow(adminToken);
                const r = await adminSeoHealthHistory(adminToken, 168);
                setSeoHealth(r.data);
                toast.success('SEO health re-checked');
              } catch (e) {
                toast.error('Re-check failed');
              } finally {
                setSeoHealthRefreshing(false);
              }
            }}
            disabled={seoHealthRefreshing}
            className="text-xs px-3 py-1.5 rounded-md bg-white border border-current hover:bg-opacity-80 font-medium disabled:opacity-50"
          >
            {seoHealthRefreshing ? 'Checking…' : 'Re-check now'}
          </button>
        </div>
      )}
      </SectionErrorBoundary>

      <SectionErrorBoundary name="SEO Health History">
      {seoHealth?.history && seoHealth.history.length > 0 && (
        <GlassCard className="p-5">
          <div className="flex items-center gap-2 mb-3 flex-wrap">
            <Globe size={16} className="text-cyan-500" />
            <h3 className="text-gray-700 font-semibold">SEO Health Trend</h3>
            <span className="text-[10px] text-gray-500">
              last {seoHealth.history.length} hourly snapshots
            </span>
            <div className="ml-auto flex items-center gap-3 text-[10px] text-gray-500">
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-emerald-500" /> ok</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-amber-500" /> degraded</span>
              <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-red-500" /> critical</span>
            </div>
          </div>
          <div className="flex flex-wrap gap-[3px]">
            {seoHealth.history.map((h, i) => {
              const s = (h.status || '').toLowerCase();
              const cls = s === 'ok'
                ? 'bg-emerald-500'
                : s === 'degraded'
                ? 'bg-amber-500'
                : s === 'critical'
                ? 'bg-red-500'
                : 'bg-gray-300';
              const when = h.checked_at || h.recorded_at;
              return (
                <div
                  key={i}
                  className={`w-2.5 h-6 rounded-sm ${cls}`}
                  title={`${s.toUpperCase()} · ${when ? new Date(when).toLocaleString() : ''} · ${h.summary?.valid_sitemaps ?? 0}/${h.summary?.total_sitemaps ?? 0} sitemaps`}
                />
              );
            })}
          </div>
          {seoHealth.latest && (
            <div className="text-[11px] text-gray-500 mt-3">
              Latest: <span className="font-semibold text-gray-700">{(seoHealth.latest.status || 'unknown').toUpperCase()}</span>
              {' · '}{seoHealth.latest.summary?.valid_sitemaps ?? 0}/{seoHealth.latest.summary?.total_sitemaps ?? 0} sitemaps valid
              {' · '}{seoHealth.latest.summary?.url_check_success_rate ?? 0}% URL checks OK
              {seoHealth.latest.checked_at && ` · ${new Date(seoHealth.latest.checked_at).toLocaleString()}`}
            </div>
          )}
        </GlassCard>
      )}
      </SectionErrorBoundary>

      {/* Task #350: on-call banner — only when the alert loop has
          auto-deep-scanned at least one sitemap in the last hour, so
          the on-call admin sees right away that there's a fresh blast
          radius to triage when they open the dashboard from the alert
          email. */}
      <SectionErrorBoundary name="SEO Auto Deep Scans">
      {seoAutoDeepScans?.recent_within_hour?.length > 0 && (
        <GlassCard
          className="p-4 border-l-4 border-red-500 bg-red-50/50"
          data-testid="seo-auto-deep-scan-banner"
        >
          <div className="flex items-start gap-3">
            <AlertTriangle size={18} className="text-red-600 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-red-900">
                On-call deep scan: {seoAutoDeepScans.recent_within_hour.length} sitemap
                {seoAutoDeepScans.recent_within_hour.length === 1 ? '' : 's'} auto-scanned in the last hour
              </p>
              <p className="text-xs text-red-700 mt-1">
                The alert loop deep-scanned{' '}
                <span className="font-mono">
                  {seoAutoDeepScans.recent_within_hour.join(', ')}
                </span>{' '}
                after a URL spike fired. Per-sitemap totals appear inline below — no need to re-click "Show all".
              </p>
            </div>
            {seoAutoDeepScans.latest_fired_at && (
              <span className="text-[10px] text-red-700 font-mono flex-shrink-0">
                {formatTimeAgo(seoAutoDeepScans.latest_fired_at)}
              </span>
            )}
          </div>
        </GlassCard>
      )}
      </SectionErrorBoundary>

      <SectionErrorBoundary name="SEO Sitemap Health">
      <GlassCard className="p-5" data-testid="seo-sitemap-health-card">
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <FileCheck size={16} className="text-cyan-500" />
          <h3 className="text-gray-700 font-semibold">SEO Sitemap Health</h3>
          {seoLive?.status && (
            <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold uppercase tracking-wider ${
              seoLive.status === 'ok' ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' :
              seoLive.status === 'degraded' ? 'bg-amber-50 text-amber-700 border border-amber-200' :
              seoLive.status === 'critical' ? 'bg-red-50 text-red-700 border border-red-200' :
              'bg-gray-50 text-gray-500 border border-gray-200'
            }`} data-testid="seo-live-status">
              {seoLive.status}
            </span>
          )}
          {seoLive?.checked_at && (
            <span className="text-[10px] text-gray-400">
              checked {formatTimeAgo(seoLive.checked_at)}
            </span>
          )}
          <button
            onClick={async () => {
              setSeoLiveLoading(true);
              setSeoLiveError(null);
              try {
                const r = await seoHealthLive();
                setSeoLive(r.data);
              } catch (e) {
                setSeoLiveError(e?.message || 'Failed');
              } finally {
                setSeoLiveLoading(false);
              }
            }}
            disabled={seoLiveLoading}
            className="ml-auto text-[11px] px-3 py-1 rounded-md border border-gray-200 text-gray-500 hover:text-gray-700 hover:bg-gray-50 disabled:opacity-50 inline-flex items-center gap-1"
            data-testid="seo-live-refresh"
          >
            <RefreshCw size={11} className={seoLiveLoading ? 'animate-spin' : ''} />
            {seoLiveLoading ? 'Probing…' : 'Probe now'}
          </button>
        </div>

        {seoLiveError && !seoLive && (
          <div className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-md px-3 py-2">
            {seoLiveError}
          </div>
        )}

        {!seoLive && !seoLiveError && (
          <div className="flex items-center gap-2 text-xs text-gray-400 py-3">
            <Loader2 size={14} className="animate-spin" /> Loading sitemap probes…
          </div>
        )}

        {seoLive && (
          <>
            {seoLive.summary && (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
                <div className="rounded-lg bg-gray-50 border border-gray-100 px-3 py-2">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400">Sitemaps Valid</p>
                  <p className="text-sm font-bold font-mono text-gray-800">
                    {seoLive.summary.valid_sitemaps ?? 0}/{seoLive.summary.total_sitemaps ?? 0}
                  </p>
                </div>
                <div className="rounded-lg bg-gray-50 border border-gray-100 px-3 py-2">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400">URL Checks OK</p>
                  <p className="text-sm font-bold font-mono text-gray-800">
                    {seoLive.summary.ok_url_checks ?? 0}/{seoLive.summary.total_url_checks ?? 0}
                  </p>
                </div>
                <div className="rounded-lg bg-gray-50 border border-gray-100 px-3 py-2">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400">Success Rate</p>
                  <p className="text-sm font-bold font-mono text-gray-800">
                    {seoLive.summary.url_check_success_rate ?? 0}%
                  </p>
                </div>
                <div className="rounded-lg bg-gray-50 border border-gray-100 px-3 py-2">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400">Published Pages</p>
                  <p className="text-sm font-bold font-mono text-gray-800">
                    {(seoLive.content_stats?.published_pages ?? 0).toLocaleString()}
                  </p>
                </div>
              </div>
            )}

            <div className="space-y-1.5">
              {(() => {
                // Task #352: pull the most recent SEO spike alert's
                // deep_scan_summaries so we can flag the sitemaps that
                // the alert loop intentionally skipped (alert_scan_cap)
                // and tell the on-call admin "manual scan needed".
                const recentSpike = (alertHistory?.alerts || [])
                  .find(a => a.type === 'seo_url_spike'
                    && a.threshold_snapshot?.deep_scan_summaries);
                const alertSkippedSitemaps = new Set();
                const alertCap = recentSpike?.threshold_snapshot
                  ?.deep_scan_summaries
                  ? Object.entries(
                      recentSpike.threshold_snapshot.deep_scan_summaries
                    )
                      .filter(([, v]) => v?.skipped
                        && v?.reason === 'alert_scan_cap')
                      .map(([k, v]) => {
                        alertSkippedSitemaps.add(k);
                        return v?.cap;
                      })[0]
                  : 0;
                return (seoLive.sitemaps || []).map((sm) => {
                // Task #352: this sitemap was deferred by the alert
                // loop because too many sitemaps were failing at once.
                // Once a deep scan has completed for it (manually or
                // otherwise), we hide the badge again.
                const isAlertSkipped = alertSkippedSitemaps.has(sm.name)
                  && !sitemapDeepScans[sm.name]?.data;
                const checks = sm.sample_checks || [];
                const okCount = checks.filter((c) => c.ok).length;
                const totalCount = checks.length;
                const allOk = sm.valid_xml && (totalCount === 0 || okCount === totalCount);
                const partial = sm.valid_xml && totalCount > 0 && okCount > 0 && okCount < totalCount;
                const broken = !sm.valid_xml || (totalCount > 0 && okCount === 0);
                const dotCls = allOk ? 'bg-emerald-500' : partial ? 'bg-amber-500' : broken ? 'bg-red-500' : 'bg-gray-300';
                // Task #298: surface the raw sample probe results inline so
                // admins can see the exact URL, HTTP status, and error for
                // every sampled URL without re-running the probe.
                const sampleRows = checks.filter((c) => c.url).slice(0, 25);
                const failingCount = checks.filter((c) => !c.ok).length;
                const isExpanded = expandedSitemap === sm.name;
                // Task #345: deep-scan results (when present) replace the
                // sample-based view. After a deep scan we know the EXACT
                // failing count; before, we can only guess from the live
                // probe's 10-URL sample.
                const deepScan = sitemapDeepScans[sm.name];
                const usingDeepScan = !!deepScan?.data;
                // Task #350: auto-deep-scan summary harvested from
                // db.alerts. Only show when no manual deep scan has
                // been loaded for this sitemap, since manual scans
                // are authoritative and freshly probed on demand.
                const autoScan = !usingDeepScan
                  ? (seoAutoDeepScans?.by_sitemap?.[sm.name] || null)
                  : null;
                // In deep-scan mode the failing list is authoritative.
                // Otherwise we render the raw sample probes as rows
                // (Task #298), which include both ok and failing results.
                const deepScanFailing = usingDeepScan ? (deepScan.data.failing || []) : [];
                // Show the "Show all failing URLs" control whenever the
                // sitemap could plausibly have more than 10 broken pages.
                // The /seo/health endpoint only probes a 10-URL random
                // sample per sitemap, so as soon as we see ANY failures
                // and the sitemap has more URLs than we sampled, the true
                // failing count is unknown and may exceed 10.
                const mayHaveMoreFailures =
                  failingCount > 0
                  && (sm.url_count ?? 0) > checks.length;
                const canExpand = sampleRows.length > 0 || usingDeepScan || mayHaveMoreFailures;
                return (
                  <div
                    key={sm.name}
                    className="rounded-lg border border-gray-100 bg-white hover:bg-gray-50"
                    data-testid={`seo-sitemap-${sm.name}`}
                  >
                    <button
                      type="button"
                      onClick={() => canExpand && setExpandedSitemap(isExpanded ? null : sm.name)}
                      disabled={!canExpand}
                      className={`w-full flex items-center gap-3 px-3 py-2 text-left ${canExpand ? 'cursor-pointer' : 'cursor-default'}`}
                    >
                      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${dotCls}`} />
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-mono text-gray-700 truncate">{sm.name}</p>
                        {sm.error && (
                          <p className="text-[10px] text-red-600 truncate" title={sm.error}>
                            {sm.error}
                          </p>
                        )}
                        {/* Task #352: badge for sitemaps the alert loop
                            skipped because the per-firing cap was hit. */}
                        {isAlertSkipped && (
                          <p
                            className="text-[10px] text-amber-800 truncate"
                            data-testid={`seo-sitemap-${sm.name}-alert-skipped`}
                            title={`Alert loop deferred this sitemap (cap=${alertCap || 0}). Run a manual deep scan.`}
                          >
                            Alert-skipped — manual scan needed
                          </p>
                        )}
                        {/* Task #350: inline auto-scan blast-radius
                            line — only visible when the alert loop has
                            already deep-scanned this sitemap and we
                            haven't loaded a manual scan since. Tells
                            the on-call admin the true failing count
                            without a re-scan. Suppressed when Task
                            #352's isAlertSkipped already covers the
                            cap-skipped case for this sitemap. */}
                        {autoScan && !(autoScan.skipped && isAlertSkipped) && (
                          <p
                            className={`text-[10px] mt-0.5 truncate ${
                              autoScan.skipped ? 'text-gray-500' : 'text-red-600'
                            }`}
                            title={`Auto-deep-scan from ${autoScan.alert_type || 'alert'} ${autoScan.fired_at ? `at ${autoScan.fired_at}` : ''}`}
                            data-testid={`seo-sitemap-${sm.name}-auto-scan`}
                          >
                            {autoScan.skipped ? (
                              <>
                                Auto deep scan skipped — alert-cycle cap of{' '}
                                <span className="font-semibold">{autoScan.cap || '—'}</span>{' '}
                                sitemaps reached. Click "Show all" to scan now.
                              </>
                            ) : autoScan.error ? (
                              <>Auto deep scan errored: {autoScan.error}</>
                            ) : (
                              <>
                                Auto deep scan: <span className="font-semibold">{autoScan.failing_count.toLocaleString()}</span>
                                {' '}of{' '}
                                <span className="font-semibold">
                                  {autoScan.checked.toLocaleString()}
                                  {autoScan.truncated && '+'}
                                </span>
                                {' URLs failing'}
                                {autoScan.fired_at && (
                                  <span className="ml-1 text-red-500/80">
                                    · {formatTimeAgo(autoScan.fired_at)}
                                  </span>
                                )}
                              </>
                            )}
                          </p>
                        )}
                      </div>
                      <div className="flex items-center gap-3 text-[11px] text-gray-500 flex-shrink-0">
                        {isAlertSkipped && (
                          <>
                            <span
                              className="font-mono px-2 py-0.5 rounded bg-amber-50 text-amber-800 border border-amber-200"
                              data-testid={`seo-sitemap-${sm.name}-alert-skipped-badge`}
                              title="The alert loop did not deep-scan this sitemap because the per-firing cap was reached."
                            >
                              scan needed
                            </span>
                            <span
                              role="button"
                              tabIndex={0}
                              data-testid={`seo-sitemap-${sm.name}-alert-skipped-scan`}
                              onClick={async (e) => {
                                e.stopPropagation();
                                if (sitemapDeepScans[sm.name]?.loading) return;
                                setExpandedSitemap(sm.name);
                                setSitemapDeepScans((prev) => ({
                                  ...prev,
                                  [sm.name]: { loading: true, error: null, data: null },
                                }));
                                try {
                                  const res = await seoHealthDeepScan(adminToken, sm.name);
                                  if (res?.data?.error) {
                                    setSitemapDeepScans((prev) => ({
                                      ...prev,
                                      [sm.name]: { loading: false, error: res.data.error, data: null },
                                    }));
                                  } else {
                                    setSitemapDeepScans((prev) => ({
                                      ...prev,
                                      [sm.name]: { loading: false, error: null, data: res.data },
                                    }));
                                  }
                                } catch (err) {
                                  const msg = err?.response?.data?.detail
                                    || err?.message
                                    || 'Scan failed';
                                  setSitemapDeepScans((prev) => ({
                                    ...prev,
                                    [sm.name]: { loading: false, error: msg, data: null },
                                  }));
                                }
                              }}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault();
                                  e.currentTarget.click();
                                }
                              }}
                              className="font-semibold px-2 py-0.5 rounded border border-amber-300 bg-white text-amber-800 hover:bg-amber-50 cursor-pointer select-none"
                            >
                              {sitemapDeepScans[sm.name]?.loading ? 'Scanning…' : 'Deep scan now'}
                            </span>
                          </>
                        )}
                        {/* Task #350: "auto" pill so admins can tell the
                            alert-loop scan apart from a manual one
                            triggered via the "Show all" button. */}
                        {autoScan && !autoScan.error && !autoScan.skipped && (
                          <span
                            className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-red-100 text-red-700 border border-red-200"
                            title="Auto-deep-scan summary harvested from the alert loop (Task #347)"
                            data-testid={`seo-sitemap-${sm.name}-auto-pill`}
                          >
                            auto
                          </span>
                        )}
                        {autoScan?.skipped && !isAlertSkipped && (
                          <span
                            className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-600 border border-gray-200"
                            title="Alert loop reached its per-cycle deep-scan cap and skipped this sitemap"
                            data-testid={`seo-sitemap-${sm.name}-auto-skipped-pill`}
                          >
                            auto · skipped
                          </span>
                        )}
                        <span title="URLs in sitemap" className="font-mono">
                          {(sm.url_count ?? 0).toLocaleString()} urls
                        </span>
                        <span
                          className={`font-mono px-2 py-0.5 rounded ${
                            allOk ? 'bg-emerald-50 text-emerald-700' :
                            partial ? 'bg-amber-50 text-amber-700' :
                            broken ? 'bg-red-50 text-red-700' :
                            'bg-gray-50 text-gray-500'
                          }`}
                          title="Sample HEAD checks against random URLs"
                        >
                          {okCount}/{totalCount} ok
                        </span>
                        {canExpand && (
                          <span className="text-gray-400 text-xs select-none">
                            {isExpanded ? '▾' : '▸'}
                          </span>
                        )}
                      </div>
                    </button>
                    {canExpand && isExpanded && (
                      <div
                        className="px-4 pb-3 pt-2 border-t border-gray-100 bg-gray-50/60"
                        data-testid={`seo-sitemap-${sm.name}-samples`}
                      >
                        <div className="flex items-center justify-between mb-2 gap-2 flex-wrap">
                          {usingDeepScan ? (
                            <p className="text-[10px] uppercase tracking-wider text-red-700 font-semibold">
                              Failing URLs ({deepScanFailing.length}
                              {deepScan.data.truncated
                                ? ` of ${deepScan.data.total_urls}+`
                                : ` of ${deepScan.data.checked} scanned`})
                            </p>
                          ) : (
                            <>
                              <p className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
                                Sample probes ({sampleRows.length})
                              </p>
                              {failingCount > 0 && (
                                <p className="text-[10px] uppercase tracking-wider text-red-600 font-semibold">
                                  {failingCount} failing{mayHaveMoreFailures ? '+ in sample' : ''}
                                </p>
                              )}
                            </>
                          )}
                          {/* Task #345: deep-scan button. Only shown when
                              the sample probe hit its 10-URL cap, since
                              that's the only situation where the displayed
                              list is incomplete. */}
                          {mayHaveMoreFailures && !usingDeepScan && (
                            <button
                              type="button"
                              data-testid={`seo-sitemap-${sm.name}-scan-all`}
                              disabled={!!deepScan?.loading}
                              onClick={async () => {
                                setSitemapDeepScans((prev) => ({
                                  ...prev,
                                  [sm.name]: { loading: true, error: null, data: null },
                                }));
                                try {
                                  const res = await seoHealthDeepScan(adminToken, sm.name);
                                  // The backend may return HTTP 200 with an
                                  // in-band error (e.g. sitemap fetch/parse
                                  // failure → `{ error, failing: [] }`).
                                  // Treat that as an error state so the
                                  // failure surfaces in red and the user
                                  // can retry, instead of silently showing
                                  // "Failing URLs (0)".
                                  if (res?.data?.error) {
                                    setSitemapDeepScans((prev) => ({
                                      ...prev,
                                      [sm.name]: {
                                        loading: false,
                                        error: res.data.error,
                                        data: null,
                                      },
                                    }));
                                  } else {
                                    setSitemapDeepScans((prev) => ({
                                      ...prev,
                                      [sm.name]: { loading: false, error: null, data: res.data },
                                    }));
                                  }
                                } catch (err) {
                                  const msg = err?.response?.data?.detail
                                    || err?.message
                                    || 'Scan failed';
                                  setSitemapDeepScans((prev) => ({
                                    ...prev,
                                    [sm.name]: { loading: false, error: msg, data: null },
                                  }));
                                }
                              }}
                              className="text-[10px] font-semibold px-2 py-1 rounded border border-red-300 bg-white text-red-700 hover:bg-red-50 disabled:opacity-50 disabled:cursor-wait flex items-center gap-1"
                            >
                              {deepScan?.loading ? (
                                <>
                                  <Loader2 size={11} className="animate-spin" /> Scanning…
                                </>
                              ) : (
                                <>Show all</>
                              )}
                            </button>
                          )}
                          {usingDeepScan && (
                            <span
                              className="text-[10px] text-gray-500 font-mono"
                              data-testid={`seo-sitemap-${sm.name}-scan-meta`}
                            >
                              full scan · {deepScan.data.checked}/{deepScan.data.total_urls} probed
                              {deepScan.data.truncated && ' (truncated at limit)'}
                            </span>
                          )}
                          {/* Task #346: CSV export of the full failing list
                              after a deep scan, so admins can paste it into
                              a sheet or share with content/eng teammates
                              without copying URLs row-by-row. Only shown
                              once we actually have deep-scan results with
                              at least one failing URL. */}
                          {usingDeepScan && deepScan.data.failing?.length > 0 && (
                            <button
                              type="button"
                              data-testid={`seo-sitemap-${sm.name}-download-csv`}
                              onClick={() => {
                                const rows = deepScan.data.failing.map((f) => {
                                  // CSV escape: wrap any field that contains a
                                  // comma, quote, or newline in double quotes
                                  // and double-up internal quotes.
                                  const esc = (v) => {
                                    let s = v == null ? '' : String(v);
                                    // CSV formula-injection guard: a cell
                                    // beginning with =, +, -, or @ would be
                                    // executed as a formula by Excel/Sheets.
                                    // Since `url`/`error` can carry external
                                    // content, prefix a single quote to
                                    // neutralize any such payload before the
                                    // standard quote/escape pass.
                                    if (/^[=+\-@]/.test(s)) s = `'${s}`;
                                    return /[",\n\r]/.test(s)
                                      ? `"${s.replace(/"/g, '""')}"`
                                      : s;
                                  };
                                  return [esc(f.url), esc(f.status ?? ''), esc(f.error ?? '')].join(',');
                                });
                                const csv = ['url,status,error', ...rows].join('\n');
                                // Prepend BOM so Excel opens UTF-8 cleanly.
                                const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
                                const ts = new Date().toISOString().replace(/[:.]/g, '-');
                                const sitemapStem = sm.name.replace(/\.xml$/i, '');
                                const filename = `failing-urls-${sitemapStem}-${ts}.csv`;
                                const url = URL.createObjectURL(blob);
                                const a = document.createElement('a');
                                a.href = url;
                                a.download = filename;
                                document.body.appendChild(a);
                                a.click();
                                document.body.removeChild(a);
                                URL.revokeObjectURL(url);
                              }}
                              className="text-[10px] font-semibold px-2 py-1 rounded border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
                            >
                              Download CSV
                            </button>
                          )}
                        </div>
                        {deepScan?.error && (
                          <div
                            className="text-[11px] text-red-700 bg-red-100 border border-red-200 rounded px-2 py-1 mb-2"
                            data-testid={`seo-sitemap-${sm.name}-scan-error`}
                          >
                            Scan failed: {deepScan.error}
                          </div>
                        )}
                        <ul className="space-y-1 max-h-64 overflow-y-auto">
                          {/* Deep-scan results contain only failing URLs;
                              treat each as `ok: false` so styling matches
                              the Task #298 sample-row renderer. */}
                          {(usingDeepScan
                            ? deepScanFailing.map((f) => ({ ...f, ok: false }))
                            : sampleRows
                          ).map((c, i) => {
                            // Defense-in-depth: only render <a> for http(s) URLs
                            // so a poisoned `javascript:` payload in Mongo can
                            // never become a clickable link in the admin UI.
                            const safeHref = typeof c.url === 'string'
                              && /^https?:\/\//i.test(c.url) ? c.url : null;
                            const failed = !c.ok;
                            const badgeCls = failed
                              ? 'bg-red-100 text-red-700'
                              : 'bg-emerald-100 text-emerald-700';
                            const rowCls = failed
                              ? 'bg-red-50 border-red-200'
                              : 'bg-white border-gray-100';
                            const statusLabel = c.status === 0 || c.status == null
                              ? 'ERR' : c.status;
                            return (
                              <li
                                key={`${sm.name}-${i}`}
                                className={`flex items-start gap-2 text-[11px] font-mono px-2 py-1.5 rounded border ${rowCls}`}
                                data-testid={`seo-sample-row${failed ? '-failed' : ''}`}
                              >
                                <span className={`px-1.5 py-0.5 rounded font-semibold flex-shrink-0 ${badgeCls}`}>
                                  {statusLabel}
                                </span>
                                <div className="min-w-0 flex-1">
                                  {safeHref ? (
                                    <a
                                      href={safeHref}
                                      target="_blank"
                                      rel="noopener noreferrer"
                                      className={`truncate block ${failed ? 'text-red-900 hover:text-red-700' : 'text-gray-700 hover:text-blue-600'}`}
                                      title={c.url}
                                    >
                                      {c.url}
                                    </a>
                                  ) : (
                                    <span className="text-gray-700 truncate block" title={c.url}>{c.url}</span>
                                  )}
                                  {c.error && (
                                    <p className="text-[10px] text-red-600 mt-0.5 truncate" title={c.error}>
                                      {c.error}
                                    </p>
                                  )}
                                </div>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    )}
                  </div>
                );
                });
              })()}
              {(!seoLive.sitemaps || seoLive.sitemaps.length === 0) && (
                <p className="text-xs text-gray-400">No sitemaps reported.</p>
              )}
            </div>

            <div className="mt-4 pt-4 border-t border-gray-100 flex items-center gap-3 flex-wrap">
              <Database size={14} className="text-gray-400" />
              <span className="text-xs font-semibold text-gray-600">D1 Sync</span>
              {(() => {
                const d1 = seoLive.d1_sync || {};
                const d1Status = (d1.status || 'unknown').toLowerCase();
                const cls = d1Status === 'ok' || d1Status === 'fresh'
                  ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                  : d1Status === 'stale' || d1Status === 'degraded'
                  ? 'bg-amber-50 text-amber-700 border-amber-200'
                  : d1Status === 'error' || d1Status === 'critical'
                  ? 'bg-red-50 text-red-700 border-red-200'
                  : 'bg-gray-50 text-gray-500 border-gray-200';
                const lastSync = d1.last_sync || d1.last_synced_at || d1.updated_at || d1.synced_at;
                return (
                  <>
                    <span className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase tracking-wider ${cls}`} data-testid="d1-sync-status">
                      {d1Status}
                    </span>
                    {lastSync && (
                      <span className="text-[11px] text-gray-500">
                        last sync {formatTimeAgo(lastSync)}
                        <span className="text-gray-400"> · {new Date(lastSync).toLocaleString()}</span>
                      </span>
                    )}
                    {d1.row_count != null && (
                      <span className="text-[11px] text-gray-500 font-mono">
                        rows: {Number(d1.row_count).toLocaleString()}
                      </span>
                    )}
                    {d1.error && (
                      <span className="text-[11px] text-red-600 truncate" title={d1.error}>
                        {d1.error}
                      </span>
                    )}
                  </>
                );
              })()}
              <button
                type="button"
                data-testid="d1-sync-trigger"
                disabled={d1SyncRunning}
                onClick={async () => {
                  setD1SyncRunning(true);
                  setD1SyncError(null);
                  setD1SyncDurationMs(null);
                  // Task #506: time the POST client-side so operators can
                  // see how long the manual sync actually took. Done in
                  // the browser to avoid a backend change; covers network
                  // + primary + extended_mirror combined.
                  const _started = (typeof performance !== 'undefined' && performance.now)
                    ? performance.now()
                    : Date.now();
                  const _elapsed = () => {
                    const _now = (typeof performance !== 'undefined' && performance.now)
                      ? performance.now()
                      : Date.now();
                    return Math.max(0, Math.round(_now - _started));
                  };
                  try {
                    const res = await axios.post(
                      `${API_BASE}/admin/d1-sync`,
                      null,
                      adminHdr(adminToken),
                    );
                    setD1SyncDurationMs(_elapsed());
                    setD1SyncResult(res?.data ?? {});
                    const data = res?.data || {};
                    const primary = data.primary || data;
                    const ext = data.extended_mirror;
                    if (primary && primary.success === false) {
                      toast.error(`D1 sync failed: ${primary.reason || 'unknown reason'}`);
                    } else if (ext && ext.success === false) {
                      toast.error(`Extended mirror failed: ${ext.reason || 'unknown reason'}`);
                    } else {
                      toast.success('D1 sync complete');
                      // Task #507: re-fetch the live SEO/D1 health snapshot
                      // once so the badge above the result panel reflects
                      // the freshly synced state immediately, instead of
                      // waiting for the next 60s poll. Only on success —
                      // a failed sync must keep the prior "stale" badge
                      // visible so operators don't get a false "ok".
                      seoHealthLive()
                        .then((r) => { setSeoLive(r.data); setSeoLiveError(null); })
                        .catch(() => { /* keep prior badge on refresh failure */ });
                    }
                  } catch (e) {
                    setD1SyncDurationMs(_elapsed());
                    const detail = e?.response?.data?.detail || e?.message || 'D1 sync failed';
                    setD1SyncResult(null);
                    setD1SyncError(typeof detail === 'string' ? detail : JSON.stringify(detail));
                    toast.error(`D1 sync failed: ${typeof detail === 'string' ? detail : 'see console'}`);
                  } finally {
                    setD1SyncRunning(false);
                  }
                }}
                className="ml-1 inline-flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-semibold border border-violet-200 bg-violet-50 text-violet-700 hover:bg-violet-100 disabled:opacity-50 disabled:cursor-not-allowed"
                title="Trigger MongoDB → D1 sync (primary + extended mirror)"
              >
                {d1SyncRunning
                  ? <Loader2 size={11} className="animate-spin" />
                  : <RefreshCw size={11} />}
                {d1SyncRunning ? 'Syncing…' : 'Sync now'}
              </button>
              {seoLive.content_stats?.last_content_update && (
                <span className="ml-auto text-[11px] text-gray-500">
                  <Clock size={11} className="inline mr-1 -mt-0.5" />
                  content updated {formatTimeAgo(seoLive.content_stats.last_content_update)}
                </span>
              )}
            </div>

            {/*
              Task #461 — surface the post-sync result so operators can see
              both the primary sync_full outcome and the extended mirror
              (seo_meta / audit_log / syllabus_map) summary returned by
              `POST /admin/d1-sync` without having to inspect the network
              tab. A failed extended_mirror surfaces the `reason` string
              the backend returned (flag_off, empty_payload, primary
              target failure, exception class) instead of being silently
              dropped.
            */}
            {(d1SyncResult || d1SyncError) && (
              <div
                className="mt-3 p-3 rounded-lg border border-gray-200 bg-gray-50"
                data-testid="d1-sync-result"
              >
                {(() => {
                  const _primary = d1SyncResult ? (d1SyncResult.primary || d1SyncResult) : null;
                  const _ext = d1SyncResult ? d1SyncResult.extended_mirror : null;
                  const overallFailed =
                    !!d1SyncError
                    || (_primary && _primary.success === false)
                    || (_ext && _ext.success === false);
                  // Task #506: surface client-measured duration so a slow
                  // run (e.g. extended_mirror dragging on D1) is visible
                  // before the freshness alert fires. >10s → amber.
                  const SLOW_MS = 10000;
                  const _slow = typeof d1SyncDurationMs === 'number' && d1SyncDurationMs >= SLOW_MS;
                  const _fmtElapsed = (ms) => (
                    ms < 1000
                      ? `${ms}ms`
                      : `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)}s`
                  );
                  return (
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      <span className="text-[11px] font-semibold uppercase tracking-wider text-gray-600">
                        Last manual sync
                      </span>
                      <span
                        className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase tracking-wider ${
                          overallFailed
                            ? 'bg-red-50 text-red-700 border-red-200'
                            : 'bg-emerald-50 text-emerald-700 border-emerald-200'
                        }`}
                        data-testid="d1-sync-result-status"
                      >
                        {overallFailed ? 'failed' : 'ok'}
                      </span>
                      {typeof d1SyncDurationMs === 'number' && (
                        <span
                          className={`text-[10px] px-2 py-0.5 rounded-full border font-mono ${
                            _slow
                              ? 'bg-amber-50 text-amber-800 border-amber-300'
                              : 'bg-gray-50 text-gray-600 border-gray-200'
                          }`}
                          data-testid="d1-sync-result-elapsed"
                          title={
                            _slow
                              ? `Slow: took ≥ ${SLOW_MS / 1000}s (threshold)`
                              : 'Client-measured time around POST /admin/d1-sync'
                          }
                        >
                          elapsed: {_fmtElapsed(d1SyncDurationMs)}
                        </span>
                      )}
                    </div>
                  );
                })()}

                {d1SyncError && (
                  <p
                    className="text-[11px] text-red-600 break-all"
                    data-testid="d1-sync-error"
                  >
                    {d1SyncError}
                  </p>
                )}

                {d1SyncResult && (() => {
                  const primary = d1SyncResult.primary || d1SyncResult;
                  const ext = d1SyncResult.extended_mirror;
                  const primaryRows = primary?.row_counts || primary?.tables_synced || null;
                  return (
                    <>
                      <div className="text-[11px] text-gray-700 font-mono mb-2">
                        primary:{' '}
                        {primary?.success === false ? (
                          <span className="text-red-600">failed</span>
                        ) : (
                          <span className="text-emerald-700">ok</span>
                        )}
                        {typeof primary?.total === 'number' && (
                          <span className="text-gray-500"> · {primary.total.toLocaleString()} rows</span>
                        )}
                        {primary?.reason && (
                          <span className="text-red-600"> · {primary.reason}</span>
                        )}
                      </div>
                      {primaryRows && typeof primaryRows === 'object' && (
                        <ul className="text-[10px] text-gray-600 font-mono mb-2 grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-0.5">
                          {Object.entries(primaryRows).map(([t, c]) => (
                            <li key={`prim-${t}`}>
                              <span className="text-gray-400">{t}:</span>{' '}
                              {Number(c).toLocaleString()}
                            </li>
                          ))}
                        </ul>
                      )}

                      {ext && (
                        <div
                          className="mt-2 pt-2 border-t border-gray-200"
                          data-testid="d1-sync-extended-mirror"
                        >
                          <div className="flex items-center gap-2 mb-1.5">
                            <span className="text-[11px] font-semibold text-gray-700">
                              Extended mirror
                            </span>
                            <span
                              className={`text-[10px] px-2 py-0.5 rounded-full border font-semibold uppercase tracking-wider ${
                                ext.success
                                  ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                                  : 'bg-red-50 text-red-700 border-red-200'
                              }`}
                              data-testid="d1-sync-extended-mirror-status"
                            >
                              {ext.success ? 'ok' : 'failed'}
                            </span>
                            {Array.isArray(ext.tables) && ext.tables.length > 0 && (
                              <span className="text-[10px] text-gray-500 font-mono">
                                {ext.tables.length} table{ext.tables.length === 1 ? '' : 's'}
                              </span>
                            )}
                          </div>
                          {!ext.success && ext.reason && (
                            <p
                              className="text-[11px] text-red-600 break-all"
                              data-testid="d1-sync-extended-mirror-reason"
                            >
                              {ext.reason}
                            </p>
                          )}
                          {ext.row_counts && typeof ext.row_counts === 'object' && Object.keys(ext.row_counts).length > 0 && (
                            <ul
                              className="text-[10px] text-gray-600 font-mono grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-0.5"
                              data-testid="d1-sync-extended-mirror-rows"
                            >
                              {Object.entries(ext.row_counts).map(([t, c]) => (
                                <li key={`ext-${t}`}>
                                  <span className="text-gray-400">{t}:</span>{' '}
                                  {Number(c).toLocaleString()}
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      )}
                    </>
                  );
                })()}
              </div>
            )}
          </>
        )}
      </GlassCard>
      </SectionErrorBoundary>
      

      {/*
        Task #991 — standalone fallback for the "N on hold" badge that
        runs *outside* the Alert History gate. The richer inline pill
        below (inside the Alert History header) is what admins see in
        the normal case, but if `/admin/alerts?limit=50` ever fails
        (the source for `alertHistory`) the cooldown indicator stays
        visible because its own poll is independent. Hidden when
        nothing is on hold OR when alertHistory is present (the inline
        pill takes over and avoids double-rendering).
      */}
      {!alertHistory && cooldownActiveCount > 0 && (
        <SectionErrorBoundary name="Suppressed Alert Badge">
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => onNavigate && onNavigate('botsecurity', { panel: 'alert-cooldowns' })}
              className="inline-flex items-center gap-1.5 text-[11px] px-3 py-1.5 rounded-full bg-amber-50 border border-amber-200 text-amber-800 font-semibold hover:bg-amber-100 transition-colors cursor-pointer"
              title={`${cooldownActiveCount} alert${cooldownActiveCount === 1 ? '' : 's'} silenced by the 6h cooldown — click to review in Bot Security`}
            >
              <Clock size={12} />
              {cooldownActiveCount} alert{cooldownActiveCount === 1 ? '' : 's'} on hold
            </button>
          </div>
        </SectionErrorBoundary>
      )}

      <SectionErrorBoundary name="Alert History">
      {alertHistory && (
        <GlassCard className="p-5">
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            <AlertTriangle size={16} className="text-orange-500" />
            <h3 className="text-gray-700 font-semibold">Alert History</h3>
            {alertHistory.alerts?.some(a => !a.acknowledged) && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-100 text-red-700 font-semibold">
                {alertHistory.alerts.filter(a => !a.acknowledged).length} unacknowledged
              </span>
            )}
            {/*
              Task #991 — at-a-glance "alert is being suppressed" badge.
              Hidden when nothing is on hold so it's never misleading
              (per the task's "Done looks like" spec). Click jumps into
              Bot Security with the Suppressed Alerts panel auto-
              expanded + scrolled into view (see AlertCooldownsPanel's
              `navContext` handler in AdminBotSecurity.jsx). A
              standalone fallback above this card covers the
              alertHistory-fetch-failed case so the indicator never
              disappears just because a sibling API blipped.
            */}
            {cooldownActiveCount > 0 && (
              <button
                type="button"
                onClick={() => onNavigate && onNavigate('botsecurity', { panel: 'alert-cooldowns' })}
                className="inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-800 font-semibold hover:bg-amber-200 transition-colors cursor-pointer"
                title={`${cooldownActiveCount} alert${cooldownActiveCount === 1 ? '' : 's'} silenced by the 6h cooldown — click to review in Bot Security`}
              >
                <Clock size={10} />
                {cooldownActiveCount} on hold
              </button>
            )}
            <div className="ml-auto flex items-center gap-2">
              <button
                onClick={toggleAlertSound}
                className={`flex items-center gap-1 text-[10px] px-2 py-1 rounded-md border transition-colors font-medium ${
                  alertSoundEnabled
                    ? 'bg-violet-50 text-violet-700 border-violet-200 hover:bg-violet-100'
                    : 'bg-gray-50 text-gray-400 border-gray-200 hover:bg-gray-100'
                }`}
                title={alertSoundEnabled ? 'Alert sound on — click to mute' : 'Alert sound off — click to enable'}
              >
                {alertSoundEnabled ? <Volume2 size={11} /> : <VolumeX size={11} />}
                {alertSoundEnabled ? 'Sound On' : 'Sound Off'}
              </button>
              {pushNotif.isSupported && (
                <button
                  onClick={async () => {
                    const currentlyEnabled = notifPrefs?.push_enabled && pushNotif.subscribed;
                    if (currentlyEnabled) {
                      await pushNotif.unsubscribe();
                      saveNotifPrefs({ push_enabled: false });
                    } else {
                      const success = await pushNotif.subscribe();
                      if (success !== false) saveNotifPrefs({ push_enabled: true });
                    }
                  }}
                  disabled={pushNotif.loading}
                  className={`flex items-center gap-1 text-[10px] px-2 py-1 rounded-md border transition-colors font-medium ${
                    (notifPrefs?.push_enabled && pushNotif.subscribed)
                      ? 'bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100'
                      : 'bg-gray-50 text-gray-400 border-gray-200 hover:bg-gray-100'
                  }`}
                  title={(notifPrefs?.push_enabled && pushNotif.subscribed) ? 'Push notifications enabled — click to disable' : 'Enable browser push notifications for critical alerts'}
                >
                  {(notifPrefs?.push_enabled && pushNotif.subscribed) ? <Bell size={11} /> : <BellOff size={11} />}
                  {pushNotif.loading ? 'Loading...' : (notifPrefs?.push_enabled && pushNotif.subscribed) ? 'Push On' : 'Push Off'}
                </button>
              )}
              <select
                className="text-[10px] border border-gray-200 rounded-md px-2 py-1 bg-white text-gray-600"
                value={alertFilter}
                onChange={e => setAlertFilter(e.target.value)}
              >
                <option value="all">All alerts</option>
                <option value="unacknowledged">Unacknowledged</option>
                <option value="acknowledged">Acknowledged</option>
              </select>
              {(() => {
                // Task #693 — count how many loaded alerts mention each
                // trigger reason so the dropdown can surface noisy
                // reasons (e.g. "checkout_skip (4)") and operators can
                // triage the loudest ones first. We dedupe within a
                // single alert (one alert that lists "foo" twice in its
                // snapshot still counts once) so the count matches the
                // post-filter row count the table will show. The same
                // counts feed the active "Reason: foo (N)" pill below
                // via the shared ``reasonCounts`` Map (lifted via the
                // outer IIFE so we only walk the alert history once).
                const reasonCounts = new Map();
                (alertHistory.alerts || []).forEach(a => {
                  if (a?.type === 'review_prompt_reason_ctr_drop' && Array.isArray(a?.threshold_snapshot?.reasons)) {
                    const seenInAlert = new Set();
                    a.threshold_snapshot.reasons.forEach(r => {
                      const name = (r && typeof r === 'object') ? (r.reason ?? '') : String(r ?? '');
                      if (name && !seenInAlert.has(name)) {
                        seenInAlert.add(name);
                        reasonCounts.set(name, (reasonCounts.get(name) || 0) + 1);
                      }
                    });
                  }
                });
                const reasons = Array.from(reasonCounts.keys()).sort((a, b) => {
                  // Noisiest first, then alphabetical for ties — matches
                  // the triage flow the dropdown is meant to enable.
                  const diff = (reasonCounts.get(b) || 0) - (reasonCounts.get(a) || 0);
                  return diff !== 0 ? diff : a.localeCompare(b);
                });
                const activeCount = reasonCounts.get(alertReasonFilter) || 0;
                if (reasons.length === 0 && !alertReasonFilter) return null;
                return (
                  <>
                    <select
                      className="text-[10px] border border-gray-200 rounded-md px-2 py-1 bg-white text-gray-600"
                      value={alertReasonFilter}
                      onChange={e => setAlertReasonFilter(e.target.value)}
                      title="Filter alert history to alerts whose reason snapshot contains this trigger reason. Counts show how many of the loaded alerts mention each reason — sorted noisiest first."
                    >
                      <option value="">All reasons</option>
                      {reasons.map(r => (
                        <option key={r} value={r}>{`${r} (${reasonCounts.get(r)})`}</option>
                      ))}
                      {alertReasonFilter && !reasons.includes(alertReasonFilter) && (
                        <option value={alertReasonFilter}>{`${alertReasonFilter} (0)`}</option>
                      )}
                    </select>
                    {alertReasonFilter && (
                      <button
                        type="button"
                        onClick={() => setAlertReasonFilter('')}
                        className="text-[10px] px-2 py-1 rounded-md bg-violet-50 text-violet-700 border border-violet-200 hover:bg-violet-100 transition-colors font-medium flex items-center gap-1"
                        title={`Clear reason filter — ${activeCount} alert${activeCount === 1 ? '' : 's'} in the loaded history mention "${alertReasonFilter}"`}
                      >
                        {`Reason: ${alertReasonFilter} (${activeCount})`}
                        <X size={10} />
                      </button>
                    )}
                  </>
                );
              })()}
              <label
                className="flex items-center gap-1 text-[10px] text-gray-600 px-2 py-1 rounded-md border border-gray-200 bg-white cursor-pointer select-none hover:bg-gray-50"
                title="Include synthetic alerts produced by the Test alert delivery button"
              >
                <input
                  type="checkbox"
                  checked={showSyntheticAlerts}
                  onChange={e => setShowSyntheticAlerts(e.target.checked)}
                  className="h-3 w-3 rounded border-gray-300 text-violet-600 focus:ring-violet-200"
                />
                Show test alerts
              </label>
              {alertHistory.alerts?.some(a => !a.acknowledged) && (
                <button
                  onClick={handleAcknowledgeAll}
                  className="text-[10px] px-2.5 py-1 rounded-md bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100 transition-colors font-medium"
                >
                  Acknowledge All
                </button>
              )}
              <button
                onClick={handleOpenAlertSettings}
                className="text-[10px] px-2.5 py-1 rounded-md bg-gray-50 text-gray-600 border border-gray-200 hover:bg-violet-50 hover:text-violet-600 hover:border-violet-200 transition-colors font-medium flex items-center gap-1"
              >
                <Settings size={10} />
                Settings
              </button>
              <button
                onClick={() => setNotifPrefsOpen(prev => !prev)}
                className={`text-[10px] px-2.5 py-1 rounded-md border transition-colors font-medium flex items-center gap-1 ${
                  notifPrefsOpen
                    ? 'bg-violet-50 text-violet-700 border-violet-200'
                    : 'bg-gray-50 text-gray-600 border-gray-200 hover:bg-violet-50 hover:text-violet-600 hover:border-violet-200'
                }`}
                data-testid="notif-prefs-toggle"
              >
                <Bell size={10} />
                Preferences
              </button>
            </div>
          </div>

          {alertSettingsOpen && alertSettingsDraft && (
            <div className="mb-4 p-4 rounded-xl bg-gray-50 border border-gray-200">
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-xs font-semibold text-gray-700">Alert Thresholds & Expiration</h4>
                <div className="flex gap-2">
                  <button onClick={handleResetAlertSettings} className="text-[10px] px-2 py-0.5 rounded bg-white border border-gray-200 text-gray-500 hover:bg-gray-100 transition-colors">Reset Defaults</button>
                  <button
                    onClick={handleSaveAlertSettings}
                    disabled={alertSettingsSaving}
                    className="text-[10px] px-3 py-0.5 rounded bg-violet-600 text-white hover:bg-violet-700 transition-colors disabled:opacity-50 font-medium"
                  >
                    {alertSettingsSaving ? 'Saving...' : 'Save'}
                  </button>
                </div>
              </div>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1">Error Rate (%)</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0.1"
                    value={alertSettingsDraft.thresholds.error_rate_pct ?? ''}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, error_rate_pct: parseFloat(e.target.value) || 0 } }))}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1">Latency p95 (ms)</label>
                  <input
                    type="number"
                    step="100"
                    min="100"
                    value={alertSettingsDraft.thresholds.latency_p95_ms ?? ''}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, latency_p95_ms: parseInt(e.target.value) || 0 } }))}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1">Fallback Rate (%)</label>
                  <input
                    type="number"
                    step="1"
                    min="1"
                    value={alertSettingsDraft.thresholds.fallback_rate_pct ?? ''}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, fallback_rate_pct: parseFloat(e.target.value) || 0 } }))}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1">Spoof RPM</label>
                  <input
                    type="number"
                    step="1"
                    min="1"
                    value={alertSettingsDraft.thresholds.spoof_rpm ?? ''}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, spoof_rpm: parseInt(e.target.value) || 0 } }))}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1">Endpoint Down (min)</label>
                  <input
                    type="number"
                    step="5"
                    min="1"
                    value={alertSettingsDraft.thresholds.endpoint_down_minutes ?? ''}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, endpoint_down_minutes: parseInt(e.target.value) || 0 } }))}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1">EP Check Interval (min)</label>
                  <input
                    type="number"
                    step="5"
                    min="1"
                    value={alertSettingsDraft.thresholds.endpoint_down_check_minutes ?? ''}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, endpoint_down_check_minutes: parseInt(e.target.value) || 0 } }))}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1" title="Fires when sitemap URL spot-checks return ≥ this % of 404s for two consecutive hourly snapshots.">URL 404 Spike (%)</label>
                  <input
                    type="number"
                    step="1"
                    min="1"
                    max="100"
                    value={alertSettingsDraft.thresholds.url_404_spike_pct ?? ''}
                    onChange={e => {
                      const raw = e.target.value;
                      const parsed = parseFloat(raw);
                      // Keep the previous value if input is blank/invalid so
                      // an empty field never silently coerces to 0% (which
                      // would alert on the slightest failure).
                      const next = (raw === '' || Number.isNaN(parsed))
                        ? alertSettingsDraft.thresholds.url_404_spike_pct
                        : parsed;
                      setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, url_404_spike_pct: next } }));
                    }}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1" title="Fires when more than this many hydrate_preload_failed events occur in the last hour. Indicates a stale-build / CDN gap.">Hydrate Failures /hr</label>
                  <input
                    type="number"
                    step="1"
                    min="1"
                    value={alertSettingsDraft.thresholds.hydrate_failure_per_hour ?? ''}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, hydrate_failure_per_hour: parseInt(e.target.value) || 0 } }))}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1" title="Fires when the hydrate auto-reload success rate falls below this % over the last hour. Indicates the new build may also be broken.">Recovery Rate Floor (%)</label>
                  <input
                    type="number"
                    step="1"
                    min="1"
                    max="100"
                    value={alertSettingsDraft.thresholds.hydrate_recovery_min_rate_pct ?? ''}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, hydrate_recovery_min_rate_pct: parseFloat(e.target.value) || 0 } }))}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1" title="Minimum auto-reload attempts in the last hour before the recovery-rate alert is allowed to fire.">Recovery Min Attempts</label>
                  <input
                    type="number"
                    step="1"
                    min="1"
                    value={alertSettingsDraft.thresholds.hydrate_recovery_min_attempts ?? ''}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, hydrate_recovery_min_attempts: parseInt(e.target.value) || 0 } }))}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1" title="Minimum review_prompt_shown events in the last 7d before the CTR-floor alert is allowed to fire.">Review Prompt Min Shown (7d)</label>
                  <input
                    type="number"
                    step="1"
                    min="1"
                    value={alertSettingsDraft.thresholds.review_prompt_ctr_min_shown ?? ''}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, review_prompt_ctr_min_shown: parseInt(e.target.value) || 0 } }))}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1" title="Fires when the 7d review-prompt click-through rate falls below this %. Indicates a UI regression broke the prompt CTA / writeReviewUrl.">Review Prompt CTR Floor (%)</label>
                  <input
                    type="number"
                    step="0.1"
                    min="0.1"
                    max="100"
                    value={alertSettingsDraft.thresholds.review_prompt_ctr_floor_pct ?? ''}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, review_prompt_ctr_floor_pct: parseFloat(e.target.value) || 0 } }))}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
                <div>
                  <label htmlFor="alert-reason-ctr-sigma-input" className="text-[10px] text-gray-500 font-medium block mb-1" title="Per-reason CTR-collapse alert: required multiple of the per-reason rolling stddev the WoW drop must additionally exceed (auto-tunes the threshold from baseline noise so volatile reasons don't page on ordinary swings). Set to 0 to disable the sigma gate and rely only on the absolute pp floor.">Reason CTR Sigma Multiplier</label>
                  <input
                    id="alert-reason-ctr-sigma-input"
                    type="number"
                    step="0.1"
                    min="0"
                    max="10"
                    value={alertSettingsDraft.thresholds.review_prompt_reason_ctr_drop_sigma ?? ''}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, thresholds: { ...prev.thresholds, review_prompt_reason_ctr_drop_sigma: parseFloat(e.target.value) || 0 } }))}
                    className="w-full text-xs border border-gray-200 rounded-md px-2 py-1.5 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none"
                  />
                </div>
              </div>
              <div className="flex items-center gap-4 pt-2 border-t border-gray-200">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={alertSettingsDraft.expiration.enabled || false}
                    onChange={e => setAlertSettingsDraft(prev => ({ ...prev, expiration: { ...prev.expiration, enabled: e.target.checked } }))}
                    className="w-3.5 h-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-500"
                  />
                  <span className="text-[11px] text-gray-600 font-medium">Auto-acknowledge after</span>
                </label>
                <input
                  type="number"
                  min="1"
                  max="365"
                  value={alertSettingsDraft.expiration.days ?? 7}
                  onChange={e => setAlertSettingsDraft(prev => ({ ...prev, expiration: { ...prev.expiration, days: parseInt(e.target.value) || 7 } }))}
                  disabled={!alertSettingsDraft.expiration.enabled}
                  className="w-16 text-xs border border-gray-200 rounded-md px-2 py-1 bg-white focus:ring-1 focus:ring-violet-300 focus:border-violet-300 outline-none disabled:opacity-40"
                />
                <span className="text-[11px] text-gray-500">days</span>
              </div>
            </div>
          )}

          {notifPrefsOpen && notifPrefs && (
            <div className="mb-4 p-4 rounded-xl bg-gray-50 border border-gray-200">
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-xs font-semibold text-gray-700">Notification Preferences</h4>
                {notifPrefsSaving && <span className="text-[10px] text-violet-500 font-medium">Saving...</span>}
              </div>

              <div className="flex items-center gap-6 mb-3 pb-3 border-b border-gray-200">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={notifPrefs.sound_enabled ?? true}
                    onChange={e => saveNotifPrefs({ sound_enabled: e.target.checked })}
                    className="w-3.5 h-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-500"
                  />
                  <span className="text-[11px] text-gray-600 font-medium">Sound Enabled</span>
                </label>
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={notifPrefs.push_enabled ?? false}
                    onChange={async (e) => {
                      const enabled = e.target.checked;
                      if (enabled && !pushNotif.subscribed) {
                        const success = await pushNotif.subscribe();
                        if (success === false) return;
                      }
                      if (!enabled && pushNotif.subscribed) {
                        await pushNotif.unsubscribe();
                      }
                      saveNotifPrefs({ push_enabled: enabled });
                    }}
                    className="w-3.5 h-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-500"
                  />
                  <span className="text-[11px] text-gray-600 font-medium">Push Enabled</span>
                </label>
                {/* Task #348: opt-out toggle for the deep-scan failing-URL
                    CSV email. Default ON in the backend defaults so admins
                    receive the email automatically; this exposes a way to
                    opt out without editing the database. */}
                <label className="flex items-center gap-2 cursor-pointer" data-testid="notif-prefs-email-failing-csv">
                  <input
                    type="checkbox"
                    checked={notifPrefs.email_failing_csv_enabled ?? true}
                    onChange={e => saveNotifPrefs({ email_failing_csv_enabled: e.target.checked })}
                    className="w-3.5 h-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-500"
                  />
                  <span className="text-[11px] text-gray-600 font-medium">
                    Email failing-URL CSV after deep scan
                  </span>
                </label>
                {/* Task #473: opt-out toggle for the daily SEO auto-publish
                    summary email added in Task #465. Default ON server-side
                    so opted-in admins get the digest after every scheduled
                    auto-publish run; this lets them turn it off without
                    hitting the API directly. */}
                <label className="flex items-center gap-2 cursor-pointer" data-testid="notif-prefs-email-seo-daily-summary">
                  <input
                    type="checkbox"
                    checked={notifPrefs.email_seo_daily_summary_enabled ?? true}
                    onChange={e => saveNotifPrefs({ email_seo_daily_summary_enabled: e.target.checked })}
                    className="w-3.5 h-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-500"
                  />
                  <span className="text-[11px] text-gray-600 font-medium">
                    Email me the daily SEO auto-publish summary
                  </span>
                </label>
              </div>

              {/* Task #476 — Cloudflare Workers KV health panel.
                  Shows per-binding daily counters vs quota with a colored
                  status pill so admins notice quota pressure before pages
                  start failing and the analytics beacon drops. The edge
                  worker auto-falls-back to the Cache API + an in-memory
                  write queue when KV throws, so a "warning" or
                  "exhausted" state means traffic is still being served —
                  but writes are queued and will replay once the quota
                  resets at 00:00 UTC. */}
              {/* Task #470 — Latest CI build status. Shows the latest
                  GitHub Actions run for the backend + frontend gates
                  with a colored pill (green=success, red=failure,
                  amber=in progress) and the run age so the on-call
                  admin sees red CI without leaving the app. */}
              <div className="mb-3 pb-3 border-b border-gray-200" data-testid="notif-prefs-ci-status">
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-[10px] text-gray-500 font-medium">
                    CI build status (latest on {ciStatus?.branch || 'main'})
                  </label>
                  {ciStatus?.repo && (
                    <span className="text-[10px] text-gray-400">{ciStatus.repo}</span>
                  )}
                </div>
                {ciStatus === null ? (
                  <div className="text-[10px] text-gray-400">Loading…</div>
                ) : ciStatus.configured === false ? (
                  <div className="text-[10px] text-gray-400" data-testid="notif-prefs-ci-status-unconfigured">
                    CI status not available{ciStatus.reason ? ` — ${ciStatus.reason}` : ''}.
                    Set <code className="font-mono">GITHUB_REPO</code> (and
                    optionally <code className="font-mono">GITHUB_TOKEN</code>
                    {' '}for private repos) to surface the latest workflow
                    runs here.
                  </div>
                ) : (
                  <ul className="space-y-1.5" data-testid="notif-prefs-ci-status-runs">
                    {Object.entries(ciStatus.runs || {}).map(([wf, run]) => {
                      if (!run) {
                        return (
                          <li
                            key={wf}
                            className="text-[11px] text-gray-500 flex items-center justify-between"
                            data-testid={`notif-prefs-ci-status-row-${wf}`}
                          >
                            <span className="font-medium">{wf}</span>
                            <span className="text-[9px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded ring-1 bg-gray-100 text-gray-600 ring-gray-200">
                              no runs
                            </span>
                          </li>
                        );
                      }
                      const inProgress = run.status !== 'completed';
                      const ok = !inProgress && run.conclusion === 'success';
                      const pillCls = inProgress
                        ? 'bg-amber-100 text-amber-700 ring-amber-200'
                        : ok
                          ? 'bg-emerald-100 text-emerald-700 ring-emerald-200'
                          : 'bg-red-100 text-red-700 ring-red-200';
                      const label = inProgress
                        ? (run.status || 'running')
                        : (run.conclusion || 'unknown');
                      const ageStr = (() => {
                        const a = run.age_seconds;
                        if (a == null) return '';
                        if (a < 60) return `${a}s ago`;
                        if (a < 3600) return `${Math.round(a / 60)}m ago`;
                        if (a < 86400) return `${Math.round(a / 3600)}h ago`;
                        return `${Math.round(a / 86400)}d ago`;
                      })();
                      return (
                        <li
                          key={wf}
                          className="text-[11px] text-gray-700"
                          data-testid={`notif-prefs-ci-status-row-${wf}`}
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-medium">{wf}</span>
                            <span className={`text-[9px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded ring-1 ${pillCls}`}>
                              {label}
                            </span>
                          </div>
                          <div className="text-[10px] text-gray-500 mt-0.5 flex items-center justify-between">
                            <span>
                              #{run.run_number} · {run.head_sha} · {run.event} · {ageStr}
                            </span>
                            <div className="flex items-center gap-2">
                              {!inProgress && run.conclusion !== 'success' && run.id && (
                                <button
                                  onClick={() => handleCiRerun(run.id, true)}
                                  disabled={ciRerunning === run.id}
                                  className="text-[9px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded ring-1 bg-amber-50 text-amber-700 ring-amber-200 hover:bg-amber-100 disabled:opacity-50 disabled:cursor-not-allowed"
                                  title="Re-run failed jobs only"
                                >
                                  {ciRerunning === run.id ? 're-running…' : 're-run'}
                                </button>
                              )}
                              {run.html_url && (
                                <a
                                  href={run.html_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-blue-600 hover:underline"
                                >
                                  view run →
                                </a>
                              )}
                            </div>
                          </div>
                        </li>
                      );
                    })}
                    {ciStatus.error && (
                      <li className="text-[10px] text-amber-700" data-testid="notif-prefs-ci-status-error">
                        CI status temporarily unavailable — {ciStatus.error}.
                      </li>
                    )}
                  </ul>
                )}
              </div>

              {/* Task #689 — Cached Gemini health probe state. Surfaces
                  the periodic probe (Task #677) result without grepping
                  logs and without spending a Vertex API call on every
                  dashboard refresh. */}
              <div className="mb-3 pb-3 border-b border-gray-200" data-testid="notif-prefs-vertex-probe">
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-[10px] text-gray-500 font-medium">
                    Gemini upstream — periodic health probe
                  </label>
                  {vertexProbe?.last_check_ts ? (
                    <span className="text-[10px] text-gray-400" data-testid="notif-prefs-vertex-probe-checked">
                      checked {new Date(vertexProbe.last_check_ts * 1000).toLocaleTimeString()}
                    </span>
                  ) : null}
                </div>
                {vertexProbe === null ? (
                  <div className="text-[10px] text-gray-400">Loading…</div>
                ) : (() => {
                  const status = vertexProbe.status || 'unknown';
                  const pillCls =
                    status === 'ok' ? 'bg-emerald-100 text-emerald-700 ring-emerald-200'
                    : status === 'unhealthy' ? 'bg-red-100 text-red-700 ring-red-200'
                    : status === 'stale' ? 'bg-amber-100 text-amber-700 ring-amber-200'
                    : 'bg-gray-100 text-gray-600 ring-gray-200';
                  const cf = vertexProbe.consecutive_failures || 0;
                  const ageS = typeof vertexProbe.age_s === 'number' ? vertexProbe.age_s : null;
                  const fmtAge = (s) => {
                    if (s == null) return '—';
                    if (s < 60) return `${Math.round(s)}s ago`;
                    if (s < 3600) return `${Math.round(s / 60)}m ago`;
                    return `${(s / 3600).toFixed(1)}h ago`;
                  };
                  return (
                    <div>
                      <div className="flex items-center gap-2 mb-1">
                        <span
                          className={`text-[9px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded ring-1 ${pillCls}`}
                          data-testid="notif-prefs-vertex-probe-status"
                        >
                          {status}
                        </span>
                        <span className="text-[11px] text-gray-600" data-testid="notif-prefs-vertex-probe-age">
                          last probe {fmtAge(ageS)}
                          {vertexProbe.source ? ` (${vertexProbe.source})` : ''}
                        </span>
                        {cf > 0 && (
                          <span
                            className="text-[10px] font-semibold text-red-700"
                            data-testid="notif-prefs-vertex-probe-consecutive"
                          >
                            {cf} consecutive failure{cf === 1 ? '' : 's'}
                          </span>
                        )}
                      </div>
                      <div className="grid grid-cols-2 gap-1 text-[10px] text-gray-500">
                        <div>
                          auth: <span className="font-mono text-gray-700">{vertexProbe.auth_mode || '—'}</span>
                        </div>
                        <div>
                          via CF gateway:{' '}
                          <span className="font-mono text-gray-700">
                            {vertexProbe.via_cf_gateway === true ? 'yes'
                              : vertexProbe.via_cf_gateway === false ? 'no' : '—'}
                          </span>
                        </div>
                        <div className="col-span-2 text-[10px] text-gray-400">
                          probe interval {vertexProbe.probe_interval_s || '—'}s · stale after {vertexProbe.ttl_s || '—'}s
                        </div>
                      </div>
                      {vertexProbe.reason && status !== 'ok' && (
                        <div
                          className="text-[10px] text-red-700 mt-1 break-words"
                          data-testid="notif-prefs-vertex-probe-reason"
                        >
                          Last failure: {vertexProbe.reason}
                        </div>
                      )}
                      {status === 'unknown' && !vertexProbe.last_check_ts && (
                        <div className="text-[10px] text-gray-500 mt-1">
                          The startup probe has not completed yet — refresh in a few seconds.
                        </div>
                      )}
                    </div>
                  );
                })()}
              </div>

              <div className="mb-3 pb-3 border-b border-gray-200" data-testid="notif-prefs-kv-health">
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-[10px] text-gray-500 font-medium">
                    Cloudflare Workers KV — daily usage (UTC)
                  </label>
                  {kvHealth?.snapshot?.utcDay && (
                    <span className="text-[10px] text-gray-400">{kvHealth.snapshot.utcDay}</span>
                  )}
                </div>
                {kvHealth === null ? (
                  <div className="text-[10px] text-gray-400">Loading…</div>
                ) : kvHealth.configured === false || !kvHealth.snapshot ? (
                  <div className="text-[10px] text-gray-400" data-testid="notif-prefs-kv-health-unconfigured">
                    KV usage telemetry not available{kvHealth.reason ? ` — ${kvHealth.reason}` : ''}.
                    The edge worker will still serve cached reads and queue
                    writes during a KV outage; this panel just won't show
                    live counters until the edge is wired up.
                  </div>
                ) : (
                  <ul className="space-y-1.5">
                    {(kvHealth.snapshot.bindings || []).map((b) => {
                      const pillCls =
                        b.status === 'exhausted' ? 'bg-red-100 text-red-700 ring-red-200'
                        : b.status === 'warning' ? 'bg-amber-100 text-amber-700 ring-amber-200'
                        : 'bg-emerald-100 text-emerald-700 ring-emerald-200';
                      const ops = ['read', 'write', 'list', 'delete'];
                      return (
                        <li
                          key={b.binding}
                          className="text-[11px] text-gray-700"
                          data-testid={`notif-prefs-kv-health-row-${b.binding}`}
                        >
                          <div className="flex items-center justify-between">
                            <span className="font-medium">{b.binding}</span>
                            <span className={`text-[9px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded ring-1 ${pillCls}`}>
                              {b.status}
                            </span>
                          </div>
                          <div className="grid grid-cols-4 gap-1 mt-1 text-[10px] text-gray-500">
                            {ops.map((op) => {
                              const used = b.counters?.[op] ?? 0;
                              const cap = b.quota?.[op] ?? 0;
                              const pct = b.percentages?.[op] ?? 0;
                              const tone =
                                pct >= 100 ? 'text-red-600'
                                : pct >= (kvHealth.snapshot.warningPct || 80) ? 'text-amber-600'
                                : 'text-gray-500';
                              return (
                                <div key={op} className="flex flex-col">
                                  <span className="uppercase text-[9px]">{op}</span>
                                  <span className={`tabular-nums ${tone}`}>
                                    {used.toLocaleString()}/{cap.toLocaleString()} ({pct.toFixed(1)}%)
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                          {b.fallbackActive && (
                            <div className="text-[10px] text-amber-700 mt-1" data-testid={`notif-prefs-kv-health-fallback-${b.binding}`}>
                              Fallback active — serving recent reads from the Cache API and queueing writes in memory.
                            </div>
                          )}
                          {b.lastAlertFired && (
                            <div className="text-[10px] text-gray-500 mt-1" data-testid={`notif-prefs-kv-health-last-alert-${b.binding}`}>
                              Last alert fired: {b.lastAlertFired.severity} on {b.lastAlertFired.op} at {new Date(b.lastAlertFired.at).toLocaleString()}
                            </div>
                          )}
                          {/* Task #510 — per-isolate breakdown. The
                              edge worker now sums CF_EDGE_CACHE
                              counters across every isolate; expanding
                              this row shows which isolate(s) are
                              hottest so an operator can see if a
                              single rogue isolate is driving burn. */}
                          {Array.isArray(b.isolates) && b.isolates.length > 0 && (() => {
                            const expanded = !!kvExpandedIsolates[b.binding];
                            return (
                              <div className="mt-1.5">
                                <button
                                  type="button"
                                  onClick={() =>
                                    setKvExpandedIsolates((prev) => ({
                                      ...prev,
                                      [b.binding]: !prev[b.binding],
                                    }))
                                  }
                                  className="text-[10px] text-blue-600 hover:underline"
                                  data-testid={`notif-prefs-kv-health-isolates-toggle-${b.binding}`}
                                >
                                  {expanded ? '▾' : '▸'} by isolate ({b.isolates.length})
                                </button>
                                {expanded && (
                                  <ul
                                    className="mt-1 ml-3 space-y-0.5"
                                    data-testid={`notif-prefs-kv-health-isolates-list-${b.binding}`}
                                  >
                                    {b.isolates.map((iso) => {
                                      const r = iso.counters?.read ?? 0;
                                      const w = iso.counters?.write ?? 0;
                                      const l = iso.counters?.list ?? 0;
                                      const d = iso.counters?.delete ?? 0;
                                      // Task #543 — shorten the worker-side
                                      // isolate UUID (e.g. crypto.randomUUID())
                                      // to "aaaaaaaa…last4" so the row stays
                                      // legible at the panel's 10px font and
                                      // doesn't leak the full identifier into
                                      // a screen-share or screenshot. Short
                                      // IDs (≤12 chars) render unchanged so
                                      // dev/test fixtures stay readable.
                                      const fullId = String(iso.id ?? '');
                                      const shortId = fullId.length > 12
                                        ? `${fullId.slice(0, 8)}…${fullId.slice(-4)}`
                                        : fullId;
                                      return (
                                        <li
                                          key={iso.id}
                                          className="text-[10px] text-gray-600 tabular-nums flex items-center justify-between gap-2"
                                          data-testid={`notif-prefs-kv-health-isolate-${b.binding}-${iso.id}`}
                                        >
                                          <span
                                            className="font-mono text-gray-500"
                                            title={fullId}
                                            data-testid={`notif-prefs-kv-health-isolate-${b.binding}-${iso.id}-id`}
                                          >{shortId}</span>
                                          <span>
                                            r {r.toLocaleString()} · w {w.toLocaleString()} · l {l.toLocaleString()} · d {d.toLocaleString()}
                                          </span>
                                        </li>
                                      );
                                    })}
                                  </ul>
                                )}
                              </div>
                            );
                          })()}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>

              {/* Task #897 — bot HTML cache rolling-hour hit rate.
                  Extracted to <BotCachePanel> so the panel logic +
                  sparkline geometry can be unit-tested without
                  rendering the whole admin dashboard. */}
              <BotCachePanel kvHealth={kvHealth} />

              {/* Task #571 — AI-input + per-layer cache hit-ratio
                  panel. Polls /api/health/cache every 60s and shows
                  per-content-type ratios + miss-reason ranking + L1
                  saturation. Renders red when any content-type
                  drops below the same floor the
                  `cache-ai-hitratio-low` CloudWatch alarm uses. */}
              <CacheHitRatioPanel adminToken={adminToken} />

              {/* Task #315 — R2 cold-storage / Logpush watchdog snapshot
                  (Task #314 watchdog persists state to KV; this surfaces
                  it so an operator can confirm the rules are working
                  between monthly cron ticks). */}
              <R2ColdStoragePanel
                r2Health={r2Health}
                reevaluating={r2Reevaluating}
                resettingWatchdog={r2ResettingWatchdog}
                onResetWatchdog={async () => {
                  // Task #322 — clear `consecutive_query_failures` +
                  // `query_fail_last_fired_at` after the operator has
                  // rotated R2_STORAGE_ANALYTICS_TOKEN. The worker
                  // returns the resulting state inline so the badge
                  // disappears immediately without a follow-up GET.
                  if (r2ResettingWatchdog) return;
                  setR2ResettingWatchdog(true);
                  try {
                    const res = await axios.post(
                      `${API_BASE}/admin/r2-storage-health/reset-watchdog`,
                      null,
                      adminHdr(adminToken),
                    );
                    if (res.data?.state) {
                      setR2Health((prev) => ({
                        ...(prev || { configured: true }),
                        configured: true,
                        state: res.data.state,
                      }));
                    }
                    toast.success('R2 watchdog-blind counter reset');
                  } catch (e) {
                    toast.error('Reset failed');
                    log.error('R2 watchdog reset failed', { error: e?.message });
                  } finally {
                    setR2ResettingWatchdog(false);
                  }
                }}
                onReevaluate={async () => {
                  if (r2Reevaluating) return;
                  setR2Reevaluating(true);
                  try {
                    const runRes = await axios.post(
                      `${API_BASE}/admin/r2-storage-health/run`,
                      null,
                      adminHdr(adminToken),
                    );
                    // The worker returns the fresh state inline so the
                    // tile updates immediately without a follow-up GET.
                    if (runRes.data?.state) {
                      setR2Health((prev) => ({
                        ...(prev || { configured: true }),
                        configured: true,
                        state: runRes.data.state,
                        // Keep the prior config metadata if the run
                        // response didn't echo it back.
                        buckets: runRes.data.result?.buckets || prev?.buckets,
                        logpush_cap_gb:
                          runRes.data.result?.logpush_cap_gb ??
                          prev?.logpush_cap_gb,
                        rules_age_days:
                          runRes.data.result?.rules_age_days ??
                          prev?.rules_age_days,
                      }));
                    }
                    // The worker returns 200 even when the underlying
                    // run was skipped (no KV binding, GraphQL query
                    // failed, etc) — surface that honestly so the
                    // operator doesn't see a green toast for a no-op.
                    const r = runRes.data?.result;
                    if (r && r.ok === false) {
                      toast.error(
                        `Re-evaluate skipped: ${r.reason || 'unknown reason'}`,
                      );
                    } else if (r && r.skipped) {
                      toast.message(
                        `Re-evaluate skipped: ${r.reason || 'no work to do'}`,
                      );
                    } else {
                      toast.success('R2 cold-storage watchdog re-evaluated');
                    }
                  } catch (e) {
                    const status = e?.response?.status;
                    const detail = e?.response?.data?.detail;
                    if (status === 429) {
                      const retry =
                        (typeof detail === 'object' && detail?.retry_after_seconds) ||
                        '?';
                      toast.error(`Cooldown — try again in ${retry}s`);
                    } else {
                      toast.error('Re-evaluate failed');
                      log.error('R2 re-evaluate failed', { error: e?.message });
                    }
                  } finally {
                    setR2Reevaluating(false);
                  }
                }}
              />

              {/* Task #474 — recent SEO summary dispatch history. */}
              <div className="mb-3 pb-3 border-b border-gray-200" data-testid="notif-prefs-seo-summary-history">
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-[10px] text-gray-500 font-medium">
                      Recent SEO summary email dispatches
                  </label>
                </div>
                {seoSummaryDispatches === null ? (
                  <div className="text-[10px] text-gray-400">Loading…</div>
                ) : seoSummaryDispatches.length === 0 ? (
                  <div className="text-[10px] text-gray-400" data-testid="notif-prefs-seo-summary-history-empty">
                    No scheduled auto-publish runs have completed yet — once one does, the dispatch result will appear here.
                  </div>
                ) : (
                  <ul className="space-y-1">
                    {seoSummaryDispatches.map((d, i) => {
                      const at = d.at ? new Date(d.at) : null;
                      const ageMs = at ? Date.now() - at.getTime() : null;
                      const ageStr = ageMs == null
                        ? '—'
                        : ageMs < 60_000 ? 'just now'
                        : ageMs < 3_600_000 ? `${Math.round(ageMs / 60_000)}m ago`
                        : ageMs < 86_400_000 ? `${Math.round(ageMs / 3_600_000)}h ago`
                        : `${Math.round(ageMs / 86_400_000)}d ago`;
                      const sent = d.sent ?? 0;
                      const failed = d.failed ?? 0;
                      const totalRecipients = d.total_recipients ?? (sent + failed);
                      const suppressed = d.suppressed_quiet_hours ?? 0;
                      const optedOut = d.opted_out ?? 0;
                      const errs = Array.isArray(d.errors) ? d.errors : [];
                      const ok = failed === 0 && (sent > 0 || (suppressed === 0 && optedOut === 0 && !d.reason));
                      const dot = failed > 0 ? 'bg-red-500'
                        : sent > 0 ? 'bg-emerald-500'
                        : 'bg-gray-300';
                      return (
                        <li
                          key={`${d.job_id}-${i}`}
                          className="flex items-start gap-2 text-[11px] text-gray-600"
                          data-testid={`notif-prefs-seo-summary-history-row-${i}`}
                        >
                          <span className={`inline-block w-1.5 h-1.5 rounded-full mt-1.5 ${dot}`} />
                          <div className="flex-1 min-w-0">
                            <div>
                              <span className="font-medium text-gray-700">{ageStr}</span>
                              <span className="text-gray-400"> · attempted </span>
                              <span className="font-medium text-gray-700">{totalRecipients}</span>
                              <span className="text-gray-400">/{d.total_admins ?? '?'} admins</span>
                              {sent > 0 && (
                                <span className="text-emerald-600"> · {sent} delivered</span>
                              )}
                              {failed > 0 && (
                                <span className="text-red-500"> · {failed} failed</span>
                              )}
                              {suppressed > 0 && (
                                <span className="text-amber-600"> · {suppressed} in quiet hours</span>
                              )}
                              {optedOut > 0 && (
                                <span className="text-gray-400"> · {optedOut} opted out</span>
                              )}
                            </div>
                            {(!ok && d.reason) && (
                              <div className="text-[10px] text-gray-400 mt-0.5">
                                Reason: <code>{d.reason}</code>
                              </div>
                            )}
                            {errs.length > 0 && (
                              <ul
                                className="mt-1 space-y-0.5"
                                data-testid={`notif-prefs-seo-summary-history-row-${i}-errors`}
                              >
                                {errs.map((e, ei) => (
                                  <li key={ei} className="text-[10px] text-red-500 truncate">
                                    <span className="font-medium">{e.email || e.admin_id || 'unknown'}</span>
                                    {e.error ? <span className="text-gray-500"> — {e.error}</span> : null}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>

              {/* Task #473: per-admin UTC quiet-hours window (consumed by
                  _quiet_hours_active in seo_engine.py). Either bound left
                  blank disables the window. Window may wrap across UTC
                  midnight (e.g. start=22, end=6 silences 22:00–06:00 UTC). */}
              <div className="mb-3 pb-3 border-b border-gray-200" data-testid="notif-prefs-quiet-hours">
                <div className="flex items-center justify-between mb-1.5">
                  <label className="text-[10px] text-gray-500 font-medium">
                    Quiet Hours (UTC) — pause non-critical emails in this window
                  </label>
                  {(notifPrefs.quiet_hours_start_utc != null || notifPrefs.quiet_hours_end_utc != null) && (
                    <button
                      onClick={() => saveNotifPrefs({ quiet_hours_start_utc: null, quiet_hours_end_utc: null })}
                      className="text-[10px] text-gray-400 hover:text-violet-600 font-medium"
                      data-testid="notif-prefs-quiet-hours-clear"
                    >
                      Clear
                    </button>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] text-gray-500">From</span>
                    <select
                      value={notifPrefs.quiet_hours_start_utc ?? ''}
                      onChange={e => {
                        const raw = e.target.value;
                        saveNotifPrefs({ quiet_hours_start_utc: raw === '' ? null : parseInt(raw, 10) });
                      }}
                      className="text-[11px] px-2 py-1 rounded-md border border-gray-200 bg-white text-gray-700 focus:ring-1 focus:ring-violet-400 focus:border-violet-400"
                      data-testid="notif-prefs-quiet-hours-start"
                    >
                      <option value="">—</option>
                      {Array.from({ length: 24 }, (_, h) => (
                        <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>
                      ))}
                    </select>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span className="text-[10px] text-gray-500">To</span>
                    <select
                      value={notifPrefs.quiet_hours_end_utc ?? ''}
                      onChange={e => {
                        const raw = e.target.value;
                        saveNotifPrefs({ quiet_hours_end_utc: raw === '' ? null : parseInt(raw, 10) });
                      }}
                      className="text-[11px] px-2 py-1 rounded-md border border-gray-200 bg-white text-gray-700 focus:ring-1 focus:ring-violet-400 focus:border-violet-400"
                      data-testid="notif-prefs-quiet-hours-end"
                    >
                      <option value="">—</option>
                      {Array.from({ length: 24 }, (_, h) => (
                        <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>
                      ))}
                    </select>
                  </div>
                  {notifPrefs.quiet_hours_start_utc != null && notifPrefs.quiet_hours_end_utc != null && (
                    notifPrefs.quiet_hours_start_utc === notifPrefs.quiet_hours_end_utc ? (
                      // Backend (_quiet_hours_active) treats start == end as
                      // an inactive window, not a 24-hour silence.
                      <span className="text-[10px] text-amber-500">
                        Inactive — start and end hour are the same
                      </span>
                    ) : (
                      <span className="text-[10px] text-gray-400">
                        Active {String(notifPrefs.quiet_hours_start_utc).padStart(2, '0')}:00–{String(notifPrefs.quiet_hours_end_utc).padStart(2, '0')}:00 UTC
                        {notifPrefs.quiet_hours_start_utc > notifPrefs.quiet_hours_end_utc && ' (wraps midnight)'}
                      </span>
                    )
                  )}
                </div>
              </div>

              <div className="mb-3">
                <label className="text-[10px] text-gray-500 font-medium block mb-1.5">Chime Tone</label>
                <div className="flex items-center gap-2 flex-wrap">
                  {Object.entries(CHIME_TONES).map(([key, tone]) => (
                    <button
                      key={key}
                      onClick={() => { playAlertChime(key); saveNotifPrefs({ chime_tone: key }); }}
                      className={`text-[10px] px-2.5 py-1 rounded-md border transition-colors font-medium ${
                        chimeTone === key
                          ? 'bg-violet-100 text-violet-700 border-violet-300'
                          : 'bg-white text-gray-500 border-gray-200 hover:bg-violet-50 hover:text-violet-600'
                      }`}
                    >
                      {tone.label}
                    </button>
                  ))}
                  {notifPrefs?.custom_chime_url && (
                    <button
                      onClick={() => { playAlertChime('custom'); saveNotifPrefs({ chime_tone: 'custom' }); }}
                      className={`text-[10px] px-2.5 py-1 rounded-md border transition-colors font-medium flex items-center gap-1 ${
                        chimeTone === 'custom'
                          ? 'bg-violet-100 text-violet-700 border-violet-300'
                          : 'bg-white text-gray-500 border-gray-200 hover:bg-violet-50 hover:text-violet-600'
                      }`}
                    >
                      <Music size={10} /> Custom
                    </button>
                  )}
                </div>
                <div className="mt-2">
                  {notifPrefs?.custom_chime_url ? (
                    <div className="flex items-center gap-2 text-[10px]">
                      <Music size={10} className="text-violet-500" />
                      <span className="text-gray-600 truncate max-w-[140px]">{notifPrefs.custom_chime_filename || 'Custom chime'}</span>
                      <button
                        onClick={() => playAlertChime('custom')}
                        className="text-violet-600 hover:text-violet-700 font-medium"
                      >
                        Preview
                      </button>
                      <button
                        onClick={handleDeleteCustomChime}
                        className="text-red-400 hover:text-red-600 ml-1"
                        title="Remove custom chime"
                      >
                        <Trash2 size={10} />
                      </button>
                    </div>
                  ) : pendingChimeFile ? (
                    <AudioTrimPreview
                      file={pendingChimeFile}
                      onConfirm={handleChimeUploadConfirm}
                      onCancel={() => setPendingChimeFile(null)}
                      uploading={chimeUploading}
                    />
                  ) : (
                    <label className="inline-flex items-center gap-1.5 text-[10px] text-violet-600 hover:text-violet-700 cursor-pointer font-medium">
                      <Upload size={10} />
                      Upload custom sound
                      <input
                        ref={chimeFileInputRef}
                        type="file"
                        accept=".mp3,.wav"
                        className="hidden"
                        onChange={handleChimeFileSelect}
                        disabled={chimeUploading}
                      />
                    </label>
                  )}
                  {!pendingChimeFile && <p className="text-[9px] text-gray-400 mt-0.5">MP3 or WAV, max 500 KB</p>}
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1.5">Sound Alerts For</label>
                  <div className="space-y-1.5">
                    {Object.entries(ALERT_SEVERITY_LABELS).map(([key, label]) => (
                      <label key={key} className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={(notifPrefs.sound_severities || []).includes(key)}
                          onChange={e => {
                            const current = notifPrefs.sound_severities || [];
                            const next = e.target.checked ? [...current, key] : current.filter(s => s !== key);
                            saveNotifPrefs({ sound_severities: next });
                          }}
                          className="w-3.5 h-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-500"
                        />
                        <span className="text-[11px] text-gray-600">{label}</span>
                      </label>
                    ))}
                  </div>
                </div>
                <div>
                  <label className="text-[10px] text-gray-500 font-medium block mb-1.5">Push Alerts For</label>
                  <div className="space-y-1.5">
                    {Object.entries(ALERT_SEVERITY_LABELS).map(([key, label]) => (
                      <label key={key} className="flex items-center gap-2 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={(notifPrefs.push_severities || []).includes(key)}
                          onChange={e => {
                            const current = notifPrefs.push_severities || [];
                            const next = e.target.checked ? [...current, key] : current.filter(s => s !== key);
                            saveNotifPrefs({ push_severities: next });
                          }}
                          className="w-3.5 h-3.5 rounded border-gray-300 text-violet-600 focus:ring-violet-500"
                        />
                        <span className="text-[11px] text-gray-600">{label}</span>
                      </label>
                    ))}
                  </div>
                </div>
              </div>

              <div className="flex items-center justify-between mt-3 pt-2 border-t border-gray-200">
                <button
                  onClick={() => saveNotifPrefs(notifPrefs.defaults || {})}
                  className="text-[10px] px-2 py-0.5 rounded bg-white border border-gray-200 text-gray-500 hover:bg-gray-100 transition-colors"
                >
                  Reset to Defaults
                </button>
              </div>

              {pushDeliverySummary && (
                <div className="mt-3 pt-3 border-t border-gray-200">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">Push Delivery (7d)</span>
                    <button
                      onClick={() => onNavigate && onNavigate('notifications')}
                      className="text-[10px] text-violet-600 hover:text-violet-700 font-medium"
                    >
                      View Details
                    </button>
                  </div>
                  <div className="grid grid-cols-4 gap-2">
                    <div className="text-center">
                      <p className="text-sm font-bold text-gray-900">{pushDeliverySummary.total_dispatches}</p>
                      <p className="text-[9px] text-gray-400">Dispatches</p>
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-bold text-emerald-600">{pushDeliverySummary.total_sent}</p>
                      <p className="text-[9px] text-gray-400">Sent</p>
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-bold text-red-500">{pushDeliverySummary.total_failed}</p>
                      <p className="text-[9px] text-gray-400">Failed</p>
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-bold text-amber-500">{pushDeliverySummary.total_expired}</p>
                      <p className="text-[9px] text-gray-400">Expired</p>
                    </div>
                  </div>
                </div>
              )}

              {pushChannelStatus && (() => {
                // Task #434 — surface the same per-channel last_success_at /
                // last_error that Bot Security → Alert Settings shows.
                // Task #442 — staleness/degraded math is extracted to
                // pushChannelTone() in src/utils/ for unit testing.
                const lastSuccess = pushChannelStatus.last_success_at;
                const lastError = pushChannelStatus.last_error;
                const { tone: toneKey, degraded } = pushChannelTone({
                  last_success_at: lastSuccess,
                  last_error: lastError,
                  last_attempt_at: pushChannelStatus.last_attempt_at,
                });
                const tone = toneKey === 'degraded'
                  ? 'bg-red-50 border-red-200 text-red-700'
                  : toneKey === 'healthy'
                    ? 'bg-emerald-50 border-emerald-200 text-emerald-700'
                    : 'bg-gray-50 border-gray-200 text-gray-500';
                const fmtRel = (iso) => {
                  if (!iso) return 'never';
                  const ms = Date.now() - new Date(iso).getTime();
                  const s = Math.round(ms / 1000);
                  if (s < 60) return `${s}s ago`;
                  const m = Math.round(s / 60);
                  if (m < 60) return `${m}m ago`;
                  const h = Math.round(m / 60);
                  if (h < 24) return `${h}h ago`;
                  return `${Math.round(h / 24)}d ago`;
                };
                return (
                  <button
                    type="button"
                    onClick={() => onNavigate && onNavigate('botsecurity', { panel: 'alert-settings', channel: 'push' })}
                    title="Open Bot Security → Alert Settings"
                    data-testid="dashboard-push-channel-health"
                    className={`mt-3 w-full text-left rounded-lg border px-3 py-2 transition-colors hover:opacity-90 ${tone}`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0">
                        <Smartphone size={12} className="shrink-0 opacity-70" />
                        <span className="text-[11px] font-semibold">Browser push pipeline</span>
                      </div>
                      <span className={`text-[9px] uppercase tracking-wider font-bold px-1.5 py-0.5 rounded ${
                        degraded ? 'bg-red-600 text-white' : (lastSuccess ? 'bg-emerald-600 text-white' : 'bg-gray-400 text-white')
                      }`} data-testid="dashboard-push-channel-badge">
                        {degraded ? 'Degraded' : (lastSuccess ? 'Healthy' : 'Idle')}
                      </span>
                    </div>
                    <p className="text-[10px] mt-1 opacity-90">
                      Last success: {lastSuccess ? `${fmtRel(lastSuccess)} (${new Date(lastSuccess).toLocaleString()})` : 'never'}
                    </p>
                    {lastError && (
                      <p className="text-[10px] mt-0.5 truncate" title={lastError}>
                        Last error: {lastError}
                      </p>
                    )}
                  </button>
                );
              })()}
            </div>
          )}

          {(!alertHistory.alerts || alertHistory.alerts.length === 0) && (
            <p className="text-center text-[11px] text-gray-400 py-6">No alerts have been triggered yet. Alerts appear here when system thresholds are exceeded.</p>
          )}

          {alertHistory.alerts?.length > 0 && (
          <div className="space-y-2 max-h-[400px] overflow-y-auto">
            {alertHistory.alerts
              .filter(a => {
                if (alertFilter === 'unacknowledged' && a.acknowledged) return false;
                if (alertFilter === 'acknowledged' && !a.acknowledged) return false;
                if (alertReasonFilter) {
                  const reasons = Array.isArray(a?.threshold_snapshot?.reasons) ? a.threshold_snapshot.reasons : [];
                  const hit = reasons.some(r => {
                    const name = (r && typeof r === 'object') ? (r.reason ?? '') : String(r ?? '');
                    return name === alertReasonFilter;
                  });
                  if (!hit) return false;
                }
                return true;
              })
              .map((alert) => {
                const severityMap = {
                  high_error_rate: 'red',
                  high_latency: 'yellow',
                  spoofed_bot_surge: 'red',
                  high_fallback_rate: 'yellow',
                  endpoint_down: 'red',
                  auto_block_expired: 'amber',
                };
                const severity = severityMap[alert.type] || 'yellow';
                const isRed = severity === 'red';
                return (
                  <div
                    key={alert._id}
                    className={`flex items-start gap-3 px-3 py-2.5 rounded-lg border text-xs transition-all ${
                      alert.acknowledged
                        ? 'bg-gray-50 border-gray-200 opacity-60'
                        : isRed
                          ? 'bg-red-50 border-red-200'
                          : 'bg-amber-50 border-amber-200'
                    }`}
                  >
                    <div className="mt-0.5 flex-shrink-0">
                      {isRed
                        ? <AlertCircle size={14} className="text-red-500" />
                        : <AlertTriangle size={14} className="text-amber-500" />
                      }
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap mb-0.5">
                        <span className={`font-semibold ${alert.acknowledged ? 'text-gray-500' : isRed ? 'text-red-800' : 'text-amber-800'}`}>
                          {alert.title}
                        </span>
                        <span className={`text-[9px] px-1.5 py-0.5 rounded font-medium ${
                          isRed ? 'bg-red-100 text-red-600' : 'bg-amber-100 text-amber-600'
                        }`}>
                          {isRed ? 'High' : 'Medium'}
                        </span>
                        <span className="text-[9px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 font-medium">
                          {alert.type.replace(/_/g, ' ')}
                        </span>
                        {alert.acknowledged && (
                          <CheckCircle size={12} className="text-emerald-500" />
                        )}
                      </div>
                      <p className={`text-[11px] ${alert.acknowledged ? 'text-gray-400' : 'text-gray-600'} break-words`}>
                        {alert.body}
                      </p>
                      {alert.threshold_snapshot && alert.threshold_snapshot.metric != null && alert.threshold_snapshot.value != null && (
                        <div className={`flex items-center gap-2 mt-1 text-[10px] px-2 py-1 rounded ${alert.acknowledged ? 'bg-gray-100 text-gray-400' : 'bg-white/60 text-gray-500'}`}>
                          <span className="font-medium">Limit:</span>
                          <span>{alert.threshold_snapshot.metric.replace(/_/g, ' ')} &gt; {alert.threshold_snapshot.value}{alert.threshold_snapshot.metric.includes('pct') ? '%' : alert.threshold_snapshot.metric.includes('ms') ? 'ms' : ''}</span>
                          {alert.threshold_snapshot.actual != null && (<>
                            <span className="text-gray-300">|</span>
                            <span className="font-medium">Actual:</span>
                            <span className={alert.acknowledged ? '' : isRed ? 'text-red-600' : 'text-amber-600'}>{alert.threshold_snapshot.actual}{alert.threshold_snapshot.metric.includes('pct') ? '%' : alert.threshold_snapshot.metric.includes('ms') ? 'ms' : ''}</span>
                          </>)}
                        </div>
                      )}
                      <AlertReasonsRow
                        alert={alert}
                        alertReasonFilter={alertReasonFilter}
                        onReasonClick={(name) => setAlertReasonFilter(name)}
                      />
                      <div className="flex items-center gap-3 mt-1.5">
                        <span className="text-[10px] text-gray-400 flex items-center gap-1">
                          <Clock size={10} />
                          {alert.fired_at ? formatTimeAgo(alert.fired_at) : 'unknown'}
                        </span>
                        {alert.fired_at && (
                          <span className="text-[9px] text-gray-300">
                            {new Date(alert.fired_at).toLocaleString()}
                          </span>
                        )}
                      </div>
                    </div>
                    {!alert.acknowledged && (
                      <button
                        onClick={() => handleAcknowledgeAlert(alert._id)}
                        className="flex-shrink-0 text-[10px] px-2 py-1 rounded-md bg-white border border-gray-200 text-gray-500 hover:bg-emerald-50 hover:text-emerald-600 hover:border-emerald-200 transition-colors"
                        title="Acknowledge"
                      >
                        <CheckCircle size={12} />
                      </button>
                    )}
                  </div>
                );
              })}

            {alertHistory.alerts.filter(a => {
              if (alertFilter === 'unacknowledged' && a.acknowledged) return false;
              if (alertFilter === 'acknowledged' && !a.acknowledged) return false;
              if (alertReasonFilter) {
                const reasons = Array.isArray(a?.threshold_snapshot?.reasons) ? a.threshold_snapshot.reasons : [];
                const hit = reasons.some(r => {
                  const name = (r && typeof r === 'object') ? (r.reason ?? '') : String(r ?? '');
                  return name === alertReasonFilter;
                });
                if (!hit) return false;
              }
              return true;
            }).length === 0 && (
              <p className="text-center text-[11px] text-gray-400 py-4">
                No alerts matching this filter
                {alertReasonFilter && (
                  <>
                    {' '}
                    <button
                      type="button"
                      onClick={() => setAlertReasonFilter('')}
                      className="underline text-violet-600 hover:text-violet-700"
                    >
                      Clear reason filter
                    </button>
                  </>
                )}
              </p>
            )}
          </div>
          )}
        </GlassCard>
      )}
      </SectionErrorBoundary>

      <SectionErrorBoundary name="SEO Prewarm Coverage">
      {prewarmCoverage && (
        <GlassCard className="p-5">
          <div className="flex items-center gap-2 mb-4">
            <Search size={16} className="text-orange-500" />
            <h3 className="text-gray-700 font-semibold">SEO Prewarm Coverage</h3>
            {prewarmCoverage.season && (
              <span className={`text-[10px] px-2 py-0.5 rounded-md font-medium ${
                prewarmCoverage.season === 'exam'
                  ? 'bg-red-100 text-red-700 border border-red-200'
                  : prewarmCoverage.season === 'results'
                    ? 'bg-amber-100 text-amber-700 border border-amber-200'
                    : 'bg-gray-100 text-gray-600 border border-gray-200'
              }`}>
                {prewarmCoverage.season}
              </span>
            )}
            {prewarmCoverage.last_run_at && (
              <span className="ml-auto text-[10px] text-gray-400">
                Last run: {new Date(prewarmCoverage.last_run_at).toLocaleString()}
                {prewarmCoverage.duration_s ? ` · ${Math.round(prewarmCoverage.duration_s)}s` : ''}
              </span>
            )}
          </div>
          {!prewarmCoverage.last_run_at ? (
            <div className="text-[11px] text-gray-500 italic">
              No prewarm run recorded yet — the nightly Lambda fires at 01:00 UTC.
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
                <div className="rounded-lg p-3 bg-orange-50 border border-orange-200 text-center">
                  <p className="text-orange-700 font-bold text-lg">{(prewarmCoverage.scanned ?? 0).toLocaleString()}</p>
                  <p className="text-[10px] text-gray-500">Chapters Scanned</p>
                </div>
                <div className="rounded-lg p-3 bg-blue-50 border border-blue-200 text-center">
                  <p className="text-blue-700 font-bold text-lg">{(prewarmCoverage.urls_warmed ?? 0).toLocaleString()}<span className="text-xs text-gray-400">/{(prewarmCoverage.urls_attempted ?? 0).toLocaleString()}</span></p>
                  <p className="text-[10px] text-gray-500">URLs Warmed (combined)</p>
                </div>
                <div className={`rounded-lg p-3 border text-center ${
                  (prewarmCoverage.success_rate ?? 0) >= 0.95 ? 'bg-green-50 border-green-200'
                    : (prewarmCoverage.success_rate ?? 0) >= 0.90 ? 'bg-amber-50 border-amber-200'
                    : 'bg-red-50 border-red-200'
                }`}>
                  <p className={`font-bold text-lg ${
                    (prewarmCoverage.success_rate ?? 0) >= 0.95 ? 'text-green-700'
                      : (prewarmCoverage.success_rate ?? 0) >= 0.90 ? 'text-amber-700'
                      : 'text-red-700'
                  }`}>{((prewarmCoverage.success_rate ?? 0) * 100).toFixed(2)}%</p>
                  <p className="text-[10px] text-gray-500">Combined Success Rate</p>
                </div>
                <div className={`rounded-lg p-3 border text-center ${
                  (prewarmCoverage.kv_success_rate ?? 0) >= 0.95 ? 'bg-green-50 border-green-200'
                    : (prewarmCoverage.kv_success_rate ?? 0) >= 0.90 ? 'bg-amber-50 border-amber-200'
                    : 'bg-red-50 border-red-200'
                }`} title="KV-eligible page-types only (mcqs / flashcards / definitions / summary / pyqs)">
                  <p className={`font-bold text-lg ${
                    (prewarmCoverage.kv_success_rate ?? 0) >= 0.95 ? 'text-green-700'
                      : (prewarmCoverage.kv_success_rate ?? 0) >= 0.90 ? 'text-amber-700'
                      : 'text-red-700'
                  }`}>{((prewarmCoverage.kv_success_rate ?? 0) * 100).toFixed(2)}%</p>
                  <p className="text-[10px] text-gray-500">KV Success Rate <span className="text-gray-400">({(prewarmCoverage.kv_warmed ?? 0).toLocaleString()}/{(prewarmCoverage.kv_attempted ?? 0).toLocaleString()})</span></p>
                </div>
              </div>
              {prewarmCoverage.by_board?.length > 0 && (
                <div className="mb-4">
                  <div className="text-[10px] text-gray-400 font-semibold mb-1.5 uppercase tracking-wider">Per-Board Coverage</div>
                  <div className="space-y-1">
                    {prewarmCoverage.by_board.map((row, i) => (
                      <div key={i} className="flex items-center gap-2 text-[10px] py-1.5 px-3 rounded bg-white border border-gray-200">
                        <span className="text-gray-700 font-medium min-w-[100px]">{row.board}</span>
                        <span className="text-gray-500">warmed <span className="font-mono font-semibold text-gray-700">{(row.warmed ?? 0).toLocaleString()}</span></span>
                        <span className="text-gray-500">failed <span className="font-mono font-semibold text-amber-700">{(row.failed ?? 0).toLocaleString()}</span></span>
                        <span className={`ml-auto font-mono font-semibold ${
                          (row.success_rate ?? 0) >= 0.95 ? 'text-green-700'
                            : (row.success_rate ?? 0) >= 0.90 ? 'text-amber-700'
                            : 'text-red-700'
                        }`}>{((row.success_rate ?? 0) * 100).toFixed(1)}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {prewarmCoverage.samples_failed?.length > 0 && (
                <div>
                  <div className="text-[10px] text-gray-400 font-semibold mb-1.5 uppercase tracking-wider">Recent Failure Samples</div>
                  <div className="space-y-1 max-h-40 overflow-y-auto">
                    {prewarmCoverage.samples_failed.slice(0, 10).map((s, i) => (
                      <div key={i} className="flex items-center gap-2 text-[10px] py-1 px-2 rounded bg-red-50 border border-red-100">
                        {s.kv_eligible && (
                          <span className="text-[9px] px-1 py-0 rounded bg-orange-100 text-orange-700 border border-orange-200 font-semibold">KV</span>
                        )}
                        <span className="text-gray-500 font-mono">{s.status || '—'}</span>
                        <span className="text-gray-700 truncate flex-1" title={s.url}>{s.url}</span>
                        <span className="text-red-700 ml-auto">{s.reason}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </GlassCard>
      )}
      </SectionErrorBoundary>

      <SectionErrorBoundary name="IndexNow Stats">
      {indexNowStats && (
        <GlassCard className="p-5">
          <div className="flex items-center gap-2 mb-4">
            <Search size={16} className="text-green-500" />
            <h3 className="text-gray-700 font-semibold">IndexNow Push Status</h3>
            <button
              onClick={async () => {
                if (resubmittingIndexNow) return;
                setResubmittingIndexNow(true);
                try {
                  const res = await axios.post(`${API_BASE}/admin/indexnow/resubmit-recent`, {}, adminHdr(adminToken));
                  const d = res.data || {};
                  const sd = d.sitemap_diff || {};
                  setResubmitMessage(
                    `Pushed ${d.recent_urls_pushed ?? 0} recent · sitemap diff: ${sd.new_queued ?? 0} new (${sd.sitemap_total ?? 0} total)`
                  );
                  try {
                    const statsRes = await axios.get(`${API_BASE}/admin/indexnow/stats`, adminHdr(adminToken));
                    setIndexNowStats(statsRes.data);
                  } catch (err) {
                    console.warn('AdminDashboard: post-resubmit indexnow stats refresh failed:', err);
                  }
                } catch (e) {
                  setResubmitMessage(`Re-submit failed: ${e?.response?.data?.detail || e.message || 'unknown error'}`);
                } finally {
                  setResubmittingIndexNow(false);
                  setTimeout(() => setResubmitMessage(''), 8000);
                }
              }}
              disabled={resubmittingIndexNow}
              className="ml-auto text-[10px] px-2.5 py-1 rounded-md bg-green-600 text-white hover:bg-green-700 transition-colors font-medium flex items-center gap-1 disabled:opacity-50"
              title="Re-submit recent URLs and any new sitemap entries to IndexNow"
            >
              <RotateCcw size={10} className={resubmittingIndexNow ? 'animate-spin' : ''} />
              {resubmittingIndexNow ? 'Re-submitting…' : 'Re-submit recent URLs to search engines'}
            </button>
          </div>
          {resubmitMessage && (
            <div className="mb-3 text-[11px] text-gray-600 bg-green-50 border border-green-200 rounded-md px-3 py-1.5">
              {resubmitMessage}
            </div>
          )}

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
            <div className="rounded-lg p-3 bg-green-50 border border-green-200 text-center">
              <p className="text-green-700 font-bold text-lg">{(indexNowStats.total_urls_pushed ?? 0).toLocaleString()}</p>
              <p className="text-[10px] text-gray-500">Total URLs Pushed</p>
            </div>
            <div className="rounded-lg p-3 bg-blue-50 border border-blue-200 text-center">
              <p className="text-blue-700 font-bold text-lg">{(indexNowStats.total_pushes ?? 0).toLocaleString()}</p>
              <p className="text-[10px] text-gray-500">Total Pushes</p>
            </div>
            <div className="rounded-lg p-3 bg-violet-50 border border-violet-200 text-center" title={TODAY_BUCKET_CAPTION}>
              <p className="text-violet-700 font-bold text-lg">{(indexNowStats.today_urls_pushed ?? 0).toLocaleString()}</p>
              <p className="text-[10px] text-gray-500">URLs Today<span className="text-gray-400"> (UTC)</span></p>
            </div>
            <div className="rounded-lg p-3 bg-amber-50 border border-amber-200 text-center">
              <p className="text-amber-700 font-bold text-lg">{indexNowStats.pending ?? 0}</p>
              <p className="text-[10px] text-gray-500">Pending</p>
            </div>
          </div>

          {indexNowStats.last_push && (
            <div className="text-[10px] text-gray-400 mb-3">
              Last push: {new Date(indexNowStats.last_push.pushed_at).toLocaleString()} ({indexNowStats.last_push.url_count} URLs, source: {indexNowStats.last_push.source})
            </div>
          )}

          {indexNowStats.sitemap_diff_latest && (
            <div className="mb-4 rounded-lg border border-indigo-200 bg-indigo-50 p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] text-indigo-700 font-semibold uppercase tracking-wider">Sitemap Diff</span>
                <span className="text-[10px] text-gray-500">
                  Last run: {indexNowStats.sitemap_diff_latest.ran_at ? new Date(indexNowStats.sitemap_diff_latest.ran_at).toLocaleString() : '—'}
                </span>
              </div>
              <div className="grid grid-cols-3 gap-2 text-center">
                <div className="rounded-md bg-white border border-indigo-100 py-1.5">
                  <p className="text-indigo-700 font-bold text-sm">{(indexNowStats.sitemap_diff_latest.sitemap_total ?? 0).toLocaleString()}</p>
                  <p className="text-[9px] text-gray-500">Sitemap Total</p>
                </div>
                <div className="rounded-md bg-white border border-indigo-100 py-1.5">
                  <p className="text-emerald-700 font-bold text-sm">{(indexNowStats.sitemap_diff_latest.new_queued ?? 0).toLocaleString()}</p>
                  <p className="text-[9px] text-gray-500">New Queued</p>
                </div>
                <div className="rounded-md bg-white border border-indigo-100 py-1.5">
                  <p className="text-amber-700 font-bold text-sm">{(indexNowStats.sitemap_diff_latest.skipped_capacity ?? 0).toLocaleString()}</p>
                  <p className="text-[9px] text-gray-500">Skipped (capacity)</p>
                </div>
              </div>
              {indexNowStats.sitemap_diff_history?.length > 1 && (
                <div className="mt-3">
                  <div className="text-[10px] text-gray-500 font-semibold mb-1 uppercase tracking-wider">Recent Runs</div>
                  <div className="space-y-1 max-h-40 overflow-y-auto">
                    {indexNowStats.sitemap_diff_history.map((run, i) => (
                      <div key={i} className="flex items-center gap-2 text-[10px] py-1 px-2 rounded bg-white border border-indigo-100">
                        <span className="text-gray-500 min-w-[140px]">
                          {run.ran_at ? new Date(run.ran_at).toLocaleString() : '—'}
                        </span>
                        <span className="text-gray-700">total <span className="font-mono font-semibold">{(run.sitemap_total ?? 0).toLocaleString()}</span></span>
                        <span className="text-emerald-700">new <span className="font-mono font-semibold">{(run.new_queued ?? 0).toLocaleString()}</span></span>
                        <span className="text-gray-500">already <span className="font-mono">{(run.already_submitted ?? 0).toLocaleString()}</span></span>
                        {(run.skipped_capacity ?? 0) > 0 && (
                          <span className="text-amber-600 ml-auto">skipped <span className="font-mono font-semibold">{run.skipped_capacity.toLocaleString()}</span></span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {indexNowStats.by_source?.length > 0 && (
            <div>
              <div className="text-[10px] text-gray-400 font-semibold mb-1.5 uppercase tracking-wider">Push Sources</div>
              <div className="flex flex-wrap gap-1.5">
                {indexNowStats.by_source.map((s, i) => (
                  <span key={i} className="text-[10px] px-2 py-0.5 rounded-md text-green-700 bg-green-50 border border-green-200">
                    {s.source}: {s.push_count} pushes · {s.url_count} URLs
                  </span>
                ))}
              </div>
            </div>
          )}

          {indexNowStats.endpoint_health?.length > 0 && (
            <div className="mt-4">
              <div className="text-[10px] text-gray-400 font-semibold mb-2 uppercase tracking-wider">Endpoint Health</div>
              <div className="space-y-1.5">
                {indexNowStats.endpoint_health.map((ep, i) => {
                  const host = ep.endpoint.replace(/https?:\/\//, '').split('/')[0];
                  const statusColor = ep.is_dead_lettered
                    ? 'bg-red-400'
                    : ep.consecutive_failures > 0
                      ? 'bg-amber-400'
                      : 'bg-green-400';
                  const statusBg = ep.is_dead_lettered
                    ? 'bg-red-50 border-red-200'
                    : ep.consecutive_failures > 0
                      ? 'bg-amber-50 border-amber-200'
                      : 'bg-green-50 border-green-200';
                  return (
                    <div key={i} className={`flex items-center gap-2 text-[10px] py-2 px-3 rounded-lg border ${statusBg}`}>
                      <span className={`w-2 h-2 rounded-full flex-shrink-0 ${statusColor}`} />
                      <span className="text-gray-700 font-medium min-w-[120px]">{host}</span>
                      <span className="text-gray-500">
                        {ep.total_successes}&#x2F;{ep.total_successes + ep.total_failures} ok
                      </span>
                      {ep.consecutive_failures > 0 && (
                        <span className="text-amber-600 flex items-center gap-0.5">
                          <AlertTriangle size={10} />
                          {ep.consecutive_failures} consecutive fail{ep.consecutive_failures !== 1 ? 's' : ''}
                        </span>
                      )}
                      {!ep.is_available && ep.backoff_remaining_seconds > 0 && (
                        <span className="text-orange-500 flex items-center gap-0.5">
                          <Clock size={10} />
                          backoff {Math.ceil(ep.backoff_remaining_seconds)}s
                        </span>
                      )}
                      {ep.is_dead_lettered && (
                        <span className="text-red-600 font-semibold flex items-center gap-0.5">
                          <AlertCircle size={10} />
                          dead-lettered
                        </span>
                      )}
                      {ep.pending_retry_urls > 0 && (
                        <span className="text-gray-500">{ep.pending_retry_urls} retry queued</span>
                      )}
                      {ep.is_dead_lettered && (
                        <button
                          onClick={() => handleRetryEndpoint(ep.endpoint)}
                          disabled={retryingEndpoint === ep.endpoint}
                          className="text-[9px] px-2 py-0.5 rounded-md bg-white text-red-600 border border-red-200 hover:bg-red-50 hover:text-red-700 transition-colors font-medium flex items-center gap-1 disabled:opacity-50"
                        >
                          <RotateCcw size={9} className={retryingEndpoint === ep.endpoint ? 'animate-spin' : ''} />
                          {retryingEndpoint === ep.endpoint ? 'Retrying...' : 'Retry'}
                        </button>
                      )}
                      <span className={`ml-auto text-[9px] px-1.5 py-0.5 rounded font-medium ${ep.is_dead_lettered ? 'text-red-700 bg-red-100' : ep.consecutive_failures > 0 ? 'text-amber-700 bg-amber-100' : 'text-green-700 bg-green-100'}`}>
                        {ep.is_dead_lettered ? 'DOWN' : ep.consecutive_failures > 0 ? 'DEGRADED' : 'HEALTHY'}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {indexNowStats.endpoint_health_history && Object.keys(indexNowStats.endpoint_health_history).length > 0 && (
            <div className="mt-4">
              <div className="text-[10px] text-gray-400 font-semibold mb-2 uppercase tracking-wider">Endpoint Health History</div>
              <div className="space-y-3 max-h-48 overflow-y-auto">
                {Object.entries(indexNowStats.endpoint_health_history).map(([endpoint, events]) => {
                  const host = endpoint.replace(/https?:\/\//, '').split('/')[0] || '?';
                  return (
                    <div key={endpoint}>
                      <div className="text-[10px] text-gray-600 font-semibold mb-1">{host}</div>
                      <div className="space-y-1">
                        {events.map((evt, i) => {
                          const eventColor = evt.event === 'recovered'
                            ? 'bg-green-400' : evt.event === 'dead_lettered'
                            ? 'bg-red-400' : evt.event === 'manual_retry'
                            ? 'bg-blue-400' : 'bg-amber-400';
                          const eventLabel = evt.event === 'recovered'
                            ? 'Recovered' : evt.event === 'dead_lettered'
                            ? 'Dead-lettered' : evt.event === 'manual_retry'
                            ? 'Manual retry' : 'Failure started';
                          const ts = evt.timestamp ? new Date(evt.timestamp) : null;
                          const ago = ts ? (() => {
                            const diff = Math.floor((Date.now() - ts.getTime()) / 1000);
                            if (diff < 60) return `${diff}s ago`;
                            if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
                            if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
                            return `${Math.floor(diff / 86400)}d ago`;
                          })() : '';
                          const detail = evt.details?.previous_consecutive_failures
                            ? `after ${evt.details.previous_consecutive_failures} failures`
                            : evt.details?.consecutive_failures
                            ? `${evt.details.consecutive_failures} consecutive`
                            : evt.details?.backoff_seconds
                            ? `backoff ${evt.details.backoff_seconds}s`
                            : '';
                          return (
                            <div key={i} className="flex items-center gap-2 text-[10px] py-1.5 px-2 rounded-lg bg-gray-50 border border-gray-100">
                              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${eventColor}`} />
                              <span className="text-gray-500 min-w-[40px]">{ago}</span>
                              <span className={evt.event === 'recovered' ? 'text-green-600' : evt.event === 'dead_lettered' ? 'text-red-600' : evt.event === 'manual_retry' ? 'text-blue-600' : 'text-amber-600'}>{eventLabel}</span>
                              {detail && <span className="text-gray-400">{detail}</span>}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {indexNowHistory?.pushes?.length > 0 && (
            <div className="mt-4">
              <div className="text-[10px] text-gray-400 font-semibold mb-2 uppercase tracking-wider">Recent Push History</div>
              <div className="space-y-1.5 max-h-48 overflow-y-auto">
                {indexNowHistory.pushes.slice(0, 15).map((push, i) => {
                  const raw = push.results || {};
                  const endpointEntries = raw.chunks
                    ? raw.chunks.flatMap(c => Object.entries(c.endpoints || {}))
                    : Object.entries(raw);
                  const hasError = endpointEntries.some(([, v]) => typeof v === 'string');
                  const allOk = endpointEntries.length > 0 && !hasError && endpointEntries.every(([, v]) => v >= 200 && v < 300);
                  return (
                    <div key={push.id || i} className="flex items-center gap-2 text-[10px] py-1.5 px-2 rounded-lg bg-gray-50 border border-gray-100">
                      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${allOk ? 'bg-green-400' : hasError ? 'bg-red-400' : 'bg-amber-400'}`} />
                      <span className="text-gray-500 w-32 flex-shrink-0">{new Date(push.pushed_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>
                      <span className="text-gray-700 font-medium">{push.url_count} URLs</span>
                      <span className="text-gray-400 px-1">·</span>
                      <span className="text-gray-500">{push.source}</span>
                      <span className="ml-auto flex gap-1">
                        {endpointEntries.map(([ep, code], j) => {
                          const host = ep.replace(/https?:\/\//, '').split('/')[0];
                          const ok = typeof code === 'number' && code >= 200 && code < 300;
                          return (
                            <span key={j} className={`px-1 py-0.5 rounded text-[9px] ${ok ? 'text-green-600 bg-green-50' : 'text-red-600 bg-red-50'}`}>
                              {host}: {code}
                            </span>
                          );
                        })}
                      </span>
                    </div>
                  );
                })}
              </div>
              {indexNowHistory.total > 15 && (
                <p className="text-[9px] text-gray-400 mt-1.5 text-center">Showing 15 of {indexNowHistory.total} pushes</p>
              )}
            </div>
          )}
        </GlassCard>
      )}
      </SectionErrorBoundary>

      {/* Page-views / bounce-rate / avg-session row was previously
          rendered here. It has been moved up to sit directly between
          the "Traffic (Cloudflare)" card and the "Cloudflare AI
          Crawl Control" card so all traffic-shaped surfaces are
          adjacent. See the marker comment near that block. */}

      <SectionErrorBoundary name="Chat Health">
      <GlassCard className="p-5">
        <div className="flex items-center gap-2 mb-5">
          <ShieldCheck size={16} className="text-violet-500" />
          <h3 className="text-gray-700 font-semibold">AI Health</h3>
          <div className="ml-auto flex items-center gap-2">
            <AlertBadge alert={ragAlert} />
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="rounded-xl p-4 flex flex-col items-center gap-2 bg-gray-50 border border-gray-100">
            <div className="flex items-center justify-between w-full mb-1">
              <span className="text-gray-500 text-xs font-medium flex items-center gap-1">
                <Target size={11} /> RAG Accuracy
              </span>
              <AlertBadge alert={ragAlert} />
            </div>
            <RagAccuracyGauge accuracy={ragAccuracy?.accuracy_pct ?? 98} />
            <p className="text-xs text-gray-400 text-center">
              {ragAccuracy?.has_data
                ? `${ragAccuracy.answered_queries} / ${ragAccuracy.total_queries} queries answered`
                : 'No queries yet — showing default'}
            </p>
          </div>

          <div className="rounded-xl p-4 bg-gray-50 border border-gray-100">
            <div className="flex items-center justify-between mb-3">
              <span className="text-gray-500 text-xs font-medium flex items-center gap-1">
                <Activity size={11} /> Daily Fallback Rate
              </span>
              <AlertBadge alert={fallbackAlert} />
            </div>
            {chatFallbacks?.has_data && chatFallbacks.daily.length > 0 ? (
              <ResponsiveContainer width="100%" height={90}>
                <LineChart data={chatFallbacks.daily}>
                  <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#9ca3af' }} tickFormatter={d => d.slice(5)} />
                  <YAxis tick={{ fontSize: 9, fill: '#9ca3af' }} domain={[0, 'auto']} />
                  <Tooltip content={<ChartTooltip />} />
                  <ReferenceLine y={5} stroke="#ef4444" strokeDasharray="3 3" label={{ value: '5% max', fill: '#ef4444', fontSize: 9 }} />
                  <Line type="monotone" dataKey="fallback_rate" stroke="#f59e0b" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : failedSections.includes('fallbacks') ? (
              <div className="flex flex-col items-center justify-center h-[90px] text-gray-400 text-xs gap-1">
                <Activity size={20} className="opacity-30" />
                <span className="text-amber-600">Could not load fallback data</span>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-[90px] text-gray-400 text-xs gap-1">
                <Activity size={20} className="opacity-30" />
                <span>No query data yet</span>
                <span className="text-emerald-600 text-xs font-medium">
                  {chatFallbacks?.fallback_rate_pct ?? 0}% fallback rate
                </span>
              </div>
            )}
            <p className="text-xs text-gray-400 mt-1">Target: &lt;5% fallback rate</p>
          </div>

          <div className="rounded-xl p-4 bg-gray-50 border border-gray-100">
            <div className="flex items-center justify-between mb-3">
              <span className="text-gray-500 text-xs font-medium flex items-center gap-1">
                <Database size={11} /> Vector Coverage
              </span>
              <AlertBadge alert={vectorAlert} />
            </div>
            {vectorStats ? (
              <div className="space-y-3">
                {[
                  { label: 'SEO Pages', pct: vectorStats.pages?.coverage_pct ?? 0, color: '#8b5cf6' },
                  { label: 'Chapters', pct: vectorStats.chapters?.coverage_pct ?? 0, color: '#3b82f6' },
                  { label: 'Overall', pct: vectorStats.overall_coverage_pct ?? 0, color: '#10b981' },
                ].map(({ label, pct, color }) => (
                  <div key={label}>
                    <div className="flex justify-between mb-1">
                      <span className="text-xs text-gray-400">{label}</span>
                      <span className="text-xs font-mono" style={{ color }}>{pct}%</span>
                    </div>
                    <div className="h-1.5 rounded-full overflow-hidden bg-gray-200">
                      <div
                        className="h-full rounded-full transition-all duration-500"
                        style={{ width: `${pct}%`, background: pct >= 90 ? color : '#f59e0b' }}
                      />
                    </div>
                  </div>
                ))}
                <p className="text-xs text-gray-400 pt-1">
                  {vectorStats.embedded ?? 0} / {vectorStats.total ?? 0} items embedded
                </p>
                {(vectorStats.embedded ?? 0) === 0 && (vectorStats.total ?? 0) > 0 && (
                  <p className="text-xs text-amber-600 mt-1">
                    Add VERTEX_SERVICE_ACCOUNT to enable embedding
                  </p>
                )}
              </div>
            ) : (
              <div className="flex items-center justify-center h-20 text-gray-400 text-xs">
                No vector data
              </div>
            )}
            <p className="text-xs text-gray-400 mt-1">Target: &ge;90%</p>
          </div>
        </div>
      </GlassCard>
      </SectionErrorBoundary>

      <SectionErrorBoundary name="Chat Speed-up">
      <GlassCard className="p-5">
        <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <Zap size={14} className="text-violet-500" />
            <h3 className="text-gray-700 font-semibold text-sm">Chat Speed-up Scoreboard</h3>
            <span className="text-xs text-gray-400">cache &amp; speculative-web impact</span>
          </div>
          <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-0.5">
            {[{d: 1, label: '24h'}, {d: 7, label: '7d'}, {d: 14, label: '14d'}, {d: 30, label: '30d'}].map(({d, label}) => (
              <button
                key={d}
                onClick={() => setSpeedupDays(d)}
                disabled={speedupLoading}
                className={`text-xs px-2.5 py-1 rounded-md transition-colors ${
                  speedupDays === d
                    ? 'bg-white text-violet-600 font-medium shadow-sm'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
                data-testid={`speedup-period-${d}`}
              >
                {label}
              </button>
            ))}
            {speedupLoading && <Loader2 size={11} className="animate-spin text-gray-400 ml-1" />}
          </div>
        </div>

        {(() => {
          const totals = chatSpeedups?.totals || {};
          const daily = chatSpeedups?.daily || [];
          const warmRuns = chatSpeedups?.warm_runs || [];
          const hasData = chatSpeedups?.has_data;
          const stats = [
            { label: 'Cache hit', value: `${totals.cache_hit_pct ?? 0}%`, sub: `${(totals.early_cache_hits ?? 0) + (totals.pre_sse_cache_hits ?? 0)} hits`, color: '#10b981' },
            { label: 'Warmed cache', value: `${totals.warmed_cache_hit_pct ?? 0}%`, sub: `${totals.early_cache_hits ?? 0} early`, color: '#7c3aed' },
            { label: 'Speculative web used', value: `${totals.speculative_web_used_pct ?? 0}%`, sub: `${totals.speculative_web_used ?? 0} / ${totals.speculative_web_started ?? 0}`, color: '#f59e0b' },
            { label: 'Avg TTFB', value: `${totals.avg_ttfb_ms ?? 0}ms`, sub: `${totals.ttfb_samples ?? 0} samples`, color: '#3b82f6' },
          ];
          return (
            <div className="space-y-4">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                {stats.map(s => (
                  <div key={s.label} className="rounded-xl p-3 bg-gray-50 border border-gray-100">
                    <p className="text-xs text-gray-500 font-medium">{s.label}</p>
                    <p className="text-xl font-bold mt-1" style={{ color: s.color }}>{s.value}</p>
                    <p className="text-xs text-gray-400 mt-0.5">{s.sub}</p>
                  </div>
                ))}
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <div className="rounded-xl p-3 bg-gray-50 border border-gray-100">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs text-gray-500 font-medium">Cache hit % &middot; Avg TTFB</span>
                    <span className="text-xs text-gray-400">{totals.chats_total ?? 0} chats</span>
                  </div>
                  {hasData && daily.length > 0 ? (
                    <ResponsiveContainer width="100%" height={130}>
                      <LineChart data={daily}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                        <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#9ca3af' }} tickFormatter={d => d.slice(5)} />
                        <YAxis yAxisId="pct" orientation="left" tick={{ fontSize: 9, fill: '#9ca3af' }} domain={[0, 100]} unit="%" />
                        <YAxis yAxisId="ms" orientation="right" tick={{ fontSize: 9, fill: '#9ca3af' }} domain={[0, 'auto']} />
                        <Tooltip content={<ChartTooltip />} />
                        <Legend wrapperStyle={{ fontSize: 9 }} />
                        <Line yAxisId="pct" type="monotone" dataKey="cache_hit_pct" stroke="#10b981" strokeWidth={2} dot={false} name="Cache %" />
                        <Line yAxisId="pct" type="monotone" dataKey="warmed_cache_hit_pct" stroke="#7c3aed" strokeWidth={2} dot={false} name="Warmed %" />
                        <Line yAxisId="ms" type="monotone" dataKey="avg_ttfb_ms" stroke="#3b82f6" strokeWidth={2} dot={false} name="TTFB ms" />
                      </LineChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="flex flex-col items-center justify-center h-[130px] text-gray-400 text-xs gap-1">
                      <Zap size={20} className="opacity-30" />
                      <span>No chat speed-up data yet</span>
                      <span className="text-xs text-gray-300">Populates after chats are served</span>
                    </div>
                  )}
                </div>

                <div className="rounded-xl p-3 bg-gray-50 border border-gray-100">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs text-gray-500 font-medium flex items-center gap-1">
                      <RefreshCw size={11} /> Recent cache-warm runs
                    </span>
                    <span className="text-xs text-gray-400">6h pre-warm cycle</span>
                  </div>
                  {warmRuns.length > 0 ? (
                    <div className="space-y-1.5 max-h-[130px] overflow-y-auto pr-1" data-testid="speedup-warm-runs">
                      {warmRuns.slice(0, 8).map((r, i) => (
                        <div key={i} className="flex items-center gap-2 text-xs">
                          <span className="text-gray-400 font-mono w-[88px] flex-shrink-0">
                            {r.ts ? new Date(r.ts).toLocaleString(undefined, { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'}
                          </span>
                          <span className="text-emerald-600 font-mono">{r.warmed}w</span>
                          <span className="text-gray-400 font-mono">{r.already_cached}c</span>
                          <span className={`font-mono ${r.failed > 0 ? 'text-red-500' : 'text-gray-300'}`}>{r.failed}f</span>
                          <span className="text-gray-400 truncate ml-auto">{r.source}</span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="flex flex-col items-center justify-center h-[130px] text-gray-400 text-xs gap-1">
                      <RefreshCw size={20} className="opacity-30" />
                      <span>No warm runs in window</span>
                      <span className="text-xs text-gray-300">Pre-warm cycle runs every 6h</span>
                    </div>
                  )}
                </div>
              </div>

              {/* ─── Per-provider TTFT / total / token-rate (Task #626) ───────── */}
              {(() => {
                const providers = chatSpeedups?.by_provider || [];
                const fallbacks = chatSpeedups?.provider_fallbacks || [];
                const fallbackTotal = fallbacks.reduce((s, f) => s + (f.count || 0), 0);
                // Render vertex_gemini and the legacy pool side-by-side
                // even when one of them has zero calls in the window —
                // synthesise a zero-row placeholder so the admin always
                // sees both baselines and "—" in the metric cells. Any
                // additional providers (e.g. a future third pool) are
                // appended in whatever order the backend returned them.
                const zeroRow = (name) => ({ provider: name, calls: 0, avg_ttfb_ms: 0, avg_total_ms: 0, ttfb_samples: 0, total_samples: 0, tokens_per_sec: 0 });
                const findProv = (name) => providers.find(p => p.provider === name) || zeroRow(name);
                const ordered = [findProv('vertex_gemini'), findProv('openai/gpt-oss-20b')];
                providers.forEach(p => {
                  if (p.provider !== 'vertex_gemini' && p.provider !== 'openai/gpt-oss-20b') ordered.push(p);
                });
                return (
                  <div
                    id="chat-speedup-providers"
                    className="rounded-xl border border-violet-100 bg-violet-50/30 overflow-hidden scroll-mt-24"
                    data-testid="chat-speedup-providers"
                  >
                    <div className="flex items-center justify-between px-3 py-2 border-b border-violet-100">
                      <span className="text-xs text-gray-600 font-medium">Per-provider chat speed</span>
                      <span className="text-xs text-gray-400">
                        Vertex Gemini vs legacy SLM pool · {ordered.length} provider{ordered.length === 1 ? '' : 's'}
                      </span>
                    </div>
                    {ordered.length === 0 ? (
                      <div className="flex flex-col items-center justify-center h-[110px] text-gray-400 text-xs gap-1">
                        <span>No provider-tagged samples in window</span>
                        <span className="text-xs text-gray-300">Populates once Vertex or legacy streams a chat</span>
                      </div>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full text-xs" data-testid="speedup-provider-table">
                          <thead className="bg-violet-100/40 text-gray-500">
                            <tr>
                              <th className="text-left px-3 py-1.5 font-medium">Provider</th>
                              <th className="text-right px-3 py-1.5 font-medium">Calls</th>
                              <th className="text-right px-3 py-1.5 font-medium">Avg TTFT ms</th>
                              <th className="text-right px-3 py-1.5 font-medium">Avg total ms</th>
                              <th className="text-right px-3 py-1.5 font-medium">Tokens / sec</th>
                            </tr>
                          </thead>
                          <tbody>
                            {ordered.map(p => {
                              const isVx = p.provider === 'vertex_gemini';
                              return (
                                <tr key={p.provider} className="border-t border-violet-100/50">
                                  <td className="px-3 py-1.5 font-mono text-gray-700">
                                    <span className={`inline-block w-1.5 h-1.5 rounded-full mr-2 ${isVx ? 'bg-violet-500' : 'bg-blue-500'}`} />
                                    {p.provider}
                                    {isVx && <span className="ml-1.5 text-[10px] text-violet-500 font-sans">happy path</span>}
                                  </td>
                                  <td className="px-3 py-1.5 text-right text-gray-700">{p.calls ?? 0}</td>
                                  <td className="px-3 py-1.5 text-right" style={{ color: '#3b82f6' }}>
                                    {p.ttfb_samples ? `${p.avg_ttfb_ms}` : '—'}
                                  </td>
                                  <td className="px-3 py-1.5 text-right text-gray-600">
                                    {p.total_samples ? `${p.avg_total_ms}` : '—'}
                                  </td>
                                  <td className="px-3 py-1.5 text-right text-gray-600">
                                    {p.tokens_per_sec ? p.tokens_per_sec.toFixed(2) : '—'}
                                  </td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>
                    )}
                    <div className="flex items-center justify-between px-3 py-2 border-t border-violet-100 bg-white/50">
                      <span className="text-xs text-gray-500 font-medium">
                        Fallbacks (Vertex → legacy)
                      </span>
                      <span
                        className={`text-xs font-semibold ${fallbackTotal > 0 ? 'text-amber-600' : 'text-emerald-600'}`}
                        data-testid="speedup-fallback-total"
                      >
                        {fallbackTotal} in window
                      </span>
                    </div>
                    {fallbacks.length > 0 ? (
                      <div className="px-3 py-2 space-y-1" data-testid="speedup-fallback-list">
                        {fallbacks.map(f => (
                          <div key={f.transition} className="flex items-center justify-between text-xs">
                            <span className="font-mono text-gray-600">{f.transition}</span>
                            <span className="text-amber-600 font-medium">{f.count}</span>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="px-3 py-2 text-xs text-gray-400">
                        No fallbacks recorded — Vertex served every chat in this window.
                      </div>
                    )}
                  </div>
                );
              })()}

              <div className="rounded-xl border border-gray-100 bg-gray-50 overflow-hidden">
                <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100">
                  <span className="text-xs text-gray-500 font-medium">Per-day breakdown</span>
                  <span className="text-xs text-gray-400">{daily.length} day{daily.length === 1 ? '' : 's'}</span>
                </div>
                {hasData && daily.length > 0 ? (
                  <div className="overflow-x-auto max-h-[260px]" data-testid="speedup-daily-table">
                    <table className="w-full text-xs">
                      <thead className="bg-gray-100 text-gray-500 sticky top-0">
                        <tr>
                          <th className="text-left px-3 py-1.5 font-medium">Date</th>
                          <th className="text-right px-3 py-1.5 font-medium">Chats</th>
                          <th className="text-right px-3 py-1.5 font-medium">Cache %</th>
                          <th className="text-right px-3 py-1.5 font-medium">Warmed %</th>
                          <th className="text-right px-3 py-1.5 font-medium">Spec-web %</th>
                          <th className="text-right px-3 py-1.5 font-medium">TTFB ms</th>
                          <th className="text-right px-3 py-1.5 font-medium">Total ms</th>
                        </tr>
                      </thead>
                      <tbody>
                        {[...daily].reverse().map(d => (
                          <tr key={d.date} className="border-t border-gray-100 hover:bg-white">
                            <td className="px-3 py-1.5 font-mono text-gray-600">{d.date}</td>
                            <td className="px-3 py-1.5 text-right text-gray-700">{d.chats_total ?? 0}</td>
                            <td className="px-3 py-1.5 text-right" style={{ color: '#10b981' }}>{d.cache_hit_pct ?? 0}%</td>
                            <td className="px-3 py-1.5 text-right" style={{ color: '#7c3aed' }}>{d.warmed_cache_hit_pct ?? 0}%</td>
                            <td className="px-3 py-1.5 text-right" style={{ color: '#f59e0b' }}>{d.speculative_web_used_pct ?? 0}%</td>
                            <td className="px-3 py-1.5 text-right" style={{ color: '#3b82f6' }}>{d.avg_ttfb_ms ?? 0}</td>
                            <td className="px-3 py-1.5 text-right text-gray-500">{d.avg_total_ms ?? 0}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-[120px] text-gray-400 text-xs gap-1">
                    <span>No per-day data in window</span>
                  </div>
                )}
              </div>

              <p className="text-xs text-gray-400">
                Window: last {chatSpeedups?.period_days ?? speedupDays} day{(chatSpeedups?.period_days ?? speedupDays) === 1 ? '' : 's'}
                {totals.avg_total_ms ? <> &middot; Avg full chat: {totals.avg_total_ms}ms</> : null}
                {totals.instant_fastpath ? <> &middot; Instant fast-path fires: {totals.instant_fastpath}</> : null}
              </p>
            </div>
          );
        })()}
      </GlassCard>
      </SectionErrorBoundary>

      <SectionErrorBoundary name="Anonymous Quota Wall">
      <GlassCard className="p-5" data-testid="anon-quota-wall-card">
        <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
          <div className="flex items-center gap-2">
            <ShieldAlert size={14} className="text-rose-500" />
            <h3 className="text-gray-700 font-semibold text-sm">Anonymous Quota Wall</h3>
            <span className="text-xs text-gray-400">device 30/day cap hits &amp; sign-up rescue</span>
            {anonQuotaWall?.alert && (
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                  anonQuotaWall.alert === 'amber'
                    ? 'bg-amber-100 text-amber-700'
                    : 'bg-emerald-100 text-emerald-700'
                }`}
                data-testid="anon-quota-alert-pill"
              >
                {anonQuotaWall.alert === 'amber' ? '≥50 devices/wk' : 'healthy'}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <div className="flex items-center gap-1 bg-gray-100 rounded-lg p-0.5">
              {[{d: 1, label: '24h'}, {d: 7, label: '7d'}, {d: 14, label: '14d'}].map(({d, label}) => (
                <button
                  key={d}
                  onClick={() => setAnonQuotaDays(d)}
                  disabled={anonQuotaLoading}
                  className={`text-xs px-2.5 py-1 rounded-md transition-colors ${
                    anonQuotaDays === d
                      ? 'bg-white text-rose-600 font-medium shadow-sm'
                      : 'text-gray-500 hover:text-gray-700'
                  }`}
                  data-testid={`anon-quota-period-${d}`}
                >
                  {label}
                </button>
              ))}
              {anonQuotaLoading && <Loader2 size={11} className="animate-spin text-gray-400 ml-1" />}
            </div>
            <button
              onClick={() => loadAnonQuotaWall(anonQuotaDays, true)}
              disabled={anonQuotaBackfilling || anonQuotaLoading}
              className="text-xs px-2.5 py-1 rounded-md border border-rose-200 bg-rose-50 text-rose-700 hover:bg-rose-100 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1"
              data-testid="anon-quota-backfill-btn"
              title="Replay today's at-cap devices from existing Redis device-credit counters so the chart isn't empty on first load"
            >
              {anonQuotaBackfilling ? <Loader2 size={11} className="animate-spin" /> : <RotateCcw size={11} />}
              Backfill today
            </button>
          </div>
        </div>

        {anonQuotaWall?.data_source === 'memory_fallback' && (
          <div className="mb-3 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-800 flex items-start gap-2" data-testid="anon-quota-degraded-banner">
            <AlertTriangle size={12} className="mt-0.5 flex-shrink-0" />
            <span>
              <span className="font-medium">Showing per-worker memory fallback.</span>{' '}
              Redis is offline or unreachable, so totals reflect only the gunicorn worker that served this request — multiply by worker count for a rough fleet estimate.
            </span>
          </div>
        )}

        {anonQuotaError && (
          <div className="mb-3 px-3 py-3 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800 flex items-start justify-between gap-3" data-testid="anon-quota-fetch-error">
            <div className="flex items-start gap-2">
              <AlertCircle size={12} className="mt-0.5 flex-shrink-0" />
              <span>
                <span className="font-medium">Couldn't refresh wall-hit stats ({anonQuotaError}).</span>{' '}
                {anonQuotaWall ? (
                  <>The numbers below are the <span className="font-medium">last successful snapshot</span> — they may be stale. Click Retry to fetch fresh data.</>
                ) : (
                  <>No data has loaded yet. Click Retry to fetch live data.</>
                )}
              </span>
            </div>
            <button
              onClick={() => loadAnonQuotaWall(anonQuotaDays)}
              disabled={anonQuotaLoading}
              className="text-xs px-2 py-1 rounded-md border border-rose-300 bg-white text-rose-700 hover:bg-rose-100 disabled:opacity-50 flex items-center gap-1 flex-shrink-0"
              data-testid="anon-quota-retry-btn"
            >
              {anonQuotaLoading ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
              Retry
            </button>
          </div>
        )}

        {/* Headline KPIs */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <div className="rounded-xl p-3 bg-rose-50 border border-rose-100">
            <div className="flex items-center gap-1 text-rose-600 text-[10px] uppercase tracking-wide font-medium mb-1">
              <ShieldAlert size={10} /> Wall hits
            </div>
            <div className="text-2xl font-bold text-rose-700" data-testid="anon-quota-total-exhausted">
              {anonQuotaWall?.total_exhausted ?? 0}
            </div>
            <div className="text-[10px] text-rose-500/70">events in window</div>
          </div>
          <div className="rounded-xl p-3 bg-gray-50 border border-gray-100">
            <div className="flex items-center gap-1 text-gray-500 text-[10px] uppercase tracking-wide font-medium mb-1">
              <Smartphone size={10} /> Unique devices
            </div>
            <div className="text-2xl font-bold text-gray-800" data-testid="anon-quota-unique-devices">
              {anonQuotaWall?.unique_devices_exhausted ?? 0}
            </div>
            <div className="text-[10px] text-gray-400">distinct cookies</div>
          </div>
          <div className="rounded-xl p-3 bg-emerald-50 border border-emerald-100">
            <div className="flex items-center gap-1 text-emerald-600 text-[10px] uppercase tracking-wide font-medium mb-1">
              <UserCheck size={10} /> Signed up
            </div>
            <div className="text-2xl font-bold text-emerald-700" data-testid="anon-quota-signup-after">
              {anonQuotaWall?.signup_after_exhaust ?? 0}
            </div>
            <div className="text-[10px] text-emerald-600/70">within 24h of wall</div>
          </div>
          <div className="rounded-xl p-3 bg-violet-50 border border-violet-100">
            <div className="flex items-center gap-1 text-violet-600 text-[10px] uppercase tracking-wide font-medium mb-1">
              <Target size={10} /> Conversion
            </div>
            <div className="text-2xl font-bold text-violet-700" data-testid="anon-quota-conversion-pct">
              {(anonQuotaWall?.conversion_pct ?? 0).toFixed(1)}%
            </div>
            <div className="text-[10px] text-violet-500/70">wall → sign-up</div>
          </div>
        </div>

        {/* Sparkline: daily exhaustion count + conversion % on second axis.
            The backend currently exposes only a window-aggregate
            `conversion_pct`. We always render the conversion series so
            the right axis is meaningful — when per-day data isn't
            available, every point is the window-level value, drawn as
            a flat dashed line and labeled accordingly. Per-day values
            are used automatically once the backend starts returning
            them. */}
        {(() => {
          const daily = anonQuotaWall?.daily ?? [];
          const windowConv = typeof anonQuotaWall?.conversion_pct === 'number'
            ? anonQuotaWall.conversion_pct
            : null;
          const hasPerDayConv = daily.some(d => typeof d.conversion_pct === 'number');
          // Project the window-level value onto every row when per-day
          // is missing, so the recharts <Line> draws a (flat) series.
          const dailyForChart = daily.map(d => ({
            ...d,
            conversion_pct: typeof d.conversion_pct === 'number'
              ? d.conversion_pct
              : windowConv,
          }));
          const showConv = anonQuotaWall?.has_data && (hasPerDayConv || windowConv !== null);
          return (
            <div className="rounded-xl p-3 bg-gray-50 border border-gray-100 mb-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-gray-600">
                  Daily wall hits &amp; conversion
                </span>
                <div className="flex items-center gap-3 text-[10px] text-gray-500">
                  <span className="flex items-center gap-1"><span className="inline-block w-2 h-2 rounded-sm bg-rose-500" /> exhausted</span>
                  {showConv && (
                    <span className="flex items-center gap-1" data-testid="anon-quota-conv-legend">
                      <span className="inline-block w-2 h-2 rounded-sm bg-violet-500" /> conv. %{!hasPerDayConv ? ' (window avg)' : ''}
                    </span>
                  )}
                </div>
              </div>
              {anonQuotaWall?.has_data && (anonQuotaWall.daily?.length ?? 0) > 0 ? (
                <ResponsiveContainer width="100%" height={140}>
                  <LineChart data={dailyForChart}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                    <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#9ca3af' }} tickFormatter={d => (d || '').slice(5)} />
                    <YAxis yAxisId="left" tick={{ fontSize: 9, fill: '#fb7185' }} domain={[0, 'auto']} allowDecimals={false} />
                    {showConv && (
                      <YAxis yAxisId="right" orientation="right" tick={{ fontSize: 9, fill: '#a78bfa' }} domain={[0, 100]} tickFormatter={v => `${v}%`} />
                    )}
                    <Tooltip content={<ChartTooltip />} />
                    <Line yAxisId="left" type="monotone" dataKey="exhausted" stroke="#f43f5e" strokeWidth={2} dot={{ r: 2 }} name="Wall hits" />
                    {showConv && (
                      <Line yAxisId="right" type="monotone" dataKey="conversion_pct" stroke="#8b5cf6" strokeWidth={2} strokeDasharray="4 3" dot={hasPerDayConv ? { r: 2 } : false} name={hasPerDayConv ? 'Conversion %' : 'Conversion % (window avg)'} data-testid="anon-quota-conv-line" />
                    )}
                  </LineChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex flex-col items-center justify-center h-[140px] text-gray-400 text-xs gap-1" data-testid="anon-quota-empty-state">
                  <ShieldAlert size={20} className="opacity-30" />
                  <span>No wall hits in the last {anonQuotaWall?.period_days ?? anonQuotaDays} day{(anonQuotaWall?.period_days ?? anonQuotaDays) === 1 ? '' : 's'}</span>
                  <span className="text-[10px] text-gray-300">Click <strong>Backfill today</strong> if devices have already hit the cap before this card shipped</span>
                </div>
              )}
              {!hasPerDayConv && anonQuotaWall?.has_data && windowConv !== null && (
                <p className="text-[10px] text-gray-400 mt-1 text-center">
                  Per-day conversion not yet emitted by the backend — the dashed line plots the window aggregate ({windowConv.toFixed(1)}%) across every day.
                </p>
              )}
            </div>
          );
        })()}

        {/* Task #809 — durable weekly trend sparkline. Reads from the
            backend's `weekly_trend` series (Redis-persisted, ~13mo
            history) so this chart survives gunicorn restarts and
            shows trends well beyond the 14-day in-memory window
            powering the daily sparkline above. Always rendered when
            the array is present (it's pre-seeded with zero buckets
            server-side) so the dashboard's layout stays stable. */}
        {(() => {
          const weekly = anonQuotaWall?.weekly_trend ?? [];
          if (weekly.length === 0) return null;
          const weeklyMax = Math.max(1, ...weekly.map(w => Number(w.exhausted) || 0));
          const weeklyTotal = weekly.reduce((a, w) => a + (Number(w.exhausted) || 0), 0);
          return (
            <div className="rounded-xl p-3 bg-gray-50 border border-gray-100 mb-3" data-testid="anon-quota-weekly-trend">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-medium text-gray-600">
                  Weekly trend ({weekly.length} weeks, ISO Mondays UTC)
                </span>
                <span className="text-[10px] text-gray-400">
                  {weeklyTotal.toLocaleString()} device-days · peak {weeklyMax.toLocaleString()}
                </span>
              </div>
              <ResponsiveContainer width="100%" height={100}>
                <LineChart data={weekly}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                  <XAxis
                    dataKey="week_start"
                    tick={{ fontSize: 9, fill: '#9ca3af' }}
                    tickFormatter={d => (d || '').slice(5)}
                    interval={Math.max(0, Math.floor(weekly.length / 6) - 1)}
                  />
                  <YAxis tick={{ fontSize: 9, fill: '#fb7185' }} domain={[0, 'auto']} allowDecimals={false} />
                  <Tooltip
                    content={<ChartTooltip />}
                    formatter={(v) => [`${v} device-days`, 'Wall hits']}
                    labelFormatter={(l) => `Week of ${l}`}
                  />
                  <Line type="monotone" dataKey="exhausted" stroke="#f43f5e" strokeWidth={2} dot={{ r: 2 }} name="Wall hits" />
                </LineChart>
              </ResponsiveContainer>
              <p className="text-[10px] text-gray-400 mt-1 text-center">
                Durable — survives backend restarts. Each bucket sums device-days that hit the cap that ISO week.
              </p>
            </div>
          );
        })()}

        {/* By-hour heatmap (24 cells) */}
        <div className="rounded-xl p-3 bg-gray-50 border border-gray-100">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-medium text-gray-600">By hour of day (UTC)</span>
            <span className="text-[10px] text-gray-400">when devices hit the wall</span>
          </div>
          {(() => {
            const byHour = anonQuotaWall?.by_hour || {};
            const counts = Array.from({ length: 24 }, (_, h) => Number(byHour[h] ?? byHour[String(h)] ?? 0));
            const maxCount = Math.max(1, ...counts);
            return (
              <div
                className="grid gap-1"
                style={{ gridTemplateColumns: 'repeat(24, minmax(0, 1fr))' }}
                data-testid="anon-quota-hour-heatmap"
              >
                {counts.map((c, h) => {
                  const intensity = c === 0 ? 0 : 0.15 + (c / maxCount) * 0.85;
                  return (
                    <div
                      key={h}
                      className="relative group rounded h-6 flex items-end justify-center"
                      style={{ background: c === 0 ? '#f3f4f6' : `rgba(244, 63, 94, ${intensity})` }}
                      title={`${String(h).padStart(2, '0')}:00 — ${c} hit${c === 1 ? '' : 's'}`}
                      data-testid={`anon-quota-hour-${h}`}
                    >
                      <span className={`text-[9px] leading-none mb-0.5 ${c > 0 && intensity > 0.5 ? 'text-white' : 'text-gray-500'}`}>
                        {h % 3 === 0 ? String(h).padStart(2, '0') : ''}
                      </span>
                    </div>
                  );
                })}
              </div>
            );
          })()}
          {(() => {
            // Backend (`metrics.get_anon_quota_exhausted_stats`)
            // returns weekday keys as the three-letter string names
            // ("Mon".."Sun"). Read with the same shape; fall back to
            // numeric indices in case the contract is ever changed
            // to Mon=0..Sun=6 down the line.
            const dowMap = anonQuotaWall?.by_day_of_week || {};
            const dowLabels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
            const dowCounts = dowLabels.map((label, i) => Number(dowMap[label] ?? dowMap[i] ?? dowMap[String(i)] ?? 0));
            const dowMax = Math.max(1, ...dowCounts);
            return (
              <div className="mt-3 pt-3 border-t border-gray-200">
                <div className="text-xs font-medium text-gray-600 mb-2">By day of week (UTC)</div>
                <div className="grid grid-cols-7 gap-1" data-testid="anon-quota-dow-strip">
                  {dowLabels.map((label, i) => {
                    const c = dowCounts[i];
                    const intensity = c === 0 ? 0 : 0.15 + (c / dowMax) * 0.85;
                    return (
                      <div key={label} className="text-center">
                        <div
                          className="rounded h-6 flex items-center justify-center"
                          style={{ background: c === 0 ? '#f3f4f6' : `rgba(244, 63, 94, ${intensity})` }}
                          data-testid={`anon-quota-dow-${i}`}
                        >
                          <span className={`text-[10px] font-medium ${c > 0 && intensity > 0.5 ? 'text-white' : 'text-gray-500'}`}>
                            {c}
                          </span>
                        </div>
                        <span className="text-[9px] text-gray-400">{label}</span>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })()}
        </div>

        <p className="text-xs text-gray-400 mt-3">
          Window: last {anonQuotaWall?.period_days ?? anonQuotaDays} day{(anonQuotaWall?.period_days ?? anonQuotaDays) === 1 ? '' : 's'}
          {(anonQuotaWall?.backfilled_today ?? 0) > 0 && <> · Backfilled today: {anonQuotaWall.backfilled_today}</>}
          {anonQuotaWall?.data_source && <> · Source: {anonQuotaWall.data_source === 'redis' ? 'Redis (cross-worker)' : 'memory fallback'}</>}
        </p>
      </GlassCard>
      </SectionErrorBoundary>

      <SectionErrorBoundary name="Latency & Top Queries">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <GlassCard className="p-5">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Clock size={14} className="text-violet-500" />
              <h3 className="text-gray-600 font-semibold text-sm">Query Latency P95</h3>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-gray-400">P95: <span className="text-gray-700 font-medium">{latency?.p95_ms ?? 0}ms</span></span>
              <AlertBadge alert={latencyAlert} />
            </div>
          </div>
          {latency?.has_data && latency.daily.length > 0 ? (
            <ResponsiveContainer width="100%" height={110}>
              <LineChart data={latency.daily}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#9ca3af' }} tickFormatter={d => d.slice(5)} />
                <YAxis tick={{ fontSize: 9, fill: '#9ca3af' }} domain={[0, 'auto']} />
                <Tooltip content={<ChartTooltip />} />
                <ReferenceLine y={2000} stroke="#ef4444" strokeDasharray="4 4" label={{ value: '2s target', fill: '#ef4444', fontSize: 9 }} />
                <Line type="monotone" dataKey="p95_ms" stroke="#7c3aed" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex flex-col items-center justify-center h-[110px] text-gray-400 text-xs gap-1">
              <Cpu size={20} className="opacity-30" />
              <span>No latency data yet</span>
              <span className="text-xs text-gray-300">Data recorded after first chat</span>
            </div>
          )}
          <p className="text-xs text-gray-400 mt-1">Target: P95 &lt;2 s · Avg: {latency?.avg_ms ?? 0}ms</p>
        </GlassCard>

        <GlassCard className="p-5">
          <div className="flex items-center gap-2 mb-3">
            <Search size={14} className="text-violet-500" />
            <h3 className="text-gray-600 font-semibold text-sm">Top Queries</h3>
            <span className="text-xs text-gray-400">content gap signal</span>
          </div>
          {topQueries?.has_data && topQueries.top_queries.length > 0 ? (
            <div className="space-y-1.5 max-h-[150px] overflow-y-auto pr-1">
              {topQueries.top_queries.map((q, i) => {
                const maxCount = topQueries.top_queries[0]?.count || 1;
                const pct = Math.round((q.count / maxCount) * 100);
                return (
                  <div key={i} className="flex items-center gap-2">
                    <span className="text-gray-300 text-xs w-4 flex-shrink-0 font-mono">{i + 1}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex justify-between mb-0.5">
                        <span className="text-xs text-gray-600 truncate">{q.query}</span>
                        <span className="text-xs text-violet-600 font-mono ml-2 flex-shrink-0">{q.count}</span>
                      </div>
                      <div className="h-1 rounded-full overflow-hidden bg-gray-100">
                        <div className="h-full rounded-full" style={{ width: `${pct}%`, background: 'linear-gradient(90deg, #7c3aed, #a78bfa)' }} />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-[100px] text-gray-400 text-xs gap-1">
              <Search size={20} className="opacity-30" />
              <span>No query data yet</span>
              <span className="text-xs text-gray-300">Populates after user chats</span>
            </div>
          )}
          <p className="text-xs text-gray-400 mt-2">
            {topQueries?.total_unique ?? 0} unique queries in last 7 days
          </p>
        </GlassCard>
      </div>
      </SectionErrorBoundary>

      <SectionErrorBoundary name="Token Spend">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <GlassCard className="p-5">
          <div className="flex items-center gap-2 mb-3">
            <Cpu size={14} className="text-violet-500" />
            <h3 className="text-gray-600 font-semibold text-sm">Token Spend</h3>
          </div>
          {tokenSpend?.has_data && tokenSpend.daily.length > 0 ? (
            <ResponsiveContainer width="100%" height={130}>
              <BarChart data={tokenSpend.daily} barSize={8}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#9ca3af' }} tickFormatter={d => d.slice(5)} />
                <YAxis tick={{ fontSize: 8, fill: '#9ca3af' }} />
                <Tooltip content={<ChartTooltip />} />
                <Legend wrapperStyle={{ fontSize: 9 }} />
                <Bar dataKey="gemini_tokens" fill="#8b5cf6" name="Gemini" radius={[3,3,0,0]} />
                <Bar dataKey="xai_tokens" fill="#06b6d4" name="xAI" radius={[3,3,0,0]} />
                <Bar dataKey="vertex_tokens" fill="#10b981" name="Vertex AI" radius={[3,3,0,0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex flex-col items-center justify-center h-[130px] text-gray-400 text-xs gap-1">
              <BarChart2 size={20} className="opacity-30" />
              <span>No token data yet</span>
              <span className="text-xs text-gray-300">Grows with AI usage</span>
            </div>
          )}
          {tokenSpend && Object.keys(tokenSpend.totals || {}).length > 0 && (
            <div className="flex gap-3 mt-2 flex-wrap">
              {Object.entries(tokenSpend.totals).map(([p, v]) => (
                <span key={p} className="text-xs text-gray-400">
                  {p}: <span className="text-gray-600">{(v.tokens || 0).toLocaleString()}</span>
                </span>
              ))}
            </div>
          )}
        </GlassCard>

        <GlassCard className="p-5">
          <div className="flex items-center gap-2 mb-3">
            <TrendingUp size={14} className="text-violet-500" />
            <h3 className="text-gray-600 font-semibold text-sm">Conversion Funnel</h3>
          </div>
          {funnel ? (
            <div className="space-y-2">
              {(funnel.funnel || []).map((step, i) => {
                const maxCount = funnel.funnel[0]?.count || 1;
                const pct = Math.round((step.count / maxCount) * 100);
                const colors = ['#64748b', '#8b5cf6', '#f59e0b', '#10b981'];
                return (
                  <div key={step.stage}>
                    <div className="flex justify-between mb-0.5">
                      <span className="text-xs text-gray-500">{step.stage}</span>
                      <span className="text-xs font-mono text-gray-700">{step.count.toLocaleString()}</span>
                    </div>
                    <div className="h-2 rounded-full overflow-hidden bg-gray-100">
                      <div
                        className="h-full rounded-full transition-all duration-500"
                        style={{ width: `${pct}%`, background: colors[i] || '#7c3aed' }}
                      />
                    </div>
                  </div>
                );
              })}
              <div className="pt-2 border-t border-gray-100 grid grid-cols-2 gap-2">
                <div className="text-center">
                  <p className="text-lg font-bold text-emerald-600">{funnel.free_to_paid_rate}%</p>
                  <p className="text-xs text-gray-400">Free→Paid</p>
                </div>
                <div className="text-center">
                  <p className="text-lg font-bold text-amber-600">{funnel.starter_to_pro_rate}%</p>
                  <p className="text-xs text-gray-400">Starter→Pro</p>
                </div>
              </div>
            </div>
          ) : (
            <div className="flex items-center justify-center h-[130px] text-gray-400 text-xs">
              Loading funnel…
            </div>
          )}
        </GlassCard>

        <GlassCard className="p-5">
          <div className="flex items-center gap-2 mb-3">
            <FileCheck size={14} className="text-violet-500" />
            <h3 className="text-gray-600 font-semibold text-sm">Assam Board Coverage</h3>
            <span className="text-xs text-gray-400">chapter × subject</span>
            {coverage?.has_data && coverage.subjects.length > 0 && (
              <span className="ml-auto text-xs text-gray-400">{coverage.subjects.length} subjects</span>
            )}
          </div>
          {coverage?.has_data && coverage.subjects.length > 0 ? (
            <div className="space-y-2 max-h-[400px] overflow-y-auto pr-1">
              {coverage.subjects.map(sub => (
                <div key={sub.subject_id}>
                  <div className="flex justify-between mb-1">
                    <span className="text-xs text-gray-600 truncate flex items-center gap-1.5">
                      {sub.subject_name}
                      {(sub.class_name || sub.stream_name) && (
                        <span className="text-[10px] text-gray-400 font-normal shrink-0">
                          {[sub.class_name, sub.stream_name].filter(Boolean).join(' · ')}
                        </span>
                      )}
                    </span>
                    <span
                      className="text-xs font-mono ml-2 flex-shrink-0"
                      style={{ color: sub.coverage_pct >= 80 ? '#10b981' : sub.coverage_pct >= 50 ? '#f59e0b' : '#ef4444' }}
                    >
                      {sub.coverage_pct}%
                    </span>
                  </div>
                  <div className="flex gap-0.5 flex-wrap">
                    {(sub.chapters || []).map(ch => (
                      <div
                        key={ch.chapter_id}
                        title={`${ch.title}: ${ch.coverage}`}
                        className="w-3 h-3 rounded-sm"
                        style={{
                          background: ch.coverage === 'full' ? '#10b981'
                            : ch.coverage === 'partial' ? '#f59e0b'
                            : '#f3f4f6',
                          border: '1px solid #e5e7eb',
                        }}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-[130px] text-gray-400 text-xs gap-1">
              <BookOpen size={20} className="opacity-30" />
              <span>No subjects found</span>
              <span className="text-xs text-gray-300">Add subjects to see coverage</span>
            </div>
          )}
          <div className="flex items-center gap-3 mt-2 pt-2 border-t border-gray-100">
            {[['#10b981', 'Full'], ['#f59e0b', 'Partial'], ['#f3f4f6', 'None']].map(([c, label]) => (
              <div key={label} className="flex items-center gap-1">
                <div className="w-2.5 h-2.5 rounded-sm" style={{ background: c, border: '1px solid #e5e7eb' }} />
                <span className="text-xs text-gray-400">{label}</span>
              </div>
            ))}
          </div>
        </GlassCard>
      </div>
      </SectionErrorBoundary>

      <SectionErrorBoundary name="Plan Distribution">
      {data?.plan_distribution && (
        <GlassCard className="p-5">
          <h3 className="text-gray-500 text-sm font-semibold mb-4">Plan Distribution</h3>
          <div className="grid grid-cols-3 gap-4">
            {[
              { key: 'free',    label: 'Free',    color: '#64748b' },
              { key: 'starter', label: 'Starter', color: '#8b5cf6' },
              { key: 'pro',     label: 'Pro',     color: '#f59e0b' },
            ].map(({ key, label, color }) => {
              const count = data.plan_distribution[key] || 0;
              const total = Object.values(data.plan_distribution).reduce((a, b) => a + b, 0) || 1;
              const pct = Math.round((count / total) * 100);
              return (
                <div key={key} className="text-center p-4 rounded-xl bg-gray-50 border border-gray-100">
                  <p className="text-2xl font-bold" style={{ color }}>{count}</p>
                  <p className="text-gray-500 text-sm">{label}</p>
                  <div className="mt-2 h-1 rounded-full overflow-hidden bg-gray-200">
                    <div className="h-full rounded-full transition-all duration-500" style={{ width: `${pct}%`, background: color }} />
                  </div>
                  <p className="text-xs text-gray-400 mt-1">{pct}%</p>
                </div>
              );
            })}
          </div>
        </GlassCard>
      )}
      </SectionErrorBoundary>

      <SectionErrorBoundary name="PWA Stats">
      {pwaStats && (
        <GlassCard className="p-5">
          <div className="flex items-center gap-2 mb-4">
            <Smartphone size={14} className="text-violet-500" />
            <h3 className="text-gray-600 font-semibold text-sm">PWA App Downloads</h3>
            {pwaStats.installs_today > 0 && (
              <span className="text-[11px] font-bold px-2.5 py-0.5 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-600" title={TODAY_BUCKET_CAPTION}>
                +{pwaStats.installs_today} today (UTC)
              </span>
            )}
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
            {[
              { label: 'Total Installs', value: pwaStats.total_installs, color: '#a78bfa' },
              { label: 'Last 7 Days', value: pwaStats.installs_7d, color: '#10b981' },
              { label: 'Prompts Shown', value: pwaStats.prompts_shown, color: '#22d3ee' },
              { label: 'Install Rate', value: `${pwaStats.conversion_rate}%`, color: pwaStats.conversion_rate >= 30 ? '#10b981' : pwaStats.conversion_rate >= 15 ? '#f59e0b' : '#ef4444' },
            ].map(item => (
              <div key={item.label} className="rounded-xl p-3 text-center bg-gray-50 border border-gray-100">
                <p className="text-xl font-bold" style={{ color: item.color }}>{item.value}</p>
                <p className="text-xs text-gray-400 mt-0.5">{item.label}</p>
              </div>
            ))}
          </div>

          {pwaStats.daily_installs?.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] text-gray-400 font-semibold uppercase tracking-wider">Daily Installs (14 days)</span>
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-1">
                    <div className="w-2 h-2 rounded-sm" style={{ background: '#8b5cf6' }} />
                    <span className="text-[10px] text-gray-400">Installs</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <div className="w-2 h-2 rounded-sm" style={{ background: 'rgba(139,92,246,0.25)' }} />
                    <span className="text-[10px] text-gray-400">Prompts</span>
                  </div>
                </div>
              </div>
              <ResponsiveContainer width="100%" height={100}>
                <BarChart data={pwaStats.daily_installs} barSize={10}>
                  <XAxis dataKey="date" tick={{ fontSize: 8, fill: '#9ca3af' }} tickFormatter={d => d.slice(5)} />
                  <YAxis tick={{ fontSize: 8, fill: '#9ca3af' }} allowDecimals={false} />
                  <Tooltip content={<ChartTooltip />} />
                  <Bar dataKey="prompts" fill="rgba(139,92,246,0.25)" name="Prompts" radius={[3, 3, 0, 0]} />
                  <Bar dataKey="installs" fill="#8b5cf6" name="Installs" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}

          <div className="flex items-center gap-4 mt-3 pt-3 border-t border-gray-100 text-xs text-gray-400">
            <span>Dismissed: <span className="text-gray-600 font-medium">{pwaStats.dismissed ?? 0}</span></span>
            <span>Rejected: <span className="text-gray-600 font-medium">{pwaStats.rejected ?? 0}</span></span>
          </div>
        </GlassCard>
      )}
      </SectionErrorBoundary>

      <SectionErrorBoundary name="SEO Pipeline">
        <PipelineWidget token={adminToken} />
      </SectionErrorBoundary>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {quickActions.map((action) => (
          <button
            key={action.id}
            onClick={() => onNavigate?.(action.id)}
            className="flex items-center justify-between p-4 rounded-2xl transition-all duration-300 group hover:shadow-md bg-white border border-gray-200 shadow-sm"
            data-testid={`quick-action-${action.id}`}
          >
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl flex items-center justify-center" style={{ background: `${action.color}15` }}>
                <action.icon size={15} style={{ color: action.color }} />
              </div>
              <span className="text-sm font-medium text-gray-700 group-hover:text-gray-900 transition-colors">{action.label}</span>
            </div>
            <ArrowRight size={14} className="text-gray-300 group-hover:text-gray-500 transition-colors" />
          </button>
        ))}
      </div>

      <SectionErrorBoundary name="Daily Visitors">
      {vs.daily_visitors?.length > 0 && (
        <GlassCard className="p-5">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-gray-500 text-sm font-semibold">Visitor Trend — Last 7 Days</h3>
            <span className="text-xs text-gray-400">Unique visitors per day</span>
          </div>
          <div className="flex items-end gap-2 h-20">
            {vs.daily_visitors.map((d, i) => {
              const maxV = Math.max(...vs.daily_visitors.map(x => x.visitors), 1);
              const pct = Math.max(4, (d.visitors / maxV) * 100);
              const isToday = i === vs.daily_visitors.length - 1;
              return (
                <div key={d.date} className="flex-1 flex flex-col items-center gap-1">
                  <div
                    className="w-full rounded-t transition-all duration-300"
                    style={{
                      height: `${pct}%`,
                      background: isToday
                        ? 'linear-gradient(to top, #7c3aed, #a78bfa)'
                        : '#e5e7eb',
                      minHeight: 4,
                    }}
                    title={`${d.date}: ${d.visitors} visitors, ${d.page_views} views`}
                  />
                  <span className="text-[10px] text-gray-400 whitespace-nowrap">
                    {d.date.slice(5)}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="flex gap-4 mt-3">
            {vs.daily_visitors.slice(-1).map(d => (
              <div key="today-summary" className="flex gap-4 text-xs text-gray-400" title={TODAY_BUCKET_CAPTION}>
                <span>Today (UTC, {UTC_MIDNIGHT_IN_IST}–now): <span className="text-violet-600 font-medium">{d.visitors} visitors</span></span>
                <span>·</span>
                <span><span className="text-gray-600 font-medium">{d.page_views}</span> page views</span>
              </div>
            ))}
          </div>
        </GlassCard>
      )}
      </SectionErrorBoundary>

      <SectionErrorBoundary name="Recent Activity">
      <GlassCard className="p-5" data-testid="recent-activity">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <Activity size={16} className="text-violet-500" />
            <h3 className="text-gray-700 font-semibold">Recent Activity</h3>
            <span className="flex h-2 w-2 relative">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60" />
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
            </span>
          </div>
          <button
            onClick={() => onNavigate?.('activitylog')}
            className="text-xs text-violet-600 hover:text-violet-700 transition-colors"
          >
            View all logs →
          </button>
        </div>

        {recentEvents.length === 0 ? (
          <div className="text-center py-8">
            <Activity size={28} className="text-gray-200 mx-auto mb-3" />
            <p className="text-gray-400 text-sm">No activity yet — events will appear here in real time</p>
          </div>
        ) : (
          <div className="space-y-1.5">
            {recentEvents.map((event, idx) => (
              <ActivityItem key={idx} event={event} idx={idx} />
            ))}
          </div>
        )}
      </GlassCard>
      </SectionErrorBoundary>

      <SectionErrorBoundary name="Draft-Served Subjects">
        <AdminDraftServedSubjects adminToken={adminToken} />
      </SectionErrorBoundary>

      <AdminQuickLinks links={['content','seomanager','analytics','users','conversations','ai','revenue','roadmap']} onNavigate={onNavigate} />
    </div>
  );
}
