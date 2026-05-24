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

const AuthContext = createContext(null);

let _inMemoryToken = null;
let _inMemoryRefreshToken = null;

const getInMemoryToken = () => _inMemoryToken;

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [authChecked, setAuthChecked] = useState(false);
  const justAuthenticated = useRef(false);

  const _storeToken = (token) => {
    _inMemoryToken = token;
    setAuthToken(token);
    if (token) {
      sessionStorage.setItem('syrabit_token', token);
    } else {
      sessionStorage.removeItem('syrabit_token');
    }
  };

  const _storeRefreshToken = (token) => {
    _inMemoryRefreshToken = token;
    if (token) {
      sessionStorage.setItem('syrabit_refresh_token', token);
    } else {
      sessionStorage.removeItem('syrabit_refresh_token');
    }
  };

  const fetchMe = useCallback(async () => {
    let resolvedUserId = null;
    try {
      const headers = _inMemoryToken
        ? { Authorization: `Bearer ${_inMemoryToken}` }
        : {};
      let res;
      try {
        res = await axios.get(`${API_BASE}/users/me`, {
          withCredentials: true,
          headers,
        });
      } catch (err) {
        // If the server signals the access token has expired, attempt a
        // silent refresh via POST /auth/refresh. On success the server
        // issues a new access token; we re-try /users/me so the caller
        // sees a fully-hydrated user.
        const status = err?.response?.status;
        const detail = err?.response?.data?.detail;
        if (status === 401 && (detail === 'token_expired' || detail === 'jwt_expired')) {
          if (_inMemoryRefreshToken) {
            try {
              const refreshRes = await axios.post(
                `${API_BASE}/auth/refresh`,
                { refresh_token: _inMemoryRefreshToken },
                { withCredentials: true },
              );
              const newToken = refreshRes?.data?.access_token;
              const newRefresh = refreshRes?.data?.refresh_token;
              if (newToken) {
                _storeToken(newToken);
              }
              if (newRefresh) {
                _storeRefreshToken(newRefresh);
              }
              res = await axios.get(`${API_BASE}/users/me`, {
                withCredentials: true,
                headers: newToken ? { Authorization: `Bearer ${newToken}` } : {},
              });
            } catch {
              throw err;
            }
          } else {
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
        // below - prevents any ad flash for cookie-only paid users.
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
      // `/users/me` probe has resolved, regardless of whether the
      // visitor is anonymous or signed in. This guarantees a paid
      // subscriber on a cookie-only session never sees an ad flash
      // before their plan hydrates.
      setAdsAuthChecked(true);
    }
  }, []);

  useEffect(() => {
    const savedToken = sessionStorage.getItem('syrabit_token');
    const savedRefreshToken = sessionStorage.getItem('syrabit_refresh_token');
    if (savedRefreshToken) _inMemoryRefreshToken = savedRefreshToken;
    setLoading(false);
    if (savedToken) {
      _inMemoryToken = savedToken;
      setAuthToken(savedToken);
      // Returning logged-in user this session - fetch immediately so
      // user-gated UI (profile menu, credits) is correct on first paint.
      fetchMe();
      return;
    }
    // No in-memory token. Could be a brand-new anonymous visitor OR a
    // returning visitor whose only credential is an httpOnly cookie
    // (which we can't read from JS). The landing page UI does not need
    // user state for first paint - only the navbar login/profile
    // toggle does, and that can flip after LCP.
    //
    // So mark auth as checked synchronously (treat as anonymous) and
    // probe /users/me lazily after first paint. If a valid cookie
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
        // Silent - sync will be retried on next sign-in (no flag set).
      }
    })();
    return () => { cancelled = true; };
  }, [user?.id]);

  // Mirror the signed-in user's plan into the ads module so paying
  // subscribers (Starter / Pro) get an ad-free experience on Notes /
  // PYQ - Task #552. Reset to null on logout / anonymous so the gate
  // re-opens for downgraded sessions on the same browser tab.
  useEffect(() => {
    setAdsUserPlan(user?.plan ?? null);
  }, [user?.plan]);


  const login = async (email, password, turnstileToken) => {
    justAuthenticated.current = true;
    const headers = {};
    if (turnstileToken) headers['x-turnstile-token'] = turnstileToken;
    try {
      const res = await axios.post(
        `${API_BASE}/auth/login`,
        { email, password },
        { withCredentials: true, headers },
      );
      const { access_token, refresh_token } = res.data;
      _storeToken(access_token);
      _storeRefreshToken(refresh_token);
      // Fetch user profile immediately
      const profileRes = await axios.get(`${API_BASE}/users/me`, {
        headers: { Authorization: `Bearer ${access_token}` },
      });
      const userData = profileRes.data;
      setUser(userData);
      hydrateAdsOptOutFromServer(userData?.ads_opt_out);
      setAdsUserPlan(userData?.plan ?? null);
      try { Analytics.login(userData.id, userData.email); } catch {}
      return userData;
    } catch (err) {
      justAuthenticated.current = false;
      throw err;
    }
  };

  const signup = async (name, email, password, consent_dpdp = false, turnstileToken) => {
    justAuthenticated.current = true;
    const headers = {};
    if (turnstileToken) headers['x-turnstile-token'] = turnstileToken;
    try {
      const res = await axios.post(
        `${API_BASE}/auth/signup`,
        { email, password, name },
        { withCredentials: true, headers },
      );
      const { access_token, refresh_token } = res.data;
      _storeToken(access_token);
      _storeRefreshToken(refresh_token);
      // Fetch user profile immediately
      const profileRes = await axios.get(`${API_BASE}/users/me`, {
        headers: { Authorization: `Bearer ${access_token}` },
      });
      const userData = profileRes.data;
      setUser(userData);
      hydrateAdsOptOutFromServer(userData?.ads_opt_out);
      setAdsUserPlan(userData?.plan ?? null);
      try { Analytics.signup(userData.email, userData.plan); } catch {}
      return userData;
    } catch (err) {
      justAuthenticated.current = false;
      throw err;
    }
  };

  const logout = async () => {
    try {
      const headers = _inMemoryToken ? { Authorization: `Bearer ${_inMemoryToken}` } : {};
      await axios.post(
        `${API_BASE}/auth/logout`,
        { refresh_token: _inMemoryRefreshToken },
        { withCredentials: true, headers },
      );
    } catch {}
    _storeToken(null);
    _storeRefreshToken(null);
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
