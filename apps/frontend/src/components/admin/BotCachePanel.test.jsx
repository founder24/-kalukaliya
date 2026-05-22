/**
 * Task #897 — unit tests for the bot HTML cache hit-rate panel.
 *
 * The panel is purely a function of the `kvHealth.snapshot.bot_cache`
 * payload the parent already loads from `/admin/kv-health`. These
 * tests pin down the four states it can render in (loading, missing,
 * healthy, warning, no-traffic) so a future regression in the
 * threshold, the sparkline path-builder, or the hit_rate fallback
 * arithmetic fails the build instead of silently rendering the wrong
 * colour the next time crawler hit-rate drops.
 */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import BotCachePanel from './BotCachePanel';

function bucketsFromRates(rates) {
  // Bucket timestamps are arbitrary — the panel only renders them
  // inside the SVG <title> tooltip. 100 events per bucket so r maps
  // cleanly to hit / (hit + miss).
  const start = Date.parse('2026-04-25T10:00:00Z');
  return rates.map((r, i) => {
    const ts = new Date(start + i * 5 * 60 * 1000).toISOString();
    if (r === null) return { ts, hit: 0, miss: 0, conditional_304: 0, fallback: 0 };
    const hit = Math.round(r * 100);
    const miss = 100 - hit;
    return { ts, hit, miss, conditional_304: 0, fallback: 0 };
  });
}

function snapshotWith(botCache) {
  return {
    configured: true,
    snapshot: {
      utcDay: '2026-04-25',
      bindings: [],
      bot_cache: botCache,
    },
  };
}

