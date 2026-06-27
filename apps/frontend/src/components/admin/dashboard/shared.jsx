import { useState, useEffect } from 'react';
import {
  Users, MessageSquare, BookOpen, Search, Bot, Eye, UserPlus,
  Server, Database, Cloud, Layers,
} from 'lucide-react';
import { seoPipelineStatus } from '@/utils/api';
import { TODAY_BUCKET_CAPTION } from '@/utils/time';

export const adminHdr = (token) => {
  const isJwt = token && typeof token === 'string' && token.split('.').length === 3;
  return isJwt ? { headers: { Authorization: `Bearer ${token}` }, withCredentials: true } : { withCredentials: true };
};

export const safeArr = (v) => (Array.isArray(v) ? v : []);
export const safeObj = (v) => (v && typeof v === 'object' && !Array.isArray(v) ? v : {});

const _COMPACT_INT_FORMATTER = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 2 });
export const formatCompactInt = (n) => {
  const num = Number(n) || 0;
  return num >= 1000 ? _COMPACT_INT_FORMATTER.format(num) : num.toLocaleString();
};

export const normalizeChatFallbacks = (d) => (d ? { ...d, daily: safeArr(d.daily) } : null);
export const normalizeLatency = (d) => (d ? { ...d, daily: safeArr(d.daily) } : null);
export const normalizeTokenSpend = (d) => (d ? { ...d, daily: safeArr(d.daily), totals: safeObj(d.totals) } : null);
export const normalizeTopQueries = (d) => (d ? { ...d, top_queries: safeArr(d.top_queries) } : null);
export const normalizeChatSpeedups = (d) => (d ? { ...d, daily: safeArr(d.daily), warm_runs: safeArr(d.warm_runs), totals: safeObj(d.totals) } : null);
export const normalizeVectorStats = (d) => (d ? { ...d, pages: safeObj(d.pages), chapters: safeObj(d.chapters) } : null);

export function GlassCard({ children, className = '', glow, ...props }) {
  return (
    <div
      className={`relative rounded-2xl overflow-hidden bg-white border border-gray-200 shadow-sm ${className}`}
      {...props}
    >
      <div className="relative">{children}</div>
    </div>
  );
}

export function StatCard({ label, value, icon: Icon, color, subLabel, subValue, pulse, onClick }) {
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

export function formatTimeAgo(dateStr) {
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

export const EVENT_ICONS = {
  signup:       { icon: UserPlus, color: '#10b981', bg: '#ecfdf5' },
  conversation: { icon: MessageSquare, color: '#8b5cf6', bg: '#f5f3ff' },
  search:       { icon: Search, color: '#60a5fa', bg: '#eff6ff' },
  subject_view: { icon: BookOpen, color: '#f59e0b', bg: '#fffbeb' },
  ai_click:     { icon: Bot, color: '#a78bfa', bg: '#f5f3ff' },
  page_view:    { icon: Eye, color: '#64748b', bg: '#f8fafc' },
};

export function ActivityItem({ event, idx }) {
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

export const DEP_ICONS = { mongodb: Database, postgresql: Database, cloudflare_cache: Cloud, supabase: Database };
export const DEP_LABELS = { mongodb: 'MongoDB', postgresql: 'PostgreSQL', cloudflare_cache: 'Cloudflare Cache', supabase: 'Supabase' };
export const STATUS_COLORS = { ok: '#10b981', error: '#ef4444', not_configured: '#64748b', unknown: '#f59e0b' };

export function DepStatusCard({ name, status, latency }) {
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

export function alertColor(alert) {
  if (alert === 'red') return '#ef4444';
  if (alert === 'yellow') return '#f59e0b';
  return '#10b981';
}

export function AlertBadge({ alert }) {
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

export function RagAccuracyGauge({ accuracy }) {
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

export const TOOLTIP_STYLE = {
  background: '#ffffff',
  border: '1px solid #e5e7eb',
  borderRadius: 12,
  color: '#374151',
  fontSize: 12,
  boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
};

export function ChartTooltip({ active, payload, label }) {
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

export function PipelineWidget({ token }) {
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
