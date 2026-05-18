import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { API_BASE, setAuthToken } from '@/utils/api';
import { studyApi } from '@/utils/studyApi';
import { pinResetMarkNeeded } from '@/utils/pinReset';
import { Analytics } from '@/utils/analytics';
import {
  hydrateAdsOptOutFromServer,
  setAdsUserPlan,
  setAdsAuthChecked,
} from '@/utils/adsConfig';
import { supabase } from '@/lib/supabase';

const AuthContext = createContext(null);

let _inMemoryToken = null;

const getInMemoryToken = () => _inMemoryToken;

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [authChecked, setAuthChecked] = useState(false);
  const justAuthenticated = useRef(false);

  const fetchMe = useCallback(async () => {
    let resolvedUserId = null;
    try {
      const headers = _inMemoryToken
        ? { Authorization: `Bearer ${_inMemoryToken}` }
        : {};
      let res;
      try {
        res = await axios.get(`${API_BASE}/auth/me`, {
          withCredentials: true,
          headers,
        });
      } catch (err) {
        // If the server signals the access token has expired, attempt a
        // silent refresh via POST /auth/refresh (httpOnly refresh-cookie
        // flow). On success the server issues a new access token; we
        // re-try /auth/me so the caller sees a fully-hydrated user.
        const status = err?.response?.status;
        const detail = err?.response?.data?.detail;
        if (status === 401 && (detail === 'token_expired' || detail === 'jwt_expired')) {
          try {
            const refreshRes = await axios.post(
              `${API_BASE}/auth/refresh`,
              {},
              { withCredentials: true },
            );
            const newToken = refreshRes?.data?.access_token;
            if (newToken) {
              _inMemoryToken = newToken;
              setAuthToken(newToken);
              try { sessionStorage.setItem('syrabit_token', newToken); } catch {}
            }
            res = await axios.get(`${API_BASE}/auth/me`, {
              withCredentials: true,
              headers: newToken ? { Authorization: `Bearer ${newToken}` } : {},
            });
          } catch {
            throw err;
          }
        } else {
          throw err;
        }
      }
      const userData = res.data;
      if (userData && userData.id) {
        setUser(userData);
        resolvedUserId = userData.id;
        // Task #530: rehydrate the local ad opt-out flag from the
        // server so cookie-restored sessions and signed-in returning
        // users immediately apply their cross-device choice on every
        // ad-bearing route, not just /profile.
        hydrateAdsOptOutFromServer(userData.ads_opt_out);
        // Task #552: also mirror the resolved plan into the ads
        // module synchronously here, so the consent gate sees the
        // paid-plan flag in the same tick we open the ad-auth gate
        // below — prevents any ad flash for cookie-only paid users.
        setAdsUserPlan(userData.plan ?? null);
      } else {
        setUser(null);
        setAdsUserPlan(null);
      }
      justAuthenticated.current = false;
      return !!resolvedUserId;
    } catch {
      if (!justAuthenticated.current) {
        setUser(null);
        setAdsUserPlan(null);
      }
      return false;
    } finally {
      setAuthChecked(true);
      // Task #552: open the ad-auth gate only after the first
      // `/auth/me` probe has resolved, regardless of whether the
      // visitor is anonymous or signed in. This guarantees a paid
      // subscriber on a cookie-only session never sees an ad flash
      // before their plan hydrates — the no-token branch in the
      // mount effect defers the probe via `requestIdleCallback`, so
      // tying ads to `authChecked` (which flips true immediately in
      // that branch) would race the plan into existence.
      setAdsAuthChecked(true);
    }
  }, []);

  useEffect(() => {
    const savedToken = sessionStorage.getItem('syrabit_token');
    setLoading(false);
    if (savedToken) {
      _inMemoryToken = savedToken;
      setAuthToken(savedToken);
      // Returning logged-in user this session — fetch immediately so
      // user-gated UI (profile menu, credits) is correct on first paint.
      fetchMe();
      return;
    }
    // No in-memory token. Could be a brand-new anonymous visitor OR a
    // returning visitor whose only credential is an httpOnly cookie
    // (which we can't read from JS). The landing page UI does not need
    // user state for first paint — only the navbar login/profile
    // toggle does, and that can flip after LCP.
    //
    // So mark auth as checked synchronously (treat as anonymous) and
    // probe /auth/me lazily after first paint. If a valid cookie
    // exists, the navbar will hydrate to the logged-in state shortly
    // after; if not, no extra round-trip was paid.
    setAuthChecked(true);
    const probe = () => { fetchMe(); };
    if (typeof window !== 'undefined' && 'requestIdleCallback' in window) {
      window.requestIdleCallback(probe, { timeout: 1500 });
    } else {
      setTimeout(probe, 600);
    }
  }, [fetchMe]);

  // Task #592: once a user is signed in, claim any notes / flashcards /
  // strict-mode settings that were created against this device's anon
  // id while signed out, and surface a one-time confirmation toast so
  // the learner sees their offline study items have moved into the
  // account. The backend endpoint is idempotent (no-op on subsequent
  // calls because the anon rows have already moved), and the local
  // flag prevents repeating the network call across page loads.
  useEffect(() => {
    if (!user?.id) return;
    if (typeof window === 'undefined') return;
    let anonId = '';
    try { anonId = localStorage.getItem('syrabit_anon_id') || ''; } catch {}
    if (!anonId || anonId === user.id) return;
    // The backend is idempotent (zero-rows after the first successful
    // call), so it's safe to invoke on every sign-in. We only want to
    // avoid showing the same one-time toast twice in the same browser
    // session if React re-mounts this provider, which is what the
    // sessionStorage flag below guards against.
    const toastFlagKey = `syrabit:claimed_toast:${anonId}->${user.id}`;
    let cancelled = false;
    (async () => {
      try {
        const res = await studyApi.claimAnonData();
        if (cancelled) return;
        const moved = (res?.notes || 0) + (res?.flashcards || 0)
          + (res?.settings_merged ? 1 : 0);
        // Task #611: the PIN hash from the anonymous session is salted
        // with the device id and can no longer be verified once the
        // actor flips to the user. Persist a local flag so the
        // Guardian / Notebook / Flashcards pages can prompt the parent
        // to set a new PIN after sign-in.
        if (res?.pin_dropped) {
          try { pinResetMarkNeeded(); } catch {}
        }
        let alreadyToasted = false;
        try { alreadyToasted = !!sessionStorage.getItem(toastFlagKey); } catch {}
        if (moved > 0 && !alreadyToasted) {
          try { sessionStorage.setItem(toastFlagKey, '1'); } catch {}
          const parts = [];
          if (res.notes) parts.push(`${res.notes} note${res.notes === 1 ? '' : 's'}`);
          if (res.flashcards) parts.push(`${res.flashcards} flashcard${res.flashcards === 1 ? '' : 's'}`);
          const detail = parts.length
            ? ` (${parts.join(' & ')})`
            : '';
          try {
            toast.success(`Your offline study items are now synced to your account${detail}.`);
          } catch {}
        }
      } catch {
        // Silent — sync will be retried on next sign-in (no flag set).
      }
    })();
    return () => { cancelled = true; };
  }, [user?.id]);

  // Mirror the signed-in user's plan into the ads module so paying
  // subscribers (Starter / Pro) get an ad-free experience on Notes /
  // PYQ — Task #552. Reset to null on logout / anonymous so the gate
  // re-opens for downgraded sessions on the same browser tab.
  useEffect(() => {
    setAdsUserPlan(user?.plan ?? null);
  }, [user?.plan]);


  const _storeToken = (token) => {
    _inMemoryToken = token;
    setAuthToken(token);
    if (token) {
      sessionStorage.setItem('syrabit_token', token);
    } else {
      sessionStorage.removeItem('syrabit_token');
    }
  };

  // Task #404 — forward Cloudflare Turnstile token (collected by
  // <TurnstileWidget /> on the public auth forms) via the canonical
  // ``x-turnstile-token`` header. The backend ``require_turnstile``
  // dependency reads this header and (when ``TURNSTILE_ON`` is on)
  // calls Cloudflare ``siteverify``. When the flag is off the header
  // is harmlessly ignored, so this is safe to ship before the flip.
  const _exchangeSupabaseSession = async (
    supabaseToken, name, consent_dpdp, turnstileToken,
  ) => {
    const body = { supabase_token: supabaseToken };
    if (name) body.name = name;
    if (consent_dpdp) body.consent_dpdp = consent_dpdp;
    const headers = {};
    if (turnstileToken) headers['x-turnstile-token'] = turnstileToken;
    const res = await axios.post(
      `${API_BASE}/auth/supabase-session`,
      body,
      { withCredentials: true, headers },
    );
    const { user: userData, access_token } = res.data;
    if (access_token) _storeToken(access_token);
    justAuthenticated.current = true;
    setUser(userData);
    hydrateAdsOptOutFromServer(userData?.ads_opt_out);
    return userData;
  };

  const login = async (email, password, turnstileToken) => {
    if (!supabase) throw new Error('Authentication is not configured.');
    // Mark as direct-auth BEFORE the Supabase call so onAuthStateChange
    // (which fires during the await) sees the flag and skips its own exchange.
    justAuthenticated.current = true;
    const { data: sbData, error: sbError } = await supabase.auth.signInWithPassword({ email, password });
    if (sbError) {
      justAuthenticated.current = false;
      const err = new Error(sbError.message);
      err.response = { data: { detail: sbError.message } };
      throw err;
    }
    if (!sbData?.session?.access_token) {
      justAuthenticated.current = false;
      throw new Error('No session returned from Supabase');
    }
    const userData = await _exchangeSupabaseSession(
      sbData.session.access_token, null, false, turnstileToken,
    );
    try { Analytics.login(userData.id, userData.email); } catch {}
    return userData;
  };

  const signup = async (name, email, password, consent_dpdp = false, turnstileToken) => {
    if (!supabase) throw new Error('Authentication is not configured.');
    // Same guard as login() — set before the Supabase call.
    justAuthenticated.current = true;
    const { data: sbData, error: sbError } = await supabase.auth.signUp({
      email,
      password,
      options: { data: { full_name: name } },
    });
    if (sbError) {
      justAuthenticated.current = false;
      const err = new Error(sbError.message);
      err.response = { data: { detail: sbError.message } };
      throw err;
    }
    if (!sbData?.session?.access_token) {
      // Supabase requires email confirmation — no session yet.
      // Clear the flag so the onAuthStateChange handler will pick up the
      // SIGNED_IN event when the user clicks their confirmation link.
      justAuthenticated.current = false;
      const confirmErr = new Error('EMAIL_CONFIRMATION_REQUIRED');
      confirmErr.email = email;
      throw confirmErr;
    }
    const userData = await _exchangeSupabaseSession(
      sbData.session.access_token, name, consent_dpdp, turnstileToken,
    );
    try { Analytics.signup(userData.email, userData.plan); } catch {}
    return userData;
  };

  // Task #156 / fix — Handle ALL Supabase SIGNED_IN events, not just Google.
  //
  // Covers:
  //   • Google OAuth redirect callback (provider='google')
  //   • Email-confirmation link callback (provider='email', no login() call)
  //   • Magic-link / future OAuth providers
  //
  // login() and signup() set justAuthenticated.current = true BEFORE their
  // Supabase call, so when the SIGNED_IN event fires for a direct
  // email/password flow, this handler detects the flag and exits early —
  // avoiding a redundant second round-trip to /api/auth/supabase-session.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!supabase) return;
    const { data: { subscription } } = supabase.auth.onAuthStateChange(
      async (event, session) => {
        if (event !== 'SIGNED_IN') return;
        // login() / signup() are handling this — skip to avoid double exchange.
        if (justAuthenticated.current) return;
        if (!session?.access_token) return;
        try {
          const userData = await _exchangeSupabaseSession(session.access_token);
          try { Analytics.login(userData.id, userData.email); } catch {}
        } catch (err) {
          console.error('[Auth] Token exchange failed:', err);
        }
      },
    );
    return () => subscription.unsubscribe();
  }, []); // empty — justAuthenticated ref + _exchangeSupabaseSession are stable

  const logout = async () => {
    try {
      await axios.post(`${API_BASE}/auth/logout`, {}, { withCredentials: true });
    } catch {}
    try { if (supabase) await supabase.auth.signOut(); } catch {}
    _storeToken(null);
    justAuthenticated.current = false;
    localStorage.removeItem('syrabit:onboarding');
    setUser(null);
    try { Analytics.logout(); } catch {}
  };

  const refreshUser = async () => {
    return await fetchMe();
  };

  const updateUser = useCallback((updates) => {
    setUser((prev) => (prev ? { ...prev, ...updates } : prev));
  }, []);

  return (
    <AuthContext.Provider value={{
      user,
      token: _inMemoryToken,
      loading,
      authChecked,
      login,
      signup,
      logout,
      refreshUser,
      updateUser,
      justAuthenticated,
      authHeader: _inMemoryToken ? { Authorization: `Bearer ${_inMemoryToken}` } : {},
      API: API_BASE,
    }}>
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be inside AuthProvider');
  return ctx;
};