describe('BotCachePanel', () => {
  it('renders a "Loading…" placeholder while kvHealth is null', () => {
    render(<BotCachePanel kvHealth={null} />);
    expect(screen.getByTestId('notif-prefs-bot-cache')).toBeInTheDocument();
    expect(screen.getByText(/Loading/i)).toBeInTheDocument();
    // Neither the panel container nor the rate should mount yet.
    expect(screen.queryByTestId('notif-prefs-bot-cache-panel')).toBeNull();
  });

  it('renders the unavailable copy when bot_cache is missing', () => {
    render(
      <BotCachePanel
        kvHealth={{ configured: true, reason: 'edge returned 503', snapshot: null }}
      />,
    );
    const fallback = screen.getByTestId('notif-prefs-bot-cache-unavailable');
    expect(fallback).toBeInTheDocument();
    expect(fallback.textContent).toMatch(/edge returned 503/);
    expect(screen.queryByTestId('notif-prefs-bot-cache-panel')).toBeNull();
  });

  it('renders a healthy hit rate with the green container, badge, and sparkline', () => {
    render(
      <BotCachePanel
        kvHealth={snapshotWith({
          hit: 950,
          miss: 50,
          conditional_304: 200,
          fallback: 0,
          hit_rate: 0.95,
          buckets: bucketsFromRates([
            0.95, 0.96, 0.94, 0.95, 0.97, 0.95,
            0.93, 0.96, 0.95, 0.94, 0.95, 0.96,
          ]),
        })}
      />,
    );

    const panel = screen.getByTestId('notif-prefs-bot-cache-panel');
    expect(panel.className).toMatch(/bg-emerald-50/);

    const rate = within(panel).getByTestId('notif-prefs-bot-cache-rate');
    expect(rate.textContent).toContain('95.0%');
    expect(rate.className).toMatch(/text-emerald-600/);

    const badge = within(panel).getByTestId('notif-prefs-bot-cache-badge');
    expect(badge.textContent).toBe('HEALTHY');
    expect(badge.className).toMatch(/bg-emerald-100/);

    expect(within(panel).getByTestId('notif-prefs-bot-cache-sparkline')).toBeInTheDocument();
    expect(within(panel).getByTestId('notif-prefs-bot-cache-hit').textContent).toContain('950');
    expect(within(panel).getByTestId('notif-prefs-bot-cache-miss').textContent).toContain('50');
    expect(within(panel).getByTestId('notif-prefs-bot-cache-cond').textContent).toContain('200');
    expect(within(panel).getByTestId('notif-prefs-bot-cache-fallback').textContent).toContain('0');

    // No warning copy when healthy.
    expect(within(panel).queryByTestId('notif-prefs-bot-cache-warning')).toBeNull();
  });

  it('flips to the warning state when hit_rate drops below 60%', () => {
    render(
      <BotCachePanel
        kvHealth={snapshotWith({
          hit: 400,
          miss: 600,
          conditional_304: 30,
          fallback: 25,
          hit_rate: 0.39,
          buckets: bucketsFromRates([
            0.4, 0.45, 0.42, 0.38, 0.41, 0.39,
            0.37, 0.36, 0.4, 0.42, 0.39, 0.38,
          ]),
        })}
      />,
    );

    const panel = screen.getByTestId('notif-prefs-bot-cache-panel');
    expect(panel.className).toMatch(/bg-red-50/);

    const rate = within(panel).getByTestId('notif-prefs-bot-cache-rate');
    expect(rate.textContent).toContain('39.0%');
    expect(rate.className).toMatch(/text-red-600/);

    const badge = within(panel).getByTestId('notif-prefs-bot-cache-badge');
    expect(badge.textContent).toBe('WARNING');
    expect(badge.className).toMatch(/bg-red-100/);

    expect(within(panel).getByTestId('notif-prefs-bot-cache-warning')).toBeInTheDocument();
    // Fallback events should be highlighted in amber when non-zero.
    const fb = within(panel).getByTestId('notif-prefs-bot-cache-fallback');
    expect(fb.textContent).toContain('25');
    expect(fb.querySelector('span:last-child').className).toMatch(/text-amber-700/);
  });

  it('renders the no-traffic placeholder without warning when every bucket is empty', () => {
    render(
      <BotCachePanel
        kvHealth={snapshotWith({
          hit: 0,
          miss: 0,
          conditional_304: 0,
          fallback: 0,
          hit_rate: 0,
          buckets: bucketsFromRates([
            null, null, null, null, null, null,
            null, null, null, null, null, null,
          ]),
        })}
      />,
    );

    const panel = screen.getByTestId('notif-prefs-bot-cache-panel');
    expect(panel.className).toMatch(/bg-gray-50/);

    expect(within(panel).getByTestId('notif-prefs-bot-cache-rate').textContent).toBe('—');
    expect(within(panel).getByTestId('notif-prefs-bot-cache-badge').textContent).toBe('NO TRAFFIC');

    // Empty state must NOT trigger the warning copy — a fresh KV with
    // zero traffic is not a regression worth shouting about.
    expect(within(panel).queryByTestId('notif-prefs-bot-cache-warning')).toBeNull();
  });

  it('falls back to the locally-computed hit_rate when the worker omits the field', () => {
    // Simulates an older worker deploy that didn't include hit_rate in
    // the payload — the panel should still derive the right colour
    // from hit / (hit + miss + fallback) instead of treating the
    // missing field as 0% and unconditionally going red.
    render(
      <BotCachePanel
        kvHealth={snapshotWith({
          hit: 800,
          miss: 200,
          conditional_304: 0,
          fallback: 0,
          // hit_rate intentionally omitted
          buckets: bucketsFromRates([
            0.8, 0.81, 0.79, 0.82, 0.8, 0.78,
            0.81, 0.83, 0.8, 0.79, 0.81, 0.82,
          ]),
        })}
      />,
    );

    const panel = screen.getByTestId('notif-prefs-bot-cache-panel');
    expect(panel.className).toMatch(/bg-emerald-50/);
    expect(within(panel).getByTestId('notif-prefs-bot-cache-rate').textContent).toContain('80.0%');
    expect(within(panel).getByTestId('notif-prefs-bot-cache-badge').textContent).toBe('HEALTHY');
  });

  it('renders the sparkline with one circle per bucket that has traffic', () => {
    render(
      <BotCachePanel
        kvHealth={snapshotWith({
          hit: 700,
          miss: 300,
          conditional_304: 0,
          fallback: 0,
          hit_rate: 0.7,
          buckets: bucketsFromRates([
            0.7, 0.7, null, 0.7, null, 0.7,
            0.7, 0.7, 0.7, 0.7, 0.7, 0.7,
          ]),
        })}
      />,
    );

    const spark = screen.getByTestId('notif-prefs-bot-cache-sparkline');
    // 12 buckets — 2 are no-traffic gaps → 10 plotted points.
    expect(spark.querySelectorAll('circle').length).toBe(10);
    // The threshold reference line is always rendered.
    expect(spark.querySelectorAll('line').length).toBe(1);
    // The polyline path is split across the gaps — at least one M
    // beyond the leading one to prove the path-builder correctly
    // breaks across no-traffic buckets.
    const path = spark.querySelector('path');
    expect(path).not.toBeNull();
    const dAttr = path.getAttribute('d') || '';
    const moveCount = (dAttr.match(/M /g) || []).length;
    expect(moveCount).toBeGreaterThan(1);
  });
});
