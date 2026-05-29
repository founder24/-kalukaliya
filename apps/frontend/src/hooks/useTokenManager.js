import { setAuthToken } from '@/utils/api';
import axios from 'axios';

let _inMemoryToken = null;
let _inMemoryRefreshToken = null;

// HF-007/HF-050: Safe sessionStorage wrappers for SSR/restricted contexts
function safeSessionGet(key) {
  try {
    if (typeof window !== 'undefined' && window.sessionStorage) {
      return sessionStorage.getItem(key);
    }
  } catch { /* SSR/restricted context */ }
  return null;
}

function safeSessionSet(key, value) {
  try {
    if (typeof window !== 'undefined' && window.sessionStorage) {
      sessionStorage.setItem(key, value);
    }
  } catch { /* SSR/restricted context */ }
}

function safeSessionRemove(key) {
  try {
    if (typeof window !== 'undefined' && window.sessionStorage) {
      sessionStorage.removeItem(key);
    }
  } catch { /* SSR/restricted context */ }
}

export const getToken = () => _inMemoryToken;
export const getRefreshToken = () => _inMemoryRefreshToken;

export function storeToken(token) {
  _inMemoryToken = token;
  setAuthToken(token);
  if (token) {
    safeSessionSet('syrabit_token', token);
  } else {
    safeSessionRemove('syrabit_token');
  }
}

export function storeRefreshToken(token) {
  _inMemoryRefreshToken = token;
  if (token) {
    safeSessionSet('syrabit_refresh_token', token);
  } else {
    safeSessionRemove('syrabit_refresh_token');
  }
}

export function clearTokens() {
  storeToken(null);
  storeRefreshToken(null);
  // HF-055: Clear any axios default headers
  try { delete axios.defaults.headers.common['Authorization']; } catch {}
}

export function hydrateTokensFromStorage() {
  const savedToken = safeSessionGet('syrabit_token');
  const savedRefreshToken = safeSessionGet('syrabit_refresh_token');
  if (savedToken) {
    _inMemoryToken = savedToken;
    setAuthToken(savedToken);
  }
  if (savedRefreshToken) {
    _inMemoryRefreshToken = savedRefreshToken;
  }
  return { hasToken: !!savedToken };
}
