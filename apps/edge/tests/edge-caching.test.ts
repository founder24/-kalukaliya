/**
 * Edge Caching Tests
 *
 * Verifies:
 * (a) CF Cache API is called for frontend GET redirect responses
 * (b) /health reads from ISR_CACHE_KV and falls back to backend fetch
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

function createMockEnv(overrides: Partial<Env> = {}): Env {
  return {
    JWT_SECRET: 'test-secret-for-unit-tests-at-least-32-characters',
    BACKEND_URL: 'http://localhost:8000',
    ALLOWED_ORIGIN: 'https://syrabit.ai',
    EDGE_SHARED_SECRET: 'test-edge-secret',
    R2_BUCKET: { get: vi.fn(async () => null) } as unknown as R2Bucket,
    RATE_LIMIT_KV: {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace,
    ISR_CACHE_KV: {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace,
    ...overrides,
  };
}

function createMockCtx(): ExecutionContext {
  return {
    waitUntil: vi.fn(),
    passThroughOnException: vi.fn(),
  };
}

// ═══════════════════════════════════════════════════════════════
// (a) CF Cache API for frontend GET redirect responses
// ═══════════════════════════════════════════════════════════════

describe('CF Cache API - Frontend GET Redirect Caching', () => {
  let originalFetch: typeof globalThis.fetch;
  let mockCacheMatch: ReturnType<typeof vi.fn>;
  let mockCachePut: ReturnType<typeof vi.fn>;
  let worker: { fetch: (request: Request, env: Env, ctx: ExecutionContext) => Promise<Response> };

  beforeEach(async () => {
    originalFetch = globalThis.fetch;
    mockCacheMatch = vi.fn(async () => undefined);
    mockCachePut = vi.fn(async () => {});
    vi.stubGlobal('caches', {
      default: {
        match: mockCacheMatch,
        put: mockCachePut,
      },
    });
    // Re-import the worker fresh each time to reset module-level healthCache
    vi.resetModules();
    worker = (await import('../src/index')).default;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('checks CF Cache API for non-API GET requests', async () => {
    // Request from a different host than ALLOWED_ORIGIN so it redirects
    const env = createMockEnv({ ALLOWED_ORIGIN: 'https://syrabit.ai' });
    const ctx = createMockCtx();
    const request = new Request('https://api.syrabit.ai/some-page', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(mockCacheMatch).toHaveBeenCalledTimes(1);
    expect(response.status).toBe(302);
  });

  it('returns cached response immediately on cache hit', async () => {
    const cachedResponse = new Response(null, {
      status: 302,
      headers: {
        'Location': 'https://syrabit.ai/some-page',
        'Cache-Control': 'public, s-maxage=3600, stale-while-revalidate=86400',
      },
    });
    mockCacheMatch.mockResolvedValueOnce(cachedResponse);

    const env = createMockEnv({ ALLOWED_ORIGIN: 'https://syrabit.ai' });
    const ctx = createMockCtx();
    const request = new Request('https://api.syrabit.ai/some-page', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(302);
    expect(response.headers.get('Location')).toBe('https://syrabit.ai/some-page');
    // On cache hit, put should NOT be called
    expect(mockCachePut).not.toHaveBeenCalled();
  });

  it('stores redirect response in cache on cache miss', async () => {
    const env = createMockEnv({ ALLOWED_ORIGIN: 'https://syrabit.ai' });
    const ctx = createMockCtx();
    const request = new Request('https://api.syrabit.ai/about', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(302);
    expect(response.headers.get('Location')).toBe('https://syrabit.ai/about');
    expect(response.headers.get('Cache-Control')).toBe('public, s-maxage=3600, stale-while-revalidate=86400');
    // Redirect responses are not cached (per Issue #29 fix), so waitUntil and cachePut should NOT be called
    expect(ctx.waitUntil).not.toHaveBeenCalled();
    expect(mockCachePut).not.toHaveBeenCalled();
  });

  it('does not cache API routes via CF Cache API', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ data: 'ok' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));

    const env = createMockEnv();
    const ctx = createMockCtx();
    const request = new Request('https://api.syrabit.ai/api/v1/users', {
      method: 'GET',
    });

    await worker.fetch(request, env, ctx);

    // Cache API should NOT be called for /api/ routes
    expect(mockCacheMatch).not.toHaveBeenCalled();
  });

  it('does not cache /health via CF Cache API', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('OK', { status: 200 })));

    const env = createMockEnv();
    const ctx = createMockCtx();
    const request = new Request('https://api.syrabit.ai/health', {
      method: 'GET',
    });

    await worker.fetch(request, env, ctx);

    // Cache API match should NOT be called for /health
    expect(mockCacheMatch).not.toHaveBeenCalled();
  });

  it('does not cache /assets/ via CF Cache API (served from R2)', async () => {
    const env = createMockEnv({
      R2_BUCKET: { get: vi.fn(async () => null) } as unknown as R2Bucket,
    });
    const ctx = createMockCtx();
    const request = new Request('https://api.syrabit.ai/assets/app.js', {
      method: 'GET',
    });

    await worker.fetch(request, env, ctx);

    // Cache API should NOT be called for /assets/ (handled by R2)
    expect(mockCacheMatch).not.toHaveBeenCalled();
  });
});

// ═══════════════════════════════════════════════════════════════
// (b) /health reads from ISR_CACHE_KV
// ═══════════════════════════════════════════════════════════════

describe('/health - ISR_CACHE_KV Cache Layer', () => {
  let originalFetch: typeof globalThis.fetch;
  let worker: { fetch: (request: Request, env: Env, ctx: ExecutionContext) => Promise<Response> };

  beforeEach(async () => {
    originalFetch = globalThis.fetch;
    // Re-import the worker fresh each time to reset module-level healthCache
    vi.resetModules();
    worker = (await import('../src/index')).default;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('reads health from ISR_CACHE_KV when KV has cached value', async () => {
    const kvPayload = JSON.stringify({ backend_reachable: true });
    const mockKV = {
      get: vi.fn(async () => kvPayload),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace;

    // Ensure global fetch is NOT called (should use KV cache)
    const fetchSpy = vi.fn(async () => new Response('', { status: 500 }));
    vi.stubGlobal('fetch', fetchSpy);

    const env = createMockEnv({ ISR_CACHE_KV: mockKV });
    const ctx = createMockCtx();
    const request = new Request('https://api.syrabit.ai/health', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    const body = await response.json() as Record<string, unknown>;
    expect(body.status).toBe('healthy');
    expect(body.backend_reachable).toBe(true);
    // KV should have been checked
    expect(mockKV.get).toHaveBeenCalledWith('edge:health');
    // Backend fetch should NOT have been called since KV had a value
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it('falls back to backend fetch when KV cache is empty', async () => {
    const mockKV = {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace;

    // Mock a successful backend health check
    const fetchSpy = vi.fn(async () => new Response('OK', { status: 200 }));
    vi.stubGlobal('fetch', fetchSpy);

    const env = createMockEnv({ ISR_CACHE_KV: mockKV });
    const ctx = createMockCtx();
    const request = new Request('https://api.syrabit.ai/health', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    const body = await response.json() as Record<string, unknown>;
    expect(body.status).toBe('healthy');
    expect(body.backend_reachable).toBe(true);
    // KV was checked but empty
    expect(mockKV.get).toHaveBeenCalledWith('edge:health');
    // Backend was fetched
    expect(fetchSpy).toHaveBeenCalled();
  });

  it('stores health result in ISR_CACHE_KV with 30s TTL after fresh fetch', async () => {
    const mockKV = {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace;

    vi.stubGlobal('fetch', vi.fn(async () => new Response('OK', { status: 200 })));

    const env = createMockEnv({ ISR_CACHE_KV: mockKV });
    const ctx = createMockCtx();
    const request = new Request('https://api.syrabit.ai/health', {
      method: 'GET',
    });

    await worker.fetch(request, env, ctx);

    // ctx.waitUntil should be called to store in KV
    expect(ctx.waitUntil).toHaveBeenCalled();
    // KV put should be called with the correct key and TTL
    expect(mockKV.put).toHaveBeenCalledWith(
      'edge:health',
      expect.any(String),
      { expirationTtl: 30 },
    );
    // Verify the stored payload
    const storedPayload = (mockKV.put as ReturnType<typeof vi.fn>).mock.calls[0][1];
    const parsed = JSON.parse(storedPayload);
    expect(parsed.backend_reachable).toBe(true);
  });

  it('handles KV read failure gracefully and falls back to backend', async () => {
    const mockKV = {
      get: vi.fn(async () => { throw new Error('KV unavailable'); }),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace;

    vi.stubGlobal('fetch', vi.fn(async () => new Response('OK', { status: 200 })));

    const env = createMockEnv({ ISR_CACHE_KV: mockKV });
    const ctx = createMockCtx();
    const request = new Request('https://api.syrabit.ai/health', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    const body = await response.json() as Record<string, unknown>;
    expect(body.status).toBe('healthy');
    expect(body.backend_reachable).toBe(true);
  });

  it('returns backend_reachable=false when backend is unreachable', async () => {
    const mockKV = {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace;

    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('Connection refused'); }));

    const env = createMockEnv({ ISR_CACHE_KV: mockKV });
    const ctx = createMockCtx();
    const request = new Request('https://api.syrabit.ai/health', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    const body = await response.json() as Record<string, unknown>;
    expect(body.backend_reachable).toBe(false);
  });
});
