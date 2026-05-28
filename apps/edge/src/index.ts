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

import { getCorsHeaders, applyCorsHeaders } from './middleware/cors';
import { turnstileVerify } from './middleware/bot';
import { verifyJWT } from './middleware/jwt';
import { checkRateLimit, rateLimitHeaders } from './middleware/rate-limit';
import { proxyRequest } from './routes/api-proxy';
import { handleISR } from './routes/isr';
import { handleRobots } from './routes/robots';

// Cached health probe state (module-level)
let healthCache: { backendReachable: boolean; timestamp: number } | null = null;
const HEALTH_CACHE_TTL_MS = 10_000; // 10 seconds

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // ── 1. CORS Preflight ──
    if (request.method === 'OPTIONS') {
      const origin = request.headers.get('Origin') || 'https://syrabit.ai';
      return new Response(null, {
        headers: getCorsHeaders(origin),
      });
    }

    // Strip trust headers that only the edge itself should set
    const sanitizedHeaders = new Headers(request.headers);
    sanitizedHeaders.delete('X-Rate-Limited-By');
    sanitizedHeaders.delete('X-Edge-Secret');
    request = new Request(request, { headers: sanitizedHeaders });

    // ── Production safety: reject if backend URL is localhost in production ──
    const isProduction = !env.ALLOWED_ORIGIN?.includes('localhost');
    const isLocalBackend = env.AZURE_BACKEND_URL?.includes('localhost') || env.AZURE_BACKEND_URL?.includes('127.0.0.1');
    if (isProduction && isLocalBackend && (url.pathname.startsWith('/api/') || url.pathname.startsWith('/health/'))) {
      return new Response(JSON.stringify({ error: 'Backend URL misconfiguration detected' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
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
      if (env.EDGE_SHARED_SECRET) {
        headers.set('X-Edge-Secret', env.EDGE_SHARED_SECRET);
      }
      request = new Request(request, { headers });
    }

    // ── 3. Turnstile Bot Protection (chat/auth endpoints) ──
    // SEC: Turnstile verification is MANDATORY (not optional) for auth and chat POST.
    // Requests without a valid token are rejected with 403.
    if (url.pathname.startsWith('/api/v1/chat') || url.pathname.startsWith('/api/v1/ai/chat') || url.pathname.startsWith('/api/v1/auth')) {
      const turnstileToken = request.headers.get('x-turnstile-token') || request.headers.get('CF-Turnstile-Response');

      // Turnstile is MANDATORY for auth endpoints
      const isAuthEndpoint =
        url.pathname.startsWith('/api/v1/auth/signup') ||
        url.pathname.startsWith('/api/v1/auth/login') ||
        url.pathname.startsWith('/api/v1/auth/forgot-password');

      // Turnstile is MANDATORY for chat POST requests (except feedback)
      const isChatPost =
        (url.pathname.startsWith('/api/v1/chat') || url.pathname.startsWith('/api/v1/ai/chat')) &&
        request.method === 'POST' &&
        !url.pathname.startsWith('/api/v1/chat/feedback');

      if ((isAuthEndpoint || isChatPost) && !turnstileToken) {
        return jsonResponse(403, { error: 'Bot verification required' });
      }

      if (turnstileToken) {
        if (!env.CF_TURNSTILE_SECRET) {
          const isProduction = !env.ALLOWED_ORIGIN?.includes('localhost');
          if (isProduction) {
            return jsonResponse(403, { error: 'Bot verification temporarily unavailable' });
          }
          console.warn('CF_TURNSTILE_SECRET is not configured — skipping Turnstile verification in dev');
        } else {
          const isValid = await turnstileVerify(turnstileToken, env.CF_TURNSTILE_SECRET);
          if (!isValid) {
            return jsonResponse(403, { error: 'Bot verification failed' });
          }
        }
      }

      // Basic bot heuristic: tag requests with bot User-Agent for analytics filtering.
      // NOTE: Bots are NOT blocked here - they are tagged only (X-Bot-Detected header)
      // for ISR routing and analytics. Edge never returns 403 for bot-detected requests.
      const ua = request.headers.get('User-Agent') || '';
      if (!ua || /bot|crawl|spider|scrape|curl|wget|python-requests|httpie/i.test(ua)) {
        const headers = new Headers(request.headers);
        headers.set('X-Bot-Detected', 'true');
        request = new Request(request, { headers });
      }
    }

    // ── 4. Per-Language Rate Limiting (chat POST only) ──
    if (!env.RATE_LIMIT_KV && (url.pathname.startsWith('/api/v1/chat') || url.pathname.startsWith('/api/v1/ai/chat')) && request.method === 'POST') {
      console.warn('RATE_LIMIT_KV binding not available - rate limiting disabled');
    }
    if (env.RATE_LIMIT_KV && (url.pathname.startsWith('/api/v1/chat') || url.pathname.startsWith('/api/v1/ai/chat')) && request.method === 'POST') {
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

      // Signal to backend that edge already performed rate limiting
      const rlHeaders = new Headers(request.headers);
      rlHeaders.set('X-Rate-Limited-By', 'edge');
      request = new Request(request, { headers: rlHeaders });
    }

    // ── 5. Routing ──

    // Robots.txt
    if (url.pathname === '/robots.txt') {
      return handleRobots(env);
    }

    // Sitemap proxy → backend (rewrite path to /api/v1/seo prefix)
    if (url.pathname.startsWith('/sitemap') && url.pathname.endsWith('.xml')) {
      const rewrittenUrl = new URL(request.url);
      rewrittenUrl.pathname = `/api/v1/seo${url.pathname}`;
      const rewrittenRequest = new Request(rewrittenUrl.toString(), request);
      return proxyRequest(rewrittenRequest, env.AZURE_BACKEND_URL, env);
    }

    /**
     * Edge-level health check with backend reachability ping.
     * Checks: Edge own status + lightweight backend reachability (2s timeout).
     * Returns backend_reachable: true/false without failing the edge health.
     * Result is cached for 10s to avoid blocking every health poll.
     */
    if (url.pathname === '/health') {
      let backendReachable = false;
      const now = Date.now();

      if (healthCache && (now - healthCache.timestamp) < HEALTH_CACHE_TTL_MS) {
        // Use cached result
        backendReachable = healthCache.backendReachable;
      } else {
        // Cache is stale or empty - make a real fetch
        try {
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 2000);
          const res = await fetch(`${env.AZURE_BACKEND_URL}/health`, {
            signal: controller.signal,
          });
          clearTimeout(timeoutId);
          backendReachable = res.ok;
        } catch {
          backendReachable = false;
        }
        healthCache = { backendReachable, timestamp: now };
      }

      const healthResponse = new Response(
        JSON.stringify({
          status: 'healthy',
          service: 'syrabit-edge',
          timestamp: new Date().toISOString(),
          backend_reachable: backendReachable,
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }
      );
      return addSecurityHeaders(healthResponse);
    }

    /**
     * Full health check: Edge + full backend dependency health.
     * Calls backend /health/deep which checks MongoDB, Redis, Azure Search, Vertex AI.
     * Returns aggregated status: "healthy" if all pass, "degraded" if backend unreachable.
     */
    if (url.pathname === '/health/full') {
      const edgeStatus = { status: 'healthy', timestamp: new Date().toISOString() };
      let backendStatus: Record<string, unknown>;
      let overallStatus: 'healthy' | 'degraded' = 'healthy';

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);
        const res = await fetch(`${env.AZURE_BACKEND_URL}/health/deep`, {
          signal: controller.signal,
        });
        clearTimeout(timeoutId);
        backendStatus = await res.json() as Record<string, unknown>;
        if (!res.ok) {
          overallStatus = 'degraded';
        }
      } catch (err) {
        overallStatus = 'degraded';
        backendStatus = {
          status: 'unreachable',
          error: err instanceof Error ? err.message : 'timeout or connection refused',
        };
      }

      const fullHealthResponse = new Response(
        JSON.stringify({
          status: overallStatus,
          edge: edgeStatus,
          backend: backendStatus,
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }
      );
      return addSecurityHeaders(fullHealthResponse);
    }

    // API routes → proxy to Azure backend
    // Note: /health/full is handled above; remaining /health/ sub-paths (e.g. /health/deep)
    // are proxied to backend. /health is handled at edge above.
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/health/')) {
      const response = await proxyRequest(request, env.AZURE_BACKEND_URL, env);
      const secured = addSecurityHeaders(response);
      const origin = request.headers.get('Origin') || '';
      applyCorsHeaders(secured.headers, origin);
      return secured;
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

    // ISR fallback for bot traffic (before 404)
    const isrResponse = await handleISR(request, env, ctx);
    if (isrResponse) {
      return isrResponse;
    }

    // Fallback: redirect GET/HEAD to the frontend domain; 404 for other methods
    if (request.method === 'GET' || request.method === 'HEAD') {
      const frontendOrigin = env.ALLOWED_ORIGIN || 'https://syrabit.ai';
      // Guard: if the edge worker IS the frontend origin, return 404 to prevent infinite redirect loop
      const frontendHost = new URL(frontendOrigin).host;
      if (url.host === frontendHost) {
        return new Response('Not Found', { status: 404 });
      }
      const redirectUrl = frontendOrigin + url.pathname + url.search;
      return Response.redirect(redirectUrl, 302);
    }

    return new Response('Not Found', { status: 404 });
  },
};

/** Add security headers to proxied responses. These are set at the edge to avoid duplication with Cloudflare's built-in headers. */
function addSecurityHeaders(response: Response): Response {
  const newResponse = new Response(response.body, response);
  newResponse.headers.set('X-Content-Type-Options', 'nosniff');
  newResponse.headers.set('X-Frame-Options', 'DENY');
  newResponse.headers.set('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
  newResponse.headers.set('X-XSS-Protection', '0');
  newResponse.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  newResponse.headers.set('Content-Security-Policy', "default-src 'self'; script-src 'self' https://challenges.cloudflare.com https://static.cloudflareinsights.com https://app.posthog.com https://browser.sentry-cdn.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' https://fonts.gstatic.com; connect-src 'self' https://*.syrabit.ai https://app.posthog.com https://*.sentry.io https://*.ingest.sentry.io https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; frame-ancestors 'none'");
  return newResponse;
}

/** Helper to create JSON error responses */
function jsonResponse(status: number, body: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
