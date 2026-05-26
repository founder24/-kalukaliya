import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { proxyRequest } from '../src/routes/api-proxy';

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

describe('API Proxy - proxyRequest', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('Successful proxy: passes through response from backend', async () => {
    const backendResponse = new Response(JSON.stringify({ data: 'test' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
    vi.stubGlobal('fetch', vi.fn(async () => backendResponse));

    const env = createMockEnv();
    const request = new Request('https://syrabit.ai/api/v1/users', {
      method: 'GET',
      headers: {
        'CF-Connecting-IP': '1.2.3.4',
        'CF-Ray': 'abc123',
      },
    });

    const response = await proxyRequest(request, env.AZURE_BACKEND_URL, env);

    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toEqual({ data: 'test' });
    expect(response.headers.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
  });

  it('Stream request: Content-Type is text/event-stream, no Content-Length', async () => {
    const streamBody = new ReadableStream({
      start(controller) {
        controller.enqueue(new TextEncoder().encode('data: hello\n\n'));
        controller.close();
      },
    });
    const backendResponse = new Response(streamBody, {
      status: 200,
      headers: {
        'Content-Type': 'text/event-stream',
        'Content-Length': '100',
      },
    });
    vi.stubGlobal('fetch', vi.fn(async () => backendResponse));

    const env = createMockEnv();
    const request = new Request('https://syrabit.ai/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'CF-Connecting-IP': '1.2.3.4',
      },
      body: JSON.stringify({ message: 'hello' }),
    });

    const response = await proxyRequest(request, env.AZURE_BACKEND_URL, env);

    expect(response.status).toBe(200);
    expect(response.headers.get('Content-Type')).toBe('text/event-stream');
    expect(response.headers.get('Content-Length')).toBeNull();
    expect(response.headers.get('Cache-Control')).toBe('no-store');
  });

  it('Backend failure: returns 503', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new Error('Connection refused');
    }));

    const env = createMockEnv();
    const request = new Request('https://syrabit.ai/api/v1/users', {
      method: 'GET',
    });

    const response = await proxyRequest(request, env.AZURE_BACKEND_URL, env);

    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body.error).toBe('Backend service unavailable');
    expect(body.details).toBe('Connection refused');
  });

  it('Header injection: X-Real-IP and CF-Ray-ID are set', async () => {
    let capturedHeaders: Headers | null = null;
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      capturedHeaders = new Headers(init.headers as HeadersInit);
      return new Response('ok', { status: 200 });
    }));

    const env = createMockEnv();
    const request = new Request('https://syrabit.ai/api/v1/users', {
      method: 'GET',
      headers: {
        'CF-Connecting-IP': '10.0.0.1',
        'CF-Ray': 'ray-xyz-123',
      },
    });

    await proxyRequest(request, env.AZURE_BACKEND_URL, env);

    expect(capturedHeaders).not.toBeNull();
    expect(capturedHeaders!.get('X-Real-IP')).toBe('10.0.0.1');
    expect(capturedHeaders!.get('CF-Ray-ID')).toBe('ray-xyz-123');
    expect(capturedHeaders!.get('X-Forwarded-Proto')).toBe('https');
  });
});
