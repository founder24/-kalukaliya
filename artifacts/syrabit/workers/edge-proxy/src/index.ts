/**
 * edge-proxy/src/index.ts
 *
 * Lightweight Cloudflare Worker that fronts the API. Originally a
 * Workers-for-Platforms tenant dispatcher, simplified into a routing
 * shim when dispatch moved to GCP Cloud Run, and extended in
 * Task #331 with an `ORIGIN_TARGET` feature flag so traffic could be
 * flipped between the Railway, Cloud Run, and Digital Ocean origins
 * without a code deploy at cutover.
 *
 * Task #335 decommissioned the legacy Railway and GCP Cloud Run
 * origins. The `ORIGIN_TARGET` variable is retained so a future
 * provider can be wired in without changing the worker source, but
 * only `do` resolves to a configured origin today.
 *
 * Origin selection
 * ────────────────
 *   ORIGIN_TARGET=do        → DO_APP_BACKEND_URL  (Digital Ocean App Platform)
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
 *   ORIGIN_TARGET            — "do" (default)
 *   DO_APP_BACKEND_URL       — https://syrabit-backend-<hash>.ondigitalocean.app
 *   DISPATCH_SHARED_SECRET   — random 256-bit hex, matched server-side
 */

export interface Env {
  ORIGIN_TARGET?: string;
  DO_APP_BACKEND_URL?: string;
  DISPATCH_SHARED_SECRET: string;
  // Durable Object bindings (see wrangler.toml).
  CHAT_SESSION?: DurableObjectNamespace;
  RATE_LIMITER?: DurableObjectNamespace;
  DO_CHAT_ON?: string;
  EDGE_CHAT_RATE_LIMIT?: string;
  EDGE_CHAT_USER_RATE_LIMIT?: string;
  // HS256 secret mirrored from the backend (`JWT_SECRET`) so the edge
  // can cryptographically verify the Bearer token before deriving a
  // per-user limiter scope. When unset the limiter degrades to per-IP
  // — never to client-supplied identity.
  EDGE_JWT_HS256_SECRET?: string;
  // Task #405 — KV namespace backing /api/edge/kv-cache/<key>. Bound
  // in wrangler.toml under both env.production and env.staging.
  CF_EDGE_CACHE?: KVNamespace;
  // Shared secret matched against the X-Edge-Admin-Secret header on
  // /api/edge/kv-cache/* (and the existing /api/edge/kv-usage probe).
  // Mirrors the backend env var of the same name.
  D1_SYNC_SECRET?: string;
}

// Re-export DO classes so wrangler can find them at the worker entrypoint.
export { ChatSession } from './chat_session';
export { RateLimiter } from './rate_limiter';
export { deriveLimiterScope, verifyHS256Jwt } from './edge_identity';
import { deriveLimiterScope } from './edge_identity';

/**
 * Task #386 — dispatch `/do/...` paths to the corresponding Durable
 * Object namespace. Returns null when the path is not a DO route so
 * the caller can fall through to the regular origin proxy.
 *
 * Auth: same shared bearer as the regular dispatch — the backend
 * `do_chat.py` shim sets `Authorization: Bearer <DISPATCH_SHARED_SECRET>`
 * on every call.
 */
