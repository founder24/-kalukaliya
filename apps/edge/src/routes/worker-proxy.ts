/**
 * Worker-to-Worker proxy — routes requests to the API Worker via Service Binding.
 *
 * When env.API_WORKER is present (production with D1 backend), requests are
 * forwarded directly over Cloudflare's internal network without a public HTTP
 * hop. Native routes stay Worker-native. The retained admin and seed
 * compatibility families also travel through this hop, so those requests carry
 * a Cloud Run OIDC token for the API Worker's explicit fallback bridge.
 *
 * Edge middleware (JWT verification, rate-limiting, HMAC signing) all run
 * before this function is called — headers are already set on the request.
 */

import { getIdentityToken } from '../utils/google-auth';

const DEFAULT_SERVICE_BINDING_TIMEOUT_MS = 10_000;

/** Only the native chat endpoint is an SSE response. */
function isSseRequest(pathname: string): boolean {
  return pathname === '/api/v1/chat/stream';
}

/**
 * These families contain both native routes and deliberate compatibility
 * fallbacks. Supplying the token to all requests in the families is safe:
 * native handlers ignore the internal header, while any unmatched route can
 * authenticate its Cloud Run hop without another edge round trip.
 */
function isCloudRunFallbackFamily(pathname: string): boolean {
  return pathname.startsWith('/api/v1/admin/') || pathname.startsWith('/api/v1/seed/');
}

export async function proxyToApiWorker(
  request: Request,
  env: Env,
): Promise<Response> {
  const url = new URL(request.url);
  const isStreamRequest = isSseRequest(url.pathname);
  const isCrawlerArtifact = url.pathname.startsWith('/api/v1/seo/');
  const needsCloudRunFallbackToken = isCloudRunFallbackFamily(url.pathname);

  // Clone and clean headers for the internal hop.
  // - Drop caller-supplied Cloud Run-specific auth headers; only the edge may
  //   mint and attach the internal fallback token below.
  // - Keep X-User-ID, X-Edge-Secret, X-Edge-Timestamp, X-Edge-Signature
  //   that the API Worker uses to verify edge trust
  const headers = new Headers(request.headers);
  headers.delete('Host');
  headers.delete('Connection');
  headers.delete('X-Cloud-Run-Token');

  if (needsCloudRunFallbackToken) {
    // The API Worker's fallback bridge cannot mint a Google identity token
    // itself. Fetch it at the edge, where GOOGLE_SA_KEY is configured, and
    // pass it only over the private service-binding request.
    const cloudRunToken = await getIdentityToken(env);
    if (cloudRunToken) {
      headers.set('X-Cloud-Run-Token', `Bearer ${cloudRunToken}`);
    }
  }

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

  const serviceBindingAbort = new AbortController();
  const outRequest = new Request(request.url, {
    method: request.method,
    headers,
    body: bodyBuffer,
    signal: serviceBindingAbort.signal,
  });

  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  try {
    // env.API_WORKER is guaranteed non-null by the caller
    const configuredTimeout = Number.parseInt(
      env.SERVICE_BINDING_TIMEOUT_MS || String(DEFAULT_SERVICE_BINDING_TIMEOUT_MS),
      10,
    );
    const timeoutMs = Number.isFinite(configuredTimeout) && configuredTimeout > 0
      ? configuredTimeout
      : DEFAULT_SERVICE_BINDING_TIMEOUT_MS;
    const response = await Promise.race([
      env.API_WORKER!.fetch(outRequest),
      new Promise<never>((_, reject) => {
        timeoutId = setTimeout(() => {
          serviceBindingAbort.abort();
          const error = new Error(`Service binding timed out after ${timeoutMs}ms`);
          error.name = 'TimeoutError';
          reject(error);
        }, timeoutMs);
      }),
    ]);
    if (timeoutId) clearTimeout(timeoutId);

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
    if (timeoutId) clearTimeout(timeoutId);
    console.error('[worker-proxy] Service binding fetch failed:', err);
    const timedOut = err instanceof Error && err.name === 'TimeoutError';
    return new Response(
      JSON.stringify({
        error: timedOut ? 'Backend service timed out' : 'Backend service unavailable',
        details: err instanceof Error ? err.message : 'Unknown error',
      }),
      {
        status: timedOut ? 504 : 503,
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
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  const controller = new AbortController();
  try {
    const req = new Request('https://syrabit-api-prod.workers.dev/health', {
      headers: { 'X-Health-Probe': 'edge' },
      signal: controller.signal,
    });
    const resp = await Promise.race([
      env.API_WORKER!.fetch(req),
      new Promise<never>((_, reject) => {
        timeoutId = setTimeout(
          () => {
            controller.abort();
            reject(new Error('API Worker health probe timed out'));
          },
          2000,
        );
      }),
    ]);
    if (timeoutId) clearTimeout(timeoutId);
    return resp.ok;
  } catch {
    if (timeoutId) clearTimeout(timeoutId);
    return false;
  }
}
