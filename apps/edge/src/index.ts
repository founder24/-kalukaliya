/**
 * Syrabit Edge Worker — Cloudflare Workers Entry Point
 *
 * Request pipeline:
 *   1. CORS preflight
 *   2. JWT verification (all /api/ except public paths)
 *   3. Bot heuristic tagging (for ISR routing and analytics)
 *   4. Per-language rate limiting (chat POST endpoints)
 *   5. Route to backend proxy or R2 assets
 */

import { getCorsHeaders, applyCorsHeaders } from './middleware/cors';
import { verifyJWT } from './middleware/jwt';
import { checkRateLimit, rateLimitHeaders } from './middleware/rate-limit';
import { proxyRequest } from './routes/api-proxy';
import { handleContentKV } from './routes/content-kv';
import { handleISR } from './routes/isr';
import { handleRobots } from './routes/robots';
import { getIdentityToken } from './utils/google-auth';

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

    // ── Generate Request ID for all requests ──
    const requestId = crypto.randomUUID();
    const reqIdHeaders = new Headers(request.headers);
    reqIdHeaders.set('X-Request-ID', requestId);
    request = new Request(request, { headers: reqIdHeaders });

    // ── Production safety: reject if backend URL is localhost in production ──
    const isProduction = !env.ALLOWED_ORIGIN?.includes('localhost');
    const isLocalBackend = env.BACKEND_URL?.includes('localhost') || env.BACKEND_URL?.includes('127.0.0.1');
    if (isProduction && isLocalBackend && (url.pathname.startsWith('/api/') || url.pathname.startsWith('/health/'))) {
      const errResponse = new Response(JSON.stringify({ error: 'Backend URL misconfiguration detected' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      });
      errResponse.headers.set('X-Request-ID', requestId);
      applyCorsHeaders(errResponse.headers, request.headers.get('Origin') || '');
      return errResponse;
    }

    // ── 2. JWT Verification (all /api/ routes except public) ──
    if (url.pathname.startsWith('/api/')) {
      const jwtResult = await verifyJWT(request, env.JWT_SECRET, env.JWT_PUBLIC_KEY);

      if (!jwtResult.valid && jwtResult.error !== 'Missing or invalid Authorization header') {
        // Token was provided but is invalid/expired — reject
        const errResp = jsonResponse(401, { error: jwtResult.error || 'Unauthorized' });
        errResp.headers.set('X-Request-ID', requestId);
        applyCorsHeaders(errResp.headers, request.headers.get('Origin') || '');
        return errResp;
      }

      // Inject authenticated user ID (or 'anonymous') for downstream backend
      const headers = new Headers(request.headers);
      headers.set('X-User-ID', jwtResult.userId || 'anonymous');
      if (jwtResult.valid && env.EDGE_SHARED_SECRET) {
        headers.set('X-Edge-Secret', env.EDGE_SHARED_SECRET);
      }
      request = new Request(request, { headers });
    }

    // ── 3. Bot Heuristic Tagging (for ISR routing and analytics) ──
    // NOTE: Bots are NOT blocked here - they are tagged only (X-Bot-Detected header)
    // for ISR routing and analytics. Edge never returns 403 for bot-detected requests.
    if (url.pathname.startsWith('/api/')) {
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

      // Authenticated users get a much higher hourly limit — their usage is
      // traceable and the backend's monthly quota is the real enforcement gate.
      // Anonymous users keep the strict 30 req/hr burst-protection limit.
      const edgeLimit = userId === 'anonymous' ? 30 : 500;
      const rl = await checkRateLimit(env.RATE_LIMIT_KV, userId, lang, edgeLimit);
      if (!rl.allowed) {
        const rlResponse = new Response(
          JSON.stringify({ error: 'Rate limit exceeded' }),
          {
            status: 429,
            headers: {
              'Content-Type': 'application/json',
              ...rateLimitHeaders(rl, edgeLimit),
            },
          }
        );
        rlResponse.headers.set('X-Request-ID', requestId);
        applyCorsHeaders(rlResponse.headers, request.headers.get('Origin') || '');
        return rlResponse;
      }

      // Signal to backend that edge already performed rate limiting
      const rlHeaders = new Headers(request.headers);
      rlHeaders.set('X-Rate-Limited-By', 'edge');
      request = new Request(request, { headers: rlHeaders });
    }

    // ── 5. Routing ──

    // Block scanner-bait and sensitive paths immediately — never proxy or redirect these.
    // Cloudflare Pages SPA returns 200 for unknown routes (SPA fallback), so these paths
    // look "accessible" to scanners. Return an explicit 404 here at the edge.
    const BLOCKED_PATH_PATTERNS = [
      /^\/\.env(\.|$)/i,
      /^\/\.git\//i,
      /^\/\.git$/i,
      /^\/\.svn\//i,
      /^\/\.htaccess$/i,
      /^\/\.DS_Store$/i,
      /^\/web\.config$/i,
      /^\/phpinfo\.php$/i,
      /^\/server-status$/i,
      /^\/wp-admin/i,
      /^\/wp-login\.php$/i,
      /^\/xmlrpc\.php$/i,
      /^\/config\.(php|yml|yaml|json)$/i,
      /^\/\.well-known\/traffic-advice$/i,
    ];
    if (BLOCKED_PATH_PATTERNS.some((re) => re.test(url.pathname))) {
      return new Response('Not Found', { status: 404, headers: { 'X-Request-ID': requestId } });
    }

    // Block path traversal sequences — Cloudflare normalises `/../` in the URL
    // but double-encoded or edge-case forms may survive. Return 404 (not 302).
    if (url.pathname.includes('..') || url.pathname.includes('%2e%2e') || url.pathname.includes('%2E%2E')) {
      return new Response(
        JSON.stringify({ detail: 'Not Found' }),
        { status: 404, headers: { 'Content-Type': 'application/json', 'X-Request-ID': requestId } }
      );
    }

    // Robots.txt
    if (url.pathname === '/robots.txt') {
      const robotsResponse = await handleRobots(env);
      robotsResponse.headers.set('X-Request-ID', requestId);
      return robotsResponse;
    }

    // Sitemap proxy → backend (rewrite path to /api/v1/seo prefix)
    if (url.pathname.startsWith('/sitemap') && url.pathname.endsWith('.xml')) {
      const rewrittenUrl = new URL(request.url);
      rewrittenUrl.pathname = `/api/v1/seo${url.pathname}`;
      const rewrittenRequest = new Request(rewrittenUrl.toString(), request);
      const sitemapResponse = await proxyRequest(rewrittenRequest, env.BACKEND_URL, env);
      sitemapResponse.headers.set('X-Request-ID', requestId);
      return sitemapResponse;
    }

    /**
     * Edge-level health check with backend reachability ping.
     * Checks: Edge own status + lightweight backend reachability (2s timeout).
     * Returns backend_reachable: true/false without failing the edge health.
     * Result is cached for 10s to avoid blocking every health poll.
     */
    if (url.pathname === '/health') {
      // Only GET and HEAD are valid on health endpoints
      if (request.method !== 'GET' && request.method !== 'HEAD') {
        const methodNotAllowed = new Response(
          JSON.stringify({ error: 'Method Not Allowed' }),
          { status: 405, headers: { 'Content-Type': 'application/json', 'Allow': 'GET, HEAD' } }
        );
        methodNotAllowed.headers.set('X-Request-ID', requestId);
        return methodNotAllowed;
      }

      let backendReachable = false;
      const now = Date.now();

      // Layer 1: Module-level in-memory cache (10s TTL, per-isolate)
      if (healthCache && (now - healthCache.timestamp) < HEALTH_CACHE_TTL_MS) {
        backendReachable = healthCache.backendReachable;
      } else if (!env.ISR_CACHE_KV) {
        // KV not bound - skip KV layer, fetch backend directly
        backendReachable = await fetchBackendHealth(env.BACKEND_URL, env);
        healthCache = { backendReachable, timestamp: now };
      } else {
        // Layer 2: KV cache (30s TTL, globally shared across all PoPs)
        try {
          const kvCached = await env.ISR_CACHE_KV.get('edge:health');
          if (kvCached) {
            const parsed = JSON.parse(kvCached) as { backend_reachable: boolean };
            backendReachable = parsed.backend_reachable;
            // Refresh in-memory cache from KV
            healthCache = { backendReachable, timestamp: now };
          } else {
            // Layer 3: Fresh backend fetch
            backendReachable = await fetchBackendHealth(env.BACKEND_URL, env);
            healthCache = { backendReachable, timestamp: now };
            // Store in KV for other PoPs (30s TTL)
            const kvPayload = JSON.stringify({ backend_reachable: backendReachable });
            ctx.waitUntil(env.ISR_CACHE_KV.put('edge:health', kvPayload, { expirationTtl: 30 }));
          }
        } catch {
          // KV read failed - fall back to direct fetch
          backendReachable = await fetchBackendHealth(env.BACKEND_URL, env);
          healthCache = { backendReachable, timestamp: now };
        }
      }

      const healthResponse = new Response(
        JSON.stringify({
          status: 'healthy',
          service: 'syrabit-backend',
          timestamp: new Date().toISOString(),
          backend_reachable: backendReachable,
        }),
        {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }
      );
      const securedHealth = addSecurityHeaders(healthResponse);
      const healthOrigin = request.headers.get('Origin') || '';
      applyCorsHeaders(securedHealth.headers, healthOrigin);
      securedHealth.headers.set('X-Request-ID', requestId);
      return securedHealth;
    }

    /**
     * Full health check: Edge + full backend dependency health.
     * Calls backend /health/deep which checks MongoDB, Redis, Vertex AI Search.
     * Returns aggregated status: "healthy" if all pass, "degraded" if backend unreachable.
     */
    if (url.pathname === '/health/full') {
      if (request.method !== 'GET' && request.method !== 'HEAD') {
        const methodNotAllowed = new Response(
          JSON.stringify({ error: 'Method Not Allowed' }),
          { status: 405, headers: { 'Content-Type': 'application/json', 'Allow': 'GET, HEAD' } }
        );
        methodNotAllowed.headers.set('X-Request-ID', requestId);
        return methodNotAllowed;
      }

      const edgeStatus = { status: 'healthy', timestamp: new Date().toISOString() };
      let backendStatus: Record<string, unknown>;
      let overallStatus: 'healthy' | 'degraded' = 'healthy';

      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);
        const headers: Record<string, string> = {};
        const token = await getIdentityToken(env);
        if (token) {
          headers['Authorization'] = `Bearer ${token}`;
        }
        const res = await fetch(`${env.BACKEND_URL}/health/deep`, {
          signal: controller.signal,
          headers,
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
      const securedFull = addSecurityHeaders(fullHealthResponse);
      const fullHealthOrigin = request.headers.get('Origin') || '';
      applyCorsHeaders(securedFull.headers, fullHealthOrigin);
      securedFull.headers.set('X-Request-ID', requestId);
      return securedFull;
    }

    // ── Library bundle KV cache (stale-while-revalidate) ─────────────────────
    // /api/v1/content/library-bundle is static curriculum metadata (~79 KB)
    // that changes only on admin deploys. Serve from KV to decouple the
    // library page from backend/MongoDB availability entirely.
    //
    // Strategy:
    //   HIT  (age < FRESH_TTL)  → return cached, no backend call
    //   STALE (age ≥ FRESH_TTL) → return cached immediately, revalidate in bg
    //   MISS                    → proxy to backend, populate cache on 200
    //   MISS + backend error    → return backend error as-is (no data yet)
    if (
      url.pathname === '/api/v1/content/library-bundle' &&
      request.method === 'GET' &&
      env.ISR_CACHE_KV
    ) {
      const FRESH_TTL_S = 300;    // 5 min — serve fresh without revalidation
      const HARD_TTL_S  = 7200;   // 2 hr  — max KV expiry; KV auto-deletes after
      const qs = url.search || '';
      const cacheKey = `api:library-bundle:${qs}`;

      const origin = request.headers.get('Origin') || '';

      const cached = await env.ISR_CACHE_KV.get(cacheKey).catch(() => null);
      if (cached) {
        let payload: { body: string; cachedAt: number } | null = null;
        try { payload = JSON.parse(cached); } catch { /* corrupt — treat as miss */ }

        if (payload) {
          const ageS = Math.floor(Date.now() / 1000) - payload.cachedAt;
          const isStale = ageS >= FRESH_TTL_S;

          if (isStale) {
            // Background revalidation — do NOT await; user already has the response
            ctx.waitUntil(
              proxyRequest(new Request(request.url, request), env.BACKEND_URL, env)
                .then(async r => {
                  if (r.status === 200) {
                    const freshBody = await r.text();
                    const entry = JSON.stringify({ body: freshBody, cachedAt: Math.floor(Date.now() / 1000) });
                    return env.ISR_CACHE_KV.put(cacheKey, entry, { expirationTtl: HARD_TTL_S });
                  }
                })
                .catch(() => { /* revalidation failed — stale cache stays */ })
            );
          }

          const resp = new Response(payload.body, {
            status: 200,
            headers: {
              'Content-Type': 'application/json',
              'X-Cache': isStale ? 'STALE' : 'HIT',
              'X-Cache-Age': String(ageS),
            },
          });
          const secured = addSecurityHeaders(resp);
          applyCorsHeaders(secured.headers, origin);
          secured.headers.set('X-Request-ID', requestId);
          return secured;
        }
      }

      // Cache MISS — proxy to backend and populate cache on success
      const backendResp = await proxyRequest(request, env.BACKEND_URL, env);
      if (backendResp.status === 200) {
        const body = await backendResp.text();
        const entry = JSON.stringify({ body, cachedAt: Math.floor(Date.now() / 1000) });
        ctx.waitUntil(
          env.ISR_CACHE_KV.put(cacheKey, entry, { expirationTtl: HARD_TTL_S })
            .catch(() => { /* KV write failure is non-fatal */ })
        );
        const missResp = new Response(body, {
          status: 200,
          headers: { 'Content-Type': 'application/json', 'X-Cache': 'MISS' },
        });
        const secured = addSecurityHeaders(missResp);
        applyCorsHeaders(secured.headers, origin);
        secured.headers.set('X-Request-ID', requestId);
        return secured;
      }

      // Backend error and no cache — pass the error through as-is
      const secured = addSecurityHeaders(backendResp);
      applyCorsHeaders(secured.headers, origin);
      secured.headers.set('X-Request-ID', requestId);
      return secured;
    }

    // ── Edge-handled AI routes (Workers AI binding — never hit the Python backend) ──

    /**
     * TTS: POST /api/v1/chat/tts
     * Body: { text: string, lang: "en" | "as" }
     * Returns: audio/mpeg binary
     *
     * Handled entirely at the edge using env.AI so the backend never receives
     * large audio payloads and Cloud Run compute is not wasted on audio synthesis.
     */
    if (url.pathname === '/api/v1/chat/tts' && request.method === 'POST') {
      const origin = request.headers.get('Origin') || '';
      const userId = request.headers.get('X-User-ID');
      if (!userId || userId === 'anonymous') {
        const r = jsonResponse(401, { error: 'Authentication required' });
        applyCorsHeaders(r.headers, origin);
        r.headers.set('X-Request-ID', requestId);
        return r;
      }

      if (!env.AI) {
        // AI binding not configured — fall through to backend proxy
      } else {
        try {
          const body = await request.json() as { text?: string; lang?: string };
          const text = (body.text || '').trim().slice(0, 5000);
          if (!text) {
            const r = jsonResponse(400, { error: 'text is required' });
            applyCorsHeaders(r.headers, origin);
            r.headers.set('X-Request-ID', requestId);
            return r;
          }
          const lang = (body.lang || 'en').toLowerCase().startsWith('as') ? 'AS' : 'EN';
          const aiResult = await env.AI.run('@cf/myshell/melotts', { prompt: text, language: lang });
          // Workers AI TTS returns a Response with audio content
          const audioBytes = aiResult instanceof Response
            ? await aiResult.arrayBuffer()
            : new ArrayBuffer(0);
          const ttsResp = new Response(audioBytes, {
            status: 200,
            headers: { 'Content-Type': 'audio/mpeg' },
          });
          applyCorsHeaders(ttsResp.headers, origin);
          ttsResp.headers.set('X-Request-ID', requestId);
          return ttsResp;
        } catch (err) {
          const r = jsonResponse(500, { error: 'TTS failed', detail: err instanceof Error ? err.message : 'unknown' });
          applyCorsHeaders(r.headers, origin);
          r.headers.set('X-Request-ID', requestId);
          return r;
        }
      }
    }

    /**
     * OCR: POST /api/v1/chat/image
     * Body: multipart/form-data — "file" (image), "prompt" (optional string)
     * Returns: { text: string, model: string }
     *
     * Handled at the edge with Workers AI vision model so image bytes never
     * travel over the internet to Cloud Run.
     */
    if (url.pathname === '/api/v1/chat/image' && request.method === 'POST') {
      const origin = request.headers.get('Origin') || '';
      const userId = request.headers.get('X-User-ID');
      if (!userId || userId === 'anonymous') {
        const r = jsonResponse(401, { error: 'Authentication required' });
        applyCorsHeaders(r.headers, origin);
        r.headers.set('X-Request-ID', requestId);
        return r;
      }

      if (!env.AI) {
        // AI binding not configured — fall through to backend proxy
      } else {
        try {
          const form = await request.formData();
          const file = form.get('file') as File | null;
          const prompt = (form.get('prompt') as string | null) || 'Extract all text from this image. Return only the text content, no commentary.';

          if (!file) {
            const r = jsonResponse(400, { error: 'file is required' });
            applyCorsHeaders(r.headers, origin);
            r.headers.set('X-Request-ID', requestId);
            return r;
          }

          const imageBytes = await file.arrayBuffer();
          if (imageBytes.byteLength > 4 * 1024 * 1024) {
            const r = jsonResponse(400, { error: 'Image must be less than 4MB' });
            applyCorsHeaders(r.headers, origin);
            r.headers.set('X-Request-ID', requestId);
            return r;
          }

          const imageArray = [...new Uint8Array(imageBytes)];
          const aiResult = await env.AI.run('@cf/unum/uform-gen2-qwen-500m', {
            image: imageArray,
            prompt,
            max_tokens: 512,
          }) as { description?: string; response?: string };

          const text = (aiResult.description || aiResult.response || '').trim();
          const ocrResp = new Response(
            JSON.stringify({ text, model: '@cf/unum/uform-gen2-qwen-500m' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          );
          applyCorsHeaders(ocrResp.headers, origin);
          ocrResp.headers.set('X-Request-ID', requestId);
          return ocrResp;
        } catch (err) {
          const r = jsonResponse(500, { error: 'OCR failed', detail: err instanceof Error ? err.message : 'unknown' });
          applyCorsHeaders(r.headers, origin);
          r.headers.set('X-Request-ID', requestId);
          return r;
        }
      }
    }

    // API routes → proxy to backend
    // Note: /health/full is handled above; remaining /health/ sub-paths (e.g. /health/deep)
    // are proxied to backend. /health is handled at edge above.
    if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/health/')) {
      const response = await proxyRequest(request, env.BACKEND_URL, env);
      const secured = addSecurityHeaders(response);
      const origin = request.headers.get('Origin') || '';
      applyCorsHeaders(secured.headers, origin);
      secured.headers.set('X-Request-ID', requestId);
      return secured;
    }

    // Static assets → serve from R2
    if (url.pathname.startsWith('/assets/')) {
      const key = url.pathname.replace('/assets/', '');
      const object = await env.R2_BUCKET.get(key);

      if (!object) {
        const notFoundResp = new Response('Not Found', { status: 404 });
        notFoundResp.headers.set('X-Request-ID', requestId);
        return notFoundResp;
      }

      const headers = new Headers();
      object.writeHttpMetadata(headers);
      headers.set('Cache-Control', 'public, max-age=31536000, immutable');
      headers.set('Access-Control-Allow-Origin', env.ALLOWED_ORIGIN || 'https://syrabit.ai');
      headers.set('X-Request-ID', requestId);

      return new Response(object.body, { headers });
    }

    // CONTENT_KV: serve pre-rendered HTML to bots for chapter pages
    const kvResponse = await handleContentKV(request, env);
    if (kvResponse) {
      const secured = addSecurityHeaders(kvResponse);
      secured.headers.set('X-Request-ID', requestId);
      return secured;
    }

    // ISR fallback for bot traffic (before 404)
    const isrResponse = await handleISR(request, env, ctx);
    if (isrResponse) {
      isrResponse.headers.set('X-Request-ID', requestId);
      return isrResponse;
    }

    // ── Direct redirect for root path to /library ──
    if (url.pathname === '/') {
      const frontendOrigin = env.ALLOWED_ORIGIN || 'https://syrabit.ai';
      const rootRedirect = new Response(null, {
        status: 302,
        headers: {
          'Location': `${frontendOrigin}/library`,
          'Cache-Control': 'public, s-maxage=3600',
          'X-Request-ID': requestId,
        },
      });
      return rootRedirect;
    }

    // ── CF Cache API for non-API GET requests ──
    // Cache redirect responses so repeat visitors get them instantly from edge cache.
    if (request.method === 'GET' || request.method === 'HEAD') {
      const cache = caches.default;
      const cached = await cache.match(request);
      if (cached) {
        const cachedResp = new Response(cached.body, cached);
        cachedResp.headers.set('X-Request-ID', requestId);
        return cachedResp;
      }

      const frontendOrigin = env.ALLOWED_ORIGIN || 'https://syrabit.ai';
      // Guard: if the edge worker IS the frontend origin, return 404 to prevent infinite redirect loop
      const frontendHost = new URL(frontendOrigin).host;
      if (url.host === frontendHost) {
        const loopResp = new Response('Not Found', { status: 404 });
        loopResp.headers.set('X-Request-ID', requestId);
        return loopResp;
      }
      const redirectUrl = frontendOrigin + url.pathname + url.search;
      const redirectResponse = new Response(null, {
        status: 302,
        headers: {
          'Location': redirectUrl,
          'Cache-Control': 'public, s-maxage=3600, stale-while-revalidate=86400',
          'X-Request-ID': requestId,
        },
      });
      // Do not cache 302 redirects - only cache non-redirect responses
      return redirectResponse;
    }

    const finalResp = new Response('Not Found', { status: 404 });
    finalResp.headers.set('X-Request-ID', requestId);
    return finalResp;
  },
};

/** Fetch backend health with a 2s timeout. Returns true if backend responds with 2xx. */
async function fetchBackendHealth(backendUrl: string, env: Env): Promise<boolean> {
  try {
    // Fetch OIDC token BEFORE starting the abort timer so the 2s window
    // covers only the actual HTTP request to the backend, not token exchange.
    const headers: Record<string, string> = {};
    const token = await getIdentityToken(env);
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2000);
    const res = await fetch(`${backendUrl}/health`, { signal: controller.signal, headers });
    clearTimeout(timeoutId);
    return res.ok;
  } catch {
    return false;
  }
}

/** Add security headers to proxied responses. These are set at the edge to avoid duplication with Cloudflare's built-in headers. */
function addSecurityHeaders(response: Response): Response {
  const newResponse = new Response(response.body, response);
  newResponse.headers.set('X-Content-Type-Options', 'nosniff');
  newResponse.headers.set('X-Frame-Options', 'DENY');
  newResponse.headers.set('Strict-Transport-Security', 'max-age=31536000; includeSubDomains');
  newResponse.headers.set('X-XSS-Protection', '0');
  newResponse.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  newResponse.headers.set('Content-Security-Policy', "default-src 'self'; script-src 'self' https://static.cloudflareinsights.com https://app.posthog.com https://browser.sentry-cdn.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; font-src 'self' https://fonts.gstatic.com; connect-src 'self' https://*.syrabit.ai https://app.posthog.com https://*.sentry.io https://*.ingest.sentry.io; frame-ancestors 'none'");
  return newResponse;
}

/** Helper to create JSON error responses */
function jsonResponse(status: number, body: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
