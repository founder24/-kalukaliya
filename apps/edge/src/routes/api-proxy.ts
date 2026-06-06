/**
 * API Proxy — Forwards requests to Backend
 *
 * Features:
 * - Stream-aware: detects /stream paths and passes response.body directly (chunked)
 * - Injects Cloudflare metadata headers (X-Real-IP, CF-Ray-ID)
 * - Sets CORS origin from validated allow-list
 * - Removes hop-by-hop headers
 * - Returns 503 on backend failure
 */

import { getCorsHeaders } from '../middleware/cors';
import { getIdentityToken } from '../utils/google-auth';

export async function proxyRequest(
  request: Request,
  backendUrl: string,
  env: Env
): Promise<Response> {
  const url = new URL(request.url);
  const targetUrl = `${backendUrl}${url.pathname}${url.search}`;
  const isStreamRequest = url.pathname.includes('/stream');

  // Origin used for error-response CORS (main response builds its own)

  // Clone headers and inject Cloudflare-specific metadata
  const headers = new Headers(request.headers);
  headers.set('X-Real-IP', request.headers.get('CF-Connecting-IP') || 'unknown');
  headers.set('CF-Ray-ID', request.headers.get('CF-Ray') || '');
  headers.set('X-Forwarded-Proto', 'https');

  // Remove hop-by-hop headers that shouldn't be forwarded
  headers.delete('Host');
  headers.delete('Connection');

  // Per-request HMAC signature (SEC-002 fix)
  if (env.EDGE_SHARED_SECRET) {
    // Timestamp for HMAC. Backend should validate within +/- 30s tolerance
    // to account for clock skew between edge and origin.
    const timestamp = Math.floor(Date.now() / 1000).toString();
    const userId = headers.get('X-User-ID') || 'anonymous';
    const message = `${timestamp}:${userId}:${url.pathname}`;

    const encoder = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw',
      encoder.encode(env.EDGE_SHARED_SECRET),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign']
    );
    const signatureBuffer = await crypto.subtle.sign('HMAC', key, encoder.encode(message));
    const signatureHex = Array.from(new Uint8Array(signatureBuffer))
      .map(b => b.toString(16).padStart(2, '0'))
      .join('');

    headers.set('X-Edge-Timestamp', timestamp);
    headers.set('X-Edge-Signature', signatureHex);
  }

  // Preserve user's JWT before OIDC overwrite so backend can still verify it
  // Cloud Run IAM requires OIDC in Authorization, but the app needs the user JWT.
  // Backend reads X-User-JWT as fallback when edge-trust HMAC doesn't match.
  const originalAuth = headers.get('Authorization');
  if (originalAuth && originalAuth.startsWith('Bearer ')) {
    headers.set('X-User-JWT', originalAuth);
  }

  // Inject Google identity token for Cloud Run authentication
  const idToken = await getIdentityToken(env);
  if (idToken) {
    headers.set('Authorization', 'Bearer ' + idToken);
  }

  const controller = new AbortController();
  const timeoutMs = parseInt(env.PROXY_TIMEOUT_MS || '30000', 10) || 30000;
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    // Buffer request body to allow retransmission on redirects
    const body = request.method !== 'GET' && request.method !== 'HEAD'
      ? await request.arrayBuffer()
      : undefined;

    let response = await fetch(targetUrl, {
      method: request.method,
      headers: headers,
      body: body,
      redirect: 'manual',
      signal: controller.signal,
    });

    // Follow same-origin redirects (e.g., FastAPI trailing-slash redirects)
    if ([301, 302, 307, 308].includes(response.status)) {
      const location = response.headers.get('Location');
      if (location) {
        const redirectUrl = new URL(location, targetUrl);
        // Only follow if redirecting to the same backend host
        const backendHost = new URL(backendUrl).host;
        if (redirectUrl.host === backendHost) {
          // Ensure HTTPS for the redirect target
          redirectUrl.protocol = 'https:';
          response = await fetch(redirectUrl.toString(), {
            method: request.method,
            headers: headers,
            body: body,
            redirect: 'manual',
            signal: controller.signal,
          });
        }
      }
    }

    clearTimeout(timeout);

    // Normalize non-JSON error responses from Cloud Run infrastructure (e.g.
    // Google Frontend 404/401/403 HTML when the service is unreachable, not yet
    // deployed, or when Cloud Run IAM rejects an unauthenticated request).
    // Passing raw HTML through to API clients is confusing — convert to JSON.
    // NOTE: JSON error responses from FastAPI (application/json) are NOT touched
    // here — they pass through with their original status code (e.g. 401, 422).
    if (!response.ok && response.status >= 400) {
      const ct = response.headers.get('Content-Type') || '';
      if (!ct.includes('application/json') && !ct.includes('text/event-stream')) {
        const errorOrigin = request.headers.get('Origin') || 'https://syrabit.ai';
        const corsH = getCorsHeaders(errorOrigin);
        // GCP infra errors (404 HTML, 401 IAM denied, 403 IAM denied, 5xx) are
        // all surfaced as 503 "Backend service unavailable" to the caller.
        // Other non-JSON errors (e.g. 400 bad request from GCP) keep their status.
        const isInfraError =
          response.status >= 500 ||
          response.status === 404 ||
          response.status === 401 ||
          response.status === 403;
        const statusCode = isInfraError ? 503 : response.status;
        return new Response(
          JSON.stringify({
            error: statusCode === 503 ? 'Backend service unavailable' : 'Request failed',
            status: response.status,
          }),
          {
            status: statusCode,
            headers: {
              'Content-Type': 'application/json',
              'Access-Control-Allow-Origin': corsH['Access-Control-Allow-Origin'],
              'Access-Control-Allow-Credentials': 'true',
            },
          }
        );
      }
    }

    const responseHeaders = new Headers(response.headers);

    if (isStreamRequest) {
      // ── Stream-specific handling ──
      // Pass response body directly without buffering (chunked transfer)
      responseHeaders.set('Content-Type', 'text/event-stream');
      responseHeaders.set('Cache-Control', 'no-store');
      responseHeaders.set('X-Content-Type-Options', 'nosniff');
      responseHeaders.set('X-Accel-Buffering', 'no');
      responseHeaders.set('X-Robots-Tag', 'noindex');
      responseHeaders.delete('Content-Length'); // Must remove for chunked

      return new Response(response.body, {
        status: response.status,
        headers: responseHeaders,
      });
    }

    // ── Standard (non-stream) proxy ──
    // Prevent search engines from indexing API responses
    responseHeaders.set('X-Robots-Tag', 'noindex, nofollow');
    // Signal to analytics that API calls are NOT page views
    responseHeaders.set('X-Content-Purpose', 'api');

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    clearTimeout(timeout);
    console.error('Proxy error:', error);

    const errorOrigin = request.headers.get('Origin') || 'https://syrabit.ai';
    const errorCors = getCorsHeaders(errorOrigin);

    if (error instanceof Error && error.name === 'AbortError') {
      return new Response(
        JSON.stringify({ error: 'Backend request timed out' }),
        {
          status: 504,
          headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': errorCors['Access-Control-Allow-Origin'], 'Access-Control-Allow-Credentials': 'true' },
        }
      );
    }

    return new Response(
      JSON.stringify({
        error: 'Backend service unavailable',
        details: error instanceof Error ? error.message : 'Unknown error',
      }),
      {
        status: 503,
        headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': errorCors['Access-Control-Allow-Origin'], 'Access-Control-Allow-Credentials': 'true' },
      }
    );
  }
}
