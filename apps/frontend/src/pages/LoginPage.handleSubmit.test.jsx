/**
 * Task #176 — LoginPage: handleSubmit post-login redirect tests.
 *
 * Verifies that after a successful email/password login the page
 * navigates to the correct route based on the returned user object:
 *   - role === 'staff' or 'admin'  →  /staff
 *   - onboarding_done === false     →  /onboarding
 *   - onboarding_done === true      →  /library
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
import { toast } from 'sonner';

async function triggerLogin() {
  await act(async () => {
    fireEvent.change(screen.getByTestId('auth-email-input'), {
      target: { value: 'user@test.com' },
    });
    fireEvent.change(screen.getByTestId('auth-password-input'), {
      target: { value: 'secret' },
    });
    fireEvent.click(screen.getByTestId('auth-submit-button'));
  });
  await act(async () => {
    vi.runAllTimers();
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  mockNavigate.mockClear();
  mockLogin.mockClear();
  vi.mocked(toast.success).mockClear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('LoginPage — handleSubmit redirect logic', () => {
  it('navigates to /onboarding when onboarding_done is false', async () => {
    mockLogin.mockResolvedValueOnce({ role: '', onboarding_done: false });
    render(<LoginPage />);
    await triggerLogin();
    expect(toast.success).toHaveBeenCalledWith('Welcome back!');
    expect(mockNavigate).toHaveBeenCalledWith('/onboarding');
  });

  it('navigates to /library when onboarding_done is true and role is a plain user', async () => {
    mockLogin.mockResolvedValueOnce({ role: '', onboarding_done: true });
    render(<LoginPage />);
    await triggerLogin();
    expect(mockNavigate).toHaveBeenCalledWith('/library');
  });

  it('navigates to /staff when role is "staff"', async () => {
    mockLogin.mockResolvedValueOnce({ role: 'staff', onboarding_done: true });
    render(<LoginPage />);
    await triggerLogin();
    expect(mockNavigate).toHaveBeenCalledWith('/staff');
  });

  it('navigates to /staff when role is "admin"', async () => {
    mockLogin.mockResolvedValueOnce({ role: 'admin', onboarding_done: true });
    render(<LoginPage />);
    await triggerLogin();
    expect(mockNavigate).toHaveBeenCalledWith('/staff');
  });

  it('does not navigate and does not show success toast when login throws', async () => {
    mockLogin.mockRejectedValueOnce(new Error('Bad credentials'));
    render(<LoginPage />);
    await triggerLogin();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });
});
