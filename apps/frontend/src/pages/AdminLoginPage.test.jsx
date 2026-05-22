/**
 * Task #452 — Lock the AdminLoginPage Turnstile contract introduced in
 * Task #423 against silent regressions.
 *
 * The page wires the Turnstile widget through an imperative ref handle
 * and forwards the captured token as the 3rd positional arg to
 * ``adminLogin``. None of that is visible in the rendered DOM, so a
 * future refactor that drops the ref, the header, or the reset() in
 * ``finally`` would silently disable admin credential-stuffing
 * protection. This suite asserts:
 *
 *   1. ``adminLogin`` receives the token captured from the widget ref
 *      as the 3rd argument on submit.
 *   2. ``reset()`` runs after a SUCCESSFUL submit (token is one-shot).
 *   3. ``reset()`` runs after a FAILED submit (token is one-shot).
 *   4. A structured ``{code, message}`` 403 detail (the new
 *      ``turnstile_required`` shape) renders the human ``message`` in
 *      the alert banner — not ``[object Object]``.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React, { forwardRef, useImperativeHandle } from 'react';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

const mockAdminLogin = vi.fn();
vi.mock('@/utils/api', () => ({
  adminLogin: (...args) => mockAdminLogin(...args),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Stub the Turnstile widget with a forwardRef that exposes the same
// imperative API the real component does (getToken / reset). The
// captured ``getToken`` and ``reset`` spies let the test drive the
// minted-token value and assert the page's reset-on-finally contract.
const turnstileGetToken = vi.fn();
const turnstileReset = vi.fn();

vi.mock('@/components/TurnstileWidget', () => ({
  default: forwardRef((props, ref) => {
    useImperativeHandle(ref, () => ({
      getToken: turnstileGetToken,
      reset: turnstileReset,
      enabled: true,
    }));
    return <div data-testid="turnstile-stub" data-action={props.action} />;
  }),
}));

import AdminLoginPage from './AdminLoginPage';

beforeEach(() => {
  mockNavigate.mockClear();
  mockAdminLogin.mockReset();
  turnstileGetToken.mockReset();
  turnstileReset.mockClear();
});

afterEach(() => {
  vi.clearAllMocks();
});

async function fillCredentials() {
  await act(async () => {
    fireEvent.change(screen.getByTestId('admin-email-input'), {
      target: { value: 'admin@syrabit.ai' },
    });
    fireEvent.change(screen.getByTestId('admin-password-input'), {
      target: { value: 'super-secret' },
    });
  });
}

async function submitForm() {
  await act(async () => {
    fireEvent.click(screen.getByTestId('admin-login-submit-button'));
  });
  // Flush the awaited adminLogin promise + finally block.
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

describe('<AdminLoginPage /> — Turnstile contract (Task #452)', () => {
  it('mounts the Turnstile widget with action="admin-login"', () => {
    turnstileGetToken.mockReturnValue('');
    render(<AdminLoginPage />);
    const stub = screen.getByTestId('turnstile-stub');
    expect(stub).toBeTruthy();
    expect(stub.getAttribute('data-action')).toBe('admin-login');
  });

  it('forwards the captured Turnstile token as the 3rd arg to adminLogin and resets on success', async () => {
    turnstileGetToken.mockReturnValue('cf-admin-token-success');
    mockAdminLogin.mockResolvedValueOnce({ data: { name: 'Root' } });

    render(<AdminLoginPage />);
    await fillCredentials();
    await submitForm();

    expect(mockAdminLogin).toHaveBeenCalledTimes(1);
    expect(mockAdminLogin).toHaveBeenCalledWith(
      'admin@syrabit.ai',
      'super-secret',
      'cf-admin-token-success',
    );
    // reset() runs in finally → exactly once after success.
    expect(turnstileReset).toHaveBeenCalledTimes(1);
    expect(mockNavigate).toHaveBeenCalledWith('/admin');
  });

  it('still calls reset() after adminLogin rejects (failure path)', async () => {
    turnstileGetToken.mockReturnValue('cf-admin-token-fail');
    mockAdminLogin.mockRejectedValueOnce({
      response: { data: { detail: 'Invalid credentials' } },
    });

    render(<AdminLoginPage />);
    await fillCredentials();
    await submitForm();

    expect(mockAdminLogin).toHaveBeenCalledWith(
      'admin@syrabit.ai',
      'super-secret',
      'cf-admin-token-fail',
    );
    expect(turnstileReset).toHaveBeenCalledTimes(1);
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(screen.getByText('Invalid credentials')).toBeTruthy();
  });

  it('renders the human message from a structured {code, message} 403 detail', async () => {
    turnstileGetToken.mockReturnValue('');
    // Backend now returns the structured turnstile_required shape:
    // { detail: { code: 'turnstile_required', message: '...' } }.
    // The page MUST surface ``message`` to the user, not the
    // stringified object (which would render "[object Object]").
    mockAdminLogin.mockRejectedValueOnce({
      response: {
        status: 403,
        data: {
          detail: {
            code: 'turnstile_required',
            message: 'Please complete the verification challenge.',
          },
        },
      },
    });

    render(<AdminLoginPage />);
    await fillCredentials();
    await submitForm();

    expect(
      screen.getByText('Please complete the verification challenge.'),
    ).toBeTruthy();
    // Defensive: the bare object must NOT have leaked into the DOM.
    expect(document.body.textContent).not.toContain('[object Object]');
    expect(turnstileReset).toHaveBeenCalledTimes(1);
  });

  it('falls back to "Invalid credentials" when the error has no detail at all', async () => {
    turnstileGetToken.mockReturnValue('');
    mockAdminLogin.mockRejectedValueOnce(new Error('network down'));

    render(<AdminLoginPage />);
    await fillCredentials();
    await submitForm();

    expect(screen.getByText('Invalid credentials')).toBeTruthy();
    expect(turnstileReset).toHaveBeenCalledTimes(1);
  });

  it('passes an empty-string token (not undefined) when the widget ref is unavailable', async () => {
    // Simulate the widget ref returning ``undefined`` (e.g. Turnstile
    // disabled by config). The page's optional-chain MUST coerce that
    // to '' so adminLogin always receives a string 3rd arg.
    turnstileGetToken.mockReturnValue(undefined);
    mockAdminLogin.mockResolvedValueOnce({ data: { name: 'Root' } });

    render(<AdminLoginPage />);
    await fillCredentials();
    await submitForm();

    expect(mockAdminLogin).toHaveBeenCalledWith(
      'admin@syrabit.ai',
      'super-secret',
      '',
    );
  });
});
