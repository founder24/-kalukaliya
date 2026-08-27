import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { getPlatformProxy } from 'wrangler';

import { revokedRtKey, signAccessToken, signRefreshToken } from '../middleware/auth';
import type { Env } from '../types';
import { authRouter } from './auth';
import { anonymousQuotaKey } from '../services/anonymous';
import {
  chatRouter,
  releaseQuotaReservation,
  reserveAnonQuota,
  reserveAuthQuota,
} from './chat';

const API_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../');
const JWT_SECRET = 'atomic-controls-test-secret-at-least-32-characters';

let env: Env;
let disposeProxy: () => Promise<void>;

function migrationStatements(): string[] {
  const directory = path.join(API_ROOT, 'drizzle/migrations');
  return fs.readdirSync(directory)
    .filter(file => file.endsWith('.sql'))
    .sort()
    .flatMap(file => fs.readFileSync(path.join(directory, file), 'utf8').split(';'))
    .map(fragment => fragment
      .split('\n')
      .filter(line => line.trim() && !line.trim().startsWith('--'))
      .join('\n')
      .trim())
    .filter(Boolean);
}

beforeAll(async () => {
  const proxy = await getPlatformProxy<Env>({
    configPath: path.join(API_ROOT, 'wrangler.toml'),
    remoteBindings: false,
    persist: false,
  });
  disposeProxy = proxy.dispose;
  env = {
    ...proxy.env,
    JWT_SECRET,
    ALLOWED_ORIGINS: '*',
    APP_ENV: 'test',
  };

  for (const statement of migrationStatements()) {
    await env.DB.prepare(statement).run();
  }
}, 60_000);

afterAll(async () => {
  await disposeProxy?.();
});

