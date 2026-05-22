/**
 * Task #173 — GoogleOAuthCallbackEffect redirect tests.
 *
 * After the Google OAuth callback lands, AuthContext fires onAuthStateChange
 * which exchanges the Supabase token and sets `user`.  This component watches
 * `user` + the intent key in sessionStorage and calls navigate() to send the
 * user to the correct page:
 *
 *   signin_with + onboarding_done=false  →  /onboarding
 *   signin_with + onboarding_done=true   →  /library
 *   signup_with (any onboarding_done)    →  /onboarding
 *   staff / admin role                   →  /staff
 *   no intent key present                →  no navigation (non-Google sign-in)
 *
 * The real GOOGLE_OAUTH_INTENT_KEY constant is imported from GoogleSignInButton
 * so that any change to the key string in production is automatically caught
 * here rather than silently allowing the test to pass with a stale value.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, act } from '@testing-library/react';
import React from 'react';

const mockNavigate = vi.fn();

vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('@/lib/supabase', () => ({
  supabase: {
    auth: {
      onAuthStateChange: vi.fn(() => ({
        data: { subscription: { unsubscribe: vi.fn() } },
      })),
      signOut: vi.fn(() => Promise.resolve({})),
    },
  },
}));

vi.mock('lucide-react', () => ({
  Loader2: () => null,
}));

let mockUser = null;

vi.mock('@/context/AuthContext', () => ({
  useAuth: () => ({ user: mockUser }),
}));

import { GOOGLE_OAUTH_INTENT_KEY } from '@/components/GoogleSignInButton';
import GoogleOAuthCallbackEffect from './GoogleOAuthCallbackEffect';

function renderEffect() {
  return render(<GoogleOAuthCallbackEffect />);
}

beforeEach(() => {
  mockNavigate.mockClear();
  sessionStorage.clear();
  mockUser = null;
});

afterEach(() => {
  sessionStorage.clear();
});

describe('GoogleOAuthCallbackEffect — post-Google-OAuth redirect', () => {
  describe('intent = signin_with', () => {
    it('redirects a first-time Google user (onboarding_done=false) to /onboarding', async () => {
      sessionStorage.setItem(GOOGLE_OAUTH_INTENT_KEY, 'signin_with');
      mockUser = { id: 'u1', email: 'new@example.com', onboarding_done: false, role: 'user' };

      await act(async () => { renderEffect(); });

      expect(mockNavigate).toHaveBeenCalledOnce();
      expect(mockNavigate).toHaveBeenCalledWith('/onboarding', { replace: true });
    });

    it('redirects a returning Google user (onboarding_done=true) to /library', async () => {
      sessionStorage.setItem(GOOGLE_OAUTH_INTENT_KEY, 'signin_with');
      mockUser = { id: 'u2', email: 'returning@example.com', onboarding_done: true, role: 'user' };

      await act(async () => { renderEffect(); });

      expect(mockNavigate).toHaveBeenCalledOnce();
      expect(mockNavigate).toHaveBeenCalledWith('/library', { replace: true });
    });

    it('redirects a staff user to /staff regardless of onboarding_done', async () => {
      sessionStorage.setItem(GOOGLE_OAUTH_INTENT_KEY, 'signin_with');
      mockUser = { id: 'u3', email: 'staff@example.com', onboarding_done: true, role: 'staff' };

      await act(async () => { renderEffect(); });

      expect(mockNavigate).toHaveBeenCalledOnce();
      expect(mockNavigate).toHaveBeenCalledWith('/staff', { replace: true });
    });

    it('redirects an admin user to /staff', async () => {
      sessionStorage.setItem(GOOGLE_OAUTH_INTENT_KEY, 'signin_with');
      mockUser = { id: 'u4', email: 'admin@example.com', onboarding_done: true, role: 'admin' };

      await act(async () => { renderEffect(); });

      expect(mockNavigate).toHaveBeenCalledOnce();
      expect(mockNavigate).toHaveBeenCalledWith('/staff', { replace: true });
    });
  });

  describe('intent = signup_with', () => {
    it('always redirects to /onboarding for signup regardless of onboarding_done', async () => {
      sessionStorage.setItem(GOOGLE_OAUTH_INTENT_KEY, 'signup_with');
      mockUser = { id: 'u5', email: 'newgoogle@example.com', onboarding_done: false, role: 'user' };

      await act(async () => { renderEffect(); });

      expect(mockNavigate).toHaveBeenCalledOnce();
      expect(mockNavigate).toHaveBeenCalledWith('/onboarding', { replace: true });
    });

    it('redirects to /onboarding even when onboarding_done=true (e.g. re-signup attempt)', async () => {
      sessionStorage.setItem(GOOGLE_OAUTH_INTENT_KEY, 'signup_with');
      mockUser = { id: 'u6', email: 'resigning@example.com', onboarding_done: true, role: 'user' };

      await act(async () => { renderEffect(); });

      expect(mockNavigate).toHaveBeenCalledOnce();
      expect(mockNavigate).toHaveBeenCalledWith('/onboarding', { replace: true });
    });
  });

  describe('no intent key (non-Google sign-in)', () => {
    it('does not navigate when the intent key is absent', async () => {
      mockUser = { id: 'u7', email: 'emailuser@example.com', onboarding_done: true, role: 'user' };

      await act(async () => { renderEffect(); });

      expect(mockNavigate).not.toHaveBeenCalled();
    });

    it('does not navigate when user is null (auth not yet resolved)', async () => {
      sessionStorage.setItem(GOOGLE_OAUTH_INTENT_KEY, 'signin_with');
      mockUser = null;

      await act(async () => { renderEffect(); });

      expect(mockNavigate).not.toHaveBeenCalled();
    });
  });

  describe('sessionStorage cleanup', () => {
    it('removes the intent key after redirect so subsequent renders do not re-navigate', async () => {
      sessionStorage.setItem(GOOGLE_OAUTH_INTENT_KEY, 'signin_with');
      mockUser = { id: 'u8', email: 'user@example.com', onboarding_done: true, role: 'user' };

      await act(async () => { renderEffect(); });

      expect(sessionStorage.getItem(GOOGLE_OAUTH_INTENT_KEY)).toBeNull();
    });
  });
});
