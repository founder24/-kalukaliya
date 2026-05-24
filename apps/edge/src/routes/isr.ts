/**
 * ISR (Incremental Static Regeneration) fallback for content routes.
 *
 * Serves cached pre-rendered HTML to bot crawlers (search engines and AI
 * agents) so they receive full content on first byte without executing JS.
 * Non-bot requests pass through to the existing SPA behaviour.
 *
 * Cache layer: Cloudflare KV (ISR_CACHE_KV) with a 1-hour TTL.
 * Backend source: GET /api/content/render/<path> returns rendered HTML.
 */

/** Bot user-agent patterns for search engines and AI crawlers */
const BOT_UA_PATTERNS = [
  /Googlebot/i,
  /Bingbot/i,
  /GPTBot/i,
  /ClaudeBot/i,
  /PerplexityBot/i,
  /Applebot/i,
  /facebookexternalhit/i,
  /Twitterbot/i,
  /LinkedInBot/i,
];

/**
 * Check whether the User-Agent indicates a known bot/crawler.
 */
function isBot(userAgent: string): boolean {
  return BOT_UA_PATTERNS.some((pattern) => pattern.test(userAgent));
}

/**
 * Check whether the pathname matches a content route:
 * - 3 segments: /{board}/{class}/{subject}
 * - 4 segments: /{board}/{class}/{subject}/{chapter}
 *
 * Excludes /api/, /assets/, and /health paths (handled upstream).
 */
function isContentRoute(pathname: string): boolean {
  if (
    pathname.startsWith('/api/') ||
    pathname.startsWith('/assets/') ||
    pathname.startsWith('/health')
  ) {
    return false;
  }

  // Strip leading slash and trailing slash, then count segments
  const trimmed = pathname.replace(/^\/+/, '').replace(/\/+$/, '');
  if (!trimmed) return false;

  const segments = trimmed.split('/');
  return segments.length === 3 || segments.length === 4;
}

/**
 * Handle ISR for bot requests to content routes.
 *
 * Returns a Response with cached/freshly-rendered HTML for bots, or null
 * if the request should fall through to existing behaviour (non-bot, non-
 * content route, or backend error).
 */
export async function handleISR(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
): Promise<Response | null> {
  // Only handle GET requests
  if (request.method !== 'GET') return null;

  const ua = request.headers.get('User-Agent') || '';
  if (!isBot(ua)) return null;

  const url = new URL(request.url);
  if (!isContentRoute(url.pathname)) return null;

  const cacheKey = `isr:${url.pathname}`;

  // Check KV cache
  const cached = await env.ISR_CACHE_KV.get(cacheKey);
  if (cached) {
    return new Response(cached, {
      status: 200,
      headers: {
        'Content-Type': 'text/html; charset=utf-8',
        'X-ISR-Cache': 'HIT',
      },
    });
  }

  // Cache miss: proxy to backend render endpoint
  const backendUrl = `${env.AZURE_BACKEND_URL.replace(/\/+$/, '')}/api/content/render${url.pathname}`;
  try {
    const backendResponse = await fetch(backendUrl, {
      method: 'GET',
      headers: { Accept: 'text/html' },
    });

    if (!backendResponse.ok) {
      // Backend error: fall through to existing behaviour
      return null;
    }

    const html = await backendResponse.text();

    // Cache in KV with 1-hour TTL (fire-and-forget via waitUntil)
    ctx.waitUntil(
      env.ISR_CACHE_KV.put(cacheKey, html, { expirationTtl: 3600 }),
    );

    return new Response(html, {
      status: 200,
      headers: {
        'Content-Type': 'text/html; charset=utf-8',
        'X-ISR-Cache': 'MISS',
      },
    });
  } catch {
    // Network/fetch error: fall through to existing behaviour
    return null;
  }
}
