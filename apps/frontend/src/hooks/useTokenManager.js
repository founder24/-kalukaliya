import { setAuthToken } from '@/utils/api';

// NOTE: Module-level state is intentional. Token changes always coincide with
// setUser() in AuthContext, which triggers re-render. If future code stores
// tokens without updating user state, wrap these in useState.
let _inMemoryToken = null;
let _inMemoryRefreshToken = null;

export const getInMemoryToken = () => _inMemoryToken;
export const getInMemoryRefreshToken = () => _inMemoryRefreshToken;

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

export function getToken() {
  return _inMemoryToken;
}

export function getRefreshToken() {
  return _inMemoryRefreshToken;
}

export function clearTokens() {
  storeToken(null);
  storeRefreshToken(null);
}

export function hydrateTokensFromStorage() {
  const savedToken = sessionStorage.getItem('syrabit_token');
  const savedRefreshToken = sessionStorage.getItem('syrabit_refresh_token');
  if (savedRefreshToken) _inMemoryRefreshToken = savedRefreshToken;
  if (savedToken) {
    _inMemoryToken = savedToken;
    setAuthToken(savedToken);
  }
  return { savedToken, savedRefreshToken };
}
