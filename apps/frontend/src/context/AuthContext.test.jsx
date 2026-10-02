import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import axios from 'axios';
import { toast } from 'sonner';
import { silentRefresh } from '@/hooks/useAuthRefresh';
import { AuthProvider, useAuth } from './AuthContext';
import { AuthGuard } from '@/components/AuthGuard';
import { setAuthToken } from '@/utils/api';

const authState = vi.hoisted(() => ({
  accessToken: null,
  refreshToken: null,
  storeToken: vi.fn(),
  storeRefreshToken: vi.fn(),
  clearTokens: vi.fn(),
  toastWarning: vi.fn(),
}));

vi.mock('axios', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock('sonner', () => ({
  toast: { warning: authState.toastWarning },
}));

vi.mock('@/hooks/useTokenManager', () => ({
  getToken: () => authState.accessToken,
  getRefreshToken: () => authState.refreshToken,
  storeToken: (token) => {
    authState.storeToken(token);
    authState.accessToken = token;
  },
  storeRefreshToken: (token) => {
    authState.storeRefreshToken(token);
    authState.refreshToken = token;
  },
  clearTokens: () => {
    authState.clearTokens();
    authState.accessToken = null;
    authState.refreshToken = null;
  },
  hydrateTokensFromStorage: () => ({ hasToken: Boolean(authState.accessToken) }),
}));

vi.mock('@/utils/api', () => ({
  API_BASE: '/api/v1',
  setAuthToken: vi.fn(),
}));

vi.mock('@/utils/analytics', () => ({
  Analytics: {
    login: vi.fn(),
    signup: vi.fn(),
    logout: vi.fn(),
    track: vi.fn(),
  },
}));

vi.mock('@/utils/adsConfig', () => ({
  hydrateAdsOptOutFromServer: vi.fn(),
  setAdsAuthChecked: vi.fn(),
  setAdsPlan: vi.fn(),
}));

vi.mock('@/hooks/useAuthRefresh', () => ({
  silentRefresh: vi.fn(),
}));

vi.mock('@/hooks/useAnonSync', () => ({
  useAnonSync: vi.fn(),
}));

function SignupProbe() {
  const { signup } = useAuth();
  const [failureStage, setFailureStage] = useState('');
  return (
    <>
      <button
        type="button"
        onClick={() => signup('Test User', 'test@example.com', 'password', true)
          .catch((error) => setFailureStage(error.authStage))}
      >
        Create account
      </button>
      {failureStage && <div role="alert">{failureStage}</div>}
    </>
  );
}

function LogoutProbe() {
  const { logout } = useAuth();
  return (
    <button type="button" onClick={() => { void logout(); }}>
      Sign out
    </button>
  );
}

describe('AuthProvider authentication failure handling', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authState.accessToken = null;
    authState.refreshToken = null;
  });

  it('warns when server revocation fails while clearing local credentials', async () => {
    authState.accessToken = 'stored-access';
    authState.refreshToken = 'stored-refresh';
    axios.get.mockResolvedValueOnce({
      data: { id: 'user-1', email: 'student@example.com' },
    });
    axios.post.mockRejectedValueOnce({ response: { status: 503 } });

    render(
      <AuthProvider>
        <LogoutProbe />
      </AuthProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));

    await waitFor(() => expect(authState.clearTokens).toHaveBeenCalled());
    expect(authState.accessToken).toBeNull();
    expect(authState.refreshToken).toBeNull();
    expect(toast.warning).toHaveBeenCalledWith(
      expect.stringContaining('server-side session revocation could not be confirmed'),
      { duration: 8000 },
    );
  });

  it('does not persist signup tokens when the profile request fails', async () => {
    axios.post.mockResolvedValueOnce({
      data: { access_token: 'new-access', refresh_token: 'new-refresh' },
    });
    axios.get.mockRejectedValueOnce({ response: { status: 503 } });

    render(
      <AuthProvider>
        <SignupProbe />
      </AuthProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('profile');
    expect(authState.storeToken).not.toHaveBeenCalled();
    expect(authState.storeRefreshToken).not.toHaveBeenCalled();
    expect(authState.accessToken).toBeNull();
    expect(authState.refreshToken).toBeNull();
    expect(setAuthToken).not.toHaveBeenCalledWith('new-access');
  });

  it.each([
    ['network', { request: {} }],
    ['server', { response: { status: 503 } }],
  ])('keeps a stored session and offers a retry after a %s profile failure', async (_kind, error) => {
    authState.accessToken = 'stored-access';
    axios.get
      .mockRejectedValueOnce(error)
      .mockResolvedValueOnce({
        data: { id: 'user-1', email: 'student@example.com', onboarding_done: true },
      });

    render(
      <MemoryRouter initialEntries={['/private']}>
        <AuthProvider>
          <Routes>
            <Route
              path="/private"
              element={<AuthGuard><div>Private page</div></AuthGuard>}
            />
            <Route path="/login" element={<div>Login redirect</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('Your saved sign-in was kept');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument();
    expect(screen.queryByText('Login redirect')).not.toBeInTheDocument();
    expect(authState.accessToken).toBe('stored-access');
    expect(authState.clearTokens).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

    expect(await screen.findByText('Private page')).toBeInTheDocument();
    await waitFor(() => expect(axios.get).toHaveBeenCalledTimes(2));
    expect(authState.accessToken).toBe('stored-access');
    expect(authState.clearTokens).not.toHaveBeenCalled();
  });

  it('preserves the session when an expired access token cannot be refreshed temporarily', async () => {
    authState.accessToken = 'stored-access';
    authState.refreshToken = 'stored-refresh';
    axios.get
      .mockRejectedValueOnce({
        response: { status: 401, data: { detail: 'token_expired' } },
      })
      .mockResolvedValueOnce({
        data: { id: 'user-1', email: 'student@example.com', onboarding_done: true },
      });
    silentRefresh.mockRejectedValueOnce({ response: { status: 503 } });

    render(
      <MemoryRouter initialEntries={['/private']}>
        <AuthProvider>
          <Routes>
            <Route
              path="/private"
              element={<AuthGuard><div>Private page</div></AuthGuard>}
            />
            <Route path="/login" element={<div>Login redirect</div>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent('Your saved sign-in was kept');
    expect(screen.queryByText('Login redirect')).not.toBeInTheDocument();
    expect(authState.accessToken).toBe('stored-access');
    expect(authState.refreshToken).toBe('stored-refresh');
    expect(authState.clearTokens).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));

    expect(await screen.findByText('Private page')).toBeInTheDocument();
    expect(authState.accessToken).toBe('stored-access');
    expect(authState.refreshToken).toBe('stored-refresh');
  });
});