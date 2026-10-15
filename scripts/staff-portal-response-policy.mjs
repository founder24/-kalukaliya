export function isPostLogoutAuthEndpoint(url, postLogoutProbe) {
  if (!postLogoutProbe) return false;
  const pathname = new URL(url).pathname;
  return pathname === '/api/v1/users/me' || pathname === '/api/v1/admin/verify';
}

export function isExpectedPostLogoutAuthResponse(url, status, postLogoutProbe) {
  return isPostLogoutAuthEndpoint(url, postLogoutProbe) && status === 401;
}

export function hasCloudflareAccessHeaders(headers = {}) {
  const normalized = new Map(
    Object.entries(headers).map(([name, value]) => [name.toLowerCase(), String(value).toLowerCase()]),
  );
  if (normalized.has('cf-access-client-id') || normalized.has('cf-access-client-secret')) return true;
  return (normalized.get('access-control-request-headers') || '')
    .split(',')
    .map(name => name.trim())
    .some(name => name === 'cf-access-client-id' || name === 'cf-access-client-secret');
}