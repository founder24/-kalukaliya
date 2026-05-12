/**
 * Task #62 — AlertSettings threshold inline-validation tests.
 * Task #59 — AlertSettings confirm-before-disable guard tests.
 *
 * Task #62 coverage (threshold inline-validation):
 *   - threshold=0 → inline error, red border, Save disabled, no PATCH
 *   - threshold=-5 → inline error, Save disabled
 *   - threshold=1.5 → inline error (non-integer; parseInt would truncate silently)
 *   - non-numeric "abc" via type=number → empty string → no inline error (save guard catches it)
 *   - threshold=1 → no error (boundary — exactly valid)
 *   - threshold=50 → no error (typical value)
 *   - typing invalid then correcting to valid → error disappears
 *   - empty field → no inline error (validated on save, not on empty)
 *   - valid threshold + clicking Save → axios.patch IS called
 *
 * Task #59 coverage (confirm-before-disable guard):
 *   1. Save with disabled=true → no axios.patch; "Confirm pause?" prompt shown.
 *   2. Confirm in the prompt  → axios.patch called with { disabled: true }.
 *   3. Cancel in the prompt   → prompt dismissed; axios.patch NOT called.
 *   4. Save with disabled=false → axios.patch called directly (no confirm step).
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

/* ── axios mock ──────────────────────────────────────────────────────────── */
const { axiosGet, axiosPatch } = vi.hoisted(() => ({
  axiosGet:   vi.fn(),
  axiosPatch: vi.fn(),
}));

vi.mock('axios', () => ({
  default: {
    get:    axiosGet,
    patch:  axiosPatch,
    post:   vi.fn().mockResolvedValue({ data: {} }),
    create: vi.fn(),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
  get:   axiosGet,
  patch: axiosPatch,
}));

vi.mock('@/utils/api', () => ({
  API_BASE: 'http://localhost:8000',
}));

/* ── component import ────────────────────────────────────────────────────── */
import { AlertSettings } from './EdgeMetricsPanel';

/* ── shared helpers ──────────────────────────────────────────────────────── */
const SETTINGS_URL = 'http://localhost:8000/admin/edge/spa-title-miss-settings';

function settingsResponse({ threshold = 50, disabled = false } = {}) {
  return {
    data: {
      configured:    true,
      threshold,
      disabled,
      kv_override_set: false,
      env_threshold: 50,
      env_disabled:  false,
    },
  };
}

function patchSuccess({ threshold = 50, disabled = false } = {}) {
  return Promise.resolve({ data: { ok: true, threshold, disabled } });
}

/* ── Task #62 helpers ────────────────────────────────────────────────────── */
async function renderLoaded(token = null) {
  axiosGet.mockResolvedValue(settingsResponse());
  axiosPatch.mockResolvedValue({ data: { ok: true, threshold: 50, disabled: false } });

  await act(async () => {
    render(<AlertSettings token={token} />);
  });

  await waitFor(() => {
    expect(screen.getByTestId('threshold-input')).toBeDefined();
  });
}

function setThreshold(value) {
  const input = screen.getByTestId('threshold-input');
  fireEvent.change(input, { target: { value } });
}

function getError() {
  return screen.queryByTestId('threshold-error');
}

function getSaveButton() {
  return screen.getByTestId('save-button');
}

/* ── Task #59 helpers ────────────────────────────────────────────────────── */
async function renderAndLoad({ disabled = false } = {}) {
  axiosGet.mockResolvedValueOnce(settingsResponse({ disabled }));
  render(<AlertSettings token="test-token" />);

  await waitFor(() =>
    expect(screen.getByTestId('alert-settings-save-btn')).toBeInTheDocument(),
  );
}

/* ══════════════════════════════════════════════════════════════════════════ */
/* Task #62 — threshold inline-validation                                     */
/* ══════════════════════════════════════════════════════════════════════════ */
describe('AlertSettings — threshold inline validation (Task #62)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows no error when the loaded threshold is valid (50)', async () => {
    await renderLoaded();
    expect(getError()).toBeNull();
    expect(getSaveButton().disabled).toBe(false);
  });

  it('shows inline error when threshold is changed to 0', async () => {
    await renderLoaded();
    setThreshold('0');
    expect(getError()).not.toBeNull();
    expect(getError().textContent).toMatch(/≥\s*1/i);
  });

  it('shows inline error when threshold is changed to -5', async () => {
    await renderLoaded();
    setThreshold('-5');
    expect(getError()).not.toBeNull();
  });

  it('shows inline error for threshold=1.5 (non-integer — Number.isInteger rejects it; parseInt would silently truncate)', async () => {
    await renderLoaded();
    setThreshold('1.5');
    expect(getError()).not.toBeNull();
    expect(getSaveButton().disabled).toBe(true);
  });

  it('shows no inline error for non-numeric "abc" (type=number yields empty string — empty field is validated on save)', async () => {
    await renderLoaded();
    setThreshold('abc');
    expect(getError()).toBeNull();
  });

  it('shows no error for threshold=1 (boundary — exactly valid)', async () => {
    await renderLoaded();
    setThreshold('1');
    expect(getError()).toBeNull();
  });

  it('shows no error for a typical valid threshold (100)', async () => {
    await renderLoaded();
    setThreshold('100');
    expect(getError()).toBeNull();
  });

  it('shows no inline error for an empty field (validated on save, not on empty)', async () => {
    await renderLoaded();
    setThreshold('');
    expect(getError()).toBeNull();
  });

  it('disables the Save button when threshold is invalid', async () => {
    await renderLoaded();
    setThreshold('0');
    expect(getSaveButton().disabled).toBe(true);
  });

  it('re-enables the Save button after correcting an invalid threshold', async () => {
    await renderLoaded();
    setThreshold('0');
    expect(getSaveButton().disabled).toBe(true);

    setThreshold('75');
    expect(getSaveButton().disabled).toBe(false);
    expect(getError()).toBeNull();
  });

  it('clears the inline error once the admin corrects the value', async () => {
    await renderLoaded();
    setThreshold('-1');
    expect(getError()).not.toBeNull();

    setThreshold('10');
    expect(getError()).toBeNull();
  });

  it('styles the input with aria-invalid=true when threshold is invalid', async () => {
    await renderLoaded();
    setThreshold('0');
    const input = screen.getByTestId('threshold-input');
    expect(input.getAttribute('aria-invalid')).toBe('true');
  });

  it('clears aria-invalid once the value is valid', async () => {
    await renderLoaded();
    setThreshold('0');
    setThreshold('25');
    const input = screen.getByTestId('threshold-input');
    expect(input.getAttribute('aria-invalid')).toBe('false');
  });

  it('makes no PATCH request when Save is clicked with an invalid threshold (button is disabled)', async () => {
    await renderLoaded();
    setThreshold('0');

    const btn = getSaveButton();
    expect(btn.disabled).toBe(true);
    fireEvent.click(btn);

    await waitFor(() => {
      expect(axiosPatch).not.toHaveBeenCalled();
    });
  });

  it('makes a PATCH request when Save is clicked with a valid threshold', async () => {
    await renderLoaded();
    setThreshold('75');

    await act(async () => {
      fireEvent.click(getSaveButton());
    });

    await waitFor(() => {
      expect(axiosPatch).toHaveBeenCalledOnce();
      const [, payload] = axiosPatch.mock.calls[0];
      expect(payload.threshold).toBe(75);
    });
  });
});

