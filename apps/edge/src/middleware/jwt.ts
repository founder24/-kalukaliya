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

/** Paths that do NOT require JWT authentication */
const PUBLIC_PATHS = [
  '/health',
  '/api/v1/auth/login',
  '/api/v1/auth/signup',
  '/api/v1/auth/refresh',
  '/api/v1/auth/forgot-password',
  '/api/v1/auth/reset-password',
  '/api/v1/admin/login',
  '/api/v1/admin/logout',
  '/api/webhooks',
  '/api/v1/content',
];

/**
 * Paths where JWT is optional - validate if present, allow anonymous if absent.
 * The backend handles anonymous users via get_current_user_optional.
 */
const OPTIONAL_AUTH_PATHS = [
  '/api/v1/chat',
  '/api/v1/ai/chat',
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

  // For optional-auth paths: validate token if present, allow anonymous if absent
  const isOptionalAuth = OPTIONAL_AUTH_PATHS.some((p) => url.pathname.startsWith(p));
  const authHeader = request.headers.get('Authorization');

  if (isOptionalAuth && (!authHeader || !authHeader.startsWith('Bearer '))) {
    return { valid: true, userId: 'anonymous' };
  }

  // Extract Bearer token
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return { valid: false, error: 'Missing or invalid Authorization header' };
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

  // Decode header to determine algorithm
  const headerJson = atob(base64UrlToBase64(headerB64));
  const header: JWTHeader = JSON.parse(headerJson);
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
    // HS256 verification
    const key = await crypto.subtle.importKey(
      'raw',
      encoder.encode(secret),
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

  // Decode payload
  const payloadJson = atob(base64UrlToBase64(payloadB64));
  const payload: JWTPayload = JSON.parse(payloadJson);

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