async function dispatchDurableObject(
  request: Request,
  env: Env,
  url: URL,
): Promise<Response | null> {
  if (!url.pathname.startsWith('/do/')) {
    return null;
  }

  // Bearer auth is mandatory for DO dispatch — these endpoints carry
  // chat-session payloads and decrement rate-limit counters, so an
  // unauthenticated caller must never reach them.
  const auth = request.headers.get('authorization') || '';
  const expected = `Bearer ${env.DISPATCH_SHARED_SECRET ?? ''}`;
  if (!env.DISPATCH_SHARED_SECRET || auth !== expected) {
    return new Response('unauthorised', { status: 401 });
  }

  // /do/chat-session/<id>[/<sub>]  — sub-paths (e.g. /typing) are
  // forwarded to the DO so it can dispatch on the path internally.
  const chatMatch = url.pathname.match(/^\/do\/chat-session\/([^/]+)(?:\/[^/]+)?$/);
  if (chatMatch) {
    if (!env.CHAT_SESSION) {
      return new Response('CHAT_SESSION binding missing', { status: 503 });
    }
    const id = env.CHAT_SESSION.idFromName(chatMatch[1]);
    const stub = env.CHAT_SESSION.get(id);
    return stub.fetch(request);
  }

  // /do/rate-limiter/check  — singleton DO id "global" so the same
  // bucket is consulted from every region.
  if (url.pathname === '/do/rate-limiter/check') {
    if (!env.RATE_LIMITER) {
      return new Response('RATE_LIMITER binding missing', { status: 503 });
    }
    const id = env.RATE_LIMITER.idFromName('global');
    const stub = env.RATE_LIMITER.get(id);
    return stub.fetch(request);
  }

  return new Response('unknown DO route', { status: 404 });
}

/**
 * Task #405 — KV write-through cache routes.
 *
 *   GET    /api/edge/kv-cache/<key>     → 200 { value, ttl_s } | 404
 *   PUT    /api/edge/kv-cache/<key>     body { value, ttl_s }  → 200 { ok, ttl_s }
 *   DELETE /api/edge/kv-cache/<key>                            → 200 { ok }
 *
 * Auth: every request must carry X-Edge-Admin-Secret matching the
 * worker's `D1_SYNC_SECRET` (the same handshake the existing
 * /api/edge/kv-usage probe uses, so no new credential needs
 * provisioning). The backend's `kv_cache.KvCache` calls these from the
 * write-through path when `CF_EDGE_CACHE_ON=1`. Returns null if the
 * path is not a kv-cache route so the caller can fall through.
 */
async function dispatchKvCache(
  request: Request,
  env: Env,
  url: URL,
): Promise<Response | null> {
  const prefix = '/api/edge/kv-cache/';
  if (!url.pathname.startsWith(prefix)) {
    return null;
  }
  const expected = env.D1_SYNC_SECRET ?? '';
  const provided = request.headers.get('x-edge-admin-secret') ?? '';
  if (!expected || !constantTimeEqual(provided, expected)) {
    return new Response('unauthorised', { status: 401 });
  }
  if (!env.CF_EDGE_CACHE) {
    return new Response('CF_EDGE_CACHE binding missing', { status: 503 });
  }
  let key: string;
  try {
    key = decodeURIComponent(url.pathname.slice(prefix.length));
  } catch {
    return new Response('invalid key', { status: 400 });
  }
  // Cloudflare KV rejects keys >512 bytes. Reject early with a clear
  // status rather than letting the KV API surface a generic 400.
  if (!key || key.length > 512) {
    return new Response('invalid key', { status: 400 });
  }

  if (request.method === 'GET') {
    const stored = await env.CF_EDGE_CACHE.getWithMetadata<unknown, { ttl_s?: number }>(
      key,
      { type: 'json' },
    );
    if (stored.value === null) {
      return new Response('not found', { status: 404 });
    }
    const ttl = stored.metadata?.ttl_s ?? 300;
    return jsonResponse({ value: stored.value, ttl_s: ttl });
  }

  if (request.method === 'PUT') {
    const body = await request.json().catch(() => null) as
      | { value?: unknown; ttl_s?: number }
      | null;
    if (!body || !('value' in body)) {
      return new Response('missing value', { status: 400 });
    }
    // Cloudflare KV requires expirationTtl >= 60s; clamp to a sensible
    // upper bound (24h) so a runaway caller cannot pin a key forever.
    const ttl = Math.max(60, Math.min(86_400, Number(body.ttl_s ?? 300)));
    try {
      await env.CF_EDGE_CACHE.put(key, JSON.stringify(body.value), {
        expirationTtl: ttl,
        metadata: { ttl_s: ttl },
      });
    } catch (err) {
      console.warn('[edge-proxy] kv-cache put failed', { key, err });
      return new Response('kv put failed', { status: 502 });
    }
    return jsonResponse({ ok: true, ttl_s: ttl });
  }

  if (request.method === 'DELETE') {
    try {
      await env.CF_EDGE_CACHE.delete(key);
    } catch (err) {
      console.warn('[edge-proxy] kv-cache delete failed', { key, err });
      return new Response('kv delete failed', { status: 502 });
    }
    return jsonResponse({ ok: true });
  }

  return new Response('method not allowed', { status: 405 });
}

