/**
 * Task 170 — Confirm the 'Clear resolved' button correctly handles a missing
 * progress log file.
 *
 * Covers every branch in handleClearResolved():
 *   1. file_missing  — backend returns { compacted: false, file_exists: false }
 *                      → toast.info "No progress log found"
 *   2. empty_log     — backend returns { compacted: true, resolved_cleared: 0 }
 *                      → toast.info "No resolved entries"
 *   3. all_resolved  — backend returns { compacted: true, resolved_cleared: N>0, still_stuck: 0 }
 *                      → toast.success with cleared count (singular)
 *   4. mixed         — backend returns { resolved_cleared: N>1, still_stuck: M }
 *                      → toast.success with plural "entries" and still-stuck count
 *   5. api_error     — POST rejects
 *                      → toast.error with the server message
 *
 * We also verify the button's loading / disabled state resets after each call.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

/* ── hoisted spy refs ────────────────────────────────────────────────────── */
const { axiosGet, axiosPost, toastInfo, toastSuccess, toastError } = vi.hoisted(() => ({
  axiosGet:     vi.fn(),
  axiosPost:    vi.fn(),
  toastInfo:    vi.fn(),
  toastSuccess: vi.fn(),
  toastError:   vi.fn(),
}));

vi.mock('axios', () => ({
  default: {
    get:    axiosGet,
    post:   axiosPost,
    create: vi.fn(),
  },
  get:  axiosGet,
  post: axiosPost,
}));

vi.mock('sonner', () => ({
  toast: {
    info:    toastInfo,
    success: toastSuccess,
    error:   toastError,
  },
}));

/* ── lightweight stubs ───────────────────────────────────────────────────── */
vi.mock('@/utils/adminHelpers', () => ({
  API:         'http://test.local',
  authHeaders: (token) => ({ headers: { Authorization: `Bearer ${token}` } }),
}));

vi.mock('@/utils/api', () => ({
  API_BASE: 'http://test.local',
}));

/* ── component import (after all vi.mock calls) ──────────────────────────── */
import SeederHistoryPanel from './SeederHistoryPanel';

/* ── baseline response builders ──────────────────────────────────────────── */

/** GET /history → empty list (no active runs) */
const historyOk = () =>
  Promise.resolve({ data: { runs: [] } });

/** GET /stuck → resolved empty list (stuck panel already in "loaded" state) */
const stuckEmpty = () =>
  Promise.resolve({ data: { stuck: [], total: 0, file_exists: true } });

/**
 * Set up axiosGet to handle both history and stuck calls.
 * stuckOverride lets tests return a populated stuck list so the
 * "Clear resolved" button is always visible.
 */
function setupGetMocks({ stuckData = { stuck: [], total: 0, file_exists: true } } = {}) {
  axiosGet.mockImplementation((url) => {
    if (url.includes('/seed-notes/history')) return historyOk();
    if (url.includes('/seed-notes/stuck'))   return Promise.resolve({ data: stuckData });
    return Promise.resolve({ data: {} });
  });
}

