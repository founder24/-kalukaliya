/**
 * Cloud Run fallback proxy for routes not yet ported to Workers.
 *
 * When BACKEND_URL is set the request is forwarded verbatim to Cloud Run
 * and the response is returned to the caller. This lets us enable
 * API_WORKER_LIVE on the edge Worker today: auth+health are served from D1,
 * everything else continues to work via Cloud Run until each route is ported.
 *
 * The forwarded request strips the hop-by-hop headers that must not be
 * re-sent (connection, transfer-encoding, keep-alive) and passes all others
 * through so that cookies, content-type, accept, etc. are preserved.
 */

import { Context } from 'hono';
import type { Env } from '../types';

const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailers',
  'transfer-encoding',
  'upgrade',
]);

export async function proxyToCloudRun(c: Context<{ Bindings: Env }>): Promise<Response> {
  const backendUrl = c.env.BACKEND_URL;

  if (!backendUrl) {
    return c.json(
      { detail: 'Route not yet migrated to Workers and no BACKEND_URL fallback configured.' },
      501,
    );
  }

  const url = new URL(c.req.url);
  const target = new URL(url.pathname + url.search, backendUrl);

  // Build forwarded headers — strip hop-by-hop and internal edge headers
  const INTERNAL_HEADERS = new Set(['x-cloud-run-token', 'x-edge-signature', 'x-edge-timestamp']);
  const forwardedHeaders = new Headers();
  for (const [key, value] of c.req.raw.headers) {
    if (!HOP_BY_HOP.has(key.toLowerCase()) && !INTERNAL_HEADERS.has(key.toLowerCase())) {
      forwardedHeaders.set(key, value);
    }
  }
  // Overwrite Host so Cloud Run accepts the request
  forwardedHeaders.set('host', target.host);

  // Use the GCP OIDC token pre-fetched by the edge Worker. Without it Cloud Run
  // returns 403 because anonymous requests are not allowed.
  const cloudRunToken = c.req.header('x-cloud-run-token');
  if (cloudRunToken) {
    forwardedHeaders.set('authorization', cloudRunToken); // already "Bearer <token>"
  }

  let body: BodyInit | null = null;
  const method = c.req.method;
  if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
    body = c.req.raw.body;
  }

  const upstream = await fetch(target.toString(), {
    method,
    headers: forwardedHeaders,
    body,
    // @ts-expect-error — CF Workers fetch supports duplex streaming
    duplex: 'half',
  });

  // Return the upstream response as-is; the edge Worker applies CORS + security
  // headers on top, so we don't add them here to avoid double-application.
  const headers = new Headers(upstream.headers);
  // Make the remaining rollback routes observable to staged cutover checks and
  // request logs. This is intentionally explicit rather than a silent retry.
  headers.set('X-Syrabit-Route', 'cloud-run-fallback');
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}
