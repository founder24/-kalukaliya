import { setAuthToken } from '@/utils/api';
import axios from 'axios';

let _inMemoryToken = null;
let _inMemoryRefreshToken = null;

// HF-007/HF-050: Safe storage wrappers for SSR/restricted contexts
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

// localStorage wrappers for refresh token persistence across tabs/sessions
//
// SECURITY NOTE (XSS trade-off): Storing refresh tokens in localStorage exposes them
// to any XSS on this origin. This is an accepted trade-off because:
//   1. CSP restricts script-src to 'self', limiting XSS vectors significantly.
//   2. Refresh tokens have a short TTL and are rotated on each use (single-use via
//      Redis atomic claim), so a stolen token has a narrow window of validity.
//   3. httpOnly cookies are not viable for this SPA architecture -- the edge worker
//      and client both need to read the token for refresh flows, and cookie-based
//      refresh would require a same-origin BFF endpoint that does not exist here.
//
function safeLocalGet(key) {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      return localStorage.getItem(key);
    }
  } catch { /* SSR/restricted context */ }
  return null;
}

function safeLocalSet(key, value) {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      localStorage.setItem(key, value);
    }
  } catch { /* SSR/restricted context */ }
}

function safeLocalRemove(key) {
  try {
    if (typeof window !== 'undefined' && window.localStorage) {
      localStorage.removeItem(key);
    }
  } catch { /* SSR/restricted context */ }
}

export const getToken = () => _inMemoryToken;
export const getRefreshToken = () => _inMemoryRefreshToken;

export function storeToken(token) {
  _inMemoryToken = token;
  setAuthToken(token);
  // Access tokens are kept in memory only for security (not persisted)
  // sessionStorage is used only for tab-restore scenarios
  if (token) {
    safeSessionSet('syrabit_token', token);
  } else {
    safeSessionRemove('syrabit_token');
  }
}

export function storeRefreshToken(token) {
  _inMemoryRefreshToken = token;
  if (token) {
    safeLocalSet('syrabit_refresh_token', token);
  } else {
    safeLocalRemove('syrabit_refresh_token');
  }
  // Clean up legacy sessionStorage entry if present
  safeSessionRemove('syrabit_refresh_token');
}

export function clearTokens() {
  storeToken(null);
  storeRefreshToken(null);
  // HF-055: Clear any axios default headers
  try { delete axios.defaults.headers.common['Authorization']; } catch {}
}

export function hydrateTokensFromStorage() {
  const savedToken = safeSessionGet('syrabit_token');
  // Refresh token: prefer localStorage, fall back to legacy sessionStorage
  const savedRefreshToken = safeLocalGet('syrabit_refresh_token') || safeSessionGet('syrabit_refresh_token');
  if (savedToken) {
    _inMemoryToken = savedToken;
    setAuthToken(savedToken);
  }
  if (savedRefreshToken) {
    _inMemoryRefreshToken = savedRefreshToken;
    // Migrate from sessionStorage to localStorage if needed
    if (!safeLocalGet('syrabit_refresh_token') && savedRefreshToken) {
      safeLocalSet('syrabit_refresh_token', savedRefreshToken);
      safeSessionRemove('syrabit_refresh_token');
    }
  }
  return { hasToken: !!savedToken };
}
