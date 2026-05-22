import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import AiGatewayCacheByModelTile from './AiGatewayCacheByModelTile';

// Task #419 — lock down the "top models by cache hit ratio" tile fed
// from /api/admin/cf-health → ai_gateway.cache_by_model. The
// frontend's job is small but high-signal: render the per-model row
// the backend hands us AND show "—" (not 0%) when a row has no cache
// telemetry, so on-call doesn't mistake a quiet model for a 100% miss
// outlier.

const baseRow = {
  provider: 'workers_ai',
  model: 'llama-3.3-70b-instruct-fp8-fast',
  hits: 6,
  misses: 2,
  bypass: 0,
  samples: 8,
  cache_status_total: 8,
  hit_ratio: 0.75,
};

describe('AiGatewayCacheByModelTile', () => {
  it('renders one row per model with the backend-provided hit ratio', () => {
    render(
      <AiGatewayCacheByModelTile
        data={{ enabled: true, cache_by_model: [baseRow] }}
      />
    );
    expect(screen.getByTestId('aig-cache-by-model-table')).toBeInTheDocument();
    const ratioCell = screen.getByTestId(`aig-cache-ratio-${baseRow.model}`);
    expect(ratioCell.textContent).toBe('75%');
  });

  it('renders "—" (not 0%) when a model has no cache telemetry', () => {
    // Backend reports hit_ratio=null when every sample for this
    // model lacked cf-aig-cache-status (e.g. only guardrail events).
    // The tile must NOT collapse that to 0% — that would paint a
    // quiet model as a 100% cache-miss outlier on the dashboard.
    render(
      <AiGatewayCacheByModelTile
        data={{
          enabled: true,
          cache_by_model: [
            {
              provider: 'vertex',
              model: 'gemini-2.5-flash',
              hits: 0,
              misses: 0,
              bypass: 0,
              samples: 1,
              cache_status_total: 0,
              hit_ratio: null,
            },
          ],
        }}
      />
    );
    const ratioCell = screen.getByTestId('aig-cache-ratio-gemini-2.5-flash');
    expect(ratioCell.textContent).toBe('—');
    expect(ratioCell.textContent).not.toBe('0%');
  });

  it('shows the empty-state copy when the window has no samples yet', () => {
    render(
      <AiGatewayCacheByModelTile data={{ enabled: true, cache_by_model: [] }} />
    );
    expect(screen.getByTestId('aig-cache-by-model-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('aig-cache-by-model-table')).not.toBeInTheDocument();
  });

  it('caps the visible row count at 5 ("top models")', () => {
    const rows = Array.from({ length: 8 }, (_, i) => ({
      ...baseRow,
      model: `m-${i}`,
      hit_ratio: 1 - i * 0.1,
    }));
    render(
      <AiGatewayCacheByModelTile
        data={{ enabled: true, cache_by_model: rows }}
      />
    );
    // Rows 0..4 visible, 5..7 not.
    for (let i = 0; i < 5; i += 1) {
      expect(screen.getByTestId(`aig-cache-row-m-${i}`)).toBeInTheDocument();
    }
    for (let i = 5; i < 8; i += 1) {
      expect(screen.queryByTestId(`aig-cache-row-m-${i}`)).not.toBeInTheDocument();
    }
  });

  it('surfaces the OBS-off state when the backend flag is disabled', () => {
    render(
      <AiGatewayCacheByModelTile
        data={{ enabled: false, cache_by_model: [] }}
      />
    );
    const flag = screen.getByTestId('aig-cache-by-model-flag');
    expect(flag.textContent).toBe('OBS OFF');
  });
});
