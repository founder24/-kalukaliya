import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, ExternalLink, RefreshCcw, ShieldAlert } from 'lucide-react';
import { adminGetBreakGlassStatus } from '@/utils/api';

const POLL_MS = 60_000;

const RUNBOOK_URL =
  'https://github.com/founder24/-kalukaliya/blob/master/docs/cloudflare-access-break-glass.md';

export default function BreakGlassBanner({ adminToken }) {
  const [active, setActive] = useState(false);
  const [loading, setLoading] = useState(false);
  const [hasSucceededOnce, setHasSucceededOnce] = useState(false);
  const [stale, setStale] = useState(false);
  const pollRef = useRef(null);

  const fetchStatus = useCallback(async () => {
    if (!adminToken) return;
    setLoading(true);
    try {
      const response = await adminGetBreakGlassStatus(adminToken);
      setActive(Boolean(response?.data?.active));
      setHasSucceededOnce(true);
      setStale(false);
    } catch {
      // Keep a last-known active warning visible through transient failures.
      // Before the first successful read, show an explicit unknown state rather
      // than silently implying that Cloudflare Access is enforcing its policy.
      setStale(true);
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => {
    if (!adminToken) return undefined;
    fetchStatus();
    pollRef.current = setInterval(fetchStatus, POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [adminToken, fetchStatus]);

  if (!active && !stale) return null;

  const unavailable = stale && !hasSucceededOnce;

  return (
    <div
      role="alert"
      data-testid="break-glass-banner"
      className={`flex items-start gap-3 px-4 py-3 border-b text-white shadow-sm ${
        unavailable ? 'border-amber-300 bg-amber-600' : 'border-red-300 bg-red-600'
      }`}
    >
      <ShieldAlert size={20} className="flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold leading-snug flex items-center gap-2">
          {unavailable
            ? 'Cloudflare Access bypass status is unavailable.'
            : 'Cloudflare Access is bypassed — restore enforcement once the incident is over.'}
          {stale && (
            <span
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-black/20 text-white"
              data-testid="break-glass-banner-stale"
              title={unavailable
                ? 'The status request failed. Retrying automatically.'
                : 'The status request failed. Showing the last-known active state until the next successful poll.'}
            >
              <AlertTriangle size={10} />
              status unavailable, retrying
            </span>
          )}
        </p>
        <p className="text-xs text-white/85 mt-0.5">
          {unavailable
            ? 'Staff authentication remains required, but the portal cannot confirm whether Access enforcement is active.'
            : 'Break-glass mode is active. Staff authentication remains required, but Cloudflare Access is not enforcing its normal login policy for this session.'}
        </p>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <button
          type="button"
          onClick={fetchStatus}
          disabled={loading}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-semibold bg-black/20 hover:bg-black/30 disabled:opacity-60 transition"
          data-testid="break-glass-banner-recheck"
        >
          <RefreshCcw size={12} className={loading ? 'animate-spin' : ''} />
          Recheck
        </button>
        <a
          href={RUNBOOK_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-semibold bg-white text-red-700 hover:bg-red-50 transition"
          data-testid="break-glass-banner-runbook"
        >
          Runbook
          <ExternalLink size={12} />
        </a>
      </div>
    </div>
  );
}