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

export default function TrafficWidget(props) {
  const p = props;
  const { data, metrics, vs: rawVs, cfVisitors24h, cfCrawlControl, botAnalytics } = props;
  const vs = rawVs && typeof rawVs === 'object' ? rawVs : {};
  const hasCfVisitorData = [
    cfVisitors24h?.totals?.visitors,
    cfVisitors24h?.totals?.requests,
    cfVisitors24h?.totals?.page_views,
    cfVisitors24h?.totals?.bytes,
  ].some(value => value != null)
    || (Array.isArray(cfVisitors24h?.series)
      && cfVisitors24h.series.some(bucket => [
        bucket?.visitors,
        bucket?.uniques,
        bucket?.requests,
        bucket?.page_views,
        bucket?.bytes,
      ].some(value => value != null)));
  const hasSiteTrafficData = [
    vs.page_views_today,
    vs.visitors_today,
    vs.bounce_rate,
    vs.avg_session_duration,
  ].some(value => value != null)
    || hasCfVisitorData;
  return (
    <>

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
        {!hasSiteTrafficData && (
          <div className="mt-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-center text-xs text-gray-500" data-testid="traffic-empty-state">
            No traffic data yet
          </div>
        )}
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
    </>
  );
}
