import { describe, it, expect, vi } from 'vitest';
import { handleISR } from '../src/routes/isr';

function createMockEnv(overrides: Partial<Env> = {}): Env {
  return {
    JWT_SECRET: 'test-secret-for-unit-tests-at-least-32-characters',
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

describe('ISR - handleISR', () => {
  it('Non-bot UA returns null (no ISR handling)', async () => {
    const env = createMockEnv();
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/page', {
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
    });

    const result = await handleISR(request, env, ctx);

    expect(result).toBeNull();
  });

  it('Bot UA with cache hit returns cached HTML with X-ISR-Cache: HIT', async () => {
    const cachedHtml = '<html><body>Cached Page</body></html>';
    const env = createMockEnv({
      ISR_CACHE_KV: {
        get: vi.fn(async () => cachedHtml),
        put: vi.fn(async () => {}),
        delete: vi.fn(async () => {}),
      } as unknown as KVNamespace,
    });
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/page', {
      headers: { 'User-Agent': 'Googlebot/2.1 (+http://www.google.com/bot.html)' },
    });

    const result = await handleISR(request, env, ctx);

    expect(result).not.toBeNull();
    expect(result!.status).toBe(200);
    expect(result!.headers.get('X-ISR-Cache')).toBe('HIT');
    expect(result!.headers.get('Content-Type')).toContain('text/html');
    const body = await result!.text();
    expect(body).toBe(cachedHtml);
  });

  it('Bot UA with cache miss falls through without an external backend request', async () => {
    const kvPutMock = vi.fn(async () => {});
    const env = createMockEnv({
      ISR_CACHE_KV: {
        get: vi.fn(async () => null),
        put: kvPutMock,
        delete: vi.fn(async () => {}),
      } as unknown as KVNamespace,
    });
    const ctx = createMockCtx();

    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    const request = new Request('https://syrabit.ai/about', {
      headers: { 'User-Agent': 'Googlebot/2.1 (+http://www.google.com/bot.html)' },
    });

    const result = await handleISR(request, env, ctx);

    expect(result).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(kvPutMock).not.toHaveBeenCalled();
  });

  it('Missing ISR_CACHE_KV binding returns null', async () => {
    const env = createMockEnv({
      ISR_CACHE_KV: undefined as unknown as KVNamespace,
    });
    const ctx = createMockCtx();
    const request = new Request('https://syrabit.ai/page', {
      headers: { 'User-Agent': 'Googlebot/2.1 (+http://www.google.com/bot.html)' },
    });

    const result = await handleISR(request, env, ctx);

    expect(result).toBeNull();
  });
});
