/**
 * Task #397 — AdminHealth metrics freshness indicator.
 *
 * Static-markup mirror tests for the small "Throttle: live • Heavy: Xs ago"
 * strip that sits directly above the burst tiles on the admin health
 * panel (AdminHealth.jsx ~line 2576). The strip is fed by the `_meta`
 * block on /admin/dashboard/metrics added in Task #396 — backend half of
 * that contract is pinned by
 * `test_meta_freshness_indicator_shape_and_cache_semantics` over in
 * artifacts/syrabit-backend.
 *
 * Pinning here matters because:
 *   • If the strip stops rendering when `metricsMeta` is missing, the
 *     AdminHealth tree still loads and admins won't notice the regression.
 *   • The fmtAgo helper has three boundaries (<60s, <60m, >=60m) that a
 *     refactor could easily flip (e.g. switching from Math.floor to
 *     Math.round or losing a unit suffix).
 *   • The throttle "live" collapse exists specifically so the label
 *     doesn't flicker between "0s ago" / "1s ago" between 1s ticks; a
 *     future tweak could raise/lower the 2s threshold without anyone
 *     noticing until on-call complains.
 *   • The data-testids (metrics-freshness, metrics-freshness-throttle,
 *     and the four per-heavy-tile IDs metrics-freshness-users /
 *     -revenue / -seo / -deps) are the documented hooks for visual
 *     smoke tests + e2e — renaming them silently would break those
 *     harnesses.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';

/**
 * Mirrors the freshness-strip IIFE from AdminHealth.jsx (~line 2576).
 * Keep the formatter, the <2s "live" collapse, and the per-tile labels
 * byte-for-byte identical to production so a divergence here triggers a
 * test failure.
 *
 * `nowS` is injected so tests can pin a deterministic "now" — production
 * uses `Date.now() / 1000` directly.
 */
function MetricsFreshnessStrip({ metricsMeta, nowS }) {
  if (!metricsMeta) return null;
  const fmtAgo = (s) => {
    const n = Math.max(0, Math.floor(s));
    if (n < 1) return 'live';
    if (n < 60) return `${n}s ago`;
    if (n < 3600) return `${Math.floor(n / 60)}m ago`;
    return `${Math.floor(n / 3600)}h ago`;
  };
  const heavyAt = Number(metricsMeta.heavy_cached_at);
  const throttleAt = Number(metricsMeta.throttle_fresh_at);
  const heavyAgeS = Number.isFinite(heavyAt) ? nowS - heavyAt : NaN;
  const throttleAgeS = Number.isFinite(throttleAt) ? nowS - throttleAt : NaN;
  const heavyLabel = Number.isFinite(heavyAgeS) ? fmtAgo(heavyAgeS) : '—';
  const throttleLabel = Number.isFinite(throttleAgeS) && throttleAgeS < 2
    ? 'live'
    : Number.isFinite(throttleAgeS) ? fmtAgo(throttleAgeS) : '—';
  return (
    <div
      className="text-[10px] text-gray-400 flex flex-wrap items-center gap-x-2 gap-y-0.5 px-1"
      data-testid="metrics-freshness"
      title={`heavy_cached_at=${heavyAt} · throttle_fresh_at=${throttleAt}`}
    >
      <span>
        Throttle:{' '}
        <span className="text-gray-600 font-medium" data-testid="metrics-freshness-throttle">
          {throttleLabel}
        </span>
      </span>
      {[
        { label: 'Users',   testid: 'metrics-freshness-users' },
        { label: 'Revenue', testid: 'metrics-freshness-revenue' },
        { label: 'SEO',     testid: 'metrics-freshness-seo' },
        { label: 'Deps',    testid: 'metrics-freshness-deps' },
      ].map(({ label, testid }) => (
        <React.Fragment key={label}>
          <span aria-hidden="true">•</span>
          <span>
            {label}:{' '}
            <span className="text-gray-600 font-medium" data-testid={testid}>
              {heavyLabel}
            </span>
          </span>
        </React.Fragment>
      ))}
    </div>
  );
}

const html = (props) => renderToStaticMarkup(<MetricsFreshnessStrip {...props} />);

