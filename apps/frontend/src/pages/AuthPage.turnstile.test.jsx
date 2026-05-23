/**
 * Task #451 — End-to-end coverage that the LoginPage / SignupPage
 * actually wait for a Turnstile token before calling the auth API
 * when the bot-protection flag is on.
 *
 * Task #422 unit-tested the widget and its hook in isolation. This
 * suite drives the *page-level* contract:
 *
 *   1. When ``useTurnstileConfig`` reports ``enabled=true`` the
 *      widget renders and the submit button is disabled until
 *      Turnstile mints a token.
 *   2. Submitting before a token exists (e.g. ``fireEvent.submit``
 *      bypassing the disabled button) is blocked at the page layer —
 *      the auth API is **never** called and an inline verification
 *      error surfaces in the existing alert banner.
 *   3. Once Turnstile fires its callback the captured token is
 *      forwarded to ``login() / signup()`` and ``reset()`` runs after
 *      submit on **both** success and failure (Turnstile tokens are
 *      one-shot per the Cloudflare contract).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React from 'react';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  Link: ({ to, children, ...rest }) => <a href={to} {...rest}>{children}</a>,
}));

const mockLogin = vi.fn();
const mockSignup = vi.fn();
vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ login: mockLogin, signup: mockSignup }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('@/hooks/usePublicStats', () => ({
  usePublicStats: () => ({ total_users: 500 }),
}));

vi.mock('@/components/Logo', () => ({
  LogoFull: () => <div data-testid="logo" />,
}));

vi.mock('@/components/GoogleSignInButton', () => ({
  default: () => <div data-testid="google-btn" />,
}));

vi.mock('@/lib/authErrors', () => ({
  formatAuthError: (err, fallback) => {
    if (err && err.message) return err.message;
    return fallback;
  },
}));

// Fake Cloudflare Turnstile global. ``render`` records the most
// recent options so the test can manually invoke the ``callback``
// to simulate the widget minting a token.
const renderCalls = [];
const turnstileResetSpy = vi.fn();
const turnstileRemoveSpy = vi.fn();

function installFakeTurnstile() {
  window.turnstile = {
    render: (container, opts) => {
      renderCalls.push({ container, opts });
      return `widget-${renderCalls.length}`;
    },
    reset: turnstileResetSpy,
    remove: turnstileRemoveSpy,
  };
}

// Mock the hook + script loader so the widget thinks the flag is
// on and the script has loaded — both halves must be true for the
// widget to call ``window.turnstile.render`` (see TurnstileWidget.jsx).
vi.mock('@/hooks/useTurnstile', () => ({
  useTurnstileConfig: () => ({
    enabled: true,
    siteKey: 'test-site-key',
    ready: true,
  }),
  loadTurnstileScript: () => Promise.resolve(window.turnstile),
}));

import LoginPage from './LoginPage';
import SignupPage from './SignupPage';
import { toast } from 'sonner';

beforeEach(() => {
  installFakeTurnstile();
  renderCalls.length = 0;
  turnstileResetSpy.mockClear();
  turnstileRemoveSpy.mockClear();
  mockNavigate.mockClear();
  mockLogin.mockClear();
  mockSignup.mockClear();
  vi.mocked(toast.success).mockClear();
});

afterEach(() => {
  delete window.turnstile;
});

async function renderLoginAndWaitForWidget() {
  render(<LoginPage />);
  // The widget renders inside an effect chain (config → script →
  // render). Flush microtasks until ``render`` is invoked.
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

async function renderSignupAndWaitForWidget() {
  render(<SignupPage />);
  await act(async () => { await Promise.resolve(); });
  await act(async () => { await Promise.resolve(); });
}

function emitToken(token) {
  // Drive the most-recently registered callback the same way the
  // real Cloudflare iframe would once it solves the challenge.
  const last = renderCalls[renderCalls.length - 1];
  expect(last).toBeTruthy();
  last.opts.callback(token);
}

async function fillLoginInputs() {
  await act(async () => {
    fireEvent.change(screen.getByTestId('auth-email-input'), {
      target: { value: 'user@test.com' },
    });
    fireEvent.change(screen.getByTestId('auth-password-input'), {
      target: { value: 'secret' },
    });
  });
}

async function clickLoginSubmit() {
  await act(async () => {
    fireEvent.click(screen.getByTestId('auth-submit-button'));
  });
  await act(async () => { await Promise.resolve(); });
}

async function forceSubmitLoginForm() {
  // Bypass the disabled button by submitting the form directly so
  // we exercise the handleSubmit guard, not just the disabled-button
  // UX. This mirrors what assistive tech / devtools could do.
  await act(async () => {
    fireEvent.submit(screen.getByTestId('auth-submit-button').closest('form'));
  });
  await act(async () => { await Promise.resolve(); });
}

async function fillSignupInputs() {
  await act(async () => {
    fireEvent.change(screen.getByPlaceholderText('Your name'), {
      target: { value: 'Test User' },
    });
    fireEvent.change(screen.getByTestId('auth-email-input'), {
      target: { value: 'user@test.com' },
    });
    fireEvent.change(screen.getByTestId('auth-password-input'), {
      target: { value: 'Password1!' },
    });
    const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
    fireEvent.change(confirmInput, { target: { value: 'Password1!' } });
    fireEvent.click(screen.getByRole('button', { name: /agree to terms/i }));
    fireEvent.click(screen.getByRole('button', { name: /consent to data processing/i }));
  });
}

async function clickSignupSubmit() {
  await act(async () => {
    fireEvent.click(screen.getByTestId('auth-submit-button'));
  });
  await act(async () => { await Promise.resolve(); });
}

async function forceSubmitSignupForm() {
  await act(async () => {
    fireEvent.submit(screen.getByTestId('auth-submit-button').closest('form'));
  });
  await act(async () => { await Promise.resolve(); });
}

describe('LoginPage — Turnstile end-to-end gating', () => {
  it('mounts the widget when useTurnstileConfig reports enabled=true', async () => {
    await renderLoginAndWaitForWidget();
    expect(renderCalls).toHaveLength(1);
    expect(renderCalls[0].opts.sitekey).toBe('test-site-key');
    expect(renderCalls[0].opts.action).toBe('login');
    expect(screen.getByTestId('turnstile-widget')).toBeTruthy();
  });

  it('disables the submit button until Turnstile mints a token', async () => {
    await renderLoginAndWaitForWidget();
    await fillLoginInputs();
    const btn = screen.getByTestId('auth-submit-button');
    // Before the token: button is disabled, aria-disabled set.
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute('aria-disabled')).toBe('true');
    // Once the challenge solves and emits a token, the button enables.
    await act(async () => { emitToken('cf-token-enable'); });
    expect(btn.disabled).toBe(false);
    expect(btn.getAttribute('aria-disabled')).toBeNull();
  });

  it('blocks submission and never calls login() when no token has been minted', async () => {
    await renderLoginAndWaitForWidget();
    await fillLoginInputs();
    // Bypass the disabled button via fireEvent.submit so the
    // handleSubmit guard is exercised (devtools/AT can do this).
    await forceSubmitLoginForm();
    expect(mockLogin).not.toHaveBeenCalled();
    // Verification UX surfaces in the existing alert banner.
    expect(screen.getByRole('alert').textContent).toMatch(
      /verification challenge/i,
    );
  });

  it('passes the captured Turnstile token into login() and resets the widget after success', async () => {
    mockLogin.mockResolvedValueOnce({ role: '', onboarding_done: true });
    await renderLoginAndWaitForWidget();
    await fillLoginInputs();

    // Cloudflare iframe solves the challenge → emits the token.
    await act(async () => { emitToken('cf-token-success'); });
    await clickLoginSubmit();

    expect(mockLogin).toHaveBeenCalledTimes(1);
    expect(mockLogin).toHaveBeenCalledWith(
      'user@test.com',
      'secret',
      'cf-token-success',
    );
    // Token is one-shot per the Turnstile contract.
    expect(turnstileResetSpy).toHaveBeenCalledTimes(1);
  });

  it('still resets the widget when login() rejects (failure path)', async () => {
    mockLogin.mockRejectedValueOnce(new Error('Bad credentials'));
    await renderLoginAndWaitForWidget();
    await fillLoginInputs();

    await act(async () => { emitToken('cf-token-fail'); });
    await clickLoginSubmit();

    expect(mockLogin).toHaveBeenCalledWith(
      'user@test.com',
      'secret',
      'cf-token-fail',
    );
    expect(turnstileResetSpy).toHaveBeenCalledTimes(1);
    // The error banner surfaces the failure UX.
    expect(screen.getByRole('alert').textContent).toContain('Bad credentials');
  });

  it('re-disables the submit button after a successful submit (token is one-shot)', async () => {
    mockLogin.mockResolvedValueOnce({ role: '', onboarding_done: true });
    await renderLoginAndWaitForWidget();
    await fillLoginInputs();
    await act(async () => { emitToken('cf-token-once'); });
    expect(screen.getByTestId('auth-submit-button').disabled).toBe(false);
    await clickLoginSubmit();
    // After the in-flight submit completes the local token state is
    // cleared so the user must complete a fresh challenge before
    // the next attempt — the button goes back to disabled.
    expect(screen.getByTestId('auth-submit-button').disabled).toBe(true);
  });
});

describe('SignupPage — Turnstile end-to-end gating', () => {
  it('mounts the widget with action="signup" when useTurnstileConfig reports enabled=true', async () => {
    await renderSignupAndWaitForWidget();
    expect(renderCalls).toHaveLength(1);
    expect(renderCalls[0].opts.sitekey).toBe('test-site-key');
    expect(renderCalls[0].opts.action).toBe('signup');
  });

  it('disables the submit button until Turnstile mints a token', async () => {
    await renderSignupAndWaitForWidget();
    await fillSignupInputs();
    const btn = screen.getByTestId('auth-submit-button');
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute('aria-disabled')).toBe('true');
    await act(async () => { emitToken('cf-signup-enable'); });
    expect(btn.disabled).toBe(false);
    expect(btn.getAttribute('aria-disabled')).toBeNull();
  });

  it('blocks submission and never calls signup() when no token has been minted', async () => {
    await renderSignupAndWaitForWidget();
    await fillSignupInputs();
    await forceSubmitSignupForm();
    expect(mockSignup).not.toHaveBeenCalled();
    expect(screen.getByRole('alert').textContent).toMatch(
      /verification challenge/i,
    );
  });

  it('passes the captured Turnstile token into signup() and resets the widget after success', async () => {
    mockSignup.mockResolvedValueOnce({ role: '', onboarding_done: false });
    await renderSignupAndWaitForWidget();
    await fillSignupInputs();

    await act(async () => { emitToken('cf-token-signup'); });
    await clickSignupSubmit();

    expect(mockSignup).toHaveBeenCalledTimes(1);
    // signup(name, email, password, consentDpdp, turnstileToken)
    expect(mockSignup).toHaveBeenCalledWith(
      'Test User',
      'user@test.com',
      'Password1!',
      true,
      'cf-token-signup',
    );
    expect(turnstileResetSpy).toHaveBeenCalledTimes(1);
  });

  it('still resets the widget when signup() rejects (failure path)', async () => {
    mockSignup.mockRejectedValueOnce(new Error('Email already taken'));
    await renderSignupAndWaitForWidget();
    await fillSignupInputs();

    await act(async () => { emitToken('cf-token-signup-fail'); });
    await clickSignupSubmit();

    expect(mockSignup).toHaveBeenCalledWith(
      'Test User',
      'user@test.com',
      'Password1!',
      true,
      'cf-token-signup-fail',
    );
    expect(turnstileResetSpy).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('alert').textContent).toContain('Email already taken');
  });

  it('re-disables the submit button after a successful submit (token is one-shot)', async () => {
    mockSignup.mockResolvedValueOnce({ role: '', onboarding_done: false });
    await renderSignupAndWaitForWidget();
    await fillSignupInputs();
    await act(async () => { emitToken('cf-signup-once'); });
    expect(screen.getByTestId('auth-submit-button').disabled).toBe(false);
    await clickSignupSubmit();
    expect(screen.getByTestId('auth-submit-button').disabled).toBe(true);
  });
});
