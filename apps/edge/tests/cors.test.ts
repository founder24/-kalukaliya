import { describe, it, expect, vi } from 'vitest';
import { getCorsHeaders, applyCorsHeaders } from '../src/middleware/cors';
import worker from '../src/index';

describe('CORS Middleware', () => {
  it('returns correct headers for allowed origin', () => {
    const headers = getCorsHeaders('https://syrabit.ai');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://syrabit.ai');
    expect(headers['Access-Control-Allow-Methods']).toContain('GET');
    expect(headers['Access-Control-Allow-Methods']).toContain('POST');
  });

  it('returns default origin for disallowed origin', () => {
    const headers = getCorsHeaders('https://evil.com');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://syrabit.ai');
  });

  it('allows app.syrabit.ai as valid origin', () => {
    const headers = getCorsHeaders('https://app.syrabit.ai');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://app.syrabit.ai');
  });

  it('applyCorsHeaders sets all headers on Headers object', () => {
    const headers = new Headers();
    applyCorsHeaders(headers, 'https://syrabit.ai');
    expect(headers.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
    expect(headers.get('Access-Control-Allow-Methods')).toContain('GET');
    expect(headers.get('Access-Control-Allow-Headers')).toContain('Authorization');
    expect(headers.get('Access-Control-Max-Age')).toBe('86400');
  });

  it('includes x-turnstile-token in Access-Control-Allow-Headers', () => {
    const headers = getCorsHeaders('https://syrabit.ai');
    expect(headers['Access-Control-Allow-Headers']).toContain('x-turnstile-token');
    expect(headers['Access-Control-Allow-Headers']).toContain('CF-Turnstile-Response');
  });

  it('accepts Pages preview URL as valid CORS origin', () => {
    const headers = getCorsHeaders('https://abc123.syrabitfrontend.pages.dev');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://abc123.syrabitfrontend.pages.dev');
  });

  it('accepts Pages preview URL with dashes as valid CORS origin', () => {
    const headers = getCorsHeaders('https://my-branch-preview.syrabitfrontend.pages.dev');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://my-branch-preview.syrabitfrontend.pages.dev');
  });

  it('rejects invalid Pages-like URLs that do not match the pattern', () => {
    const headers = getCorsHeaders('https://evil.syrabitfrontend.pages.dev.attacker.com');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://syrabit.ai');
  });
});

describe('Worker CORS preflight integration', () => {
  function createMockEnv(overrides: Partial<Env> = {}): Env {
    return {
      JWT_SECRET: 'test-secret-for-unit-tests-at-least-32-characters',
      CF_TURNSTILE_SECRET: 'test-turnstile-secret',
      AZURE_BACKEND_URL: 'http://localhost:8000',
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

  it('reflects a Pages preview origin in the preflight response', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();
    const previewOrigin = 'https://d2136f37.syrabitfrontend.pages.dev';

    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'OPTIONS',
      headers: {
        'Origin': previewOrigin,
        'Access-Control-Request-Method': 'POST',
      },
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe(previewOrigin);
    expect(response.headers.get('Access-Control-Allow-Methods')).toContain('POST');
    expect(response.headers.get('Access-Control-Allow-Headers')).toContain('x-turnstile-token');
  });

  it('reflects syrabit.ai origin in the preflight response', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();

    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'OPTIONS',
      headers: {
        'Origin': 'https://syrabit.ai',
        'Access-Control-Request-Method': 'POST',
      },
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
  });

  it('falls back to default origin for disallowed origins in preflight', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();

    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'OPTIONS',
      headers: {
        'Origin': 'https://evil.com',
        'Access-Control-Request-Method': 'POST',
      },
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).toBe(200);
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
  });

  it('reflects Pages preview origin in proxy response headers', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();
    const previewOrigin = 'https://abc123.syrabitfrontend.pages.dev';

    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));

    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'POST',
      headers: {
        'Origin': previewOrigin,
        'Content-Type': 'application/json',
        'x-turnstile-token': 'valid-token',
        'User-Agent': 'Mozilla/5.0',
      },
      body: JSON.stringify({ message: 'hello', lang: 'en' }),
    });

    // Mock turnstile verification
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (typeof url === 'string' && url.includes('turnstile')) {
        return new Response(JSON.stringify({ success: true }));
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    const response = await worker.fetch(request, env, ctx);

    expect(response.headers.get('Access-Control-Allow-Origin')).toBe(previewOrigin);
  });
});
