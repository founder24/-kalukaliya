import { describe, expect, it, vi } from 'vitest';

import { usersRouter } from './users';
import type { Env } from '../types';
import { currentQuotaDayPeriod } from '../services/anonymous';

const ANON_ID = 'anon_0123456789abcdef0123456789abcdef';

function quotaDb(dailyUsed = 0): D1Database {
  return {
    prepare: vi.fn((query: string) => {
      let args: unknown[] = [];
      const statement = {
        bind: (...bound: unknown[]) => {
          args = bound;
          return statement;
        },
        first: async () => {
          if (query.includes('SELECT COALESCE((')) return { count: dailyUsed };
          if (query.includes('SELECT count FROM anonymous_quota_usage')) {
            return null;
          }
          if (query.includes('INSERT INTO anonymous_quota_usage')) {
            return { count: 1 };
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
  it('reads the daily quota balance used by anonymous chat', async () => {
    const DB = quotaDb(1);
    const RATE_LIMIT_KV = quotaKv();

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
      credits_remaining: 5,
      credits_limit: 6,
      rpm_limit: 6,
      quota_period: 'day',
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
      credits_remaining: 6,
      rpm_limit: 6,
    });
  });

  it('does not carry a retired daily KV balance into the RPM bucket', async () => {
    const DB = quotaDb();
    const RATE_LIMIT_KV = quotaKv(20);
    const response = await usersRouter.fetch(
      new Request('https://api.example/credits', {
        headers: { 'x-anon-id': ANON_ID },
      }),
      { DB, RATE_LIMIT_KV } as unknown as Env,
    );

    await expect(response.json()).resolves.toMatchObject({
      credits_used: 0,
      credits_remaining: 6,
      rpm_limit: 6,
      quota_period: 'day',
    });
  });
});