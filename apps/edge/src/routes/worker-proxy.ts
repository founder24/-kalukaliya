/**
 * Worker-to-Worker proxy — routes requests to the API Worker via Service Binding.
 *
 * When env.API_WORKER is present (production with D1 backend), requests are
 * forwarded directly over Cloudflare's internal network without a public HTTP
 * hop. All API routes are Worker-native, including staff publishing and seed
 * operations, so the proxy never requests a Cloud Run OIDC identity token.
 *
 * Edge middleware (JWT verification, rate-limiting, HMAC signing) all run
 * before this function is called — headers are already set on the request.
 */

export async function proxyToApiWorker(
  request: Request,
  env: Env,
): Promise<Response> {
  const url = new URL(request.url);
  const isStreamRequest = url.pathname.includes('/stream');
  const isCrawlerArtifact = url.pathname.startsWith('/api/v1/seo/');

  // Clone and clean headers for the internal hop.
  // - Drop Cloud Run-specific auth headers (OIDC not needed for Worker-to-Worker)
  // - Keep X-User-ID, X-Edge-Secret, X-Edge-Timestamp, X-Edge-Signature
  //   that the API Worker uses to verify edge trust
  const headers = new Headers(request.headers);
  headers.delete('Host');
  headers.delete('Connection');

  // Inject per-request HMAC signature so the API Worker can verify this
  // request came from the trusted edge worker (same as the Cloud Run path).
  if (env.EDGE_SHARED_SECRET) {
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const userId = headers.get('X-User-ID') || 'anonymous';
    const message = `${timestamp}:${userId}:${url.pathname}`;

    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw',
      encoder.encode(env.EDGE_SHARED_SECRET),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign'],
    );
    const signatureBuffer = await crypto.subtle.sign('HMAC', key, encoder.encode(message));
    const signatureHex = Array.from(new Uint8Array(signatureBuffer))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');

    headers.set('X-Edge-Timestamp', timestamp);
    headers.set('X-Edge-Signature', signatureHex);
  }

  // Keep the original Authorization header so the API Worker's auth endpoints
  // can read it directly (they expect `Authorization: Bearer <user-jwt>`).
  // Also copy it to X-User-JWT as a secondary claim for HMAC-verified routes.
  const originalAuth = headers.get('Authorization');
  if (originalAuth?.startsWith('Bearer ')) {
    headers.set('X-User-JWT', originalAuth);
    // Authorization is intentionally kept — do NOT delete it.
  }

  // Add Cloudflare metadata (same as the HTTP proxy path)
  headers.set('X-Real-IP', request.headers.get('CF-Connecting-IP') || 'unknown');
  headers.set('CF-Ray-ID', request.headers.get('CF-Ray') || '');
  headers.set('X-Forwarded-Proto', 'https');
  headers.delete('Origin');

  // Forward the full original URL unchanged. The API Worker mounts routes at
  // the same /api/v1/… paths the edge uses, so no path rewriting is needed.
  //
  // Read the body as ArrayBuffer rather than forwarding request.body directly:
  // Node 18+ rejects `new Request(url, { body: ReadableStream })` unless
  // `duplex: 'half'` is set, and the Service Binding fetch does not accept
  // that option. ArrayBuffer is always accepted by both environments.
  const bodyBuffer =
    request.method !== 'GET' && request.method !== 'HEAD'
      ? await request.arrayBuffer()
      : undefined;

  const outRequest = new Request(request.url, {
    method: request.method,
    headers,
    body: bodyBuffer,
  });

  try {
    // env.API_WORKER is guaranteed non-null by the caller
    const response = await env.API_WORKER!.fetch(outRequest);

    const responseHeaders = new Headers(response.headers);

    if (isStreamRequest) {
      responseHeaders.set('Content-Type', 'text/event-stream');
      responseHeaders.set('Cache-Control', 'no-store');
      responseHeaders.set('X-Content-Type-Options', 'nosniff');
      responseHeaders.set('X-Accel-Buffering', 'no');
      responseHeaders.set('X-Robots-Tag', 'noindex');
      responseHeaders.delete('Content-Length');

      return new Response(response.body, {
        status: response.status,
        headers: responseHeaders,
      });
    }

    // Root crawler artifacts are rewritten to /api/v1/seo by the edge. They
    // must remain indexable, unlike normal JSON API responses.
    if (!isCrawlerArtifact) {
      responseHeaders.set('X-Robots-Tag', 'noindex, nofollow');
      responseHeaders.set('X-Content-Purpose', 'api');
    } else {
      responseHeaders.delete('X-Robots-Tag');
      responseHeaders.delete('X-Content-Purpose');
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (err) {
    console.error('[worker-proxy] Service binding fetch failed:', err);
    return new Response(
      JSON.stringify({
        error: 'Backend service unavailable',
        details: err instanceof Error ? err.message : 'Unknown error',
      }),
      {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      },
    );
  }
}

/**
 * Ping the API Worker health endpoint for the edge health check.
 * Returns true if the API Worker responds 2xx within 2s.
 */
export async function pingApiWorkerHealth(env: Env): Promise<boolean> {
  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 2000);
    const req = new Request('https://syrabit-api-prod.workers.dev/health', {
      signal: controller.signal,
    });
    const resp = await env.API_WORKER!.fetch(req);
    clearTimeout(timeoutId);
    return resp.ok;
  } catch {
    return false;
  }
}
