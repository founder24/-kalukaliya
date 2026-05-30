/**
 * CORS Middleware - Cross-Origin Resource Sharing Policy
 * Supports production domains and Cloudflare Pages preview deployments.
 */

const ALLOWED_ORIGINS = ['https://syrabit.ai', 'https://www.syrabit.ai', 'https://app.syrabit.ai'];
const PAGES_PREVIEW_REGEX = /^https:\/\/[a-z0-9-]+\.syrabitfrontend\.pages\.dev$/;

function isAllowedOrigin(origin: string): boolean {
  return ALLOWED_ORIGINS.includes(origin) || PAGES_PREVIEW_REGEX.test(origin);
}

export function getCorsHeaders(origin: string): Record<string, string> {
  const validOrigin = isAllowedOrigin(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    'Access-Control-Allow-Origin': validOrigin,
    'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, x-anon-id, traceparent',
    'Access-Control-Allow-Credentials': 'true',
    'Access-Control-Max-Age': '86400',
    'Access-Control-Expose-Headers': 'X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset, X-Request-ID',
  };
}

export function applyCorsHeaders(headers: Headers, origin?: string): void {
  const validOrigin = origin && isAllowedOrigin(origin) ? origin : ALLOWED_ORIGINS[0];
  const cors = getCorsHeaders(validOrigin);
  Object.entries(cors).forEach(([key, value]) => {
    headers.set(key, value);
  });
}
