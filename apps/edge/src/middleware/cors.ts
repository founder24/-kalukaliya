/**
 * CORS Middleware - Strict Cross-Origin Resource Sharing Policy
 */

export const corsHeaders = {
  'Access-Control-Allow-Origin': 'https://syrabit.ai',
  'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
  'Access-Control-Allow-Headers': 'Content-Type, Authorization, CF-Turnstile-Response',
  'Access-Control-Max-Age': '86400',
};

export function applyCorsHeaders(headers: Headers): void {
  Object.entries(corsHeaders).forEach(([key, value]) => {
    headers.set(key, value);
  });
}
