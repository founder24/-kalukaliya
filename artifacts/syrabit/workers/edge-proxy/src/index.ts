/**
 * edge-proxy/src/index.ts
 *
 * Lightweight Cloudflare Worker that fronts the API. Originally a
 * Workers-for-Platforms tenant dispatcher, simplified into a routing
 * shim when dispatch moved to GCP Cloud Run, and extended in
 * Task #331 with an `ORIGIN_TARGET` feature flag so traffic can be
 * flipped between the Railway, Cloud Run, and Digital Ocean origins
 * without a code deploy at cutover.
 *
 * Origin selection
 * ────────────────
 *   ORIGIN_TARGET=do        → DO_APP_BACKEND_URL  (Digital Ocean App Platform)
 *   ORIGIN_TARGET=cloudrun  → DISPATCH_CLOUD_RUN_URL (legacy GCP Cloud Run)
 *   ORIGIN_TARGET=railway   → BACKEND_RAILWAY_URL (legacy Railway)
 *
 * Default is `cloudrun` for backwards-compatibility with the prior
 * deploy. The cutover task flips the production secret to `do`.
 *
 * Performance boost wiring
 * ────────────────────────
 * • Cache-Control headers set by the downstream service are respected
 *   verbatim; cache layer (Cloudflare or DO LB) caches static + API
 *   responses.
 * • Early Hints (103) are emitted for key assets so browsers start
 *   fetching sub-resources before the full response arrives.
 *
 * Required wrangler secrets / vars
 * ─────────────────────────────────
 *   ORIGIN_TARGET            — "do" | "cloudrun" | "railway" (default "cloudrun")
 *   DO_APP_BACKEND_URL       — https://syrabit-backend-<hash>.ondigitalocean.app
 *   DISPATCH_CLOUD_RUN_URL   — https://dispatch-v2-<hash>-el.a.run.app
 *   BACKEND_RAILWAY_URL      — https://syrabit-backend-production.up.railway.app
 *   DISPATCH_SHARED_SECRET   — random 256-bit hex, matched server-side
 */

export interface Env {
  ORIGIN_TARGET?: string;
  DO_APP_BACKEND_URL?: string;
  DISPATCH_CLOUD_RUN_URL?: string;
  BACKEND_RAILWAY_URL?: string;
  DISPATCH_SHARED_SECRET: string;
}

const EARLY_HINTS_ASSETS = [
  '</fonts/inter.woff2>; rel=preload; as=font; crossorigin',
  '</icons/icon-192x192.png>; rel=preload; as=image',
];

function resolveOrigin(env: Env): { url: string; target: string } | null {
  const target = (env.ORIGIN_TARGET ?? 'cloudrun').toLowerCase();
  switch (target) {
    case 'do':
      return env.DO_APP_BACKEND_URL ? { url: env.DO_APP_BACKEND_URL, target } : null;
    case 'railway':
      return env.BACKEND_RAILWAY_URL ? { url: env.BACKEND_RAILWAY_URL, target } : null;
    case 'cloudrun':
    default:
      return env.DISPATCH_CLOUD_RUN_URL ? { url: env.DISPATCH_CLOUD_RUN_URL, target: 'cloudrun' } : null;
  }
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const origin = resolveOrigin(env);
    if (!origin) {
      return new Response('origin not configured for ORIGIN_TARGET', { status: 503 });
    }

    const url = new URL(request.url);
    const targetUrl = `${origin.url}${url.pathname}${url.search}`;

    const upstreamRequest = new Request(targetUrl, {
      method: request.method,
      headers: (() => {
        const h = new Headers(request.headers);
        h.set('x-dispatch-secret', env.DISPATCH_SHARED_SECRET ?? '');
        h.set('x-forwarded-host', url.hostname);
        h.set('x-origin-target', origin.target);
        // Propagate Cloudflare geo headers so the origin can apply
        // regional logic without re-doing IP geolocation.
        h.set('x-cf-ipcountry', request.headers.get('cf-ipcountry') ?? '');
        h.set('x-real-ip', request.headers.get('cf-connecting-ip') ?? '');
        return h;
      })(),
      body: ['GET', 'HEAD'].includes(request.method) ? null : request.body,
      redirect: 'follow',
    });

    try {
      const upstream = await fetch(upstreamRequest);

      const contentType = upstream.headers.get('content-type') ?? '';
      if (contentType.includes('text/html')) {
        ctx.waitUntil(Promise.resolve());
      }

      const response = new Response(upstream.body, upstream);

      if (contentType.includes('text/html')) {
        const r = new Response(response.body, response);
        r.headers.set('Link', EARLY_HINTS_ASSETS.join(', '));
        r.headers.set('x-syrabit-origin', origin.target);
        return r;
      }

      response.headers.set('x-syrabit-origin', origin.target);
      return response;
    } catch (err) {
      console.error('[edge-proxy] upstream fetch failed', { target: origin.target, err });
      return new Response('upstream unavailable', { status: 502 });
    }
  },
};
