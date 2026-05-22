import { useEffect, useRef, useState, useCallback } from 'react';
import { ShieldAlert, ExternalLink, RefreshCcw, AlertTriangle, ShieldOff } from 'lucide-react';
import { toast } from 'sonner';
import { adminGetDiagnostics, adminDisableBreakGlass } from '@/utils/api';

const POLL_MS = 60_000;

const RUNBOOK_URL =
  'https://github.com/shaitanfiles-cloud/syrabit-zip-convert/blob/master/artifacts/syrabit-backend/docs/CLOUDFLARE_ZERO_TRUST.md#71-what-to-do-if-cloudflare-access-goes-down';

export default function BreakGlassBanner({ adminToken }) {
  const [active, setActive] = useState(false);
  const [source, setSource] = useState(null);
  const [loading, setLoading] = useState(false);
  const [disabling, setDisabling] = useState(false);
  const [hasSucceededOnce, setHasSucceededOnce] = useState(false);
  const [stale, setStale] = useState(false);
  const pollRef = useRef(null);

  const fetchDiagnostics = useCallback(async () => {
    if (!adminToken) return;
    setLoading(true);
    try {
      const res = await adminGetDiagnostics(adminToken);
      const cf = res?.data?.cf_access || {};
      setActive(Boolean(cf.break_glass_active));
      setSource(cf.break_glass_source || null);
      setHasSucceededOnce(true);
      setStale(false);
    } catch {
      // Preserve last-known active state on transient failure so the banner
      // does NOT silently disappear mid-incident. Only flag the data as
      // stale; the visible warning persists until a successful poll
      // explicitly returns break_glass_active=false.
      setStale(true);
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  const handleDisable = useCallback(async () => {
    if (!adminToken || disabling) return;
    const ok = window.confirm(
      'Disable Cloudflare Access break-glass mode now?\n\n'
        + 'This restores Access enforcement on every admin worker. '
        + 'You should also remove CF_ACCESS_BREAK_GLASS from Railway and rotate '
        + 'the Worker break-glass secret so the bypass cannot be re-armed by a restart.',
    );
    if (!ok) return;
    setDisabling(true);
    try {
      const res = await adminDisableBreakGlass(adminToken);
      const cf = res?.data?.cf_access || {};
      setActive(Boolean(cf.break_glass_active));
      setSource(cf.break_glass_source || null);
      setStale(false);
      setHasSucceededOnce(true);
      if (res?.data?.redis_persisted === false) {
        toast.warning(
          'Break-glass disabled in this worker, but the cluster-wide flag failed '
            + 'to persist. Other workers may still be bypassed — check Redis and retry.',
        );
      } else {
        toast.success('Cloudflare Access break-glass disabled.');
      }
    } catch (err) {
      toast.error(
        `Failed to disable break-glass: ${err?.response?.data?.detail || err?.message || 'unknown error'}`,
      );
    } finally {
      setDisabling(false);
    }
  }, [adminToken, disabling]);

  useEffect(() => {
    if (!adminToken) return undefined;
    fetchDiagnostics();
    pollRef.current = setInterval(fetchDiagnostics, POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [adminToken, fetchDiagnostics]);

  // Hide the banner only when (a) we have never seen a successful poll and
  // we have nothing to display, or (b) the most recent successful poll
  // explicitly reported break-glass inactive. Transient diagnostics failures
  // after an active state was observed keep the banner up (with a stale
  // indicator) so an in-progress incident is never silently masked.
  if (!active && (!stale || !hasSucceededOnce)) return null;

  return (
    <div
      role="alert"
      data-testid="break-glass-banner"
      className="flex items-start gap-3 px-4 py-3 border-b border-red-300 bg-red-600 text-white shadow-sm"
    >
      <ShieldAlert size={20} className="flex-shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold leading-snug flex items-center gap-2">
          Cloudflare Access is bypassed — restore enforcement once the incident is over.
          {stale && (
            <span
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-red-800/60 text-red-50"
              data-testid="break-glass-banner-stale"
              title="Diagnostics request failed. Showing last-known state until the next successful poll."
            >
              <AlertTriangle size={10} />
              diagnostics unavailable, retrying
            </span>
          )}
        </p>
        <p className="text-xs text-red-100 mt-0.5">
          Break-glass mode is active
          {source ? ` via ${source}` : ''}. Admin login is currently protected only by the JWT and origin shared
          secret. Disable break-glass as soon as Cloudflare Zero Trust recovers.
        </p>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <button
          type="button"
          onClick={handleDisable}
          disabled={disabling || !active}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-semibold bg-white text-red-700 hover:bg-red-50 disabled:opacity-60 transition"
          data-testid="break-glass-banner-disable"
          title="Persist a Redis-backed force-disable visible to every worker. You must still clear the source env / Worker secret afterwards."
        >
          <ShieldOff size={12} className={disabling ? 'animate-pulse' : ''} />
          {disabling ? 'Disabling…' : 'Disable now'}
        </button>
        <button
          type="button"
          onClick={fetchDiagnostics}
          disabled={loading}
          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-md text-[11px] font-semibold bg-red-700 hover:bg-red-800 disabled:opacity-60 transition"
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
