/**
 * Task #315 — unit tests for the R2 cold-storage health panel.
 *
 * Covers the four states it can render in: loading, unconfigured,
 * within the 30-day grace window (warming), stuck-rules warning, and
 * Logpush-cap warning. Plus the re-evaluate button click path.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import R2ColdStoragePanel from './R2ColdStoragePanel';

function withState(over = {}) {
  return {
    configured: true,
    disabled: false,
    buckets: ['syrabit-assets', 'syrabit-media'],
    logpush_cap_gb: 5,
    rules_applied_at: '2026-01-01T00:00:00Z',
    rules_age_days: 120,
    state: {
      last_evaluated_at: new Date().toISOString(),
      ia_share_last_fired_at: null,
      logpush_last_fired_at: null,
      last_ia_share: 0.4,
      last_total_gb: 50,
      last_logpush_gb: 1.5,
    },
    ...over,
  };
}

describe('R2ColdStoragePanel', () => {
  it('renders Loading… while r2Health is null', () => {
    render(<R2ColdStoragePanel r2Health={null} onReevaluate={() => {}} reevaluating={false} />);
    expect(screen.getByTestId('notif-prefs-r2-cold-storage')).toBeInTheDocument();
    expect(screen.getByText(/Loading/)).toBeInTheDocument();
    expect(screen.queryByTestId('r2-cold-storage-panel')).toBeNull();
  });

  it('renders the unconfigured placeholder when configured=false', () => {
    render(
      <R2ColdStoragePanel
        r2Health={{ configured: false, reason: 'D1_SYNC_SECRET not set' }}
        onReevaluate={() => {}}
        reevaluating={false}
      />,
    );
    const fallback = screen.getByTestId('r2-cold-storage-unconfigured');
    expect(fallback.textContent).toMatch(/D1_SYNC_SECRET not set/);
    expect(screen.queryByTestId('r2-cold-storage-panel')).toBeNull();
  });

  it('badges IA-share as HEALTHY when share is non-zero past grace', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState()}
        onReevaluate={() => {}}
        reevaluating={false}
      />,
    );
    const tile = screen.getByTestId('r2-cold-storage-ia-share');
    expect(tile.textContent).toMatch(/40\.0%/);
    expect(tile.textContent).toMatch(/HEALTHY/);
  });

  it('badges IA-share as STUCK when 0% past grace with non-trivial total', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState({
          state: {
            last_evaluated_at: new Date().toISOString(),
            ia_share_last_fired_at: new Date().toISOString(),
            logpush_last_fired_at: null,
            last_ia_share: 0,
            last_total_gb: 80,
            last_logpush_gb: 1,
          },
        })}
        onReevaluate={() => {}}
        reevaluating={false}
      />,
    );
    const tile = screen.getByTestId('r2-cold-storage-ia-share');
    expect(tile.textContent).toMatch(/STUCK/);
    expect(screen.getByTestId('r2-cold-storage-last-fired').textContent).toMatch(
      /IA-share alert last fired/,
    );
  });

  it('badges IA-share as WARMING within the 30-day grace window', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState({
          rules_age_days: 10,
          state: {
            last_evaluated_at: new Date().toISOString(),
            ia_share_last_fired_at: null,
            logpush_last_fired_at: null,
            last_ia_share: 0,
            last_total_gb: 80,
            last_logpush_gb: 0,
          },
        })}
        onReevaluate={() => {}}
        reevaluating={false}
      />,
    );
    const tile = screen.getByTestId('r2-cold-storage-ia-share');
    expect(tile.textContent).toMatch(/WARMING/);
  });

  it('badges Logpush as OVER CAP when above the configured cap', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState({
          state: {
            last_evaluated_at: new Date().toISOString(),
            ia_share_last_fired_at: null,
            logpush_last_fired_at: new Date().toISOString(),
            last_ia_share: 0.4,
            last_total_gb: 50,
            last_logpush_gb: 6.5,
          },
        })}
        onReevaluate={() => {}}
        reevaluating={false}
      />,
    );
    const tile = screen.getByTestId('r2-cold-storage-logpush');
    expect(tile.textContent).toMatch(/6\.50 GB/);
    expect(tile.textContent).toMatch(/OVER CAP/);
  });

  it('invokes onReevaluate when the button is clicked', () => {
    const cb = vi.fn();
    render(
      <R2ColdStoragePanel
        r2Health={withState()}
        onReevaluate={cb}
        reevaluating={false}
      />,
    );
    fireEvent.click(screen.getByTestId('r2-cold-storage-reevaluate'));
    expect(cb).toHaveBeenCalledTimes(1);
  });

  // ── Task #319 — watchdog-blind indicator ────────────────────────────
  it('hides the watchdog indicator when consecutive_query_failures is 0', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState({
          query_fail_threshold: 2,
          state: {
            last_evaluated_at: new Date().toISOString(),
            ia_share_last_fired_at: null,
            logpush_last_fired_at: null,
            last_ia_share: 0.4,
            last_total_gb: 50,
            last_logpush_gb: 1.5,
            consecutive_query_failures: 0,
            query_fail_last_fired_at: null,
          },
        })}
        onReevaluate={() => {}}
        reevaluating={false}
      />,
    );
    expect(screen.queryByTestId('r2-cold-storage-watchdog-indicator')).toBeNull();
  });

  it('renders the watchdog indicator amber when below threshold', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState({
          query_fail_threshold: 2,
          state: {
            last_evaluated_at: new Date().toISOString(),
            ia_share_last_fired_at: null,
            logpush_last_fired_at: null,
            last_ia_share: null,
            last_total_gb: null,
            last_logpush_gb: null,
            consecutive_query_failures: 1,
            query_fail_last_fired_at: null,
          },
        })}
        onReevaluate={() => {}}
        reevaluating={false}
      />,
    );
    const ind = screen.getByTestId('r2-cold-storage-watchdog-indicator');
    expect(ind.getAttribute('data-watchdog-state')).toBe('warn');
    expect(ind.getAttribute('data-watchdog-count')).toBe('1');
    expect(ind.getAttribute('data-watchdog-threshold')).toBe('2');
    expect(ind.className).toMatch(/amber/);
    expect(ind.textContent).toMatch(/watchdog 1\/2/i);
    const tip = ind.getAttribute('title') || '';
    expect(tip).toMatch(/1 of 2/);
    expect(tip).toMatch(/1 more failed monthly evaluation will trip/);
    expect(tip).toMatch(/Never fired/);
    expect(tip).toMatch(/Runbook:/);
  });

  it('reports the correct remaining-evaluations count for custom thresholds', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState({
          query_fail_threshold: 4,
          state: {
            last_evaluated_at: new Date().toISOString(),
            ia_share_last_fired_at: null,
            logpush_last_fired_at: null,
            last_ia_share: null,
            last_total_gb: null,
            last_logpush_gb: null,
            consecutive_query_failures: 1,
            query_fail_last_fired_at: null,
          },
        })}
        onReevaluate={() => {}}
        reevaluating={false}
      />,
    );
    const ind = screen.getByTestId('r2-cold-storage-watchdog-indicator');
    expect(ind.getAttribute('data-watchdog-state')).toBe('warn');
    expect(ind.textContent).toMatch(/watchdog 1\/4/i);
    const tip = ind.getAttribute('title') || '';
    expect(tip).toMatch(/1 of 4/);
    expect(tip).toMatch(/3 more failed monthly evaluations will trip/);
  });

  it('renders the watchdog indicator red once the threshold is crossed', () => {
    const fired = '2026-04-15T12:00:00Z';
    render(
      <R2ColdStoragePanel
        r2Health={withState({
          query_fail_threshold: 2,
          state: {
            last_evaluated_at: new Date().toISOString(),
            ia_share_last_fired_at: null,
            logpush_last_fired_at: null,
            last_ia_share: null,
            last_total_gb: null,
            last_logpush_gb: null,
            consecutive_query_failures: 2,
            query_fail_last_fired_at: fired,
          },
        })}
        onReevaluate={() => {}}
        reevaluating={false}
      />,
    );
    const ind = screen.getByTestId('r2-cold-storage-watchdog-indicator');
    expect(ind.getAttribute('data-watchdog-state')).toBe('tripped');
    expect(ind.className).toMatch(/red/);
    expect(ind.textContent).toMatch(/watchdog 2\/2/i);
    const tip = ind.getAttribute('title') || '';
    expect(tip).toMatch(/Watchdog-blind page has fired/);
    expect(tip).toMatch(/Last fired:/);
    expect(ind.getAttribute('href')).toMatch(/cloudflare-monthly-cost-review\.md#step-5/);
  });

  it('falls back to the default threshold when query_fail_threshold is missing', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState({
          state: {
            last_evaluated_at: new Date().toISOString(),
            ia_share_last_fired_at: null,
            logpush_last_fired_at: null,
            last_ia_share: null,
            last_total_gb: null,
            last_logpush_gb: null,
            consecutive_query_failures: 1,
            query_fail_last_fired_at: null,
          },
        })}
        onReevaluate={() => {}}
        reevaluating={false}
      />,
    );
    const ind = screen.getByTestId('r2-cold-storage-watchdog-indicator');
    expect(ind.getAttribute('data-watchdog-threshold')).toBe('2');
  });

  it('hides the watchdog indicator when configured=false', () => {
    render(
      <R2ColdStoragePanel
        r2Health={{ configured: false, reason: 'unset' }}
        onReevaluate={() => {}}
        reevaluating={false}
      />,
    );
    expect(screen.queryByTestId('r2-cold-storage-watchdog-indicator')).toBeNull();
  });

  it('disables the button while reevaluating', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState()}
        onReevaluate={() => {}}
        reevaluating={true}
      />,
    );
    expect(screen.getByTestId('r2-cold-storage-reevaluate')).toBeDisabled();
  });

  // ── Task #322 — inline reset for the watchdog-blind indicator ─────

  it('hides the reset button when the watchdog counter is 0', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState()}
        onReevaluate={() => {}}
        reevaluating={false}
        onResetWatchdog={() => {}}
        resettingWatchdog={false}
      />,
    );
    // Indicator itself is hidden when count=0, so the reset button
    // (which lives inside it) must also be hidden.
    expect(screen.queryByTestId('r2-cold-storage-watchdog-indicator')).toBeNull();
    expect(screen.queryByTestId('r2-cold-storage-watchdog-reset')).toBeNull();
  });

  it('shows the reset button next to the watchdog indicator when count >= 1', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState({
          state: {
            last_evaluated_at: new Date().toISOString(),
            ia_share_last_fired_at: null,
            logpush_last_fired_at: null,
            last_ia_share: null,
            last_total_gb: null,
            last_logpush_gb: null,
            consecutive_query_failures: 1,
            query_fail_last_fired_at: null,
          },
        })}
        onReevaluate={() => {}}
        reevaluating={false}
        onResetWatchdog={() => {}}
        resettingWatchdog={false}
      />,
    );
    expect(screen.getByTestId('r2-cold-storage-watchdog-indicator')).toBeInTheDocument();
    expect(screen.getByTestId('r2-cold-storage-watchdog-reset')).toBeInTheDocument();
  });

  it('omits the reset button when no onResetWatchdog handler is supplied', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState({
          state: {
            last_evaluated_at: new Date().toISOString(),
            ia_share_last_fired_at: null,
            logpush_last_fired_at: null,
            last_ia_share: null,
            last_total_gb: null,
            last_logpush_gb: null,
            consecutive_query_failures: 2,
            query_fail_last_fired_at: '2026-04-15T12:00:00Z',
          },
        })}
        onReevaluate={() => {}}
        reevaluating={false}
      />,
    );
    expect(screen.getByTestId('r2-cold-storage-watchdog-indicator')).toBeInTheDocument();
    expect(screen.queryByTestId('r2-cold-storage-watchdog-reset')).toBeNull();
  });

  it('invokes onResetWatchdog when the reset button is clicked', () => {
    const onResetWatchdog = vi.fn();
    render(
      <R2ColdStoragePanel
        r2Health={withState({
          state: {
            last_evaluated_at: new Date().toISOString(),
            ia_share_last_fired_at: null,
            logpush_last_fired_at: null,
            last_ia_share: null,
            last_total_gb: null,
            last_logpush_gb: null,
            consecutive_query_failures: 2,
            query_fail_last_fired_at: '2026-04-15T12:00:00Z',
          },
        })}
        onReevaluate={() => {}}
        reevaluating={false}
        onResetWatchdog={onResetWatchdog}
        resettingWatchdog={false}
      />,
    );
    fireEvent.click(screen.getByTestId('r2-cold-storage-watchdog-reset'));
    expect(onResetWatchdog).toHaveBeenCalledTimes(1);
  });

  it('disables the reset button while resettingWatchdog is true', () => {
    render(
      <R2ColdStoragePanel
        r2Health={withState({
          state: {
            last_evaluated_at: new Date().toISOString(),
            ia_share_last_fired_at: null,
            logpush_last_fired_at: null,
            last_ia_share: null,
            last_total_gb: null,
            last_logpush_gb: null,
            consecutive_query_failures: 2,
            query_fail_last_fired_at: '2026-04-15T12:00:00Z',
          },
        })}
        onReevaluate={() => {}}
        reevaluating={false}
        onResetWatchdog={() => {}}
        resettingWatchdog={true}
      />,
    );
    const btn = screen.getByTestId('r2-cold-storage-watchdog-reset');
    expect(btn).toBeDisabled();
    expect(btn.textContent).toMatch(/Resetting/);
  });
});
