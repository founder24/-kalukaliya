import { Loader2, RefreshCw, Calendar, Clock, AlertTriangle, CheckCircle2, ShieldCheck, HelpCircle } from 'lucide-react';

function fmtDate(v) {
  if (!v) return '—';
  try {
    const d = typeof v === 'string' ? new Date(v) : new Date(v);
    if (Number.isNaN(d.getTime())) return String(v);
    return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
  } catch { return String(v); }
}

function hoursSince(v) {
  if (!v) return null;
  try {
    const d = new Date(v);
    if (Number.isNaN(d.getTime())) return null;
    return (Date.now() - d.getTime()) / 3600000;
  } catch { return null; }
}

function fmtRelative(v) {
  const h = hoursSince(v);
  if (h == null) return 'never';
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}m ago`;
  if (h < 48) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function monitorStatePill(state) {
  // `state` is the persisted ``last_state`` string from the lock doc:
  // "stale" (red), "healthy" (green), or null (the monitor has not
  // yet recorded a transition — most often a fresh install).
  if (state === 'stale') {
    return { label: 'Stale', bg: 'rgba(239,68,68,0.10)', fg: '#b91c1c', border: 'rgba(239,68,68,0.30)' };
  }
  if (state === 'healthy') {
    return { label: 'Healthy', bg: 'rgba(16,185,129,0.10)', fg: '#047857', border: 'rgba(16,185,129,0.25)' };
  }
  return { label: 'Not yet observed', bg: '#f3f4f6', fg: '#6b7280', border: '#e5e7eb' };
}

function heartbeatStatePill(state) {
  // Heartbeat watcher's persisted ``last_state``: "down" means the
  // watcher paged because the monitor stopped heartbeating; "healthy"
  // means it has since recovered. ``null`` means the watcher has not
  // observed (or persisted) a transition yet — typically a fresh
  // install, since the watcher only writes on actual down/recovery.
  if (state === 'down') {
    return { label: 'Down', bg: 'rgba(239,68,68,0.10)', fg: '#b91c1c', border: 'rgba(239,68,68,0.30)' };
  }
  if (state === 'healthy') {
    return { label: 'Recovered', bg: 'rgba(16,185,129,0.10)', fg: '#047857', border: 'rgba(16,185,129,0.25)' };
  }
  return { label: 'Not yet observed', bg: '#f3f4f6', fg: '#6b7280', border: '#e5e7eb' };
}

function nextExpectedRun(cfg, lastIso) {
  if (!cfg) return null;
  const hour = Number(cfg.target_hour_utc ?? 2);
  const freq = cfg.frequency || 'daily';
  const now = new Date();
  const candidate = new Date(Date.UTC(
    now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hour, 0, 0
  ));
  if (freq === 'weekly') {
    const targetDow = Number(cfg.weekday ?? 1); // 0=Mon ... 6=Sun by Python convention; treat as JS day where 0=Sun, 1=Mon
    // Backend uses Python's weekday(): Mon=0..Sun=6. Convert to JS getUTCDay (Sun=0..Sat=6).
    const jsDow = (targetDow + 1) % 7;
    let diff = (jsDow - candidate.getUTCDay() + 7) % 7;
    if (diff === 0 && candidate <= now) diff = 7;
    candidate.setUTCDate(candidate.getUTCDate() + diff);
  } else {
    if (candidate <= now) candidate.setUTCDate(candidate.getUTCDate() + 1);
  }
  // Ensure we're after last run
  if (lastIso) {
    try {
      const last = new Date(lastIso);
      if (!Number.isNaN(last.getTime()) && candidate <= last) {
        candidate.setUTCDate(candidate.getUTCDate() + (freq === 'weekly' ? 7 : 1));
      }
    } catch { /* ignore */ }
  }
  return candidate.toISOString();
}

function hourLabel(hour) {
  if (hour == null || Number.isNaN(Number(hour))) return '—';
  const h = Number(hour);
  const utc = `${String(h).padStart(2, '0')}:00 UTC`;
  // IST = UTC + 5:30
  const istMin = h * 60 + 330;
  const istH = Math.floor((istMin / 60) % 24);
  const istM = istMin % 60;
  const ist = `${String(istH).padStart(2, '0')}:${String(istM).padStart(2, '0')} IST`;
  return `${utc} · ${ist}`;
}

const WEEKDAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export default function SchedulePanel({ schedule, scheduleLoading, loadSchedule }) {
  const cfg = schedule?.config || null;
  const lastMarker = schedule?.last_marker || null;
  const recent = (schedule?.recent_runs || []).slice(0, 5);
  const enabled = Boolean(cfg?.enabled);
  const freq = cfg?.frequency || 'daily';
  const lastRunIso = recent[0]?.completed_at || lastMarker?.claimed_at || lastMarker?.last_run_at || null;
  const lastAgeH = hoursSince(lastRunIso);
  const staleThresholdH = freq === 'weekly' ? 24 * 8 : 36;
  const isStale = enabled && (lastAgeH == null || lastAgeH > staleThresholdH);
  const next = nextExpectedRun(cfg, lastRunIso);
  const monitor = schedule?.staleness_monitor || null;
  const pill = monitorStatePill(monitor?.last_state);
  const debounceH = Number(monitor?.debounce_remaining_h ?? 0);
  const realertH = Number(monitor?.realert_interval_h ?? 24);
  const inDebounce = debounceH > 0;
  const tooltip = `The staleness monitor re-pages admins at most once every ${realertH}h while the scheduler is stale, so a known-broken cron doesn't spam your inbox. ${
    inDebounce
      ? `An alert was sent recently — the next re-page would fire in ~${Math.round(debounceH)}h if the scheduler is still stale by then.`
      : 'No active debounce — the next stale observation would page admins immediately.'
  }`;

  const heartbeat = schedule?.heartbeat_monitor || null;
  const hbPill = heartbeatStatePill(heartbeat?.last_state);
  const hbAgeH = heartbeat?.age_h == null ? null : Number(heartbeat.age_h);
  const hbMaxAgeH = Number(heartbeat?.max_age_h ?? 3);
  const hbDebounceH = Number(heartbeat?.debounce_remaining_h ?? 0);
  const hbRealertH = Number(heartbeat?.realert_interval_h ?? 12);
  const hbInDebounce = hbDebounceH > 0;
  const hbBehind = hbAgeH != null && hbAgeH > hbMaxAgeH;
  const hbTooltip = `The heartbeat watcher pages admins once if the staleness monitor itself stops heartbeating (no signal for >${hbMaxAgeH}h), then debounces re-pages for ${hbRealertH}h. ${
    hbInDebounce
      ? `An alert was sent recently — the next re-page would fire in ~${Math.round(hbDebounceH)}h if the monitor is still down by then.`
      : 'No active debounce — the next down observation would page admins immediately.'
  }`;

  return (
    <div className="space-y-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-gray-900">Scheduled auto-publish</p>
          <p className="text-xs mt-0.5" style={{ color: '#9ca3af' }}>
            Status of the background job that generates SEO pages on a {freq} cadence.
          </p>
        </div>
        <button onClick={loadSchedule} disabled={scheduleLoading}
          className="p-1.5 rounded-lg border border-gray-200 hover:bg-gray-50 transition-colors text-gray-400">
          <RefreshCw size={14} className={scheduleLoading ? 'animate-spin' : ''} />
        </button>
      </div>

      {scheduleLoading && !schedule && (
        <div className="rounded-xl p-6 border border-gray-200 bg-gray-50 flex items-center gap-2 text-sm text-gray-500">
          <Loader2 size={14} className="animate-spin" /> Loading schedule…
        </div>
      )}

      {schedule && (
        <>
          {isStale && (
            <div className="rounded-xl p-4 border flex items-start gap-3"
              style={{ background: 'rgba(239,68,68,0.06)', borderColor: 'rgba(239,68,68,0.25)' }}>
              <AlertTriangle size={16} className="mt-0.5" style={{ color: '#ef4444' }} />
              <div>
                <p className="text-sm font-semibold" style={{ color: '#b91c1c' }}>
                  Scheduled job may have stopped firing
                </p>
                <p className="text-xs mt-0.5" style={{ color: '#7f1d1d' }}>
                  {lastRunIso
                    ? `Last scheduled run was ${Math.round(lastAgeH)}h ago — expected at most every ${staleThresholdH}h for a ${freq} cadence.`
                    : `No scheduled run has been recorded yet — expected at least one within ${staleThresholdH}h.`}
                </p>
              </div>
            </div>
          )}

          {!isStale && enabled && (
            <div className="rounded-xl p-4 border flex items-start gap-3"
              style={{ background: 'rgba(16,185,129,0.06)', borderColor: 'rgba(16,185,129,0.20)' }}>
              <CheckCircle2 size={16} className="mt-0.5" style={{ color: '#10b981' }} />
              <div>
                <p className="text-sm font-semibold" style={{ color: '#047857' }}>Scheduler healthy</p>
                <p className="text-xs mt-0.5" style={{ color: '#065f46' }}>
                  {lastRunIso
                    ? `Most recent scheduled run completed ${Math.round(lastAgeH)}h ago.`
                    : 'Scheduler is enabled and waiting for its first run.'}
                </p>
              </div>
            </div>
          )}

          {!enabled && (
            <div className="rounded-xl p-4 border flex items-start gap-3"
              style={{ background: 'rgba(245,158,11,0.07)', borderColor: 'rgba(245,158,11,0.25)' }}>
              <AlertTriangle size={16} className="mt-0.5" style={{ color: '#d97706' }} />
              <div>
                <p className="text-sm font-semibold" style={{ color: '#92400e' }}>Scheduler disabled</p>
                <p className="text-xs mt-0.5" style={{ color: '#78350f' }}>
                  Set <code className="font-mono">SEO_AUTO_PUBLISH_ENABLED=1</code> in the backend env to turn it on.
                </p>
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <div className="rounded-xl p-4 border" style={{ background: '#fafafa', borderColor: '#e5e7eb' }}>
              <div className="flex items-center gap-2">
                <ShieldCheck size={14} style={{ color: '#6b7280' }} />
                <p className="text-sm font-semibold text-gray-900">Monitor health</p>
                <span
                  className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium"
                  style={{ background: pill.bg, color: pill.fg, border: `1px solid ${pill.border}` }}
                >
                  {pill.label}
                </span>
                <span title={tooltip} className="cursor-help inline-flex items-center" style={{ color: '#9ca3af' }}>
                  <HelpCircle size={12} />
                </span>
              </div>
              <p className="text-[11px] mt-1" style={{ color: '#9ca3af' }}>
                Server-side check that emails admins when this scheduler stops firing. Re-pages at most once every {realertH}h while stale.
              </p>
              <div className="grid grid-cols-2 gap-3 mt-3">
                <div>
                  <p className="text-[10px] uppercase tracking-wider" style={{ color: '#9ca3af' }}>Last evaluated</p>
                  <p className="text-xs font-medium mt-1 text-gray-800" title={fmtDate(monitor?.updated_at)}>
                    {fmtRelative(monitor?.updated_at)}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider" style={{ color: '#9ca3af' }}>Last alert sent</p>
                  <p className="text-xs font-medium mt-1 text-gray-800" title={fmtDate(monitor?.last_alert_at)}>
                    {fmtRelative(monitor?.last_alert_at)}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider" style={{ color: '#9ca3af' }}>Last run observed</p>
                  <p className="text-xs font-medium mt-1 text-gray-800" title={fmtDate(monitor?.last_run_at_observed)}>
                    {monitor?.last_run_at_observed ? fmtRelative(monitor.last_run_at_observed) : '—'}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider" style={{ color: '#9ca3af' }}>Re-page debounce</p>
                  <p
                    className="text-xs font-medium mt-1"
                    style={{ color: inDebounce ? '#b45309' : '#10b981' }}
                    title={tooltip}
                  >
                    {inDebounce ? `${Math.round(debounceH)}h remaining` : 'Ready to fire'}
                  </p>
                </div>
              </div>
            </div>

            <div className="rounded-xl p-4 border" style={{ background: '#fafafa', borderColor: '#e5e7eb' }}>
              <div className="flex items-center gap-2">
                <ShieldCheck size={14} style={{ color: '#6b7280' }} />
                <p className="text-sm font-semibold text-gray-900">Heartbeat watcher</p>
                <span
                  className="inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium"
                  style={{ background: hbPill.bg, color: hbPill.fg, border: `1px solid ${hbPill.border}` }}
                >
                  {hbPill.label}
                </span>
                <span title={hbTooltip} className="cursor-help inline-flex items-center" style={{ color: '#9ca3af' }}>
                  <HelpCircle size={12} />
                </span>
              </div>
              <p className="text-[11px] mt-1" style={{ color: '#9ca3af' }}>
                Watches the monitor itself. Pages admins once if the monitor stops heartbeating for &gt;{hbMaxAgeH}h, then debounces re-pages for {hbRealertH}h.
              </p>
              <div className="grid grid-cols-2 gap-3 mt-3">
                <div>
                  <p className="text-[10px] uppercase tracking-wider" style={{ color: '#9ca3af' }}>Monitor heartbeat age</p>
                  <p
                    className="text-xs font-medium mt-1"
                    style={{ color: hbAgeH == null ? '#6b7280' : (hbBehind ? '#b91c1c' : '#047857') }}
                    title={fmtDate(heartbeat?.monitor_updated_at)}
                  >
                    {hbAgeH == null
                      ? '—'
                      : hbAgeH < 1
                        ? `${Math.max(1, Math.round(hbAgeH * 60))}m`
                        : `${hbAgeH.toFixed(1)}h`}
                    {hbAgeH != null && (
                      <span style={{ color: '#9ca3af' }}> / {hbMaxAgeH}h max</span>
                    )}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider" style={{ color: '#9ca3af' }}>Last alert sent</p>
                  <p className="text-xs font-medium mt-1 text-gray-800" title={fmtDate(heartbeat?.last_alert_at)}>
                    {fmtRelative(heartbeat?.last_alert_at)}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider" style={{ color: '#9ca3af' }}>Watcher state</p>
                  <p
                    className="text-xs font-medium mt-1"
                    style={{ color: heartbeat?.last_state === 'down' ? '#b91c1c' : (heartbeat?.last_state === 'healthy' ? '#047857' : '#6b7280') }}
                  >
                    {heartbeat?.last_state ? heartbeat.last_state : 'not yet observed'}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] uppercase tracking-wider" style={{ color: '#9ca3af' }}>Re-page debounce</p>
                  <p
                    className="text-xs font-medium mt-1"
                    style={{ color: hbInDebounce ? '#b45309' : '#10b981' }}
                    title={hbTooltip}
                  >
                    {hbInDebounce ? `${Math.round(hbDebounceH)}h remaining` : 'Ready to fire'}
                  </p>
                </div>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="rounded-xl p-4 border" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
              <p className="text-[10px] uppercase tracking-wider" style={{ color: '#9ca3af' }}>Status</p>
              <p className="text-sm font-semibold mt-1" style={{ color: enabled ? '#10b981' : '#9ca3af' }}>
                {enabled ? 'Enabled' : 'Disabled'}
              </p>
            </div>
            <div className="rounded-xl p-4 border" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
              <p className="text-[10px] uppercase tracking-wider" style={{ color: '#9ca3af' }}>Frequency</p>
              <p className="text-sm font-semibold mt-1 capitalize text-gray-900">
                {freq}
                {freq === 'weekly' && cfg?.weekday != null
                  ? ` · ${WEEKDAY_LABELS[Number(cfg.weekday) % 7] || ''}`
                  : ''}
              </p>
            </div>
            <div className="rounded-xl p-4 border" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
              <p className="text-[10px] uppercase tracking-wider" style={{ color: '#9ca3af' }}>Target hour</p>
              <p className="text-sm font-semibold mt-1 text-gray-900">{hourLabel(cfg?.target_hour_utc)}</p>
            </div>
            <div className="rounded-xl p-4 border" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
              <p className="text-[10px] uppercase tracking-wider" style={{ color: '#9ca3af' }}>Next expected run</p>
              <p className="text-sm font-semibold mt-1 text-gray-900 flex items-center gap-1">
                <Clock size={12} className="text-gray-400" />
                {enabled ? fmtDate(next) : '—'}
              </p>
            </div>
          </div>

          {Array.isArray(cfg?.page_types) && cfg.page_types.length > 0 && (
            <div className="text-xs" style={{ color: '#6b7280' }}>
              Page types: {cfg.page_types.map((pt) => (
                <span key={pt} className="inline-block px-2 py-0.5 mr-1 rounded-md font-mono"
                  style={{ background: '#f3f4f6', border: '1px solid #e5e7eb', color: '#374151' }}>{pt}</span>
              ))}
            </div>
          )}

          <div className="rounded-xl border overflow-hidden" style={{ borderColor: '#e5e7eb' }}>
            <div className="px-4 py-3 flex items-center gap-2 border-b" style={{ background: '#f9fafb', borderColor: '#e5e7eb' }}>
              <Calendar size={14} className="text-gray-400" />
              <p className="text-sm font-semibold text-gray-900">Recent scheduled runs</p>
              <span className="text-xs" style={{ color: '#9ca3af' }}>(last {recent.length})</span>
            </div>
            {recent.length === 0 ? (
              <div className="p-6 text-center text-sm" style={{ color: '#9ca3af' }}>
                No scheduled runs recorded yet.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead style={{ background: '#fafafa', color: '#6b7280' }}>
                    <tr className="text-left">
                      <th className="px-4 py-2 font-medium">When</th>
                      <th className="px-4 py-2 font-medium">Generated</th>
                      <th className="px-4 py-2 font-medium">Skipped</th>
                      <th className="px-4 py-2 font-medium">Errors</th>
                      <th className="px-4 py-2 font-medium">Avg SEO</th>
                      <th className="px-4 py-2 font-medium">Avg GEO</th>
                      <th className="px-4 py-2 font-medium">Job</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.map((r, idx) => {
                      const errors = Number(r.errors || 0);
                      return (
                        <tr key={r.job_id || idx} className="border-t" style={{ borderColor: '#f3f4f6' }}>
                          <td className="px-4 py-2 text-gray-700">{fmtDate(r.completed_at)}</td>
                          <td className="px-4 py-2 font-semibold" style={{ color: '#10b981' }}>{r.total_generated ?? 0}</td>
                          <td className="px-4 py-2 text-gray-600">{r.skipped ?? 0}</td>
                          <td className="px-4 py-2 font-semibold" style={{ color: errors > 0 ? '#ef4444' : '#9ca3af' }}>{errors}</td>
                          <td className="px-4 py-2 text-gray-700">{r.avg_seo_score != null ? Math.round(r.avg_seo_score) : '—'}</td>
                          <td className="px-4 py-2 text-gray-700">{r.avg_geo_score != null ? Math.round(r.avg_geo_score) : '—'}</td>
                          <td className="px-4 py-2 font-mono text-[10px]" style={{ color: '#9ca3af' }}>{r.job_id || '—'}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
