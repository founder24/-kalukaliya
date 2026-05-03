/**
 * Task #315 — R2 cold-storage health tile.
 *
 * The Task #314 worker watchdog (workers/edge-proxy/src/r2-storage-
 * class-alert.ts) writes its last-evaluated snapshot to KV under
 * `r2_storage_class_alert:state` once a month. The backend proxies
 * that snapshot via `/admin/r2-storage-health`; this panel renders it
 * alongside the existing KV / bot-cache tiles so an operator can
 * confirm the lifecycle rules are still working between cron ticks
 * without opening the Cloudflare dashboard.
 *
 * The "Re-evaluate now" button POSTs to /admin/r2-storage-health/run
 * which proxies through to the worker's POST endpoint. The worker
 * enforces a short cooldown so the button can't be spammed past the
 * 28-day per-alert cooldown anchor that protects against duplicate
 * paging.
 *
 * Props:
 *   - r2Health:   `null` while loading, otherwise the response from
 *                 /admin/r2-storage-health. Shape:
 *                   { configured, disabled?, buckets?, logpush_cap_gb?,
 *                     rules_applied_at?, rules_age_days?,
 *                     state: { last_evaluated_at, ia_share_last_fired_at,
 *                              logpush_last_fired_at, last_ia_share,
 *                              last_total_gb, last_logpush_gb }, reason? }
 *   - onReevaluate:  async () => void — triggers the POST. Caller is
 *                    responsible for refreshing `r2Health` afterwards.
 *   - reevaluating:  boolean — disables the button + shows a spinner.
 */
import React from 'react';
import { RefreshCw, AlertTriangle } from 'lucide-react';

const IA_SHARE_GRACE_DAYS = 30;

function fmtGb(v) {
  if (v == null || !Number.isFinite(v)) return '—';
  if (v < 0.01) return `${(v * 1024).toFixed(1)} MB`;
  return `${v.toFixed(2)} GB`;
}

