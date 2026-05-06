import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import AiGatewayGuardrailByModelTile from './AiGatewayGuardrailByModelTile';

// Task #448 — lock down the "models by guardrail block ratio" tile fed
// from /api/admin/cf-health → ai_gateway.guardrail_by_model. Sibling
// of AiGatewayCacheByModelTile.test.jsx — the frontend's job is small
// but high-signal: render the per-model row the backend hands us AND
// show "—" (not 0%) when a row has no guardrail telemetry, so on-call
// doesn't mistake a quiet model for a 0%-blocked outlier.

const baseRow = {
  provider: 'workers_ai',
  model: 'llama-3.3-70b-instruct-fp8-fast',
  allows: 6,
  rewrites: 1,
  blocks: 3,
  samples: 10,
  guardrail_total: 10,
  block_ratio: 0.3,
};

describe('AiGatewayGuardrailByModelTile', () => {
  it('renders one row per model with the backend-provided block ratio', () => {
    render(
      <AiGatewayGuardrailByModelTile
        data={{ enabled: true, guardrail_by_model: [baseRow] }}
      />
    );
    expect(screen.getByTestId('aig-guardrail-by-model-table')).toBeInTheDocument();
    const ratioCell = screen.getByTestId(`aig-guardrail-ratio-${baseRow.model}`);
    expect(ratioCell.textContent).toBe('30%');
  });

  it('renders "—" (not 0%) when a model has no guardrail telemetry', () => {
    // Backend reports block_ratio=null when every sample for this
    // model lacked cf-aig-guardrail-action (e.g. only cache events).
    // The tile must NOT collapse that to 0% — that would paint a
    // quiet model as a "0% blocked" outlier on the dashboard.
    render(
      <AiGatewayGuardrailByModelTile
        data={{
          enabled: true,
          guardrail_by_model: [
            {
              provider: 'vertex',
              model: 'gemini-2.5-flash',
              allows: 0,
              rewrites: 0,
              blocks: 0,
              samples: 1,
              guardrail_total: 0,
              block_ratio: null,
            },
          ],
        }}
      />
    );
    const ratioCell = screen.getByTestId('aig-guardrail-ratio-gemini-2.5-flash');
    expect(ratioCell.textContent).toBe('—');
    expect(ratioCell.textContent).not.toBe('0%');
  });

  it('shows the empty-state copy when the window has no samples yet', () => {
    render(
      <AiGatewayGuardrailByModelTile data={{ enabled: true, guardrail_by_model: [] }} />
    );
    expect(screen.getByTestId('aig-guardrail-by-model-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('aig-guardrail-by-model-table')).not.toBeInTheDocument();
  });

  it('caps the visible row count at 5 ("top models")', () => {
    const rows = Array.from({ length: 8 }, (_, i) => ({
      ...baseRow,
      model: `m-${i}`,
      block_ratio: 1 - i * 0.1,
    }));
    render(
      <AiGatewayGuardrailByModelTile
        data={{ enabled: true, guardrail_by_model: rows }}
      />
    );
    for (let i = 0; i < 5; i += 1) {
      expect(screen.getByTestId(`aig-guardrail-row-m-${i}`)).toBeInTheDocument();
    }
    for (let i = 5; i < 8; i += 1) {
      expect(screen.queryByTestId(`aig-guardrail-row-m-${i}`)).not.toBeInTheDocument();
    }
  });

  it('surfaces the OBS-off state when the backend flag is disabled', () => {
    render(
      <AiGatewayGuardrailByModelTile
        data={{ enabled: false, guardrail_by_model: [] }}
      />
    );
    const flag = screen.getByTestId('aig-guardrail-by-model-flag');
    expect(flag.textContent).toBe('OBS OFF');
  });
});
