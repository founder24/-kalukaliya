/**
 * Latency audit coverage that remains relevant to the Worker-only backend.
 */
import { describe, expect, it } from 'vitest';
import { checkRateLimit } from '../src/middleware/rate-limit';
import { createMockRateLimitNamespace } from './helpers/rate-limit-store';

describe('Edge rate-limit latency controls', () => {
  it('allows a fresh identity and preserves the edge rate-limit marker', async () => {
    const { namespace } = await createMockRateLimitNamespace();
    const result = await checkRateLimit(namespace, 'user-1', 'en', 30);
    expect(result.allowed).toBe(true);

    const headers = new Headers({ Authorization: 'Bearer token123' });
    headers.set('X-Rate-Limited-By', 'edge');
    expect(headers.get('X-Rate-Limited-By')).toBe('edge');
    expect(headers.get('Authorization')).toBe('Bearer token123');
  });

  it('uses one internal Durable Object request per check', async () => {
    const { namespace, fetch } = await createMockRateLimitNamespace(5);
    await checkRateLimit(namespace, 'user-perf', 'as', 30);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});