/* ══════════════════════════════════════════════════════════════════════════ */
/* Task #59 — confirm-before-disable guard                                    */
/* ══════════════════════════════════════════════════════════════════════════ */
describe('AlertSettings — confirm-before-disable guard (Task #59)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('Save while disabled=true shows "Confirm pause?" and does NOT call axios.patch', async () => {
    await renderAndLoad({ disabled: true });

    fireEvent.click(screen.getByTestId('alert-settings-save-btn'));

    expect(axiosPatch).not.toHaveBeenCalled();
    expect(screen.getByTestId('alert-settings-confirm-dialog')).toBeInTheDocument();
    expect(screen.getByText(/Confirm pause\?/i)).toBeInTheDocument();
  });

  it('Clicking Confirm calls axios.patch with { disabled: true }', async () => {
    await renderAndLoad({ disabled: true });
    axiosPatch.mockReturnValueOnce(patchSuccess({ disabled: true }));
    axiosGet.mockResolvedValueOnce(settingsResponse({ disabled: true }));

    fireEvent.click(screen.getByTestId('alert-settings-save-btn'));

    expect(screen.getByTestId('alert-settings-confirm-dialog')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('alert-settings-confirm-btn'));

    await waitFor(() => expect(axiosPatch).toHaveBeenCalledTimes(1));
    expect(axiosPatch).toHaveBeenCalledWith(
      SETTINGS_URL,
      expect.objectContaining({ disabled: true }),
      expect.anything(),
    );
  });

  it('Clicking Cancel dismisses the prompt without calling axios.patch', async () => {
    await renderAndLoad({ disabled: true });

    fireEvent.click(screen.getByTestId('alert-settings-save-btn'));

    expect(screen.getByTestId('alert-settings-confirm-dialog')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('alert-settings-cancel-btn'));

    expect(axiosPatch).not.toHaveBeenCalled();
    expect(screen.queryByTestId('alert-settings-confirm-dialog')).toBeNull();
    expect(screen.getByTestId('alert-settings-save-btn')).toBeInTheDocument();
  });

  it('Save while disabled=false calls axios.patch directly (no confirm step)', async () => {
    await renderAndLoad({ disabled: false });
    axiosPatch.mockReturnValueOnce(patchSuccess({ disabled: false }));
    axiosGet.mockResolvedValueOnce(settingsResponse({ disabled: false }));

    expect(screen.queryByTestId('alert-settings-confirm-dialog')).toBeNull();

    fireEvent.click(screen.getByTestId('alert-settings-save-btn'));

    await waitFor(() => expect(axiosPatch).toHaveBeenCalledTimes(1));
    expect(axiosPatch).toHaveBeenCalledWith(
      SETTINGS_URL,
      expect.objectContaining({ disabled: false }),
      expect.anything(),
    );
    expect(screen.queryByTestId('alert-settings-confirm-dialog')).toBeNull();
  });
});
