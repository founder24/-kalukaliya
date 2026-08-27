/**
 * Anonymous-browser identity and quota-key helpers.
 *
 * The browser-generated ID is the canonical anonymous identity for chat,
 * history, and credit balances. IP-derived IDs exist only as a fallback for
 * storage-restricted browsers.
 */

export const BROWSER_ANON_ID_PATTERN = /^anon_[a-f0-9]{32}$/;
export const ANONYMOUS_MONTHLY_LIMIT = 30;

export function isBrowserAnonId(value: string | null | undefined): value is string {
  return typeof value === 'string' && BROWSER_ANON_ID_PATTERN.test(value.trim());
}

export function currentQuotaPeriod(): string {
  const d = new Date();
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

export function anonUserId(req: Request): string {
  const browserId = req.headers.get('x-anon-id')?.trim();
  if (isBrowserAnonId(browserId)) return browserId;

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