import { useState, useEffect, useCallback, useRef } from 'react';
import { toast } from 'sonner';
import { log } from '@/utils/logger';
import AdminQuickLinks from '../AdminQuickLinks';
import AdminDraftServedSubjects from '../AdminDraftServedSubjects';
import AlertReasonsRow from '../AlertReasonsRow';
import BotCachePanel from '../BotCachePanel';
import CacheHitRatioPanel from '../CacheHitRatioPanel';
import R2ColdStoragePanel from '../R2ColdStoragePanel';
import AudioTrimPreview from '../AudioTrimPreview';
import CloudflareAnalyticsBanner from '../analytics/CloudflareAnalyticsBanner';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import { computeHeavyFreshness } from '@/utils/metricsFreshness';
import { usePushNotifications } from '@/hooks/usePushNotifications';
import { pushChannelTone } from '@/utils/pushChannelTone';
import { TODAY_BUCKET_CAPTION, UTC_MIDNIGHT_IN_IST } from '@/utils/time';
import axios from 'axios';
import {
  adminGetDashboard, adminGetCfOverview, seoPipelineStatus,
  adminSeoHealthHistory, adminSeoHealthSnapshotNow, seoHealthLive,
  seoHealthDeepScan, adminSeoDeepScanHistory, adminGetAlertCooldowns, API_BASE,
} from '@/utils/api';
import {
  Users, MessageSquare, BookOpen, Zap, Loader2, Activity,
  ArrowRight, PenTool, Settings, Eye, TrendingUp, RefreshCw,
  UserPlus, Globe, Search, Bot, BarChart2, Server, Clock,
  CheckCircle, AlertCircle, AlertTriangle, Wifi, Database, DollarSign, Crown,
  Layers, Link2, FileCheck, Target, Cpu, ShieldCheck, Smartphone,
  Volume2, VolumeX, Bell, BellOff, RotateCcw, Upload, Trash2, Music, X,
  ShieldAlert, UserCheck, Cloud,
} from 'lucide-react';
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, CartesianGrid, Legend,
  AreaChart, Area,
} from 'recharts';
import {
  GlassCard, StatCard, formatTimeAgo, ActivityItem, DepStatusCard,
  RagAccuracyGauge, ChartTooltip, alertColor, AlertBadge, TOOLTIP_STYLE,
  PipelineWidget, formatCompactInt, adminHdr,
} from './shared';

export default function AiHealthWidget(props) {
  const p = props;
  return (
    <>
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
    </>
  );
}
