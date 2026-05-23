/**
 * Task #940 — unit tests for the EntitySeoTab admin panel.
 *
 * Locks down three behaviours:
 *
 *   1. Renders the healthy aggregate state with all five signal cards.
 *   2. Surfaces missing Wikidata claims with deep-link buttons that
 *      point at the per-property edit URL the backend provided.
 *   3. Renders the regression list when the latest snapshot's drift
 *      payload contains regressions (so the panel actually shows the
 *      operator something to triage, instead of a green pill lying).
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock('../../../utils/api.jsx', () => ({
  adminEntitySeoStatus: vi.fn(),
  adminEntitySeoRefresh: vi.fn(),
  adminEntitySeoHistory: vi.fn(),
}));

import * as api from '../../../utils/api.jsx';
import EntitySeoTab from './EntitySeoTab';

const HEALTHY_PAYLOAD = {
  configured: true,
  snapshot: {
    generated_at: '2026-04-26T04:30:00.000Z',
    iso_week: '2026-W17',
    aggregate_status: 'ok',
    signals: {
      wikidata:  { status: 'ok', summary: 'Syrabit.ai (Q42) — 7 claims, 0 desired claims missing.',
                   fields: { qid: 'Q42', claim_count: 7, edit_url: 'https://www.wikidata.org/wiki/Q42' } },
      wikipedia: { status: 'ok', summary: 'Article live: Syrabit.ai',
                   fields: { title: 'Syrabit.ai', page_url: 'https://en.wikipedia.org/wiki/Syrabit.ai' } },
      crunchbase:{ status: 'ok', summary: 'Crunchbase reachable.',
                   fields: { permalink: 'syrabit-ai', completeness_pct: 100,
                             page_url: 'https://www.crunchbase.com/organization/syrabit-ai' } },
      sameas:    { status: 'ok', summary: 'All 7 verified profiles live.',
                   fields: { total: 7, broken: [] } },
      google_kg: { status: 'ok', summary: 'Panel entry: Syrabit.ai.',
                   fields: { configured: true, name: 'Syrabit.ai' } },
    },
    summary: { wikidata_claims: 7, wikidata_missing: 0, sameas_broken: 0 },
    missing_claims: [],
  },
  drift: { hadBaseline: true, regressions: [], improvements: [],
           summaryDeltas: {
             wikidata_claims:  { current: 7, previous: 7, delta: 0 },
             wikidata_missing: { current: 0, previous: 0, delta: 0 },
             sameas_broken:    { current: 0, previous: 0, delta: 0 },
           } },
  missingClaims: [],
};

describe('EntitySeoTab', () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it('renders the healthy aggregate state with all five signal cards', async () => {
    api.adminEntitySeoStatus.mockResolvedValue({ data: HEALTHY_PAYLOAD });
    render(<EntitySeoTab adminToken="tok" />);
    await waitFor(() => expect(screen.getByTestId('entity-seo-tab')).toBeInTheDocument());
    // Healthy aggregate pill rendered (one in the header + one per
    // healthy signal card; all five "ok" pills together = 6 instances,
    // so just assert the count is non-zero rather than uniqueness).
    expect(screen.getAllByTestId('entity-status-pill-ok').length).toBeGreaterThan(0);
    // All five signal cards present.
    for (const name of ['wikidata', 'wikipedia', 'crunchbase', 'sameas', 'google_kg']) {
      expect(screen.getByTestId(`entity-signal-${name}`)).toBeInTheDocument();
    }
    // No claims to file → empty-state copy rendered.
    expect(screen.getByTestId('entity-missing-claims-empty')).toBeInTheDocument();
    expect(api.adminEntitySeoStatus).toHaveBeenCalledWith('tok');
  });

  it('renders missing claims with deep-link buttons', async () => {
    api.adminEntitySeoStatus.mockResolvedValue({ data: {
      ...HEALTHY_PAYLOAD,
      missingClaims: [
        { prop: 'P131', label: 'located in', expected: 'Q1',
          edit_url: 'https://www.wikidata.org/wiki/Q42#P131' },
        { prop: 'P112', label: 'founder', expected: '',
          edit_url: 'https://www.wikidata.org/wiki/Q42#P112' },
      ],
    }});
    render(<EntitySeoTab adminToken="tok" />);
    const link131 = await screen.findByTestId('entity-missing-claim-P131');
    expect(link131).toHaveAttribute('href', 'https://www.wikidata.org/wiki/Q42#P131');
    expect(link131).toHaveAttribute('target', '_blank');
    expect(screen.getByTestId('entity-missing-claim-P112')).toBeInTheDocument();
  });

  it('renders missing mention opportunities with deep-link buttons', async () => {
    api.adminEntitySeoStatus.mockResolvedValue({ data: {
      ...HEALTHY_PAYLOAD,
      missingMentions: [
        { id: 'wikipedia_education_in_assam',
          label: 'Wikipedia — Education in Assam',
          url: 'https://en.wikipedia.org/wiki/Education_in_Assam',
          expected_term: 'Syrabit', status: 'missing', mentioned: false,
          summary: 'No mention of "Syrabit" found.' },
        { id: 'wikipedia_education_in_guwahati',
          label: 'Wikipedia — Education in Guwahati',
          url: 'https://en.wikipedia.org/wiki/Guwahati',
          expected_term: 'Syrabit', status: 'missing', mentioned: false,
          summary: 'No mention of "Syrabit" found.' },
      ],
    }});
    render(<EntitySeoTab adminToken="tok" />);
    const list = await screen.findByTestId('entity-missing-mentions');
    expect(list).toHaveTextContent('2 mention opportunities');
    expect(list).toHaveTextContent('Education in Assam');
    const link = screen.getByTestId('entity-missing-mention-wikipedia_education_in_assam');
    expect(link).toHaveAttribute('href', 'https://en.wikipedia.org/wiki/Education_in_Assam');
    expect(link).toHaveAttribute('target', '_blank');
    // The mentions signal card is rendered alongside the other signals.
    expect(screen.getByTestId('entity-signal-mentions')).toBeInTheDocument();
  });

  it('surfaces regressions when the snapshot drift contains them', async () => {
    api.adminEntitySeoStatus.mockResolvedValue({ data: {
      ...HEALTHY_PAYLOAD,
      snapshot: { ...HEALTHY_PAYLOAD.snapshot, aggregate_status: 'degraded' },
      drift: {
        hadBaseline: true,
        regressions: [
          { name: 'wikidata', from: 'ok', to: 'missing',
            summary: 'Wikidata entity Q42 not found (deleted?).' },
        ],
        improvements: [],
        summaryDeltas: HEALTHY_PAYLOAD.drift.summaryDeltas,
      },
    }});
    render(<EntitySeoTab adminToken="tok" />);
    const regressions = await screen.findByTestId('entity-regressions');
    expect(regressions).toHaveTextContent('1 signal regression');
    expect(regressions).toHaveTextContent('wikidata');
  });
});
