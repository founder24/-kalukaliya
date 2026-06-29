/**
 * JWT Verification Middleware for Cloudflare Workers
 *
 * Verifies HS256 and RS256 JWTs using the Web Crypto API (available in Workers runtime).
 * Algorithm is auto-detected from the token header's `alg` field.
 * Extracts user ID from the `sub` claim and injects it as X-User-ID header.
 * Skips verification for public endpoints (login, signup, health).
 */

export interface JWTVerifyResult {
  valid: boolean;
  userId?: string;
  error?: string;
}

interface JWTPayload {
  sub: string;
  exp: number;
  type: string;
  iat?: number;
}

interface JWTHeader {
  alg: string;
  typ?: string;
}

/**
 * ── Route group auth classification (canonical reference) ──────────────────
 *
 * Group A — PUBLIC_PATHS (no JWT at edge, no auth at backend):
 *   Auth endpoints (/login, /signup, /refresh, /forgot-password, /reset-password)
 *   Public content (/boards, /classes, /subjects, /chapters, /seo/*, /sitemap/*)
 *   Analytics, config, health, webhooks
 *
 * Group B — OPTIONAL_AUTH_PATHS (JWT verified if present; anonymous allowed):
 *   /api/v1/chat, /api/v1/conversations, /api/v1/edu
 *   The backend uses get_current_user_optional() on these routes.
 *   Anonymous users get a capped monthly quota enforced by MongoDB.
 *
 * Group C — PROTECTED (JWT required at edge; backend enforces tier/permissions):
 *   /api/v1/users/*, /api/v1/subscription/*, /api/v1/feedback/*
 *   All routes NOT listed in PUBLIC_PATHS or OPTIONAL_AUTH_PATHS.
 *
 * Group D — ADMIN routes (/api/v1/admin/*):
 *   Listed in PUBLIC_PATHS intentionally — admin routes are COOKIE-protected
 *   on the backend (require_admin_session dependency). They must NOT be
 *   JWT-gated at the edge because admin sessions use httpOnly cookies, not
 *   Bearer tokens. The edge cannot inspect cookies without forwarding them.
 *   EXCEPTION: machine/cron routes in admin_cron.py accept Bearer tokens;
 *   those are verified entirely by the backend using TRANSLATE_CRON_SECRET.
 *
 * Invariant: JWT_SECRET and ADMIN_JWT_SECRET are DIFFERENT keys.
 *   JWT_SECRET  → signs user access tokens (Group B/C)
 *   ADMIN_JWT_SECRET → signs admin session tokens (Group D, backend only)
 *   The edge only sees JWT_SECRET. Admin tokens never pass through edge JWT check.
 * ───────────────────────────────────────────────────────────────────────────
 */

/** Paths that do NOT require JWT authentication at the edge.
 *
 * IMPORTANT: This array must be kept in sync with the backend content router
 * (apps/backend/app/api/v1/) when new endpoints are added. A new authenticated
 * endpoint accidentally listed here will be publicly accessible without auth.
 * Review both this file and the backend router together when adding routes.
 *
 * Admin routes (/api/v1/admin/) are intentionally listed here — see Group D above.
 */
const PUBLIC_PATHS = [
  '/health',
  '/api/v1/auth/login',
  '/api/v1/auth/signup',
  '/api/v1/auth/refresh',
  '/api/v1/auth/forgot-password',
  '/api/v1/auth/reset-password',
  // All admin routes use httponly-cookie session auth handled entirely by the
  // backend — they must NOT be JWT-gated at the edge.  Only login/logout are
  // listed explicitly for clarity; the prefix match below covers /verify,
  // /dashboard, /content/*, /studio/*, /seo/*, etc.
  '/api/v1/admin/',
  '/api/webhooks',
  '/api/v1/content/public',
  '/api/v1/content/boards',
  '/api/v1/content/classes',
  '/api/v1/content/streams',
  '/api/v1/content/subjects',
  '/api/v1/content/chapters',
  '/api/v1/content/chunks',
  '/api/v1/content/chapter-by-slug',
  '/api/v1/content/topic',
  '/api/v1/content/library-bundle',
  '/api/content/library-bundle',
  '/api/v1/content/resolve-subject',
  '/api/v1/seo',
  '/api/v1/seo/page',
  '/api/v1/seo/page-bundle',
  '/api/v1/seo/page-types',
  '/api/v1/seo/related',
  '/sitemap',
  '/robots.txt',
  // Analytics & page-tracking — no auth required, fired from every page load
  '/api/v1/analytics',
  '/api/analytics',
  // Public config endpoints — Trustpilot widget config, no auth required
  '/api/v1/config',
];

