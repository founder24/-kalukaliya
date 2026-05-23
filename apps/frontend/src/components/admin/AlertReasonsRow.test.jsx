import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import AlertReasonsRow from './AlertReasonsRow';

// Task #680 — the per-reason chip block on review_prompt_reason_ctr_drop
// alerts in AdminDashboard's alert history was previously only validated
// by manual inspection. Lock down the rendering so a future refactor of
// threshold_snapshot rendering can't silently drop the reasons branch.

const sampleReasonsAlert = {
  _id: 'alert-1',
  type: 'review_prompt_reason_ctr_drop',
  acknowledged: false,
  threshold_snapshot: {
    metric: 'ctr_pct',
    value: 5,
    actual: 1.2,
    reasons: [
      { reason: 'too_long', delta_pp: -3.4, prev_ctr_pct: 6.1, curr_ctr_pct: 2.7 },
      { reason: 'off_topic', delta_pp: -2.1, prev_ctr_pct: 4.5, curr_ctr_pct: 2.4 },
    ],
  },
};

describe('AlertReasonsRow', () => {
  it('renders each reason name and its delta_pp for review_prompt_reason_ctr_drop alerts', () => {
    const html = renderToStaticMarkup(
      <AlertReasonsRow
        alert={sampleReasonsAlert}
        alertReasonFilter=""
        onReasonClick={() => {}}
      />
    );
    // The "Reasons:" label and both reason chip names are present.
    expect(html).toContain('Reasons:');
    expect(html).toContain('too_long');
    expect(html).toContain('off_topic');
    // delta_pp is rendered with a sign and one decimal place + " pp".
    expect(html).toContain('(-3.4 pp)');
    expect(html).toContain('(-2.1 pp)');
    // The container is actually emitted (not short-circuited).
    expect(html).toContain('data-testid="alert-reasons-row"');
  });

  it('does NOT render the reasons row for other alert types, even if reasons are present', () => {
    const otherAlert = {
      ...sampleReasonsAlert,
      _id: 'alert-2',
      type: 'seo_health_drop', // any non-reason-CTR alert type
    };
    const html = renderToStaticMarkup(
      <AlertReasonsRow
        alert={otherAlert}
        alertReasonFilter=""
        onReasonClick={() => {}}
      />
    );
    // Component should short-circuit to null — nothing rendered at all.
    expect(html).toBe('');
    expect(html).not.toContain('Reasons:');
    expect(html).not.toContain('too_long');
  });

  it('renders nothing when reasons array is missing or empty', () => {
    const emptyAlert = {
      _id: 'alert-3',
      type: 'review_prompt_reason_ctr_drop',
      acknowledged: false,
      threshold_snapshot: { metric: 'ctr_pct', value: 5, actual: 1.2 },
    };
    const html = renderToStaticMarkup(
      <AlertReasonsRow alert={emptyAlert} alertReasonFilter="" onReasonClick={() => {}} />
    );
    expect(html).toBe('');
  });
});
