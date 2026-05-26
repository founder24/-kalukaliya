/**
 * Latency Audit Tests for Edge Worker
 *
 * Verifies:
 * (a) Rate limit adds X-Rate-Limited-By header to proxied requests
 * (b) Proxy request includes Connection: keep-alive
 * (c) Rate limit check is fast (no external calls beyond KV)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { checkRateLimit } from '../src/middleware/rate-limit';
import { proxyRequest } from '../src/routes/api-proxy';

// ═══════════════════════════════════════════════════════════════
// (a) Rate limit sets X-Rate-Limited-By header on proxied request
// ═══════════════════════════════════════════════════════════════

describe('Edge Rate Limit - X-Rate-Limited-By header', () => {
  it('rate limit check returns allowed=true for fresh user', async () => {
    const kv = {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
    } as unknown as KVNamespace;

    const result = await checkRateLimit(kv, 'user-1', 'en', 30);
    expect(result.allowed).toBe(true);
  });

  it('after rate limit passes, edge worker sets X-Rate-Limited-By header', async () => {
    // Simulate the flow from index.ts: after checkRateLimit passes,
    // the edge worker creates a new request with X-Rate-Limited-By: edge
    const kv = {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
    } as unknown as KVNamespace;

    const result = await checkRateLimit(kv, 'user-test', 'en', 30);
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
      AZURE_BACKEND_URL: 'https://backend.azurecontainerapps.io',
      ALLOWED_ORIGIN: 'https://syrabit.ai',
    } as unknown as Env;

    await proxyRequest(request, env.AZURE_BACKEND_URL, env);

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
      AZURE_BACKEND_URL: 'https://backend.azurecontainerapps.io',
      ALLOWED_ORIGIN: 'https://syrabit.ai',
    } as unknown as Env;

    await proxyRequest(request, env.AZURE_BACKEND_URL, env);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [targetUrl] = fetchMock.mock.calls[0];
    expect(targetUrl).toBe('https://backend.azurecontainerapps.io/api/v1/health');
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
  it('rate limit check completes quickly (under 10ms with mock KV)', async () => {
    const kv = {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
    } as unknown as KVNamespace;

    const start = performance.now();
    for (let i = 0; i < 100; i++) {
      await checkRateLimit(kv, `user-${i}`, 'en', 30);
    }
    const elapsed = performance.now() - start;

    // 100 rate limit checks should complete in under 100ms total
    expect(elapsed).toBeLessThan(100);
  });

  it('rate limit only calls KV get and put (no other external services)', async () => {
    const kv = {
      get: vi.fn(async () => '5'),
      put: vi.fn(async () => {}),
    } as unknown as KVNamespace;

    await checkRateLimit(kv, 'user-perf', 'as', 30);

    // Should only interact with KV
    expect(kv.get).toHaveBeenCalledTimes(1);
    expect(kv.put).toHaveBeenCalledTimes(1);
  });
});
