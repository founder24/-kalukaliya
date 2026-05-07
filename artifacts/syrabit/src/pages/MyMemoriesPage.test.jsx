/**
 * Task #480 — Lock the "Forget everything" privacy control on
 * /profile/memories against silent regressions.
 *
 * Task #443 added a bulk-delete dialog that hits
 * ``DELETE /api/user/memories`` and is gated by a typed-FORGET
 * confirmation. This suite seeds two memories, opens the dialog, and
 * asserts:
 *
 *   1. The "Forget all" button stays disabled until the user types
 *      the literal string ``FORGET`` (case-sensitive, no padding).
 *   2. On confirm, the page calls ``apiClient().delete('/user/memories')``
 *      exactly once, swaps to the empty-state, and surfaces a success
 *      toast that includes the deleted count returned by the backend.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

const mockNavigate = vi.fn();
const mockGet = vi.fn();
const mockDelete = vi.fn();
const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();

let mockUser = { id: 'user-A', email: 'a@syrabit.ai' };

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ user: mockUser }),
}));

vi.mock('@/utils/api', () => ({
  apiClient: () => ({
    get:    (...args) => mockGet(...args),
    delete: (...args) => mockDelete(...args),
  }),
}));

vi.mock('sonner', () => ({
  toast: {
    success: (...args) => mockToastSuccess(...args),
    error:   (...args) => mockToastError(...args),
  },
}));

vi.mock('@/components/layout/AppLayout', () => ({
  AppLayout: ({ children }) => <main>{children}</main>,
}));

vi.mock('@/components/PageTitle', () => ({
  PageTitle: () => null,
}));

import MyMemoriesPage from './MyMemoriesPage';

const SEEDED_ITEMS = [
  {
    id: 'mem-1',
    kind: 'qa',
    text: "A's first memory",
    subject_id: 'bio-11',
    subject_name: 'Biology',
    chapter_name: 'Cells',
    created_at: '2026-05-01T10:00:00Z',
  },
  {
    id: 'mem-2',
    kind: 'fact',
    text: "A's second memory",
    subject_id: 'phy-11',
    subject_name: 'Physics',
    chapter_name: 'Optics',
    created_at: '2026-05-01T11:00:00Z',
  },
];

beforeEach(() => {
  mockNavigate.mockReset();
  mockGet.mockReset();
  mockDelete.mockReset();
  mockToastSuccess.mockReset();
  mockToastError.mockReset();
  mockUser = { id: 'user-A', email: 'a@syrabit.ai' };

  mockGet.mockResolvedValue({
    data: {
      items:    SEEDED_ITEMS,
      total:    SEEDED_ITEMS.length,
      offset:   0,
      limit:    20,
      has_more: false,
    },
  });
});

async function renderAndWaitForList() {
  render(<MyMemoriesPage />);
  // Wait for the seeded memories to populate so the "Forget everything"
  // affordance (gated on total > 0) is visible.
  await screen.findAllByTestId('memory-card');
}

describe('<MyMemoriesPage /> — Forget everything flow (Task #443/#480)', () => {
  it('keeps "Forget all" disabled until the user types FORGET exactly', async () => {
    await renderAndWaitForList();

    fireEvent.click(screen.getByTestId('forget-all-memories'));
    const dialog = await screen.findByTestId('forget-all-dialog');
    expect(dialog).toBeTruthy();

    const confirmBtn = screen.getByTestId('confirm-forget-all');
    const input = screen.getByTestId('forget-all-input');

    // 1. Initially disabled (empty input).
    expect(confirmBtn).toBeDisabled();

    // 2. Partial / wrong-case input stays disabled — the gate is a
    //    strict string match on the literal "FORGET".
    fireEvent.change(input, { target: { value: 'forget' } });
    expect(confirmBtn).toBeDisabled();
    fireEvent.change(input, { target: { value: 'FORGE' } });
    expect(confirmBtn).toBeDisabled();
    fireEvent.change(input, { target: { value: ' FORGET' } });
    expect(confirmBtn).toBeDisabled();

    // 3. Exact "FORGET" enables the confirm button.
    fireEvent.change(input, { target: { value: 'FORGET' } });
    expect(confirmBtn).not.toBeDisabled();

    // No DELETE call should have fired while the user was typing.
    expect(mockDelete).not.toHaveBeenCalled();
  });

  it('confirms "Forget all" → calls DELETE /user/memories, shows empty-state and success toast', async () => {
    mockDelete.mockResolvedValueOnce({ data: { ok: true, deleted: 2 } });

    await renderAndWaitForList();
    fireEvent.click(screen.getByTestId('forget-all-memories'));

    const input = await screen.findByTestId('forget-all-input');
    fireEvent.change(input, { target: { value: 'FORGET' } });

    await act(async () => {
      fireEvent.click(screen.getByTestId('confirm-forget-all'));
    });

    // Hits the bulk-delete endpoint exactly once with no path-suffix.
    await waitFor(() => {
      expect(mockDelete).toHaveBeenCalledTimes(1);
    });
    expect(mockDelete).toHaveBeenCalledWith('/user/memories');

    // Empty-state replaces the seeded list.
    await screen.findByTestId('memories-empty');
    expect(screen.queryAllByTestId('memory-card')).toHaveLength(0);

    // Dialog tears itself down on success.
    expect(screen.queryByTestId('forget-all-dialog')).toBeNull();

    // Success toast carries the deleted count returned by the API.
    expect(mockToastSuccess).toHaveBeenCalledWith('Forgot 2 memories');
    expect(mockToastError).not.toHaveBeenCalled();
  });

  it('surfaces an error toast and keeps the list intact when the bulk delete fails', async () => {
    mockDelete.mockRejectedValueOnce(new Error('boom'));

    await renderAndWaitForList();
    fireEvent.click(screen.getByTestId('forget-all-memories'));

    const input = await screen.findByTestId('forget-all-input');
    fireEvent.change(input, { target: { value: 'FORGET' } });

    await act(async () => {
      fireEvent.click(screen.getByTestId('confirm-forget-all'));
    });

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith('Failed to forget your memories');
    });
    // Seeded memories are still on screen — nothing was wiped client-side.
    expect(screen.getAllByTestId('memory-card')).toHaveLength(2);
    expect(mockToastSuccess).not.toHaveBeenCalled();
  });
});

/**
 * Task #481 / #526 — Lock the debounced keyword search box on
 * /profile/memories. Asserts the three behaviors that make search
 * feel reliable to a student:
 *
 *   1. Typing fires exactly ONE reload after the 300ms debounce
 *      (and forwards the trimmed `q` param to the backend).
 *   2. The clear (X) button resets the input + result list back to
 *      the unfiltered baseline returned by the API.
 *   3. When the backend returns no matches for `q`, the existing
 *      "no matches" empty-state copy is shown (not the cold-start
 *      "Syra hasn't saved any memories" copy).
 */
