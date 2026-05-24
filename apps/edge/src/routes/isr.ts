/**
 * ISR (Incremental Static Regeneration) fallback for bot traffic.
 *
 * When a known crawler requests a page that isn't prerendered, this handler
 * proxies the request to the backend, caches the HTML in KV with a 1-hour TTL,
 * and serves subsequent bot hits from cache. Non-bot requests return null so
 * other handlers (SPA shell, R2 assets) take over.
 */

const BOT_UA_RE =
  /googlebot|bingbot|gptbot|claudebot|perplexitybot|applebot|yandex|baidu/i;

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

    const url = new URL(request.url);
    const cacheKey = url.pathname;

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

    // Cache miss: proxy to backend
    const backendUrl = `${env.AZURE_BACKEND_URL.replace(/\/$/, '')}${url.pathname}${url.search}`;
    const response = await fetch(backendUrl, {
      method: request.method,
      headers: request.headers,
    });

    if (response.status === 200) {
      const contentType = response.headers.get('Content-Type') || '';
      if (contentType.includes('text/html')) {
        const html = await response.text();

        // Cache asynchronously so we don't block the response
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
      }
    }

    // Non-200 or non-HTML: fall through to other handlers
    return null;
  } catch {
    // On any error, fall through gracefully
    return null;
  }
}
