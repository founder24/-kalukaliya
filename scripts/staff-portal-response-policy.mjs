export function isPostLogoutAuthEndpoint(url, postLogoutProbe) {
  if (!postLogoutProbe) return false;
  const pathname = new URL(url).pathname;
  return pathname === '/api/v1/users/me' || pathname === '/api/v1/admin/verify';
}

export function isExpectedPostLogoutAuthResponse(url, status, postLogoutProbe) {
  return isPostLogoutAuthEndpoint(url, postLogoutProbe) && status === 401;
}