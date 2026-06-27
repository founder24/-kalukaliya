import { AlertTriangle, Clock } from 'lucide-react';
import { classifyStatus, formatRelative } from './shared';

export default function PrerenderStatusBody({ status }) {
  const {
    configured,
    last_triggered_at: last,
    last_status: lastStatus,
    last_reason: lastReason,
    last_error: lastError,
    pending_reasons: pendingReasons = [],
    pending,
    trigger_count: triggerCount,
    coalesce_window_sec: coalesceSec,
    min_interval_sec: minIntervalSec,
    nightly_interval_sec: nightlySec,
  } = status;

  const klass = classifyStatus(lastStatus);
  const statusColor =
    klass === 'ok'   ? 'text-emerald-600 bg-emerald-50 border-emerald-200'
    : klass === 'fail' ? 'text-red-600 bg-red-50 border-red-200'
    : 'text-gray-500 bg-gray-50 border-gray-200';
  const statusLabel = lastStatus ?? 'never fired';

  return (
    <div className="space-y-3">
      {configured === false && (
        <div className="flex items-start gap-2 p-3 rounded-xl bg-amber-50 border border-amber-200 text-xs text-amber-700">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <span><span className="font-mono font-semibold">CF_PAGES_DEPLOY_HOOK_URL</span> is not configured on the backend. Refresh requests will fail.</span>
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="rounded-xl p-3 border border-gray-200 bg-white">
          <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1 flex items-center gap-1">
            <Clock size={10} /> Last fired
          </p>
          <p className="text-sm font-mono font-semibold text-gray-900" data-testid="prerender-last-fired">
            {formatRelative(last)}
          </p>
          {last && (
            <p className="text-[10px] text-gray-400 mt-0.5">
              {new Date(last * 1000).toLocaleString()}
            </p>
          )}
        </div>

        <div className={`rounded-xl p-3 border ${statusColor}`}>
          <p className="text-[10px] uppercase tracking-wider opacity-60 mb-1">Last status</p>
          <p className="text-sm font-mono font-semibold" data-testid="prerender-last-status">{statusLabel}</p>
          {lastReason && <p className="text-[10px] opacity-70 mt-0.5 truncate" title={lastReason}>{lastReason}</p>}
        </div>

        <div className="rounded-xl p-3 border border-gray-200 bg-white">
          <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-1">Pending reasons</p>
          <p className="text-sm font-mono font-semibold text-gray-900" data-testid="prerender-queued-count">
            {pendingReasons.length}{pending ? ' · queued' : ''}
          </p>
          {pendingReasons.length > 0 && (
            <p className="text-[10px] text-gray-400 mt-0.5 truncate" title={pendingReasons.join(', ')}>
              {pendingReasons.slice(0, 3).join(', ')}{pendingReasons.length > 3 ? '…' : ''}
            </p>
          )}
        </div>
      </div>

      {lastError && (
        <div className="flex items-start gap-2 p-3 rounded-xl bg-red-50 border border-red-200 text-xs text-red-700">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-semibold mb-0.5">Last error</p>
            <p className="font-mono break-all">{String(lastError)}</p>
          </div>
        </div>
      )}

      <div className="text-[11px] text-gray-400 leading-relaxed border-t border-gray-100 pt-3 mt-1">
        Total triggers: <span className="font-mono text-gray-600">{triggerCount ?? 0}</span>
        {' · '}
        coalesce window <span className="font-mono text-gray-600">{coalesceSec ?? '?'}s</span>
        {' · '}
        cooldown <span className="font-mono text-gray-600">{minIntervalSec ?? '?'}s</span>
        {' · '}
        nightly safety-net every <span className="font-mono text-gray-600">{nightlySec ?? '?'}s</span>.
      </div>
    </div>
  );
}
