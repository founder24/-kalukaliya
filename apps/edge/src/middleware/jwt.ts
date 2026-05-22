/**
 * JWT Verification Middleware for Cloudflare Workers
 *
 * Verifies HS256 JWTs using the Web Crypto API (available in Workers runtime).
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

/** Paths that do NOT require JWT authentication */
const PUBLIC_PATHS = [
  '/health',
  '/api/v1/auth/login',
  '/api/v1/auth/signup',
  '/api/v1/auth/refresh',
  '/api/webhooks',
  '/api/v1/chat',  // Chat allows anonymous (backend handles via optional auth)
];

/**
 * Verify JWT from Authorization header.
 * Returns { valid: true, userId } on success, or { valid: false, error } on failure.
 */
export async function verifyJWT(
  request: Request,
  jwtSecret: string
): Promise<JWTVerifyResult> {
  const url = new URL(request.url);

  // Skip JWT for public endpoints
  if (PUBLIC_PATHS.some((p) => url.pathname.startsWith(p))) {
    return { valid: true, userId: 'anonymous' };
  }

  // Extract Bearer token
  const authHeader = request.headers.get('Authorization');
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return { valid: false, error: 'Missing or invalid Authorization header' };
  }

  const token = authHeader.slice(7);
  if (!token) {
    return { valid: false, error: 'Empty token' };
  }

  try {
    const payload = await decodeAndVerify(token, jwtSecret);

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
 * Decode JWT parts, verify HMAC-SHA256 signature using Web Crypto API.
 */
async function decodeAndVerify(token: string, secret: string): Promise<JWTPayload> {
  const parts = token.split('.');
  if (parts.length !== 3) {
    throw new Error('Malformed token: expected 3 parts');
  }

  const [headerB64, payloadB64, signatureB64] = parts;

  // Import secret key for HMAC verification
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['verify']
  );

  // Verify signature
  const signatureInput = encoder.encode(`${headerB64}.${payloadB64}`);
  const signature = base64UrlDecode(signatureB64);

  const isValid = await crypto.subtle.verify('HMAC', key, signature, signatureInput);
  if (!isValid) {
    throw new Error('Invalid signature');
  }

  // Decode payload
  const payloadJson = atob(base64UrlToBase64(payloadB64));
  const payload: JWTPayload = JSON.parse(payloadJson);

  return payload;
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
