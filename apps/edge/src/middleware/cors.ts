/**
 * CORS Middleware - Strict Cross-Origin Resource Sharing Policy
 */

export function getCorsHeaders(origin: string): Record<string, string> {
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, CF-Turnstile-Response',
    'Access-Control-Max-Age': '86400',
  };
}

/** @deprecated Use getCorsHeaders(origin) for dynamic origin support */
export const corsHeaders = getCorsHeaders('https://syrabit.ai');

export function applyCorsHeaders(headers: Headers, origin?: string): void {
  const cors = origin ? getCorsHeaders(origin) : corsHeaders;
  Object.entries(cors).forEach(([key, value]) => {
    headers.set(key, value);
  });
}
