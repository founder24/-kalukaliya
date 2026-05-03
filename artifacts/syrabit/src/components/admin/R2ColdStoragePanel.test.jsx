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
});
