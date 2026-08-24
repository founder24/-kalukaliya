import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import axios from 'axios';
import { toast } from 'sonner';
import ResetPasswordPage from './ResetPasswordPage';

vi.mock('axios');
vi.mock('react-router-dom', () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useSearchParams: () => [new URLSearchParams()],
}));
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));
vi.mock('@/components/Logo', () => ({
  LogoFull: () => <div data-testid="logo" />,
}));
vi.mock('@/utils/api', () => ({ API_BASE: '/api/v1' }));
vi.mock('@/lib/authErrors', () => ({
  formatAuthError: (_error, fallback) => fallback,
}));

describe('ResetPasswordPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('uses the native Worker password-reset request and confirmation contracts', async () => {
    axios.post.mockResolvedValueOnce({ data: {} }).mockResolvedValueOnce({ data: {} });
    render(<ResetPasswordPage />);

    fireEvent.change(screen.getByPlaceholderText('your@email.com'), {
      target: { value: 'student@example.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Send Reset Link' }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenCalledWith(
        '/api/v1/auth/reset-password/request',
        { email: 'student@example.com' },
      );
    });

    fireEvent.change(screen.getByPlaceholderText('Paste your reset token'), {
      target: { value: 'reset-token' },
    });
    fireEvent.change(screen.getByPlaceholderText('••••••••'), {
      target: { value: 'new-password' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Update Password' }));

    await waitFor(() => {
      expect(axios.post).toHaveBeenLastCalledWith(
        '/api/v1/auth/reset-password/confirm',
        { token: 'reset-token', password: 'new-password' },
      );
    });
    expect(toast.success).toHaveBeenCalledWith('Password updated!');
  });
});