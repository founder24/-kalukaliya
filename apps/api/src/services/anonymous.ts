/**
 * Anonymous-browser identity and quota-key helpers.
 *
 * The browser-generated ID is the canonical anonymous identity for chat,
 * history, and credit balances. When browser storage is unavailable, a signed
 * edge cookie is preferred before the final connection-IP fallback.
 */

export const BROWSER_ANON_ID_PATTERN = /^anon_[a-f0-9]{32}$/;
export const ANONYMOUS_COOKIE_NAME = 'syrabit_anon_id';
export const CHAT_RPM_LIMIT = 6;
export const ANONYMOUS_DAILY_LIMIT = CHAT_RPM_LIMIT;
export const ANONYMOUS_MONTHLY_LIMIT = CHAT_RPM_LIMIT;
const SIGNATURE_PATTERN = /^[a-f0-9]{64}$/;

export function isBrowserAnonId(value: string | null | undefined): value is string {
  return typeof value === 'string' && BROWSER_ANON_ID_PATTERN.test(value.trim());
}

export function currentQuotaPeriod(): string {
  // One UTC minute bucket, shared across Workers isolates through D1.
  return new Date().toISOString().slice(0, 16);
}

function cookieValue(cookieHeader: string, name: string): string | null {
  const prefix = `${name}=`;
  const part = cookieHeader.split(';')
    .map(value => value.trim())
    .find(value => value.startsWith(prefix));
  return part ? part.slice(prefix.length) : null;
}

function hex(bytes: ArrayBuffer): string {
  return Array.from(new Uint8Array(bytes))
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('');
}

function timingSafeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

async function signedCookieAnonId(req: Request, secret?: string): Promise<string | null> {
  if (!secret) return null;
  const encoded = cookieValue(req.headers.get('Cookie') ?? '', ANONYMOUS_COOKIE_NAME);
  if (!encoded) return null;

  let value: string;
  try {
    value = decodeURIComponent(encoded);
  } catch {
    return null;
  }
  const separator = value.lastIndexOf('.');
  if (separator < 0) return null;
  const id = value.slice(0, separator);
  const signature = value.slice(separator + 1);
  if (!isBrowserAnonId(id) || !SIGNATURE_PATTERN.test(signature)) return null;

  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const expected = hex(await crypto.subtle.sign('HMAC', key, encoder.encode(id)));
  return timingSafeEqual(signature, expected) ? id : null;
}

async function isTrustedEdgeRequest(req: Request, secret?: string): Promise<boolean> {
  if (!secret) return false;
  const timestamp = req.headers.get('X-Edge-Timestamp');
  const signature = req.headers.get('X-Edge-Signature');
  const userId = req.headers.get('X-User-ID') ?? 'anonymous';
  if (!timestamp || !signature || !SIGNATURE_PATTERN.test(signature)) return false;
  const timestampSeconds = Number.parseInt(timestamp, 10);
  if (!Number.isSafeInteger(timestampSeconds)
    || Math.abs(Math.floor(Date.now() / 1000) - timestampSeconds) > 120) {
    return false;
  }

  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const pathname = new URL(req.url).pathname;
  const message = `${timestamp}:${userId}:${pathname}`;
  const expected = hex(await crypto.subtle.sign('HMAC', key, encoder.encode(message)));
  return timingSafeEqual(signature, expected);
}

export async function anonUserId(req: Request, cookieSecret?: string): Promise<string> {
  const cookieId = await signedCookieAnonId(req, cookieSecret);
  if (cookieId) return cookieId;

  const browserId = req.headers.get('x-anon-id')?.trim();
  if (isBrowserAnonId(browserId) && await isTrustedEdgeRequest(req, cookieSecret)) {
    return browserId;
  }

  // Cloudflare supplies and overwrites this header before either Worker runs.
  // Forwarding headers are intentionally excluded because direct callers can
  // forge them and otherwise impersonate fallback-owned anonymous history.
  const ip = req.headers.get('CF-Connecting-IP') ?? 'unknown';
  const normalizedIp = ip.trim().toLowerCase().replace(/[^a-z0-9]/g, '_').slice(0, 55);
  return `ip_${normalizedIp}`;
}

export function anonymousQuotaKey(anonId: string, period = currentQuotaPeriod()): string {
  return `anon_quota:${anonId}:${period}`;
}