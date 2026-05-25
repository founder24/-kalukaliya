import { describe, it, expect, vi } from 'vitest';
import { checkRateLimit } from '../src/middleware/rate-limit';

function createMockKV(store: Record<string, string> = {}) {
  return {
    get: vi.fn(async (key: string) => store[key] || null),
    put: vi.fn(async (key: string, value: string) => { store[key] = value; }),
  } as unknown as KVNamespace;
}

describe('Rate Limiting', () => {
  it('allows request under limit', async () => {
    const kv = createMockKV();
    const result = await checkRateLimit(kv, 'user-1', 'en', 30);
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(29);
  });

  it('blocks request at limit', async () => {
    const store: Record<string, string> = {};
    const kv = createMockKV(store);
    // Pre-set counter to limit
    const windowKey = Math.floor(Date.now() / (60 * 60 * 1000));
    store[`rl:user-1:en:${windowKey}`] = '30';

    const result = await checkRateLimit(kv, 'user-1', 'en', 30);
    expect(result.allowed).toBe(false);
    expect(result.remaining).toBe(0);
  });

  it('increments counter on each call', async () => {
    const kv = createMockKV();
    await checkRateLimit(kv, 'user-2', 'as', 10);
    expect(kv.put).toHaveBeenCalledWith(
      expect.stringContaining('rl:user-2:as:'),
      '1',
      expect.objectContaining({ expirationTtl: 7200 })
    );
  });

  it('returns correct remaining count', async () => {
    const store: Record<string, string> = {};
    const kv = createMockKV(store);
    const windowKey = Math.floor(Date.now() / (60 * 60 * 1000));
    store[`rl:user-3:en:${windowKey}`] = '5';

    const result = await checkRateLimit(kv, 'user-3', 'en', 10);
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(4);
  });

  it('includes resetAt timestamp', async () => {
    const kv = createMockKV();
    const result = await checkRateLimit(kv, 'user-4', 'en', 30);
    expect(result.resetAt).toBeGreaterThan(Date.now());
  });
});
