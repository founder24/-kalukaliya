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
  headers.delete('Content-Length');
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

  const controller = new AbortController();
  const timeoutMs = parseInt(env.PROXY_TIMEOUT_MS || '30000', 10) || 30000;
  const timeout = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(targetUrl, {
      method: request.method,
      headers: headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
      signal: controller.signal,
    });

    clearTimeout(timeout);

    const responseHeaders = new Headers(response.headers);
    const requestOrigin = request.headers.get('Origin') || 'https://syrabit.ai';
    const cors = getCorsHeaders(requestOrigin);
    responseHeaders.set('Access-Control-Allow-Origin', cors['Access-Control-Allow-Origin']);
    responseHeaders.set('Access-Control-Allow-Credentials', 'true');

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
