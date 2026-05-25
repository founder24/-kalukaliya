import axios from 'axios';
import { API_BASE } from '@/utils/api';
import { getInMemoryToken, getInMemoryRefreshToken, storeToken, storeRefreshToken } from './useTokenManager';

/**
 * Attempt a silent token refresh and retry the original request.
 * Returns the axios response on success, or throws the original error.
 */
export async function silentRefresh(originalError) {
  const status = originalError?.response?.status;
  const detail = originalError?.response?.data?.detail;

  if (status === 401 && (detail === 'token_expired' || detail === 'jwt_expired')) {
    const refreshToken = getInMemoryRefreshToken();
    if (refreshToken) {
      const refreshRes = await axios.post(
        `${API_BASE}/auth/refresh`,
        { refresh_token: refreshToken },
        { withCredentials: true },
      );
      const newToken = refreshRes?.data?.access_token;
      const newRefresh = refreshRes?.data?.refresh_token;
      if (newToken) {
        storeToken(newToken);
      }
      if (newRefresh) {
        storeRefreshToken(newRefresh);
      }
      // Retry the original /users/me call
      const res = await axios.get(`${API_BASE}/users/me`, {
        withCredentials: true,
        headers: newToken ? { Authorization: `Bearer ${newToken}` } : {},
      });
      return res;
    }
  }
  throw originalError;
}
