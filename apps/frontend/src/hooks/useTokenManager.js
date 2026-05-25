import { setAuthToken } from '@/utils/api';

let _inMemoryToken = null;
let _inMemoryRefreshToken = null;

export const getToken = () => _inMemoryToken;
export const getRefreshToken = () => _inMemoryRefreshToken;

export function storeToken(token) {
  _inMemoryToken = token;
  setAuthToken(token);
  if (token) {
    sessionStorage.setItem('syrabit_token', token);
  } else {
    sessionStorage.removeItem('syrabit_token');
  }
}

export function storeRefreshToken(token) {
  _inMemoryRefreshToken = token;
  if (token) {
    sessionStorage.setItem('syrabit_refresh_token', token);
  } else {
    sessionStorage.removeItem('syrabit_refresh_token');
  }
}

export function clearTokens() {
  storeToken(null);
  storeRefreshToken(null);
}

export function hydrateTokensFromStorage() {
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
}
