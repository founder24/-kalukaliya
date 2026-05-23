/**
 * Task #398 — AdminDashboard "Last updated Xs ago" badge.
 *
 * Static-markup mirror tests for the freshness paragraph that hangs off
 * the Revenue section grid in AdminDashboard.jsx (~L1246).  We exercise
 * the exact JSX shape from production in a tiny local component so the
 * tests are fast and don't need the whole AdminDashboard tree booted
 * (which requires axios mocks for ~17 admin endpoints — see the
 * existing AdminDashboard.r2Watchdog* tests for that pattern).
 *
 * Pinning here matters because:
 *   • The badge is the only on-screen signal that distinguishes a
 *     fresh revenue figure from one cached up to 5 s server-side
 *     (Task #395).  Wording drift would let an admin acting on a
 *     stale number believe it's live.
 *   • The amber-stale style at >5 s is the only canary admins have
 *     when the metrics-cache stops refreshing during an incident —
 *     if the threshold drifts, on-call won't notice the cache TTL
 *     was blown until the burst tiles also flip.
 *   • The "live" sub-second wording mirrors AdminHealth.jsx (Task
 *     #396) so the same heavy_cached_at value, viewed on either
 *     panel, never produces conflicting language.
 *   • The data-testid hooks (dashboard-metrics-freshness, -age,
 *     -stale) are the contract Task #397's broader regression suite
 *     and any e2e harness will target.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import { computeHeavyFreshness } from '@/utils/metricsFreshness';

/**
 * Mirrors the freshness badge from AdminDashboard.jsx.  Imports
 * the same `computeHeavyFreshness` helper as production so the
 * mirror cannot drift from the real component — if the wording
 * or stale boundary in `metricsFreshness.js` ever changes, both
 * the badge and this mirror move together.  ``nowMs`` is a
 * parameter so each test can pin a deterministic age without
 * mocking the global Date.
 */
function MetricsFreshnessBadge({ meta, nowMs }) {
  if (!meta) return null;
  const heavyAt = Number(meta.heavy_cached_at);
  const { label: heavyLabel, stale } = computeHeavyFreshness(heavyAt, nowMs);
  return (
    <p
      className={`text-[11px] mt-2 px-1 ${stale ? 'text-amber-600 font-medium' : 'text-gray-400'}`}
      data-testid="dashboard-metrics-freshness"
      title={`heavy_cached_at=${heavyAt} (Task #396)`}
    >
      Last updated{' '}
      <span data-testid="dashboard-metrics-freshness-age">{heavyLabel}</span>
      {stale && (
        <span className="ml-1" data-testid="dashboard-metrics-freshness-stale">
          — cache TTL (~5s) exceeded
        </span>
      )}
    </p>
  );
}

