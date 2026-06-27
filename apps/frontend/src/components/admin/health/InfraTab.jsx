import { Database, Zap, CreditCard, RefreshCw, ShieldCheck, AlertTriangle, Wifi, Copy, Check, Users, Activity, MessageSquare, TrendingUp, DollarSign, BarChart2, RotateCw, Clock, Undo2, Star, ExternalLink } from 'lucide-react';
import CronHealthPill, { SlackConfigBadge } from '../CronHealthPill';
import CfWafDriftCronPill from '../CfWafDriftCronPill';
import D1MirrorLagPill from '../D1MirrorLagPill';
import TrustpilotRefreshCronPill from '../TrustpilotRefreshCronPill';
import EdgeProxyDeployCronPill from '../EdgeProxyDeployCronPill';
import UnifiedLogsCfPullCronPill from '../UnifiedLogsCfPullCronPill';
import EmbedBackfillPill from '../EmbedBackfillPill';
import EmbedStackHealthPill from '../EmbedStackHealthPill';
import CfAuditCard from '../CfAuditCard';
import AiGatewayCacheByModelTile from '../AiGatewayCacheByModelTile';
import AiGatewayGuardrailByModelTile from '../AiGatewayGuardrailByModelTile';
import AdminAwsInfraCard from '../AdminAwsInfraCard';
import AdminCronJobsCard from '../AdminCronJobsCard';
import AdminMemoryBrainTile from '../AdminMemoryBrainTile';
import AdminAzureAiPanel from '../AdminAzureAiPanel';
import AdminQuickLinks from '../AdminQuickLinks';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import EdgeMetricsPanel from '../EdgeMetricsPanel';
import ProviderLatencyBench from './ProviderLatencyBench';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, BarChart, Bar, LineChart, Line } from 'recharts';
import axios from 'axios';
import { API_BASE } from '@/utils/api';
import { adminHeaders } from './shared';
import { toast } from 'sonner';
import { computeHeavyFreshness, computeThrottleFreshness } from '@/utils/metricsFreshness';

