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
  getToken,
  getRefreshToken,
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

  const fetchMe = useCallback(async () => {
    let resolvedUserId = null;
    try {
      const token = getToken();
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
        const status = err?.response?.status;
        const detail = err?.response?.data?.detail;
        if (status === 401 && (detail === 'token_expired' || detail === 'jwt_expired')) {
          if (getRefreshToken()) {
            try {
              const newToken = await silentRefresh();
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
        hydrateAdsOptOutFromServer(userData.ads_opt_out);
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
      setAdsAuthChecked(true);
    }
  }, []);

  useEffect(() => {
    const { hasToken } = hydrateTokensFromStorage();
    setLoading(false);
    if (hasToken) {
      fetchMe();
      return;
    }
    setAuthChecked(true);
    const probe = () => { fetchMe(); };
    if (typeof window !== 'undefined' && 'requestIdleCallback' in window) {
      window.requestIdleCallback(probe, { timeout: 1500 });
    } else {
      setTimeout(probe, 600);
    }
  }, [fetchMe]);

  // Sync anonymous study data when user signs in
  useAnonSync(user?.id);

  // Mirror the signed-in user's plan into the ads module
  useEffect(() => {
    setAdsUserPlan(user?.plan ?? null);
  }, [user?.plan]);


  const login = async (email, password) => {
    justAuthenticated.current = true;
    try {
      const res = await axios.post(
        `${API_BASE}/auth/login`,
        { email, password },
        { withCredentials: true },
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

  const signup = async (name, email, password, consent_dpdp = false) => {
    justAuthenticated.current = true;
    try {
      const res = await axios.post(
        `${API_BASE}/auth/signup`,
        { email, password, name, consent_dpdp },
        { withCredentials: true },
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
      const token = getToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      await axios.post(
        `${API_BASE}/auth/logout`,
        { refresh_token: getRefreshToken() },
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
      token: getToken(),
      loading,
      authChecked,
      login,
      signup,
      logout,
      refreshUser,
      updateUser,
      justAuthenticated,
      authHeader: getToken() ? { Authorization: `Bearer ${getToken()}` } : {},
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
