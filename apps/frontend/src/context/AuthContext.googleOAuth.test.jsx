/**
 * Task #173 — AuthContext: onAuthStateChange Google OAuth handler tests.
 *
 * Verifies that when Supabase fires a SIGNED_IN event with provider='google',
 * AuthContext calls /api/auth/supabase-session and resolves the user — which
 * in turn allows GoogleOAuthCallbackEffect to navigate to the correct page.
 *
 * Email/password SIGNED_IN events (provider='email') must be ignored here
 * because they go through login()/signup() directly.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act, waitFor } from '@testing-library/react';
import React from 'react';

let capturedAuthCallback = null;
const mockUnsubscribe = vi.fn();

vi.mock('@/lib/supabase', () => ({
  supabase: {
    auth: {
      onAuthStateChange: vi.fn((cb) => {
        capturedAuthCallback = cb;
        return { data: { subscription: { unsubscribe: mockUnsubscribe } } };
      }),
      signOut: vi.fn(() => Promise.resolve({})),
    },
  },
}));

const mockAxiosGet = vi.fn();
const mockAxiosPost = vi.fn();

vi.mock('axios', () => ({
  default: {
    get: (...args) => mockAxiosGet(...args),
    post: (...args) => mockAxiosPost(...args),
  },
}));

vi.mock('@/utils/api', () => ({
  API_BASE: 'http://localhost:8000',
  setAuthToken: vi.fn(),
}));

vi.mock('@/utils/studyApi', () => ({
  studyApi: { claimAnonData: vi.fn(() => Promise.resolve({ notes: 0, flashcards: 0 })) },
}));

vi.mock('@/utils/pinReset', () => ({ pinResetMarkNeeded: vi.fn() }));

vi.mock('@/utils/analytics', () => ({
  Analytics: { login: vi.fn(), signup: vi.fn(), logout: vi.fn() },
}));

vi.mock('@/utils/adsConfig', () => ({
  hydrateAdsOptOutFromServer: vi.fn(),
  setAdsUserPlan: vi.fn(),
  setAdsAuthChecked: vi.fn(),
}));

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { AuthProvider, useAuth } from './AuthContext';

function UserSpy({ onUser }) {
  const { user, authChecked } = useAuth();
  React.useEffect(() => { if (authChecked) onUser(user); }, [authChecked, user, onUser]);
  return null;
}

function makeGoogleSession(overrides = {}) {
  return {
    access_token: 'sb-google-token',
    user: {
      app_metadata: { provider: 'google' },
      ...overrides,
    },
  };
}

beforeEach(() => {
  capturedAuthCallback = null;
  mockUnsubscribe.mockClear();
  mockAxiosGet.mockClear();
  mockAxiosGet.mockResolvedValue({ data: {} });
  mockAxiosPost.mockClear();
  mockAxiosPost.mockResolvedValue({ data: {} });
  sessionStorage.clear();
});

afterEach(() => {
  sessionStorage.clear();
});

describe('AuthContext — onAuthStateChange Google OAuth handler', () => {
  it('calls /api/auth/supabase-session when Supabase fires SIGNED_IN with provider=google', async () => {
    const newUser = {
      id: 'u1',
      email: 'newuser@example.com',
      plan: 'free',
      onboarding_done: false,
    };
    mockAxiosPost.mockResolvedValueOnce({
      data: { user: newUser, access_token: 'custom-jwt' },
    });

    render(
      <AuthProvider>
        <div />
      </AuthProvider>,
    );

    await waitFor(() => expect(capturedAuthCallback).not.toBeNull());

    await act(async () => {
      await capturedAuthCallback('SIGNED_IN', makeGoogleSession());
    });

    expect(mockAxiosPost).toHaveBeenCalledWith(
      expect.stringContaining('/auth/supabase-session'),
      expect.objectContaining({ supabase_token: 'sb-google-token' }),
      expect.objectContaining({ withCredentials: true }),
    );
  });

  it('does NOT call /api/auth/supabase-session for provider=email (email/password handles its own exchange)', async () => {
    mockAxiosPost.mockResolvedValue({ data: {} });

    render(
      <AuthProvider>
        <div />
      </AuthProvider>,
    );

    await waitFor(() => expect(capturedAuthCallback).not.toBeNull());

    await act(async () => {
      await capturedAuthCallback('SIGNED_IN', {
        access_token: 'sb-email-token',
        user: { app_metadata: { provider: 'email' } },
      });
    });

    expect(mockAxiosPost).not.toHaveBeenCalledWith(
      expect.stringContaining('/auth/supabase-session'),
      expect.anything(),
      expect.anything(),
    );
  });

  it('does NOT call /api/auth/supabase-session for non-SIGNED_IN events (e.g. TOKEN_REFRESHED)', async () => {
    render(
      <AuthProvider>
        <div />
      </AuthProvider>,
    );

    await waitFor(() => expect(capturedAuthCallback).not.toBeNull());

    await act(async () => {
      await capturedAuthCallback('TOKEN_REFRESHED', makeGoogleSession());
    });

    expect(mockAxiosPost).not.toHaveBeenCalledWith(
      expect.stringContaining('/auth/supabase-session'),
      expect.anything(),
      expect.anything(),
    );
  });

  it('sets user state after successful Google token exchange', async () => {
    const returningUser = {
      id: 'u2',
      email: 'returning@example.com',
      plan: 'starter',
      onboarding_done: true,
    };
    mockAxiosPost.mockResolvedValueOnce({
      data: { user: returningUser, access_token: 'custom-jwt' },
    });

    const onUser = vi.fn();
    render(
      <AuthProvider>
        <UserSpy onUser={onUser} />
      </AuthProvider>,
    );

    await waitFor(() => expect(capturedAuthCallback).not.toBeNull());

    await act(async () => {
      await capturedAuthCallback('SIGNED_IN', makeGoogleSession());
    });

    await waitFor(() =>
      expect(onUser).toHaveBeenCalledWith(
        expect.objectContaining({ email: 'returning@example.com', onboarding_done: true }),
      ),
    );
  });
});
