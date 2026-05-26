/**
 * CORS Middleware - Strict Cross-Origin Resource Sharing Policy
 */

const ALLOWED_ORIGINS = ['https://syrabit.ai', 'https://app.syrabit.ai', 'https://www.syrabit.ai'];

export function getCorsHeaders(origin: string): Record<string, string> {
  const validOrigin = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    'Access-Control-Allow-Origin': validOrigin,
    'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, CF-Turnstile-Response, x-turnstile-token',
    'Access-Control-Max-Age': '86400',
  };
}

/** @deprecated Use getCorsHeaders(origin) for dynamic origin support */
export const corsHeaders = getCorsHeaders('https://syrabit.ai');

export function applyCorsHeaders(headers: Headers, origin?: string): void {
  const validOrigin = origin && ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  const cors = getCorsHeaders(validOrigin);
  Object.entries(cors).forEach(([key, value]) => {
    headers.set(key, value);
  });
}
