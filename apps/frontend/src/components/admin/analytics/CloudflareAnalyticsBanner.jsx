import { useEffect, useState, useCallback, useRef } from 'react';
import { Cloud, RefreshCw, ExternalLink, ChevronDown, ChevronUp, Loader2, CheckCircle } from 'lucide-react';
import { adminGetCfStatus, adminCfRecheck } from '@/utils/api';

const REQUIRED_SCOPES = [
  'Account · Account Analytics : Read',
  'Zone · Zone Analytics : Read',
  'Zone · Zone : Read',
];
const CF_TOKEN_PAGE = 'https://dash.cloudflare.com/profile/api-tokens';

function fmtBlocked(seconds) {
  if (seconds == null || seconds <= 0) return null;
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

function fmtCheckedAt(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
    timeZoneName: 'short',
  });
}

export default function CloudflareAnalyticsBanner({
  adminToken,
  onRecheck,
  variant = 'default',
  className = '',
}) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [rechecking, setRechecking] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [recheckError, setRecheckError] = useState(null);
  const [recheckedOk, setRecheckedOk] = useState(false);
  // Monotonic request id so an older /cf-status response cannot overwrite
  // a newer one (e.g. when adminToken changes mid-flight on logout/swap).
  const reqIdRef = useRef(0);

  const fetchStatus = useCallback(async () => {
    // No token → reset all banner state (logout / token cleared).
    if (!adminToken) {
      reqIdRef.current += 1;
      setStatus(null);
      setRecheckError(null);
      setRecheckedOk(false);
      setExpanded(false);
      setLoading(false);
      return;
    }
    const myReq = ++reqIdRef.current;
    try {
      const r = await adminGetCfStatus(adminToken);
      if (myReq !== reqIdRef.current) return; // a newer call superseded us
      setStatus(r.data || null);
    } catch (_e) {
      if (myReq !== reqIdRef.current) return;
      setStatus(null);
    } finally {
      if (myReq === reqIdRef.current) setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => {
    fetchStatus();
  }, [fetchStatus]);

  const handleRecheck = async () => {
    if (!adminToken || rechecking) return;
    setRechecking(true);
    setRecheckError(null);
    setRecheckedOk(false);
    const myReq = ++reqIdRef.current;
    try {
      const r = await adminCfRecheck(adminToken);
      if (myReq !== reqIdRef.current) return;
      const next = r.data || null;
      setStatus(next);
      // Notify parent on every successful recheck response so it can refresh
      // dependent widgets — recovery (auth_ok=true) AND failure transitions
      // both warrant a parent reload (e.g. to clear stale chart data).
      onRecheck?.(next);
      if (next?.auth_ok) {
        setRecheckedOk(true);
      } else if (next?.last_error) {
        setRecheckError(next.last_error);
      } else {
        setRecheckError('Cloudflare still not responding — try again in a minute.');
      }
    } catch (e) {
      if (myReq !== reqIdRef.current) return;
      setRecheckError(e?.response?.data?.detail || e?.message || 'Re-check failed');
    } finally {
      if (myReq === reqIdRef.current) setRechecking(false);
    }
  };

  if (loading || !status) return null;
  // Healthy — render nothing (parent already shows the data).
  if (status.auth_ok === true) return null;

  const isDark = variant === 'dark';
  const palette = isDark
    ? {
        bg: 'rgba(239,68,68,0.06)',
        border: '1px solid rgba(239,68,68,0.20)',
        title: 'text-red-600',
        body: 'text-red-700/80',
        subBg: 'rgba(0,0,0,0.20)',
        subBorder: '1px solid rgba(239,68,68,0.10)',
        meta: 'text-red-200/70',
        chip: 'bg-red-500/10 text-red-700 border border-red-500/20',
        btn: 'bg-red-500/15 text-red-200 border border-red-500/25 hover:bg-red-500/25',
        btnGhost: 'text-red-700 hover:text-red-100',
        codeBg: 'bg-black/30 text-red-100',
      }
    : {
        bg: '#fef2f2',
        border: '1px solid #fecaca',
        title: 'text-red-700',
        body: 'text-red-600',
        subBg: '#ffffff',
        subBorder: '1px solid #fecaca',
        meta: 'text-red-500/80',
        chip: 'bg-white text-red-700 border border-red-200',
        btn: 'bg-white text-red-700 border border-red-300 hover:bg-red-50',
        btnGhost: 'text-red-600 hover:text-red-800',
        codeBg: 'bg-red-50 text-red-700',
      };

  const blockedFor = fmtBlocked(status.blocked_for_seconds);
  const checkedAt = fmtCheckedAt(status.last_check_at);
  const hint = status.rotation_hint || (
    !status.configured
      ? 'CF_ANALYTICS_API_TOKEN or CF_ZONE_ID is missing. Set both as Railway secrets.'
      : 'Create a new Cloudflare API token with Account Analytics:Read, Zone Analytics:Read, Zone:Read scopes, then update CF_ANALYTICS_API_TOKEN.'
  );
  const headline = !status.configured
    ? 'Cloudflare analytics not configured'
    : status.needs_rotation
      ? 'Cloudflare analytics token rejected — rotation needed'
      : 'Cloudflare analytics unavailable';
  const subline = !status.configured
    ? 'CF_ANALYTICS_API_TOKEN and/or CF_ZONE_ID environment variables are not set on the backend.'
    : status.last_error
      ? `Last error from Cloudflare: ${status.last_error}`
      : 'No data is being returned for visitor and page-view counts.';

  return (
    <div
      className={`rounded-xl ${className}`}
      style={{ background: palette.bg, border: palette.border }}
      data-testid="cf-analytics-banner"
    >
      {/* Top summary row */}
      <div className="flex items-start gap-3 p-3.5">
        <Cloud size={16} className={`flex-shrink-0 mt-0.5 ${palette.title}`} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <p className={`text-sm font-semibold ${palette.title}`}>{headline}</p>
            {blockedFor && (
              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${palette.chip}`}>
                breaker open · re-probe allowed in {blockedFor}
              </span>
            )}
            {status.consecutive_failures > 0 && (
              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${palette.chip}`}>
                {status.consecutive_failures} consecutive 401s
              </span>
            )}
          </div>
          <p className={`text-xs mt-0.5 ${palette.body}`}>{subline}</p>
          {checkedAt && (
            <p className={`text-[11px] mt-1 ${palette.meta}`}>Last probed {checkedAt}</p>
          )}
          {recheckedOk && (
            <p className="text-[11px] mt-1 text-emerald-500 flex items-center gap-1">
              <CheckCircle size={11} /> Token accepted — refresh the dashboard to load data.
            </p>
          )}
        </div>
        <div className="flex flex-col items-stretch gap-1.5 flex-shrink-0">
          <button
            onClick={handleRecheck}
            disabled={rechecking || !adminToken}
            className={`flex items-center justify-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors disabled:opacity-50 ${palette.btn}`}
            data-testid="cf-recheck-btn"
          >
            {rechecking ? <Loader2 size={11} className="animate-spin" /> : <RefreshCw size={11} />}
            {rechecking ? 'Re-checking…' : 'Re-check now'}
          </button>
          <button
            onClick={() => setExpanded(v => !v)}
            className={`flex items-center justify-center gap-1 text-[11px] px-2 py-1 rounded-lg transition-colors ${palette.btnGhost}`}
            aria-expanded={expanded}
          >
            {expanded ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
            {expanded ? 'Hide details' : 'How to fix'}
          </button>
        </div>
      </div>

      {recheckError && !recheckedOk && (
        <div className={`mx-3.5 mb-3 px-2.5 py-1.5 rounded text-[11px] font-mono ${palette.codeBg}`} role="alert">
          {recheckError}
        </div>
      )}

      {/* Expanded "How to fix" panel */}
      {expanded && (
        <div
          className="border-t px-3.5 py-3 space-y-2.5"
          style={{ borderColor: isDark ? 'rgba(239,68,68,0.15)' : '#fecaca', background: palette.subBg }}
        >
          <p className={`text-xs ${palette.body}`}>{hint}</p>

          <div>
            <p className={`text-[10px] uppercase tracking-wider font-semibold mb-1.5 ${palette.meta}`}>
              Probe diagnostics
            </p>
            <dl className={`text-[11px] grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1 ${palette.body}`}>
              <dt className={`font-mono ${palette.meta}`}>last_error</dt>
              <dd className={`font-mono break-all ${palette.body}`}>
                {status.last_error || <span className={palette.meta}>—</span>}
              </dd>
              <dt className={`font-mono ${palette.meta}`}>consecutive_failures</dt>
              <dd className={`font-mono ${palette.body}`}>{status.consecutive_failures ?? 0}</dd>
              <dt className={`font-mono ${palette.meta}`}>blocked_for_seconds</dt>
              <dd className={`font-mono ${palette.body}`}>
                {status.blocked_for_seconds != null && status.blocked_for_seconds > 0
                  ? `${status.blocked_for_seconds}s (${blockedFor})`
                  : <span className={palette.meta}>0 — breaker closed</span>}
              </dd>
              {status.configured != null && (
                <>
                  <dt className={`font-mono ${palette.meta}`}>configured</dt>
                  <dd className={`font-mono ${palette.body}`}>{String(status.configured)}</dd>
                </>
              )}
            </dl>
          </div>

          <div>
            <p className={`text-[10px] uppercase tracking-wider font-semibold mb-1.5 ${palette.meta}`}>
              Required scopes (all three, Read)
            </p>
            <ul className="space-y-1">
              {REQUIRED_SCOPES.map(s => (
                <li
                  key={s}
                  className={`text-[11px] font-mono px-2 py-1 rounded ${palette.codeBg}`}
                >
                  {s}
                </li>
              ))}
            </ul>
          </div>

          <div className="flex items-center gap-2 flex-wrap pt-1">
            <a
              href={CF_TOKEN_PAGE}
              target="_blank"
              rel="noopener noreferrer"
              className={`flex items-center gap-1.5 text-xs font-medium px-2.5 py-1.5 rounded-lg transition-colors ${palette.btn}`}
            >
              <ExternalLink size={11} /> Open Cloudflare API tokens
            </a>
            <span className={`text-[11px] ${palette.meta}`}>
              After creating the token, paste it into Replit / Railway as <code className={`px-1 rounded ${palette.codeBg}`}>CF_ANALYTICS_API_TOKEN</code>, then click <strong>Re-check now</strong> — no service restart needed.
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
