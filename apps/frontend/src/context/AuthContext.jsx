import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { API_BASE } from '@/utils/api';
import { Analytics } from '@/utils/analytics';
import { useTokenManager, getInMemoryToken, getInMemoryRefreshToken } from '@/hooks/useTokenManager';
import { useAnonymousSync } from '@/hooks/useAnonymousSync';
import { useAdsSync, hydrateAdsFromUser } from '@/hooks/useAdsSync';
import { setAdsAuthChecked } from '@/utils/adsConfig';

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const [authChecked, setAuthChecked] = useState(false);
  const justAuthenticated = useRef(false);
  const { storeToken, storeRefreshToken, clearTokens, hydrateFromSession } = useTokenManager();

  useAnonymousSync(user?.id);
  useAdsSync(user, authChecked);

  const fetchMe = useCallback(async () => {
    let resolvedUserId = null;
    try {
      const token = getInMemoryToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      let res;
      try {
        res = await axios.get(`${API_BASE}/users/me`, { withCredentials: true, headers });
      } catch (err) {
        const status = err?.response?.status;
        const detail = err?.response?.data?.detail;
        if (status === 401 && (detail === 'token_expired' || detail === 'jwt_expired')) {
          const refreshToken = getInMemoryRefreshToken();
          if (refreshToken) {
            try {
              const refreshRes = await axios.post(
                `${API_BASE}/auth/refresh`,
                { refresh_token: refreshToken },
                { withCredentials: true },
              );
              if (refreshRes?.data?.access_token) storeToken(refreshRes.data.access_token);
              if (refreshRes?.data?.refresh_token) storeRefreshToken(refreshRes.data.refresh_token);
              res = await axios.get(`${API_BASE}/users/me`, {
                withCredentials: true,
                headers: refreshRes?.data?.access_token
                  ? { Authorization: `Bearer ${refreshRes.data.access_token}` } : {},
              });
            } catch { throw err; }
          } else { throw err; }
        } else { throw err; }
      }
      const userData = res.data;
      if (userData && userData.id) {
        setUser(userData);
        resolvedUserId = userData.id;
        hydrateAdsFromUser(userData);
      } else {
        setUser(null);
        hydrateAdsFromUser(null);
      }
      justAuthenticated.current = false;
      return !!resolvedUserId;
    } catch {
      if (!justAuthenticated.current) { setUser(null); hydrateAdsFromUser(null); }
      return false;
    } finally {
      setAuthChecked(true);
      setAdsAuthChecked(true);
    }
  }, [storeToken, storeRefreshToken]);

  useEffect(() => {
    const { hasToken } = hydrateFromSession();
    setLoading(false);
    if (hasToken) { fetchMe(); return; }
    setAuthChecked(true);
    const probe = () => { fetchMe(); };
    if (typeof window !== 'undefined' && 'requestIdleCallback' in window) {
      window.requestIdleCallback(probe, { timeout: 1500 });
    } else { setTimeout(probe, 600); }
  }, [fetchMe, hydrateFromSession]);

  const login = async (email, password, turnstileToken) => {
    justAuthenticated.current = true;
    const headers = {};
    if (turnstileToken) headers['x-turnstile-token'] = turnstileToken;
    try {
      const res = await axios.post(`${API_BASE}/auth/login`, { email, password },
        { withCredentials: true, headers });
      storeToken(res.data.access_token);
      storeRefreshToken(res.data.refresh_token);
      const profileRes = await axios.get(`${API_BASE}/users/me`, {
        headers: { Authorization: `Bearer ${res.data.access_token}` },
      });
      setUser(profileRes.data);
      hydrateAdsFromUser(profileRes.data);
      try { Analytics.login(profileRes.data.id, profileRes.data.email); } catch {}
      return profileRes.data;
    } catch (err) { justAuthenticated.current = false; throw err; }
  };

  const signup = async (name, email, password, consent_dpdp = false, turnstileToken) => {
    justAuthenticated.current = true;
    const headers = {};
    if (turnstileToken) headers['x-turnstile-token'] = turnstileToken;
    try {
      const res = await axios.post(`${API_BASE}/auth/signup`,
        { email, password, name, consent_dpdp },
        { withCredentials: true, headers });
      storeToken(res.data.access_token);
      storeRefreshToken(res.data.refresh_token);
      const profileRes = await axios.get(`${API_BASE}/users/me`, {
        headers: { Authorization: `Bearer ${res.data.access_token}` },
      });
      setUser(profileRes.data);
      hydrateAdsFromUser(profileRes.data);
      try { Analytics.signup(profileRes.data.email, profileRes.data.plan); } catch {}
      return profileRes.data;
    } catch (err) { justAuthenticated.current = false; throw err; }
  };

  const logout = async () => {
    try {
      const token = getInMemoryToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      await axios.post(`${API_BASE}/auth/logout`,
        { refresh_token: getInMemoryRefreshToken() },
        { withCredentials: true, headers });
    } catch {}
    clearTokens();
    justAuthenticated.current = false;
    localStorage.removeItem('syrabit:onboarding');
    setUser(null);
    try { Analytics.logout(); } catch {}
  };

  const refreshUser = async () => await fetchMe();

  const updateUser = useCallback((updates) => {
    setUser((prev) => (prev ? { ...prev, ...updates } : prev));
  }, []);

  return (
    <AuthContext.Provider value={{
      user, token: getInMemoryToken(), loading, authChecked,
      login, signup, logout, refreshUser, updateUser,
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
