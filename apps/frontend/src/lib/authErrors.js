const FRIENDLY = {
  invalid_credentials: 'Email or password is incorrect.',
  user_not_found: 'No account found with that email.',
  email_exists: 'An account with this email already exists. Try signing in instead.',
  weak_password: 'Password is too weak. Use at least 8 characters with letters and numbers.',
  rate_limited: 'Too many attempts. Please wait a minute and try again.',
  auth_rate_limited: 'Too many sign-in attempts. Please wait a minute and try again.',
  rate_limit_storage_unavailable: 'Sign-in is temporarily unavailable. Please try again shortly.',
  password_reset_required: 'Please reset your password before signing in.',
  password_login_unavailable: 'This account uses a different sign-in method. Try the option you originally used.',
  account_locked: 'This account has been temporarily locked. Please reset your password to continue.',
  email_not_verified: 'Please verify your email before signing in. Check your inbox for the verification link.',
  google_token_invalid: 'Google sign-in failed. Please try again.',
  reset_token_invalid: 'This password reset link is no longer valid. Please request a new one.',
  reset_token_expired: 'This password reset link has expired. Please request a new one.',
};

const FRIENDLY_DETAILS = {
  'invalid credentials': FRIENDLY.invalid_credentials,
  'too many authentication attempts': FRIENDLY.auth_rate_limited,
  'authentication service temporarily unavailable': FRIENDLY.rate_limit_storage_unavailable,
  'password login not available for this account': FRIENDLY.password_login_unavailable,
};

function getFriendlyCopy(map, key) {
  return typeof key === 'string' && Object.prototype.hasOwnProperty.call(map, key)
    ? map[key]
    : null;
}

function extractPydanticMsg(msg) {
  if (!msg) return null;
  const m = String(msg).match(/Value error,\s*(.+)/i);
  return m ? m[1].trim() : msg;
}

function formatDetail(detail) {
  if (typeof detail === 'string') {
    const normalized = detail.trim().toLowerCase();
    return getFriendlyCopy(FRIENDLY, detail) ||
      getFriendlyCopy(FRIENDLY, normalized) ||
      getFriendlyCopy(FRIENDLY_DETAILS, normalized) ||
      (/^[a-z0-9_]+$/.test(detail) ? null : detail);
  }

  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0];
    if (typeof first === 'string') {
      return getFriendlyCopy(FRIENDLY, first) ||
        getFriendlyCopy(FRIENDLY_DETAILS, first.trim().toLowerCase()) ||
        first;
    }
    const rawMsg = first?.msg || first?.message;
    if (rawMsg) return extractPydanticMsg(rawMsg);
  }

  if (detail && typeof detail === 'object') {
    const code = detail.code || detail.error_code;
    const friendlyCopy = getFriendlyCopy(FRIENDLY, code);
    if (friendlyCopy) return friendlyCopy;
    if (typeof detail.message === 'string' && detail.message.trim()) return detail.message;
  }

  return null;
}

function getRequestId(err) {
  const headers = err?.response?.headers;
  const requestId = headers?.get?.('x-request-id') ||
    headers?.['x-request-id'] ||
    headers?.['X-Request-ID'];
  return typeof requestId === 'string' && /^[a-z0-9-]{8,80}$/i.test(requestId)
    ? requestId
    : null;
}

function withRequestReference(message, err) {
  const requestId = getRequestId(err);
  return requestId ? `${message} Reference: ${requestId}.` : message;
}

function getFallbackForFailure(err, fallback) {
  if (err?.authStage === 'profile') {
    return withRequestReference(
      'Authentication succeeded, but your profile could not be loaded. No session was saved. Please try again.',
      err,
    );
  }

  const status = err?.response?.status;
  let message;
  if (status === 401) {
    message = 'Your request was not authorized. Check the submitted details or link and try again.';
  } else if (status === 403) {
    message = 'This request was blocked. Check your account access or contact support.';
  } else if (status === 429) {
    message = 'Too many requests. Please wait a minute and try again.';
  } else if (Number.isFinite(status) && status >= 500) {
    message = 'The authentication service is temporarily unavailable. Please try again shortly.';
  }
  if (message) return withRequestReference(message, err);

  if (!err?.response && (err?.request || err?.code === 'ERR_NETWORK' || err?.code === 'ECONNABORTED')) {
    return 'Could not reach the authentication service. Check your connection and try again.';
  }

  return fallback;
}

export function formatAuthError(err, fallback = 'Something went wrong. Please try again.') {
  const data = err?.response?.data;
  const status = err?.response?.status;
  const detail = data?.detail;
  const isGenericServerError = typeof detail === 'string' &&
    /^internal server error[.!]?$/i.test(detail.trim());
  if (Number.isFinite(status) && status >= 500 && isGenericServerError) {
    return getFallbackForFailure(err, fallback);
  }

  const detailMessage = formatDetail(data?.detail);
  if (detailMessage) return detailMessage;

  const rootCode = data?.error_code || data?.code;
  const rootFriendlyCopy = getFriendlyCopy(FRIENDLY, rootCode);
  if (rootFriendlyCopy) return rootFriendlyCopy;

  const rootMessage = typeof data?.message === 'string'
    ? data.message
    : typeof data?.error === 'string' ? data.error : null;
  if (rootMessage?.trim()) return rootMessage;

  return getFallbackForFailure(err, fallback);
}
