import { useCallback } from 'react';
import { setAuthToken } from '@/utils/api';

let _inMemoryToken = null;
let _inMemoryRefreshToken = null;

export function getInMemoryToken() {
  return _inMemoryToken;
}

export function getInMemoryRefreshToken() {
  return _inMemoryRefreshToken;
}

export function useTokenManager() {
  const storeToken = useCallback((token) => {
    _inMemoryToken = token;
    setAuthToken(token);
    if (token) {
      sessionStorage.setItem('syrabit_token', token);
    } else {
      sessionStorage.removeItem('syrabit_token');
    }
  }, []);

  const storeRefreshToken = useCallback((token) => {
    _inMemoryRefreshToken = token;
    if (token) {
      sessionStorage.setItem('syrabit_refresh_token', token);
    } else {
      sessionStorage.removeItem('syrabit_refresh_token');
    }
  }, []);

  const clearTokens = useCallback(() => {
    storeToken(null);
    storeRefreshToken(null);
  }, [storeToken, storeRefreshToken]);

  const hydrateFromSession = useCallback(() => {
    const savedToken = sessionStorage.getItem('syrabit_token');
    const savedRefreshToken = sessionStorage.getItem('syrabit_refresh_token');
    if (savedToken) {
      _inMemoryToken = savedToken;
      setAuthToken(savedToken);
    }
    if (savedRefreshToken) {
      _inMemoryRefreshToken = savedRefreshToken;
    }
    return { hasToken: !!savedToken };
  }, []);

  return {
    token: _inMemoryToken,
    refreshToken: _inMemoryRefreshToken,
    storeToken,
    storeRefreshToken,
    clearTokens,
    hydrateFromSession,
  };
}
