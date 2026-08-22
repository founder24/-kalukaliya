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

export default function SeoWidget(props) {
  const p = props;
  const {
    adminToken, onNavigate, navContext, data, loading,
    prewarmCoverage, indexNowStats, indexNowHistory, retryingEndpoint,
    resubmittingIndexNow, resubmitMessage, setResubmitMessage,
    alertHistory, cooldownActiveCount,
    alertFilter, setAlertFilter, alertReasonFilter, setAlertReasonFilter,
    showSyntheticAlerts, setShowSyntheticAlerts,
    alertSettingsOpen, alertSettingsDraft, setAlertSettingsDraft, alertSettingsSaving,
    notifPrefs, notifPrefsSaving, notifPrefsOpen, setNotifPrefsOpen,
    pushDeliverySummary, seoSummaryDispatches,
    kvHealth, kvExpandedIsolates, setKvExpandedIsolates,
    r2Health, r2ResettingWatchdog, setR2ResettingWatchdog, r2Reevaluating, setR2Reevaluating,
    vertexProbe, ciStatus, ciRerunning,
    chimeUploading, pendingChimeFile, setPendingChimeFile, chimeFileInputRef,
    pushNotif,
    alertSoundEnabled, chimeTone, CHIME_TONES, ALERT_SEVERITY_LABELS,
    seoHealth, seoHealthRefreshing, setSeoHealth, setSeoHealthRefreshing,
    seoLive, seoLiveLoading, seoLiveError, setSeoLive, setSeoLiveLoading, setSeoLiveError,
    setR2Health,
    seoAutoDeepScans,
    expandedSitemap, setExpandedSitemap,
    sitemapDeepScans, setSitemapDeepScans,
    d1SyncRunning, setD1SyncRunning, d1SyncResult, setD1SyncResult,
    d1SyncDurationMs, setD1SyncDurationMs, d1SyncError, setD1SyncError,
    vs,
    handleAcknowledgeAlert, handleAcknowledgeAll, handleOpenAlertSettings,
    handleSaveAlertSettings, handleResetAlertSettings,
    handleRetryEndpoint, handleCiRerun,
    saveNotifPrefs, toggleAlertSound, playAlertChime,
    handleChimeFileSelect, handleChimeUploadConfirm, handleDeleteCustomChime,
  } = props;
  return (
    <>

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
    </>
  );
}