/** Flush the microtask queue so useEffect fetches settle. */
async function flushEffects() {
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

/**
 * Render the panel, flush the initial history fetch, click "Check" to load
 * the stuck list, and return the "Clear resolved" button.
 *
 * After "Check" the stuck panel shows the button regardless of whether there
 * are stuck chapters, so we can test all branches.
 */
async function renderAndOpenStuckPanel(stuckData) {
  setupGetMocks({ stuckData });

  render(<SeederHistoryPanel adminToken="test-admin-token" />);

  // Wait for history to load (removes loading state)
  await flushEffects();

  // Click "Check" to trigger the stuck-list fetch and reveal the clear button
  const checkBtn = await screen.findByRole('button', { name: /^Check$/i });
  fireEvent.click(checkBtn);

  // Wait for the clear button to appear (stuck !== null after fetch)
  const clearBtn = await screen.findByRole('button', { name: /Clear resolved/i });
  return clearBtn;
}

/* ══════════════════════════════════════════════════════════════════════════
   Test suite
   ══════════════════════════════════════════════════════════════════════════ */

describe('SeederHistoryPanel — Clear resolved button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  /* ── 1. File missing ─────────────────────────────────────────────────── */
  it('shows info toast "No progress log found" when the file is absent', async () => {
    const clearBtn = await renderAndOpenStuckPanel();

    axiosPost.mockResolvedValueOnce({
      data: {
        compacted:        false,
        file_exists:      false,
        records_before:   0,
        records_after:    0,
        resolved_cleared: 0,
        still_stuck:      0,
      },
    });

    fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(toastInfo).toHaveBeenCalledWith(
        expect.stringContaining('No progress log found'),
      );
    });

    expect(toastSuccess).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
  });

  /* ── 2. Empty / all-unresolved log ──────────────────────────────────── */
  it('shows info toast "No resolved entries" when nothing can be cleared', async () => {
    const clearBtn = await renderAndOpenStuckPanel();

    axiosPost.mockResolvedValueOnce({
      data: {
        compacted:        true,
        file_exists:      true,
        records_before:   3,
        records_after:    3,
        resolved_cleared: 0,
        still_stuck:      3,
      },
    });

    fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(toastInfo).toHaveBeenCalledWith(
        expect.stringContaining('No resolved entries'),
      );
    });

    expect(toastSuccess).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
  });

  /* ── 3. One resolved (singular "entry") ─────────────────────────────── */
  it('shows success toast with singular "entry" when exactly 1 is cleared', async () => {
    const clearBtn = await renderAndOpenStuckPanel();

    axiosPost.mockResolvedValueOnce({
      data: {
        compacted:        true,
        file_exists:      true,
        records_before:   2,
        records_after:    1,
        resolved_cleared: 1,
        still_stuck:      1,
      },
    });

    fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(toastSuccess).toHaveBeenCalledWith(
        expect.stringContaining('Cleared 1 resolved entry'),
      );
    });

    // Singular: must NOT say "entries"
    const call = toastSuccess.mock.calls[0][0];
    expect(call).not.toMatch(/entries/);
    expect(call).toContain('1 still stuck');
    expect(toastInfo).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
  });

  /* ── 4. Multiple resolved (plural "entries") + still-stuck count ─────── */
  it('shows success toast with plural "entries" and still-stuck count for mixed result', async () => {
    const clearBtn = await renderAndOpenStuckPanel();

    axiosPost.mockResolvedValueOnce({
      data: {
        compacted:        true,
        file_exists:      true,
        records_before:   7,
        records_after:    4,
        resolved_cleared: 3,
        still_stuck:      4,
      },
    });

    fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(toastSuccess).toHaveBeenCalledWith(
        expect.stringContaining('Cleared 3 resolved entries'),
      );
    });

    const call = toastSuccess.mock.calls[0][0];
    expect(call).toContain('4 still stuck');
    expect(toastInfo).not.toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
  });

  /* ── 5. All resolved — zero remaining ───────────────────────────────── */
  it('shows success toast with "0 still stuck" when everything is cleared', async () => {
    const clearBtn = await renderAndOpenStuckPanel();

    axiosPost.mockResolvedValueOnce({
      data: {
        compacted:        true,
        file_exists:      true,
        records_before:   5,
        records_after:    0,
        resolved_cleared: 5,
        still_stuck:      0,
      },
    });

    fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(toastSuccess).toHaveBeenCalledWith(
        expect.stringContaining('Cleared 5 resolved entries'),
      );
    });

    expect(toastSuccess.mock.calls[0][0]).toContain('0 still stuck');
  });

  /* ── 6. API error — server detail surfaced ───────────────────────────── */
  it('shows error toast with the server message when the API call fails', async () => {
    const clearBtn = await renderAndOpenStuckPanel();

    axiosPost.mockRejectedValueOnce({
      response: { data: { detail: 'Internal server error during compact' } },
      message: 'Request failed',
    });

    fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        expect.stringContaining('Internal server error during compact'),
      );
    });

    expect(toastSuccess).not.toHaveBeenCalled();
    expect(toastInfo).not.toHaveBeenCalled();
  });

  /* ── 7. Network error — falls back to err.message ────────────────────── */
  it('shows error toast with err.message when there is no response body', async () => {
    const clearBtn = await renderAndOpenStuckPanel();

    axiosPost.mockRejectedValueOnce(new Error('Network Error'));

    fireEvent.click(clearBtn);

    await waitFor(() => {
      expect(toastError).toHaveBeenCalledWith(
        expect.stringContaining('Network Error'),
      );
    });
  });

  /* ── 8. Button is disabled while the request is in-flight ───────────── */
  it('disables the Clear resolved button while the request is pending', async () => {
    const clearBtn = await renderAndOpenStuckPanel();

    let resolveClear;
    axiosPost.mockReturnValueOnce(
      new Promise((resolve) => { resolveClear = resolve; }),
    );

    fireEvent.click(clearBtn);

    // Button should become disabled / show loading label while pending
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /Clearing…/i }),
      ).toBeInTheDocument();
    });

    // Resolve the request
    resolveClear({
      data: {
        compacted: false, file_exists: false,
        resolved_cleared: 0, still_stuck: 0,
        records_before: 0, records_after: 0,
      },
    });

    // Button should revert to idle label
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /Clear resolved/i }),
      ).toBeInTheDocument();
    });
  });

  /* ── 9. POST is sent to the correct endpoint ─────────────────────────── */
  it('POSTs to /seed-notes/stuck/clear with an empty body', async () => {
    const clearBtn = await renderAndOpenStuckPanel();

    axiosPost.mockResolvedValueOnce({
      data: {
        compacted: false, file_exists: false,
        resolved_cleared: 0, still_stuck: 0,
        records_before: 0, records_after: 0,
      },
    });

    fireEvent.click(clearBtn);

    await waitFor(() => expect(toastInfo).toHaveBeenCalled());

    const [url, body] = axiosPost.mock.calls[0];
    expect(url).toMatch(/seed-notes\/stuck\/clear$/);
    expect(body).toEqual({});
  });
});
