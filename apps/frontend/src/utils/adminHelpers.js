import { API_BASE } from './api';
export const API = API_BASE;

export function authHeaders(token) {
  const isRealJwt = token && token.split('.').length === 3;
  return { headers: isRealJwt ? { Authorization: `Bearer ${token}` } : {}, withCredentials: true };
}

export function autoSlug(text) {
  return (text || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
}

function wordCount(text) {
  return (text || '').trim().split(/\s+/).filter(Boolean).length;
}