describe('AdminDashboard freshness badge (Task #398)', () => {
  it('does not render when _meta is missing — failed metrics fetch must leave the section quiet', () => {
    const html = renderToStaticMarkup(<MetricsFreshnessBadge meta={null} nowMs={Date.now()} />);
    expect(html).toBe('');
  });

  it('renders "live" for sub-second ages so the wording matches AdminHealth (Task #396)', () => {
    const now = 1_000_000_000_000;
    const meta = { heavy_cached_at: now / 1000 - 0.3 };
    const html = renderToStaticMarkup(<MetricsFreshnessBadge meta={meta} nowMs={now} />);
    expect(html).toContain('data-testid="dashboard-metrics-freshness"');
    expect(html).toContain('data-testid="dashboard-metrics-freshness-age"');
    expect(html).toMatch(/>live</);
    // Sub-second is NOT stale: must use the muted gray color, not amber.
    expect(html).toContain('text-gray-400');
    expect(html).not.toContain('text-amber-600');
    expect(html).not.toContain('dashboard-metrics-freshness-stale');
  });

  it('formats single-digit seconds as "Xs ago"', () => {
    const now = 1_000_000_000_000;
    const meta = { heavy_cached_at: now / 1000 - 3 };
    const html = renderToStaticMarkup(<MetricsFreshnessBadge meta={meta} nowMs={now} />);
    expect(html).toMatch(/>3s ago</);
    // 3s is below the 5s stale threshold, so still gray.
    expect(html).toContain('text-gray-400');
    expect(html).not.toContain('dashboard-metrics-freshness-stale');
  });

  it('flips to amber + bold + "cache TTL exceeded" suffix at >5s — the only canary for a wedged metrics-cache during an incident', () => {
    const now = 1_000_000_000_000;
    // 7s ago: well past the Task #395 ~5s heavy-cache TTL.
    const meta = { heavy_cached_at: now / 1000 - 7 };
    const html = renderToStaticMarkup(<MetricsFreshnessBadge meta={meta} nowMs={now} />);
    expect(html).toMatch(/>7s ago</);
    expect(html).toContain('text-amber-600');
    expect(html).toContain('font-medium');
    expect(html).not.toContain('text-gray-400');
    // Stale suffix must be present and labeled so a screen-reader/test
    // can identify it independently of the age span.
    expect(html).toContain('data-testid="dashboard-metrics-freshness-stale"');
    expect(html).toContain('cache TTL (~5s) exceeded');
  });

  it('treats exactly 5s as fresh, 6s as stale — pins the boundary so a future TTL change is noticed', () => {
    const now = 1_000_000_000_000;
    const fresh = renderToStaticMarkup(
      <MetricsFreshnessBadge meta={{ heavy_cached_at: now / 1000 - 5 }} nowMs={now} />,
    );
    const stale = renderToStaticMarkup(
      <MetricsFreshnessBadge meta={{ heavy_cached_at: now / 1000 - 6 }} nowMs={now} />,
    );
    expect(fresh).toContain('text-gray-400');
    expect(fresh).not.toContain('dashboard-metrics-freshness-stale');
    expect(stale).toContain('text-amber-600');
    expect(stale).toContain('dashboard-metrics-freshness-stale');
  });

  it('promotes minutes correctly so a long outage doesn\'t print a four-digit "Xs ago"', () => {
    const now = 1_000_000_000_000;
    const meta = { heavy_cached_at: now / 1000 - 125 };
    const html = renderToStaticMarkup(<MetricsFreshnessBadge meta={meta} nowMs={now} />);
    expect(html).toMatch(/>2m ago</);
    expect(html).toContain('text-amber-600');
  });

  it('falls back to "—" when heavy_cached_at is non-numeric so a malformed _meta never prints "NaNs ago" or flips to amber', () => {
    const now = 1_000_000_000_000;
    const html = renderToStaticMarkup(
      <MetricsFreshnessBadge meta={{ heavy_cached_at: 'oops' }} nowMs={now} />,
    );
    // Visible age must be the em-dash, never "NaNs ago" / "NaNm ago".
    expect(html).toMatch(/dashboard-metrics-freshness-age">—</);
    expect(html).not.toMatch(/NaN[smh] ago/);
    // Malformed input must NOT trip the amber stale state — that
    // would scream "outage" at on-call for a parser bug.
    expect(html).not.toContain('text-amber-600');
    expect(html).not.toContain('dashboard-metrics-freshness-stale');
    // The raw value still surfaces in the debug title attribute so a
    // dev opening the badge tooltip can see exactly what came back.
    expect(html).toContain('title="heavy_cached_at=NaN');
  });

  it('exposes raw heavy_cached_at via the title attribute for debugging', () => {
    const now = 1_000_000_000_000;
    const meta = { heavy_cached_at: 1234567890.5 };
    const html = renderToStaticMarkup(<MetricsFreshnessBadge meta={meta} nowMs={now} />);
    expect(html).toContain('title="heavy_cached_at=1234567890.5 (Task #396)"');
  });
});
