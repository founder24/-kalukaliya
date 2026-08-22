export function getCorsHeaders(origin: string, allowedOrigins: string): HeadersInit {
  const allowed = allowedOrigins.split(',').map(o => o.trim());
  const isAllowed = allowed.includes(origin) || allowed.includes('*');
  const effectiveOrigin = isAllowed ? origin : (allowed[0] ?? 'https://syrabit.ai');

  return {
    'Access-Control-Allow-Origin': effectiveOrigin,
    'Access-Control-Allow-Methods': 'GET, POST, PUT, PATCH, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Request-ID, X-Cron-Token, X-Edge-Secret',
    'Access-Control-Allow-Credentials': 'true',
    'Access-Control-Max-Age': '86400',
    'Vary': 'Origin',
  };
}

export function applyCors(headers: Headers, origin: string, allowedOrigins: string): void {
  const corsHeaders = getCorsHeaders(origin, allowedOrigins);
  for (const [key, value] of Object.entries(corsHeaders)) {
    headers.set(key, value);
  }
}
