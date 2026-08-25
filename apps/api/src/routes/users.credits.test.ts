import { describe, expect, it, vi } from 'vitest';

import { reserveAnonQuota } from './chat';
import { usersRouter } from './users';
import type { Env } from '../types';

const ANON_ID = 'anon_0123456789abcdef0123456789abcdef';

function quotaKv(): KVNamespace {
  const values = new Map<string, string>();
  return {
    get: vi.fn(async (key: string) => values.get(key) ?? null),
    put: vi.fn(async (key: string, value: string) => { values.set(key, value); }),
  } as unknown as KVNamespace;
}

describe('anonymous credit balance', () => {
  it('reads the same Worker KV counter that chat reserves', async () => {
    const RATE_LIMIT_KV = quotaKv();
    await reserveAnonQuota(RATE_LIMIT_KV, ANON_ID);

    const response = await usersRouter.fetch(
      new Request('https://api.example/credits', {
        headers: { 'x-anon-id': ANON_ID, 'CF-Connecting-IP': '203.0.113.9' },
      }),
      { RATE_LIMIT_KV } as unknown as Env,
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      anon_id: ANON_ID,
      tier: 'free',
      credits_used: 1,
      credits_remaining: 29,
      monthly_limit: 30,
    });
  });

  it('does not accept a malformed browser identity as a quota key', async () => {
    const RATE_LIMIT_KV = quotaKv();
    const response = await usersRouter.fetch(
      new Request('https://api.example/credits', {
        headers: { 'x-anon-id': 'anon_wrong', 'CF-Connecting-IP': '203.0.113.9' },
      }),
      { RATE_LIMIT_KV } as unknown as Env,
    );

    await expect(response.json()).resolves.toMatchObject({
      anon_id: 'ip_203_0_113_9',
      credits_used: 0,
      credits_remaining: 30,
    });
  });
});