describe('atomic quota controls', () => {
  it('allows exactly the anonymous limit under parallel reservations', async () => {
    const anonId = 'anon_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    const results = await Promise.all(
      Array.from(
        { length: 40 },
        () => reserveAnonQuota(env.DB, env.RATE_LIMIT_KV, anonId),
      ),
    );

    expect(results.filter(result => result.allowed)).toHaveLength(30);
    expect(results.filter(result => !result.allowed)).toHaveLength(10);

    const row = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(row?.count).toBe(30);
  });

  it('preserves partial legacy usage across parallel first reservations', async () => {
    const anonId = 'anon_11111111111111111111111111111111';
    await env.RATE_LIMIT_KV.put(anonymousQuotaKey(anonId), '20');

    const results = await Promise.all(
      Array.from(
        { length: 20 },
        () => reserveAnonQuota(env.DB, env.RATE_LIMIT_KV, anonId),
      ),
    );

    expect(results.filter(result => result.allowed)).toHaveLength(10);
    expect(results.filter(result => !result.allowed)).toHaveLength(10);
    const row = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(row?.count).toBe(30);
  });

  it('does not reset an at-limit legacy anonymous user', async () => {
    const anonId = 'anon_22222222222222222222222222222222';
    await env.RATE_LIMIT_KV.put(anonymousQuotaKey(anonId), '30');

    const results = await Promise.all(
      Array.from(
        { length: 10 },
        () => reserveAnonQuota(env.DB, env.RATE_LIMIT_KV, anonId),
      ),
    );

    expect(results.every(result => !result.allowed)).toBe(true);
    const row = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(row?.count).toBe(30);
  });

  it('does not lose concurrent anonymous or authenticated releases', async () => {
    const anonId = 'anon_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
    await Promise.all(Array.from(
      { length: 20 },
      () => reserveAnonQuota(env.DB, env.RATE_LIMIT_KV, anonId),
    ));
    await Promise.all(Array.from(
      { length: 20 },
      () => releaseQuotaReservation(env.DB, anonId, true),
    ));

    const anonRow = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(anonRow?.count).toBe(0);

    const userId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO users (id, email, role, subscription_tier, session_valid_after)
       VALUES (?, ?, 'student', 'free', 0)`,
    ).bind(userId, `${userId}@example.test`).run();

    const authResults = await Promise.all(
      Array.from({ length: 40 }, () => reserveAuthQuota(env.DB, userId, 'free', 'student')),
    );
    expect(authResults.filter(result => result.allowed)).toHaveLength(30);
    expect(authResults.filter(result => !result.allowed)).toHaveLength(10);

    await Promise.all(Array.from(
      { length: 30 },
      () => releaseQuotaReservation(env.DB, userId, false),
    ));
    const authRow = await env.DB.prepare(
      'SELECT count FROM quota_usage WHERE user_id = ?',
    ).bind(userId).first<{ count: number }>();
    expect(authRow?.count).toBe(0);
  });

  it('preserves the count when reservations and releases interleave', async () => {
    const anonId = 'anon_dddddddddddddddddddddddddddddddd';
    await Promise.all(Array.from(
      { length: 30 },
      () => reserveAnonQuota(env.DB, env.RATE_LIMIT_KV, anonId),
    ));

    const operations = await Promise.all([
      ...Array.from({ length: 15 }, async () => {
        await releaseQuotaReservation(env.DB, anonId, true);
        return null;
      }),
      ...Array.from(
        { length: 15 },
        () => reserveAnonQuota(env.DB, env.RATE_LIMIT_KV, anonId),
      ),
    ]);
    const allowedReservations = operations.filter(
      result => result !== null && result.allowed,
    ).length;

    const row = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(row?.count).toBe(15 + allowedReservations);
  });

  it('fails chat closed when quota storage is unavailable', async () => {
    const unavailableDb = {
      prepare() {
        throw new Error('D1 unavailable');
      },
    } as unknown as D1Database;

    const response = await chatRouter.fetch(
      new Request('https://api.example/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_cccccccccccccccccccccccccccccccc',
        },
        body: JSON.stringify({ message: 'hello', lang: 'en' }),
      }),
      { ...env, DB: unavailableDb },
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      error_code: 'quota_storage_unavailable',
    });
  });

  it('releases the reserved slot when the streaming provider fails', async () => {
    const anonId = 'anon_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee';
    const background: Promise<unknown>[] = [];
    const failingEnv = {
      ...env,
      AI: {
        run: async (model: string) => {
          if (model === '@cf/baai/bge-m3') {
            return { data: [{ values: [0.1, 0.2, 0.3] }] };
          }
          throw new Error('provider unavailable');
        },
      } as unknown as Ai,
      VECTORIZE: {
        query: async () => ({ matches: [] }),
      } as unknown as VectorizeIndex,
    };
    const context = {
      waitUntil(promise: Promise<unknown>) {
        background.push(promise);
      },
      passThroughOnException() {},
    } as unknown as ExecutionContext;

    const response = await chatRouter.fetch(
      new Request('https://api.example/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'x-anon-id': anonId },
        body: JSON.stringify({ message: 'hello', lang: 'en' }),
      }),
      failingEnv,
      context,
    );

    expect(response.status).toBe(200);
    await response.text();
    await Promise.all(background);

    const row = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(row?.count).toBe(0);
  });

  it('keeps authenticated chat identity and releases its quota on provider failure', async () => {
    const userId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO users (id, email, role, subscription_tier, session_valid_after)
       VALUES (?, ?, 'student', 'free', 0)`,
    ).bind(userId, `${userId}@example.test`).run();
    const accessToken = await signAccessToken(userId, 'student', JWT_SECRET);
    const background: Promise<unknown>[] = [];
    const failingEnv = {
      ...env,
      AI: {
        run: async (model: string) => {
          if (model === '@cf/baai/bge-m3') {
            return { data: [{ values: [0.1, 0.2, 0.3] }] };
          }
          throw new Error('provider unavailable');
        },
      } as unknown as Ai,
      VECTORIZE: {
        query: async () => ({ matches: [] }),
      } as unknown as VectorizeIndex,
    };
    const context = {
      waitUntil(promise: Promise<unknown>) {
        background.push(promise);
      },
      passThroughOnException() {},
    } as unknown as ExecutionContext;

    const response = await chatRouter.fetch(
      new Request('https://api.example/stream', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ message: 'hello', lang: 'en' }),
      }),
      failingEnv,
      context,
    );
    expect(response.status).toBe(200);
    await response.text();
    await Promise.all(background);

    const authRow = await env.DB.prepare(
      'SELECT count FROM quota_usage WHERE user_id = ?',
    ).bind(userId).first<{ count: number }>();
    expect(authRow?.count).toBe(0);
  });

  it('loads D1 content for the chapter selected by semantic retrieval', async () => {
    const chapterId = `semantic-${crypto.randomUUID()}`;
    await env.DB.prepare(
      `INSERT INTO chapters (id, subject_id, title, slug, status, notes_en)
       VALUES (?, 'physics', 'Semantic chapter', ?, 'published', 'Matched chapter notes')`,
    ).bind(chapterId, chapterId).run();
    const background: Promise<unknown>[] = [];
    const failingEnv = {
      ...env,
      AI: {
        run: async (model: string) => {
          if (model === '@cf/baai/bge-m3') {
            return { data: [{ values: [0.1, 0.2, 0.3] }] };
          }
          throw new Error('provider unavailable');
        },
      } as unknown as Ai,
      VECTORIZE: {
        query: async () => ({
          matches: [{
            id: 'chunk-1',
            score: 0.9,
            metadata: {
              chapterId,
              chapterTitle: 'Semantic chapter',
              subjectId: 'physics',
            },
          }],
        }),
      } as unknown as VectorizeIndex,
    };
    const context = {
      waitUntil(promise: Promise<unknown>) {
        background.push(promise);
      },
      passThroughOnException() {},
    } as unknown as ExecutionContext;

    const response = await chatRouter.fetch(
      new Request('https://api.example/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_ffffffffffffffffffffffffffffffff',
        },
        body: JSON.stringify({ message: 'explain this', lang: 'en' }),
      }),
      failingEnv,
      context,
    );
    const stream = await response.text();
    await Promise.all(background);

    expect(stream).toContain('"rag_path":"vectorize_d1"');
    expect(stream).toContain('"rag_chapter_name":"Semantic chapter"');
  });
});

