/**
 * Task #194 — LoginPage: axe accessibility audit.
 *
 * Covers two key render states:
 *  - Clean form (no error)
 *  - Error banner active after a failed login attempt
 *
 * Source fixes applied to LoginPage.jsx as part of this task:
 *  - aria-label="Go to Syrabit home" on both logo <Link> elements
 *    (desktop sidebar + mobile header) — icon-wrapped links need explicit
 *    accessible names when the inner component is mocked as an empty div.
 *  - aria-label={showPass ? 'Hide password' : 'Show password'} on the
 *    password visibility toggle button (icon-only control).
 */
import { describe, it, expect, vi } from 'vitest';
import { axe, toHaveNoViolations } from 'jest-axe';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React from 'react';

expect.extend(toHaveNoViolations);

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

describe('LoginPage — axe accessibility audit', () => {
  it('has no axe violations on the clean form (no error state)', async () => {
    const { container } = render(<LoginPage />);
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations when an error banner is active after failed login', async () => {
    mockLogin.mockRejectedValueOnce(new Error('Bad credentials'));
    const { container } = render(<LoginPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-email-input'), { target: { value: 'u@test.com' } });
      fireEvent.change(screen.getByTestId('auth-password-input'), { target: { value: 'wrong' } });
      fireEvent.click(screen.getByTestId('auth-submit-button'));
    });
    await act(async () => {});
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