describe('Task #397 — AdminHealth metrics freshness strip', () => {
  it('renders nothing when metricsMeta is null (no backend _meta yet)', () => {
    expect(html({ metricsMeta: null, nowS: 1700000000 })).toBe('');
  });

  it('renders nothing when metricsMeta is undefined', () => {
    expect(html({ metricsMeta: undefined, nowS: 1700000000 })).toBe('');
  });

  it('renders the strip with all four heavy tiles + throttle when meta is present', () => {
    const out = html({
      metricsMeta: { heavy_cached_at: 1700000000, throttle_fresh_at: 1700000000 },
      nowS: 1700000000,
    });
    expect(out).toContain('data-testid="metrics-freshness"');
    expect(out).toContain('data-testid="metrics-freshness-throttle"');
    expect(out).toContain('data-testid="metrics-freshness-users"');
    expect(out).toContain('data-testid="metrics-freshness-revenue"');
    expect(out).toContain('data-testid="metrics-freshness-seo"');
    expect(out).toContain('data-testid="metrics-freshness-deps"');
    expect(out).toContain('Throttle:');
    expect(out).toContain('Users:');
    expect(out).toContain('Revenue:');
    expect(out).toContain('SEO:');
    expect(out).toContain('Deps:');
  });

  it('exposes the raw timestamps in the title attribute for hover-debugging', () => {
    const out = html({
      metricsMeta: { heavy_cached_at: 1700000000, throttle_fresh_at: 1700000123 },
      nowS: 1700000123,
    });
    expect(out).toContain('title="heavy_cached_at=1700000000 · throttle_fresh_at=1700000123"');
  });

  // ---------- fmtAgo boundaries ----------

  it('formats sub-second heavy ages as "live"', () => {
    const out = html({
      metricsMeta: { heavy_cached_at: 1700000000.6, throttle_fresh_at: 1700000000 },
      nowS: 1700000000.9,
    });
    // heavy age = 0.3s -> floor -> 0 -> "live"
    const heavyMatch = out.match(/data-testid="metrics-freshness-users"[^>]*>([^<]+)</);
    expect(heavyMatch).not.toBeNull();
    expect(heavyMatch[1]).toBe('live');
  });

  it('formats heavy ages of 1s..59s as "Ns ago"', () => {
    for (const age of [1, 14, 59]) {
      const out = html({
        metricsMeta: { heavy_cached_at: 1700000000, throttle_fresh_at: 1700000000 },
        nowS: 1700000000 + age,
      });
      const m = out.match(/data-testid="metrics-freshness-users"[^>]*>([^<]+)</);
      expect(m).not.toBeNull();
      expect(m[1]).toBe(`${age}s ago`);
    }
  });

  it('rolls over to "Nm ago" exactly at 60s', () => {
    const out = html({
      metricsMeta: { heavy_cached_at: 1700000000, throttle_fresh_at: 1700000000 },
      nowS: 1700000000 + 60,
    });
    const m = out.match(/data-testid="metrics-freshness-users"[^>]*>([^<]+)</);
    expect(m[1]).toBe('1m ago');
  });

  it('formats heavy ages between 60s and <3600s as "Nm ago"', () => {
    const cases = [
      [60, '1m ago'],
      [119, '1m ago'],
      [120, '2m ago'],
      [3599, '59m ago'],
    ];
    for (const [age, expected] of cases) {
      const out = html({
        metricsMeta: { heavy_cached_at: 1700000000, throttle_fresh_at: 1700000000 },
        nowS: 1700000000 + age,
      });
      const m = out.match(/data-testid="metrics-freshness-deps"[^>]*>([^<]+)</);
      expect(m[1]).toBe(expected);
    }
  });

  it('rolls over to "Nh ago" exactly at 3600s and beyond', () => {
    const cases = [
      [3600, '1h ago'],
      [7199, '1h ago'],
      [7200, '2h ago'],
    ];
    for (const [age, expected] of cases) {
      const out = html({
        metricsMeta: { heavy_cached_at: 1700000000, throttle_fresh_at: 1700000000 },
        nowS: 1700000000 + age,
      });
      const m = out.match(/data-testid="metrics-freshness-revenue"[^>]*>([^<]+)</);
      expect(m[1]).toBe(expected);
    }
  });

  it('clamps negative ages (clock skew) to "live" rather than "-3s ago"', () => {
    const out = html({
      metricsMeta: { heavy_cached_at: 1700000010, throttle_fresh_at: 1700000010 },
      nowS: 1700000000, // now is *before* the cache stamp
    });
    // Math.max(0, ...) inside fmtAgo guarantees we never render a negative.
    const throttleM = out.match(/data-testid="metrics-freshness-throttle"[^>]*>([^<]+)</);
    const seoM = out.match(/data-testid="metrics-freshness-seo"[^>]*>([^<]+)</);
    expect(throttleM[1]).toBe('live');
    expect(seoM[1]).toBe('live');
    // Make sure no visible "-3s ago" leaked through (the label spans
    // are the only place a stray minus would surface — title attr +
    // CSS class names legitimately contain hyphens).
    for (const id of ['throttle', 'users', 'revenue', 'seo', 'deps']) {
      const m = out.match(new RegExp(`data-testid="metrics-freshness-${id}"[^>]*>([^<]+)<`));
      expect(m[1]).not.toMatch(/-/);
    }
  });

  it('shows "—" for the heavy label when heavy_cached_at is missing/non-numeric', () => {
    // `Number(metricsMeta.heavy_cached_at)` is the production coercion,
    // so we test the values that actually become NaN under it: a missing
    // field (undefined), an unparseable string, and an explicit NaN.
    // (Note: `Number(null) === 0`, which is finite — the backend never
    // sends null for these fields, but we don't pretend to swallow it.)
    const cases = [
      { heavy_cached_at: undefined, throttle_fresh_at: 1700000000 },
      { heavy_cached_at: 'not-a-number', throttle_fresh_at: 1700000000 },
      { heavy_cached_at: NaN, throttle_fresh_at: 1700000000 },
      { /* field omitted entirely */ throttle_fresh_at: 1700000000 },
    ];
    for (const meta of cases) {
      const out = html({ metricsMeta: meta, nowS: 1700000000 });
      const m = out.match(/data-testid="metrics-freshness-users"[^>]*>([^<]+)</);
      expect(m).not.toBeNull();
      expect(m[1]).toBe('—');
    }
  });

  // ---------- Throttle "live" collapse ----------

  it('collapses throttle ages strictly under 2s to "live" (anti-flicker)', () => {
    for (const age of [0, 0.4, 1, 1.999]) {
      const out = html({
        metricsMeta: { heavy_cached_at: 1700000000, throttle_fresh_at: 1700000000 },
        nowS: 1700000000 + age,
      });
      const m = out.match(/data-testid="metrics-freshness-throttle"[^>]*>([^<]+)</);
      expect(m).not.toBeNull();
      expect(m[1]).toBe('live');
    }
  });

  it('starts ticking the throttle label exactly at 2s', () => {
    const out = html({
      metricsMeta: { heavy_cached_at: 1700000000, throttle_fresh_at: 1700000000 },
      nowS: 1700000002,
    });
    const m = out.match(/data-testid="metrics-freshness-throttle"[^>]*>([^<]+)</);
    expect(m[1]).toBe('2s ago');
  });

  it('formats throttle ages above 2s using the same fmtAgo unit ladder', () => {
    const cases = [
      [3, '3s ago'],
      [60, '1m ago'],
      [3600, '1h ago'],
    ];
    for (const [age, expected] of cases) {
      const out = html({
        metricsMeta: { heavy_cached_at: 1700000000, throttle_fresh_at: 1700000000 },
        nowS: 1700000000 + age,
      });
      const m = out.match(/data-testid="metrics-freshness-throttle"[^>]*>([^<]+)</);
      expect(m[1]).toBe(expected);
    }
  });

  it('shows "—" for the throttle label when throttle_fresh_at is missing/non-numeric', () => {
    // Same coercion rules as the heavy case above — `Number(null) === 0`
    // is finite, so the cases that actually surface "—" are the
    // genuinely-missing / unparseable ones.
    const cases = [
      { heavy_cached_at: 1700000000, throttle_fresh_at: undefined },
      { heavy_cached_at: 1700000000, throttle_fresh_at: 'nope' },
      { heavy_cached_at: 1700000000, throttle_fresh_at: NaN },
      { heavy_cached_at: 1700000000 /* throttle_fresh_at omitted */ },
    ];
    for (const meta of cases) {
      const out = html({ metricsMeta: meta, nowS: 1700000000 });
      const m = out.match(/data-testid="metrics-freshness-throttle"[^>]*>([^<]+)</);
      expect(m[1]).toBe('—');
    }
  });

  it('keeps all four heavy tiles in lock-step (single shared cache stamp)', () => {
    // Heavy block is computed + cached as one chunk on the backend
    // (cms_sarvam_health.admin_dashboard_metrics), so the four labels
    // MUST always show the same age. A future split into per-collection
    // TTLs is fine, but should be a deliberate change — pin it here.
    const out = html({
      metricsMeta: { heavy_cached_at: 1700000000, throttle_fresh_at: 1700000000 },
      nowS: 1700000000 + 17,
    });
    const ids = ['users', 'revenue', 'seo', 'deps'];
    const labels = ids.map((id) => {
      const m = out.match(new RegExp(`data-testid="metrics-freshness-${id}"[^>]*>([^<]+)<`));
      return m && m[1];
    });
    expect(labels).toEqual(['17s ago', '17s ago', '17s ago', '17s ago']);
  });
});
