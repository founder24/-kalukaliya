/**
 * Tasks #396 / #398 — shared freshness helpers for the
 * `/admin/dashboard/metrics` `_meta` block.
 *
 * Backend (artifacts/syrabit-backend/routes/cms_sarvam_health.py
 * `admin_dashboard_metrics`) piggybacks
 *   `_meta = {heavy_cached_at, throttle_fresh_at}`
 * (unix-second floats) on every response so the AdminHealth burst
 * tiles (Task #396) and the AdminDashboard revenue/users/SEO badge
 * (Task #398) can both surface "Last updated Xs ago" labels.
 *
 * The two panels MUST agree on:
 *   - sub-second wording ("live")
 *   - the heavy-cache stale boundary (>5s — see _METRICS_CACHE_TTL)
 *   - the throttle-tile "live" collapse threshold (<2s)
 * otherwise the same `heavy_cached_at` value renders as conflicting
 * language on the two panels and an admin troubleshooting an incident
 * has to mentally translate.  Centralising the formatter + thresholds
 * here means a future tuning change updates both panels in lockstep.
 */

/**
 * Heavy-cache stale boundary.  Mirrors `_METRICS_CACHE_TTL` (~5s) in
 * the backend (Task #395).  Above this age the AdminDashboard badge
 * flips to amber so on-call notices when the metrics-cache stops
 * refreshing during an incident.
 */
export const HEAVY_CACHE_TTL_S = 5;

/**
 * Throttle-tile "live" collapse threshold.  Throttle tiles bypass
 * the cache (Task #388), so on a normal poll the age is well under
 * a second; collapsing anything under this bound to "live" stops the
 * AdminHealth label flickering between "0s ago" / "1s ago" / "live"
 * on every 1s render tick.
 */
export const THROTTLE_LIVE_THRESHOLD_S = 2;

/**
 * Shared "Xs ago" formatter for the heavy + throttle freshness
 * labels.  Sub-second renders as `"live"` so the same
 * `heavy_cached_at` value, viewed on either panel, never produces
 * conflicting wording.
 *
 * @param {number} seconds  Age in seconds (will be clamped at 0).
 * @returns {string}  "live" | "Xs ago" | "Xm ago" | "Xh ago"
 */
export function fmtAgo(seconds) {
  const n = Math.max(0, Math.floor(seconds));
  if (n < 1) return 'live';
  if (n < 60) return `${n}s ago`;
  if (n < 3600) return `${Math.floor(n / 60)}m ago`;
  return `${Math.floor(n / 3600)}h ago`;
}

/**
 * Compute the {ageS, label, stale} triple for the heavy half of the
 * `_meta` block.  ``stale`` flips at ``HEAVY_CACHE_TTL_S``.
 *
 * @param {*} metaHeavyAt  Raw `_meta.heavy_cached_at` (unix seconds).
 * @param {number} [nowMs]  Wall-clock in ms; defaults to `Date.now()`.
 *                          Tests pass a fixed value to pin ages
 *                          deterministically without mocking the
 *                          global Date.
 */
export function computeHeavyFreshness(metaHeavyAt, nowMs = Date.now()) {
  const at = Number(metaHeavyAt);
  if (!Number.isFinite(at)) return { ageS: NaN, label: '—', stale: false };
  const ageS = nowMs / 1000 - at;
  return { ageS, label: fmtAgo(ageS), stale: ageS > HEAVY_CACHE_TTL_S };
}

/**
 * Compute the {ageS, label} pair for the throttle half of the
 * `_meta` block.  Sub-``THROTTLE_LIVE_THRESHOLD_S`` ages collapse to
 * `"live"` to avoid 1s-tick flicker on AdminHealth.
 *
 * @param {*} metaThrottleAt  Raw `_meta.throttle_fresh_at` (unix s).
 * @param {number} [nowMs]    Wall-clock in ms; defaults to `Date.now()`.
 */
export function computeThrottleFreshness(metaThrottleAt, nowMs = Date.now()) {
  const at = Number(metaThrottleAt);
  if (!Number.isFinite(at)) return { ageS: NaN, label: '—' };
  const ageS = nowMs / 1000 - at;
  if (ageS < THROTTLE_LIVE_THRESHOLD_S) return { ageS, label: 'live' };
  return { ageS, label: fmtAgo(ageS) };
}
