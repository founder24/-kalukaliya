/**
 * GoogleOAuthCallbackEffect — Task #169 / auth-fix
 *
 * Handles post-OAuth/confirmation navigation for the entire app.
 *
 * Covered flows:
 *   1. Google OAuth redirect — GoogleSignInButton stores an intent key in
 *      sessionStorage before the redirect.  When user + intent are present,
 *      we navigate to the appropriate destination.
 *   2. Email-confirmation link — Supabase fires SIGNED_IN after PKCE code
 *      exchange; AuthContext now exchanges the token for all providers, sets
 *      `user`, but doesn't navigate (no intent key).  We detect this by
 *      checking for the SYRABIT_EMAIL_CONFIRM_KEY flag that AuthContext
 *      writes after a successful email-confirmation exchange.
 *   3. Email/password sign-ins — handled directly in LoginPage/SignupPage;
 *      no intent key, no confirmation flag → this effect is a no-op.
 */
import { useEffect, useRef } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { toast } from 'sonner';
import { useAuth } from '@/context/AuthContext';
import { GOOGLE_OAUTH_INTENT_KEY } from '@/components/GoogleSignInButton';

const EMAIL_CONFIRM_NAV_KEY = 'syrabit_email_confirm_nav';

export default function GoogleOAuthCallbackEffect() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const handledRef = useRef(false);

  useEffect(() => {
    if (!user) return;
    if (handledRef.current) return;

    const role = user.role || '';

    // ── Flow 1: Google OAuth intent key ────────────────────────────────────
    let intent;
    try { intent = sessionStorage.getItem(GOOGLE_OAUTH_INTENT_KEY); } catch {}
    if (intent === 'signin_with' || intent === 'signup_with') {
      handledRef.current = true;
      try { sessionStorage.removeItem(GOOGLE_OAUTH_INTENT_KEY); } catch {}
      if (intent === 'signup_with') {
        toast.success('Account created! Welcome to Syrabit.ai!');
        navigate('/onboarding', { replace: true });
      } else {
        toast.success('Welcome back!');
        if (role === 'staff' || role === 'admin') {
          navigate('/staff', { replace: true });
        } else if (!user.onboarding_done) {
          navigate('/onboarding', { replace: true });
        } else {
          navigate('/library', { replace: true });
        }
      }
      return;
    }

    // ── Flow 2: Email-confirmation PKCE callback ────────────────────────────
    // After the user clicks the confirmation link, Supabase redirects to the
    // app root with ?code= in the URL.  AuthContext's onAuthStateChange
    // exchanges the token and sets user.  We detect this case by checking
    // whether the current URL contains a Supabase auth code param AND we're
    // at the root (no explicit nav intent exists).  A one-time sessionStorage
    // flag guards against re-triggering on subsequent renders.
    const searchParams = new URLSearchParams(location.search);
    const hasSupabaseCode = searchParams.has('code') || searchParams.has('access_token');
    const confirmFlag = (() => { try { return sessionStorage.getItem(EMAIL_CONFIRM_NAV_KEY); } catch { return null; } })();

    if (hasSupabaseCode && !confirmFlag) {
      handledRef.current = true;
      try { sessionStorage.setItem(EMAIL_CONFIRM_NAV_KEY, '1'); } catch {}
      toast.success('Email confirmed! Welcome to Syrabit.ai!');
      if (role === 'staff' || role === 'admin') {
        navigate('/staff', { replace: true });
      } else if (!user.onboarding_done) {
        navigate('/onboarding', { replace: true });
      } else {
        navigate('/library', { replace: true });
      }
    }
  }, [user, navigate, location.search]);

  return null;
}