function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const EARLY_HINTS_ASSETS = [
  '</fonts/inter.woff2>; rel=preload; as=font; crossorigin',
  '</icons/icon-192x192.png>; rel=preload; as=image',
];

function resolveOrigin(env: Env): { url: string; target: string } | null {
  const target = (env.ORIGIN_TARGET ?? 'do').toLowerCase();
  if (target !== 'do') {
    console.warn('[edge-proxy] unsupported ORIGIN_TARGET — Railway and Cloud Run were decommissioned in Task #335', { target });
  }
  return env.DO_APP_BACKEND_URL ? { url: env.DO_APP_BACKEND_URL, target: 'do' } : null;
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // Task #386 — DO routes are intercepted BEFORE the origin proxy
    // so chat-session reads / rate-limit checks never hit DigitalOcean.
    const doResponse = await dispatchDurableObject(request, env, url);
    if (doResponse) {
      return doResponse;
    }

    // Task #405 — KV write-through cache routes. Handled at the edge
    // so the in-process LRU on a sibling pod can hydrate without an
    // origin round-trip.
    const kvResponse = await dispatchKvCache(request, env, url);
    if (kvResponse) {
      return kvResponse;
    }

    // Edge rate-limit for chat ingress, gated by DO_CHAT_ON. Scope is
    // user-keyed when the Bearer token verifies against
    // EDGE_JWT_HS256_SECRET (the same HS256 secret the backend uses
    // for JWT_SECRET); otherwise it falls back to per-IP. Fails open
    // on DO errors. Covers every chat ingress shape the backend
    // exposes: /chat, /api/chat, /ai_chat, /api/ai_chat, /ai/chat,
    // /api/ai/chat.
    const chatPathRe = /^\/(?:api\/)?(?:ai\/|ai_)?chat(?:\b|\/)/i;
    if (
      String(env.DO_CHAT_ON ?? '').toLowerCase() === 'true' &&
      env.RATE_LIMITER &&
      chatPathRe.test(url.pathname)
    ) {
      try {
        const { scope, kind } = await deriveLimiterScope(request, env);
        const limit = Math.max(
          1,
          Number(
            kind === 'do-chat-user'
              ? (env.EDGE_CHAT_USER_RATE_LIMIT ?? env.EDGE_CHAT_RATE_LIMIT ?? '60')
              : (env.EDGE_CHAT_RATE_LIMIT ?? '30'),
          ),
        );
        const id = env.RATE_LIMITER.idFromName('global');
        const stub = env.RATE_LIMITER.get(id);
        const probe = await stub.fetch('https://internal/do/rate-limiter/check', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: `edge-chat:${scope}`, limit, window_s: 60 }),
        });
        if (probe.ok) {
          const verdict = await probe.json() as { allowed?: boolean; remaining?: number };
          if (verdict && verdict.allowed === false) {
            return new Response(
              JSON.stringify({ error: 'rate_limited', scope: 'edge', remaining: 0 }),
              {
                status: 429,
                headers: {
                  'Content-Type': 'application/json',
                  'Retry-After': '60',
                  'X-Edge-Rate-Scope': kind,
                },
              },
            );
          }
        }
      } catch (err) {
        console.warn('[edge-proxy] edge rate-limit probe failed', { err });
      }
    }

    const origin = resolveOrigin(env);
    if (!origin) {
      return new Response('origin not configured for ORIGIN_TARGET', { status: 503 });
    }

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
