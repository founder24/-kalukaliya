/**
 * GoogleSignInButton — Task #156
 *
 * Uses Supabase OAuth (supabase.auth.signInWithOAuth) instead of the
 * deprecated Google Identity Services (GIS) flow.  Clicking the button
 * triggers a redirect to Google → Supabase → back to this origin.
 * After the redirect, AuthContext.onAuthStateChange picks up the SIGNED_IN
 * event, exchanges the Supabase token at /api/auth/supabase-session, and
 * sets the user.  The root-level GoogleOAuthCallbackEffect (Task #169)
 * reads the intent from sessionStorage and handles post-login navigation
 * for any page that renders this button, not just LoginPage/SignupPage.
 *
 * Props:
 *   text     — 'signin_with' (default) | 'signup_with'
 *   onError  — called if the OAuth redirect itself fails to initiate
 *   disabled — disables the button
 */
import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { supabase } from '@/lib/supabase';

const INTENT_KEY = 'syrabit_google_oauth_intent';

export default function GoogleSignInButton({
  text = 'signin_with',
  onError,
  disabled = false,
}) {
  const [busy, setBusy] = useState(false);

  const handleClick = async () => {
    if (busy || disabled) return;
    setBusy(true);
    try {
      sessionStorage.setItem(INTENT_KEY, text);
      if (!supabase) throw new Error('Authentication is not configured.');
      const { error } = await supabase.auth.signInWithOAuth({
        provider: 'google',
        options: {
          redirectTo: window.location.origin,
        },
      });
      if (error) {
        sessionStorage.removeItem(INTENT_KEY);
        onError?.(error);
        setBusy(false);
      }
    } catch (err) {
      sessionStorage.removeItem(INTENT_KEY);
      onError?.(err);
      setBusy(false);
    }
  };

  const label = text === 'signup_with' ? 'Sign up with Google' : 'Sign in with Google';

  return (
    <div className="relative w-full flex justify-center">
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled || busy}
        className="w-full inline-flex items-center justify-center gap-3 rounded-lg border border-border bg-background px-4 py-2.5 text-sm font-medium text-foreground shadow-sm hover:bg-muted/40 active:bg-muted disabled:opacity-60 transition-colors"
        style={{ minHeight: 44 }}
        aria-label={label}
      >
        {busy ? (
          <Loader2 size={16} className="animate-spin shrink-0" />
        ) : (
          <GoogleColorIcon />
        )}
        {busy ? 'Redirecting to Google…' : label}
      </button>
    </div>
  );
}

function GoogleColorIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 18 18"
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="#4285F4"
        d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615Z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18Z"
      />
      <path
        fill="#FBBC05"
        d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332Z"
      />
      <path
        fill="#EA4335"
        d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 6.29C4.672 4.163 6.656 3.58 9 3.58Z"
      />
    </svg>
  );
}

export { INTENT_KEY as GOOGLE_OAUTH_INTENT_KEY };
