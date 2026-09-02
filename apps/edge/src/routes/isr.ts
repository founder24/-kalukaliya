/**
 * ISR (Incremental Static Regeneration) — Edge Worker is the SOLE owner.
 *
 * ── Bot prerender ownership boundary ────────────────────────────────────────
 * EDGE WORKER (this file) — PRIMARY and authoritative for bot HTML.
 *   - Detects bot UA on every request that reaches api.syrabit.ai.
 *   - Serves from ISR_CACHE_KV (1-hour TTL) on cache hit.
 *   - On miss: falls through to the frontend route. Rendering is never fetched
 *     from an external backend.
 *   - Handles: all /api/*, all dynamic page routes proxied through the edge.
 *
 * CLOUDFLARE PAGES WORKER (public/_worker.js) — SECONDARY / SPA-only.
 *   - Handles routes that are served directly from Cloudflare Pages CDN
 *     (static assets, prebuilt HTML pages) WITHOUT going through this edge worker.
 *   - Only sees traffic that does NOT pass through api.syrabit.ai.
 *   - Its bot rendering is a fallback for direct Pages hits only (e.g., direct
 *     browser navigation to syrabit.ai/library/... before the edge catches it).
 *   - It proxies to the backend /html/<path> endpoint for bot renders.
 *
 * Rule: If a route is proxied through the Edge Worker, the Edge Worker's
 * ISR cache is the single truth. The Pages worker must NOT also cache the
 * same route in a competing layer. Routes served only from Pages CDN are
 * owned by the Pages worker.
 *
 * Non-bot requests: return null → other handlers (SPA shell, R2 assets) take over.
 * ─────────────────────────────────────────────────────────────────────────────
 */

const BOT_UA_RE =
  /googlebot|bingbot|gptbot|claudebot|perplexitybot|applebot|yandex|baidu|facebookexternalhit|twitterbot|linkedinbot|whatsapp|slackbot|discordbot|telegrambot|pinterestbot/i;

export async function handleISR(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
): Promise<Response | null> {
  try {
    const ua = request.headers.get('User-Agent') || '';
    if (!BOT_UA_RE.test(ua)) {
      return null;
    }

    // Guard: KV binding may not be available in dev/preview environments
    if (!env.ISR_CACHE_KV) {
      return null;
    }

    const url = new URL(request.url);
    const cacheKey = url.pathname + url.search;

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

    // Cache miss: rendering belongs to the frontend route.
    return null;
  } catch {
    // On any error, fall through gracefully
    return null;
  }
}
