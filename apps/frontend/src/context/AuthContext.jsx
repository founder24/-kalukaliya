import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { API_BASE } from '@/utils/api';
import { Analytics } from '@/utils/analytics';
import {
  hydrateAdsOptOutFromServer,
  setAdsAuthChecked,
  setAdsPlan,
} from '@/utils/adsConfig';
import {
  getToken,
  getRefreshToken,
  storeToken,
  storeRefreshToken,
  clearTokens,
  hydrateTokensFromStorage,
} from '@/hooks/useTokenManager';
import { setAuthToken } from '@/utils/api';
import { silentRefresh } from '@/hooks/useAuthRefresh';
import { useAnonSync } from '@/hooks/useAnonSync';

const AuthContext = createContext(null);

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [token, setToken] = useState(getToken);
  const [loading, setLoading] = useState(true);
  const [authChecked, setAuthChecked] = useState(false);
  const justAuthenticated = useRef(false);
  const fetchMeInFlight = useRef(null);

  const fetchMe = useCallback(() => {
    // React StrictMode re-runs mount effects in development. Reuse the same
    // probe while it is pending so anonymous pages do not issue duplicate
    // /users/me requests (and duplicate expected 401 responses).
    if (fetchMeInFlight.current) return fetchMeInFlight.current;

    const request = (async () => {
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
                setToken(newToken);
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
          resolvedUserId = userData.id;
          hydrateAdsOptOutFromServer(userData.ads_opt_out);
          // Set the plan before publishing the user so ad-bearing route
          // effects cannot run once with anonymous consent during hydration.
          setAdsPlan(userData.plan, userData.ads_free_until);
          setUser(userData);
        } else {
          setAdsPlan(null);
          setUser(null);
        }
        justAuthenticated.current = false;
        return !!resolvedUserId;
      } catch {
        if (!justAuthenticated.current) {
          setAdsPlan(null);
          setUser(null);
        }
        return false;
      } finally {
        setAuthChecked(true);
        setAdsAuthChecked(true);
      }
    })();

    fetchMeInFlight.current = request;
    request.then(
      () => { if (fetchMeInFlight.current === request) fetchMeInFlight.current = null; },
      () => { if (fetchMeInFlight.current === request) fetchMeInFlight.current = null; },
    );
    return request;
  }, []);

  useEffect(() => {
    const { hasToken } = hydrateTokensFromStorage();
    setLoading(false);
    if (hasToken) {
      const storedToken = getToken();
      if (storedToken) setAuthToken(storedToken);
      fetchMe();
      return;
    }
    // No access token means this is an anonymous session. The protected
    // /users/me endpoint would only return an expected 401, so publish the
    // anonymous state directly instead of creating a noisy failed request.
    setAdsPlan(null);
    setUser(null);
    setAuthChecked(true);
    setAdsAuthChecked(true);
  }, [fetchMe]);

  // Sync anonymous study data when user signs in
  useAnonSync(user?.id);

  // Mirror the signed-in user's plan into the ads module
  useEffect(() => {
    setAdsPlan(user?.plan, user?.ads_free_until);
  }, [user?.plan, user?.ads_free_until]);


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
      setToken(access_token);
      setAuthToken(access_token);
      // Fetch user profile immediately
      const profileRes = await axios.get(`${API_BASE}/users/me`, {
        headers: { Authorization: `Bearer ${access_token}` },
        withCredentials: true,
      });
      const userData = profileRes.data;
      hydrateAdsOptOutFromServer(userData?.ads_opt_out);
      setAdsPlan(userData?.plan, userData?.ads_free_until);
      setUser(userData);
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
      setToken(access_token);
      setAuthToken(access_token);
      // Fetch user profile immediately
      const profileRes = await axios.get(`${API_BASE}/users/me`, {
        headers: { Authorization: `Bearer ${access_token}` },
        withCredentials: true,
      });
      const userData = profileRes.data;
      hydrateAdsOptOutFromServer(userData?.ads_opt_out);
      setAdsPlan(userData?.plan, userData?.ads_free_until);
      setUser(userData);
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
        // Never let a slow revocation endpoint trap the user in the staff
        // portal. Local credentials are cleared below even when this request
        // times out; the rotating refresh-token claim remains bounded.
        { withCredentials: true, headers, timeout: 5000 },
      );
    } catch (err) {
      try { Analytics.track('logout_backend_error', { status: err?.response?.status }); } catch {}
    }
    clearTokens();
    setAuthToken(null);
    setToken(null);
    justAuthenticated.current = false;
    localStorage.removeItem('syrabit:onboarding');
    setAdsPlan(null);
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
      token,
      loading,
      authChecked,
      login,
      signup,
      logout,
      refreshUser,
      updateUser,
      justAuthenticated,
      authHeader: token ? { Authorization: `Bearer ${token}` } : {},
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
