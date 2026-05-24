import { describe, it, expect } from 'vitest';
import { checkRateLimit, rateLimitHeaders } from '../src/middleware/rate-limit';

// Mock KV namespace
function createMockKV(): KVNamespace {
  const store = new Map<string, string>();
  return {
    get: async (key: string) => store.get(key) || null,
    put: async (key: string, value: string) => { store.set(key, value); },
    delete: async (key: string) => { store.delete(key); },
    list: async () => ({ keys: [], list_complete: true, cacheStatus: null }),
    getWithMetadata: async () => ({ value: null, metadata: null, cacheStatus: null }),
  } as unknown as KVNamespace;
}

describe('Rate Limiting', () => {
  it('allows request when under limit', async () => {
    const kv = createMockKV();
    const result = await checkRateLimit(kv, 'user-1', 'en', 30);
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(29);
  });

  it('blocks request when at limit', async () => {
    const kv = createMockKV();
    // Fill up to limit
    for (let i = 0; i < 30; i++) {
      await checkRateLimit(kv, 'user-2', 'en', 30);
    }
    const result = await checkRateLimit(kv, 'user-2', 'en', 30);
    expect(result.allowed).toBe(false);
    expect(result.remaining).toBe(0);
  });

  it('tracks per-user and per-language independently', async () => {
    const kv = createMockKV();
    await checkRateLimit(kv, 'user-3', 'en', 30);
    await checkRateLimit(kv, 'user-3', 'as', 30);
    const enResult = await checkRateLimit(kv, 'user-3', 'en', 30);
    expect(enResult.remaining).toBe(28); // 30 - 2 (second call for 'en')
  });

  it('returns correct resetAt timestamp', async () => {
    const kv = createMockKV();
    const result = await checkRateLimit(kv, 'user-4', 'en', 30);
    expect(result.resetAt).toBeGreaterThan(Date.now());
    // Should be within the next hour
    expect(result.resetAt).toBeLessThanOrEqual(Date.now() + 3600000);
  });

  it('rateLimitHeaders includes Retry-After when blocked', () => {
    const result = { allowed: false, remaining: 0, resetAt: Date.now() + 60000 };
    const headers = rateLimitHeaders(result, 30);
    expect(headers['X-RateLimit-Limit']).toBe('30');
    expect(headers['X-RateLimit-Remaining']).toBe('0');
    expect(headers['Retry-After']).toBeDefined();
  });

  it('rateLimitHeaders omits Retry-After when allowed', () => {
    const result = { allowed: true, remaining: 25, resetAt: Date.now() + 60000 };
    const headers = rateLimitHeaders(result, 30);
    expect(headers['X-RateLimit-Limit']).toBe('30');
    expect(headers['X-RateLimit-Remaining']).toBe('25');
    expect(headers['Retry-After']).toBeUndefined();
  });
});
