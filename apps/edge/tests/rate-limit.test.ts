import { describe, it, expect, vi } from 'vitest';
import { checkRateLimit, rateLimitHeaders } from '../src/middleware/rate-limit';

function createMockKV(store: Record<string, string> = {}): KVNamespace {
  return {
    get: vi.fn(async (key: string) => store[key] ?? null),
    put: vi.fn(async (key: string, value: string) => {
      store[key] = value;
    }),
    delete: vi.fn(),
    list: vi.fn(),
    getWithMetadata: vi.fn(),
  } as unknown as KVNamespace;
}

describe('checkRateLimit', () => {
  it('allows requests under the limit', async () => {
    const kv = createMockKV();
    const result = await checkRateLimit(kv, 'user-1', 'en', 30);
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(29);
  });

  it('blocks requests at the limit', async () => {
    const store: Record<string, string> = {};
    const kv = createMockKV(store);
    // Simulate count already at limit
    const now = Date.now();
    const windowMs = 60 * 60 * 1000;
    const windowKey = Math.floor(now / windowMs);
    const key = `rl:user-1:en:${windowKey}`;
    store[key] = '30';

    const result = await checkRateLimit(kv, 'user-1', 'en', 30);
    expect(result.allowed).toBe(false);
    expect(result.remaining).toBe(0);
  });

  it('increments the counter correctly', async () => {
    const store: Record<string, string> = {};
    const kv = createMockKV(store);
    const now = Date.now();
    const windowMs = 60 * 60 * 1000;
    const windowKey = Math.floor(now / windowMs);
    const key = `rl:user-1:en:${windowKey}`;
    store[key] = '5';

    const result = await checkRateLimit(kv, 'user-1', 'en', 30);
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(24);
    expect(kv.put).toHaveBeenCalledWith(key, '6', { expirationTtl: 7200 });
  });
});

describe('rateLimitHeaders', () => {
  it('formats headers correctly when allowed', () => {
    const result = {
      allowed: true,
      remaining: 25,
      resetAt: 1700000000000,
    };
    const headers = rateLimitHeaders(result, 30);
    expect(headers['X-RateLimit-Limit']).toBe('30');
    expect(headers['X-RateLimit-Remaining']).toBe('25');
    expect(headers['X-RateLimit-Reset']).toBe('1700000000');
    expect(headers['Retry-After']).toBeUndefined();
  });

  it('includes Retry-After when blocked', () => {
    const futureReset = Date.now() + 60000;
    const result = {
      allowed: false,
      remaining: 0,
      resetAt: futureReset,
    };
    const headers = rateLimitHeaders(result, 30);
    expect(headers['X-RateLimit-Remaining']).toBe('0');
    expect(headers['Retry-After']).toBeDefined();
    expect(parseInt(headers['Retry-After'])).toBeGreaterThan(0);
  });
});