function fmtPct(v) {
  if (v == null || !Number.isFinite(v)) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

function fmtRelative(iso) {
  if (!iso) return 'never';
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return 'never';
  const diff = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export default function R2ColdStoragePanel({ r2Health, onReevaluate, reevaluating }) {
  return (
    <div
      className="mb-3 pb-3 border-b border-gray-200"
      data-testid="notif-prefs-r2-cold-storage"
    >
      <div className="flex items-center justify-between mb-1.5">
        <label className="text-[10px] text-gray-500 font-medium">
          R2 cold storage — lifecycle health
        </label>
        <button
          type="button"
          onClick={onReevaluate}
          disabled={reevaluating || r2Health === null || r2Health?.configured === false}
          className="text-[10px] px-2 py-0.5 rounded ring-1 ring-gray-200 bg-white text-gray-600 hover:bg-gray-50 disabled:opacity-50 inline-flex items-center gap-1"
          data-testid="r2-cold-storage-reevaluate"
          title="Re-run the R2 lifecycle / Logpush watchdog now"
        >
          <RefreshCw size={10} className={reevaluating ? 'animate-spin' : ''} />
          Re-evaluate now
        </button>
      </div>

      {r2Health === null ? (
        <div className="text-[10px] text-gray-400">Loading…</div>
      ) : r2Health.configured === false ? (
        <div
          className="text-[10px] text-gray-400"
          data-testid="r2-cold-storage-unconfigured"
        >
          R2 cold-storage telemetry not available
          {r2Health.reason ? ` — ${r2Health.reason}` : ''}.
          The monthly lifecycle / Logpush watchdog will populate this
          tile once the edge worker is wired up.
        </div>
      ) : (
        <Body
          health={r2Health}
        />
      )}
    </div>
  );
}

function Body({ health }) {
  const state = health.state || {};
  const iaShare = state.last_ia_share;
  const totalGb = state.last_total_gb;
  const logpushGb = state.last_logpush_gb;
  const cap = health.logpush_cap_gb ?? 5;
  const rulesAge = health.rules_age_days;
  const lastEvaluatedAt = state.last_evaluated_at;
  const iaFiredAt = state.ia_share_last_fired_at;
  const logpushFiredAt = state.logpush_last_fired_at;

  const iaWithinGrace =
    rulesAge != null && rulesAge < IA_SHARE_GRACE_DAYS;
  // Mirrors the alert module's `iaShare === 0 && totalBytes >= 1GB &&
  // ageDays >= 30` condition. We badge "warning" here when the same
  // condition holds even if the cooldown has suppressed a fresh page,
  // so the operator sees the underlying problem on the dashboard.
  const iaWarn =
    iaShare === 0 &&
    totalGb != null &&
    totalGb >= 1 &&
    rulesAge != null &&
    rulesAge >= IA_SHARE_GRACE_DAYS;
  const iaBadgeCls = iaWarn
    ? 'bg-red-100 text-red-700 ring-red-200'
    : iaWithinGrace
      ? 'bg-gray-100 text-gray-500 ring-gray-200'
      : 'bg-emerald-100 text-emerald-700 ring-emerald-200';
  const iaBadgeLabel = iaWarn ? 'STUCK' : iaWithinGrace ? 'WARMING' : 'HEALTHY';

  const logpushWarn = logpushGb != null && logpushGb > cap;
  const logpushBadgeCls = logpushWarn
    ? 'bg-amber-100 text-amber-700 ring-amber-200'
    : logpushGb == null
      ? 'bg-gray-100 text-gray-500 ring-gray-200'
      : 'bg-emerald-100 text-emerald-700 ring-emerald-200';
  const logpushBadgeLabel = logpushWarn
    ? 'OVER CAP'
    : logpushGb == null
      ? 'NO DATA'
      : 'UNDER CAP';

  return (
    <div
      className="rounded-md ring-1 ring-gray-200 bg-white p-2 space-y-2"
      data-testid="r2-cold-storage-panel"
    >
      <div className="grid grid-cols-2 gap-2">
        <Tile
          label="IA share"
          value={fmtPct(iaShare)}
          sub={
            totalGb != null
              ? `of ${fmtGb(totalGb)} total`
              : 'no snapshot yet'
          }
          badge={iaBadgeLabel}
          badgeCls={iaBadgeCls}
          testId="r2-cold-storage-ia-share"
        />
        <Tile
          label="Logpush prefix"
          value={fmtGb(logpushGb)}
          sub={`cap ${cap} GB`}
          badge={logpushBadgeLabel}
          badgeCls={logpushBadgeCls}
          testId="r2-cold-storage-logpush"
        />
      </div>

      <div className="grid grid-cols-2 gap-2 text-[10px] text-gray-500">
        <div data-testid="r2-cold-storage-rules-age">
          <div className="uppercase text-[9px] text-gray-400">Rules age</div>
          <div className="tabular-nums text-gray-700">
            {rulesAge == null ? 'unset' : `${rulesAge}d`}
            {rulesAge != null && rulesAge < IA_SHARE_GRACE_DAYS && (
              <span className="text-[9px] text-gray-400 ml-1">
                (grace until {IA_SHARE_GRACE_DAYS}d)
              </span>
            )}
          </div>
        </div>
        <div data-testid="r2-cold-storage-last-evaluated">
          <div className="uppercase text-[9px] text-gray-400">Last evaluated</div>
          <div className="tabular-nums text-gray-700">{fmtRelative(lastEvaluatedAt)}</div>
        </div>
      </div>

      {(iaFiredAt || logpushFiredAt) && (
        <div
          className="text-[10px] text-amber-700 bg-amber-50 ring-1 ring-amber-200 rounded px-2 py-1"
          data-testid="r2-cold-storage-last-fired"
        >
          <div className="flex items-start gap-1.5">
            <AlertTriangle size={11} className="mt-0.5 shrink-0" />
            <div>
              {iaFiredAt && (
                <div>
                  IA-share alert last fired{' '}
                  <span className="font-mono">{fmtRelative(iaFiredAt)}</span>
                  {' '}({new Date(iaFiredAt).toLocaleString()})
                </div>
              )}
              {logpushFiredAt && (
                <div>
                  Logpush-cap alert last fired{' '}
                  <span className="font-mono">{fmtRelative(logpushFiredAt)}</span>
                  {' '}({new Date(logpushFiredAt).toLocaleString()})
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {health.disabled && (
        <div
          className="text-[10px] text-gray-500"
          data-testid="r2-cold-storage-disabled"
        >
          Watchdog disabled via <code className="font-mono">R2_STORAGE_ALERT_DISABLED</code>.
        </div>
      )}

      {Array.isArray(health.buckets) && health.buckets.length > 0 && (
        <div className="text-[10px] text-gray-400">
          Buckets: <span className="font-mono">{health.buckets.join(', ')}</span>
        </div>
      )}
    </div>
  );
}

function Tile({ label, value, sub, badge, badgeCls, testId }) {
  return (
    <div
      className="rounded ring-1 ring-gray-100 bg-gray-50 px-2 py-1.5"
      data-testid={testId}
    >
      <div className="flex items-center justify-between">
        <span className="uppercase text-[9px] text-gray-400">{label}</span>
        <span
          className={`text-[9px] uppercase tracking-wide font-semibold px-1 py-0.5 rounded ring-1 ${badgeCls}`}
        >
          {badge}
        </span>
      </div>
      <div className="text-lg font-semibold tabular-nums text-gray-800 leading-tight mt-0.5">
        {value}
      </div>
      <div className="text-[10px] text-gray-500">{sub}</div>
    </div>
  );
}
