import { describe, expect, it, vi } from 'vitest';

import { reserveAnonQuota } from './chat';
import { usersRouter } from './users';
import type { Env } from '../types';

const ANON_ID = 'anon_0123456789abcdef0123456789abcdef';

function quotaDb(): D1Database {
  const values = new Map<string, number>();
  return {
    prepare: vi.fn((query: string) => {
      let args: unknown[] = [];
      const statement = {
        bind: (...bound: unknown[]) => {
          args = bound;
          return statement;
        },
        first: async () => {
          const key = `${String(args[0])}:${String(args[1])}`;
          if (query.includes('INSERT INTO anonymous_quota_usage')) {
            const legacy = Number(args[2]);
            const current = Math.max(values.get(key) ?? 0, legacy);
            if (query.includes('SELECT ?, ?, ? + 1')) {
              const limit = Number(args[5]);
              if (current >= limit) return null;
              values.set(key, current + 1);
              return { count: current + 1 };
            }
            values.set(key, current);
            return { count: current };
          }
          throw new Error(`Unexpected query: ${query}`);
        },
      };
      return statement;
    }),
  } as unknown as D1Database;
}

function quotaKv(initial = 0): KVNamespace {
  return {
    get: vi.fn(async () => initial > 0 ? String(initial) : null),
  } as unknown as KVNamespace;
}

describe('anonymous credit balance', () => {
  it('reads the same atomic D1 counter that chat reserves', async () => {
    const DB = quotaDb();
    const RATE_LIMIT_KV = quotaKv();
    await reserveAnonQuota(DB, RATE_LIMIT_KV, ANON_ID);

    const response = await usersRouter.fetch(
      new Request('https://api.example/credits', {
        headers: { 'x-anon-id': ANON_ID, 'CF-Connecting-IP': '203.0.113.9' },
      }),
      { DB, RATE_LIMIT_KV } as unknown as Env,
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
    const DB = quotaDb();
    const RATE_LIMIT_KV = quotaKv();
    const response = await usersRouter.fetch(
      new Request('https://api.example/credits', {
        headers: { 'x-anon-id': 'anon_wrong', 'CF-Connecting-IP': '203.0.113.9' },
      }),
      { DB, RATE_LIMIT_KV } as unknown as Env,
    );

    await expect(response.json()).resolves.toMatchObject({
      anon_id: 'ip_203_0_113_9',
      credits_used: 0,
      credits_remaining: 30,
    });
  });

  it('preserves a legacy KV balance before the first D1 reservation', async () => {
    const DB = quotaDb();
    const RATE_LIMIT_KV = quotaKv(20);
    const response = await usersRouter.fetch(
      new Request('https://api.example/credits', {
        headers: { 'x-anon-id': ANON_ID },
      }),
      { DB, RATE_LIMIT_KV } as unknown as Env,
    );

    await expect(response.json()).resolves.toMatchObject({
      credits_used: 20,
      credits_remaining: 10,
      monthly_limit: 30,
    });
  });
});