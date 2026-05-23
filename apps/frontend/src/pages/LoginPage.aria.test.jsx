/**
 * Task #190 — LoginPage: ARIA accessibility tests for error states.
 *
 * Verifies that when the login form shows an error:
 *   - The error banner has role="alert" and id="login-error-message"
 *   - Both email and password inputs gain aria-invalid and aria-describedby
 *     pointing to the banner id
 * And when there is no error those attributes are absent.
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
vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ login: mockLogin }),
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
  formatAuthError: (_err, fallback) => fallback,
}));

import LoginPage from './LoginPage';

async function submitForm() {
  await act(async () => {
    fireEvent.change(screen.getByTestId('auth-email-input'), {
      target: { value: 'user@test.com' },
    });
    fireEvent.change(screen.getByTestId('auth-password-input'), {
      target: { value: 'wrongpassword' },
    });
    fireEvent.click(screen.getByTestId('auth-submit-button'));
  });
  await act(async () => {});
}

beforeEach(() => {
  mockNavigate.mockClear();
  mockLogin.mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('LoginPage — error banner aria attributes', () => {
  it('gives the error banner role="alert" and id="login-error-message" when login fails', async () => {
    mockLogin.mockRejectedValueOnce(new Error('Bad credentials'));
    render(<LoginPage />);
    await submitForm();
    const banner = document.getElementById('login-error-message');
    expect(banner).toBeTruthy();
    expect(banner.getAttribute('role')).toBe('alert');
  });

  it('does not render the login-error-message element when there is no error', () => {
    render(<LoginPage />);
    expect(document.getElementById('login-error-message')).toBeNull();
  });

  it('clears the error banner (and its id) when a subsequent login succeeds', async () => {
    vi.useFakeTimers();
    mockLogin.mockRejectedValueOnce(new Error('Bad credentials'));
    mockLogin.mockResolvedValueOnce({ role: '', onboarding_done: true });
    render(<LoginPage />);

    await submitForm();
    expect(document.getElementById('login-error-message')).toBeTruthy();

    await submitForm();
    await act(async () => { vi.runAllTimers(); });
    expect(document.getElementById('login-error-message')).toBeNull();
    vi.useRealTimers();
  });
});

describe('LoginPage — email input aria attributes on error', () => {
  it('sets aria-invalid="true" on the email input after a failed login', async () => {
    mockLogin.mockRejectedValueOnce(new Error('Bad credentials'));
    render(<LoginPage />);
    await submitForm();
    expect(screen.getByTestId('auth-email-input').getAttribute('aria-invalid')).toBe('true');
  });

  it('sets aria-describedby="login-error-message" on the email input after a failed login', async () => {
    mockLogin.mockRejectedValueOnce(new Error('Bad credentials'));
    render(<LoginPage />);
    await submitForm();
    expect(screen.getByTestId('auth-email-input').getAttribute('aria-describedby')).toBe('login-error-message');
  });

  it('does not set aria-invalid on the email input when there is no error', () => {
    render(<LoginPage />);
    expect(screen.getByTestId('auth-email-input').getAttribute('aria-invalid')).toBeNull();
  });

  it('does not set aria-describedby on the email input when there is no error', () => {
    render(<LoginPage />);
    expect(screen.getByTestId('auth-email-input').getAttribute('aria-describedby')).toBeNull();
  });

  it('removes aria-invalid from the email input after a successful retry', async () => {
    vi.useFakeTimers();
    mockLogin.mockRejectedValueOnce(new Error('Bad credentials'));
    mockLogin.mockResolvedValueOnce({ role: '', onboarding_done: true });
    render(<LoginPage />);

    await submitForm();
    expect(screen.getByTestId('auth-email-input').getAttribute('aria-invalid')).toBe('true');

    await submitForm();
    await act(async () => { vi.runAllTimers(); });
    expect(screen.getByTestId('auth-email-input').getAttribute('aria-invalid')).toBeNull();
    vi.useRealTimers();
  });
});

describe('LoginPage — password input aria attributes on error', () => {
  it('sets aria-invalid="true" on the password input after a failed login', async () => {
    mockLogin.mockRejectedValueOnce(new Error('Bad credentials'));
    render(<LoginPage />);
    await submitForm();
    expect(screen.getByTestId('auth-password-input').getAttribute('aria-invalid')).toBe('true');
  });

  it('sets aria-describedby="login-error-message" on the password input after a failed login', async () => {
    mockLogin.mockRejectedValueOnce(new Error('Bad credentials'));
    render(<LoginPage />);
    await submitForm();
    expect(screen.getByTestId('auth-password-input').getAttribute('aria-describedby')).toBe('login-error-message');
  });

  it('does not set aria-invalid on the password input when there is no error', () => {
    render(<LoginPage />);
    expect(screen.getByTestId('auth-password-input').getAttribute('aria-invalid')).toBeNull();
  });

  it('does not set aria-describedby on the password input when there is no error', () => {
    render(<LoginPage />);
    expect(screen.getByTestId('auth-password-input').getAttribute('aria-describedby')).toBeNull();
  });

  it('removes aria-invalid from the password input after a successful retry', async () => {
    vi.useFakeTimers();
    mockLogin.mockRejectedValueOnce(new Error('Bad credentials'));
    mockLogin.mockResolvedValueOnce({ role: '', onboarding_done: true });
    render(<LoginPage />);

    await submitForm();
    expect(screen.getByTestId('auth-password-input').getAttribute('aria-invalid')).toBe('true');

    await submitForm();
    await act(async () => { vi.runAllTimers(); });
    expect(screen.getByTestId('auth-password-input').getAttribute('aria-invalid')).toBeNull();
    vi.useRealTimers();
  });
});
