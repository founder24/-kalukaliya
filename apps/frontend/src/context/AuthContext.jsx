import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
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
  const [authVerificationError, setAuthVerificationError] = useState(false);
  const justAuthenticated = useRef(false);
  const fetchMeInFlight = useRef(null);

  const fetchMe = useCallback(() => {
    // React StrictMode re-runs mount effects in development. Reuse the same
    // probe while it is pending so anonymous pages do not issue duplicate
    // /users/me requests (and duplicate expected 401 responses).
    if (fetchMeInFlight.current) return fetchMeInFlight.current;

    setAuthChecked(false);
    setAuthVerificationError(false);
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
              } catch (refreshError) {
                // Preserve the actual refresh failure. Re-throwing the first
                // 401 would make a transient refresh outage look like an
                // invalid session and send a still-authenticated user to login.
                throw refreshError;
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
          setAuthVerificationError(false);
          hydrateAdsOptOutFromServer(userData.ads_opt_out);
          // Set the plan before publishing the user so ad-bearing route
          // effects cannot run once with anonymous consent during hydration.
          setAdsPlan(userData.plan);
          setUser(userData);
        } else {
          setAuthVerificationError(false);
          setAdsPlan(null);
          setUser(null);
        }
        justAuthenticated.current = false;
        return !!resolvedUserId;
      } catch (err) {
        const status = err?.response?.status;
        const hasStoredToken = Boolean(getToken());
        if (hasStoredToken && status !== 401) {
          // A failed profile probe does not prove that the credentials are
          // invalid. Keep them and give protected routes an explicit retry
          // path instead of redirecting the user as though they were signed
          // out. A 401 remains the authoritative invalid-session response.
          setAuthVerificationError(true);
        } else if (!justAuthenticated.current) {
          setAuthVerificationError(false);
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
    setAdsPlan(user?.plan);
  }, [user?.plan]);


  const login = async (email, password) => {
    justAuthenticated.current = true;
    let authStage = 'login';
    try {
      const res = await axios.post(
        `${API_BASE}/auth/login`,
        { email, password },
        { withCredentials: true },
      );
      const { access_token, refresh_token } = res.data;
      // Fetch user profile immediately
      authStage = 'profile';
      const profileRes = await axios.get(`${API_BASE}/users/me`, {
        headers: { Authorization: `Bearer ${access_token}` },
        withCredentials: true,
      });
      const userData = profileRes.data;
      hydrateAdsOptOutFromServer(userData?.ads_opt_out);
      setAdsPlan(userData?.plan);
      // Do not persist credentials until both the login and profile requests
      // succeed; otherwise a failed profile fetch leaves a partial session.
      storeToken(access_token);
      storeRefreshToken(refresh_token);
      setToken(access_token);
      setAuthToken(access_token);
      setUser(userData);
      setAuthVerificationError(false);
      try { Analytics.login(userData.id, userData.email); } catch {}
      return userData;
    } catch (err) {
      justAuthenticated.current = false;
      if (err && typeof err === 'object') {
        try { err.authStage = authStage; } catch {}
      }
      throw err;
    }
  };

  const signup = async (name, email, password, consent_dpdp = false) => {
    justAuthenticated.current = true;
    let authStage = 'signup';
    try {
      const res = await axios.post(
        `${API_BASE}/auth/signup`,
        { email, password, name, consent_dpdp },
        { withCredentials: true },
      );
      const { access_token, refresh_token } = res.data;
      // Fetch user profile immediately
      authStage = 'profile';
      const profileRes = await axios.get(`${API_BASE}/users/me`, {
        headers: { Authorization: `Bearer ${access_token}` },
        withCredentials: true,
      });
      const userData = profileRes.data;
      hydrateAdsOptOutFromServer(userData?.ads_opt_out);
      setAdsPlan(userData?.plan);
      // Commit the credentials only after profile loading succeeds, matching
      // login's all-or-nothing authentication flow.
      storeToken(access_token);
      storeRefreshToken(refresh_token);
      setToken(access_token);
      setAuthToken(access_token);
      setUser(userData);
      setAuthVerificationError(false);
      try { Analytics.signup(userData.email, userData.plan); } catch {}
      return userData;
    } catch (err) {
      justAuthenticated.current = false;
      if (err && typeof err === 'object') {
        try { err.authStage = authStage; } catch {}
      }
      throw err;
    }
  };

  const logout = async () => {
    let serverRevocationConfirmed = true;
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
      serverRevocationConfirmed = false;
      try { Analytics.track('logout_backend_error', { status: err?.response?.status }); } catch {}
    }
    clearTokens();
    setAuthToken(null);
    setToken(null);
    justAuthenticated.current = false;
    setAuthVerificationError(false);
    localStorage.removeItem('syrabit:onboarding');
    setAdsPlan(null);
    setUser(null);
    try { Analytics.logout(); } catch {}
    if (!serverRevocationConfirmed) {
      toast.warning(
        'Signed out on this device, but server-side session revocation could not be confirmed. Reconnect and sign out again.',
        { duration: 8000 },
      );
    }
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
      authVerificationError,
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
