/**
 * API Proxy — Forwards requests to Azure Backend
 *
 * Features:
 * - Stream-aware: detects /stream paths and passes response.body directly (chunked)
 * - Injects Cloudflare metadata headers (X-Real-IP, CF-Ray-ID)
 * - Sets CORS origin dynamically via getCorsHeaders validation
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

  // Clone headers and inject Cloudflare-specific metadata
  const headers = new Headers(request.headers);
  headers.set('X-Real-IP', request.headers.get('CF-Connecting-IP') || 'unknown');
  headers.set('CF-Ray-ID', request.headers.get('CF-Ray') || '');
  headers.set('X-Forwarded-Proto', 'https');

  // Remove hop-by-hop headers that shouldn't be forwarded
  headers.delete('Host');
  headers.delete('Content-Length');

  try {
    const response = await fetch(targetUrl, {
      method: request.method,
      headers: headers,
      body: request.method !== 'GET' && request.method !== 'HEAD' ? request.body : undefined,
    });

    const responseHeaders = new Headers(response.headers);
    const requestOrigin = request.headers.get('Origin') || env.ALLOWED_ORIGIN || 'https://syrabit.ai';
    const corsOrigin = getCorsHeaders(requestOrigin)['Access-Control-Allow-Origin'];
    responseHeaders.set('Access-Control-Allow-Origin', corsOrigin);

    if (isStreamRequest) {
      // ── Stream-specific handling ──
      // Pass response body directly without buffering (chunked transfer)
      responseHeaders.set('Content-Type', 'text/event-stream');
      responseHeaders.set('Cache-Control', 'no-store');
      responseHeaders.set('Connection', 'keep-alive');
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
    console.error('Proxy error:', error);
    return new Response(
      JSON.stringify({
        error: 'Backend service unavailable',
        details: error instanceof Error ? error.message : 'Unknown error',
      }),
      {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }
    );
  }
}
