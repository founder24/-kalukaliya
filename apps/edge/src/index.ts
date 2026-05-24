/**
 * Syrabit Edge Worker — Cloudflare Workers Entry Point
 *
 * Request pipeline:
 *   1. CORS preflight
 *   2. JWT verification (all /api/ except public paths)
 *   3. Turnstile bot protection (chat/auth endpoints, if token present)
 *   4. Per-language rate limiting (chat POST endpoints)
 *   5. Route to backend proxy or R2 assets
 */

import { getCorsHeaders } from './middleware/cors';
import { turnstileVerify } from './middleware/bot';
import { verifyJWT } from './middleware/jwt';
import { checkRateLimit, rateLimitHeaders } from './middleware/rate-limit';
import { proxyRequest } from './routes/api-proxy';

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // ── 1. CORS Preflight ──
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        headers: getCorsHeaders(env.ALLOWED_ORIGIN || 'https://syrabit.ai'),
      });
    }

    // ── 2. JWT Verification (all /api/ routes except public) ──
    if (url.pathname.startsWith('/api/')) {
      const jwtResult = await verifyJWT(request, env.JWT_SECRET);

      if (!jwtResult.valid && jwtResult.error !== 'Missing or invalid Authorization header') {
        // Token was provided but is invalid/expired — reject
        return jsonResponse(401, { error: jwtResult.error || 'Unauthorized' });
      }

      // Inject authenticated user ID (or 'anonymous') for downstream backend
      const headers = new Headers(request.headers);
      headers.set('X-User-ID', jwtResult.userId || 'anonymous');
      request = new Request(request, { headers });
    }

    // ── 3. Turnstile Bot Protection (chat/auth endpoints) ──
    if (url.pathname.startsWith('/api/v1/chat') || url.pathname.startsWith('/api/v1/auth')) {
      const turnstileToken = request.headers.get('CF-Turnstile-Response');

      // Turnstile is MANDATORY for auth endpoints
      const isAuthEndpoint =
        url.pathname.startsWith('/api/v1/auth/signup') ||
        url.pathname.startsWith('/api/v1/auth/login') ||
        url.pathname.startsWith('/api/v1/auth/forgot-password');

      if (isAuthEndpoint && !turnstileToken) {
        return jsonResponse(403, { error: 'Bot verification required' });
      }

      if (turnstileToken) {
        const isValid = await turnstileVerify(turnstileToken, env.CF_TURNSTILE_SECRET);
        if (!isValid) {
          return jsonResponse(403, { error: 'Bot verification failed' });
        }
      }

      // Basic bot heuristic: reject requests with no User-Agent or known bot patterns
      const ua = request.headers.get('User-Agent') || '';
      if (!ua || /bot|crawl|spider|scrape|curl|wget|python-requests|httpie/i.test(ua)) {
        // Still allow through but tag as bot for analytics filtering
        const headers = new Headers(request.headers);
        headers.set('X-Bot-Detected', 'true');
        request = new Request(request, { headers });
      }
    }

    // ── 4. Per-Language Rate Limiting (chat POST only) ──
    if (url.pathname.startsWith('/api/v1/chat') && request.method === 'POST') {
      const userId = request.headers.get('X-User-ID') || 'anonymous';

      // Best-effort lang extraction from request body
      let lang = 'en';
      try {
        const cloned = request.clone();
        const body = await cloned.json() as { lang?: string };
        if (body.lang === 'en' || body.lang === 'as') {
          lang = body.lang;
        }
      } catch {
        // Body parsing failed — default to 'en'
      }

      const rl = await checkRateLimit(env.RATE_LIMIT_KV, userId, lang);
      if (!rl.allowed) {
        return new Response(
          JSON.stringify({ error: 'Rate limit exceeded' }),
          {
            status: 429,
            headers: {
              'Content-Type': 'application/json',
              ...rateLimitHeaders(rl),
            },
          }
        );
      }
    }

    // ── 5. Routing ──

    // API routes → proxy to Azure backend
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/health')) {
      return proxyRequest(request, env.AZURE_BACKEND_URL, env);
    }

    // Static assets → serve from R2
    if (url.pathname.startsWith('/assets/')) {
      const key = url.pathname.replace('/assets/', '');
      const object = await env.R2_BUCKET.get(key);

      if (!object) {
        return new Response('Not Found', { status: 404 });
      }

      const headers = new Headers();
      object.writeHttpMetadata(headers);
      headers.set('Cache-Control', 'public, max-age=31536000, immutable');
      headers.set('Access-Control-Allow-Origin', env.ALLOWED_ORIGIN || 'https://syrabit.ai');

      return new Response(object.body, { headers });
    }

    // Fallback
    return new Response('Not Found', { status: 404 });
  },
};

/** Helper to create JSON error responses */
function jsonResponse(status: number, body: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
