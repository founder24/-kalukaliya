/**
 * Task #62 — AlertSettings threshold inline-validation tests.
 *
 * The AlertSettings sub-panel in EdgeMetricsPanel allows an admin to adjust
 * the SPA title-miss alert threshold at runtime.  Task #62 added client-side
 * validation that:
 *
 *   1. Shows an inline error next to the threshold input when the value is
 *      not a finite integer ≥ 1 (e.g. 0, -5, "abc", "1.5").
 *   2. Styles the input with a red border when the value is invalid.
 *   3. Disables the Save button while the input is invalid.
 *   4. Makes no network request (no axios.patch call) when the threshold is
 *      invalid and the Save button is clicked.
 *
 * The inline error disappears as soon as the admin types a valid value,
 * and the Save button re-enables so they can submit.
 *
 * Coverage:
 *   - threshold=0 → inline error, red border, Save disabled, no PATCH
 *   - threshold=-5 → inline error, Save disabled
 *   - threshold=1.5 → inline error (non-integer; parseInt would truncate silently)
 *   - non-numeric "abc" via type=number → empty string → no inline error (save guard catches it)
 *   - threshold=1 → no error (boundary — exactly valid)
 *   - threshold=50 → no error (typical value)
 *   - typing invalid then correcting to valid → error disappears
 *   - empty field → no inline error (validated on save, not on empty)
 *   - valid threshold + clicking Save → axios.patch IS called
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

/* ── canonical server response ───────────────────────────────────────────── */
const SETTINGS_RESP = {
  configured:    true,
  threshold:     50,
  disabled:      false,
  kv_override_set: false,
  env_threshold: 50,
  env_disabled:  false,
};

async function renderLoaded(token = null) {
  axiosGet.mockResolvedValue({ data: SETTINGS_RESP });
  axiosPatch.mockResolvedValue({
    data: { ok: true, threshold: 50, disabled: false },
  });

  await act(async () => {
    render(<AlertSettings token={token} />);
  });

  // Wait for the settings to load so the form is visible.
  await waitFor(() => {
    expect(screen.getByTestId('threshold-input')).toBeDefined();
  });
}

/* ── helpers ─────────────────────────────────────────────────────────────── */
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

/* ── tests ───────────────────────────────────────────────────────────────── */

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
    // This is the key regression-prevention test for the parseInt → Number.isInteger
    // migration: parseInt("1.5") === 1 (valid), Number.isInteger(1.5) === false (invalid).
    // The Pydantic int field on the backend would reject 1.5 with a 422.
    await renderLoaded();
    setThreshold('1.5');
    expect(getError()).not.toBeNull();
    expect(getSaveButton().disabled).toBe(true);
  });

  it('shows no inline error for non-numeric "abc" (type=number yields empty string — empty field is validated on save)', async () => {
    // <input type="number"> rejects non-numeric characters at the browser level;
    // jsdom sets e.target.value="" for "abc", which hits the "empty = no error" path.
    // The save() guard still catches the invalid value before any network call.
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

    // Button is disabled — click should have no effect.
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