export default function InfraTab({ adminToken, onNavigate, health, loading, deps, allOk, hasError, chartData, peaks, current, metricsLoading, timeRange, setTimeRange, loadMetrics, loadHealth, benchLatest, benchLoading, loadBenchLatest, cfAddons, cfAddonsLoading, loadCfAddons, awsCredits, awsCreditsLoading, loadAwsCredits, gcpCredits, gcpCreditsLoading, loadGcpCredits, axiomCredits, axiomCreditsLoading, loadAxiomCredits, sentryCredits, sentryCreditsLoading, loadSentryCredits, cfAuditData, cfAuditLoading, loadCfAudit, cfHealthData, cfHealthLoading, loadCfHealth, edgeProxyDeployCronHealth, edgeProxyDeployCronLoading, loadEdgeProxyDeployCronHealth, cfDriftCronHealth, cfDriftCronLoading, loadCfDriftCronHealth, tpCronHealth, tpCronLoading, loadTpCronHealth, unifiedLogsCfPullCronHealth, unifiedLogsCfPullCronLoading, loadUnifiedLogsCfPullCronHealth, edgeProxyDeployCronAlertState, cfDriftCronAlertState, tpCronAlertState, unifiedLogsCfPullCronAlertState, aigGuardrailAlertState, slackWebhookMissingAlertStates, slackWebhookMissingAlertHistories, snoozeSlackWebhookMissing, edgeProxyDeployCronAlertHistory, cfDriftCronAlertHistory, tpCronAlertHistory, unifiedLogsCfPullCronAlertHistory, loadEdgeProxyDeployCronAlertHistory, loadCfDriftCronAlertHistory, loadTpCronAlertHistory, loadUnifiedLogsCfPullCronAlertHistory, d1MirrorLagHealth, d1MirrorLagLoading, loadD1MirrorLagHealth, d1MirrorLagAlertHistory, loadD1MirrorLagAlertHistory, tpJsonldReport, tpJsonldLoading, tpJsonldHistory, tpJsonldAlerts, loadTpJsonldReport, loadTpJsonldHistory, loadTpJsonldAlerts, aiCacheStats, aiCacheLoading, aiCachePurging, loadAiCacheStats, pineconeHealth, pineconeLoading, pineconeSwitch, setPineconeSwitch, loadPineconeHealth, healthUrl, copied, handleCopy, SLACK_WEBHOOK_MISSING_ENVS }) {
  return (
    <>
        {/*
          Phase 4 — Task #332. AWS workers + Azure cron tiles render
          first inside the Infrastructure tab so any in-progress
          incident on the migrated tiers is visible above the older
          (GCP-era) panels without scrolling.
        */}
        <SectionErrorBoundary name="AWS Infra (SQS + Lambda workers)">
          <AdminAwsInfraCard adminToken={adminToken} />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Cron (Azure Container Apps Jobs)">
          <AdminCronJobsCard adminToken={adminToken} />
        </SectionErrorBoundary>

        {/* Task #417 — memory_brain hot-path counters + 24h sparkline. */}
        <SectionErrorBoundary name="Memory Brain hot-path">
          <AdminMemoryBrainTile adminToken={adminToken} />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Azure-native AI features">
          <AdminAzureAiPanel token={adminToken} />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Provider Latency Bench">
        <ProviderLatencyBench
          benchLatest={benchLatest}
          benchLoading={benchLoading}
          loadBenchLatest={loadBenchLatest}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="System Status Banner">
        <div className={`rounded-2xl p-4 flex items-center gap-3 ${
          loading ? 'bg-gray-50 border border-gray-200' : hasError ? 'bg-red-50 border border-red-200' : 'bg-emerald-50 border border-emerald-200'
        }`}>
          {loading ? <Wifi size={20} className="text-gray-400 animate-pulse" /> :
           hasError ? <AlertTriangle size={20} className="text-red-500" /> :
           <ShieldCheck size={20} className="text-emerald-500" />}
          <div className="flex-1">
            <p className={`text-sm font-semibold ${
              loading ? 'text-gray-500' : hasError ? 'text-red-600' : 'text-emerald-600'
            }`}>
              {loading ? 'Running health probes...' : hasError ? 'Degraded — Check Dependencies' : 'All Systems Operational'}
            </p>
            {health && (
              <p className="text-xs text-gray-400 mt-0.5">
                v{health.version || '1.0.0'} · {health.workers} workers · uptime {Math.floor((health.uptime_seconds || 0) / 60)}m
              </p>
            )}
          </div>
          <button onClick={() => { loadHealth(); loadMetrics(); }} className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100" data-testid="button-refresh-health">
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Trustpilot JSON-LD Coverage">
        {(() => {
          // Task #750 — pass/fail per URL from the daily verifier
          // (.github/workflows/trustpilot-jsonld-prod.yml). Tile turns
          // red when ANY URL failed the latest scheduled run, so a
          // SERP-star regression surfaces here, not just in CI email.
          const data = tpJsonldReport && !tpJsonldReport._error
            ? tpJsonldReport
            : null;
          const configured = !!data?.configured;
          const report = data?.report || null;
          const failed = report?.failed ?? 0;
          const total = report?.totalUrls ?? (report?.results?.length || 0);
          const tileFailed = configured && report && (failed > 0 || report.ok === false);
          const tileUnknown = !configured || !report;
          const containerCls = tileFailed
            ? 'bg-red-50 border-red-200'
            : tileUnknown
              ? 'bg-gray-50 border-gray-200'
              : 'bg-emerald-50 border-emerald-200';
          const headerColor = tileFailed
            ? 'text-red-600'
            : tileUnknown
              ? 'text-gray-500'
              : 'text-emerald-600';
          let timestampLabel = 'never';
          if (report?.generatedAt) {
            try {
              const ts = new Date(report.generatedAt);
              const diff = Math.max(0, Math.floor((Date.now() - ts.getTime()) / 1000));
              if (diff < 60) timestampLabel = `${diff}s ago`;
              else if (diff < 3600) timestampLabel = `${Math.floor(diff / 60)}m ago`;
              else if (diff < 86400) timestampLabel = `${Math.floor(diff / 3600)}h ago`;
              else timestampLabel = `${Math.floor(diff / 86400)}d ago`;
            } catch { /* keep default */ }
          }
          return (
            <div className={`rounded-2xl p-4 border ${containerCls}`} data-testid="trustpilot-jsonld-tile">
              <div className="flex items-center gap-3 mb-3">
                {tileFailed
                  ? <AlertTriangle size={18} className="text-red-500" />
                  : tileUnknown
                    ? <Star size={18} className="text-gray-400" />
                    : <Star size={18} className="text-emerald-500" />}
                <div className="flex-1 min-w-0">
                  <p className={`text-sm font-semibold ${headerColor}`} data-testid="trustpilot-jsonld-status">
                    {tileUnknown
                      ? 'Trustpilot JSON-LD coverage — no verifier run yet'
                      : tileFailed
                        ? `Trustpilot JSON-LD coverage — ${failed}/${total} URL${failed === 1 ? '' : 's'} failed`
                        : `Trustpilot JSON-LD coverage — all ${total} URL${total === 1 ? '' : 's'} pass`}
                  </p>
                  <p className="text-[11px] text-gray-500 mt-0.5">
                    Last run {timestampLabel}
                    {report?.target ? ` · target=${report.target}` : ''}
                    {report?.origin ? ` · ${report.origin}` : ''}
                  </p>
                </div>
                {/* Task #968 — surface the dedicated Slack fan-out
                    configuration health (SLACK_TRUSTPILOT_WEBHOOK_URL)
                    next to the per-event verifier alerter, mirroring
                    the badge Task #964 added to the three cron pills.
                    Renders nothing when the backend hasn't published
                    the field yet (in-flight rollout safe). */}
                <SlackConfigBadge
                  configured={tpJsonldReport?.slackConfigured}
                  envName={tpJsonldReport?.slackWebhookEnv}
                  testId="trustpilot-jsonld"
                />
                {report?.runUrl && (
                  <a
                    href={report.runUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[11px] text-violet-600 hover:text-violet-700 inline-flex items-center gap-1"
                    data-testid="trustpilot-jsonld-run-link"
                    title="Open the GitHub Actions run that produced this report"
                  >
                    Run <ExternalLink size={11} />
                  </a>
                )}
                <button
                  onClick={loadTpJsonldReport}
                  disabled={tpJsonldLoading}
                  className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                  data-testid="button-refresh-trustpilot-jsonld"
                  title="Refresh"
                >
                  <RefreshCw size={13} className={tpJsonldLoading ? 'animate-spin' : ''} />
                </button>
              </div>
              {(() => {
                // Task #754 — 30-day pass-rate sparkline. Rendered above
                // the per-URL table so ops sees a slow-moving regression
                // (e.g. the line drifting from 100% to 80% over a week)
                // without having to compare table snapshots day to day.
                const points = (tpJsonldHistory?.points || [])
                  .filter((p) => p && p.passRate != null)
                  .map((p) => ({
                    ts: p.ts,
                    label: p.ts ? new Date(p.ts).toLocaleDateString() : '',
                    passRatePct: Math.round((p.passRate ?? 0) * 1000) / 10,
                    avgRating: p.avgRatingValue,
                    passed: p.passed,
                    failed: p.failed,
                    total: p.totalUrls,
                  }));
                if (points.length < 2) return null;
                const passColor = tileFailed ? '#dc2626' : '#10b981';
                return (
                  <div className="mb-3" data-testid="trustpilot-jsonld-sparkline">
                    <div className="flex items-center justify-between mb-1">
                      <p className="text-[10px] uppercase tracking-wider text-gray-500">
                        Pass-rate · last {points.length} run{points.length === 1 ? '' : 's'} (30d TTL)
                      </p>
                      <p className="text-[10px] text-gray-400 font-mono">
                        latest {points[points.length - 1].passRatePct}%
                      </p>
                    </div>
                    <ResponsiveContainer width="100%" height={48}>
                      <LineChart data={points} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
                        <YAxis hide domain={[0, 100]} />
                        <Tooltip
                          contentStyle={TOOLTIP_STYLE}
                          formatter={(v, name) => {
                            if (name === 'passRatePct') return [`${v}%`, 'pass-rate'];
                            return [v, name];
                          }}
                          labelFormatter={(_, payload) => {
                            const p = payload?.[0]?.payload;
                            if (!p) return '';
                            const bits = [p.label];
                            if (p.passed != null && p.total != null) {
                              bits.push(`${p.passed}/${p.total} pass`);
                            }
                            if (p.avgRating != null) {
                              bits.push(`avg ★ ${Number(p.avgRating).toFixed(2)}`);
                            }
                            return bits.join(' · ');
                          }}
                        />
                        <Line
                          type="monotone"
                          dataKey="passRatePct"
                          stroke={passColor}
                          strokeWidth={2}
                          dot={false}
                          isAnimationActive={false}
                        />
                      </LineChart>
                    </ResponsiveContainer>
                    {(() => {
                      // Task #760 — second sparkline: 30-day average
                      // ratingValue trend. Pass-rate alone can't catch a
                      // slow drift in the actual star rating (e.g. 4.7★
                      // → 4.5★ over a fortnight); this chart does.
                      // Points without avgRating (pre-Task-#754 rows or
                      // an all-fail run with no numeric ratings) are
                      // filtered out so the line doesn't fake zeros.
                      const ratingPoints = points.filter(
                        (p) => p.avgRating != null && Number.isFinite(Number(p.avgRating)),
                      ).map((p) => ({
                        ...p,
                        avgRatingNum: Number(p.avgRating),
                      }));
                      if (ratingPoints.length < 2) return null;
                      // Tighten Y domain around the observed range so
                      // sub-0.2★ drift is actually visible on a 48px
                      // chart. Clamped to a sane Trustpilot band.
                      const values = ratingPoints.map((p) => p.avgRatingNum);
                      const minV = Math.max(0, Math.min(...values) - 0.1);
                      const maxV = Math.min(5, Math.max(...values) + 0.1);
                      const latest = ratingPoints[ratingPoints.length - 1].avgRatingNum;
                      return (
                        <div className="mt-2" data-testid="trustpilot-jsonld-rating-sparkline">
                          <div className="flex items-center justify-between mb-1">
                            <p className="text-[10px] uppercase tracking-wider text-gray-500">
                              Avg ratingValue · last {ratingPoints.length} run{ratingPoints.length === 1 ? '' : 's'}
                            </p>
                            <p className="text-[10px] text-gray-400 font-mono">
                              latest ★ {latest.toFixed(2)}
                            </p>
                          </div>
                          <ResponsiveContainer width="100%" height={48}>
                            <LineChart
                              data={ratingPoints}
                              margin={{ top: 2, right: 2, bottom: 2, left: 2 }}
                            >
                              <YAxis hide domain={[minV, maxV]} />
                              <Tooltip
                                contentStyle={TOOLTIP_STYLE}
                                formatter={(v, name) => {
                                  if (name === 'avgRatingNum') {
                                    return [`★ ${Number(v).toFixed(2)}`, 'avg rating'];
                                  }
                                  return [v, name];
                                }}
                                labelFormatter={(_, payload) => {
                                  const p = payload?.[0]?.payload;
                                  if (!p) return '';
                                  const bits = [p.label];
                                  if (p.avgRating != null) {
                                    bits.push(`★ ${Number(p.avgRating).toFixed(2)}`);
                                  }
                                  return bits.join(' · ');
                                }}
                              />
                              <Line
                                type="monotone"
                                dataKey="avgRatingNum"
                                stroke="#f59e0b"
                                strokeWidth={2}
                                dot={false}
                                isAnimationActive={false}
                              />
                            </LineChart>
                          </ResponsiveContainer>
                        </div>
                      );
                    })()}
                  </div>
                );
              })()}
              {(() => {
                // Task #758 — recent regression / recovery / streak
                // alert events. Reads from the notifications the
                // dispatcher already writes, so a flappy URL (alerted,
                // recovered, re-alerted within a week) stands out at a
                // glance — something single-fire email dedup hides.
                const events = tpJsonldAlerts?.events || [];
                if (!events.length) return null;
                const stateStyles = {
                  regression: 'bg-red-50 text-red-700 border-red-200',
                  streak: 'bg-amber-50 text-amber-700 border-amber-200',
                  recovery: 'bg-emerald-50 text-emerald-700 border-emerald-200',
                };
                const stateLabels = {
                  regression: 'REGRESSION',
                  streak: 'STREAK',
                  recovery: 'RECOVERY',
                };
                const fmtAge = (iso) => {
                  if (!iso) return '';
                  const t = new Date(iso).getTime();
                  if (!Number.isFinite(t)) return '';
                  const s = Math.max(0, Math.round((Date.now() - t) / 1000));
                  if (s < 60) return `${s}s ago`;
                  if (s < 3600) return `${Math.round(s / 60)}m ago`;
                  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
                  return `${Math.round(s / 86400)}d ago`;
                };
                return (
                  <div
                    className="mb-3 pt-2 border-t border-gray-100"
                    data-testid="trustpilot-jsonld-alert-history"
                  >
                    <div className="flex items-center justify-between mb-1.5">
                      <p className="text-[10px] uppercase tracking-wider text-gray-500">
                        Recent alerts · last {events.length}
                      </p>
                      <p className="text-[10px] text-gray-400">
                        auto-refreshes · 60s
                      </p>
                    </div>
                    <ul className="space-y-1 max-h-40 overflow-y-auto pr-1">
                      {events.map((e) => (
                        <li
                          key={e.id || `${e.created_at}-${e.title}`}
                          className="flex items-start gap-2 text-[11px] leading-snug"
                          data-testid={`trustpilot-jsonld-alert-${e.state}`}
                        >
                          <span
                            className={`shrink-0 mt-0.5 inline-block px-1.5 py-0.5 rounded border font-bold text-[9px] tracking-wider ${stateStyles[e.state] || stateStyles.regression}`}
                            title={e.state}
                          >
                            {stateLabels[e.state] || e.state?.toUpperCase() || 'ALERT'}
                          </span>
                          <span className="flex-1 min-w-0">
                            <span
                              className="block text-gray-700 truncate"
                              title={e.title}
                            >
                              {e.title}
                            </span>
                            {Array.isArray(e.urls) && e.urls.length > 0 ? (
                              // Render the per-URL bullets backend
                              // parsed out of the alert body so ops can
                              // spot a flappy URL at a glance. Capped
                              // at 5 with a "+N more" suffix so one
                              // giant alert can't push the strip off
                              // screen.
                              <span
                                className="block mt-0.5 text-[10px] font-mono text-gray-600"
                                data-testid={`trustpilot-jsonld-alert-urls-${e.id || e.created_at}`}
                              >
                                {e.urls.slice(0, 5).map((u, i) => (
                                  <span
                                    key={`${u}-${i}`}
                                    className="block truncate"
                                    title={u}
                                  >
                                    · {u}
                                  </span>
                                ))}
                                {e.urls.length > 5 ? (
                                  <span className="block text-gray-400">
                                    · +{e.urls.length - 5} more
                                  </span>
                                ) : null}
                              </span>
                            ) : null}
                            <span className="block text-[10px] text-gray-400 font-mono">
                              {fmtAge(e.created_at)}
                              {e.created_at ? ` · ${new Date(e.created_at).toLocaleString()}` : ''}
                            </span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                );
              })()}
              {tileUnknown ? (
                <p className="text-[11px] text-gray-500 leading-relaxed">
                  The daily <code className="font-mono">trustpilot-jsonld-prod</code> workflow will publish per-URL pass/fail here once it runs (06:00 UTC). Until then, treat the build-time inject step as the source of truth.
                </p>
              ) : (
                <div className="overflow-x-auto" data-testid="trustpilot-jsonld-table">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-[10px] uppercase tracking-wider text-gray-500 border-b border-gray-100">
                        <th className="py-1.5 pr-3 font-bold">URL</th>
                        <th className="py-1.5 pr-3 font-bold">HTTP</th>
                        <th className="py-1.5 pr-3 font-bold">Pass</th>
                        <th className="py-1.5 font-bold">Detail</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(report.results || []).map((r, idx) => (
                        <tr key={`${r.url}-${idx}`} className="border-b border-gray-50" data-testid={`trustpilot-jsonld-row-${idx}`}>
                          <td className="py-1.5 pr-3 font-mono text-gray-700">{r.url}</td>
                          <td className="py-1.5 pr-3 font-mono text-gray-500">{r.status ?? '—'}</td>
                          <td className="py-1.5 pr-3">
                            <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold ${
                              r.pass
                                ? 'bg-emerald-50 text-emerald-600 border border-emerald-200'
                                : 'bg-red-50 text-red-600 border border-red-200'
                            }`}>
                              {r.pass ? 'PASS' : 'FAIL'}
                            </span>
                          </td>
                          <td className="py-1.5 text-gray-600 font-mono text-[11px]">
                            {r.pass
                              ? (r.ratingValue != null && r.reviewCount != null
                                  ? `${r.ratingValue}★ · ${r.reviewCount} reviews`
                                  : '—')
                              : (r.reason || 'fail')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })()}
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Trustpilot Refresh Cron">
        {/*
          Task #755 — surface the daily refresh-cron heartbeat next to
          the existing Trustpilot data tile so admins can spot a silent
          cron at a glance instead of waiting for the email. Endpoint
          shape comes from /admin/health/trustpilot/refresh-cron (Task
          #751). Task #835 — the visual pill is the shared
          <CronHealthPill> component. Task #838 — the configuration
          (header text per status, two-line success/any heartbeat
          caption, default workflow URL) was extracted into
          <TrustpilotRefreshCronPill> so its colour mapping and
          dual-heartbeat caption can be unit-tested in isolation
          (see TrustpilotRefreshCronPill.test.jsx). testId moved from
          "trustpilot-cron" to "trustpilot-refresh-cron" to align
          with the cf-waf-drift pill's naming convention.
        */}
        <TrustpilotRefreshCronPill
          data={tpCronHealth}
          loading={tpCronLoading}
          onRefresh={loadTpCronHealth}
          alertState={tpCronAlertState}
          alertHistory={tpCronAlertHistory}
          onLoadAlertHistory={loadTpCronAlertHistory}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Cloudflare WAF Drift Cron">
        {/*
          Task #833 — sibling pill for the daily cf-waf-drift-daily
          workflow heartbeat (Task #831). Same shape as the Trustpilot
          refresh-cron pill above, with one addition: a "Last run"
          deep-link when the heartbeat carries one, since jumping
          straight to the offending GitHub Actions run is the first
          thing an admin wants when the pill turns red. Endpoint:
          /admin/health/cf-waf-drift/cron — status keys mirror the
          Trustpilot endpoint. Task #835 — the visual pill is the
          shared <CronHealthPill> component. Task #836 — the
          configuration was extracted into <CfWafDriftCronPill> so
          its colour mapping, heartbeat-age caption, and conditional
          verify/aggregate-RC text can be unit-tested in isolation
          (see CfWafDriftCronPill.test.jsx).
        */}
        <CfWafDriftCronPill
          data={cfDriftCronHealth}
          loading={cfDriftCronLoading}
          onRefresh={loadCfDriftCronHealth}
          alertState={cfDriftCronAlertState}
          alertHistory={cfDriftCronAlertHistory}
          onLoadAlertHistory={loadCfDriftCronAlertHistory}
          slackMissingAlertState={slackWebhookMissingAlertStates['CF_WAF_DRIFT_SLACK_WEBHOOK']}
          slackMissingAlertHistory={slackWebhookMissingAlertHistories['CF_WAF_DRIFT_SLACK_WEBHOOK']}
          onSnoozeSlackMissing={snoozeSlackWebhookMissing}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Edge-Proxy Deploy CI">
        {/*
          Task #882 — surface the latest `edge-proxy-deploy` GitHub
          Actions run next to the other cron pills. The workflow runs
          unattended on every push to master that touches
          workers/edge-proxy/**; its `smoke-preview` job is the
          canonical signal that the latest worker build still passes
          the burst / D1 / KV / bot-cache checks. A red badge there
          previously only lived in the GitHub Actions UI — this pill
          puts it on the AdminHealth dashboard on-call already
          watches. Endpoint: /admin/health/edge-proxy-deploy/cron.
        */}
        <EdgeProxyDeployCronPill
          data={edgeProxyDeployCronHealth}
          loading={edgeProxyDeployCronLoading}
          onRefresh={loadEdgeProxyDeployCronHealth}
          alertState={edgeProxyDeployCronAlertState}
          alertHistory={edgeProxyDeployCronAlertHistory}
          onLoadAlertHistory={loadEdgeProxyDeployCronAlertHistory}
          slackMissingAlertState={slackWebhookMissingAlertStates['EDGE_PROXY_DEPLOY_SLACK_WEBHOOK']}
          slackMissingAlertHistory={slackWebhookMissingAlertHistories['EDGE_PROXY_DEPLOY_SLACK_WEBHOOK']}
          onSnoozeSlackMissing={snoozeSlackWebhookMissing}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Cloudflare Log Ingest">
        {/*
          Task #956 — surface the unified-logs Cloudflare GraphQL pull
          silence alerter (Task #951) on the AdminHealth dashboard
          alongside the other cron pills. Until this pill shipped, the
          only signal that ingest had stalled was the on-call page or
          the cf_pull_last_run timestamp on /api/admin/logs/status
          quietly growing old. The pill turns red when the lock doc's
          updated_at is older than ~3× the configured pull interval
          (default 5 min floor), shows the lease owner and last
          successful cursor advance inline, and exposes the same
          "last paged Xh ago · in debounce ~Yh" caption + paged
          history disclosure as its siblings.
          Endpoint: /admin/health/unified-logs/cf-pull/cron.
          Tasks #957 / #963 — the alerter pages on three channels
          (in-app + email + Slack), matching the cf-waf-drift /
          edge-proxy-deploy pills. Slack is the third best-effort
          channel and is gated on `UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK`
          being set on the backend; the shared SlackConfigBadge
          inside <CronHealthPill> renders a "Slack ✓ / ✗" indicator
          next to this pill so a deploy-without-Slack-coverage gap is
          visible at a glance. See §8.7.7 of CLOUDFLARE_ZERO_TRUST.md
          for the sibling-webhook table.
        */}
        <UnifiedLogsCfPullCronPill
          data={unifiedLogsCfPullCronHealth}
          loading={unifiedLogsCfPullCronLoading}
          onRefresh={loadUnifiedLogsCfPullCronHealth}
          alertState={unifiedLogsCfPullCronAlertState}
          alertHistory={unifiedLogsCfPullCronAlertHistory}
          onLoadAlertHistory={loadUnifiedLogsCfPullCronAlertHistory}
          slackMissingAlertState={slackWebhookMissingAlertStates['UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK']}
          slackMissingAlertHistory={slackWebhookMissingAlertHistories['UNIFIED_LOGS_CF_PULL_SLACK_WEBHOOK']}
          onSnoozeSlackMissing={snoozeSlackWebhookMissing}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="D1 Mirror Lag">
        {/*
          Task #508 — surface the D1 mirror lag alerter (Task #460)
          on the AdminHealth dashboard alongside the other cron pills.
          The alerter watches the cross-replica nightly mirror lease's
          ``last_fired_at`` timestamp and pages on-call when the lag
          exceeds ``D1_MIRROR_LAG_THRESHOLD_S`` for
          ``D1_MIRROR_LAG_REQUIRED_STREAK`` consecutive checks. Until
          this pill shipped, admins couldn't visually see "the mirror
          is N hours behind, paging in M more polls" without hitting
          the JSON endpoint manually. The shared <CronHealthPill>
          backbone gives us the same colour cascade + paged-history
          disclosure as the sibling alerters; the wrapper adapts the
          alerter's ``not_enabled / never_observed / breached / healthy``
          vocabulary onto the shared ``not_configured / never_observed
          / silent / healthy`` keys.
        */}
        <D1MirrorLagPill
          data={d1MirrorLagHealth}
          loading={d1MirrorLagLoading}
          onRefresh={loadD1MirrorLagHealth}
          alertHistory={d1MirrorLagAlertHistory}
          onLoadAlertHistory={loadD1MirrorLagAlertHistory}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Cloudflare Audit Card">
        {/*
          Task #133 — surface the latest cloudflare-weekly-audit.yml run
          (19-item full audit, Phases 1–6) on the admin health panel so
          on-call sees the pass/warn/fail breakdown without navigating
          to GitHub Actions.  The card fetches via
          /admin/health/cf-audit/latest which:
            • queries GitHub API for the latest run conclusion + age,
            • downloads the cf-audit-report artifact ZIP,
            • parses the embedded JSON for PASS/WARN/FAIL/PLAN_REQUIRED counts,
            • caches the summary in Redis per run_id for 4 hours.
          The card turns amber ("stale") when the last run is >8 days old
          and red when conclusion=failure.  Clicking "View run" deep-links
          to the specific GitHub Actions run.
        */}
        <CfAuditCard
          data={cfAuditData}
          loading={cfAuditLoading}
          onRefresh={loadCfAudit}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Embed Backfill Progress">
        {/*
          Task #433 — surface the legacy → workers_ai_custom embed
          backfill (Task #411) on the admin dashboard so on-call can
          see *which* old provider produced the chunks still pending
          re-embed (cohere / voyage / (missing)) instead of treating
          the backlog as one opaque bucket. Endpoint:
          /admin/embed/backfill/progress.
        */}
        <EmbedBackfillPill adminToken={adminToken} />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Embed Stack Health">
        {/*
          Task #436 — surface the live per-leg watchdog state from
          GET /admin/health/embed-stack so on-call sees "embed leg has
          failed 2/3 times" *before* the Task #412 page fires, and can
          confirm recovery without waiting for the recovery alert.
          Each of the three legs (embed / rerank / memory_brain) gets
          a small badge that turns amber during the warm-up window
          (1..threshold-1 failures) and red the moment the watchdog
          latches firing=true.
        */}
        <EmbedStackHealthPill adminToken={adminToken} />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Assamese Corpus Coverage">
        {/*
          Task #45 — surface per-collection Assamese coverage against
          the 0.85 script-ratio gate so the lock §6 row's health is
          observable instead of inferred. Reads
          /api/health/corpus/assamese which aggregates the four
          tracked collections (subjects, chapters, seo_pages,
          pyq_html_pages) and the latest run report's accept/reject
          counts so on-call can see WHY a collection isn't moving.
          AssameseBackfillPanel supersedes AssameseCorpusCoveragePill
          by adding a full trigger UI with force-regenerate mode,
          per-collection progress tracking and live polling.
        */}
        <AssameseBackfillPanel adminToken={adminToken} />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="AI Gateway Cache by Model">
        {/*
          Task #419 — surface "top models by cache hit ratio" from the
          unified /admin/cf-health snapshot. Until this tile shipped,
          on-call could only see aggregate hit/miss totals on the CF
          panel and had to slice ai_gateway.recent_samples by hand to
          tell whether the cache was actually doing its job for the
          high-volume models (e.g. llama-3.3-70b-instruct-fp8-fast vs
          gpt-oss-120b). A model with no cache telemetry in the window
          renders as "—" rather than 0% so it isn't mistaken for a
          100% miss outlier.
        */}
        <AiGatewayCacheByModelTile
          data={cfHealthData?.ai_gateway}
          loading={cfHealthLoading}
          onRefresh={loadCfHealth}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="AI Gateway Guardrail by Model">
        {/*
          Task #448 — sibling of the cache-by-model tile above. The
          AI Gateway already counted aggregate guardrail allow/rewrite/
          block totals, but on-call had no way to tell *which* model
          was disproportionately tripping the Llama-Guard / AI Content
          Safety layer without slicing ai_gateway.recent_samples by
          hand. This tile renders the same recent-samples window
          bucketed by (provider, model) → block ratio, with "—" for
          rows that carry no guardrail telemetry so a quiet model is
          not painted as a 0%-blocked outlier.
        */}
        <AiGatewayGuardrailByModelTile
          data={cfHealthData?.ai_gateway}
          loading={cfHealthLoading}
          onRefresh={loadCfHealth}
          alerterState={aigGuardrailAlertState}
        />
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Signup Throttle">
        {/*
          Task #430 — Task #407 added a per-IP signup rate gate on
          /auth/signup that flows through do_chat.rate_check, but
          blocked attempts only bumped the shared
          do_chat.rate_check_blocked counter. That counter mixes
          chat throttling and signup throttling on the same number,
          so on-call had no way to tell whether a spike was a
          bot-signup wave or normal chat traffic. The backend now
          exposes per-prefix breakdowns
          (rate_check_blocked_by_prefix / rate_check_total_by_prefix)
          and this tile renders the "signup" slice. Task #462 — the
          headline number is now the rolling 1-hour blocked count
          (``rate_check_blocked_by_prefix_last_hour``) so on-call sees
          a stable "spike right now" signal that survives ACA revision
          rolls; the process-lifetime count is kept as a small caption
          for context.
        */}
        {(() => {
          const doChat = cfHealthData?.do_chat;
          if (!doChat || doChat.error) return null;
          const blockedByPrefix = doChat.rate_check_blocked_by_prefix || {};
          const blockedLastHour = doChat.rate_check_blocked_by_prefix_last_hour || {};
          const totalByPrefix = doChat.rate_check_total_by_prefix || {};
          const signupBlockedLifetime = blockedByPrefix.signup || 0;
          const signupBlockedHour = blockedLastHour.signup || 0;
          const signupTotal = totalByPrefix.signup || 0;
          const ratio = signupTotal > 0 ? signupBlockedLifetime / signupTotal : 0;
          const tone = signupBlockedLifetime > 0 ? 'amber' : 'emerald';
          const colors = tone === 'amber'
            ? { tile: 'bg-amber-50 border-amber-200', icon: 'bg-amber-100 text-amber-500', heading: 'text-amber-600' }
            : { tile: 'bg-emerald-50 border-emerald-200', icon: 'bg-emerald-100 text-emerald-500', heading: 'text-emerald-600' };
          return (
            <div
              className={`rounded-2xl p-4 border ${colors.tile}`}
              data-testid="signup-throttle-tile"
            >
              <div className="flex items-center gap-3 mb-3">
                <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${colors.icon}`}>
                  <ShieldCheck size={17} />
                </div>
                <div className="flex-1 min-w-0">
                  <p className={`text-sm font-semibold ${colors.heading}`}>
                    Signup throttle (do_chat)
                  </p>
                  <p className="text-[11px] text-gray-500 mt-0.5">
                    Per-IP signup rate gate · /auth/signup · Task #407
                  </p>
                </div>
                <button
                  onClick={loadCfHealth}
                  disabled={cfHealthLoading}
                  className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-white"
                  data-testid="button-refresh-signup-throttle"
                >
                  <RefreshCw size={14} className={cfHealthLoading ? 'animate-spin' : ''} />
                </button>
              </div>
              <div className="grid grid-cols-3 gap-3">
                <div className="rounded-xl p-3 border border-gray-200 bg-white">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">
                    Blocked signups (last hour)
                  </p>
                  <p
                    className="text-2xl font-bold font-mono text-gray-900"
                    data-testid="signup-throttle-blocked-hour"
                  >
                    {signupBlockedHour}
                  </p>
                  <p className="text-[10px] text-gray-400 mt-0.5">
                    <span data-testid="signup-throttle-blocked">{signupBlockedLifetime}</span> since pod start
                  </p>
                </div>
                <div className="rounded-xl p-3 border border-gray-200 bg-white">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">
                    Total checks
                  </p>
                  <p
                    className="text-2xl font-bold font-mono text-gray-900"
                    data-testid="signup-throttle-total"
                  >
                    {signupTotal}
                  </p>
                  <p className="text-[10px] text-gray-400 mt-0.5">
                    signup-scoped only
                  </p>
                </div>
                <div className="rounded-xl p-3 border border-gray-200 bg-white">
                  <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">
                    Block ratio
                  </p>
                  <p
                    className="text-2xl font-bold font-mono text-gray-900"
                    data-testid="signup-throttle-ratio"
                  >
                    {signupTotal > 0 ? `${Math.round(ratio * 100)}%` : '—'}
                  </p>
                  <p className="text-[10px] text-gray-400 mt-0.5">
                    blocked ÷ total
                  </p>
                </div>
              </div>
            </div>
          );
        })()}
        </SectionErrorBoundary>

        <SectionErrorBoundary name="GCP Credit Panel">
        {(() => {
          const gc = gcpCredits && !gcpCredits._error ? gcpCredits : null;
          const saConfigured = gc?.service_account_configured ?? false;
          const creditsLow = gc?.credits_low ?? false;
          const liveSpend = gc?.live_spend_data ?? false;
          const liveBudget = gc?.live_budget_data ?? false;
          const isUnconfigured = !gcpCreditsLoading && (!gc || !saConfigured);
          const tileCls = creditsLow
            ? 'bg-red-50 border-red-200'
            : isUnconfigured
              ? 'bg-gray-50 border-gray-200'
              : 'bg-emerald-50 border-emerald-200';
          const headerCls = creditsLow
            ? 'text-red-600'
            : isUnconfigured
              ? 'text-gray-500'
              : 'text-emerald-600';
          return (
            <div className={`rounded-2xl p-4 border ${tileCls}`} data-testid="gcp-credit-panel">
              <div className="flex items-center gap-3 mb-3">
                <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${
                  creditsLow ? 'bg-red-100' : isUnconfigured ? 'bg-gray-100' : 'bg-emerald-100'
                }`}>
                  <DollarSign size={17} className={creditsLow ? 'text-red-500' : isUnconfigured ? 'text-gray-400' : 'text-emerald-500'} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className={`text-sm font-semibold ${headerCls}`} data-testid="gcp-credit-heading">
                      Google Cloud Credits
                    </p>
                    {creditsLow && (
                      <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-600 border border-red-300 uppercase tracking-wide" data-testid="gcp-credits-low-badge">
                        Credits Low
                      </span>
                    )}
                    {gc && (liveSpend || liveBudget) && (
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
                        liveSpend
                          ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                          : 'bg-amber-50 text-amber-600 border-amber-200'
                      }`} data-testid="gcp-data-source-badge">
                        {liveSpend ? 'Live · BigQuery' : 'Live · Budget API'}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-gray-500 mt-0.5">
                    {isUnconfigured
                      ? 'GCP service account not configured'
                      : gc?.billing_account_name
                        ? gc.billing_account_name
                        : gc?.billing_account_id
                          ? `Account: ${gc.billing_account_id}`
                          : 'GCP Billing'}
                  </p>
                </div>
                <button
                  onClick={loadGcpCredits}
                  disabled={gcpCreditsLoading}
                  className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                  data-testid="button-refresh-gcp-credits"
                  title="Refresh GCP credit data"
                >
                  <RefreshCw size={13} className={gcpCreditsLoading ? 'animate-spin' : ''} />
                </button>
              </div>

              {gcpCreditsLoading && !gc && (
                <div className="flex justify-center py-4">
                  <RefreshCw size={16} className="animate-spin text-gray-300" />
                </div>
              )}

              {gcpCredits?._error && (
                <p className="text-xs text-red-500 mt-1">Failed to load GCP credit data — check backend logs.</p>
              )}

              {isUnconfigured && !gcpCredits?._error && (
                <div className="mt-2 space-y-1.5">
                  <p className="text-xs text-gray-600 font-medium">Setup instructions:</p>
                  <ol className="space-y-1">
                    {[
                      'Create a GCP service account with roles/billing.viewer',
                      'Download its JSON key and set GOOGLE_APPLICATION_CREDENTIALS_JSON in your env',
                      'Set GOOGLE_BILLING_ACCOUNT_ID to enable live budget data',
                      'Enable BigQuery Billing Export for real per-service spend figures',
                    ].map((s, i) => (
                      <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                        <span className="w-4 h-4 rounded-full bg-blue-50 flex items-center justify-center text-[9px] font-bold text-blue-600 flex-shrink-0 mt-0.5">{i + 1}</span>
                        {s}
                      </li>
                    ))}
                  </ol>
                </div>
              )}

              {gc && saConfigured && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-1">
                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Grant Total</p>
                    <p className="text-sm font-bold font-mono text-gray-900" data-testid="gcp-grant-usd">
                      ${gc.grant_usd != null ? Number(gc.grant_usd).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 }) : '—'}
                    </p>
                  </div>
                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Spend MTD</p>
                    <p className={`text-sm font-bold font-mono ${creditsLow ? 'text-red-600' : 'text-gray-900'}`} data-testid="gcp-spend-mtd">
                      ${gc.spend_mtd_usd != null ? Number(gc.spend_mtd_usd).toFixed(2) : '—'}
                    </p>
                  </div>
                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Remaining</p>
                    <p className={`text-sm font-bold font-mono ${creditsLow ? 'text-red-600' : 'text-emerald-600'}`} data-testid="gcp-remaining">
                      ${gc.estimated_remaining_usd != null ? Number(gc.estimated_remaining_usd).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}
                    </p>
                  </div>
                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Runway</p>
                    <p className={`text-sm font-bold font-mono ${creditsLow ? 'text-red-600' : 'text-gray-900'}`} data-testid="gcp-runway">
                      {gc.months_runway != null
                        ? (gc.months_runway >= 999 ? '∞' : `${Number(gc.months_runway).toFixed(1)} mo`)
                        : '—'}
                    </p>
                  </div>
                </div>
              )}

              {gc && gc.billing_alert_active && (
                <div className="flex items-start gap-2 p-2 mt-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-700">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                  <span>GCP billing alert is active — a budget threshold has been breached.</span>
                </div>
              )}

              {gc && gc.billing_api_error && (
                <p className="text-[11px] text-amber-600 mt-2 font-mono break-all">{gc.billing_api_error}</p>
              )}
            </div>
          );
        })()}
        </SectionErrorBoundary>

        {/* Task #263 — CF paid add-on migration status panel */}
        <SectionErrorBoundary name="CF Add-on Migration">
        {(() => {
          const data = cfAddons && !cfAddons._error ? cfAddons : null;
          const counts = data?.status_counts ?? { pending: 0, in_progress: 0, complete: 0 };
          const totalPending = data?.monthly_savings_pending_usd ?? 0;
          const totalSaved  = data?.monthly_savings_realised_usd ?? 0;
          const allDone     = data && counts.pending === 0 && counts.in_progress === 0;
          const anyInProgress = counts.in_progress > 0;
          const tileCls = allDone
            ? 'bg-emerald-50 border-emerald-200'
            : anyInProgress
              ? 'bg-blue-50 border-blue-200'
              : 'bg-amber-50 border-amber-200';
          const headerCls = allDone
            ? 'text-emerald-700'
            : anyInProgress
              ? 'text-blue-700'
              : 'text-amber-700';
          const STATUS_STYLE = {
            complete:    'bg-emerald-100 text-emerald-700 border-emerald-200',
            in_progress: 'bg-blue-100 text-blue-700 border-blue-200',
            pending:     'bg-amber-100 text-amber-700 border-amber-200',
          };
          const STATUS_LABEL = {
            complete:    '✅ Complete',
            in_progress: '🔵 In Progress',
            pending:     '🟡 Pending',
          };
          return (
            <div className={`rounded-2xl p-4 border ${tileCls}`} data-testid="cf-addon-migration-panel">
              <div className="flex items-center gap-3 mb-3">
                <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${
                  allDone ? 'bg-emerald-100' : anyInProgress ? 'bg-blue-100' : 'bg-amber-100'
                }`}>
                  <DollarSign size={17} className={allDone ? 'text-emerald-500' : anyInProgress ? 'text-blue-500' : 'text-amber-500'} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <p className={`text-sm font-semibold ${headerCls}`} data-testid="cf-addon-heading">
                      CF Add-on Migration
                    </p>
                    {data && (
                      <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
                        allDone
                          ? 'bg-emerald-50 text-emerald-600 border-emerald-200'
                          : 'bg-amber-50 text-amber-700 border-amber-200'
                      }`} data-testid="cf-addon-savings-badge">
                        {allDone ? `$${totalSaved}/mo saved` : `$${totalPending}/mo remaining`}
                      </span>
                    )}
                  </div>
                  <p className="text-[11px] text-gray-500 mt-0.5">
                    Replace paid CF add-ons with startup-credit-covered alternatives — Task #263
                  </p>
                </div>
                <button
                  onClick={loadCfAddons}
                  disabled={cfAddonsLoading}
                  className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                  data-testid="button-refresh-cf-addons"
                  title="Refresh CF add-on migration status"
                >
                  <RefreshCw size={13} className={cfAddonsLoading ? 'animate-spin' : ''} />
                </button>
              </div>

              {cfAddonsLoading && !data && (
                <div className="flex justify-center py-4">
                  <RefreshCw size={16} className="animate-spin text-gray-300" />
                </div>
              )}

              {cfAddons?._error && (
                <p className="text-xs text-red-500 mt-1">Failed to load CF add-on data — check backend logs.</p>
              )}

              {data && (
                <>
                  {/* Status summary counts */}
                  <div className="flex gap-2 flex-wrap mb-3">
                    {[
                      { key: 'complete',    label: 'Complete' },
                      { key: 'in_progress', label: 'In Progress' },
                      { key: 'pending',     label: 'Pending' },
                    ].map(({ key, label }) => counts[key] > 0 && (
                      <span key={key} className={`text-[10px] font-semibold px-2 py-0.5 rounded-full border ${STATUS_STYLE[key]}`}>
                        {counts[key]} {label}
                      </span>
                    ))}
                    {totalSaved > 0 && (
                      <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full border bg-emerald-50 text-emerald-700 border-emerald-200">
                        ${totalSaved}/mo saved so far
                      </span>
                    )}
                  </div>

                  {/* Add-on rows */}
                  <div className="space-y-2">
                    {(data.addons || []).map((addon, i) => (
                      <div key={i} className="rounded-xl p-3 bg-white/70 border border-white/80 text-xs">
                        <div className="flex items-start justify-between gap-2 mb-1">
                          <div className="flex items-center gap-2 flex-wrap min-w-0">
                            <span className="font-semibold text-gray-900 shrink-0">{addon.service}</span>
                            <span className="font-mono text-gray-500 shrink-0">${addon.monthly_cost_usd}/mo</span>
                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full border shrink-0 ${STATUS_STYLE[addon.status] ?? STATUS_STYLE.pending}`}>
                              {STATUS_LABEL[addon.status] ?? addon.status}
                            </span>
                          </div>
                        </div>
                        <p className="text-gray-600 mb-0.5">
                          <span className="text-gray-400">→ </span>{addon.migration_target}
                        </p>
                        <p className="text-gray-400">
                          Covered by: <span className="text-gray-600">{addon.credit_programme}</span>
                        </p>
                        {addon.notes && (
                          <p className="text-gray-400 mt-1 leading-snug">{addon.notes}</p>
                        )}
                      </div>
                    ))}
                  </div>

                  {data.runbook_url && (
                    <a
                      href={data.runbook_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 mt-3 text-[11px] text-blue-500 hover:text-blue-700 hover:underline"
                      data-testid="cf-addon-runbook-link"
                    >
                      <ExternalLink size={11} />
                      View migration runbook
                    </a>
                  )}
                </>
              )}
            </div>
          );
        })()}
        </SectionErrorBoundary>
        {/* Task #263 — Startup credit burn panels: AWS Activate, Azure, Axiom, Sentry */}
        <SectionErrorBoundary name="Startup Credit Panels">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">

          {/* ── AWS Activate ─────────────────────────────────────────────── */}
          {(() => {
            const d = awsCredits && !awsCredits._error ? awsCredits : null;
            const low = d?.credits_low ?? false;
            const unconfigured = !awsCreditsLoading && (!d || !d.configured);
            const tile = low ? 'bg-red-50 border-red-200' : unconfigured ? 'bg-gray-50 border-gray-200' : 'bg-orange-50 border-orange-200';
            const hdr = low ? 'text-red-600' : unconfigured ? 'text-gray-500' : 'text-orange-600';
            return (
              <div className={`rounded-2xl p-4 border ${tile}`} data-testid="aws-credit-panel">
                <div className="flex items-center gap-3 mb-3">
                  <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${low ? 'bg-red-100' : unconfigured ? 'bg-gray-100' : 'bg-orange-100'}`}>
                    <DollarSign size={17} className={low ? 'text-red-500' : unconfigured ? 'text-gray-400' : 'text-orange-500'} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className={`text-sm font-semibold ${hdr}`} data-testid="aws-credit-heading">AWS Activate</p>
                      {low && <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-600 border border-red-300 uppercase tracking-wide">Credits Low</span>}
                      {d?.services?.length > 0 && (
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full border bg-orange-50 text-orange-600 border-orange-200">
                          Lambda · SES · Route 53 · CloudFront · Bedrock
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5">
                      {unconfigured ? 'AWS cost explorer not configured' : d?.account_alias ?? 'AWS Activate (Portfolio)'}
                    </p>
                  </div>
                  <button onClick={loadAwsCredits} disabled={awsCreditsLoading}
                    className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                    data-testid="button-refresh-aws-credits" title="Refresh AWS credit data">
                    <RefreshCw size={13} className={awsCreditsLoading ? 'animate-spin' : ''} />
                  </button>
                </div>

                {awsCreditsLoading && !d && <div className="flex justify-center py-4"><RefreshCw size={16} className="animate-spin text-gray-300" /></div>}
                {awsCredits?._error && <p className="text-xs text-red-500 mt-1">Failed to load AWS credit data — check backend logs.</p>}

                {unconfigured && !awsCredits?._error && (
                  <div className="mt-2 space-y-1.5">
                    <p className="text-xs text-gray-600 font-medium">Setup instructions:</p>
                    <ol className="space-y-1">
                      {[
                        'Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY with ce:GetCostAndUsage permission',
                        'Set AWS_ACTIVATE_GRANT_USD to the Activate programme grant total',
                        'Set AWS_ACTIVATE_EXPIRY to the credit expiry date (YYYY-MM-DD)',
                      ].map((s, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                          <span className="w-4 h-4 rounded-full bg-orange-50 flex items-center justify-center text-[9px] font-bold text-orange-600 flex-shrink-0 mt-0.5">{i + 1}</span>
                          {s}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}

                {d && d.configured && (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-1">
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Grant Total</p>
                      <p className="text-sm font-bold font-mono text-gray-900" data-testid="aws-grant-usd">
                        ${d.grant_usd != null ? Number(d.grant_usd).toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'}
                      </p>
                    </div>
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Spend MTD</p>
                      <p className={`text-sm font-bold font-mono ${low ? 'text-red-600' : 'text-gray-900'}`} data-testid="aws-spend-mtd">
                        ${d.spend_mtd_usd != null ? Number(d.spend_mtd_usd).toFixed(2) : '—'}
                      </p>
                    </div>
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Remaining</p>
                      <p className={`text-sm font-bold font-mono ${low ? 'text-red-600' : 'text-orange-600'}`} data-testid="aws-remaining">
                        ${d.estimated_remaining_usd != null ? Number(d.estimated_remaining_usd).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}
                      </p>
                    </div>
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Runway</p>
                      <p className={`text-sm font-bold font-mono ${low ? 'text-red-600' : 'text-gray-900'}`} data-testid="aws-runway">
                        {d.months_runway != null ? (d.months_runway >= 999 ? '∞' : `${Number(d.months_runway).toFixed(1)} mo`) : '—'}
                      </p>
                    </div>
                  </div>
                )}
                {d && d.days_until_expiry != null && (
                  <p className="text-[11px] text-gray-500 mt-2">
                    Credits expire: <span className="font-mono font-semibold">{d.expiry_date ?? '—'}</span>
                    {' '}(<span className={d.days_until_expiry < 60 ? 'text-red-500 font-semibold' : 'text-gray-600'}>{d.days_until_expiry}d remaining</span>)
                  </p>
                )}
              </div>
            );
          })()}

          {/* ── GCP Credits ───────────────────────────────────────── */}
          {(() => {
            const d = gcpCredits && !gcpCredits._error ? gcpCredits : null;
            const low = d?.credits_low ?? false;
            const unconfigured = !gcpCreditsLoading && (!d || !d.configured);
            const tile = low ? 'bg-red-50 border-red-200' : unconfigured ? 'bg-gray-50 border-gray-200' : 'bg-blue-50 border-blue-200';
            const hdr = low ? 'text-red-600' : unconfigured ? 'text-gray-500' : 'text-blue-600';
            return (
              <div className={`rounded-2xl p-4 border ${tile}`} data-testid="gcp-credit-panel">
                <div className="flex items-center gap-3 mb-3">
                  <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${low ? 'bg-red-100' : unconfigured ? 'bg-gray-100' : 'bg-blue-100'}`}>
                    <DollarSign size={17} className={low ? 'text-red-500' : unconfigured ? 'text-gray-400' : 'text-blue-500'} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className={`text-sm font-semibold ${hdr}`} data-testid="gcp-credit-heading">GCP Credits</p>
                      {low && <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-600 border border-red-300 uppercase tracking-wide">Credits Low</span>}
                      {d?.configured && (
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full border bg-blue-50 text-blue-600 border-blue-200">
                          Cloud Run · Vertex AI · Secret Manager
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5">
                      {unconfigured ? 'GCP Billing not configured' : d?.subscription_name ?? 'GCP Credits'}
                    </p>
                  </div>
                  <button onClick={loadGcpCredits} disabled={gcpCreditsLoading}
                    className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                    data-testid="button-refresh-gcp-credits" title="Refresh GCP credit data">
                    <RefreshCw size={13} className={gcpCreditsLoading ? 'animate-spin' : ''} />
                  </button>
                </div>

                {gcpCreditsLoading && !d && <div className="flex justify-center py-4"><RefreshCw size={16} className="animate-spin text-gray-300" /></div>}
                {gcpCredits?._error && <p className="text-xs text-red-500 mt-1">Failed to load GCP credit data - check backend logs.</p>}

                {unconfigured && !gcpCredits?._error && (
                  <div className="mt-2 space-y-1.5">
                    <p className="text-xs text-gray-600 font-medium">Setup instructions:</p>
                    <ol className="space-y-1">
                      {[
                        'Create a GCP service account with Billing Account Viewer role',
                        'Set GCP_PROJECT_ID and GOOGLE_APPLICATION_CREDENTIALS_JSON',
                        'Set GCP_CREDITS_GRANT_USD and GCP_CREDITS_EXPIRY (YYYY-MM-DD)',
                      ].map((s, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                          <span className="w-4 h-4 rounded-full bg-blue-50 flex items-center justify-center text-[9px] font-bold text-blue-600 flex-shrink-0 mt-0.5">{i + 1}</span>
                          {s}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}

                {d && d.configured && (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-1">
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Grant Total</p>
                      <p className="text-sm font-bold font-mono text-gray-900" data-testid="azure-grant-usd">
                        ${d.grant_usd != null ? Number(d.grant_usd).toLocaleString(undefined, { maximumFractionDigits: 0 }) : '—'}
                      </p>
                    </div>
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Spend MTD</p>
                      <p className={`text-sm font-bold font-mono ${low ? 'text-red-600' : 'text-gray-900'}`} data-testid="azure-spend-mtd">
                        ${d.spend_mtd_usd != null ? Number(d.spend_mtd_usd).toFixed(2) : '—'}
                      </p>
                    </div>
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Remaining</p>
                      <p className={`text-sm font-bold font-mono ${low ? 'text-red-600' : 'text-blue-600'}`} data-testid="azure-remaining">
                        ${d.estimated_remaining_usd != null ? Number(d.estimated_remaining_usd).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}
                      </p>
                    </div>
                    <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                      <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Runway</p>
                      <p className={`text-sm font-bold font-mono ${low ? 'text-red-600' : 'text-gray-900'}`} data-testid="azure-runway">
                        {d.months_runway != null ? (d.months_runway >= 999 ? '∞' : `${Number(d.months_runway).toFixed(1)} mo`) : '—'}
                      </p>
                    </div>
                  </div>
                )}
                {d && d.days_until_expiry != null && (
                  <p className="text-[11px] text-gray-500 mt-2">
                    Credits expire: <span className="font-mono font-semibold">{d.expiry_date ?? '—'}</span>
                    {' '}(<span className={d.days_until_expiry < 60 ? 'text-red-500 font-semibold' : 'text-gray-600'}>{d.days_until_expiry}d remaining</span>)
                  </p>
                )}
              </div>
            );
          })()}

          {/* ── Axiom startup tier ───────────────────────────────────────── */}
          {(() => {
            const d = axiomCredits && !axiomCredits._error ? axiomCredits : null;
            const overLimit = d?.over_limit ?? false;
            const unconfigured = !axiomCreditsLoading && (!d || !d.configured);
            const tile = overLimit ? 'bg-red-50 border-red-200' : unconfigured ? 'bg-gray-50 border-gray-200' : 'bg-violet-50 border-violet-200';
            const hdr = overLimit ? 'text-red-600' : unconfigured ? 'text-gray-500' : 'text-violet-600';
            const ingestPct = d?.ingest_gb != null && d?.ingest_limit_gb != null
              ? Math.min(100, Math.round((d.ingest_gb / d.ingest_limit_gb) * 100))
              : null;
            return (
              <div className={`rounded-2xl p-4 border ${tile}`} data-testid="axiom-credit-panel">
                <div className="flex items-center gap-3 mb-3">
                  <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${overLimit ? 'bg-red-100' : unconfigured ? 'bg-gray-100' : 'bg-violet-100'}`}>
                    <BarChart2 size={17} className={overLimit ? 'text-red-500' : unconfigured ? 'text-gray-400' : 'text-violet-500'} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className={`text-sm font-semibold ${hdr}`} data-testid="axiom-credit-heading">Axiom Log Explorer</p>
                      {overLimit && <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-600 border border-red-300 uppercase tracking-wide">Over Limit</span>}
                      {d?.configured && !overLimit && (
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full border bg-violet-50 text-violet-600 border-violet-200">Startup Tier · 500 GB/mo</span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5">
                      {unconfigured ? 'Axiom API token not configured' : 'Replaces Cloudflare Log Explorer'}
                    </p>
                  </div>
                  <button onClick={loadAxiomCredits} disabled={axiomCreditsLoading}
                    className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                    data-testid="button-refresh-axiom-credits" title="Refresh Axiom usage">
                    <RefreshCw size={13} className={axiomCreditsLoading ? 'animate-spin' : ''} />
                  </button>
                </div>

                {axiomCreditsLoading && !d && <div className="flex justify-center py-4"><RefreshCw size={16} className="animate-spin text-gray-300" /></div>}
                {axiomCredits?._error && <p className="text-xs text-red-500 mt-1">Failed to load Axiom usage data — check backend logs.</p>}

                {unconfigured && !axiomCredits?._error && (
                  <div className="mt-2 space-y-1.5">
                    <p className="text-xs text-gray-600 font-medium">Setup instructions:</p>
                    <ol className="space-y-1">
                      {[
                        'Create an Axiom API token with Query + Ingest permissions',
                        'Set AXIOM_API_TOKEN and AXIOM_ORG_ID on the backend',
                        'Configure Cloudflare Logpush to POST to api.axiom.co/v1/datasets/cf-logs/ingest',
                      ].map((s, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                          <span className="w-4 h-4 rounded-full bg-violet-50 flex items-center justify-center text-[9px] font-bold text-violet-600 flex-shrink-0 mt-0.5">{i + 1}</span>
                          {s}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}

                {d && d.configured && (
                  <div className="space-y-2 mt-1">
                    <div className="grid grid-cols-3 gap-2">
                      <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                        <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Ingest MTD</p>
                        <p className={`text-sm font-bold font-mono ${overLimit ? 'text-red-600' : 'text-gray-900'}`} data-testid="axiom-ingest-gb">
                          {d.ingest_gb != null ? `${Number(d.ingest_gb).toFixed(1)} GB` : '—'}
                        </p>
                      </div>
                      <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                        <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Limit</p>
                        <p className="text-sm font-bold font-mono text-gray-900">
                          {d.ingest_limit_gb != null ? `${d.ingest_limit_gb} GB` : '500 GB'}
                        </p>
                      </div>
                      <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                        <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Retention</p>
                        <p className="text-sm font-bold font-mono text-violet-600">
                          {d.retention_days != null ? `${d.retention_days}d` : '30d'}
                        </p>
                      </div>
                    </div>
                    {ingestPct != null && (
                      <div>
                        <div className="flex justify-between mb-1">
                          <span className="text-[10px] text-gray-500">Monthly ingest usage</span>
                          <span className={`text-[10px] font-semibold ${ingestPct > 80 ? 'text-red-500' : ingestPct > 60 ? 'text-amber-500' : 'text-violet-600'}`}>{ingestPct}%</span>
                        </div>
                        <div className="h-1.5 rounded-full overflow-hidden bg-gray-100">
                          <div
                            style={{ width: `${ingestPct}%`, background: ingestPct > 80 ? '#ef4444' : ingestPct > 60 ? '#f59e0b' : '#7c3aed' }}
                            className="h-full rounded-full transition-all duration-500"
                          />
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })()}

          {/* ── Sentry startup tier ──────────────────────────────────────── */}
          {(() => {
            const d = sentryCredits && !sentryCredits._error ? sentryCredits : null;
            const overLimit = d?.over_limit ?? false;
            const unconfigured = !sentryCreditsLoading && (!d || !d.configured);
            const tile = overLimit ? 'bg-red-50 border-red-200' : unconfigured ? 'bg-gray-50 border-gray-200' : 'bg-indigo-50 border-indigo-200';
            const hdr = overLimit ? 'text-red-600' : unconfigured ? 'text-gray-500' : 'text-indigo-600';
            const errorPct = d?.errors_used != null && d?.errors_limit != null
              ? Math.min(100, Math.round((d.errors_used / d.errors_limit) * 100))
              : null;
            return (
              <div className={`rounded-2xl p-4 border ${tile}`} data-testid="sentry-credit-panel">
                <div className="flex items-center gap-3 mb-3">
                  <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${overLimit ? 'bg-red-100' : unconfigured ? 'bg-gray-100' : 'bg-indigo-100'}`}>
                    <AlertTriangle size={17} className={overLimit ? 'text-red-500' : unconfigured ? 'text-gray-400' : 'text-indigo-500'} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <p className={`text-sm font-semibold ${hdr}`} data-testid="sentry-credit-heading">Sentry</p>
                      {overLimit && <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-red-100 text-red-600 border border-red-300 uppercase tracking-wide">Quota Exceeded</span>}
                      {d?.configured && !overLimit && (
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full border bg-indigo-50 text-indigo-600 border-indigo-200">
                          Startup · {d?.plan ?? 'Team'} plan
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-gray-500 mt-0.5">
                      {unconfigured ? 'Sentry auth token not configured' : 'Error tracking · Perf monitoring'}
                    </p>
                  </div>
                  <button onClick={loadSentryCredits} disabled={sentryCreditsLoading}
                    className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                    data-testid="button-refresh-sentry-credits" title="Refresh Sentry usage">
                    <RefreshCw size={13} className={sentryCreditsLoading ? 'animate-spin' : ''} />
                  </button>
                </div>

                {sentryCreditsLoading && !d && <div className="flex justify-center py-4"><RefreshCw size={16} className="animate-spin text-gray-300" /></div>}
                {sentryCredits?._error && <p className="text-xs text-red-500 mt-1">Failed to load Sentry usage data — check backend logs.</p>}

                {unconfigured && !sentryCredits?._error && (
                  <div className="mt-2 space-y-1.5">
                    <p className="text-xs text-gray-600 font-medium">Setup instructions:</p>
                    <ol className="space-y-1">
                      {[
                        'Generate a Sentry auth token with org:read and project:read scopes',
                        'Set SENTRY_AUTH_TOKEN, SENTRY_ORG, SENTRY_PROJECT on the backend',
                        'Apply for Sentry for Startups at sentry.io/for/startups/',
                      ].map((s, i) => (
                        <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                          <span className="w-4 h-4 rounded-full bg-indigo-50 flex items-center justify-center text-[9px] font-bold text-indigo-600 flex-shrink-0 mt-0.5">{i + 1}</span>
                          {s}
                        </li>
                      ))}
                    </ol>
                  </div>
                )}

                {d && d.configured && (
                  <div className="space-y-2 mt-1">
                    <div className="grid grid-cols-3 gap-2">
                      <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                        <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Errors MTD</p>
                        <p className={`text-sm font-bold font-mono ${overLimit ? 'text-red-600' : 'text-gray-900'}`} data-testid="sentry-errors-used">
                          {d.errors_used != null ? Number(d.errors_used).toLocaleString() : '—'}
                        </p>
                      </div>
                      <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                        <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Quota</p>
                        <p className="text-sm font-bold font-mono text-gray-900">
                          {d.errors_limit != null ? Number(d.errors_limit).toLocaleString() : '—'}
                        </p>
                      </div>
                      <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                        <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Plan Expires</p>
                        <p className={`text-sm font-bold font-mono ${d?.days_until_expiry != null && d.days_until_expiry < 60 ? 'text-red-500' : 'text-indigo-600'}`}>
                          {d.expiry_date ?? '—'}
                        </p>
                      </div>
                    </div>
                    {errorPct != null && (
                      <div>
                        <div className="flex justify-between mb-1">
                          <span className="text-[10px] text-gray-500">Monthly error quota</span>
                          <span className={`text-[10px] font-semibold ${errorPct > 80 ? 'text-red-500' : errorPct > 60 ? 'text-amber-500' : 'text-indigo-600'}`}>{errorPct}%</span>
                        </div>
                        <div className="h-1.5 rounded-full overflow-hidden bg-gray-100">
                          <div
                            style={{ width: `${errorPct}%`, background: errorPct > 80 ? '#ef4444' : errorPct > 60 ? '#f59e0b' : '#6366f1' }}
                            className="h-full rounded-full transition-all duration-500"
                          />
                        </div>
                      </div>
                    )}
                    {d?.perf_transactions_used != null && (
                      <p className="text-[11px] text-gray-500">
                        Perf transactions: <span className="font-mono text-gray-700">{Number(d.perf_transactions_used).toLocaleString()}</span>
                        {d.perf_transactions_limit ? ` / ${Number(d.perf_transactions_limit).toLocaleString()}` : ''}
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })()}

        </div>

        {/* Startup credits summary row */}
        <div className="rounded-2xl p-4 bg-gradient-to-r from-violet-50 via-blue-50 to-orange-50 border border-violet-100">
          <p className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3 flex items-center gap-2">
            <DollarSign size={13} className="text-violet-500" />
            Startup Credit Programmes — Coverage Summary
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
            {[
              { label: 'GCP Activate', amount: '$100 000', expiry: 'Mar 2027', color: 'text-emerald-600', covers: 'Cloud Run · CDN · Storage · Logging' },
              { label: 'AWS Activate', amount: '$100 000', expiry: 'Jan 2027', color: 'text-orange-600', covers: 'Lambda · SES · Route 53 · CloudFront · Bedrock' },
              { label: 'Azure Startups', amount: '$5 000', expiry: 'Jan 2027', color: 'text-blue-600', covers: 'Front Door · Cosmos DB · DDoS · Monitor' },
              { label: 'Axiom + Sentry', amount: 'Free tiers', expiry: 'Ongoing', color: 'text-violet-600', covers: 'Logs · Errors · Perf · Replays' },
            ].map((p) => (
              <div key={p.label} className="rounded-xl p-3 bg-white/70 border border-white">
                <p className={`text-sm font-bold ${p.color}`}>{p.amount}</p>
                <p className="text-[11px] font-semibold text-gray-700 mt-0.5">{p.label}</p>
                <p className="text-[10px] text-gray-400 mt-0.5">expires {p.expiry}</p>
                <p className="text-[10px] text-gray-500 mt-1 leading-tight">{p.covers}</p>
              </div>
            ))}
          </div>
          <p className="text-[11px] text-gray-400 mt-3 text-center">
            Cloudflare Enterprise zone retained for WAF · mTLS · Zero Trust · Pages
            {' '}·{' '}
            <a href="/docs/infra/startup-credits-migration.md" className="underline hover:text-violet-600 transition-colors" target="_blank" rel="noopener noreferrer">
              View migration runbook →
            </a>
          </p>
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Live Traffic Stats">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <PeakBadge label="Active Now (5m)" value={current?.active_5m ?? 0} color="emerald" />
          <PeakBadge label="Peak Users (5m)" value={peaks?.active_users_5m ?? 0} color="violet" />
          <PeakBadge label="Current RPS" value={current?.rps ?? 0} color="blue" />
          <PeakBadge label="Peak RPS" value={peaks?.rps ?? 0} color="amber" />
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Activity Counters">
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <PeakBadge label="Active (15m)" value={current?.active_15m ?? 0} color="emerald" />
          <PeakBadge label="Active (60m)" value={current?.active_60m ?? 0} color="emerald" />
          <PeakBadge label="Total Requests" value={current?.requests ?? 0} color="blue" />
          <PeakBadge label="AI Chats" value={current?.chats ?? 0} color="violet" />
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Active Users Over Time">
        <div className="rounded-xl p-5 bg-white border border-gray-200 shadow-sm">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2">
              <Users size={16} className="text-violet-500" />
              <h3 className="text-sm font-semibold text-gray-900">Active Users Over Time</h3>
            </div>
            <div className="flex gap-1">
              {[
                { label: '1h', val: 60 },
                { label: '6h', val: 360 },
                { label: '24h', val: 1440 },
              ].map(({ label, val }) => (
                <button
                  key={val}
                  onClick={() => setTimeRange(val)}
                  className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                    timeRange === val
                      ? 'bg-violet-50 text-violet-600 border border-violet-200'
                      : 'text-gray-400 hover:text-gray-600 hover:bg-gray-50 border border-transparent'
                  }`}
                  data-testid={`button-range-${label}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          {metricsLoading ? (
            <div className="flex justify-center py-10">
              <RefreshCw size={20} className="animate-spin text-gray-300" />
            </div>
          ) : chartData.length < 2 ? (
            <div className="flex flex-col items-center justify-center py-10 text-gray-400">
              <Activity size={32} className="mb-2 opacity-40" />
              <p className="text-sm">Collecting data... Graph will appear after 2+ minutes.</p>
              <p className="text-xs mt-1 text-gray-300">Snapshots are taken every 60 seconds.</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="grad5m" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#7c3aed" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="grad15m" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.1} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="grad60m" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.1} />
                    <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                <XAxis dataKey="time" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} allowDecimals={false} />
                <Tooltip content={<CustomTooltip />} />
                <Legend
                  wrapperStyle={{ fontSize: 11, color: '#6b7280', paddingTop: 8 }}
                  iconType="circle"
                  iconSize={8}
                />
                <Area type="monotone" dataKey="active_5m" name="Active (5m)" stroke="#7c3aed" fill="url(#grad5m)" strokeWidth={2} dot={false} />
                <Area type="monotone" dataKey="active_15m" name="Active (15m)" stroke="#10b981" fill="url(#grad15m)" strokeWidth={1.5} dot={false} strokeDasharray="4 2" />
                <Area type="monotone" dataKey="active_60m" name="Active (60m)" stroke="#3b82f6" fill="url(#grad60m)" strokeWidth={1.5} dot={false} strokeDasharray="6 3" />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Requests Per Second">
        <div className="rounded-xl p-5 bg-white border border-gray-200 shadow-sm">
          <div className="flex items-center gap-2 mb-4">
            <TrendingUp size={16} className="text-blue-500" />
            <h3 className="text-sm font-semibold text-gray-900">Requests Per Second</h3>
          </div>
          {metricsLoading ? (
            <div className="flex justify-center py-10">
              <RefreshCw size={20} className="animate-spin text-gray-300" />
            </div>
          ) : chartData.length < 2 ? (
            <div className="flex flex-col items-center justify-center py-8 text-gray-400">
              <p className="text-sm">Waiting for data points...</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={180}>
              <AreaChart data={chartData}>
                <defs>
                  <linearGradient id="gradRps" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                <XAxis dataKey="time" tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                <YAxis tick={{ fill: '#9ca3af', fontSize: 10 }} tickLine={false} axisLine={false} />
                <Tooltip content={<CustomTooltip />} />
                <Area type="monotone" dataKey="rps" name="RPS" stroke="#f59e0b" fill="url(#gradRps)" strokeWidth={2} dot={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="AI Response Cache">
        <div className="rounded-xl p-4 bg-white border border-gray-200 shadow-sm" data-testid="ai-cache-panel">
          <div className="flex items-center justify-between mb-3">
            <div>
              <p className="text-xs font-bold text-gray-500 uppercase tracking-wider inline-flex items-center gap-2">
                AI Response Cache
                <span
                  data-testid="ai-cache-breaker-status"
                  className={`text-[10px] px-1.5 py-0.5 rounded-full font-mono normal-case tracking-normal ${
                    aiCacheStats?.managed?.breaker_open
                      ? 'bg-red-50 text-red-600 border border-red-200'
                      : 'bg-emerald-50 text-emerald-600 border border-emerald-200'
                  }`}
                >
                  Breaker: {aiCacheStats?.managed?.breaker_open ? 'OPEN' : 'CLOSED'}
                </span>
              </p>
              <p className="text-[11px] text-gray-400 mt-0.5">
                Backend: <span className="font-mono text-gray-600">{aiCacheStats?.managed?.backend || '—'}</span>
                {' · '}TTL: <span className="font-mono text-gray-600">{aiCacheStats?.managed?.ttl_seconds ?? '—'}s</span>
                {' · '}Max entry: <span className="font-mono text-gray-600">{aiCacheStats?.managed?.max_entry_bytes ?? '—'}B</span>
                {' · '}Namespace: <span className="font-mono text-gray-600">{aiCacheStats?.managed?.namespace || '—'}</span>
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={loadAiCacheStats}
                disabled={aiCacheLoading}
                className="text-xs px-3 py-1.5 rounded-lg border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-50 inline-flex items-center gap-1.5"
                data-testid="button-ai-cache-refresh"
              >
                <RotateCw size={12} className={aiCacheLoading ? 'animate-spin' : ''} /> Refresh
              </button>
              <button
                onClick={purgeAiCache}
                disabled={aiCachePurging}
                className="text-xs px-3 py-1.5 rounded-lg bg-red-50 text-red-600 hover:bg-red-100 border border-red-200 disabled:opacity-50"
                data-testid="button-ai-cache-purge"
              >
                {aiCachePurging ? 'Purging…' : 'Purge all'}
              </button>
            </div>
          </div>
          {aiCacheStats?.managed?.breaker_open && (
            <div className="mb-3 text-xs px-3 py-2 rounded-lg bg-red-50 text-red-700 border border-red-200 inline-flex items-center gap-2">
              <AlertTriangle size={12} /> Circuit breaker OPEN — cache temporarily disabled. Last error:
              <span className="font-mono">{aiCacheStats?.managed?.last_error || 'unknown'}</span>
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[
              { label: 'Hit rate', value: aiCacheStats?.managed?.hit_rate != null ? `${(aiCacheStats.managed.hit_rate * 100).toFixed(1)}%` : '—' },
              { label: 'Hits', value: aiCacheStats?.managed?.hits ?? '—' },
              { label: 'Misses', value: aiCacheStats?.managed?.misses ?? '—' },
              { label: 'Errors', value: aiCacheStats?.managed?.errors ?? '—' },
              { label: 'Bytes stored', value: aiCacheStats?.managed?.bytes_stored ?? '—' },
              { label: 'Oversize skipped', value: aiCacheStats?.managed?.entries_skipped_oversize ?? '—' },
              { label: 'Avg saved / hit (ms)', value: aiCacheStats?.managed?.avg_saved_latency_ms ?? '—' },
              { label: 'Total saved (s)', value: aiCacheStats?.managed?.estimated_total_saved_ms != null
                  ? (aiCacheStats.managed.estimated_total_saved_ms / 1000).toFixed(1)
                  : '—' },
            ].map((m) => (
              <div key={m.label} className="rounded-lg bg-gray-50 border border-gray-100 p-2">
                <div className="text-[10px] text-gray-400 uppercase tracking-wider">{m.label}</div>
                <div className="text-sm font-semibold text-gray-800 font-mono">{m.value}</div>
              </div>
            ))}
          </div>
          <div className="mt-2 text-[10px] text-gray-400">
            L1 in-memory: <span className="font-mono">{aiCacheStats?.l1?.size ?? 0}/{aiCacheStats?.l1?.maxsize ?? '—'}</span>
            {' · '}Last purge: <span className="font-mono">{aiCacheStats?.managed?.purge_count ?? 0}×</span>
          </div>
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Pinecone Index Health">
        {(() => {
          const fetchFailed = pineconeHealth?._error === true;
          const ph = pineconeHealth && !fetchFailed ? pineconeHealth : null;
          const configured = ph ? ph.configured : false;
          const status = ph?.status ?? 'unknown';
          const isReady = status === 'ready';
          const isUnconfigured = !fetchFailed && (!configured || status === 'not_configured');
          const isEmpty = configured && isReady && ph?.total_vectors === 0;
          const hasWarning = isUnconfigured || isEmpty;
          const containerCls = fetchFailed
            ? 'bg-gray-50 border-gray-200'
            : isUnconfigured
              ? 'bg-gray-50 border-gray-200'
              : isEmpty
                ? 'bg-amber-50 border-amber-200'
                : isReady
                  ? 'bg-emerald-50 border-emerald-200'
                  : 'bg-amber-50 border-amber-200';
          const headerColor = fetchFailed
            ? 'text-gray-400'
            : isUnconfigured
              ? 'text-gray-500'
              : isEmpty
                ? 'text-amber-600'
                : isReady
                  ? 'text-emerald-600'
                  : 'text-amber-600';

          return (
            <div className={`rounded-2xl p-4 border ${containerCls}`} data-testid="pinecone-health-tile">
              <div className="flex items-center gap-3 mb-3">
                <Database size={18} className={fetchFailed || isUnconfigured ? 'text-gray-400' : isReady && !isEmpty ? 'text-emerald-500' : 'text-amber-500'} />
                <div className="flex-1 min-w-0">
                  <p className={`text-sm font-semibold ${headerColor}`} data-testid="pinecone-health-status">
                    Pinecone vector index
                    {fetchFailed && ' — health check unavailable'}
                    {!fetchFailed && isUnconfigured && ' — not configured'}
                    {!fetchFailed && !isUnconfigured && ` — ${ph?.index_name ?? '—'}`}
                  </p>
                  <p className="text-[11px] text-gray-500 mt-0.5">
                    {fetchFailed
                      ? 'Could not reach the health endpoint — check backend logs'
                      : isUnconfigured
                        ? 'Set PINECONE_KEY + PINECONE_INDEX to enable'
                        : `${ph?.dimensions ?? '—'}-dim cosine · serverless`}
                  </p>
                </div>

                {!fetchFailed && hasWarning && (
                  <span className="flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 border border-amber-200">
                    <AlertTriangle size={11} />
                    {isUnconfigured ? 'Unconfigured' : 'Empty index'}
                  </span>
                )}
                {fetchFailed && (
                  <span className="flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-gray-100 text-gray-500 border border-gray-200">
                    <AlertTriangle size={11} />
                    Unavailable
                  </span>
                )}

                <button
                  onClick={loadPineconeHealth}
                  disabled={pineconeLoading}
                  className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-white/60"
                  data-testid="button-refresh-pinecone"
                  title="Refresh Pinecone health"
                >
                  <RefreshCw size={13} className={pineconeLoading ? 'animate-spin' : ''} />
                </button>
              </div>

              {ph && !isUnconfigured && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Status</p>
                    <p className={`text-sm font-bold font-mono capitalize ${isReady ? 'text-emerald-600' : 'text-amber-600'}`} data-testid="pinecone-status-value">
                      {status}
                    </p>
                  </div>

                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Vectors</p>
                    <p className="text-sm font-bold font-mono text-gray-900" data-testid="pinecone-vector-count">
                      {ph.total_vectors != null ? ph.total_vectors.toLocaleString() : '—'}
                    </p>
                  </div>

                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Query latency</p>
                    <p className="text-sm font-bold font-mono" data-testid="pinecone-latency">
                      {ph.latency_ms != null
                        ? <LatencyBadge ms={ph.latency_ms} />
                        : <span className="text-gray-400">—</span>}
                    </p>
                  </div>

                  <div className="rounded-xl p-3 border border-white/70 bg-white/60">
                    <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Index name</p>
                    <p className="text-sm font-bold font-mono text-gray-700 truncate" title={ph.index_name}>
                      {ph.index_name ?? '—'}
                    </p>
                  </div>
                </div>
              )}

              {ph?.error && (
                <div className="flex items-start gap-2 p-2 rounded-lg bg-red-50 border border-red-200 text-xs text-red-700 mb-3">
                  <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                  <span className="font-mono break-all">{ph.error}</span>
                </div>
              )}

              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-[11px] text-gray-500 font-medium">Switch active retriever:</span>
                {['pinecone_vector', 'mongodb_vector'].map((name) => (
                  <button
                    key={name}
                    onClick={() => switchPineconeRetriever(name)}
                    disabled={!!pineconeSwitch}
                    data-testid={`pinecone-switch-${name}`}
                    className={`text-[11px] px-2.5 py-1 rounded-lg border font-mono font-semibold transition-colors ${
                      pineconeSwitch === name
                        ? 'bg-violet-100 text-violet-700 border-violet-300 animate-pulse'
                        : 'bg-white text-violet-600 border-violet-200 hover:bg-violet-50'
                    }`}
                  >
                    {pineconeSwitch === name ? 'Switching…' : name}
                  </button>
                ))}
              </div>
            </div>
          );
        })()}
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Dependency Status">
        <div className="space-y-3">
          {(() => {
            const KNOWN_SERVICES = [
              { key: 'mongodb',  icon: Database, label: 'Syrabit DB (MongoDB)', desc: 'User data, sessions, content, rate limits' },
              { key: 'redis',    icon: Wifi,     label: 'Redis Cache (Upstash)', desc: 'Shared content cache & session store' },
              { key: 'llm',      icon: Zap,      label: 'AI Provider Pool',      desc: 'Multi-provider SLM pool — Groq, Cerebras, Sarvam, OpenRouter, Fireworks' },
              { key: 'supabase', icon: Database, label: 'Supabase',              desc: 'Auth, user profiles, persistent storage' },
            ];
            const knownKeys = new Set(KNOWN_SERVICES.map(s => s.key));
            const extraKeys = Object.keys(deps).filter(k => !knownKeys.has(k));
            const allServices = [
              ...KNOWN_SERVICES,
              ...extraKeys.map(k => ({ key: k, icon: Activity, label: k.charAt(0).toUpperCase() + k.slice(1), desc: '' })),
            ];
            return allServices.map(({ key, icon: Icon, label, desc }) => {
              const dep = deps[key] || {};
              const isOk = dep.status === 'ok';
              const isNotConfigured = dep.status === 'not_configured';
              const isError = dep.status === 'error';
              return (
                <div key={key} className={`rounded-xl p-4 flex items-center gap-3 bg-white border border-gray-200 shadow-sm`} data-testid={`dep-${key}`} data-syra={`${key}-latency`}>
                  <div className={`w-10 h-10 rounded-xl flex items-center justify-center ${
                    isOk ? 'bg-emerald-50' : isNotConfigured ? 'bg-gray-100' : isError ? 'bg-red-50' : 'bg-amber-50'
                  }`}>
                    <Icon size={18} className={isOk ? 'text-emerald-500' : isNotConfigured ? 'text-gray-400' : isError ? 'text-red-500' : 'text-amber-500'} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-gray-900">{label}</p>
                    {desc && <p className="text-xs text-gray-400">{desc}</p>}
                    {dep.error && <p className="text-xs text-red-500 mt-0.5">{dep.error}</p>}
                  </div>
                  <div className="flex items-center gap-2">
                    <LatencyBadge ms={dep.latencyMs} />
                    <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${
                      isOk ? 'bg-emerald-50 text-emerald-600' :
                      isNotConfigured ? 'bg-gray-100 text-gray-500' :
                      isError ? 'bg-red-50 text-red-600' :
                      'bg-amber-50 text-amber-600 animate-pulse'
                    }`}>
                      {loading ? 'PROBING...' : dep.status?.toUpperCase().replace('_', ' ') || 'UNKNOWN'}
                    </span>
                  </div>
                </div>
              );
            });
          })()}
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Health Endpoint URL">
        <div className="rounded-xl p-4 bg-white border border-gray-200 shadow-sm">
          <p className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Health Endpoint URL</p>
          <div className="flex items-center gap-2">
            <code className="flex-1 text-xs font-mono text-gray-600 bg-gray-50 px-3 py-2 rounded-lg truncate border border-gray-200">{healthUrl}</code>
            <button onClick={handleCopy} className="p-2 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 flex-shrink-0" data-testid="button-copy-url">
              {copied ? <Check size={14} className="text-emerald-500" /> : <Copy size={14} />}
            </button>
          </div>
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="UptimeRobot Setup">
        <div className="rounded-xl p-4 bg-white border border-gray-200 shadow-sm">
          <p className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-3">UptimeRobot Setup</p>
          <ol className="space-y-2">
            {['Create free UptimeRobot account at uptimerobot.com','Add new HTTP(s) monitor','Paste the health URL above','Enable keyword monitoring: \'"status":"ok"\'','Configure alert contacts (email/Slack)','Save — you\'ll get 5-minute uptime checks'].map((s, i) => (
              <li key={i} className="flex items-start gap-2 text-xs text-gray-500">
                <span className="w-5 h-5 rounded-full bg-violet-50 flex items-center justify-center text-[10px] font-bold text-violet-600 flex-shrink-0 mt-0.5">{i+1}</span>{s}
              </li>
            ))}
          </ol>
        </div>
        </SectionErrorBoundary>

        <SectionErrorBoundary name="Edge Metrics">
          <EdgeMetricsPanel token={adminToken} />
        </SectionErrorBoundary>
    </>
  );
}
