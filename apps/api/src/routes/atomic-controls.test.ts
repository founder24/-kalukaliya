import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { getPlatformProxy } from 'wrangler';

import { revokedRtKey, signAccessToken, signRefreshToken } from '../middleware/auth';
import type { Env } from '../types';
import { authRouter } from './auth';
import {
  anonymousQuotaKey,
  currentQuotaMonthPeriod,
  currentQuotaMinutePeriod,
} from '../services/anonymous';
import { adminContentRouter } from './admin-content';
import {
  chatRouter,
  fetchMatchedChunkContext,
  getAuthMonthlyQuotaUsage,
  insertChatRequestClaim,
  persistCompletedChat,
  releaseQuotaReservation,
  reserveAnonQuota,
  reserveAuthQuota,
} from './chat';

const API_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../');
const JWT_SECRET = 'atomic-controls-test-secret-at-least-32-characters';
const EDGE_SHARED_SECRET = 'atomic-controls-edge-secret-at-least-32-characters';

let env: Env;
let disposeProxy: () => Promise<void>;

async function trustedAnonHeaders(anonId: string): Promise<Record<string, string>> {
  const timestamp = String(Math.floor(Date.now() / 1000));
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(EDGE_SHARED_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const pathname = '/stream';
  const signature = await crypto.subtle.sign(
    'HMAC',
    key,
    new TextEncoder().encode(`${timestamp}:anonymous:${pathname}`),
  );
  return {
    'Content-Type': 'application/json',
    'x-anon-id': anonId,
    'x-edge-timestamp': timestamp,
    'x-edge-signature': Array.from(new Uint8Array(signature))
      .map(byte => byte.toString(16).padStart(2, '0'))
      .join(''),
  };
}

async function createFreeUser(): Promise<string> {
  const userId = crypto.randomUUID();
  await env.DB.prepare(
    `INSERT INTO users (id, email, role, subscription_tier, session_valid_after)
     VALUES (?, ?, 'student', 'free', 0)`,
  ).bind(userId, `${userId}@example.test`).run();
  return userId;
}

function previousMonthPeriod(period: string): string {
  const year = Number(period.slice(0, 4));
  const month = Number(period.slice(5, 7));
  if (!Number.isInteger(year) || !Number.isInteger(month) || month < 1 || month > 12) {
    throw new Error(`Invalid quota month: ${period}`);
  }
  return new Date(Date.UTC(year, month - 2, 1)).toISOString().slice(0, 7);
}

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
    EDGE_SHARED_SECRET,
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
  it('reserves exactly 30 authenticated free claims per UTC month under concurrency', async () => {
    const userId = await createFreeUser();
    const firstPeriod = '2026-09';
    const nextPeriod = '2026-10';
    const minutePeriod = '2026-09-29T12:00';

    const firstMonth = await Promise.all(
      Array.from({ length: 40 }, (_, index) => insertChatRequestClaim(
        env.DB,
        `month_sep_${index}_${crypto.randomUUID()}`,
        userId,
        false,
        false,
        minutePeriod,
        { period: firstPeriod, limit: 30 },
      )),
    );
    expect(firstMonth.filter(Boolean)).toHaveLength(30);
    expect(await getAuthMonthlyQuotaUsage(env.DB, userId, firstPeriod)).toBe(30);

    const nextMonth = await Promise.all(
      Array.from({ length: 30 }, (_, index) => insertChatRequestClaim(
        env.DB,
        `month_oct_${index}_${crypto.randomUUID()}`,
        userId,
        false,
        false,
        minutePeriod,
        { period: nextPeriod, limit: 30 },
      )),
    );
    expect(nextMonth.filter(Boolean)).toHaveLength(30);
    expect(await getAuthMonthlyQuotaUsage(env.DB, userId, nextPeriod)).toBe(30);
  });

  it('returns a distinct monthly-limit response and refunds the minute reservation', async () => {
    const userId = await createFreeUser();
    const period = currentQuotaMonthPeriod();
    const minutePeriod = currentQuotaMinutePeriod();
    const clientRequestId = `monthly_limit_${crypto.randomUUID().replace(/-/g, '')}`;
    await env.DB.prepare(
      'INSERT INTO monthly_quota_usage (user_id, period, count) VALUES (?, ?, 30)',
    ).bind(userId, period).run();
    const accessToken = await signAccessToken(userId, 'student', JWT_SECRET);

    const response = await chatRouter.fetch(
      new Request('https://api.example/stream', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: 'Explain kinetic energy',
          lang: 'en',
          client_request_id: clientRequestId,
        }),
      }),
      env,
    );

    expect(response.status).toBe(429);
    await expect(response.json()).resolves.toMatchObject({
      error_code: 'chat_monthly_limit',
      quota: { used: 30, limit: 30, period },
    });
    const minuteRow = await env.DB.prepare(
      'SELECT count FROM quota_usage WHERE user_id = ? AND period = ?',
    ).bind(userId, minutePeriod).first<{ count: number }>();
    expect(minuteRow?.count ?? 0).toBe(0);
    const claim = await env.DB.prepare(
      'SELECT request_id FROM chat_request_claims WHERE request_id = ?',
    ).bind(clientRequestId).first<{ request_id: string }>();
    expect(claim).toBeNull();
  });

  it('frees an authenticated monthly reservation when its owner cancels', async () => {
    const userId = await createFreeUser();
    const requestId = `monthly_cancel_${crypto.randomUUID().replace(/-/g, '')}`;
    const monthPeriod = currentQuotaMonthPeriod();
    const minutePeriod = currentQuotaMinutePeriod();
    await env.DB.prepare(
      `INSERT INTO quota_usage (id, user_id, period, count)
       VALUES (?, ?, ?, 1)`,
    ).bind(`${userId}:${minutePeriod}`, userId, minutePeriod).run();
    const inserted = await insertChatRequestClaim(
      env.DB,
      requestId,
      userId,
      false,
      true,
      minutePeriod,
      { period: monthPeriod, limit: 30 },
    );
    expect(inserted).toBe(true);
    expect(await getAuthMonthlyQuotaUsage(env.DB, userId, monthPeriod)).toBe(1);

    const accessToken = await signAccessToken(userId, 'student', JWT_SECRET);
    const response = await chatRouter.fetch(
      new Request('https://api.example/cancel', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ client_request_id: requestId }),
      }),
      env,
    );
    expect(response.status).toBe(202);
    await expect(response.json()).resolves.toMatchObject({ cancelled: true });
    expect(await getAuthMonthlyQuotaUsage(env.DB, userId, monthPeriod)).toBe(0);

    const minuteRow = await env.DB.prepare(
      'SELECT count FROM quota_usage WHERE user_id = ? AND period = ?',
    ).bind(userId, minutePeriod).first<{ count: number }>();
    expect(minuteRow?.count).toBe(0);
  });

  it('settles against the claim month after the calendar has rolled over', async () => {
    const userId = await createFreeUser();
    const currentPeriod = currentQuotaMonthPeriod();
    const claimPeriod = previousMonthPeriod(currentPeriod);
    const requestId = `monthly_settle_${crypto.randomUUID().replace(/-/g, '')}`;
    await env.DB.prepare(
      'INSERT INTO monthly_quota_usage (user_id, period, count) VALUES (?, ?, 29)',
    ).bind(userId, claimPeriod).run();
    const inserted = await insertChatRequestClaim(
      env.DB,
      requestId,
      userId,
      false,
      false,
      currentQuotaMinutePeriod(),
      { period: claimPeriod, limit: 30 },
    );
    expect(inserted).toBe(true);

    await persistCompletedChat(env.DB, {
      userId,
      sessionId: `session_${crypto.randomUUID()}`,
      userMessage: 'Explain inertia',
      assistantResponse: 'A short answer.',
      lang: 'en',
      modelUsed: 'test-model',
      isAnon: false,
      requestId,
      responseMetadata: {},
      confidenceTier: 'high',
    });

    const oldMonth = await env.DB.prepare(
      'SELECT count FROM monthly_quota_usage WHERE user_id = ? AND period = ?',
    ).bind(userId, claimPeriod).first<{ count: number }>();
    expect(oldMonth?.count).toBe(30);
    expect(await getAuthMonthlyQuotaUsage(env.DB, userId, claimPeriod)).toBe(30);
    expect(await getAuthMonthlyQuotaUsage(env.DB, userId, currentPeriod)).toBe(0);
    const claim = await env.DB.prepare(
      'SELECT status, monthly_period FROM chat_request_claims WHERE request_id = ?',
    ).bind(requestId).first<{ status: string; monthly_period: string }>();
    expect(claim).toEqual({ status: 'completed', monthly_period: claimPeriod });
  });

  it('allows exactly the anonymous limit under parallel reservations', async () => {
    const anonId = 'anon_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
    const results = await Promise.all(
      Array.from(
        { length: 40 },
        () => reserveAnonQuota(env.DB, env.RATE_LIMIT_KV, anonId),
      ),
    );

    expect(results.filter(result => result.allowed)).toHaveLength(6);
    expect(results.filter(result => !result.allowed)).toHaveLength(34);

    const row = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(row?.count).toBe(6);
  });

  it('does not carry partial daily KV usage into a minute bucket', async () => {
    const anonId = 'anon_11111111111111111111111111111111';
    await env.RATE_LIMIT_KV.put(anonymousQuotaKey(anonId), '20');

    const results = await Promise.all(
      Array.from(
        { length: 20 },
        () => reserveAnonQuota(env.DB, env.RATE_LIMIT_KV, anonId),
      ),
    );

    expect(results.filter(result => result.allowed)).toHaveLength(6);
    expect(results.filter(result => !result.allowed)).toHaveLength(14);
    const row = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(row?.count).toBe(6);
  });

  it('retires an at-limit daily KV counter when RPM begins', async () => {
    const anonId = 'anon_22222222222222222222222222222222';
    await env.RATE_LIMIT_KV.put(anonymousQuotaKey(anonId), '30');

    const results = await Promise.all(
      Array.from(
        { length: 10 },
        () => reserveAnonQuota(env.DB, env.RATE_LIMIT_KV, anonId),
      ),
    );

    expect(results.filter(result => result.allowed)).toHaveLength(6);
    expect(results.filter(result => !result.allowed)).toHaveLength(4);
    const row = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(row?.count).toBe(6);
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
    expect(authResults.filter(result => result.allowed)).toHaveLength(6);
    expect(authResults.filter(result => !result.allowed)).toHaveLength(34);

    await Promise.all(Array.from(
      { length: 6 },
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
      { length: 6 },
      () => reserveAnonQuota(env.DB, env.RATE_LIMIT_KV, anonId),
    ));

    const operations = await Promise.all([
      ...Array.from({ length: 3 }, async () => {
        await releaseQuotaReservation(env.DB, anonId, true);
        return null;
      }),
      ...Array.from(
        { length: 3 },
        () => reserveAnonQuota(env.DB, env.RATE_LIMIT_KV, anonId),
      ),
    ]);
    const allowedReservations = operations.filter(
      result => result !== null && result.allowed,
    ).length;

    const row = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(row?.count).toBe(3 + allowedReservations);
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
        headers: await trustedAnonHeaders(anonId),
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
    const clientRequestId = `failed_auth_${crypto.randomUUID().replace(/-/g, '')}`;
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
        body: JSON.stringify({
          message: 'hello',
          lang: 'en',
          client_request_id: clientRequestId,
        }),
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
    const monthlyRow = await env.DB.prepare(
      'SELECT count FROM monthly_quota_usage WHERE user_id = ? AND period = ?',
    ).bind(userId, currentQuotaMonthPeriod()).first<{ count: number }>();
    expect(monthlyRow).toBeNull();
    const claim = await env.DB.prepare(
      'SELECT request_id FROM chat_request_claims WHERE request_id = ?',
    ).bind(clientRequestId).first<{ request_id: string }>();
    expect(claim).toBeNull();
  });

  it('uses the exact metadata passage when a legacy vector has no D1 chunk mirror', async () => {
    const chapterId = `semantic-${crypto.randomUUID()}`;
    const subjectId = `semantic-subject-${crypto.randomUUID()}`;
    await env.DB.prepare(
      `INSERT INTO subjects (id, stream_id, name, slug, is_published)
       VALUES (?, NULL, 'Semantic Physics', ?, 1)`,
    ).bind(subjectId, subjectId).run();
    await env.DB.prepare(
      `INSERT INTO chapters (id, subject_id, title, slug, status, notes_en)
       VALUES (?, ?, 'Semantic chapter', ?, 'published', NULL)`,
    ).bind(chapterId, subjectId, chapterId).run();
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
              subjectId,
              content: 'Matched metadata passage',
              medium: 'english',
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

  it('grounds semantic retrieval with the exact matched D1 passage', async () => {
    const chapterId = `matched-passage-${crypto.randomUUID()}`;
    const subjectId = `matched-subject-${crypto.randomUUID()}`;
    const vectorId = `vector-${crypto.randomUUID()}`;
    await env.DB.prepare(
      `INSERT INTO subjects (id, stream_id, name, slug, is_published)
       VALUES (?, NULL, 'Matched Physics', ?, 1)`,
    ).bind(subjectId, subjectId).run();
    await env.DB.prepare(
      `INSERT INTO chapters (id, subject_id, title, slug, status, notes_en)
       VALUES (?, ?, 'Long chapter', ?, 'published', 'Unrelated chapter opening')`,
    ).bind(chapterId, subjectId, chapterId).run();
    await env.DB.prepare(
      `INSERT INTO chunks (id, chapter_id, subject_id, source_type, medium, chunk_type, content, vector_id)
       VALUES (?, ?, ?, 'notes', 'english', 'text', ?, ?)`,
    ).bind(crypto.randomUUID(), chapterId, subjectId, 'Exact later-topic matched passage', vectorId).run();

    const chunks = await fetchMatchedChunkContext(
      env.DB,
      [{
        id: vectorId,
        score: 0.94,
        metadata: { chapterId, subjectId, chapterTitle: 'Long chapter' },
      }] as VectorizeMatch[],
      chapterId,
      'en',
      subjectId,
    );

    expect(chunks).toHaveLength(1);
    expect(chunks[0]?.content).toBe('Exact later-topic matched passage');
    expect(chunks[0]?.content).not.toContain('Unrelated chapter opening');
  });

  it('does not use metadata when the vector ID has a mismatched D1 mirror', async () => {
    const subjectId = `stale-subject-${crypto.randomUUID()}`;
    const expectedChapterId = `expected-${crypto.randomUUID()}`;
    const staleChapterId = `stale-${crypto.randomUUID()}`;
    const vectorId = `stale-vector-${crypto.randomUUID()}`;
    await env.DB.prepare(
      `INSERT INTO subjects (id, stream_id, name, slug, is_published)
       VALUES (?, NULL, 'Stale Physics', ?, 1)`,
    ).bind(subjectId, subjectId).run();
    await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO chapters (id, subject_id, title, slug, status)
         VALUES (?, ?, 'Expected chapter', ?, 'published')`,
      ).bind(expectedChapterId, subjectId, expectedChapterId),
      env.DB.prepare(
        `INSERT INTO chapters (id, subject_id, title, slug, status)
         VALUES (?, ?, 'Stale chapter', ?, 'published')`,
      ).bind(staleChapterId, subjectId, staleChapterId),
      env.DB.prepare(
        `INSERT INTO chunks (id, chapter_id, subject_id, source_type, medium, content, vector_id)
         VALUES (?, ?, ?, 'notes', 'english', 'Wrong mirrored passage', ?)`,
      ).bind(crypto.randomUUID(), staleChapterId, subjectId, vectorId),
    ]);

    const chunks = await fetchMatchedChunkContext(
      env.DB,
      [{
        id: vectorId,
        score: 0.95,
        metadata: {
          chapterId: expectedChapterId,
          subjectId,
          chapterTitle: 'Expected chapter',
          content: 'Metadata must not bypass the stale mirror',
        },
      }] as VectorizeMatch[],
      expectedChapterId,
      'en',
      subjectId,
    );

    expect(chunks).toEqual([]);
  });
});

describe('D1-backed authentication rate limits', () => {
  it('admits ten concurrent login attempts and stores only a hashed client IP', async () => {
    const clientIp = '198.51.100.181';
    const nowMs = (Math.floor(Date.now() / 60_000) + 2) * 60_000 + 1234;
    const request = () => new Request('https://api.example/login', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'CF-Connecting-IP': clientIp,
        'X-Forwarded-For': '203.0.113.250',
      },
      body: JSON.stringify({ email: 'unknown-auth-limit@example.test', password: 'invalid' }),
    });

    const outcomes = await Promise.all(
      Array.from({ length: 12 }, () => authRouter.fetch(request(), env)),
    );
    const blocked = outcomes.filter(response => response.status === 429);

    expect(outcomes.filter(response => response.status === 401)).toHaveLength(10);
    expect(blocked).toHaveLength(2);
    expect(blocked.every(response => response.headers.has('Retry-After'))).toBe(true);

    const stored = await env.DB.prepare(
      `SELECT bucket_key, request_count
       FROM auth_rate_limits
       WHERE bucket_key LIKE 'auth:login:%'
       ORDER BY updated_at DESC
       LIMIT 1`,
    ).first<{ bucket_key: string; request_count: number }>();
    expect(stored?.request_count).toBe(11);
    expect(stored?.bucket_key).not.toContain(clientIp);
    expect(stored?.bucket_key).not.toContain('203.0.113.250');
  });

  it('applies the stricter independent admin-login limit', async () => {
    const nowMs = (Math.floor(Date.now() / 60_000) + 2) * 60_000 + 1234;
    const adminEmail = `auth-limit-${crypto.randomUUID()}@example.test`;
    const outcomes = await Promise.all(
      Array.from({ length: 6 }, () => adminContentRouter.fetch(
        new Request('https://api.example/login', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'CF-Connecting-IP': '198.51.100.182',
          },
          body: JSON.stringify({ email: adminEmail, password: 'invalid' }),
        }),
        env,
      )),
    );
    const blocked = outcomes.filter(response => response.status === 429);

    expect(outcomes.filter(response => response.status === 401)).toHaveLength(5);
    expect(blocked).toHaveLength(1);
    expect(blocked[0]?.status).toBe(429);
  });

  it('fails closed without Cloudflare client IP instead of trusting forwarded headers', async () => {
    const response = await authRouter.fetch(
      new Request('https://api.example/login', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Forwarded-For': '198.51.100.183',
        },
        body: JSON.stringify({ email: 'unknown-auth-limit@example.test', password: 'invalid' }),
      }),
      env,
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      error_code: 'rate_limit_storage_unavailable',
    });
  });
});

describe('atomic refresh-token rotation', () => {
  it('revokes the body refresh token when logout is authenticated with an access token', async () => {
    const userId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO users (id, email, role, subscription_tier, session_valid_after)
       VALUES (?, ?, 'student', 'free', 0)`,
    ).bind(userId, `${userId}@example.test`).run();

    const accessToken = await signAccessToken(userId, 'student', JWT_SECRET);
    const { token: refreshToken } = await signRefreshToken(userId, 'student', JWT_SECRET);
    const logout = await authRouter.fetch(
      new Request('https://api.example/logout', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ refresh_token: refreshToken }),
      }),
      env,
    );
    expect(logout.status).toBe(200);
    expect(logout.headers.get('Set-Cookie')).toContain(
      'syrabit_admin_session=; Path=/api/; Max-Age=0',
    );

    const replay = await authRouter.fetch(
      new Request('https://api.example/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refreshToken }),
      }),
      env,
    );
    expect(replay.status).toBe(401);
    // Logout now bumps the account-wide session cutoff (so a leaked access
    // token cannot survive logout either), and the refresh route checks that
    // cutoff before the single-token jti claim. The account-level message
    // fires first; the jti is still consumed so the token can never mint a
    // new session either way.
    await expect(replay.json()).resolves.toMatchObject({
      detail: 'Session expired after password change. Sign in again.',
    });
  });

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