import { Fragment, useEffect, useState } from 'react';
import { TrendingUp, Eye, Users, DollarSign, Zap, Target,
  AlertTriangle, Calendar, ShieldCheck, RefreshCw,
  AlertOctagon, Star, MousePointerClick, XCircle,
  ChevronDown, ChevronRight } from 'lucide-react';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar, ComposedChart, Line,
} from 'recharts';
import { Card, Stat, TT, fmt, fmtInr } from './shared';
import { adminGetHydrateStats, adminAcknowledgeAlert,
  adminGetReviewPromptStats,
  adminGetReviewPromptBaselineNoise,
  adminGetReviewPromptByReasonTrend } from '@/utils/api';

const TIME_RANGES = [
  { value: 1,  label: 'Today' },
  { value: 7,  label: 'Last 7 days' },
  { value: 30, label: 'Last 30 days' },
  { value: 90, label: 'Last 90 days' },
];

export default function OverviewTab({ data, vs, widgetErrors, load, mrr, predicted, growth, arpu, ltv,
  cfConnected, overviewDays, setOverviewDays, adminToken }) {
  const [hydrate, setHydrate] = useState(null);
  const [hydrateLoading, setHydrateLoading] = useState(false);
  const [hydrateError, setHydrateError] = useState(false);
  const loadHydrate = async () => {
    setHydrateLoading(true);
    setHydrateError(false);
    try {
      const r = await adminGetHydrateStats(adminToken, 7);
      setHydrate(r.data);
    } catch {
      setHydrateError(true);
      setHydrate(null);
    } finally {
      setHydrateLoading(false);
    }
  };
  const [reviewPrompt, setReviewPrompt] = useState(null);
  const [reviewPromptLoading, setReviewPromptLoading] = useState(false);
  const [reviewPromptError, setReviewPromptError] = useState(false);
  // Task #681 — per-reason baseline noise snapshot. Fetched in parallel
  // with the funnel rollup so the tile can render the noise band next
  // to each row without an extra round-trip on toggle. A failed fetch
  // is non-fatal: the funnel still renders, the noise columns just
  // show "—".
  const [reviewBaseline, setReviewBaseline] = useState(null);
  const loadReviewPrompt = async () => {
    setReviewPromptLoading(true);
    setReviewPromptError(false);
    try {
      // Task #659: 7-day window so per-reason deltas are true
      // week-over-week (vs the prior 7 days), matching the weekly
      // digest email's semantics.
      const [statsRes, baselineRes] = await Promise.allSettled([
        adminGetReviewPromptStats(adminToken, 7),
        adminGetReviewPromptBaselineNoise(adminToken, 7),
      ]);
      if (statsRes.status === 'fulfilled') {
        setReviewPrompt(statsRes.value.data);
      } else {
        throw statsRes.reason;
      }
      setReviewBaseline(
        baselineRes.status === 'fulfilled' ? baselineRes.value.data : null,
      );
    } catch {
      setReviewPromptError(true);
      setReviewPrompt(null);
      setReviewBaseline(null);
    } finally {
      setReviewPromptLoading(false);
    }
  };
  useEffect(() => {
    if (adminToken) {
      loadHydrate();
      loadReviewPrompt();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adminToken]);
  const hasDailySignup = data?.daily_signups?.some(d => d.count > 0);
  const hasPlanUsage   = data?.plan_usage && Object.keys(data.plan_usage).length > 0;
  const cf = vs.cloudflare || {};
  const dailyVisitors = cf.daily_visitors || [];
  const hasDailyCf = dailyVisitors.some(d => d.visitors > 0 || d.page_views > 0);
  const rangeLabel = TIME_RANGES.find(t => t.value === overviewDays)?.label || `Last ${overviewDays} days`;
  const cfEmptyMsg = cfConnected
    ? 'No data yet for this range'
    : 'Cloudflare analytics unavailable — check API token and Zone ID';

  return (
    <>
      <div className="flex items-center gap-3 flex-wrap">
        <div className="flex items-center gap-1.5 text-gray-600 text-sm">
          <Calendar size={14} />
          <span>Time range:</span>
        </div>
        {TIME_RANGES.map(t => (
          <button key={t.value} onClick={() => setOverviewDays(t.value)}
            className={`px-3.5 py-1.5 rounded-xl text-xs font-medium transition-all ${
              overviewDays === t.value ? 'text-white' : 'text-gray-600 hover:text-gray-800'
            }`}
            style={overviewDays === t.value
              ? { background: 'linear-gradient(135deg, #7c3aed, #6d28d9)', boxShadow: '0 2px 12px rgba(124,58,237,0.3)' }
              : { background: '#f9fafb', border: '1px solid #e5e7eb' }
            }>
            {t.label}
          </button>
        ))}
      </div>

      {widgetErrors.overview && (
        <div className="flex items-center gap-3 p-3.5 rounded-xl" style={{
          background: 'rgba(245,158,11,0.06)',
          border: '1px solid rgba(245,158,11,0.15)',
        }}>
          <AlertTriangle size={14} className="text-amber-700 flex-shrink-0" />
          <p className="text-xs text-amber-700/80 flex-1">Overview data failed to load — some metrics unavailable.</p>
          <button onClick={() => load(true)} className="text-xs text-amber-700 hover:text-gray-900 px-2.5 py-1 rounded-lg transition-colors"
            style={{ background: 'rgba(245,158,11,0.12)' }}>Retry</button>
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <Stat icon={TrendingUp} label={`Visitors (${rangeLabel})`}
          value={cfConnected ? (cf.total_visitors ?? 0).toLocaleString() : '—'} color="#f6821f" sub="Cloudflare" />
        <Stat icon={Users} label="Visitors Today"
          value={cfConnected ? (cf.visitors_today ?? 0).toLocaleString() : '—'} color="#06b6d4" sub="Cloudflare" />
        <Stat icon={Eye} label="Page Views Today"
          value={cfConnected ? (cf.page_views_today ?? 0).toLocaleString() : '—'} color="#ec4899" sub="Cloudflare" />
        <Stat icon={Users} label="Active Users" value={data?.active_users ?? 0} color="#8b5cf6" />
      </div>

      {(data?.pageviews != null || data?.sessions != null) && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {data?.pageviews != null && (
            <Stat icon={Eye} label="Pageviews" value={(data.pageviews ?? 0).toLocaleString()} color="#6366f1" sub="Analytics" />
          )}
          {data?.sessions != null && (
            <Stat icon={Users} label="Sessions" value={(data.sessions ?? 0).toLocaleString()} color="#8b5cf6" sub="Analytics" />
          )}
          {data?.bounce_rate != null && (
            <Stat icon={TrendingUp} label="Bounce Rate" value={`${Math.round((data.bounce_rate ?? 0) * 100)}%`} color="#f59e0b" sub="Analytics" />
          )}
        </div>
      )}

      {mrr > 0 && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <Stat icon={DollarSign} label="MRR (30d)"       value={fmtInr(mrr)}       color="#10b981" trend={growth} />
          <Stat icon={TrendingUp} label="Predicted MRR"   value={fmtInr(predicted)} color="#7c3aed" />
          <Stat icon={Target}     label="ARPU"            value={fmtInr(arpu)}       color="#f59e0b" />
          <Stat icon={Zap}        label="LTV (12-mo)"     value={fmtInr(ltv)}        color="#06b6d4" />
        </div>
      )}

      <HydrateHealthCard
        hydrate={hydrate}
        loading={hydrateLoading}
        error={hydrateError}
        onRetry={loadHydrate}
        adminToken={adminToken}
      />

      <ReviewPromptFunnelCard
        stats={reviewPrompt}
        baseline={reviewBaseline}
        loading={reviewPromptLoading}
        error={reviewPromptError}
        onRetry={loadReviewPrompt}
        adminToken={adminToken}
      />

      <Card title={`Daily Visitors — ${rangeLabel}`} empty={!hasDailyCf} emptyMsg={cfEmptyMsg}>
        <ResponsiveContainer width="100%" height={260}>
          <AreaChart data={dailyVisitors} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f9fafb" />
            <XAxis dataKey="date" tick={{ fill: '#4b5563', fontSize: 11 }} tickFormatter={fmt}
              interval={Math.max(0, Math.floor(dailyVisitors.length / 8) - 1)} />
            <YAxis tick={{ fill: '#4b5563', fontSize: 11 }} />
            <Tooltip {...TT} />
            <Area type="monotone" dataKey="visitors" name="Cloudflare Visitors"
              stroke="#f6821f" fill="rgba(246,130,31,0.12)" strokeWidth={2} />
          </AreaChart>
        </ResponsiveContainer>
      </Card>

      <Card title={`Daily Page Views — ${rangeLabel}`} empty={!hasDailyCf} emptyMsg={cfEmptyMsg}>
        <ResponsiveContainer width="100%" height={220}>
          <AreaChart data={dailyVisitors} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f9fafb" />
            <XAxis dataKey="date" tick={{ fill: '#4b5563', fontSize: 11 }} tickFormatter={fmt}
              interval={Math.max(0, Math.floor(dailyVisitors.length / 8) - 1)} />
            <YAxis tick={{ fill: '#4b5563', fontSize: 11 }} />
            <Tooltip {...TT} />
            <Area type="monotone" dataKey="page_views" name="Cloudflare Page Views"
              stroke="#ec4899" fill="rgba(236,72,153,0.10)" strokeWidth={2} />
          </AreaChart>
        </ResponsiveContainer>
      </Card>

      <Card title={`Daily Signups — ${rangeLabel}`} empty={!hasDailySignup} emptyMsg="No signups in this range">
        <ResponsiveContainer width="100%" height={180}>
          <BarChart data={data.daily_signups} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f9fafb" />
            <XAxis dataKey="date" tick={{ fill: '#4b5563', fontSize: 11 }} tickFormatter={fmt}
              interval={Math.max(0, Math.floor((data.daily_signups?.length || 0) / 8) - 1)} />
            <YAxis tick={{ fill: '#4b5563', fontSize: 11 }} allowDecimals={false} />
            <Tooltip {...TT} />
            <Bar dataKey="count" name="Signups" fill="#7c3aed" radius={[4, 4, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </Card>

      {hasPlanUsage && (
        <Card title="Credits Used by Plan">
          <ResponsiveContainer width="100%" height={140}>
            <BarChart data={Object.entries(data.plan_usage).map(([plan, used]) => ({ plan, used }))}
              margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f9fafb" />
              <XAxis dataKey="plan" tick={{ fill: '#4b5563', fontSize: 11 }} />
              <YAxis tick={{ fill: '#4b5563', fontSize: 11 }} />
              <Tooltip {...TT} />
              <Bar dataKey="used" name="Credits Used" fill="#7c3aed" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      )}
    </>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Task #408: ops-health tile for the hydrate-lifecycle telemetry that
// Tasks #405 / #407 emit. Healthy state is intentionally low-key so the
// admin's eye doesn't get drawn to it when nothing's wrong.
// ─────────────────────────────────────────────────────────────────────
function HydrateHealthCard({ hydrate, loading, error, onRetry, adminToken }) {
  const [ackingId, setAckingId] = useState(null);
  const [ackedIds, setAckedIds] = useState(() => new Set());
  const [ackErrors, setAckErrors] = useState({});
  const handleAcknowledge = async (alertId) => {
    if (!alertId || !adminToken) return;
    setAckingId(alertId);
    setAckErrors(prev => {
      if (!prev[alertId]) return prev;
      const { [alertId]: _, ...rest } = prev;
      return rest;
    });
    try {
      await adminAcknowledgeAlert(adminToken, alertId);
      setAckedIds(prev => {
        const next = new Set(prev);
        next.add(alertId);
        return next;
      });
      onRetry?.();
    } catch (e) {
      setAckErrors(prev => ({
        ...prev,
        [alertId]: 'Acknowledge failed — try again or use the Alerts page',
      }));
    } finally {
      setAckingId(null);
    }
  };
  if (loading && !hydrate) {
    return (
      <Card title="Hydration & Stale-Build Recovery (7d)">
        <p className="text-gray-600 text-sm text-center py-6">Loading…</p>
      </Card>
    );
  }
  if (error) {
    return (
      <Card title="Hydration & Stale-Build Recovery (7d)" error onRetry={onRetry} />
    );
  }
  const h = hydrate || {};
  const failed = h.preload_failed_total || 0;
  const attempts = h.auto_reload_attempts || 0;
  const recoveries = h.auto_reload_recoveries || 0;
  const stalled = h.stalled_total || 0;
  const manual = h.manual_failures || 0;
  const successRate = h.auto_reload_success_rate_pct;
  const isHealthy = failed === 0 && stalled === 0;
  const topKinds = Array.isArray(h.top_kinds) ? h.top_kinds : [];
  const topUAs = Array.isArray(h.top_user_agents) ? h.top_user_agents : [];
  const activeAlerts = (Array.isArray(h.active_alerts) ? h.active_alerts : [])
    .filter(a => a && a._id && !ackedIds.has(a._id));

  return (
    <Card
      title="Hydration & Stale-Build Recovery (7d)"
      action={
        <button
          onClick={onRetry}
          className="text-xs text-gray-600 hover:text-gray-700 px-2 py-0.5 rounded-lg flex items-center gap-1"
        >
          <RefreshCw size={10} /> Refresh
        </button>
      }
    >
      {activeAlerts.length > 0 && (
        <div className="space-y-2 mb-4">
          {activeAlerts.map(alert => (
            <HydrateAlertBadge
              key={alert._id}
              alert={alert}
              acking={ackingId === alert._id}
              ackError={ackErrors[alert._id]}
              onAcknowledge={() => handleAcknowledge(alert._id)}
            />
          ))}
        </div>
      )}
      {isHealthy ? (
        <div className="flex items-center gap-3 py-4">
          <div className="w-9 h-9 rounded-lg flex items-center justify-center"
            style={{ background: 'rgba(16,185,129,0.10)' }}>
            <ShieldCheck size={16} className="text-emerald-500" />
          </div>
          <div>
            <p className="text-emerald-600 text-sm font-medium">No stale-build recoveries — healthy</p>
            <p className="text-gray-600 text-xs mt-0.5">No hydration failures or stalls in the last 7 days.</p>
          </div>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Stat icon={AlertOctagon} label="Preload failures"
              value={failed.toLocaleString()} color="#f59e0b"
              sub={manual > 0 ? `${manual} without auto-reload attempt` : 'all auto-reload eligible'} />
            <Stat icon={RefreshCw} label="Auto-reload attempts"
              value={attempts.toLocaleString()} color="#7c3aed" sub="stale-chunk reloads" />
            <Stat icon={ShieldCheck} label="Recovery success"
              value={successRate == null ? '—' : `${successRate}%`}
              color="#10b981"
              sub={`${recoveries.toLocaleString()} healthy hydrations after reload`} />
            <Stat icon={AlertTriangle} label="Stalled hydrations"
              value={stalled.toLocaleString()} color="#ec4899"
              sub="≥5s on Suspense fallback" />
          </div>

          {(topKinds.length > 0 || topUAs.length > 0) && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-4">
              {topKinds.length > 0 && (
                <div className="rounded-xl p-3.5 bg-gray-50 border border-gray-200">
                  <p className="text-gray-500 text-xs font-medium mb-2">Top failing page chunks</p>
                  <ul className="space-y-1">
                    {topKinds.map((row, i) => (
                      <li key={i} className="flex items-center justify-between text-xs">
                        <span className="text-gray-700 truncate mr-2">{row.value || '—'}</span>
                        <span className="text-gray-600 font-mono">{row.count}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {topUAs.length > 0 && (
                <div className="rounded-xl p-3.5 bg-gray-50 border border-gray-200">
                  <p className="text-gray-500 text-xs font-medium mb-2">Top user-agents</p>
                  <ul className="space-y-1">
                    {topUAs.map((row, i) => (
                      <li key={i} className="flex items-center justify-between text-xs">
                        <span className="text-gray-700 truncate mr-2" title={row.value}>
                          {(row.value || '—').slice(0, 60)}
                        </span>
                        <span className="text-gray-600 font-mono">{row.count}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </Card>
  );
}

// Task #415: inline badge for fired hydrate-scoped alerts. Shows the
// alert title, when it fired (so admins know an email already went out
// at e.g. 09:14 UTC), and a button that hits the same acknowledge
// endpoint as the main /admin/alerts page.
function HydrateAlertBadge({ alert, acking, ackError, onAcknowledge }) {
  const firedAt = alert.fired_at ? new Date(alert.fired_at) : null;
  const firedLabel = firedAt && !isNaN(firedAt.getTime())
    ? firedAt.toLocaleString(undefined, {
        month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
        timeZoneName: 'short',
      })
    : '—';
  const isRecovery = alert.type === 'hydrate_recovery_low';
  const accent = isRecovery ? '#ec4899' : '#f59e0b';
  return (
    <div
      className="flex items-start gap-3 p-3.5 rounded-xl"
      style={{
        background: isRecovery ? 'rgba(236,72,153,0.06)' : 'rgba(245,158,11,0.06)',
        border: `1px solid ${isRecovery ? 'rgba(236,72,153,0.20)' : 'rgba(245,158,11,0.20)'}`,
      }}
    >
      <AlertOctagon size={16} className="flex-shrink-0 mt-0.5" style={{ color: accent }} />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold" style={{ color: accent }}>
          {alert.title || (isRecovery ? 'Stale-build recovery rate low' : 'Stale-build failures spiking')}
        </p>
        <p className="text-xs text-gray-500 mt-0.5">
          Alert fired {firedLabel} — ops already notified by email
        </p>
        {alert.body && (
          <p className="text-xs text-gray-600 mt-1 line-clamp-2" title={alert.body}>
            {alert.body.length > 160 ? `${alert.body.slice(0, 160)}…` : alert.body}
          </p>
        )}
        {ackError && (
          <p className="text-xs text-red-600 mt-1.5" role="alert">{ackError}</p>
        )}
      </div>
      <button
        onClick={onAcknowledge}
        disabled={acking}
        className="text-xs px-2.5 py-1 rounded-lg flex-shrink-0 transition-colors disabled:opacity-50"
        style={{
          background: 'rgba(255,255,255,0.6)',
          border: `1px solid ${accent}`,
          color: accent,
        }}
      >
        {acking ? 'Acknowledging…' : 'Acknowledge'}
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────
// Task #654: Trustpilot review-prompt funnel tile (relabeled #726;
// formerly Google reviews — migrated in #724). Shows shown / clicked /
// dismissed totals, click-through rate, and a per-trigger-reason
// breakdown so the team can see which surfaces (quiz_high_score,
// chapter_engaged, etc.) actually convert to Trustpilot review clicks.
// ─────────────────────────────────────────────────────────────────────
// Task #659 — single per-reason row, including week-over-week delta
// columns. Reasons that newly appeared / disappeared in the current
// window get a "new" / "gone" pill so ops can spot which surface is
// responsible for an overall CTR swing instead of guessing.
// Task #686 — persist the admin's last comparison reason in
// localStorage so the picker doesn't reset to "— none —" every time
// the row is collapsed/re-opened or the page is reloaded. Stored as
// a single shared value (per browser, not per primary reason) since
// admins typically triage with the same comparison baseline across
// reasons. The "Clear comparison" button wipes both the in-memory
// state and the stored value.
const REVIEW_PROMPT_COMPARE_STORAGE_KEY =
  'syrabit:adminReviewPromptCompareReason';

function readStoredCompareReason() {
  if (typeof window === 'undefined') return '';
  try {
    return window.localStorage.getItem(REVIEW_PROMPT_COMPARE_STORAGE_KEY) || '';
  } catch {
    return '';
  }
}

function writeStoredCompareReason(value) {
  if (typeof window === 'undefined') return;
  try {
    if (value) {
      window.localStorage.setItem(REVIEW_PROMPT_COMPARE_STORAGE_KEY, value);
    } else {
      window.localStorage.removeItem(REVIEW_PROMPT_COMPARE_STORAGE_KEY);
    }
  } catch {
    // localStorage may be unavailable (private mode / quota); the
    // picker still works in-memory for the current session.
  }
}

function ReviewPromptReasonTrend({ adminToken, reason }) {
  // Task #673 — admins can overlay a second trigger reason's CTR /
  // shown line onto the same chart to spot whether a regression is
  // unique to one reason or shared across surfaces.
  // Task #686 — initialise from localStorage; ignore the stored value
  // if it equals the primary reason (would be a no-op overlay).
  const [compare, setCompare] = useState(() => {
    const stored = readStoredCompareReason();
    return stored && stored !== reason ? stored : '';
  });
  const [trend, setTrend] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  // If the parent row's primary reason changes to whatever was stored
  // as the comparison, drop the overlay (can't compare a reason to
  // itself). Otherwise keep whatever the admin picked.
  useEffect(() => {
    if (compare && compare === reason) {
      setCompare('');
    }
  }, [reason, compare]);

  const handleCompareChange = (value) => {
    setCompare(value);
    writeStoredCompareReason(value);
  };

  const clearCompare = () => {
    setCompare('');
    writeStoredCompareReason('');
  };

  useEffect(() => {
    let cancelled = false;
    const run = async () => {
      setLoading(true);
      setError(false);
      try {
        const r = await adminGetReviewPromptByReasonTrend(
          adminToken, reason, 8, compare || null,
        );
        if (!cancelled) setTrend(r.data);
      } catch {
        if (!cancelled) {
          setError(true);
          setTrend(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    run();
    return () => { cancelled = true; };
  }, [adminToken, reason, compare]);

  // Task #686 — sanitise a stale stored value: if the persisted
  // comparison reason no longer appears in this row's pickable list
  // (e.g., it stopped firing in the last 8 weeks, or the backend
  // pruned it), drop it so the controlled <select> always has a
  // matching <option> and we never fire a useless compare API call.
  // This effect is hoisted above the conditional returns below to
  // keep the hook order stable across renders.
  useEffect(() => {
    if (!trend || !compare) return;
    const reasons = Array.isArray(trend.available_reasons)
      ? trend.available_reasons
      : [];
    const isPickable = reasons.includes(compare) && compare !== reason;
    if (!isPickable) {
      setCompare('');
      writeStoredCompareReason('');
    }
  }, [trend, compare, reason]);

  if (loading && !trend) {
    return <p className="text-gray-500 text-xs py-3 px-3.5">Loading 8-week trend…</p>;
  }
  if (error) {
    return <p className="text-rose-600 text-xs py-3 px-3.5">Failed to load trend.</p>;
  }
  const buckets = Array.isArray(trend?.buckets) ? trend.buckets : [];
  if (buckets.length === 0) {
    return <p className="text-gray-500 text-xs py-3 px-3.5">No data for this reason in the last 8 weeks.</p>;
  }
  const compareBuckets = Array.isArray(trend?.compare_buckets) ? trend.compare_buckets : [];
  const compareReason = trend?.compare_reason || '';
  const availableReasons = Array.isArray(trend?.available_reasons)
    ? trend.available_reasons
    : [];
  // Picker only lists reasons that fired ≥1 event in the window AND
  // aren't the primary row that was just expanded — that one's already
  // the baseline series.
  const pickable = availableReasons.filter(r => r && r !== reason);

  const chartData = buckets.map((b, i) => {
    const d = b?.week_end ? new Date(b.week_end) : null;
    const label = d
      ? `${d.getUTCMonth() + 1}/${d.getUTCDate()}`
      : '—';
    const cb = compareBuckets[i] || {};
    return {
      label,
      shown: b.shown || 0,
      clicked: b.clicked || 0,
      ctr_pct: b.ctr_pct == null ? null : Number(b.ctr_pct),
      compare_shown: cb.shown || 0,
      compare_clicked: cb.clicked || 0,
      compare_ctr_pct: cb.ctr_pct == null ? null : Number(cb.ctr_pct),
    };
  });
  const totalShown = chartData.reduce((a, b) => a + b.shown, 0);
  const totalClicked = chartData.reduce((a, b) => a + b.clicked, 0);
  const overallCtr = totalShown > 0
    ? Math.round((totalClicked / totalShown) * 1000) / 10
    : null;
  const totalCompareShown = chartData.reduce((a, b) => a + b.compare_shown, 0);
  const totalCompareClicked = chartData.reduce((a, b) => a + b.compare_clicked, 0);
  const overallCompareCtr = totalCompareShown > 0
    ? Math.round((totalCompareClicked / totalCompareShown) * 1000) / 10
    : null;

  return (
    <div className="px-3.5 py-3 bg-white border-t border-gray-200">
      <div className="flex items-center justify-between mb-2 gap-3 flex-wrap">
        <p className="text-gray-600 text-xs font-medium">
          8-week trend · <span className="font-mono text-gray-700">{reason}</span>
          {compareReason && (
            <>
              {' '}vs{' '}
              <span className="font-mono text-amber-600">{compareReason}</span>
            </>
          )}
        </p>
        <div className="flex items-center gap-2 text-[11px] text-gray-500">
          <label className="flex items-center gap-1.5">
            <span className="text-gray-500">Compare to</span>
            <select
              value={compare}
              onChange={(e) => handleCompareChange(e.target.value)}
              className="bg-white border border-gray-300 rounded px-1.5 py-0.5 text-[11px] text-gray-700 focus:outline-none focus:border-violet-400 max-w-[180px]"
              disabled={pickable.length === 0}
              title={pickable.length === 0
                ? 'No other reasons have data in the last 8 weeks'
                : 'Overlay another reason (remembered across panel toggles and reloads)'}
            >
              <option value="">— none —</option>
              {pickable.map(r => (
                <option key={r} value={r}>{r}</option>
              ))}
            </select>
          </label>
          {/* Task #686 — explicit reset back to the "— none —" baseline.
              Only shown when an overlay is active so it doesn't clutter
              the picker UI in the common single-reason case. */}
          {compare && (
            <button
              type="button"
              onClick={clearCompare}
              className="text-[11px] text-gray-500 hover:text-rose-600 underline underline-offset-2 decoration-dotted"
              title="Reset the comparison picker to — none — and forget the saved choice"
            >
              Clear comparison
            </button>
          )}
        </div>
      </div>
      <p className="text-gray-500 text-[11px] mb-2">
        <span className="text-cyan-600">●</span>{' '}
        {reason}: Σ {totalShown.toLocaleString()} shown · {totalClicked.toLocaleString()} clicked ·
        {' '}CTR {overallCtr == null ? '—' : `${overallCtr}%`}
        {compareReason && (
          <>
            {'  ·  '}
            <span className="text-amber-500">●</span>{' '}
            {compareReason}: Σ {totalCompareShown.toLocaleString()} shown ·
            {' '}{totalCompareClicked.toLocaleString()} clicked ·
            {' '}CTR {overallCompareCtr == null ? '—' : `${overallCompareCtr}%`}
          </>
        )}
      </p>
      <ResponsiveContainer width="100%" height={170}>
        <ComposedChart data={chartData} margin={{ top: 5, right: 10, bottom: 0, left: -10 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
          <XAxis dataKey="label" tick={{ fill: '#6b7280', fontSize: 10 }} />
          <YAxis yAxisId="left" tick={{ fill: '#6b7280', fontSize: 10 }} />
          <YAxis yAxisId="right" orientation="right"
            tick={{ fill: '#6b7280', fontSize: 10 }}
            tickFormatter={(v) => `${v}%`} />
          <Tooltip {...TT} />
          <Bar yAxisId="left" dataKey="shown" name={`${reason} shown`}
            fill="rgba(124,58,237,0.25)" />
          <Bar yAxisId="left" dataKey="clicked" name={`${reason} clicked`}
            fill="#10b981" />
          <Line yAxisId="right" type="monotone" dataKey="ctr_pct"
            name={`${reason} CTR %`}
            stroke="#06b6d4" strokeWidth={2} dot={{ r: 2 }}
            connectNulls={false} />
          {compareReason && (
            <>
              <Bar yAxisId="left" dataKey="compare_shown"
                name={`${compareReason} shown`}
                fill="rgba(245,158,11,0.25)" />
              <Bar yAxisId="left" dataKey="compare_clicked"
                name={`${compareReason} clicked`}
                fill="#f59e0b" />
              <Line yAxisId="right" type="monotone" dataKey="compare_ctr_pct"
                name={`${compareReason} CTR %`}
                stroke="#f97316" strokeWidth={2}
                strokeDasharray="4 3" dot={{ r: 2 }}
                connectNulls={false} />
            </>
          )}
        </ComposedChart>
      </ResponsiveContainer>
      {compareReason && (
        <ReviewPromptReasonDeltaStrip
          chartData={chartData}
          primaryReason={reason}
          compareReason={compareReason}
          totalShown={totalShown}
          totalCompareShown={totalCompareShown}
          totalClicked={totalClicked}
          totalCompareClicked={totalCompareClicked}
        />
      )}
    </div>
  );
}

// Task #687 — compact per-week + aggregate delta strip rendered below
// the overlay chart. Each cell shows `primary − compare`:
//   • Δ shown — emitted whenever either reason fired in the week, so
//     a one-sided week (e.g., compare went silent) still surfaces the
//     full count gap instead of being hidden behind "—".
//   • Δ CTR  — only emitted when BOTH reasons have a valid CTR for
//     the week; otherwise we render "—" so admins don't misread a
//     null-vs-N% comparison as "tied at 0 pp".
// Aggregate Δ CTR is computed from raw click/shown totals rather
// than the parent's already-rounded `overallCtr`/`overallCompareCtr`,
// so a sub-decimal gap (e.g., 10.04 vs 9.96) is preserved instead of
// collapsing to 0.0 pp.
// Colours match the existing per-row delta styling in this file:
// rose for negative (primary trailing compare), emerald for positive
// (primary ahead), gray neutral marker (▬) for zero CTR, plain gray
// for zero count and for the "no data" placeholder.
function ReviewPromptReasonDeltaStrip({
  chartData,
  primaryReason,
  compareReason,
  totalShown,
  totalCompareShown,
  totalClicked,
  totalCompareClicked,
}) {
  const fmtShown = (d) => {
    if (d == null) return <span className="text-gray-400">—</span>;
    if (d > 0) return <span className="text-emerald-600">+{d.toLocaleString()}</span>;
    if (d < 0) return <span className="text-rose-600">{d.toLocaleString()}</span>;
    return <span className="text-gray-500">0</span>;
  };
  const fmtCtr = (d) => {
    if (d == null) return <span className="text-gray-400">—</span>;
    const rounded = Math.round(d * 10) / 10;
    if (rounded > 0) return <span className="text-emerald-600">▲ +{rounded.toFixed(1)} pp</span>;
    if (rounded < 0) return <span className="text-rose-600">▼ {rounded.toFixed(1)} pp</span>;
    return <span className="text-gray-500">▬ 0.0 pp</span>;
  };

  const rows = chartData.map((b) => {
    const hasPrimary = (b.shown || 0) > 0;
    const hasCompare = (b.compare_shown || 0) > 0;
    const shownDelta = (hasPrimary || hasCompare)
      ? (b.shown || 0) - (b.compare_shown || 0)
      : null;
    const ctrDelta = (b.ctr_pct != null && b.compare_ctr_pct != null)
      ? b.ctr_pct - b.compare_ctr_pct
      : null;
    return { label: b.label, shownDelta, ctrDelta };
  });

  const aggShownDelta = totalShown - totalCompareShown;
  // Compute aggregate CTR delta from raw totals to avoid the
  // double-rounding artefact that would otherwise mask a real
  // sub-percentage-point gap.
  const aggCtrDelta = (totalShown > 0 && totalCompareShown > 0)
    ? ((totalClicked / totalShown) - (totalCompareClicked / totalCompareShown)) * 100
    : null;

  return (
    <div className="mt-3 border-t border-gray-100 pt-2">
      <p
        className="text-[10px] uppercase tracking-wide text-gray-500 mb-1"
        title={`Each cell is "${primaryReason} − ${compareReason}". Positive = ${primaryReason} is ahead.`}
      >
        Δ {primaryReason} − {compareReason}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-[11px] tabular-nums">
          <thead>
            <tr className="text-gray-500">
              <th className="text-left font-normal pr-2 pb-1">Week</th>
              {rows.map((r, i) => (
                <th key={`h-${i}`} className="text-right font-normal px-1 pb-1">
                  {r.label}
                </th>
              ))}
              <th className="text-right font-semibold text-gray-700 pl-2 pb-1 border-l border-gray-200">
                Total
              </th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="text-left text-gray-600 pr-2 py-0.5">Δ shown</td>
              {rows.map((r, i) => (
                <td key={`s-${i}`} className="text-right px-1 py-0.5">
                  {fmtShown(r.shownDelta)}
                </td>
              ))}
              <td className="text-right pl-2 py-0.5 font-semibold border-l border-gray-200">
                {fmtShown(aggShownDelta)}
              </td>
            </tr>
            <tr>
              <td className="text-left text-gray-600 pr-2 py-0.5">Δ CTR</td>
              {rows.map((r, i) => (
                <td key={`c-${i}`} className="text-right px-1 py-0.5">
                  {fmtCtr(r.ctrDelta)}
                </td>
              ))}
              <td className="text-right pl-2 py-0.5 font-semibold border-l border-gray-200">
                {fmtCtr(aggCtrDelta)}
              </td>
            </tr>
          </tbody>
          {/* Explicit aggregate row spelled out across the full width
              so the 8-week summary reads as a single sentence in
              addition to being available column-wise via "Total". */}
          <tfoot>
            <tr className="border-t border-gray-200">
              <td colSpan={rows.length + 2} className="text-left pt-1.5 text-[11px] text-gray-700">
                <span className="font-semibold text-gray-600 mr-2">Aggregate (8 wks)</span>
                <span className="mr-3">Δ shown {fmtShown(aggShownDelta)}</span>
                <span>Δ CTR {fmtCtr(aggCtrDelta)}</span>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  );
}

// Task #681 — render the per-reason noise band (μ ± σ pp over N
// baseline weeks) and the current week's z-score. Pure presentation;
// the numbers come straight from
// `/admin/analytics/review-prompt-stats/baseline-noise`. Returns the
// three table cells in render order so the row component can drop
// them inline with its existing columns.
function ReviewPromptBaselineCells({ noise }) {
  if (!noise) {
    return (
      <>
        <td className="text-right text-gray-400 px-3.5 py-2">—</td>
        <td className="text-right text-gray-400 px-3.5 py-2">—</td>
        <td className="text-right text-gray-400 px-3.5 py-2">—</td>
      </>
    );
  }
  const mean = noise.baseline_mean_ctr_pct;
  const stddev = noise.baseline_stddev_pp;
  const z = noise.current_z_score;
  const weeks = noise.baseline_weeks_used || 0;

  // Mean cell — null when fewer than 2 qualifying baseline weeks; show
  // "n/a" with a tooltip so admins know the row exists but the
  // baseline is too thin to summarise.
  let meanCell;
  if (mean == null) {
    meanCell = (
      <span
        className="text-gray-400"
        title={`Need ≥2 baseline weeks above the min-shown gate; have ${weeks}`}
      >
        n/a
      </span>
    );
  } else {
    meanCell = (
      <span
        className="text-gray-700"
        title={`Baseline mean over ${weeks} qualifying week(s)`}
      >
        {mean.toFixed(1)}%
      </span>
    );
  }

  // Stddev cell — same gating as mean.
  let stddevCell;
  if (stddev == null) {
    stddevCell = <span className="text-gray-400">—</span>;
  } else {
    stddevCell = (
      <span
        className="text-gray-700"
        title="Sample stddev (Bessel-corrected) — same formula the auto-tuned alert uses"
      >
        ±{stddev.toFixed(1)} pp
      </span>
    );
  }

  // z-score cell — colour-coded so an admin can spot a cold/hot week
  // at a glance. Note: the auto-tuned alert gates on the WoW pp drop
  // (vs prev week) exceeding sigma_mult × stddev — z here is a quick
  // visual proxy for "how far from normal is this week", not the
  // exact alert decision.
  let zCell;
  if (z == null) {
    zCell = <span className="text-gray-400">—</span>;
  } else {
    const abs = Math.abs(z);
    let cls = 'text-gray-500';
    if (z <= -2) cls = 'text-rose-600 font-semibold';
    else if (z < -1) cls = 'text-amber-600';
    else if (z >= 2) cls = 'text-emerald-600 font-semibold';
    else if (z > 1) cls = 'text-emerald-500';
    const sign = z > 0 ? '+' : '';
    zCell = (
      <span
        className={cls}
        title={`Current CTR is ${abs.toFixed(2)}σ ${z < 0 ? 'below' : 'above'} the baseline mean`}
      >
        {sign}{z.toFixed(2)}σ
      </span>
    );
  }

  return (
    <>
      <td className="text-right font-mono px-3.5 py-2">{meanCell}</td>
      <td className="text-right font-mono px-3.5 py-2">{stddevCell}</td>
      <td className="text-right font-mono px-3.5 py-2">{zCell}</td>
    </>
  );
}

// Task #695 — compute the σ/μ ratio for a reason. ``null`` when the
// baseline is too thin (mean missing, stddev missing, or mean ≤ 0 —
// dividing by a near-zero baseline mean would explode and isn't
// meaningful triage signal anyway). Exported via module-level scope
// so the funnel card can sort by it without re-running the math.
function _reviewPromptVolatilityRatio(noise) {
  if (!noise) return null;
  const mean = noise.baseline_mean_ctr_pct;
  const stddev = noise.baseline_stddev_pp;
  if (mean == null || stddev == null) return null;
  if (mean <= 0) return null;
  return stddev / mean;
}

function ReviewPromptReasonRow({ row, noise, expanded, onToggle, volatilityThreshold }) {
  const status = row?.status;
  const shownDelta = row?.shown_delta;
  const ctrDelta = row?.ctr_delta_pct;

  let shownDeltaCell;
  if (status === 'new') {
    shownDeltaCell = (
      <span className="inline-block px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 font-semibold text-[10px] uppercase tracking-wide">
        new
      </span>
    );
  } else if (status === 'gone') {
    shownDeltaCell = (
      <span className="inline-block px-1.5 py-0.5 rounded bg-rose-100 text-rose-700 font-semibold text-[10px] uppercase tracking-wide">
        gone
      </span>
    );
  } else if (shownDelta == null) {
    shownDeltaCell = <span className="text-gray-400">—</span>;
  } else if (shownDelta > 0) {
    shownDeltaCell = <span className="text-emerald-600">+{shownDelta.toLocaleString()}</span>;
  } else if (shownDelta < 0) {
    shownDeltaCell = <span className="text-rose-600">{shownDelta.toLocaleString()}</span>;
  } else {
    shownDeltaCell = <span className="text-gray-500">0</span>;
  }

  let ctrDeltaCell;
  if (status === 'new' || status === 'gone') {
    ctrDeltaCell = <span className="text-gray-400">—</span>;
  } else if (ctrDelta == null) {
    ctrDeltaCell = <span className="text-gray-400">n/a</span>;
  } else if (ctrDelta > 0) {
    ctrDeltaCell = <span className="text-emerald-600">▲ +{ctrDelta.toFixed(1)} pp</span>;
  } else if (ctrDelta < 0) {
    ctrDeltaCell = <span className="text-rose-600">▼ {ctrDelta.toFixed(1)} pp</span>;
  } else {
    ctrDeltaCell = <span className="text-gray-500">▬ 0.0 pp</span>;
  }

  return (
    <tr
      className="border-t border-gray-200 cursor-pointer hover:bg-gray-100 transition-colors"
      onClick={onToggle}
      title="Click to view 8-week trend"
    >
      <td className="text-left text-gray-700 px-3.5 py-2 truncate">
        <span className="inline-flex items-center gap-1">
          {expanded
            ? <ChevronDown size={12} className="text-gray-400" />
            : <ChevronRight size={12} className="text-gray-400" />}
          <span className="truncate" title={row.reason}>{row.reason || '—'}</span>
          {(() => {
            // Task #695 — flag rows whose σ/μ ratio exceeds the
            // admin-tunable volatility threshold so triage can spot
            // jittery reasons before they false-positive an alert.
            const ratio = _reviewPromptVolatilityRatio(noise);
            if (ratio == null) return null;
            // Threshold of 0 is a valid "highlight every reason with a
            // computable σ/μ" mode; only a negative or non-finite
            // threshold disables the badge entirely.
            if (!Number.isFinite(volatilityThreshold) || volatilityThreshold < 0) return null;
            if (ratio < volatilityThreshold) return null;
            return (
              <span
                className="inline-block px-1.5 py-0.5 rounded bg-amber-100 text-amber-800 font-semibold text-[10px] uppercase tracking-wide"
                title={`σ/μ = ${ratio.toFixed(2)} (≥ ${volatilityThreshold.toFixed(2)} threshold) — high week-to-week variance, likely to false-positive the auto-tuned alert. Consider raising the sigma multiplier.`}
              >
                volatile
              </span>
            );
          })()}
        </span>
      </td>
      <td className="text-right text-gray-700 font-mono px-3.5 py-2">{row.shown}</td>
      <td className="text-right text-gray-700 font-mono px-3.5 py-2">{row.clicked}</td>
      <td className="text-right text-gray-700 font-mono px-3.5 py-2">{row.dismissed}</td>
      <td className="text-right text-gray-700 font-mono px-3.5 py-2">
        {row.ctr_pct == null ? '—' : `${row.ctr_pct}%`}
      </td>
      <td className="text-right font-mono px-3.5 py-2">{shownDeltaCell}</td>
      <td className="text-right font-mono px-3.5 py-2">{ctrDeltaCell}</td>
      <ReviewPromptBaselineCells noise={noise} />
    </tr>
  );
}

// Task #695 — localStorage keys for the sort + threshold knobs so an
// admin's preference survives a refresh. Stored as plain strings to
// keep parsing trivial and forward-compatible.
const REVIEW_PROMPT_SORT_KEY = 'syrabit:review-prompt-funnel:sort';
const REVIEW_PROMPT_VOL_THRESHOLD_KEY = 'syrabit:review-prompt-funnel:vol-threshold';
const REVIEW_PROMPT_VOL_THRESHOLD_DEFAULT = 0.5;
const REVIEW_PROMPT_SORT_OPTIONS = [
  { value: 'shown', label: 'Shown' },
  { value: 'absz', label: '|z|' },
  { value: 'sigmaratio', label: 'σ/μ' },
];

function ReviewPromptFunnelCard({ stats, baseline, loading, error, onRetry, adminToken }) {
  const [expandedReason, setExpandedReason] = useState(null);
  // Task #695 — sort toggle + volatility threshold. Default sort is
  // 'shown' so today's behaviour (most-fired first) doesn't regress
  // for admins who haven't opted in. Lazy initialiser reads
  // localStorage once on mount; SSR-guarded.
  const [sortMode, setSortMode] = useState(() => {
    if (typeof window === 'undefined') return 'shown';
    try {
      const v = window.localStorage.getItem(REVIEW_PROMPT_SORT_KEY);
      if (v && REVIEW_PROMPT_SORT_OPTIONS.some(o => o.value === v)) return v;
    } catch (err) { console.warn('OverviewTab: failed to read review-prompt sort from localStorage:', err); }
    return 'shown';
  });
  const [volatilityThreshold, setVolatilityThreshold] = useState(() => {
    if (typeof window === 'undefined') return REVIEW_PROMPT_VOL_THRESHOLD_DEFAULT;
    try {
      const raw = window.localStorage.getItem(REVIEW_PROMPT_VOL_THRESHOLD_KEY);
      if (raw != null) {
        const parsed = parseFloat(raw);
        if (Number.isFinite(parsed) && parsed >= 0) return parsed;
      }
    } catch (err) { console.warn('OverviewTab: failed to read volatility threshold from localStorage:', err); }
    return REVIEW_PROMPT_VOL_THRESHOLD_DEFAULT;
  });
  useEffect(() => {
    if (typeof window === 'undefined') return;
    try { window.localStorage.setItem(REVIEW_PROMPT_SORT_KEY, sortMode); } catch (err) { console.warn('OverviewTab: failed to persist review-prompt sort to localStorage:', err); }
  }, [sortMode]);
  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      window.localStorage.setItem(
        REVIEW_PROMPT_VOL_THRESHOLD_KEY,
        String(volatilityThreshold),
      );
    } catch (err) { console.warn('OverviewTab: failed to persist volatility threshold to localStorage:', err); }
  }, [volatilityThreshold]);
  if (loading && !stats) {
    return (
      <Card title="Trustpilot Review Prompt Funnel (7d)">
        <p className="text-gray-600 text-sm text-center py-6">Loading…</p>
      </Card>
    );
  }
  if (error) {
    return (
      <Card title="Trustpilot Review Prompt Funnel (7d)" error onRetry={onRetry} />
    );
  }
  const s = stats || {};
  const shown = s.shown || 0;
  const clicked = s.clicked || 0;
  const dismissed = s.dismissed || 0;
  const ctr = s.ctr_pct;
  const dismissRate = s.dismiss_rate_pct;
  const byReasonRaw = Array.isArray(s.by_reason) ? s.by_reason : [];
  const isEmpty = shown === 0 && clicked === 0 && dismissed === 0;
  // Task #681 — per-reason baseline noise lookup. Object keyed by
  // reason name so each row pulls its noise band in O(1).
  const noiseByReason = (baseline && baseline.by_reason) || {};
  const baselineWeeks = baseline?.baseline_weeks ?? null;
  const sigmaMult = baseline?.sigma_mult ?? null;
  const minShown = baseline?.min_shown ?? null;
  // Task #695 — apply the chosen sort. ``shown`` (default) preserves
  // the existing order. ``absz`` highlights reasons whose current
  // week is furthest from baseline (alert-likely). ``sigmaratio``
  // highlights structurally jittery reasons (high σ/μ — most likely
  // to false-positive). Reasons missing the relevant metric sink to
  // the bottom so they don't crowd out actionable rows.
  const byReason = (() => {
    if (sortMode === 'shown') return byReasonRaw;
    const decorated = byReasonRaw.map((row, i) => {
      const noise = noiseByReason[row?.reason || 'unknown'] || null;
      let key = null;
      if (sortMode === 'absz') {
        const z = noise?.current_z_score;
        key = z == null ? null : Math.abs(z);
      } else if (sortMode === 'sigmaratio') {
        key = _reviewPromptVolatilityRatio(noise);
      }
      return { row, i, key };
    });
    decorated.sort((a, b) => {
      const aMissing = a.key == null;
      const bMissing = b.key == null;
      if (aMissing && bMissing) return a.i - b.i;
      if (aMissing) return 1;
      if (bMissing) return -1;
      if (b.key !== a.key) return b.key - a.key;
      return a.i - b.i;
    });
    return decorated.map(d => d.row);
  })();
  const handleOpenSigmaSetting = () => {
    // AdminDashboard owns the Alert Settings panel state; dispatching a
    // window event keeps OverviewTab decoupled from the parent's
    // internals while still giving admins a single click from "I see
    // a noisy reason" to "let me tune the sigma multiplier".
    try {
      window.dispatchEvent(new CustomEvent('syrabit:open-alert-sigma-setting'));
    } catch (err) {
      console.warn('OverviewTab: failed to dispatch syrabit:open-alert-sigma-setting:', err);
    }
  };

  return (
    <Card
      title="Trustpilot Review Prompt Funnel (7d)"
      action={
        <button
          onClick={onRetry}
          className="text-xs text-gray-600 hover:text-gray-700 px-2 py-0.5 rounded-lg flex items-center gap-1"
        >
          <RefreshCw size={10} /> Refresh
        </button>
      }
    >
      {isEmpty ? (
        <div className="flex items-center gap-3 py-4">
          <div className="w-9 h-9 rounded-lg flex items-center justify-center"
            style={{ background: 'rgba(124,58,237,0.10)' }}>
            <Star size={16} className="text-violet-500" />
          </div>
          <div>
            <p className="text-gray-700 text-sm font-medium">No review prompts fired yet</p>
            <p className="text-gray-500 text-xs mt-0.5">
              Counters will populate once engaged students hit a happy moment (quiz, chapter read).
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <Stat icon={Star} label="Prompts shown"
              value={shown.toLocaleString()} color="#7c3aed"
              sub="happy-moment triggers" />
            <Stat icon={MousePointerClick} label="Clicked through"
              value={clicked.toLocaleString()} color="#10b981"
              sub="opened Trustpilot review form" />
            <Stat icon={ShieldCheck} label="Click-through rate"
              value={ctr == null ? '—' : `${ctr}%`}
              color="#06b6d4"
              sub="closest proxy for Trustpilot reviews" />
            <Stat icon={XCircle} label="Dismissed"
              value={dismissed.toLocaleString()} color="#f59e0b"
              sub={dismissRate == null ? 'no shown events' : `${dismissRate}% dismiss rate`} />
          </div>

          {byReason.length > 0 && (
            <div className="rounded-xl border border-gray-200 bg-gray-50 mt-4 overflow-hidden">
              <div className="px-3.5 py-2 border-b border-gray-200 flex items-center justify-between gap-2 flex-wrap">
                <p className="text-gray-500 text-xs font-medium">
                  By trigger reason · Δ vs prev week · noise band
                </p>
                {/* Task #695 — sort toggle + admin-tunable volatility
                    threshold. Stored in localStorage so the choice
                    persists across refreshes. Default sort 'shown'
                    matches today's behaviour. */}
                <div className="flex items-center gap-3 text-[11px] text-gray-500">
                  <label className="inline-flex items-center gap-1">
                    <span>Sort:</span>
                    <select
                      value={sortMode}
                      onChange={(e) => setSortMode(e.target.value)}
                      className="px-1.5 py-0.5 rounded border border-gray-300 bg-white text-gray-700"
                      title="Sort by raw shown count, |z| (furthest from baseline this week), or σ/μ (structurally noisy)."
                    >
                      {REVIEW_PROMPT_SORT_OPTIONS.map(opt => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </label>
                  <label className="inline-flex items-center gap-1">
                    <span title="Reasons whose σ/μ ratio meets or exceeds this threshold get a 'volatile' pill.">
                      Volatile σ/μ ≥
                    </span>
                    <input
                      type="number"
                      min="0"
                      step="0.05"
                      value={volatilityThreshold}
                      onChange={(e) => {
                        const parsed = parseFloat(e.target.value);
                        setVolatilityThreshold(
                          Number.isFinite(parsed) && parsed >= 0
                            ? parsed
                            : REVIEW_PROMPT_VOL_THRESHOLD_DEFAULT,
                        );
                      }}
                      className="w-14 px-1.5 py-0.5 rounded border border-gray-300 bg-white text-gray-700 font-mono"
                    />
                  </label>
                </div>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-gray-500">
                    <th className="text-left font-medium px-3.5 py-2">Reason</th>
                    <th className="text-right font-medium px-3.5 py-2">Shown</th>
                    <th className="text-right font-medium px-3.5 py-2">Clicked</th>
                    <th className="text-right font-medium px-3.5 py-2">Dismissed</th>
                    <th className="text-right font-medium px-3.5 py-2">CTR</th>
                    <th className="text-right font-medium px-3.5 py-2">Δ Shown</th>
                    <th className="text-right font-medium px-3.5 py-2">Δ CTR</th>
                    <th
                      className="text-right font-medium px-3.5 py-2"
                      title={baselineWeeks
                        ? `Baseline mean CTR over the last ${baselineWeeks} qualifying weeks (each ≥ ${minShown ?? 0} shown).`
                        : 'Baseline mean CTR over the configured rolling window.'}
                    >
                      Baseline μ
                    </th>
                    <th
                      className="text-right font-medium px-3.5 py-2"
                      title="Sample stddev of weekly CTR (Bessel-corrected) — same noise band the auto-tuned alert uses."
                    >
                      σ (pp)
                    </th>
                    <th
                      className="text-right font-medium px-3.5 py-2"
                      title="Current 7d CTR distance from the baseline mean, in stddev units. The auto-tuned alert fires when the week-over-week pp drop exceeds sigma × stddev (and the absolute pp floor) — z is a quick proxy for how far this week is from normal."
                    >
                      z (this wk)
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {byReason.map((row, i) => {
                    const reasonKey = row?.reason || 'unknown';
                    const isExpanded = expandedReason === reasonKey;
                    return (
                      <Fragment key={`reason-${i}-${reasonKey}`}>
                        <ReviewPromptReasonRow
                          row={row}
                          noise={noiseByReason[reasonKey] || null}
                          expanded={isExpanded}
                          onToggle={() => setExpandedReason(
                            isExpanded ? null : reasonKey
                          )}
                          volatilityThreshold={volatilityThreshold}
                        />
                        {isExpanded && (
                          <tr className="bg-white">
                            <td colSpan={10} className="p-0">
                              <ReviewPromptReasonTrend
                                adminToken={adminToken}
                                reason={reasonKey}
                              />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
              {/* Task #681 — legend explains the noise band the rightmost
                  three columns render and links straight to the
                  Alert Settings sigma knob (so admins who spot a
                  jittery reason can tune the multiplier without
                  hunting through the alert panel). */}
              <div className="px-3.5 py-2 border-t border-gray-200 bg-white text-[11px] text-gray-500 leading-relaxed">
                <p>
                  <span className="font-medium text-gray-600">Noise band:</span>{' '}
                  μ is the mean per-reason CTR over the last{' '}
                  <span className="text-gray-700">{baselineWeeks ?? '—'}</span> baseline
                  week(s) where shown ≥ <span className="text-gray-700">{minShown ?? '—'}</span>;
                  σ is the sample stddev (in pp). z shows how far this week's CTR
                  sits from μ in σ units — a quick read on volatility. The
                  auto-tuned collapse alert fires when the week-over-week CTR
                  drop exceeds the absolute pp floor AND is also larger than{' '}
                  <span className="text-gray-700">
                    {sigmaMult == null ? '—' : sigmaMult.toFixed(1)}× σ
                  </span>{' '}
                  for that reason.{' '}
                  <button
                    type="button"
                    onClick={handleOpenSigmaSetting}
                    className="text-violet-600 hover:text-violet-700 underline underline-offset-2"
                    title="Jump to the Reason CTR Sigma Multiplier in the Alert Settings panel"
                  >
                    Tune sigma multiplier →
                  </button>
                </p>
              </div>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
