const FRIENDLY = {
  invalid_credentials: 'Email or password is incorrect.',
  user_not_found: 'No account found with that email.',
  email_exists: 'An account with this email already exists. Try signing in instead.',
  weak_password: 'Password is too weak. Use at least 8 characters with letters and numbers.',
  rate_limited: 'Too many attempts. Please wait a minute and try again.',
  account_locked: 'This account has been temporarily locked. Please reset your password to continue.',
  email_not_verified: 'Please verify your email before signing in. Check your inbox for the verification link.',
  google_token_invalid: 'Google sign-in failed. Please try again.',
  reset_token_invalid: 'This password reset link is no longer valid. Please request a new one.',
  reset_token_expired: 'This password reset link has expired. Please request a new one.',
};

export function formatAuthError(err, fallback = 'Something went wrong. Please try again.') {
  const detail = err?.response?.data?.detail;
  if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
    const code = detail.code;
    if (typeof code === 'string' && FRIENDLY[code]) return FRIENDLY[code];
    if (typeof detail.message === 'string' && detail.message) return detail.message;
    return fallback;
  }
  if (typeof detail === 'string') {
    if (FRIENDLY[detail]) return FRIENDLY[detail];
    if (/^[a-z0-9_]+$/.test(detail)) return fallback;
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    if (typeof first === 'string') return FRIENDLY[first] || first;
    if (first?.msg) return first.msg;
  }
  return fallback;
}
