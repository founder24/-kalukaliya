import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

import worker from '../src/index';

function createMockEnv(overrides: Partial<Env> = {}): Env {
  return {
    JWT_SECRET: 'test-secret-for-unit-tests-at-least-32-characters',
    BACKEND_URL: 'http://localhost:8000',
    ALLOWED_ORIGIN: 'https://syrabit.ai',
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

describe('Deployment Audit - Full Worker Fetch Handler', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('CORS preflight (OPTIONS /api/v1/chat) returns 200 with CORS headers', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'OPTIONS',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
    expect(response.headers.get('Access-Control-Allow-Methods')).toContain('POST');
    expect(response.headers.get('Access-Control-Allow-Headers')).toContain('Authorization');
  });

  it('Health endpoint returns edge status directly', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();

    const request = new Request('https://syrabit.ai/health', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    const json = await response.json() as Record<string, unknown>;
    expect(json.status).toBe('healthy');
    expect(json.service).toBe('syrabit-edge');
    expect(json.timestamp).toBeDefined();
  });

  it('/robots.txt returns robots content', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/robots.txt', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    const text = await response.text();
    expect(text).toContain('User-agent: *');
    expect(text).toContain('Sitemap:');
    expect(response.headers.get('Content-Type')).toContain('text/plain');
  });

  it('/assets/missing returns 404', async () => {
    const env = createMockEnv({
      R2_BUCKET: { get: vi.fn(async () => null) } as unknown as R2Bucket,
    });
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/assets/missing.js', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(404);
  });

  it('/assets/found returns R2 object with correct headers', async () => {
    const mockBody = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('file-content'));
        controller.close();
      },
    });
    const mockR2Object = {
      body: mockBody,
      writeHttpMetadata: vi.fn((headers: Headers) => {
        headers.set('Content-Type', 'application/javascript');
      }),
    };
    const env = createMockEnv({
      R2_BUCKET: { get: vi.fn(async () => mockR2Object) } as unknown as R2Bucket,
    });
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/assets/app.js', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    expect(response.headers.get('Content-Type')).toBe('application/javascript');
    expect(response.headers.get('Cache-Control')).toContain('immutable');
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
  });

  it('Unknown path returns 404', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/unknown-path', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(404);
  });

  it('JWT-protected endpoint without token gets passed through to backend', async () => {
    const env = createMockEnv({ ALLOWED_ORIGIN: 'http://localhost:3000' });
    const ctx = createMockCtx();

    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ data: 'ok' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));

    // /api/v1/users is not in PUBLIC_PATHS, so JWT will return
    // "Missing or invalid Authorization header" -- but the edge does NOT reject that
    const request = new Request('https://syrabit.ai/api/v1/users', {
      method: 'GET',
    });

    const response = await worker.fetch(request, env, ctx);

    // Should pass through to backend (not 401)
    expect(response.status).toBe(200);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      expect.stringContaining('http://localhost:8000/api/v1/users'),
      expect.anything(),
    );
  });

  it('Rate-limited chat POST with KV mock - allowed when under limit', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();

    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ reply: 'hello' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));

    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0',
      },
      body: JSON.stringify({ message: 'hello', lang: 'en' }),
    });

    const response = await worker.fetch(request, env, ctx);

    // Should not be rate limited (under limit)
    expect(response.status).not.toBe(429);
  });

  it('Rate-limited chat POST - blocked when over limit (returns 429)', async () => {
    const store: Record<string, string> = {};

    const env = createMockEnv({
      ALLOWED_ORIGIN: 'http://localhost:3000',
      RATE_LIMIT_KV: {
        get: vi.fn(async (key: string) => {
          // Return 30 (at limit) for any rate-limit key
          if (key.startsWith('rl:')) return '30';
          return store[key] || null;
        }),
        put: vi.fn(async () => {}),
        delete: vi.fn(async () => {}),
      } as unknown as KVNamespace,
    });
    const ctx = createMockCtx();

    vi.stubGlobal('fetch', vi.fn(async () => {
      return new Response(JSON.stringify({ reply: 'hello' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0',
      },
      body: JSON.stringify({ message: 'hello', lang: 'en' }),
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(429);
    const body = await response.json();
    expect(body).toHaveProperty('error', 'Rate limit exceeded');
  });

  it('Request without RATE_LIMIT_KV binding skips rate limiting gracefully', async () => {
    const env = createMockEnv({
      RATE_LIMIT_KV: undefined as unknown as KVNamespace,
    });
    const ctx = createMockCtx();

    vi.stubGlobal('fetch', vi.fn(async () => {
      return new Response(JSON.stringify({ reply: 'hello' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0',
      },
      body: JSON.stringify({ message: 'hello', lang: 'en' }),
    });

    // Should not throw - should skip rate limiting and proxy to backend
    const response = await worker.fetch(request, env, ctx);

    expect(response.status).not.toBe(429);
    expect(response.status).not.toBe(500);
  });
});