/**
 * Paths where JWT is optional - validate if present, allow anonymous if absent.
 * The backend handles anonymous users via get_current_user_optional.
 */
const OPTIONAL_AUTH_PATHS = [
  '/api/v1/chat',
  '/api/v1/ai/chat',
  '/api/v1/conversations',
  '/api/v1/conversations/anon',
  '/api/v1/edu',
];

/**
 * Verify JWT from Authorization header.
 * Returns { valid: true, userId } on success, or { valid: false, error } on failure.
 * Supports both HS256 (with jwtSecret) and RS256 (with jwtPublicKey).
 */
export async function verifyJWT(
  request: Request,
  jwtSecret: string,
  jwtPublicKey?: string
): Promise<JWTVerifyResult> {
  const url = new URL(request.url);

  // Skip JWT for public endpoints
  if (PUBLIC_PATHS.some((p) => url.pathname.startsWith(p))) {
    return { valid: true, userId: 'anonymous' };
  }

  // For optional-auth paths: allow anonymous only when NO Authorization header present.
  // If a header IS present but malformed, reject it (401) rather than silently downgrading.
  const isOptionalAuth = OPTIONAL_AUTH_PATHS.some((p) => url.pathname.startsWith(p));
  const authHeader = request.headers.get('Authorization');

  if (isOptionalAuth && !authHeader) {
    return { valid: true, userId: 'anonymous' };
  }

  // If neither secret nor public key is configured the edge cannot verify tokens.
  // Pass through to the backend (which has its own JWT verification) rather than
  // rejecting valid user tokens with a misleading 401. This is the safe fallback
  // for environments where wrangler secrets have not yet been provisioned.
  if (!jwtSecret && !jwtPublicKey) {
    return { valid: false, error: 'Missing or invalid Authorization header' };
  }

  // Extract Bearer token — two distinct failure modes:
  //   1. No header at all → treated as anonymous (not rejected) for non-protected routes
  //   2. Header present but wrong scheme → rejected with 401 (Malformed header)
  if (!authHeader) {
    return { valid: false, error: 'Missing or invalid Authorization header' };
  }
  if (!authHeader.startsWith('Bearer ')) {
    return { valid: false, error: 'Malformed Authorization header' };
  }

  const token = authHeader.slice(7);
  if (!token) {
    return { valid: false, error: 'Empty token' };
  }

  try {
    const payload = await decodeAndVerify(token, jwtSecret, jwtPublicKey);

    // Check expiry
    const now = Math.floor(Date.now() / 1000);
    if (payload.exp < now) {
      return { valid: false, error: 'Token expired' };
    }

    // Must be an access token (not refresh)
    if (payload.type !== 'access') {
      return { valid: false, error: 'Invalid token type' };
    }

    if (!payload.sub) {
      return { valid: false, error: 'Token missing subject' };
    }

    return { valid: true, userId: payload.sub };
  } catch (e) {
    const msg = e instanceof Error ? e.message : 'Unknown verification error';
    return { valid: false, error: msg };
  }
}

/**
 * Decode JWT parts, verify signature using Web Crypto API.
 * Supports HS256 (HMAC-SHA256) and RS256 (RSASSA-PKCS1-v1_5 with SHA-256).
 * Algorithm is detected from the token header.
 */
