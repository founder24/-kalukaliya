import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { API_BASE } from '@/utils/api';
import { Analytics } from '@/utils/analytics';
import {
  hydrateAdsOptOutFromServer,
  setAdsUserPlan,
  setAdsAuthChecked,
} from '@/utils/adsConfig';
import {
  getInMemoryToken,
  getInMemoryRefreshToken,
  storeToken,
  storeRefreshToken,
  clearTokens,
  hydrateTokensFromStorage,
} from '@/hooks/useTokenManager';
import { silentRefresh } from '@/hooks/useAuthRefresh';
import { useAnonSync } from '@/hooks/useAnonSync';

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [authChecked, setAuthChecked] = useState(false);
  const justAuthenticated = useRef(false);

  const _storeToken = storeToken;
  const _storeRefreshToken = storeRefreshToken;

  const fetchMe = useCallback(async () => {
    let resolvedUserId = null;
    try {
      const token = getInMemoryToken();
      const headers = token
        ? { Authorization: `Bearer ${token}` }
        : {};
      let res;
      try {
        res = await axios.get(`${API_BASE}/users/me`, {
          withCredentials: true,
          headers,
        });
      } catch (err) {
        // Attempt silent refresh via the extracted helper
        res = await silentRefresh(err);
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
    const { savedToken } = hydrateTokensFromStorage();
    setLoading(false);
    if (savedToken) {
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

  // Task #592: Claim anonymous study data on sign-in using extracted hook
  useAnonSync(user?.id);

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
      storeToken(access_token);
      storeRefreshToken(refresh_token);
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
        { email, password, name, consent_dpdp },
        { withCredentials: true, headers },
      );
      const { access_token, refresh_token } = res.data;
      storeToken(access_token);
      storeRefreshToken(refresh_token);
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
      const token = getInMemoryToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      await axios.post(
        `${API_BASE}/auth/logout`,
        { refresh_token: getInMemoryRefreshToken() },
        { withCredentials: true, headers },
      );
    } catch {}
    clearTokens();
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
      token: getInMemoryToken(),
      loading,
      authChecked,
      login,
      signup,
      logout,
      refreshUser,
      updateUser,
      justAuthenticated,
      authHeader: getInMemoryToken() ? { Authorization: `Bearer ${getInMemoryToken()}` } : {},
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