describe('<MyMemoriesPage /> — debounced keyword search (Task #481/#526)', () => {
  it('debounces typing and triggers exactly one reload with the trimmed q', async () => {
    vi.useFakeTimers();
    try {
      // First call (initial mount, no q) returns the seeded list.
      mockGet.mockResolvedValueOnce({
        data: { items: SEEDED_ITEMS, total: 2, offset: 0, limit: 20, has_more: false },
      });

      render(<MyMemoriesPage />);
      // Flush the initial mount load so the search box renders.
      await act(async () => { await Promise.resolve(); });
      await act(async () => { vi.advanceTimersByTime(300); });
      await act(async () => { await Promise.resolve(); });

      const input = screen.getByTestId('memory-search-input');
      expect(mockGet).toHaveBeenCalledTimes(1);

      // Second call (the debounced search) returns just one item.
      mockGet.mockResolvedValueOnce({
        data: {
          items: [SEEDED_ITEMS[0]],
          total: 1, offset: 0, limit: 20, has_more: false,
        },
      });

      // Three keystrokes inside the 300ms window — must collapse
      // into ONE backend call, not three.
      await act(async () => { fireEvent.change(input, { target: { value: 'p' } }); });
      await act(async () => { vi.advanceTimersByTime(100); });
      await act(async () => { fireEvent.change(input, { target: { value: 'ph' } }); });
      await act(async () => { vi.advanceTimersByTime(100); });
      await act(async () => { fireEvent.change(input, { target: { value: 'phys' } }); });

      // Mid-flight: still only the original mount call.
      expect(mockGet).toHaveBeenCalledTimes(1);

      // Cross the debounce boundary → one fresh load fires.
      await act(async () => { vi.advanceTimersByTime(300); });
      await act(async () => { await Promise.resolve(); });

      expect(mockGet).toHaveBeenCalledTimes(2);
      const [, lastOpts] = mockGet.mock.calls[1];
      expect(mockGet.mock.calls[1][0]).toBe('/user/memories');
      expect(lastOpts.params.q).toBe('phys');
      // limit/offset stay aligned with the page contract.
      expect(lastOpts.params.offset).toBe(0);
      expect(lastOpts.params.limit).toBe(20);
    } finally {
      vi.useRealTimers();
    }
  });

  it('clear (X) button resets the input and reloads the unfiltered list', async () => {
    vi.useFakeTimers();
    try {
      // Mount load → seeded items.
      mockGet.mockResolvedValueOnce({
        data: { items: SEEDED_ITEMS, total: 2, offset: 0, limit: 20, has_more: false },
      });
      render(<MyMemoriesPage />);
      await act(async () => { vi.advanceTimersByTime(300); });
      await act(async () => { await Promise.resolve(); });

      const input = screen.getByTestId('memory-search-input');

      // Search load → only one item matches.
      mockGet.mockResolvedValueOnce({
        data: { items: [SEEDED_ITEMS[0]], total: 1, offset: 0, limit: 20, has_more: false },
      });
      await act(async () => { fireEvent.change(input, { target: { value: 'newton' } }); });
      await act(async () => { vi.advanceTimersByTime(300); });
      await act(async () => { await Promise.resolve(); });
      expect(mockGet).toHaveBeenCalledTimes(2);

      // The clear button only renders while the input has content.
      const clearBtn = screen.getByTestId('memory-search-clear');

      // Reset load → seeded list again, no `q` param.
      mockGet.mockResolvedValueOnce({
        data: { items: SEEDED_ITEMS, total: 2, offset: 0, limit: 20, has_more: false },
      });
      await act(async () => { fireEvent.click(clearBtn); });
      await act(async () => { vi.advanceTimersByTime(300); });
      await act(async () => { await Promise.resolve(); });

      // Input is empty + clear button is gone.
      expect(screen.getByTestId('memory-search-input').value).toBe('');
      expect(screen.queryByTestId('memory-search-clear')).toBeNull();

      // The reset reload omits the `q` param entirely (not q='').
      expect(mockGet).toHaveBeenCalledTimes(3);
      const [, resetOpts] = mockGet.mock.calls[2];
      expect('q' in resetOpts.params).toBe(false);

      // Both seeded cards are back on screen.
      expect(screen.getAllByTestId('memory-card')).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('shows the "no matches" empty state when q returns zero results', async () => {
    vi.useFakeTimers();
    try {
      mockGet.mockResolvedValueOnce({
        data: { items: SEEDED_ITEMS, total: 2, offset: 0, limit: 20, has_more: false },
      });
      render(<MyMemoriesPage />);
      await act(async () => { vi.advanceTimersByTime(300); });
      await act(async () => { await Promise.resolve(); });

      const input = screen.getByTestId('memory-search-input');

      // Search returns no matches — must show the *no-matches* copy,
      // NOT the cold-start "Syra hasn't saved any memories" copy.
      mockGet.mockResolvedValueOnce({
        data: { items: [], total: 0, offset: 0, limit: 20, has_more: false },
      });
      await act(async () => {
        fireEvent.change(input, { target: { value: 'zzznomatch' } });
      });
      await act(async () => { vi.advanceTimersByTime(300); });
      await act(async () => { await Promise.resolve(); });

      const empty = screen.getByTestId('memories-empty');
      expect(empty.textContent).toMatch(/No memories match these filters/i);
      expect(empty.textContent).not.toMatch(/hasn't saved any memories/i);
      // Cards from the previous load are gone.
      expect(screen.queryAllByTestId('memory-card')).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
