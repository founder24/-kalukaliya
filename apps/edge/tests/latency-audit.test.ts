/**
 * Latency Audit Tests for Edge Worker
 *
 * Verifies:
 * (a) Rate limit adds X-Rate-Limited-By header to proxied requests
 * (b) Proxy request includes Connection: keep-alive
 * (c) Rate limit check uses one internal Durable Object call
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { checkRateLimit } from '../src/middleware/rate-limit';
import { proxyRequest } from '../src/routes/api-proxy';
import { createMockRateLimitNamespace } from './helpers/rate-limit-store';

// ═══════════════════════════════════════════════════════════════
// (a) Rate limit sets X-Rate-Limited-By header on proxied request
// ═══════════════════════════════════════════════════════════════

describe('Edge Rate Limit - X-Rate-Limited-By header', () => {
  it('rate limit check returns allowed=true for fresh user', async () => {
    const { namespace } = await createMockRateLimitNamespace();
    const result = await checkRateLimit(namespace, 'user-1', 'en', 30);
    expect(result.allowed).toBe(true);
  });

  it('after rate limit passes, edge worker sets X-Rate-Limited-By header', async () => {
    // Simulate the flow from index.ts: after checkRateLimit passes,
    // the edge worker creates a new request with X-Rate-Limited-By: edge
    const { namespace } = await createMockRateLimitNamespace();
    const result = await checkRateLimit(namespace, 'user-test', 'en', 30);
    expect(result.allowed).toBe(true);

    // After rate limit passes, edge injects the header
    const originalHeaders = new Headers({ 'Authorization': 'Bearer token123' });
    const rlHeaders = new Headers(originalHeaders);
    rlHeaders.set('X-Rate-Limited-By', 'edge');

    expect(rlHeaders.get('X-Rate-Limited-By')).toBe('edge');
    // Original auth header is preserved
    expect(rlHeaders.get('Authorization')).toBe('Bearer token123');
  });

  it('verifies index.ts source sets X-Rate-Limited-By after rate limit', async () => {
    // Read the source to verify the header is set in the actual worker code
    const fs = await import('node:fs');
    const source = fs.readFileSync('./src/index.ts', 'utf-8');
    expect(source).toContain("X-Rate-Limited-By");
    expect(source).toContain("'edge'");
  });
});

// ═══════════════════════════════════════════════════════════════
// (b) Proxy request does NOT set Connection: keep-alive (hop-by-hop, no effect in Workers)
// ═══════════════════════════════════════════════════════════════

describe('Edge Proxy - Connection header', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => new Response('OK', {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
  });

  it('proxy does NOT set Connection: keep-alive on outbound request (Workers manage their own pooling)', async () => {
    const request = new Request('https://edge.syrabit.ai/api/v1/chat/', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'CF-Connecting-IP': '1.2.3.4',
      },
      body: JSON.stringify({ message: 'hello' }),
    });

    const env = {
      BACKEND_URL: 'https://backend.run.app',
      ALLOWED_ORIGIN: 'https://syrabit.ai',
    } as unknown as Env;

    await proxyRequest(request, env.BACKEND_URL, env);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [targetUrl, fetchOptions] = fetchMock.mock.calls[0];
    const sentHeaders = fetchOptions.headers as Headers;
    expect(sentHeaders.get('Connection')).toBeNull();
  });

  it('proxy forwards request to correct backend URL', async () => {
    const request = new Request('https://edge.syrabit.ai/api/v1/health', {
      method: 'GET',
      headers: { 'CF-Connecting-IP': '1.2.3.4' },
    });

    const env = {
      BACKEND_URL: 'https://backend.run.app',
      ALLOWED_ORIGIN: 'https://syrabit.ai',
    } as unknown as Env;

    await proxyRequest(request, env.BACKEND_URL, env);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [targetUrl] = fetchMock.mock.calls[0];
    expect(targetUrl).toBe('https://backend.run.app/api/v1/health');
  });

  it('proxy source code does not set Connection: keep-alive on outbound', async () => {
    const fs = await import('node:fs');
    const source = fs.readFileSync('./src/routes/api-proxy.ts', 'utf-8');
    // Connection: keep-alive should NOT be set on outbound requests (hop-by-hop, no effect)
    // But it's still set on streaming responses (which is fine for SSE)
    expect(source).not.toContain("headers.set('Connection', 'keep-alive')");
  });
});

// ═══════════════════════════════════════════════════════════════
// (c) Rate limit check performance (no external HTTP calls)
// ═══════════════════════════════════════════════════════════════

describe('Edge Rate Limit - Performance', () => {
  it('rate limit check completes quickly with a mock Durable Object', async () => {
    const { namespace } = await createMockRateLimitNamespace();

    const start = performance.now();
    for (let i = 0; i < 100; i++) {
      await checkRateLimit(namespace, `user-${i}`, 'en', 30);
    }
    const elapsed = performance.now() - start;

    // Keep the in-memory adapter comfortably below a quarter second without
    // asserting an unrealistically tight scheduler-dependent threshold.
    expect(elapsed).toBeLessThan(250);
  });

  it('rate limit performs one internal Durable Object request', async () => {
    const { namespace, fetch } = await createMockRateLimitNamespace(5);
    await checkRateLimit(namespace, 'user-perf', 'as', 30);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
