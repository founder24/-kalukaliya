import axios from 'axios';
import { API_BASE } from '@/utils/api';
import { getRefreshToken, storeToken, storeRefreshToken } from './useTokenManager';

/**
 * Attempts a silent token refresh when a 401 with token_expired is received.
 * Returns the new access token on success, or throws on failure.
 */
export async function silentRefresh() {
  const refreshToken = getRefreshToken();
  if (!refreshToken) {
    throw new Error('No refresh token available');
  }
  const res = await axios.post(
    `${API_BASE}/auth/refresh`,
    { refresh_token: refreshToken },
    { withCredentials: true },
  );
  const newToken = res?.data?.access_token;
  const newRefresh = res?.data?.refresh_token;
  if (newToken) storeToken(newToken);
  if (newRefresh) storeRefreshToken(newRefresh);
  return newToken;
}

/**
 * Wraps a request attempt with automatic refresh on 401 token_expired.
 */
export async function withRefresh(requestFn) {
  try {
    return await requestFn();
  } catch (err) {
    const status = err?.response?.status;
    const detail = err?.response?.data?.detail;
    if (status === 401 && (detail === 'token_expired' || detail === 'jwt_expired')) {
      const newToken = await silentRefresh();
      return await requestFn(newToken);
    }
    throw err;
  }
}
