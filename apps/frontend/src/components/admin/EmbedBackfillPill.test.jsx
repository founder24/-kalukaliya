import React from 'react';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';

// Task #467 — lock down the EmbedBackfillPill (Task #433) so future
// refactors can't silently drop the per-source breakdown or the
// percent-complete number. The tile is the only place admins can see
// whether the remaining workers_ai_custom backfill backlog is
// dominated by Cohere (safe to defer) vs Voyage / (missing) (which
// signal drift). Without these tests a regression that flattens the
// breakdown back into one opaque bucket would only be caught by an
// admin spotting it manually.

const { axiosGet } = vi.hoisted(() => ({ axiosGet: vi.fn() }));

vi.mock('axios', () => ({
  default: { get: axiosGet },
  get: axiosGet,
}));

vi.mock('@/utils/api', () => ({ API_BASE: 'http://test.local' }));

import EmbedBackfillPill from './EmbedBackfillPill';

describe('EmbedBackfillPill', () => {
  beforeEach(() => {
    axiosGet.mockReset();
  });

  it('renders the percent number and a row per remaining_by_source entry, sorted by count desc', async () => {
    axiosGet.mockResolvedValueOnce({
      data: {
        percent: 73.4,
        remaining: 12_850,
        re_embedded: 35_000,
        total_chunks: 47_850,
        remaining_by_source: {
          cohere: 12_000,
          voyage: 800,
          '(missing)': 50,
        },
        throughput: { chunks_per_min: 1234 },
        eta_seconds: 625,
      },
    });

    render(<EmbedBackfillPill adminToken="t" />);

    // Percent number is the headline metric; lock the exact string.
    const pct = await screen.findByTestId('embed-backfill-percent');
    expect(pct).toHaveTextContent('73.4%');

    // Each per-source row must render with the testid the dashboard
    // and any e2e suite depend on, with the chunk count formatted
    // via toLocaleString.
    const cohere = screen.getByTestId('embed-backfill-source-cohere');
    expect(cohere).toHaveTextContent('cohere');
    expect(cohere).toHaveTextContent((12_000).toLocaleString());

    const voyage = screen.getByTestId('embed-backfill-source-voyage');
    expect(voyage).toHaveTextContent('voyage');
    expect(voyage).toHaveTextContent('800');

    const missing = screen.getByTestId('embed-backfill-source-(missing)');
    expect(missing).toHaveTextContent('(missing)');
    expect(missing).toHaveTextContent('50');

    // The list must be sorted by count desc so the worst offender is
    // first — guards against an accidental drop of the `.sort(...)`.
    const list = within(screen.getByTestId('embed-backfill-by-source'))
      .getAllByRole('listitem');
    expect(list.map((li) => li.getAttribute('data-testid'))).toEqual([
      'embed-backfill-source-cohere',
      'embed-backfill-source-voyage',
      'embed-backfill-source-(missing)',
    ]);

    // Pending count + throughput / ETA captions should also surface.
    expect(screen.getByTestId('embed-backfill-remaining'))
      .toHaveTextContent((12_850).toLocaleString());
    expect(screen.getByTestId('embed-backfill-rate'))
      .toHaveTextContent('1,234 chunks/min');
    expect(screen.getByTestId('embed-backfill-eta'))
      .toHaveTextContent('10m');

    // The error slot must stay empty on the happy path.
    expect(screen.queryByTestId('embed-backfill-error')).toBeNull();
  });

  it('shows the "All chunks migrated." fallback when remaining=0 and the breakdown is empty', async () => {
    axiosGet.mockResolvedValueOnce({
      data: {
        percent: 100,
        remaining: 0,
        re_embedded: 47_850,
        total_chunks: 47_850,
        remaining_by_source: {},
      },
    });

    render(<EmbedBackfillPill adminToken="t" />);

    const empty = await screen.findByTestId('embed-backfill-by-source-empty');
    expect(empty).toHaveTextContent('All chunks migrated.');

    // No per-source rows should render.
    expect(
      within(screen.getByTestId('embed-backfill-by-source'))
        .queryAllByRole('listitem'),
    ).toHaveLength(0);

    // Tile flips to the emerald "done" tone when remaining hits 0
    // with a non-zero total — protects the colour mapping too.
    const tile = screen.getByTestId('embed-backfill-tile');
    expect(tile.className).toMatch(/bg-emerald-50/);
  });

  it('shows the "(breakdown unavailable)" fallback when remaining>0 but the backend omits per-source counts', async () => {
    axiosGet.mockResolvedValueOnce({
      data: {
        percent: 12.5,
        remaining: 100,
        re_embedded: 15,
        total_chunks: 115,
        // remaining_by_source intentionally omitted
      },
    });

    render(<EmbedBackfillPill adminToken="t" />);

    const empty = await screen.findByTestId('embed-backfill-by-source-empty');
    expect(empty).toHaveTextContent('(breakdown unavailable)');
  });

  it('renders the error fallback when the progress request fails', async () => {
    axiosGet.mockRejectedValueOnce({
      response: { data: { detail: 'admin token required' } },
    });

    render(<EmbedBackfillPill adminToken="" />);

    const err = await screen.findByTestId('embed-backfill-error');
    expect(err).toHaveTextContent('admin token required');

    // Error mode hides the percent + breakdown blocks entirely so a
    // stale value isn't shown alongside the error.
    expect(screen.queryByTestId('embed-backfill-percent')).toBeNull();
    expect(screen.queryByTestId('embed-backfill-by-source')).toBeNull();
  });

  it('falls back to the axios error message when no response detail is provided', async () => {
    axiosGet.mockRejectedValueOnce(new Error('Network Error'));

    render(<EmbedBackfillPill adminToken="t" />);

    await waitFor(() => {
      expect(screen.getByTestId('embed-backfill-error'))
        .toHaveTextContent('Network Error');
    });
  });
});