describe('atomic refresh-token rotation', () => {
  it('mints only one token pair from concurrent refresh requests', async () => {
    const userId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO users (id, email, role, subscription_tier, session_valid_after)
       VALUES (?, ?, 'student', 'free', 0)`,
    ).bind(userId, `${userId}@example.test`).run();

    const { token } = await signRefreshToken(userId, 'student', JWT_SECRET);
    const responses = await Promise.all(Array.from({ length: 12 }, () =>
      authRouter.fetch(
        new Request('https://api.example/refresh', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ refresh_token: token }),
        }),
        env,
      ),
    ));

    expect(responses.filter(response => response.status === 200)).toHaveLength(1);
    expect(responses.filter(response => response.status === 401)).toHaveLength(11);
  });

  it('fails refresh closed when the claim store is unavailable', async () => {
    const userId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO users (id, email, role, subscription_tier, session_valid_after)
       VALUES (?, ?, 'student', 'free', 0)`,
    ).bind(userId, `${userId}@example.test`).run();
    const { token } = await signRefreshToken(userId, 'student', JWT_SECRET);

    const unavailableDb = {
      prepare(query: string) {
        if (query.includes('INSERT INTO refresh_token_claims')) {
          throw new Error('D1 unavailable');
        }
        return env.DB.prepare(query);
      },
      batch: env.DB.batch.bind(env.DB),
      exec: env.DB.exec.bind(env.DB),
      dump: env.DB.dump.bind(env.DB),
      withSession: env.DB.withSession.bind(env.DB),
    } as D1Database;

    const response = await authRouter.fetch(
      new Request('https://api.example/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: token }),
      }),
      { ...env, DB: unavailableDb },
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      error_code: 'auth_storage_unavailable',
    });
  });

  it('continues to reject tokens revoked by the legacy KV scheme', async () => {
    const userId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO users (id, email, role, subscription_tier, session_valid_after)
       VALUES (?, ?, 'student', 'free', 0)`,
    ).bind(userId, `${userId}@example.test`).run();
    const { token, jti } = await signRefreshToken(userId, 'student', JWT_SECRET);
    await env.RATE_LIMIT_KV.put(revokedRtKey(jti), '1', { expirationTtl: 3600 });

    const response = await authRouter.fetch(
      new Request('https://api.example/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: token }),
      }),
      env,
    );

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toMatchObject({
      detail: 'Refresh token has already been used or revoked',
    });
  });

  it('fails refresh closed when the legacy revocation store is unavailable', async () => {
    const userId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO users (id, email, role, subscription_tier, session_valid_after)
       VALUES (?, ?, 'student', 'free', 0)`,
    ).bind(userId, `${userId}@example.test`).run();
    const { token } = await signRefreshToken(userId, 'student', JWT_SECRET);
    const unavailableKv = {
      get: async () => { throw new Error('KV unavailable'); },
    } as unknown as KVNamespace;

    const response = await authRouter.fetch(
      new Request('https://api.example/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: token }),
      }),
      { ...env, RATE_LIMIT_KV: unavailableKv },
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      error_code: 'auth_storage_unavailable',
    });
  });
});