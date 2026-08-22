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

export default function UserAnalyticsWidget(props) {
  const p = props;
  const {
    data, load, pwaStats,
    anonQuotaWall, anonQuotaDays: rawAnonQuotaDays, setAnonQuotaDays, anonQuotaLoading,
    anonQuotaBackfilling, anonQuotaError, loadAnonQuotaWall,
    latency, latencyAlert,
    topQueries, tokenSpend, funnel, coverage,
  } = props;
  const anonQuotaDays = [1, 7, 14].includes(rawAnonQuotaDays) ? rawAnonQuotaDays : 14;
  const latencyDaily = Array.isArray(latency?.daily) ? latency.daily : [];
  const topQueryRows = Array.isArray(topQueries?.top_queries) ? topQueries.top_queries : [];
  const tokenSpendDaily = Array.isArray(tokenSpend?.daily) ? tokenSpend.daily : [];
  const coverageSubjects = Array.isArray(coverage?.subjects) ? coverage.subjects : [];
  return (
    <>

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
          {latency?.has_data && latencyDaily.length > 0 ? (
            <ResponsiveContainer width="100%" height={110}>
              <LineChart data={latencyDaily}>
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
          {topQueries?.has_data && topQueryRows.length > 0 ? (
            <div className="space-y-1.5 max-h-[150px] overflow-y-auto pr-1">
              {topQueryRows.map((q, i) => {
                const maxCount = topQueryRows[0]?.count || 1;
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
          {tokenSpend?.has_data && tokenSpendDaily.length > 0 ? (
            <ResponsiveContainer width="100%" height={130}>
              <BarChart data={tokenSpendDaily} barSize={8}>
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
            {coverage?.has_data && coverageSubjects.length > 0 && (
              <span className="ml-auto text-xs text-gray-400">{coverageSubjects.length} subjects</span>
            )}
          </div>
          {coverage?.has_data && coverageSubjects.length > 0 ? (
            <div className="space-y-2 max-h-[400px] overflow-y-auto pr-1">
              {coverageSubjects.map(sub => (
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
    </>
  );
}
