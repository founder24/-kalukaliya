/**
 * Google Identity Token Fetcher
 *
 * Authenticates to Google Cloud Run using a service account key stored
 * as a Cloudflare Worker secret (GOOGLE_SA_KEY). Creates a signed JWT,
 * exchanges it for an identity token, and caches the result.
 */

interface ServiceAccountKey {
  client_email: string;
  private_key: string;
}

interface CachedToken {
  token: string;
  expiresAt: number;
}

// Module-level cache for the identity token
let cachedToken: CachedToken | null = null;

/**
 * Base64URL encode a Uint8Array (no padding, URL-safe)
 */
function base64UrlEncode(data: Uint8Array): string {
  let binary = '';
  for (let i = 0; i < data.length; i++) {
    binary += String.fromCharCode(data[i]);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

/**
 * Base64URL encode a string
 */
function base64UrlEncodeString(str: string): string {
  return base64UrlEncode(new TextEncoder().encode(str));
}

/**
 * Parse PEM private key and import as CryptoKey for RS256 signing
 */
async function importPrivateKey(pem: string): Promise<CryptoKey> {
  // Strip PEM header/footer and whitespace
  const pemBody = pem
    .replace(/-----BEGIN PRIVATE KEY-----/g, '')
    .replace(/-----END PRIVATE KEY-----/g, '')
    .replace(/-----BEGIN RSA PRIVATE KEY-----/g, '')
    .replace(/-----END RSA PRIVATE KEY-----/g, '')
    .replace(/\s/g, '');

  // Decode base64 to ArrayBuffer
  const binaryString = atob(pemBody);
  const bytes = new Uint8Array(binaryString.length);
  for (let i = 0; i < binaryString.length; i++) {
    bytes[i] = binaryString.charCodeAt(i);
  }

  return crypto.subtle.importKey(
    'pkcs8',
    bytes.buffer,
    { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' },
    false,
    ['sign']
  );
}

/**
 * Create and sign a JWT for Google token exchange
 */
async function createSignedJwt(
  clientEmail: string,
  privateKey: string,
  targetAudience: string
): Promise<string> {
  const now = Math.floor(Date.now() / 1000);

  const header = {
    alg: 'RS256',
    typ: 'JWT',
  };

  const payload = {
    iss: clientEmail,
    sub: clientEmail,
    aud: 'https://oauth2.googleapis.com/token',
    target_audience: targetAudience,
    iat: now,
    exp: now + 3600,
  };

  const headerEncoded = base64UrlEncodeString(JSON.stringify(header));
  const payloadEncoded = base64UrlEncodeString(JSON.stringify(payload));
  const unsignedToken = `${headerEncoded}.${payloadEncoded}`;

  const key = await importPrivateKey(privateKey);
  const signatureBuffer = await crypto.subtle.sign(
    'RSASSA-PKCS1-v1_5',
    key,
    new TextEncoder().encode(unsignedToken)
  );

  const signature = base64UrlEncode(new Uint8Array(signatureBuffer));
  return `${unsignedToken}.${signature}`;
}

/**
 * Exchange a signed JWT for a Google identity token
 */
async function exchangeJwtForIdToken(signedJwt: string): Promise<string> {
  const body = `grant_type=${encodeURIComponent('urn:ietf:params:oauth:grant-type:jwt-bearer')}&assertion=${encodeURIComponent(signedJwt)}`;

  const response = await fetch('https://oauth2.googleapis.com/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });

  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`Token exchange failed (${response.status}): ${errorText}`);
  }

  const data = (await response.json()) as { id_token: string };
  return data.id_token;
}

/**
 * Get a Google identity token for authenticating to Cloud Run.
 * Returns null if GOOGLE_SA_KEY is not configured.
 * Caches the token and refreshes 5 minutes before expiry.
 */
export async function getIdentityToken(env: Env): Promise<string | null> {
  if (!env.GOOGLE_SA_KEY) {
    return null;
  }

  const now = Math.floor(Date.now() / 1000);

  // Return cached token if still valid (refresh 5 min before expiry)
  if (cachedToken && cachedToken.expiresAt - now > 300) {
    return cachedToken.token;
  }

  const saKey: ServiceAccountKey = JSON.parse(env.GOOGLE_SA_KEY);
  const targetAudience = env.BACKEND_URL;

  const signedJwt = await createSignedJwt(
    saKey.client_email,
    saKey.private_key,
    targetAudience
  );

  const idToken = await exchangeJwtForIdToken(signedJwt);

  // Cache with 1 hour expiry (matching JWT exp)
  cachedToken = {
    token: idToken,
    expiresAt: now + 3600,
  };

  return idToken;
}

// Exported for testing
export { createSignedJwt, base64UrlEncode, base64UrlEncodeString };

/**
 * Reset the token cache (used in tests)
 */
export function resetTokenCache(): void {
  cachedToken = null;
}