async function decodeAndVerify(
  token: string,
  secret: string,
  publicKey?: string
): Promise<JWTPayload> {
  const parts = token.split('.');
  if (parts.length !== 3) {
    throw new Error('Malformed token: expected 3 parts');
  }

  const [headerB64, payloadB64, signatureB64] = parts;

  // Decode header to determine algorithm.
  // atob + JSON.parse can throw SyntaxError/DOMException on garbage input —
  // catch and rethrow a clean message so internal JS errors never leak to clients.
  let header: JWTHeader;
  try {
    const headerJson = atob(base64UrlToBase64(headerB64));
    header = JSON.parse(headerJson) as JWTHeader;
  } catch {
    throw new Error('Malformed token');
  }
  const alg = header.alg;

  // Security: reject 'none' algorithm to prevent algorithm confusion attacks
  if (!alg || alg.toLowerCase() === 'none') {
    throw new Error('Unsupported algorithm: none');
  }

  const encoder = new TextEncoder();
  const signatureInput = encoder.encode(`${headerB64}.${payloadB64}`);
  const signature = base64UrlDecode(signatureB64);

  if (alg === 'RS256') {
    if (!publicKey) {
      throw new Error('RS256 token received but no public key configured');
    }
    // RS256 verification using RSASSA-PKCS1-v1_5
    const key = await importRSAPublicKey(publicKey);
    const isValid = await crypto.subtle.verify(
      'RSASSA-PKCS1-v1_5',
      key,
      signature,
      signatureInput
    );
    if (!isValid) {
      throw new Error('Invalid signature');
    }
  } else if (alg === 'HS256') {
    // HS256 verification — trim() normalises trailing newlines that GCP Secret Manager
    // appends when mounting secrets as env vars, ensuring the key matches regardless of
    // whether the secret was stored with or without a trailing newline.
    const key = await crypto.subtle.importKey(
      'raw',
      encoder.encode(secret.trim()),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['verify']
    );
    const isValid = await crypto.subtle.verify('HMAC', key, signature, signatureInput);
    if (!isValid) {
      throw new Error('Invalid signature');
    }
  } else {
    throw new Error(`Unsupported algorithm: ${alg}`);
  }

  // Decode payload — same guard as header decode above.
  let payload: JWTPayload;
  try {
    const payloadJson = atob(base64UrlToBase64(payloadB64));
    payload = JSON.parse(payloadJson) as JWTPayload;
  } catch {
    throw new Error('Malformed token');
  }

  return payload;
}

/**
 * Import a PEM-encoded RSA public key for use with Web Crypto API.
 * Strips PEM headers/footers, decodes base64 to get DER bytes,
 * then imports as SPKI format for RSASSA-PKCS1-v1_5 verification.
 */
async function importRSAPublicKey(pem: string): Promise<CryptoKey> {
  // Strip PEM headers and whitespace
  const pemContents = pem
    .replace(/-----BEGIN PUBLIC KEY-----/g, '')
    .replace(/-----END PUBLIC KEY-----/g, '')
    .replace(/\s+/g, '');

  // Decode base64 to binary
  const binary = atob(pemContents);
  const derBytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    derBytes[i] = binary.charCodeAt(i);
  }

  return crypto.subtle.importKey(
    'spki',
    derBytes.buffer,
    { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
    false,
    ['verify']
  );
}

/**
 * Convert base64url string to ArrayBuffer for signature verification.
 */
function base64UrlDecode(str: string): ArrayBuffer {
  const base64 = base64UrlToBase64(str);
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

/**
 * Convert base64url encoding to standard base64.
 */
function base64UrlToBase64(str: string): string {
  let base64 = str.replace(/-/g, '+').replace(/_/g, '/');
  const padding = 4 - (base64.length % 4);
  if (padding !== 4) {
    base64 += '='.repeat(padding);
  }
  return base64;
}
