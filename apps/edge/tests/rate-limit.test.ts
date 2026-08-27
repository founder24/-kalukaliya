import { describe, it, expect, vi } from 'vitest';
import { anonymousRateLimitIdentity, checkRateLimit } from '../src/middleware/rate-limit';
import { createMockRateLimitNamespace } from './helpers/rate-limit-store';

describe('Rate Limiting', () => {
  it('isolates anonymous browsers by validated persistent ID', () => {
    const request = new Request('https://syrabit.ai/api/v1/chat/stream', {
      headers: {
        'x-anon-id': 'anon_0123456789abcdef0123456789abcdef',
        'CF-Connecting-IP': '203.0.113.9',
      },
    });

    expect(anonymousRateLimitIdentity(request))
      .toBe('anon_0123456789abcdef0123456789abcdef');
  });

  it('uses the connection IP when the browser ID is missing or malformed', () => {
    const request = new Request('https://syrabit.ai/api/v1/chat/stream', {
      headers: {
        'x-anon-id': 'anon_wrong',
        'CF-Connecting-IP': '203.0.113.9',
      },
    });

    expect(anonymousRateLimitIdentity(request)).toBe('ip_203_0_113_9');
  });

  it('does not trust caller-controlled forwarding headers for fallback identity', () => {
    const request = new Request('https://syrabit.ai/api/v1/chat/stream', {
      headers: {
        'x-anon-id': 'anon_wrong',
        'X-Real-IP': '203.0.113.9',
        'X-Forwarded-For': '198.51.100.44',
      },
    });

    expect(anonymousRateLimitIdentity(request)).toBe('ip_unknown');
  });

  it('allows request under limit', async () => {
    const { namespace } = await createMockRateLimitNamespace();
    const result = await checkRateLimit(namespace, 'user-1', 'en', 30);
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(29);
  });

  it('blocks request at limit', async () => {
    const { namespace } = await createMockRateLimitNamespace(30);
    const result = await checkRateLimit(namespace, 'user-1', 'en', 30);
    expect(result.allowed).toBe(false);
    expect(result.remaining).toBe(0);
  });

  it('routes a bucket through one Durable Object stub', async () => {
    const { namespace, fetch } = await createMockRateLimitNamespace();
    await checkRateLimit(namespace, 'user-2', 'as', 10);
    await checkRateLimit(namespace, 'user-2', 'as', 10);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls[0]?.[0]).toContain('rl:user-2:as:');
  });

  it('returns correct remaining count', async () => {
    const { namespace } = await createMockRateLimitNamespace(5);
    const result = await checkRateLimit(namespace, 'user-3', 'en', 10);
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(4);
  });

  it('includes resetAt timestamp', async () => {
    const { namespace } = await createMockRateLimitNamespace();
    const result = await checkRateLimit(namespace, 'user-4', 'en', 30);
    expect(result.resetAt).toBeGreaterThan(Date.now());
  });

  it('allows exactly the limit under parallel requests', async () => {
    const { namespace } = await createMockRateLimitNamespace();
    const results = await Promise.all(
      Array.from({ length: 40 }, () => checkRateLimit(namespace, 'parallel-user', 'en', 30)),
    );
    expect(results.filter(result => result.allowed)).toHaveLength(30);
    expect(results.filter(result => !result.allowed)).toHaveLength(10);
  });
});
