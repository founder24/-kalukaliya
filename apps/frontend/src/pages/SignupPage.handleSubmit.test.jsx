/**
 * Task #181 — SignupPage: handleSubmit post-registration redirect tests.
 *
 * Verifies that after a successful signup the page navigates to the
 * correct route based on the returned user object:
 *   - role === 'staff' or 'admin'  →  /staff
 *   - any other role (new user)    →  /onboarding
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { axe, toHaveNoViolations } from 'jest-axe';

expect.extend(toHaveNoViolations);
import { render, screen, fireEvent, act } from '@testing-library/react';
import React from 'react';

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
  Link: ({ to, children, ...rest }) => <a href={to} {...rest}>{children}</a>,
}));

const mockSignup = vi.fn();
vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ signup: mockSignup }),
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

import SignupPage from './SignupPage';
import { toast } from 'sonner';

async function triggerSignup() {
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
    const [confirmInput] = screen.getAllByPlaceholderText('••••••••').slice(-1);
    fireEvent.change(confirmInput, { target: { value: 'Password1!' } });
    fireEvent.click(screen.getByRole('button', { name: /agree to terms/i }));
    fireEvent.click(screen.getByRole('button', { name: /consent to data processing/i }));
  });
  await act(async () => {
    fireEvent.click(screen.getByTestId('auth-submit-button'));
  });
  await act(async () => {});
}

beforeEach(() => {
  mockNavigate.mockClear();
  mockSignup.mockClear();
  vi.mocked(toast.success).mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
});

async function fillForm({ password = 'Password1!', confirmPassword = 'Password1!', agreed = false, consentDpdp = false } = {}) {
  await act(async () => {
    fireEvent.change(screen.getByPlaceholderText('Your name'), {
      target: { value: 'Test User' },
    });
    fireEvent.change(screen.getByTestId('auth-email-input'), {
      target: { value: 'user@test.com' },
    });
    fireEvent.change(screen.getByTestId('auth-password-input'), {
      target: { value: password },
    });
    const [confirmInput] = screen.getAllByPlaceholderText('••••••••').slice(-1);
    fireEvent.change(confirmInput, { target: { value: confirmPassword } });
    if (agreed) {
      fireEvent.click(screen.getByRole('button', { name: /agree to terms/i }));
    }
    if (consentDpdp) {
      fireEvent.click(screen.getByRole('button', { name: /consent to data processing/i }));
    }
  });
  await act(async () => {
    fireEvent.click(screen.getByTestId('auth-submit-button'));
  });
  await act(async () => {});
}

describe('SignupPage — inline password-mismatch hint', () => {
  it('shows the inline hint when confirm password differs from password', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
      const [confirmInput] = screen.getAllByPlaceholderText('••••••••').slice(-1);
      fireEvent.change(confirmInput, { target: { value: 'Different1!' } });
    });
    expect(screen.getByText("Passwords don't match")).toBeTruthy();
  });

  it('hides the inline hint once confirm password is corrected to match', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
      const [confirmInput] = screen.getAllByPlaceholderText('••••••••').slice(-1);
      fireEvent.change(confirmInput, { target: { value: 'Different1!' } });
    });
    expect(screen.getByText("Passwords don't match")).toBeTruthy();
    await act(async () => {
      const [confirmInput] = screen.getAllByPlaceholderText('••••••••').slice(-1);
      fireEvent.change(confirmInput, { target: { value: 'Password1!' } });
    });
    expect(screen.queryByText("Passwords don't match")).toBeNull();
  });
});

describe('SignupPage — handleSubmit validation guards', () => {
  it('shows "Passwords do not match" and never calls signup when passwords differ', async () => {
    render(<SignupPage />);
    await fillForm({ password: 'Password1!', confirmPassword: 'Different1!', agreed: true, consentDpdp: true });
    expect(screen.getByText('Passwords do not match')).toBeTruthy();
    expect(mockSignup).not.toHaveBeenCalled();
  });

  it('shows "Please agree to the Terms of Service" and never calls signup when Terms unchecked', async () => {
    render(<SignupPage />);
    await fillForm({ password: 'Password1!', confirmPassword: 'Password1!', agreed: false, consentDpdp: true });
    expect(screen.getByText('Please agree to the Terms of Service')).toBeTruthy();
    expect(mockSignup).not.toHaveBeenCalled();
  });

  it('shows the DPDP error and never calls signup when DPDP consent is missing', async () => {
    render(<SignupPage />);
    await fillForm({ password: 'Password1!', confirmPassword: 'Password1!', agreed: true, consentDpdp: false });
    expect(screen.getByText('Please provide consent for data processing under the DPDP Act')).toBeTruthy();
    expect(mockSignup).not.toHaveBeenCalled();
  });
});

describe('SignupPage — error banner is cleared on retry', () => {
  it('clears a stale validation error when the user fixes the form and resubmits', async () => {
    mockSignup.mockResolvedValueOnce({ role: '', onboarding_done: false });
    render(<SignupPage />);

    // Phase 1: submit with mismatched passwords → error banner appears
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
      fireEvent.change(confirmInput, { target: { value: 'Different1!' } });
      fireEvent.click(screen.getByRole('button', { name: /agree to terms/i }));
      fireEvent.click(screen.getByRole('button', { name: /consent to data processing/i }));
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('auth-submit-button'));
    });
    await act(async () => {});

    expect(screen.getByText('Passwords do not match')).toBeTruthy();

    // Phase 2: fix the confirm password and resubmit — setError('') fires at
    // the top of handleSubmit, so the banner must be gone before signup runs
    await act(async () => {
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Password1!' } });
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('auth-submit-button'));
    });
    await act(async () => {});

    expect(screen.queryByText('Passwords do not match')).toBeNull();
    expect(mockSignup).toHaveBeenCalledTimes(1);
  });
});

describe('SignupPage — confirm-password red border', () => {
  it('applies border-red-500/40 to the confirm-password input when passwords do not match', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Different1!' } });
    });
    const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
    expect(confirmInput.className).toContain('border-red-500/40');
  });

  it('removes border-red-500/40 from the confirm-password input once passwords match', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Different1!' } });
    });
    // Confirm the red border is present first
    expect(screen.getAllByPlaceholderText('••••••••').slice(-1)[0].className).toContain('border-red-500/40');

    await act(async () => {
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Password1!' } });
    });
    const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
    expect(confirmInput.className).not.toContain('border-red-500/40');
  });
});

describe('SignupPage — password input red border', () => {
  it('applies border-red-500/40 to the password input when confirm password is non-empty and does not match', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Different1!' } });
    });
    const passwordInput = screen.getByTestId('auth-password-input');
    expect(passwordInput.className).toContain('border-red-500/40');
  });

  it('removes border-red-500/40 from the password input once passwords match', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Different1!' } });
    });
    expect(screen.getByTestId('auth-password-input').className).toContain('border-red-500/40');

    await act(async () => {
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Password1!' } });
    });
    expect(screen.getByTestId('auth-password-input').className).not.toContain('border-red-500/40');
  });

  it('does not apply border-red-500/40 to the password input when confirm password is empty', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
    });
    expect(screen.getByTestId('auth-password-input').className).not.toContain('border-red-500/40');
  });
});

describe('SignupPage — aria-invalid and aria-describedby on password mismatch', () => {
  it('sets aria-invalid on both inputs when confirm is non-empty and passwords differ', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Different1!' } });
    });
    expect(screen.getByTestId('auth-password-input').getAttribute('aria-invalid')).toBe('true');
    const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
    expect(confirmInput.getAttribute('aria-invalid')).toBe('true');
  });

  it('removes aria-invalid from both inputs once passwords match', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Different1!' } });
    });
    await act(async () => {
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Password1!' } });
    });
    expect(screen.getByTestId('auth-password-input').getAttribute('aria-invalid')).toBeNull();
    const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
    expect(confirmInput.getAttribute('aria-invalid')).toBeNull();
  });

  it('does not set aria-invalid when confirm password is empty', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
    });
    expect(screen.getByTestId('auth-password-input').getAttribute('aria-invalid')).toBeNull();
  });

  it('sets aria-describedby on the confirm input pointing to the error message id', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Different1!' } });
    });
    const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
    expect(confirmInput.getAttribute('aria-describedby')).toBe('confirm-password-mismatch');
    expect(document.getElementById('confirm-password-mismatch')).toBeTruthy();
  });

  it('removes aria-describedby from the confirm input once passwords match', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Different1!' } });
    });
    await act(async () => {
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Password1!' } });
    });
    const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
    expect(confirmInput.getAttribute('aria-describedby')).toBeNull();
  });
});

describe('SignupPage — email format error aria attributes', () => {
  it('shows the email error paragraph with role="alert" and id="email-format-error" after blur with an invalid email', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-email-input'), {
        target: { value: 'notanemail' },
      });
      fireEvent.blur(screen.getByTestId('auth-email-input'));
    });
    const errorEl = document.getElementById('email-format-error');
    expect(errorEl).toBeTruthy();
    expect(errorEl.getAttribute('role')).toBe('alert');
    expect(errorEl.textContent).toBe('Please enter a valid email address');
  });

  it('sets aria-invalid and aria-describedby on the email input when the email is invalid', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-email-input'), {
        target: { value: 'bad-email' },
      });
      fireEvent.blur(screen.getByTestId('auth-email-input'));
    });
    const emailInput = screen.getByTestId('auth-email-input');
    expect(emailInput.getAttribute('aria-invalid')).toBe('true');
    expect(emailInput.getAttribute('aria-describedby')).toBe('email-format-error');
  });

  it('removes email error and aria attributes once a valid email is entered', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-email-input'), {
        target: { value: 'bad-email' },
      });
      fireEvent.blur(screen.getByTestId('auth-email-input'));
    });
    expect(document.getElementById('email-format-error')).toBeTruthy();
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-email-input'), {
        target: { value: 'valid@example.com' },
      });
      fireEvent.blur(screen.getByTestId('auth-email-input'));
    });
    expect(document.getElementById('email-format-error')).toBeNull();
    const emailInput = screen.getByTestId('auth-email-input');
    expect(emailInput.getAttribute('aria-invalid')).toBeNull();
    expect(emailInput.getAttribute('aria-describedby')).toBeNull();
  });

  it('blocks submit and shows the email error when submit is attempted with an invalid email', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-email-input'), {
        target: { value: 'notvalid' },
      });
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
      const confirmInput = screen.getAllByPlaceholderText('••••••••').slice(-1)[0];
      fireEvent.change(confirmInput, { target: { value: 'Password1!' } });
      fireEvent.click(screen.getByRole('button', { name: /agree to terms/i }));
      fireEvent.click(screen.getByRole('button', { name: /consent to data processing/i }));
    });
    await act(async () => {
      // Use fireEvent.submit on the form to bypass jsdom native email validation
      // so our custom handleSubmit guard is exercised
      fireEvent.submit(screen.getByTestId('auth-submit-button').closest('form'));
    });
    await act(async () => {});
    expect(document.getElementById('email-format-error')).toBeTruthy();
    expect(mockSignup).not.toHaveBeenCalled();
  });

  it('does not show an email error when no email has been entered yet', () => {
    render(<SignupPage />);
    expect(document.getElementById('email-format-error')).toBeNull();
    expect(screen.getByTestId('auth-email-input').getAttribute('aria-invalid')).toBeNull();
  });
});

describe('SignupPage — error banner aria attributes', () => {
  it('gives the error banner role="alert" and id="signup-error-message" when an error is shown', async () => {
    render(<SignupPage />);
    await fillForm({ password: 'Password1!', confirmPassword: 'Password1!', agreed: false, consentDpdp: true });
    const banner = document.getElementById('signup-error-message');
    expect(banner).toBeTruthy();
    expect(banner.getAttribute('role')).toBe('alert');
  });

  it('does not render signup-error-message element when there is no error', () => {
    render(<SignupPage />);
    expect(document.getElementById('signup-error-message')).toBeNull();
  });
});

describe('SignupPage — ToS button aria attributes on error', () => {
  it('sets aria-invalid and aria-describedby on the ToS button when the ToS error fires', async () => {
    render(<SignupPage />);
    await fillForm({ password: 'Password1!', confirmPassword: 'Password1!', agreed: false, consentDpdp: true });
    const tosBtn = screen.getByRole('button', { name: /agree to terms/i });
    expect(tosBtn.getAttribute('aria-invalid')).toBe('true');
    expect(tosBtn.getAttribute('aria-describedby')).toBe('signup-error-message');
  });

  it('removes aria-invalid from the ToS button after the error is resolved', async () => {
    mockSignup.mockResolvedValueOnce({ role: '', onboarding_done: false });
    render(<SignupPage />);
    // Trigger ToS error
    await fillForm({ password: 'Password1!', confirmPassword: 'Password1!', agreed: false, consentDpdp: true });
    expect(screen.getByRole('button', { name: /agree to terms/i }).getAttribute('aria-invalid')).toBe('true');
    // Fix: check Terms and resubmit successfully
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /agree to terms/i }));
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('auth-submit-button'));
    });
    await act(async () => {});
    expect(screen.getByRole('button', { name: /agree to terms/i }).getAttribute('aria-invalid')).toBeNull();
    expect(screen.getByRole('button', { name: /agree to terms/i }).getAttribute('aria-describedby')).toBeNull();
  });
});

describe('SignupPage — DPDP button aria attributes on error', () => {
  it('sets aria-invalid and aria-describedby on the DPDP button when the DPDP error fires', async () => {
    render(<SignupPage />);
    await fillForm({ password: 'Password1!', confirmPassword: 'Password1!', agreed: true, consentDpdp: false });
    const dpdpBtn = screen.getByRole('button', { name: /consent to data processing/i });
    expect(dpdpBtn.getAttribute('aria-invalid')).toBe('true');
    expect(dpdpBtn.getAttribute('aria-describedby')).toBe('signup-error-message');
  });

  it('removes aria-invalid from the DPDP button after the error is resolved', async () => {
    mockSignup.mockResolvedValueOnce({ role: '', onboarding_done: false });
    render(<SignupPage />);
    // Trigger DPDP error
    await fillForm({ password: 'Password1!', confirmPassword: 'Password1!', agreed: true, consentDpdp: false });
    expect(screen.getByRole('button', { name: /consent to data processing/i }).getAttribute('aria-invalid')).toBe('true');
    // Fix: check DPDP and resubmit successfully
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /consent to data processing/i }));
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('auth-submit-button'));
    });
    await act(async () => {});
    expect(screen.getByRole('button', { name: /consent to data processing/i }).getAttribute('aria-invalid')).toBeNull();
    expect(screen.getByRole('button', { name: /consent to data processing/i }).getAttribute('aria-describedby')).toBeNull();
  });
});

describe('SignupPage — password strength hint aria attributes', () => {
  it('gives the strength paragraph id="password-strength-hint" and aria-live="polite"', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
    });
    const hint = document.getElementById('password-strength-hint');
    expect(hint).toBeTruthy();
    expect(hint.getAttribute('aria-live')).toBe('polite');
  });

  it('links the password input to the strength hint via aria-describedby when password is non-empty', async () => {
    render(<SignupPage />);
    await act(async () => {
      fireEvent.change(screen.getByTestId('auth-password-input'), {
        target: { value: 'Password1!' },
      });
    });
    expect(screen.getByTestId('auth-password-input').getAttribute('aria-describedby')).toBe('password-strength-hint');
  });

  it('does not set aria-describedby on the password input when password is empty', () => {
    render(<SignupPage />);
    expect(screen.getByTestId('auth-password-input').getAttribute('aria-describedby')).toBeNull();
  });
});

describe('SignupPage — handleSubmit redirect logic', () => {
  it('navigates to /onboarding when user has onboarding_done=false', async () => {
    mockSignup.mockResolvedValueOnce({ role: '', onboarding_done: false });
    render(<SignupPage />);
    await triggerSignup();
    expect(toast.success).toHaveBeenCalledWith('Account created! Welcome to Syrabit.ai!');
    expect(mockNavigate).toHaveBeenCalledWith('/onboarding');
  });

  it('navigates to /staff when role is "staff"', async () => {
    mockSignup.mockResolvedValueOnce({ role: 'staff', onboarding_done: false });
    render(<SignupPage />);
    await triggerSignup();
    expect(mockNavigate).toHaveBeenCalledWith('/staff');
  });

  it('navigates to /staff when role is "admin"', async () => {
    mockSignup.mockResolvedValueOnce({ role: 'admin', onboarding_done: false });
    render(<SignupPage />);
    await triggerSignup();
    expect(mockNavigate).toHaveBeenCalledWith('/staff');
  });

  it('does not navigate and does not show success toast when signup throws', async () => {
    mockSignup.mockRejectedValueOnce(new Error('Email already in use'));
    render(<SignupPage />);
    await triggerSignup();
    expect(mockNavigate).not.toHaveBeenCalled();
    expect(toast.success).not.toHaveBeenCalled();
  });
});

describe('SignupPage — axe accessibility audit', () => {
  it('has no axe violations on the clean form (no error state)', async () => {
    const { container } = render(<SignupPage />);
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations when the ToS error is active', async () => {
    const { container } = render(<SignupPage />);
    await fillForm({ password: 'Password1!', confirmPassword: 'Password1!', agreed: false, consentDpdp: true });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('has no axe violations when the DPDP error is active', async () => {
    const { container } = render(<SignupPage />);
    await fillForm({ password: 'Password1!', confirmPassword: 'Password1!', agreed: true, consentDpdp: false });
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
