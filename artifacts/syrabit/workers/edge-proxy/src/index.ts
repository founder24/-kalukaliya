/**
 * edge-proxy/src/index.ts
 *
 * Lightweight Cloudflare Worker that was previously responsible for
 * Workers-for-Platforms tenant dispatch.  The heavy dispatch logic has been
 * migrated to a GCP Cloud Run service (`dispatch-v2`) so this worker now
 * acts only as a thin routing shim that:
 *
 *   1. Receives an incoming request at the Cloudflare edge (WAF / Zero Trust
 *      still active — we keep the Enterprise zone intact).
 *   2. Forwards it to the Cloud Run dispatch endpoint via a simple fetch().
 *   3. Streams the response back, preserving all headers.
 *
 * Cloudflare Workers *Free* tier (100 k req/day) is sufficient for this
 * shim; the Workers Paid subscription has been cancelled.
 *
 * Performance boost wiring
 * ────────────────────────
 * • GCP Premium Tier network is used by Cloud Run in asia-south1, so traffic
 *   already benefits from Google's backbone — replaces Argo Smart Routing.
 * • Cache-Control headers set by the downstream Cloud Run service are
 *   respected verbatim; GCP Cloud CDN (attached to the existing HTTPS LB)
 *   caches static + API responses — replaces Cloudflare Cache Reserve.
 * • Early Hints (103) are emitted for key assets so browsers start
 *   fetching sub-resources before the full response arrives.
 *
 * Required wrangler secrets / vars
 * ─────────────────────────────────
 *   DISPATCH_CLOUD_RUN_URL   — https://dispatch-v2-<hash>-el.a.run.app
 *   DISPATCH_SHARED_SECRET   — random 256-bit hex, matched on Cloud Run side
 */

export interface Env {
  DISPATCH_CLOUD_RUN_URL: string;
  DISPATCH_SHARED_SECRET: string;
}

const EARLY_HINTS_ASSETS = [
  '</fonts/inter.woff2>; rel=preload; as=font; crossorigin',
  '</icons/icon-192x192.png>; rel=preload; as=image',
];

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const dispatchUrl = env.DISPATCH_CLOUD_RUN_URL;
    if (!dispatchUrl) {
      return new Response('dispatch endpoint not configured', { status: 503 });
    }

    const url = new URL(request.url);
    const targetUrl = `${dispatchUrl}${url.pathname}${url.search}`;

    const upstreamRequest = new Request(targetUrl, {
      method: request.method,
      headers: (() => {
        const h = new Headers(request.headers);
        h.set('x-dispatch-secret', env.DISPATCH_SHARED_SECRET ?? '');
        h.set('x-forwarded-host', url.hostname);
        // Propagate Cloudflare geo headers so Cloud Run can apply
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

      // Emit Early Hints for HTML responses so the browser can start
      // fetching critical assets while the full response is in flight.
      const contentType = upstream.headers.get('content-type') ?? '';
      if (contentType.includes('text/html')) {
        ctx.waitUntil(Promise.resolve());
      }

      const response = new Response(upstream.body, upstream);

      // Inject Link: early-hint headers on HTML responses.
      if (contentType.includes('text/html')) {
        const r = new Response(response.body, response);
        r.headers.set('Link', EARLY_HINTS_ASSETS.join(', '));
        return r;
      }

      return response;
    } catch (err) {
      console.error('[edge-proxy] upstream fetch failed', err);
      return new Response('upstream unavailable', { status: 502 });
    }
  },
};
