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

export default function ChatWidget(props) {
  const p = props;
  const {
    data, load, vs,
    ragAlert, ragAccuracy, fallbackAlert, chatFallbacks, failedSections,
    vectorAlert, vectorStats,
    chatSpeedups, speedupDays: rawSpeedupDays, setSpeedupDays, speedupLoading,
    latency, latencyAlert,
    topQueries, tokenSpend, funnel, coverage,
  } = props;
  const safeFailedSections = Array.isArray(failedSections) ? failedSections : [];
  const speedupDays = [1, 7, 14, 30].includes(rawSpeedupDays) ? rawSpeedupDays : 7;
  const fallbackDaily = Array.isArray(chatFallbacks?.daily) ? chatFallbacks.daily : [];
  return (
    <>

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
            {chatFallbacks?.has_data && fallbackDaily.length > 0 ? (
              <ResponsiveContainer width="100%" height={90}>
                <LineChart data={fallbackDaily}>
                  <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#9ca3af' }} tickFormatter={d => d.slice(5)} />
                  <YAxis tick={{ fontSize: 9, fill: '#9ca3af' }} domain={[0, 'auto']} />
                  <Tooltip content={<ChartTooltip />} />
                  <ReferenceLine y={5} stroke="#ef4444" strokeDasharray="3 3" label={{ value: '5% max', fill: '#ef4444', fontSize: 9 }} />
                  <Line type="monotone" dataKey="fallback_rate" stroke="#f59e0b" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            ) : safeFailedSections.includes('fallbacks') ? (
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
    </>
  );
}
