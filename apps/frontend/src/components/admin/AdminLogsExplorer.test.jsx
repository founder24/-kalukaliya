/**
 * Task #944 — AdminLogsExplorer component tests.
 *
 * The deep DOM/event story is covered by the e2e admin sweep — these
 * tests just lock down the SSR markup so a refactor of the column
 * layout, badge palette, or filter chip wiring trips a fast unit test
 * before reaching staging.
 */
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect, vi } from 'vitest';

vi.mock('@/utils/api', () => ({
  adminLogsList:        vi.fn(() => Promise.resolve({ data: { logs: [], total: 0 } })),
  adminLogsStatus:      vi.fn(() => Promise.resolve({ data: {
    paused: false, ttl_days: 14, ingest_token_configured: true,
    cf_pull_24h: {
      ticks: 5, total_calls: 61, total_subdivisions: 9, total_saturated: 2,
      max_calls: 50, max_subdivisions: 6,
      subdivided_ticks: 2, subdivided_pct: 40.0,
      window_s: 86400, oldest_ts: '2026-04-25T12:00:00+00:00',
      newest_ts: '2026-04-26T12:00:00+00:00',
    },
  } })),
  adminLogsTrace:       vi.fn(() => Promise.resolve({ data: { logs: [] } })),
  adminLogsPause:       vi.fn(),
  adminLogsResume:      vi.fn(),
  adminLogsRotateToken: vi.fn(),
  adminLogsClear:       vi.fn(),
  adminLogsExportUrl:   vi.fn(() => 'https://example.com/export'),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

vi.mock('@/components/ErrorBoundary', () => ({
  SectionErrorBoundary: ({ children }) => <div>{children}</div>,
}));

import AdminLogsExplorer from './AdminLogsExplorer';

describe('AdminLogsExplorer', () => {
  it('renders the title and the source filter chips', () => {
    const html = renderToStaticMarkup(
      <AdminLogsExplorer adminToken="t" />,
    );
    expect(html).toContain('Unified Logs');
    // Source filter chips
    expect(html).toContain('Edge worker');
    expect(html).toContain('Cloudflare GraphQL');
    expect(html).toContain('Backend (FastAPI)');
    expect(html).toContain('Cron / jobs');
  });

  it('renders the level filter chips', () => {
    const html = renderToStaticMarkup(
      <AdminLogsExplorer adminToken="t" />,
    );
    for (const lv of ['debug', 'info', 'warn', 'error']) {
      expect(html.toLowerCase()).toContain(lv);
    }
  });

  it('renders the time-window preset buttons', () => {
    const html = renderToStaticMarkup(
      <AdminLogsExplorer adminToken="t" />,
    );
    for (const lbl of ['Last 15m', 'Last 1h', 'Last 24h', 'Last 7d']) {
      expect(html).toContain(lbl);
    }
  });

  it('renders all the action buttons (refresh, export, pause, clear)', () => {
    const html = renderToStaticMarkup(
      <AdminLogsExplorer adminToken="t" />,
    );
    expect(html).toContain('Refresh');
    expect(html).toContain('Live tail');
    expect(html).toContain('CSV');
    expect(html).toContain('NDJSON');
    expect(html).toContain('Rotate token');
    expect(html).toContain('Clear');
  });

  it('renders the table header columns', () => {
    const html = renderToStaticMarkup(
      <AdminLogsExplorer adminToken="t" />,
    );
    for (const col of ['Time', 'Source', 'Level', 'Status', 'Route',
                       'Country / Colo', 'Cache', 'Duration', 'Correlation']) {
      expect(html).toContain(col);
    }
  });

});

// Task #953 — CF pull cost (24h) widget render.
import { describe as describe3, it as it3, expect as expect3, afterEach as afterEach3, vi as vi3 } from 'vitest';
import { render as render3, screen as screen3, fireEvent as fireEvent3, cleanup as cleanup3, waitFor as waitFor3 } from '@testing-library/react';

describe3('AdminLogsExplorer — CF pull cost (24h) widget', () => {
  afterEach3(() => { cleanup3(); vi3.clearAllMocks(); });

  it3('shows the 24h aggregate inside the safeguards section when the status payload includes cf_pull_24h', async () => {
    const apiMod = await import('@/utils/api');
    apiMod.adminLogsList.mockResolvedValue({ data: { logs: [], total: 0 } });
    apiMod.adminLogsStatus.mockResolvedValue({ data: {
      paused: false, ttl_days: 14, ingest_token_configured: true,
      backend_sample_rate: 0.05, edge_sample_rate: 0.05,
      max_ingest_batch: 500, cf_pull_interval_s: 60,
      cf_pull_24h: {
        ticks: 5, total_calls: 61, total_subdivisions: 9, total_saturated: 2,
        max_calls: 50, max_subdivisions: 6,
        subdivided_ticks: 2, subdivided_pct: 40.0,
        window_s: 86400,
        oldest_ts: '2026-04-25T12:00:00+00:00',
        newest_ts: '2026-04-26T12:00:00+00:00',
      },
    } });

    render3(<AdminLogsExplorer adminToken="t" />);

    // Open the collapsible "Ingest safeguards" section so the widget is in the DOM.
    await waitFor3(() => {
      const safeguards = screen3.queryByText(/Ingest safeguards/i);
      expect3(safeguards).toBeTruthy();
      fireEvent3.click(safeguards);
    });

    const widget = await screen3.findByTestId('cf-pull-cost-widget');
    expect3(widget).toBeTruthy();
    // Totals + worst-tick + paginated-% must all render.
    expect3(widget.textContent).toContain('61 calls');
    expect3(widget.textContent).toContain('9 subdivisions');
    expect3(widget.textContent).toContain('5 ticks aggregated');
    expect3(widget.textContent).toContain('peak: 50 calls');
    expect3(widget.textContent).toContain('40% of ticks paginated');
    // Saturated minutes (data lost) must surface as a warning chip.
    expect3(screen3.getByTestId('cf-pull-cost-saturated').textContent)
      .toMatch(/2 saturated minutes/);
  });

  // Task #960 — inline sparkline rendered next to the totals so a
  // sudden 1→50 calls/tick spike is visible without leaving the
  // safeguards card.
  it3('renders an inline sparkline (with worst-tick highlight + per-point hover) when cf_pull_history_recent is present', async () => {
    const apiMod = await import('@/utils/api');
    apiMod.adminLogsList.mockResolvedValue({ data: { logs: [], total: 0 } });
    const recent = [
      { ts: '2026-04-26T11:55:00+00:00', calls:  1, subdivisions: 0, saturated: 0 },
      { ts: '2026-04-26T11:56:00+00:00', calls:  1, subdivisions: 0, saturated: 0 },
      { ts: '2026-04-26T11:57:00+00:00', calls:  1, subdivisions: 0, saturated: 0 },
      { ts: '2026-04-26T11:58:00+00:00', calls:  8, subdivisions: 3, saturated: 0 },
      { ts: '2026-04-26T11:59:00+00:00', calls: 50, subdivisions: 6, saturated: 2 },
    ];
    apiMod.adminLogsStatus.mockResolvedValue({ data: {
      paused: false, ttl_days: 14, ingest_token_configured: true,
      backend_sample_rate: 0.05, edge_sample_rate: 0.05,
      max_ingest_batch: 500, cf_pull_interval_s: 60,
      cf_pull_24h: {
        ticks: 5, total_calls: 61, total_subdivisions: 9, total_saturated: 2,
        max_calls: 50, max_subdivisions: 6,
        subdivided_ticks: 2, subdivided_pct: 40.0,
        window_s: 300,
        oldest_ts: '2026-04-26T11:55:00+00:00',
        newest_ts: '2026-04-26T11:59:00+00:00',
      },
      cf_pull_history_recent: recent,
    } });

    render3(<AdminLogsExplorer adminToken="t" />);
    await waitFor3(() => {
      const safeguards = screen3.queryByText(/Ingest safeguards/i);
      expect3(safeguards).toBeTruthy();
      fireEvent3.click(safeguards);
    });

    const sparkline = await screen3.findByTestId('cf-pull-cost-sparkline');
    expect3(sparkline).toBeTruthy();
    // Accessibility: aria-label summarises the peak so screen readers
    // (and anyone who can't see the chart) get the same information.
    expect3(sparkline.getAttribute('aria-label')).toMatch(/peak 50 call/);
    expect3(sparkline.getAttribute('aria-label'))
      .toContain('2026-04-26T11:59:00+00:00');
    // The two series — calls (solid) and subdivisions (dashed overlay)
    // — must both render so a slow drift in subdivision depth is also
    // visible, not just call count.
    expect3(screen3.getByTestId('cf-pull-cost-sparkline-calls')).toBeTruthy();
    expect3(screen3.getByTestId('cf-pull-cost-sparkline-subs')).toBeTruthy();
    // The worst-tick datapoint is marked so the operator can see WHEN
    // the spike happened, not just THAT it happened.
    const worst = screen3.getByTestId('cf-pull-cost-sparkline-worst');
    expect3(worst).toBeTruthy();
    // Per-point hover tooltip exposes the timestamp + values.
    const worstTitle = worst.querySelector('title');
    expect3(worstTitle).toBeTruthy();
    expect3(worstTitle.textContent).toContain('2026-04-26T11:59:00+00:00');
    expect3(worstTitle.textContent).toContain('50 calls');
    expect3(worstTitle.textContent).toContain('6 subdivisions');
    // Screen-reader fallback so accessibility doesn't depend on hover.
    expect3(screen3.getByTestId('cf-pull-cost-sparkline-sr').textContent)
      .toMatch(/Worst tick at 2026-04-26T11:59:00\+00:00.*50 calls/);
  });

  it3('omits the sparkline when there is too little history to plot a trend', async () => {
    const apiMod = await import('@/utils/api');
    apiMod.adminLogsList.mockResolvedValue({ data: { logs: [], total: 0 } });
    apiMod.adminLogsStatus.mockResolvedValue({ data: {
      paused: false, ttl_days: 14, ingest_token_configured: true,
      backend_sample_rate: 0.05, edge_sample_rate: 0.05,
      max_ingest_batch: 500, cf_pull_interval_s: 60,
      cf_pull_24h: {
        ticks: 1, total_calls: 1, total_subdivisions: 0, total_saturated: 0,
        max_calls: 1, max_subdivisions: 0,
        subdivided_ticks: 0, subdivided_pct: 0.0,
        window_s: 0,
        oldest_ts: '2026-04-26T11:59:00+00:00',
        newest_ts: '2026-04-26T11:59:00+00:00',
      },
      // Single datapoint — no trend to draw, so the sparkline must
      // hide entirely (the totals row alone is the right surface for
      // a one-tick deploy).
      cf_pull_history_recent: [
        { ts: '2026-04-26T11:59:00+00:00', calls: 1, subdivisions: 0, saturated: 0 },
      ],
    } });

    render3(<AdminLogsExplorer adminToken="t" />);
    await waitFor3(() => {
      const safeguards = screen3.queryByText(/Ingest safeguards/i);
      expect3(safeguards).toBeTruthy();
      fireEvent3.click(safeguards);
    });

    // Widget itself still renders (it's the totals card).
    await screen3.findByTestId('cf-pull-cost-widget');
    // Sparkline does NOT.
    expect3(screen3.queryByTestId('cf-pull-cost-sparkline')).toBeNull();
  });

  it3('hides the widget entirely when the status payload has no cf_pull_24h (fresh deploy)', async () => {
    const apiMod = await import('@/utils/api');
    apiMod.adminLogsList.mockResolvedValue({ data: { logs: [], total: 0 } });
    apiMod.adminLogsStatus.mockResolvedValue({ data: {
      paused: false, ttl_days: 14, ingest_token_configured: true,
      backend_sample_rate: 0.05, edge_sample_rate: 0.05,
      max_ingest_batch: 500, cf_pull_interval_s: 60,
      cf_pull_24h: null,
    } });

    render3(<AdminLogsExplorer adminToken="t" />);

    await waitFor3(() => {
      const safeguards = screen3.queryByText(/Ingest safeguards/i);
      expect3(safeguards).toBeTruthy();
      fireEvent3.click(safeguards);
    });

    // The "CF GraphQL pull" SafeguardRow still renders, but the
    // dedicated 24h widget must NOT — a fresh deploy with no
    // history would otherwise show a misleading "0 calls" row.
    await waitFor3(() => {
      expect3(screen3.queryByText(/CF GraphQL pull/)).toBeTruthy();
    });
    expect3(screen3.queryByTestId('cf-pull-cost-widget')).toBeNull();
  });
});

// Live DOM tests for row expansion + copy-correlation-id workflows.
import { describe as describe2, it as it2, expect as expect2, vi as vi2, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';

describe2('AdminLogsExplorer — row expansion + copy CID', () => {
  afterEach(() => {
    cleanup();
    vi2.clearAllMocks();
  });

  it2('expands a row to show full JSON payload, and copies the correlation id', async () => {
    const apiMod = await import('@/utils/api');
    const sampleRow = {
      _id: 'edge_abcdef123',
      timestamp: '2026-04-26T10:00:00.000Z',
      received_at: '2026-04-26T10:00:01.000Z',
      source: 'edge',
      level: 'error',
      status: 502,
      method: 'GET',
      route: '/api/test',
      duration_ms: 1234,
      country: 'IN',
      colo: 'BLR',
      cache: 'miss',
      correlation_id: 'corr-xyz-123',
      message: 'origin returned 502',
    };
    apiMod.adminLogsList.mockResolvedValue({
      data: { logs: [sampleRow], total: 1, total_capped: false, next_before: null },
    });
    apiMod.adminLogsStatus.mockResolvedValue({ data: {
      paused: false, ttl_days: 14, ingest_token_configured: true,
      backend_sample_rate: 0.05, max_ingest_batch: 500, cf_pull_interval_s: 60,
    } });
    const writeText = vi2.fn(() => Promise.resolve());
    Object.defineProperty(global.navigator, 'clipboard', {
      value: { writeText }, configurable: true,
    });

    render(<AdminLogsExplorer adminToken="t" />);

    // The row should appear once the mocked list resolves.
    await waitFor(() => {
      expect2(screen.getByTestId('row-expand-0')).toBeTruthy();
    });

    // Initially the JSON payload row is not in the DOM.
    expect2(screen.queryByTestId('row-json-0')).toBeNull();

    // Clicking the chevron expands the row and shows full JSON.
    fireEvent.click(screen.getByTestId('row-expand-0'));
    const pre = await screen.findByTestId('row-json-0');
    expect2(pre.textContent).toContain('"correlation_id"');
    expect2(pre.textContent).toContain('corr-xyz-123');
    expect2(pre.textContent).toContain('"_id"');

    // Copy correlation id button calls clipboard with the cid.
    fireEvent.click(screen.getByTestId('copy-cid-0'));
    await waitFor(() => expect2(writeText).toHaveBeenCalledWith('corr-xyz-123'));
  });
});
