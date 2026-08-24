/**
 * Native admin publish/seed contract checks.
 *
 * Uses a local D1 binding and deliberately omits BACKEND_URL. The checks prove
 * that legacy admin-session cookies and cron tokens reach Worker-native routes,
 * and that D1 conditional inserts reject duplicate queue launches.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { getPlatformProxy } from 'wrangler';
import { SignJWT } from 'jose';
import type { Env } from '../types';
import { resumePublishJobs, resumeSeedRuns } from './admin-content';
import { hashPassword } from '../middleware/auth';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const API_ROOT = path.resolve(__dirname, '../../');
const ADMIN_SECRET = 'admin-contract-secret';
const CRON_SECRET = 'cron-contract-secret';

function migrations(): string[] {
  return fs.readdirSync(path.join(API_ROOT, 'drizzle/migrations'))
    .filter(name => name.endsWith('.sql')).sort()
    .flatMap(name => fs.readFileSync(path.join(API_ROOT, 'drizzle/migrations', name), 'utf8')
      .split(';').map(fragment => fragment.split('\n')
        .filter(line => line.trim() && !line.trim().startsWith('--')).join('\n').trim())
      .filter(Boolean));
}

async function adminSession(): Promise<string> {
  return new SignJWT({ role: 'admin', type: 'admin' })
    .setProtectedHeader({ alg: 'HS256' }).setSubject('native-admin')
    .setIssuedAt().setExpirationTime('1h')
    .sign(new TextEncoder().encode(ADMIN_SECRET));
}

let env: Env;
let dispose: () => Promise<void>;
let workerFetch: (request: Request) => Promise<Response>;
let workerScheduled: (controller: ScheduledController, env: Env, ctx: ExecutionContext) => Promise<void>;
let session: string;
let chapterId: string;
const background: Promise<unknown>[] = [];

beforeAll(async () => {
  const proxy = await getPlatformProxy<Env>({
    configPath: path.join(API_ROOT, 'wrangler.toml'),
    remoteBindings: false,
    persist: false,
  });
  dispose = proxy.dispose;
  env = {
    ...proxy.env,
    JWT_SECRET: 'ordinary-user-secret',
    ADMIN_JWT_SECRET: ADMIN_SECRET,
    TRANSLATE_CRON_SECRET: CRON_SECRET,
    RESET_TOKEN_SECRET: 'reset-secret',
    EDGE_SHARED_SECRET: 'edge-secret',
    RAZORPAY_KEY_ID: 'rzp',
    RAZORPAY_KEY_SECRET: 'rzp-secret',
    RAZORPAY_WEBHOOK_SECRET: 'webhook-secret',
    RESEND_API_KEY: 'resend-secret',
    ALLOWED_ORIGINS: '*',
    APP_ENV: 'test',
    AI: {
      run: async (model: string, input: { text?: string[] }) => (
        model === '@cf/baai/bge-m3'
          ? { data: (input.text ?? []).map(() => ({ values: [0.01, 0.02] })) }
          : { response: 'Generated contract-test notes.' }
      ),
    } as unknown as Ai,
    // Intentionally no BACKEND_URL.
  };
  for (const statement of migrations()) await env.DB.prepare(statement).run();
  await env.DB.batch([
    env.DB.prepare(`INSERT INTO boards (id, name, slug) VALUES ('board','Board','board')`),
    env.DB.prepare(`INSERT INTO classes (id, board_id, name, slug) VALUES ('class','board','Class','class')`),
    env.DB.prepare(`INSERT INTO streams (id, class_id, name, slug) VALUES ('stream','class','Science','science')`),
    env.DB.prepare(`INSERT INTO subjects (id, stream_id, name, slug, is_published) VALUES ('subject','stream','Physics','physics',1)`),
    env.DB.prepare(`INSERT INTO chapters (id, subject_id, title, slug, status) VALUES ('chapter','subject','Native queue chapter','native-queue','draft')`),
    env.DB.prepare(`INSERT INTO chapters (id, subject_id, title, slug, status) VALUES ('chapter-two','subject','Second native chapter','native-queue-two','draft')`),
    env.DB.prepare(`INSERT INTO chapters (id, subject_id, title, slug, status) VALUES ('chapter-three','subject','Third native chapter','native-queue-three','draft')`),
  ]);
  await env.DB.prepare(`
    INSERT INTO users (id, email, hashed_password, role, name)
    VALUES ('native-admin', 'admin@example.test', ?, 'admin', 'Native Admin')
  `).bind(await hashPassword('correct-password')).run();
  chapterId = 'chapter';
  session = await adminSession();
  const { default: worker } = await import('../index.js');
  const executionContext = {
    waitUntil: (promise: Promise<unknown>) => { background.push(promise); },
    passThroughOnException: () => {},
  } as unknown as ExecutionContext;
  workerFetch = request => (worker.fetch as (r: Request, e: Env, c: ExecutionContext) => Promise<Response>)(
    request, env, executionContext,
  );
  workerScheduled = worker.scheduled as (controller: ScheduledController, env: Env, ctx: ExecutionContext) => Promise<void>;
});

afterAll(async () => {
  await Promise.allSettled(background);
  await dispose?.();
});

function adminRequest(pathname: string, method = 'GET', body?: unknown): Request {
  return new Request(`http://worker${pathname}`, {
    method,
    headers: {
      Cookie: `syrabit_admin_session=${session}`,
      ...(body ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
}

describe('Worker-native admin publishing and seed dispatch', () => {
  it('supports the existing admin login, verify, and logout cookie lifecycle', async () => {
    const login = await workerFetch(new Request('http://worker/api/v1/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: 'admin@example.test', password: 'correct-password' }),
    }));
    expect(login.status).toBe(200);
    const cookie = login.headers.get('Set-Cookie');
    expect(cookie).toContain('syrabit_admin_session=');
    const verify = await workerFetch(new Request('http://worker/api/v1/admin/verify', {
      headers: { Cookie: cookie ?? '' },
    }));
    expect(verify.status).toBe(200);
    const logout = await workerFetch(new Request('http://worker/api/v1/admin/logout', {
      method: 'POST', headers: { Cookie: cookie ?? '' },
    }));
    expect(logout.status).toBe(200);
    expect(logout.headers.get('Set-Cookie')).toContain('Max-Age=0');
  });

  it('queues a publish job through the existing admin-session cookie without BACKEND_URL', async () => {
    const response = await workerFetch(adminRequest(
      `/api/v1/admin/content/chapters/${chapterId}/publish`, 'POST',
    ));
    expect(response.status).toBe(200);
    expect(response.headers.get('X-Syrabit-Route')).toBe('worker-native');
    const body = await response.json() as { job_id: string; status: string };
    expect(body.status).toBe('queued');

    const duplicate = await workerFetch(adminRequest(
      `/api/v1/admin/content/chapters/${chapterId}/publish`, 'POST',
    ));
    expect(duplicate.status).toBe(409);
  });

  it('turns interrupted publish work into a retryable partial job', async () => {
    await env.DB.prepare(`
      INSERT INTO publish_jobs (id, chapter_id, status, progress, created_at, updated_at)
      VALUES ('abandoned-publish', 'chapter-two', 'running', '[]', 1, 1)
    `).run();
    await resumePublishJobs(env);
    const recovered = await env.DB.prepare(`SELECT status, error_log FROM publish_jobs WHERE id = 'abandoned-publish'`)
      .first<{ status: string; error_log: string }>();
    expect(recovered?.status).toBe('partial');
    expect(recovered?.error_log).toContain('lease expired');
  });

  it('serves a D1-authored published CMS document by its seo_slug', async () => {
    const created = await workerFetch(adminRequest('/api/v1/admin/content/cms-documents', 'POST', {
      title: 'Native CMS document', seo_slug: 'native-cms-document', content: 'Published content',
    }));
    expect(created.status).toBe(201);
    const document = await created.json() as { id: string };
    const published = await workerFetch(adminRequest(`/api/v1/admin/content/cms-documents/${document.id}/publish`, 'POST'));
    expect(published.status).toBe(200);
    const publicResponse = await workerFetch(new Request('http://worker/api/v1/content/cms-documents/native-cms-document'));
    expect(publicResponse.status).toBe(200);
    expect((await publicResponse.json() as { title: string }).title).toBe('Native CMS document');
  });

  it('uses the separate cron secret and rejects a concurrent seed launch', async () => {
    const headers = { Authorization: `Bearer ${CRON_SECRET}`, 'Content-Type': 'application/json' };
    const first = await workerFetch(new Request('http://worker/api/v1/admin/cron/seed-notes', {
      method: 'POST', headers, body: JSON.stringify({ chapter_ids: [chapterId, 'chapter-two', 'chapter-three'] }),
    }));
    expect(first.status).toBe(200);
    expect(first.headers.get('X-Syrabit-Route')).toBe('worker-native');
    const firstBody = await first.json() as { job: string; run_id: string };
    expect(firstBody.job).toBe('started');
    await Promise.allSettled(background);
    const queued = await env.DB.prepare(`SELECT status, processed FROM seed_runs WHERE id = ?`).bind(firstBody.run_id).first<{ status: string; processed: number }>();
    expect(queued).toMatchObject({ status: 'queued', processed: 2 });

    // Explicitly model another worker holding the D1 seed lock. The route's
    // conditional insert must reject it regardless of medium.
    await env.DB.prepare(`
      INSERT INTO seed_runs (id, medium, status, total_chapters, processed, failed, log, started_at)
      VALUES ('manual-running', 'as', 'running', 1, 0, 0, '[]', 1)
    `).run();
    const duplicate = await workerFetch(new Request('http://worker/api/v1/admin/cron/seed-assamese', {
      method: 'POST', headers, body: JSON.stringify({ chapter_ids: [chapterId] }),
    }));
    expect(duplicate.status).toBe(409);

    const forbidden = await workerFetch(new Request('http://worker/api/v1/admin/cron/seed-notes/status'));
    expect(forbidden.status).toBe(401);

    await resumeSeedRuns(env);
    const resumed = await env.DB.prepare(`SELECT status, processed FROM seed_runs WHERE id = ?`).bind(firstBody.run_id).first<{ status: string; processed: number }>();
    expect(resumed).toMatchObject({ status: 'completed', processed: 3 });
    await env.DB.prepare(`UPDATE seed_runs SET status = 'completed' WHERE id = 'manual-running'`).run();
  });

  it('reclaims only an expired interrupted lease and completes durable chapter outcomes once', async () => {
    await env.DB.batch([
      env.DB.prepare(`INSERT INTO chapters (id, subject_id, title, slug, status, notes_en) VALUES ('interrupted-one','subject','Interrupted one','interrupted-one','draft','Notes persisted before the Worker stopped.')`),
      env.DB.prepare(`INSERT INTO chapters (id, subject_id, title, slug, status) VALUES ('interrupted-two','subject','Interrupted two','interrupted-two','draft')`),
      env.DB.prepare(`INSERT INTO chapters (id, subject_id, title, slug, status) VALUES ('interrupted-three','subject','Interrupted three','interrupted-three','draft')`),
    ]);
    const queuedLog = JSON.stringify([
      { chapter_id: 'interrupted-one', status: 'queued', at: '2026-08-22T00:00:00.000Z' },
      { chapter_id: 'interrupted-two', status: 'queued', at: '2026-08-22T00:00:00.000Z' },
      { chapter_id: 'interrupted-three', status: 'queued', at: '2026-08-22T00:00:00.000Z' },
    ]);
    const current = Math.floor(Date.now() / 1000);
    await env.DB.batch([
      env.DB.prepare(`
        INSERT INTO seed_runs (id, medium, status, total_chapters, processed, failed, log, started_at, lease_token, lease_expires_at, expires_at)
        VALUES ('interrupted-seed', 'en', 'running', 3, 0, 0, ?, ?, 'abandoned-worker', ?, ?)
      `).bind(queuedLog, current - 60, current - 1, current + 86_400),
      env.DB.prepare(`
        INSERT INTO seed_runs (id, medium, status, total_chapters, processed, failed, log, started_at, lease_token, lease_expires_at, expires_at)
        VALUES ('active-seed', 'en', 'running', 1, 0, 0, '[]', ?, 'live-worker', ?, ?)
      `).bind(current, current + 600, current + 86_400),
    ]);

    const originalAi = env.AI;
    let noteGenerations = 0;
    env.AI = {
      run: async (model: string, input: { text?: string[] }) => {
        if (model === '@cf/baai/bge-m3') {
          return { data: (input.text ?? []).map(() => ({ values: [0.01, 0.02] })) };
        }
        noteGenerations++;
        return { response: `Recovered notes ${noteGenerations}` };
      },
    } as unknown as Ai;
    const scheduledContext = {
      waitUntil: (promise: Promise<unknown>) => { background.push(promise); },
      passThroughOnException: () => {},
    } as unknown as ExecutionContext;

    try {
      // This invokes the same */5 cron entrypoint Cloudflare calls. The first
      // tick claims only the expired run and processes its bounded two-item batch.
      await workerScheduled({ cron: '*/5 * * * *', scheduledTime: Date.now(), noRetry: () => {} } as ScheduledController, env, scheduledContext);
      let recovered = await env.DB.prepare(`SELECT status, processed FROM seed_runs WHERE id = 'interrupted-seed'`)
        .first<{ status: string; processed: number; failed?: number; log?: string }>();
      const stillActive = await env.DB.prepare(`SELECT status, lease_token FROM seed_runs WHERE id = 'active-seed'`)
        .first<{ status: string; lease_token: string }>();
      expect(recovered).toMatchObject({ status: 'queued', processed: 2 });
      expect(stillActive).toMatchObject({ status: 'running', lease_token: 'live-worker' });
      expect(noteGenerations).toBe(1);

      // The second cron tick finishes the final queued chapter. The first
      // chapter is marked done from its persisted notes instead of regenerated.
      await workerScheduled({ cron: '*/5 * * * *', scheduledTime: Date.now(), noRetry: () => {} } as ScheduledController, env, scheduledContext);
      recovered = await env.DB.prepare(`SELECT status, processed, failed, log FROM seed_runs WHERE id = 'interrupted-seed'`)
        .first<{ status: string; processed: number; failed?: number; log?: string }>();
      expect(recovered?.status).toBe('completed');
      expect(recovered?.processed).toBe(3);
      expect(recovered?.failed).toBe(0);
      expect(noteGenerations).toBe(2);
      expect(JSON.parse(recovered?.log ?? '[]').map((entry: { status: string }) => entry.status)).toEqual(['done', 'done', 'done']);

      // Once the non-expired lease finishes, a staff member can start a new
      // run. The normal (non-forced) retry observes existing notes and does
      // not duplicate generation.
      await env.DB.prepare(`UPDATE seed_runs SET status = 'completed' WHERE id = 'active-seed'`).run();
      const next = await workerFetch(new Request('http://worker/api/v1/admin/cron/seed-notes', {
        method: 'POST',
        headers: { Authorization: `Bearer ${CRON_SECRET}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ chapter_ids: ['interrupted-one'] }),
      }));
      expect(next.status).toBe(200);
      await Promise.allSettled(background);
      const nextRun = await next.json() as { run_id: string };
      const completedNextRun = await env.DB.prepare(`SELECT status, processed FROM seed_runs WHERE id = ?`)
        .bind(nextRun.run_id).first<{ status: string; processed: number }>();
      expect(completedNextRun).toMatchObject({ status: 'completed', processed: 1 });
      expect(noteGenerations).toBe(2);
    } finally {
      env.AI = originalAi;
    }
  });

  it('fences a stale AI response after the lease is reclaimed before chapter persistence', async () => {
    await env.DB.prepare(`
      INSERT INTO chapters (id, subject_id, title, slug, status)
      VALUES ('stale-provider','subject','Stale provider chapter','stale-provider','draft')
    `).run();
    const originalAi = env.AI;
    let providerCalls = 0;
    let firstStarted!: () => void;
    let releaseFirst!: (value: { response: string }) => void;
    const started = new Promise<void>(resolve => { firstStarted = resolve; });
    const firstResponse = new Promise<{ response: string }>(resolve => { releaseFirst = resolve; });
    env.AI = {
      run: async (model: string, input: { text?: string[] }) => {
        if (model === '@cf/baai/bge-m3') {
          return { data: (input.text ?? []).map(() => ({ values: [0.01, 0.02] })) };
        }
        providerCalls++;
        if (providerCalls === 1) {
          firstStarted();
          return firstResponse;
        }
        return { response: 'Recovered owner content' };
      },
    } as unknown as Ai;

    try {
      const startedRun = await workerFetch(new Request('http://worker/api/v1/admin/cron/seed-notes', {
        method: 'POST',
        headers: { Authorization: `Bearer ${CRON_SECRET}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ chapter_ids: ['stale-provider'] }),
      }));
      const { run_id: runId } = await startedRun.json() as { run_id: string };
      await started;
      const inFlight = await env.DB.prepare(`SELECT log FROM seed_runs WHERE id = ?`).bind(runId)
        .first<{ log: string }>();
      expect(JSON.parse(inFlight?.log ?? '[]')[0]?.status).toBe('running');

      // Model the original Worker crossing its lease deadline while its
      // provider request is still in flight. Recovery gets a new token and
      // commits its own result before the first response is released.
      await env.DB.prepare(`UPDATE seed_runs SET lease_expires_at = ? WHERE id = ?`)
        .bind(Math.floor(Date.now() / 1000) - 1, runId).run();
      await resumeSeedRuns(env);
      const recoveredChapter = await env.DB.prepare(`SELECT notes_en FROM chapters WHERE id = 'stale-provider'`)
        .first<{ notes_en: string }>();
      expect(recoveredChapter?.notes_en).toBe('Recovered owner content');

      releaseFirst({ response: 'Stale owner content' });
      await Promise.allSettled(background);
      const finalChapter = await env.DB.prepare(`SELECT notes_en FROM chapters WHERE id = 'stale-provider'`)
        .first<{ notes_en: string }>();
      const finalRun = await env.DB.prepare(`SELECT status, processed, log FROM seed_runs WHERE id = ?`).bind(runId)
        .first<{ status: string; processed: number; log: string }>();
      expect(providerCalls).toBe(2);
      expect(finalChapter?.notes_en).toBe('Recovered owner content');
      expect(finalRun?.status).toBe('completed');
      expect(finalRun?.processed).toBe(1);
      expect(JSON.parse(finalRun?.log ?? '[]')[0]?.status).toBe('done');
    } finally {
      env.AI = originalAi;
    }
  });

  it('leaves an AI response retryable when its lease expires before cron reclaims it', async () => {
    await env.DB.prepare(`
      INSERT INTO chapters (id, subject_id, title, slug, status)
      VALUES ('expired-before-cron','subject','Expired before cron','expired-before-cron','draft')
    `).run();
    const originalAi = env.AI;
    let providerCalls = 0;
    let firstStarted!: () => void;
    let releaseFirst!: (value: { response: string }) => void;
    const started = new Promise<void>(resolve => { firstStarted = resolve; });
    const firstResponse = new Promise<{ response: string }>(resolve => { releaseFirst = resolve; });
    env.AI = {
      run: async (model: string, input: { text?: string[] }) => {
        if (model === '@cf/baai/bge-m3') {
          return { data: (input.text ?? []).map(() => ({ values: [0.01, 0.02] })) };
        }
        providerCalls++;
        if (providerCalls === 1) {
          firstStarted();
          return firstResponse;
        }
        return { response: 'Recovered after expiry' };
      },
    } as unknown as Ai;

    try {
      const startedRun = await workerFetch(new Request('http://worker/api/v1/admin/cron/seed-notes', {
        method: 'POST',
        headers: { Authorization: `Bearer ${CRON_SECRET}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ chapter_ids: ['expired-before-cron'] }),
      }));
      const { run_id: runId } = await startedRun.json() as { run_id: string };
      await started;
      await env.DB.prepare(`UPDATE seed_runs SET lease_expires_at = ? WHERE id = ?`)
        .bind(Math.floor(Date.now() / 1000) - 1, runId).run();

      // The first Worker receives a provider response after its lease expired,
      // but before the scheduler has reclaimed the row. It must not write
      // either content or a durable done outcome.
      releaseFirst({ response: 'Expired owner content' });
      await Promise.allSettled(background);
      const beforeRecovery = await env.DB.prepare(`SELECT status, processed, log FROM seed_runs WHERE id = ?`).bind(runId)
        .first<{ status: string; processed: number; log: string }>();
      const beforeChapter = await env.DB.prepare(`SELECT notes_en FROM chapters WHERE id = 'expired-before-cron'`)
        .first<{ notes_en: string | null }>();
      expect(beforeRecovery?.status).toBe('running');
      expect(beforeRecovery?.processed).toBe(0);
      expect(JSON.parse(beforeRecovery?.log ?? '[]')[0]?.status).toBe('running');
      expect(beforeChapter?.notes_en).toBeNull();

      await resumeSeedRuns(env);
      const recovered = await env.DB.prepare(`SELECT status, processed, log FROM seed_runs WHERE id = ?`).bind(runId)
        .first<{ status: string; processed: number; log: string }>();
      const recoveredChapter = await env.DB.prepare(`SELECT notes_en FROM chapters WHERE id = 'expired-before-cron'`)
        .first<{ notes_en: string }>();
      expect(providerCalls).toBe(2);
      expect(recovered?.status).toBe('completed');
      expect(recovered?.processed).toBe(1);
      expect(JSON.parse(recovered?.log ?? '[]')[0]?.status).toBe('done');
      expect(recoveredChapter?.notes_en).toBe('Recovered after expiry');
    } finally {
      env.AI = originalAi;
    }
  });

  it('recovers a forced run after its atomic chapter outcome without regenerating it', async () => {
    await env.DB.prepare(`
      INSERT INTO chapters (id, subject_id, title, slug, status, notes_en)
      VALUES ('forced-atomic','subject','Forced atomic chapter','forced-atomic','draft','Original notes')
    `).run();
    const originalAi = env.AI;
    let providerCalls = 0;
    env.AI = {
      run: async (model: string, input: { text?: string[] }) => {
        if (model === '@cf/baai/bge-m3') {
          return { data: (input.text ?? []).map(() => ({ values: [0.01, 0.02] })) };
        }
        providerCalls++;
        return { response: 'Forced committed notes' };
      },
    } as unknown as Ai;

    try {
      const startedRun = await workerFetch(new Request('http://worker/api/v1/admin/cron/seed-notes', {
        method: 'POST',
        headers: { Authorization: `Bearer ${CRON_SECRET}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ chapter_ids: ['forced-atomic'], force: true }),
      }));
      const { run_id: runId } = await startedRun.json() as { run_id: string };
      await Promise.allSettled(background);
      const committed = await env.DB.prepare(`SELECT status, processed, log FROM seed_runs WHERE id = ?`).bind(runId)
        .first<{ status: string; processed: number; log: string }>();
      expect(committed?.status).toBe('completed');
      expect(committed?.processed).toBe(1);
      expect(JSON.parse(committed?.log ?? '[]')[0]?.status).toBe('done');

      // This is the state a Worker would leave if it died after the atomic
      // chapter/log commit but before its terminal run update. A forced run
      // must recognize its durable done entry rather than regenerate content.
      await env.DB.prepare(`
        UPDATE seed_runs
        SET status = 'running', lease_token = 'forced-interrupted',
            lease_expires_at = ?, completed_at = NULL
        WHERE id = ?
      `).bind(Math.floor(Date.now() / 1000) - 1, runId).run();
      await resumeSeedRuns(env);

      const recovered = await env.DB.prepare(`SELECT status, processed, failed, log FROM seed_runs WHERE id = ?`).bind(runId)
        .first<{ status: string; processed: number; failed: number; log: string }>();
      const chapter = await env.DB.prepare(`SELECT notes_en FROM chapters WHERE id = 'forced-atomic'`)
        .first<{ notes_en: string }>();
      expect(providerCalls).toBe(1);
      expect(chapter?.notes_en).toBe('Forced committed notes');
      expect(recovered?.status).toBe('completed');
      expect(recovered?.processed).toBe(1);
      expect(recovered?.failed).toBe(0);
      expect(JSON.parse(recovered?.log ?? '[]')[0]?.status).toBe('done');
    } finally {
      env.AI = originalAi;
    }
  });
});