/**
 * Worker-native admin publishing and content-seeding routes.
 *
 * This module deliberately retains the established /api/v1/admin/content/*
 * contracts. It uses D1, KV, Vectorize and Workers AI directly; no request is
 * forwarded to Cloud Run and no Google identity token is needed.
 */

import { Hono, type Context } from 'hono';
import { and, desc, eq } from 'drizzle-orm';
import { createDb } from '../db/client';
import { boards, classes, chapters, publishJobs, seedRuns, streams, subjects, users } from '../db/schema';
import { extractBearer, isSessionValid, sessionIssuedAt, signAdminToken, verifyAdminToken, verifyPassword, verifyToken } from '../middleware/auth';
import { generate } from '../services/ai';
import type { Env } from '../types';

export const adminContentRouter = new Hono<{ Bindings: Env }>();

type JobStep = {
  name: string;
  label: string;
  status?: 'pending' | 'running' | 'done' | 'failed' | 'skipped';
  result?: Record<string, unknown>;
  error?: string;
  started_at?: string;
  finished_at?: string;
};

type SeedLog = {
  chapter_id: string;
  title?: string;
  status: 'queued' | 'running' | 'done' | 'failed' | 'skipped';
  error?: string;
  at: string;
};

const now = () => Math.floor(Date.now() / 1000);
const SEED_LEASE_SECONDS = 900;

export function sanitizeGeneratedNotes(text: string): string {
  let cleaned = text.trim()
    .replace(/^```(?:markdown)?\s*/i, '')
    .replace(/\s*```$/, '')
    .trim();
  const firstHeading = cleaned.search(/^##\s+\S/m);
  if (firstHeading >= 0) return cleaned.slice(firstHeading).trim();
  cleaned = cleaned.replace(
    /^\s*(?:here|below|the following) are (?:comprehensive\s+)?(?:study\s+)?notes?\s+for the chapter[^.\n]*[.!?]\s*(?:---\s*)?/i,
    '',
  );
  return cleaned.trim();
}

function parseJson<T>(raw: string | null | undefined, fallback: T): T {
  try { return raw ? JSON.parse(raw) as T : fallback; } catch { return fallback; }
}

function cookieValue(cookie: string, key: string): string | null {
  const prefix = `${key}=`;
  return cookie.split(';').map(part => part.trim()).find(part => part.startsWith(prefix))
    ?.slice(prefix.length) ?? null;
}

async function requireAdmin(c: Context<{ Bindings: Env }>): Promise<string | Response> {
  const bearer = extractBearer(c.req.header('Authorization') ?? null);
  const session = cookieValue(c.req.header('Cookie') ?? '', 'syrabit_admin_session');
  for (const token of [session, bearer].filter((value): value is string => Boolean(value))) {
    const payload = await verifyAdminToken(token, c.env.ADMIN_JWT_SECRET);
    if (payload?.sub) {
      if (await isSessionValid(c.env.DB, payload.sub, payload.iat)) return payload.sub;
      const response = c.json({ detail: 'Session expired after password change. Sign in again.' }, 401);
      if (session) response.headers.set('Set-Cookie', 'syrabit_admin_session=; Path=/api/; Max-Age=0; HttpOnly; SameSite=Lax');
      return response;
    }
  }
  // Cloud Run also accepted a normal access token for a user whose role was
  // admin. Preserve that bearer-only compatibility path.
  if (bearer) {
    const access = await verifyToken(bearer, c.env.JWT_SECRET);
    if (access?.type === 'access' && access.role === 'admin' && access.sub
      && await isSessionValid(c.env.DB, access.sub, access.iat)) return access.sub;
  }
  return c.json({ detail: bearer || session ? 'Invalid or expired admin session' : 'Authentication required' }, 401);
}
async function cronOrAdminAuthorized(c: Context<{ Bindings: Env }>): Promise<boolean> {
  if (cronAuthorized(c)) return true;
  return typeof (await requireAdmin(c)) === 'string';
}

function adminCookie(token: string, secure: boolean): string {
  return `syrabit_admin_session=${token}; Path=/api/; Max-Age=28800; HttpOnly; SameSite=${secure ? 'Strict; Secure' : 'Lax'}`;
}

function slugify(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '') || crypto.randomUUID();
}

// Native session lifecycle for the existing AdminGuard. Keeping it here means
// all operational admin paths share exactly the same D1 identity source.
adminContentRouter.post('/login', async c => {
  const body = await safeBody(c);
  const email = typeof body.email === 'string' ? body.email.trim().toLowerCase() : '';
  const password = typeof body.password === 'string' ? body.password : '';
  if (!email || !password) return c.json({ detail: 'Email and password required' }, 400);
  const user = await createDb(c.env.DB).select().from(users).where(eq(users.email, email)).get();
  if (!user?.hashedPassword || !(await verifyPassword(password, user.hashedPassword))) {
    return c.json({ detail: 'Invalid credentials' }, 401);
  }
  if (user.role !== 'admin') return c.json({ detail: 'Insufficient permissions' }, 403);
  const issuedAt = await sessionIssuedAt(c.env.DB, user.id);
  const token = await signAdminToken(user.id, c.env.ADMIN_JWT_SECRET, issuedAt);
  const response = c.json({ status: 'ok', name: user.name ?? '', user_id: user.id });
  response.headers.set('Set-Cookie', adminCookie(token, c.env.APP_ENV === 'production'));
  return response;
});

adminContentRouter.get('/verify', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return c.json({ status: 'ok', user_id: actor });
});

adminContentRouter.post('/logout', async c => {
  const response = c.json({ status: 'ok', message: 'Logged out', server_revocation: false });
  response.headers.set('Set-Cookie', 'syrabit_admin_session=; Path=/api/; Max-Age=0; HttpOnly; SameSite=Lax');
  return response;
});

adminContentRouter.post('/content/boards', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const body = await safeBody(c);
  const name = typeof body.name === 'string' ? body.name.trim() : '';
  if (!name) return c.json({ detail: 'name is required' }, 422);
  const id = crypto.randomUUID();
  const status = typeof body.status === 'string' ? body.status : 'published';
  await createDb(c.env.DB).insert(boards).values({ id, name, slug: slugify(name), description: typeof body.description === 'string' ? body.description : null, status, createdAt: now(), updatedAt: now() });
  return c.json({ id, name, slug: slugify(name), status }, 201);
});

adminContentRouter.post('/content/classes', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const body = await safeBody(c); const name = typeof body.name === 'string' ? body.name.trim() : '';
  const boardId = typeof body.board_id === 'string' ? body.board_id : '';
  if (!name || !boardId) return c.json({ detail: 'name and board_id are required' }, 422);
  const id = crypto.randomUUID();
  const status = typeof body.status === 'string' ? body.status : 'published';
  await createDb(c.env.DB).insert(classes).values({ id, name, slug: slugify(name), boardId, status, createdAt: now() });
  return c.json({ id, name, board_id: boardId, status }, 201);
});

adminContentRouter.post('/content/streams', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const body = await safeBody(c); const name = typeof body.name === 'string' ? body.name.trim() : '';
  const classId = typeof body.class_id === 'string' ? body.class_id : '';
  if (!name || !classId) return c.json({ detail: 'name and class_id are required' }, 422);
  const id = crypto.randomUUID();
  const status = typeof body.status === 'string' ? body.status : 'published';
  await createDb(c.env.DB).insert(streams).values({ id, name, slug: slugify(name), classId, status, createdAt: now() });
  return c.json({ id, name, class_id: classId, status }, 201);
});

async function safeBody(c: Context<{ Bindings: Env }>): Promise<Record<string, unknown>> {
  try { return await c.req.json<Record<string, unknown>>(); } catch { return {}; }
}

async function writeJob(
  env: Env,
  jobId: string,
  status: string,
  steps: JobStep[],
  error?: string | null,
  terminal = false,
): Promise<void> {
  const timestamp = now();
  await createDb(env.DB).update(publishJobs).set({
    status,
    progress: JSON.stringify(steps),
    errorLog: error ?? null,
    updatedAt: timestamp,
    ...(terminal ? { completedAt: timestamp } : {}),
  }).where(eq(publishJobs.id, jobId));
}

async function prewarmSubject(env: Env, subjectId: string): Promise<void> {
  const rows = await createDb(env.DB).select({
    id: chapters.id,
    title: chapters.title,
    slug: chapters.slug,
    slugAs: chapters.slugAs,
    chapterNumber: chapters.chapterNumber,
    status: chapters.status,
    notesEn: chapters.notesEn,
    notesAs: chapters.notesAs,
    qaEn: chapters.qaEn,
  }).from(chapters).where(eq(chapters.subjectId, subjectId)).orderBy(chapters.chapterNumber);

  await env.CONTENT_KV.put(`subject:${subjectId}:chapters`, JSON.stringify(rows.map(chapter => ({
    chapter_id: chapter.id,
    title: chapter.title,
    slug: chapter.slug,
    slug_as: chapter.slugAs ?? null,
    chapter_number: chapter.chapterNumber ?? null,
    status: chapter.status ?? 'draft',
    notes_generated: Boolean(chapter.notesEn),
    has_assamese: Boolean(chapter.notesAs),
    has_qa: Boolean(chapter.qaEn && chapter.qaEn !== '[]'),
  }))), { expirationTtl: 86400 * 7 });
}

function step(steps: JobStep[], name: string): JobStep {
  const found = steps.find(item => item.name === name);
  if (!found) throw new Error(`Unknown publish step ${name}`);
  return found;
}

async function runNativeReindex(env: Env, chapter: {
  id: string; subjectId: string; notesEn: string | null; notesAs: string | null;
}): Promise<Record<string, unknown>> {
  const entries: Array<{ id: string; values: number[]; metadata: Record<string, string> }> = [];
  const sources = [
    { medium: 'english', text: chapter.notesEn },
    { medium: 'assamese', text: chapter.notesAs },
  ] as const;
  for (const source of sources) {
    const text = source.text?.trim();
    if (!text) continue;
    const chunks = text.match(/[\s\S]{1,1800}(?:\s|$)/g)?.map(part => part.trim()).filter(Boolean) ?? [];
    const embeddings = await (env.AI as unknown as {
      run(model: string, input: { text: string[] }): Promise<{ data: Array<{ values: number[] }> }>;
    }).run('@cf/baai/bge-m3', { text: chunks });
    chunks.forEach((content, index) => {
      const values = embeddings.data[index]?.values;
      if (values?.length) entries.push({
        id: `${chapter.id}_${source.medium}_notes_${index}`,
        values,
        metadata: {
          chapterId: chapter.id, subjectId: chapter.subjectId, medium: source.medium,
          sourceType: 'notes', chunkType: 'text', content: content.slice(0, 512),
        },
      });
    });
  }
  if (entries.length) await env.VECTORIZE.upsert(entries);
  await createDb(env.DB).update(chapters).set({ ragIndexedAt: now(), updatedAt: now() })
    .where(eq(chapters.id, chapter.id));
  return { status: entries.length ? 'done' : 'skipped', vectors: entries.length };
}

async function runPublish(env: Env, jobId: string, chapterId: string): Promise<void> {
  const leaseToken = crypto.randomUUID();
  const claimed = await env.DB.prepare(`
    UPDATE publish_jobs SET status = 'running', lease_token = ?, lease_expires_at = ?, updated_at = ?
    WHERE id = ? AND (status IN ('pending', 'partial') OR lease_expires_at < ?)
  `).bind(leaseToken, now() + 900, now(), jobId, now()).run();
  if ((claimed.meta.changes ?? 0) !== 1) return;
  const write = async (status: string, steps: JobStep[], error?: string | null, terminal = false) => {
    const timestamp = now();
    await env.DB.prepare(`
      UPDATE publish_jobs SET status=?, progress=?, error_log=?, updated_at=?, completed_at=CASE WHEN ? THEN ? ELSE completed_at END,
        lease_token=CASE WHEN ? THEN NULL ELSE lease_token END, lease_expires_at=CASE WHEN ? THEN NULL ELSE ? END
      WHERE id=? AND lease_token=?
    `).bind(status, JSON.stringify(steps), error ?? null, timestamp, terminal ? 1 : 0, timestamp, terminal ? 1 : 0, terminal ? 1 : 0, now() + 900, jobId, leaseToken).run();
  };
  const db = createDb(env.DB);
  const chapter = await db.select({
    id: chapters.id, title: chapters.title, subjectId: chapters.subjectId,
    notesEn: chapters.notesEn, notesAs: chapters.notesAs,
  }).from(chapters).where(eq(chapters.id, chapterId)).get();
  if (!chapter) {
    await write('failed', [], `Chapter ${chapterId} not found`, true);
    return;
  }
  const steps: JobStep[] = [
    { name: 'gcs', label: 'Write to native D1 content store', status: 'pending' },
    { name: 'cloudflare', label: 'Refresh Cloudflare KV content cache', status: 'pending' },
    { name: 'status_update', label: 'Update publish status', status: 'pending' },
    { name: 'pages_rebuild', label: 'Publish dynamic Worker content', status: 'pending' },
    { name: 'indexnow', label: 'IndexNow ping', status: 'pending' },
    { name: 'wikidata', label: 'Wikidata enrichment', status: 'skipped', result: { reason: 'not part of publish write path' } },
    { name: 'embeddings', label: 'Topic embeddings', status: 'skipped', result: { reason: 'chapter RAG reindex covers searchable content' } },
    { name: 'rag_reindex', label: 'RAG vector reindex', status: 'pending' },
  ];
  await write('running', steps);

  const run = async (name: string, action: () => Promise<Record<string, unknown>>, critical = false) => {
    const current = step(steps, name);
    current.status = 'running'; current.started_at = new Date().toISOString();
    await write('running', steps);
    try {
      current.result = await action();
      current.status = 'done';
    } catch (error) {
      current.status = 'failed';
      current.error = error instanceof Error ? error.message : String(error);
      if (critical) throw error;
    } finally {
      current.finished_at = new Date().toISOString();
      await write('running', steps);
    }
  };

  try {
    await run('gcs', async () => ({ status: 'done', store: 'd1', chapter_id: chapter.id }), true);
    await run('cloudflare', async () => {
      await prewarmSubject(env, chapter.subjectId);
      return { status: 'done', cache: 'CONTENT_KV' };
    }, true);
    await run('status_update', async () => {
      await db.update(chapters).set({ status: 'published', updatedAt: now() }).where(eq(chapters.id, chapter.id));
      return { status: 'done' };
    }, true);
    await run('pages_rebuild', async () => ({ status: 'done', delivery: 'worker-dynamic' }));
    await run('indexnow', async () => {
      if (!env.INDEXNOW_API_KEY) return { status: 'skipped', reason: 'INDEXNOW_API_KEY not configured' };
      const response = await fetch('https://api.indexnow.org/indexnow', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host: 'syrabit.ai', key: env.INDEXNOW_API_KEY, urlList: [`https://syrabit.ai/${chapter.title}`] }),
      });
      if (!response.ok) throw new Error(`IndexNow returned ${response.status}`);
      return { status: 'done' };
    });
    await run('rag_reindex', () => runNativeReindex(env, chapter));
    const hasCriticalFailure = ['gcs', 'cloudflare', 'status_update']
      .some(name => step(steps, name).status === 'failed');
    await write(hasCriticalFailure ? 'partial' : 'done', steps,
      hasCriticalFailure ? 'A critical native publish step failed.' : null, true);
  } catch (error) {
    await write('partial', steps, error instanceof Error ? error.message : String(error), true);
  }
}

/** Convert abandoned request-lifetime publish work into an explicit retryable state. */
export async function resumePublishJobs(env: Env): Promise<void> {
  await env.DB.prepare(`
    UPDATE publish_jobs
    SET status = 'partial', error_log = 'Worker execution lease expired; retry this publish job.', completed_at = ?, updated_at = ?
    WHERE status IN ('pending', 'running') AND (lease_expires_at IS NULL OR lease_expires_at < ?)
  `).bind(now(), now(), now() - 900).run();
}

async function updateSeedRun(
  env: Env, id: string, leaseToken: string, processed: number, failed: number, log: SeedLog[], status?: string,
): Promise<boolean> {
  const terminal = status === 'completed' || status === 'completed_with_errors';
  const timestamp = now();
  const result = await env.DB.prepare(`
    UPDATE seed_runs
    SET processed = ?, failed = ?, log = ?, status = COALESCE(?, status),
        completed_at = CASE WHEN ? THEN ? ELSE completed_at END,
        lease_token = CASE WHEN ? THEN NULL ELSE lease_token END,
        lease_expires_at = CASE WHEN ? THEN NULL ELSE lease_expires_at END
    WHERE id = ? AND status = 'running' AND lease_token = ? AND lease_expires_at >= ?
  `).bind(processed, failed, JSON.stringify(log), status ?? null, terminal ? 1 : 0, timestamp,
    terminal ? 1 : 0, terminal ? 1 : 0, id, leaseToken, timestamp).run();
  return (result.meta.changes ?? 0) === 1;
}

async function renewSeedLease(env: Env, id: string, leaseToken: string): Promise<boolean> {
  const result = await env.DB.prepare(`
    UPDATE seed_runs SET lease_expires_at = ?
    WHERE id = ? AND status = 'running' AND lease_token = ? AND lease_expires_at >= ?
  `).bind(now() + SEED_LEASE_SECONDS, id, leaseToken, now()).run();
  return (result.meta.changes ?? 0) === 1;
}

async function commitSeedChapter(
  env: Env,
  runId: string,
  leaseToken: string,
  chapterId: string,
  medium: 'en' | 'as',
  text: string,
  log: SeedLog[],
): Promise<boolean> {
  const timestamp = now();
  const field = medium === 'en' ? 'notes_en' : 'notes_as';
  const storedText = medium === 'en' ? sanitizeGeneratedNotes(text) : text.trim();
  const processed = log.filter(entry => entry.status === 'done').length;
  const failed = log.filter(entry => entry.status === 'failed').length;
  const result = await env.DB.batch([
    // D1 batches are transactional: the fenced content write and its durable
    // per-run outcome either commit together or both roll back. This applies
    // to forced runs too, so recovery never sees replaced content with a
    // queued chapter result.
    env.DB.prepare(`
      UPDATE chapters SET ${field} = ?, rag_updated_at = ?, updated_at = ?
      WHERE id = ? AND EXISTS (
        SELECT 1 FROM seed_runs
        WHERE id = ? AND status = 'running' AND lease_token = ? AND lease_expires_at >= ?
      )
    `).bind(storedText, timestamp, timestamp, chapterId, runId, leaseToken, timestamp),
    env.DB.prepare(`
      UPDATE seed_runs
      SET processed = ?, failed = ?, log = ?, lease_expires_at = ?
      WHERE id = ? AND status = 'running' AND lease_token = ? AND lease_expires_at >= ?
        AND EXISTS (SELECT 1 FROM chapters WHERE id = ?)
    `).bind(processed, failed, JSON.stringify(log), timestamp + SEED_LEASE_SECONDS, runId, leaseToken, timestamp, chapterId),
  ]);
  return (result[0]?.meta.changes ?? 0) === 1 && (result[1]?.meta.changes ?? 0) === 1;
}

async function runSeed(env: Env, runId: string, medium: 'en' | 'as'): Promise<void> {
  const db = createDb(env.DB);
  const leaseToken = crypto.randomUUID();
  const claim = await env.DB.prepare(
    `UPDATE seed_runs SET status = 'running', started_at = ?, lease_token = ?, lease_expires_at = ? WHERE id = ? AND status = 'queued'`,
  ).bind(now(), leaseToken, now() + SEED_LEASE_SECONDS, runId).run();
  if ((claim.meta.changes ?? 0) !== 1) return;
  const run = await db.select().from(seedRuns).where(eq(seedRuns.id, runId)).get();
  if (!run) return;
  // Only the Worker holding the new run lease requeues a chapter left in its
  // transient in-flight state by a prior interrupted invocation.
  const log = parseJson<SeedLog[]>(run.log, []).map(entry => (
    entry.status === 'running' ? { ...entry, status: 'queued' as const } : entry
  ));
  const batch = log.filter(entry => entry.status === 'queued').slice(0, 2);
  const checkpoint = async (status = 'running') => updateSeedRun(
    env, runId, leaseToken,
    log.filter(entry => entry.status === 'done').length,
    log.filter(entry => entry.status === 'failed').length,
    log, status,
  );
  // The Worker may await an AI model for a long time. Renewing the lease while
  // it is active prevents the five-minute cron from overlapping that invocation.
  const heartbeat = setInterval(() => {
    void renewSeedLease(env, runId, leaseToken).catch(error => {
      console.error(`[seed] lease heartbeat failed for ${runId}:`, error);
    });
  }, 60_000);
  try {
    for (const entry of batch) {
      const chapter = await db.select({
        id: chapters.id, title: chapters.title, notesEn: chapters.notesEn, notesAs: chapters.notesAs,
      }).from(chapters).where(eq(chapters.id, entry.chapter_id)).get();
      if (!chapter) {
        entry.status = 'skipped';
        entry.at = new Date().toISOString();
        if (!(await checkpoint())) return;
        continue;
      }
      // A non-forced run that crashed after the chapter write can complete
      // from its durable marker without a second generation.
      const existingNotes = medium === 'en' ? chapter.notesEn : chapter.notesAs;
      if (existingNotes?.trim() && !Boolean(run.isForced)) {
        entry.title = chapter.title;
        entry.status = 'done';
        entry.at = new Date().toISOString();
        if (!(await checkpoint())) return;
        continue;
      }
      entry.title = chapter.title;
      entry.status = 'running';
      entry.at = new Date().toISOString();
      if (!(await checkpoint())) return;
      try {
        if (medium === 'as' && !chapter.notesEn?.trim()) {
          entry.status = 'skipped';
          entry.error = 'English notes are missing';
          entry.at = new Date().toISOString();
        } else {
          const request = medium === 'en'
            ? {
              systemPrompt: 'You write accurate, structured AHSEC study notes in English. Use concise Markdown headings, definitions, examples, and exam revision points. Do not invent a syllabus. Return only the notes and begin with a ## heading; never add an introductory sentence.',
              userMessage: `Create study notes for the chapter titled "${chapter.title}". Begin directly with the first ## heading.`,
              maxTokens: 1800,
            }
            : {
              systemPrompt: 'Translate educational notes into clear Assamese. Preserve Markdown headings, formulas, and factual accuracy. Return only the Assamese study notes.',
              userMessage: `Translate these notes into Assamese:\n\n${chapter.notesEn ?? ''}`,
              maxTokens: 1800,
            };
          const result = await generate(env.AI, request);
          entry.status = 'done';
          entry.at = new Date().toISOString();
          if (!(await commitSeedChapter(env, runId, leaseToken, chapter.id, medium, result.text, log))) return;
        }
      } catch (error) {
        entry.status = 'failed';
        entry.error = error instanceof Error ? error.message : String(error);
        entry.at = new Date().toISOString();
      }
      if (!(await checkpoint())) return;
    }
    const processed = log.filter(entry => entry.status === 'done').length;
    const failed = log.filter(entry => entry.status === 'failed').length;
    const remaining = log.some(entry => entry.status === 'queued');
    await updateSeedRun(env, runId, leaseToken, processed, failed, log,
      remaining ? 'queued' : (failed ? 'completed_with_errors' : 'completed'));
  } finally {
    clearInterval(heartbeat);
  }
}

export async function resumeSeedRuns(env: Env): Promise<void> {
  // A crashed invocation cannot execute finally blocks. Requeue its lease
  // on the next five-minute cron tick after its 15-minute lease expires, then
  // process only bounded batches per tick.
  await env.DB.prepare(
    `UPDATE seed_runs SET status = 'queued', lease_token = NULL, lease_expires_at = NULL
     WHERE status = 'running' AND lease_expires_at < ?`,
  ).bind(now()).run();
  const rows = await createDb(env.DB).select({ id: seedRuns.id, medium: seedRuns.medium })
    .from(seedRuns).where(eq(seedRuns.status, 'queued')).limit(3);
  for (const row of rows) await runSeed(env, row.id, row.medium === 'as' ? 'as' : 'en');
}

async function launchSeed(c: Context<{ Bindings: Env }>, medium: 'en' | 'as'): Promise<Response> {
  const body = await safeBody(c);
  const limit = Math.max(1, Math.min(Number(body.limit ?? 100), 200));
  const force = Boolean(body.force);
  const requested = Array.isArray(body.chapter_ids)
    ? body.chapter_ids.filter((id): id is string => typeof id === 'string').slice(0, limit)
    : [];
  const db = createDb(c.env.DB);

  let candidateIds: string[] = [];
  if (!requested.length) {
    const field = medium === 'en' ? 'notes_en' : 'notes_as';
    const subject = typeof body.subject === 'string' ? body.subject.trim() : '';
    const board = typeof body.board === 'string' ? body.board.trim() : '';
    const predicates = [
      !force ? `(c.${field} IS NULL OR TRIM(c.${field}) = '')` : '1 = 1',
      subject ? '(s.id = ? OR s.slug = ?)' : '1 = 1',
      board ? '(b.id = ? OR b.slug = ?)' : '1 = 1',
    ];
    const bindings: unknown[] = [
      ...(subject ? [subject, subject] : []),
      ...(board ? [board, board] : []),
      limit,
    ];
    const result = await c.env.DB.prepare(
      `SELECT c.id FROM chapters c
       JOIN subjects s ON s.id = c.subject_id
       JOIN streams st ON st.id = s.stream_id
       JOIN classes cl ON cl.id = st.class_id
       JOIN boards b ON b.id = cl.board_id
       WHERE ${predicates.join(' AND ')} LIMIT ?`,
    ).bind(...bindings).all<{ id: string }>();
    candidateIds = (result.results ?? []).map((row: { id: string }) => row.id);
  }
  const chapterIds = requested.length ? requested : candidateIds;
  if (!chapterIds.length) {
    return c.json({ job: 'nothing_to_do', total_queued: 0, message: 'No chapters need this seed run.' });
  }
  const id = crypto.randomUUID();
  // One conditional INSERT is the lock: D1 serializes writes, so concurrent
  // staff requests cannot both observe "no running job" and start duplicate
  // Workers AI work.
  const created = await c.env.DB.prepare(`
    INSERT INTO seed_runs (id, medium, status, is_forced, total_chapters, processed, failed, log, started_at, expires_at)
    SELECT ?, ?, 'queued', ?, ?, 0, 0, ?, ?, ?
    WHERE NOT EXISTS (SELECT 1 FROM seed_runs WHERE status IN ('queued', 'running'))
  `).bind(
    id, medium, force ? 1 : 0, chapterIds.length,
    JSON.stringify(chapterIds.map(chapter_id => ({ chapter_id, status: 'queued', at: new Date().toISOString() }))),
    now(), now() + 86400 * 90,
  ).run();
  if ((created.meta.changes ?? 0) !== 1) {
    return c.json({ detail: 'A content seed job is already running.' }, 409);
  }
  c.executionCtx.waitUntil(runSeed(c.env, id, medium));
  return c.json({ job: 'started', run_id: id, total_queued: chapterIds.length, concurrency: 1 });
}

adminContentRouter.post('/content/chapters/:chapterId/publish', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const chapterId = c.req.param('chapterId');
  const db = createDb(c.env.DB);
  const chapter = await db.select({ id: chapters.id }).from(chapters).where(eq(chapters.id, chapterId)).get();
  if (!chapter) return c.json({ detail: 'Chapter not found' }, 404);
  const jobId = crypto.randomUUID();
  const created = await c.env.DB.prepare(`
    INSERT INTO publish_jobs (id, chapter_id, status, progress, created_at, updated_at)
    SELECT ?, ?, 'pending', '[]', ?, ?
    WHERE NOT EXISTS (
      SELECT 1 FROM publish_jobs
      WHERE chapter_id = ? AND status IN ('pending', 'running', 'partial')
    )
  `).bind(jobId, chapterId, now(), now(), chapterId).run();
  if ((created.meta.changes ?? 0) !== 1) {
    const active = await db.select({ id: publishJobs.id }).from(publishJobs)
      .where(and(eq(publishJobs.chapterId, chapterId), eq(publishJobs.status, 'pending'))).get();
    return c.json({ detail: 'A publish job is already running for this chapter.', job_id: active?.id ?? null }, 409);
  }
  c.executionCtx.waitUntil(runPublish(c.env, jobId, chapterId));
  return c.json({ job_id: jobId, status: 'queued', chapter_id: chapterId });
});

// Compatibility aliases for the existing editor. Shared CRUD lives in
// staffRouter, which is mounted immediately after this router in routes/index.
adminContentRouter.get('/content/chapters', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const subjectId = c.req.query('subject_id');
  if (!subjectId) return c.json({ detail: 'subject_id is required' }, 422);
  return c.redirect(new URL(`/api/v1/admin/content/chapters/${encodeURIComponent(subjectId)}`, c.req.url).toString(), 307);
});
adminContentRouter.patch('/content/chapters/:chapterId', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return c.redirect(new URL(`/api/v1/admin/content/chapter/${encodeURIComponent(c.req.param('chapterId'))}`, c.req.url).toString(), 307);
});
adminContentRouter.delete('/content/chapters/:chapterId', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return c.redirect(new URL(`/api/v1/admin/content/chapter/${encodeURIComponent(c.req.param('chapterId'))}`, c.req.url).toString(), 307);
});

adminContentRouter.post('/content/chapters/:chapterId/generate-notes', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const db = createDb(c.env.DB);
  const chapter = await db.select({ id: chapters.id, title: chapters.title }).from(chapters)
    .where(eq(chapters.id, c.req.param('chapterId'))).get();
  if (!chapter) return c.json({ detail: 'Chapter not found' }, 404);
  try {
    const result = await generate(c.env.AI, {
      systemPrompt: 'You write accurate, structured AHSEC study notes in English. Use concise Markdown headings, definitions, examples, and revision points. Do not invent a syllabus. Return only the notes and begin with a ## heading; never add an introductory sentence.',
      userMessage: `Create study notes for the chapter titled "${chapter.title}". Begin directly with the first ## heading.`,
      maxTokens: 1800,
    });
    await db.update(chapters).set({ notesEn: sanitizeGeneratedNotes(result.text), ragUpdatedAt: now(), updatedAt: now() })
      .where(eq(chapters.id, chapter.id));
    return c.json({ status: 'generated', chapter_id: chapter.id, model: result.model });
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'Notes generation failed' }, 502);
  }
});

async function generateAssameseNotes(c: Context<{ Bindings: Env }>): Promise<Response> {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const db = createDb(c.env.DB);
  const chapterId = c.req.param('chapterId');
  if (!chapterId) return c.json({ detail: 'Chapter id is required' }, 400);
  const chapter = await db.select({ id: chapters.id, title: chapters.title, notesEn: chapters.notesEn }).from(chapters)
    .where(eq(chapters.id, chapterId)).get();
  if (!chapter) return c.json({ detail: 'Chapter not found' }, 404);
  if (!chapter.notesEn?.trim()) return c.json({ detail: 'English notes are required before translation' }, 400);
  try {
    const result = await generate(c.env.AI, {
      systemPrompt: 'Translate educational notes into clear Assamese. Preserve Markdown, formulas, and factual accuracy. Return only Assamese notes.',
      userMessage: `Translate these notes for "${chapter.title}":\n\n${chapter.notesEn}`,
      maxTokens: 1800,
    });
    await db.update(chapters).set({ notesAs: result.text, ragUpdatedAt: now(), updatedAt: now() }).where(eq(chapters.id, chapter.id));
    return c.json({ status: 'translated', chapter_id: chapter.id, translated_text: result.text, word_count: result.text.split(/\s+/).filter(Boolean).length });
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'Translation failed' }, 502);
  }
}
adminContentRouter.post('/content/chapters/:chapterId/generate-notes/as', generateAssameseNotes);
adminContentRouter.post('/content/chapters/:chapterId/translate', generateAssameseNotes);

adminContentRouter.get('/content/translation-progress', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const row = await c.env.DB.prepare(`
    SELECT COUNT(*) AS total,
      SUM(CASE WHEN notes_as IS NOT NULL AND TRIM(notes_as) != '' THEN 1 ELSE 0 END) AS translated
    FROM chapters
  `).first<{ total: number; translated: number }>();
  const total = Number(row?.total ?? 0);
  const translated = Number(row?.translated ?? 0);
  return c.json({ total, translated, missing: total - translated, progress: total ? Math.round(translated * 100 / total) : 0 });
});

adminContentRouter.get('/content/draft-served-subjects', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const rows = await c.env.DB.prepare(`
    SELECT s.id, s.name, s.slug, s.is_published, COUNT(c.id) AS chapter_count
    FROM subjects s LEFT JOIN chapters c ON c.subject_id = s.id
    WHERE s.is_published = 0 GROUP BY s.id ORDER BY s.name
  `).all();
  return c.json({ subjects: rows.results ?? [] });
});

adminContentRouter.get('/content/coverage', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const row = await c.env.DB.prepare(`
    SELECT COUNT(*) AS total,
      SUM(CASE WHEN notes_en IS NOT NULL AND TRIM(notes_en) != '' THEN 1 ELSE 0 END) AS notes,
      SUM(CASE WHEN notes_as IS NOT NULL AND TRIM(notes_as) != '' THEN 1 ELSE 0 END) AS assamese
    FROM chapters
  `).first();
  return c.json(row ?? { total: 0, notes: 0, assamese: 0 });
});

adminContentRouter.post('/content/regenerate-sitemap', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return c.json({ status: 'queued', message: 'Published chapter routes are served dynamically by the Worker; no static sitemap rebuild is required.' });
});

adminContentRouter.get('/content/chapters/:chapterId/audit-log', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const rows = await c.env.DB.prepare(`
    SELECT * FROM content_audit_log WHERE target_id = ? ORDER BY created_at DESC LIMIT 100
  `).bind(c.req.param('chapterId')).all().catch(() => ({ results: [] }));
  return c.json({ entries: rows.results ?? [] });
});

adminContentRouter.get('/content/subject/:subjectId/chapter-cards', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const rows = await createDb(c.env.DB).select({
    chapterId: chapters.id, notes: chapters.notesEn, title: chapters.title,
  }).from(chapters).where(eq(chapters.subjectId, c.req.param('subjectId')));
  return c.json({ cards: rows.map(row => ({
    chapter_id: row.chapterId, title: row.title, notes_generated: Boolean(row.notes),
    word_count: row.notes?.trim().split(/\s+/).filter(Boolean).length ?? 0,
    pyq_count: 0, mark_wise_counts: {}, flashcard_count: 0, blog_count: 0,
    seo_topic_count: 0, linked_topics: [], seo_page_types: {}, seo_pages_published: 0,
  })) });
});

adminContentRouter.get('/content/chapters/:chapterId/stats', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const row = await createDb(c.env.DB).select({ id: chapters.id, notes: chapters.notesEn })
    .from(chapters).where(eq(chapters.id, c.req.param('chapterId'))).get();
  if (!row) return c.json({ detail: 'Chapter not found' }, 404);
  return c.json({
    chapter_id: row.id, notes_generated: Boolean(row.notes),
    word_count: row.notes?.trim().split(/\s+/).filter(Boolean).length ?? 0,
    pyq_count: 0, mark_wise_counts: {}, flashcard_count: 0, geo_blog_count: 0,
    pyq_html_count: 0, seo_topic_count: 0, linked_topics: [], seo_page_types: {}, seo_pages_published: 0,
  });
});

adminContentRouter.get('/content/subject/:subjectId/coverage', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const rows = await createDb(c.env.DB).select({ id: chapters.id, notes: chapters.notesEn })
    .from(chapters).where(eq(chapters.subjectId, c.req.param('subjectId')));
  return c.json({ chapters: rows.map(row => ({
    chapter_id: row.id, coverage_score: row.notes?.trim() ? 100 : 0,
  })) });
});

adminContentRouter.patch('/content/chapters/:chapterId/rag', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const body = await safeBody(c);
  const db = createDb(c.env.DB);
  await db.update(chapters).set({
    ragText: typeof body.rag_text_en === 'string' ? body.rag_text_en : undefined,
    ragTextAs: typeof body.rag_text_as === 'string' ? body.rag_text_as : undefined,
    updatedAt: now(),
  }).where(eq(chapters.id, c.req.param('chapterId')));
  return c.json({ status: 'saved', chapter_id: c.req.param('chapterId') });
});

async function uploadAdminAsset(c: Context<{ Bindings: Env }>, prefix: string): Promise<Response> {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  if (!c.env.R2_PUBLIC_URL) return c.json({ detail: 'R2_PUBLIC_URL is not configured' }, 503);
  const form = await c.req.formData().catch(() => null);
  const file = form?.get('file') as unknown;
  if (!file || typeof file !== 'object' || !('arrayBuffer' in file) || !('name' in file)) {
    return c.json({ detail: 'file is required' }, 422);
  }
  const upload = file as File;
  if (upload.size > 10 * 1024 * 1024) return c.json({ detail: 'File too large (max 10 MB)' }, 413);
  const extension = (upload.name.split('.').pop() || 'bin').toLowerCase().replace(/[^a-z0-9]/g, '');
  const key = `${prefix}/${new Date().toISOString().slice(0, 10)}/${crypto.randomUUID()}.${extension}`;
  await c.env.R2_BUCKET.put(key, await upload.arrayBuffer(), { httpMetadata: { contentType: upload.type || 'application/octet-stream' } });
  return c.json({ url: `${c.env.R2_PUBLIC_URL.replace(/\/$/, '')}/${key}`, filename: key });
}

adminContentRouter.post('/content/upload-image', async c => {
  const response = await uploadAdminAsset(c, 'admin-uploads');
  return response;
});

adminContentRouter.post('/content/subjects/:subjectId/thumbnail', async c => {
  const response = await uploadAdminAsset(c, 'subject-thumbnails');
  if (!response.ok) return response;
  const body = await response.clone().json<{ url: string }>();
  await createDb(c.env.DB).update(subjects).set({ imageUrl: body.url, updatedAt: now() })
    .where(eq(subjects.id, c.req.param('subjectId')));
  return response;
});

adminContentRouter.post('/content/chapters/:chapterId/attach-file', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const form = await c.req.formData().catch(() => null);
  const file = form?.get('file') as unknown;
  if (!file || typeof file !== 'object' || !('text' in file) || !('name' in file)) return c.json({ detail: 'file is required' }, 422);
  const upload = file as File;
  if (upload.size > 10 * 1024 * 1024) return c.json({ detail: 'File too large (max 10 MB)' }, 413);
  const ext = upload.name.split('.').pop()?.toLowerCase();
  if (ext !== 'txt' && ext !== 'md') return c.json({ detail: 'Only txt and md files can be extracted natively' }, 400);
  const text = (await upload.text()).trim();
  if (!text) return c.json({ detail: 'No text could be extracted from the file' }, 400);
  const db = createDb(c.env.DB);
  const chapter = await db.select({ ragText: chapters.ragText }).from(chapters).where(eq(chapters.id, c.req.param('chapterId'))).get();
  if (!chapter) return c.json({ detail: 'Chapter not found' }, 404);
  const combined = [chapter.ragText, text].filter(Boolean).join('\n\n');
  await db.update(chapters).set({ ragText: combined, ragUpdatedAt: now(), updatedAt: now() }).where(eq(chapters.id, c.req.param('chapterId')));
  return c.json({ text_extracted: text.length, appended: true });
});

type CmsDocument = Record<string, unknown> & { id: string; status: string; created_at: string; updated_at: string };
function cmsRow(row: { id: string; data: string; status: string; created_at: number; updated_at: number }): CmsDocument {
  const data = parseJson<Record<string, unknown>>(row.data, {});
  return {
    ...data, id: row.id, status: row.status,
    word_count: typeof data.content === 'string' ? data.content.trim().split(/\s+/).filter(Boolean).length : 0,
    created_at: new Date(row.created_at * 1000).toISOString(),
    updated_at: new Date(row.updated_at * 1000).toISOString(),
  };
}

adminContentRouter.get('/content/cms-documents', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const rows = await c.env.DB.prepare(`SELECT * FROM cms_documents ORDER BY updated_at DESC`).all<{ id: string; data: string; status: string; created_at: number; updated_at: number }>();
  return c.json((rows.results ?? []).map(cmsRow));
});

adminContentRouter.post('/content/cms-documents', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const body = await safeBody(c);
  const id = crypto.randomUUID();
  await c.env.DB.prepare(`INSERT INTO cms_documents (id,data,status,created_at,updated_at) VALUES (?,?,?,?,?)`)
    .bind(id, JSON.stringify(body), typeof body.status === 'string' ? body.status : 'draft', now(), now()).run();
  return c.json(cmsRow({ id, data: JSON.stringify(body), status: typeof body.status === 'string' ? body.status : 'draft', created_at: now(), updated_at: now() }), 201);
});

adminContentRouter.patch('/content/cms-documents/:docId', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const existing = await c.env.DB.prepare(`SELECT * FROM cms_documents WHERE id = ?`).bind(c.req.param('docId')).first<{ id: string; data: string; status: string; created_at: number; updated_at: number }>();
  if (!existing) return c.json({ detail: 'Document not found' }, 404);
  const body = await safeBody(c);
  const data = { ...parseJson<Record<string, unknown>>(existing.data, {}), ...body };
  const status = typeof body.status === 'string' ? body.status : existing.status;
  const updated = now();
  await c.env.DB.prepare(`UPDATE cms_documents SET data=?, status=?, updated_at=? WHERE id=?`).bind(JSON.stringify(data), status, updated, existing.id).run();
  return c.json(cmsRow({ ...existing, data: JSON.stringify(data), status, updated_at: updated }));
});

adminContentRouter.delete('/content/cms-documents/:docId', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const deleted = await c.env.DB.prepare(`DELETE FROM cms_documents WHERE id = ?`).bind(c.req.param('docId')).run();
  if ((deleted.meta.changes ?? 0) !== 1) return c.json({ detail: 'Document not found' }, 404);
  return c.json({ status: 'deleted' });
});

adminContentRouter.post('/content/cms-documents/:docId/publish', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const existing = await c.env.DB.prepare(`SELECT status FROM cms_documents WHERE id = ?`).bind(c.req.param('docId')).first<{ status: string }>();
  if (!existing) return c.json({ detail: 'Document not found' }, 404);
  const status = existing.status === 'published' ? 'draft' : 'published';
  await c.env.DB.prepare(`UPDATE cms_documents SET status=?, updated_at=? WHERE id=?`).bind(status, now(), c.req.param('docId')).run();
  return c.json({ status });
});

adminContentRouter.post('/content/cms-documents/:docId/revisions', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const existing = await c.env.DB.prepare(`SELECT data FROM cms_documents WHERE id = ?`).bind(c.req.param('docId')).first<{ data: string }>();
  if (!existing) return c.json({ detail: 'Document not found' }, 404);
  const at = now();
  await c.env.DB.prepare(`INSERT INTO cms_document_revisions (id,document_id,data,created_at) VALUES (?,?,?,?)`)
    .bind(crypto.randomUUID(), c.req.param('docId'), existing.data, at).run();
  return c.json({ status: 'ok', revision_saved_at: new Date(at * 1000).toISOString() });
});
adminContentRouter.post('/content/cms-documents/:docId/revision', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const existing = await c.env.DB.prepare(`SELECT data FROM cms_documents WHERE id = ?`).bind(c.req.param('docId')).first<{ data: string }>();
  if (!existing) return c.json({ detail: 'Document not found' }, 404);
  const at = now();
  await c.env.DB.prepare(`INSERT INTO cms_document_revisions (id,document_id,data,created_at) VALUES (?,?,?,?)`)
    .bind(crypto.randomUUID(), c.req.param('docId'), existing.data, at).run();
  return c.json({ status: 'ok', revision_saved_at: new Date(at * 1000).toISOString() });
});
adminContentRouter.post('/content/cms-documents/:docId/link-syllabus', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const existing = await c.env.DB.prepare(`SELECT * FROM cms_documents WHERE id = ?`).bind(c.req.param('docId')).first<{ id: string; data: string; status: string; created_at: number; updated_at: number }>();
  if (!existing) return c.json({ detail: 'Document not found' }, 404);
  const body = await safeBody(c);
  const data = { ...parseJson<Record<string, unknown>>(existing.data, {}), linked_scope: body.linked_scope ?? body.scope ?? null };
  await c.env.DB.prepare(`UPDATE cms_documents SET data=?, updated_at=? WHERE id=?`).bind(JSON.stringify(data), now(), existing.id).run();
  return c.json(cmsRow({ ...existing, data: JSON.stringify(data), updated_at: now() }));
});

adminContentRouter.post('/content/auto-heal', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return c.json({ status: 'ok', message: 'Dynamic Worker delivery has no static content artifacts to heal.' });
});
adminContentRouter.post('/content/extract-pdf-text', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const form = await c.req.formData().catch(() => null);
  const file = form?.get('file') as unknown;
  if (!file || typeof file !== 'object' || !('text' in file)) return c.json({ detail: 'file is required' }, 422);
  const upload = file as File;
  const ext = upload.name.split('.').pop()?.toLowerCase();
  if (ext !== 'txt' && ext !== 'md') return c.json({ detail: 'Native PDF text extraction is unavailable; upload text or Markdown instead.' }, 422);
  const text = await upload.text();
  return c.json({ text, chars: text.length });
});
adminContentRouter.post('/content/subject/:subjectId/format-notes', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const result = await c.env.DB.prepare(`
    UPDATE chapters SET notes_en = TRIM(notes_en), notes_as = TRIM(notes_as), updated_at = ?
    WHERE subject_id = ?
  `).bind(now(), c.req.param('subjectId')).run();
  return c.json({ chapters_formatted: result.meta.changes ?? 0, message: 'Notes formatting complete' });
});
adminContentRouter.get('/content/version-history/:chapterId', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const rows = await c.env.DB.prepare(`SELECT * FROM content_audit_log WHERE target_id = ? ORDER BY created_at DESC LIMIT 50`)
    .bind(c.req.param('chapterId')).all().catch(() => ({ results: [] }));
  return c.json(rows.results ?? []);
});

for (const resource of [
  { path: 'boards', table: 'boards' },
  { path: 'classes', table: 'classes' },
  { path: 'streams', table: 'streams' },
] as const) {
  adminContentRouter.patch(`/content/${resource.path}/:id`, async c => {
    const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
    const body = await safeBody(c);
    const name = typeof body.name === 'string' ? body.name.trim() : null;
    const status = typeof body.status === 'string' ? body.status : null;
    if (name && status) {
      await c.env.DB.prepare(`UPDATE ${resource.table} SET name = ?, slug = ?, status = ? WHERE id = ?`).bind(name, slugify(name), status, c.req.param('id')).run();
    } else if (name) {
      await c.env.DB.prepare(`UPDATE ${resource.table} SET name = ?, slug = ? WHERE id = ?`).bind(name, slugify(name), c.req.param('id')).run();
    } else if (status) {
      await c.env.DB.prepare(`UPDATE ${resource.table} SET status = ? WHERE id = ?`).bind(status, c.req.param('id')).run();
    }
    return c.json({ id: c.req.param('id'), status: status ?? 'published' });
  });
  adminContentRouter.delete(`/content/${resource.path}/:id`, async c => {
    const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
    const result = await c.env.DB.prepare(`DELETE FROM ${resource.table} WHERE id = ?`).bind(c.req.param('id')).run();
    if ((result.meta.changes ?? 0) !== 1) return c.json({ detail: 'Not found' }, 404);
    return c.json({ status: 'deleted' });
  });
}

adminContentRouter.post('/content/bulk-status', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const body = await safeBody(c);
  const ids = Array.isArray(body.ids) ? body.ids.filter((id): id is string => typeof id === 'string') : [];
  const status = typeof body.status === 'string' ? body.status : 'draft';
  const table = body.scope === 'subjects' ? 'subjects' : 'chapters';
  if (!ids.length) return c.json({ modified: 0 });
  const marks = ids.map(() => '?').join(',');
  const result = await c.env.DB.prepare(
    `UPDATE ${table} SET ${table === 'subjects' ? 'is_published' : 'status'} = ?, updated_at = ? WHERE id IN (${marks})`,
  ).bind(table === 'subjects' ? (status === 'published' ? 1 : 0) : status, now(), ...ids).run();
  return c.json({ modified: result.meta.changes ?? 0 });
});

adminContentRouter.post('/content/subjects/:subjectId/bulk-publish', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const body = await safeBody(c);
  const requested = Array.isArray(body.chapter_ids) ? body.chapter_ids.filter((id): id is string => typeof id === 'string') : [];
  const rows = requested.length
    ? requested.map(id => ({ id }))
    : await createDb(c.env.DB).select({ id: chapters.id }).from(chapters).where(eq(chapters.subjectId, c.req.param('subjectId')));
  const jobIds: string[] = [];
  for (const row of rows) {
    const id = crypto.randomUUID();
    const result = await c.env.DB.prepare(`
      INSERT INTO publish_jobs (id, chapter_id, status, progress, created_at, updated_at)
      SELECT ?, ?, 'pending', '[]', ?, ? WHERE NOT EXISTS
      (SELECT 1 FROM publish_jobs WHERE chapter_id = ? AND status IN ('pending','running','partial'))
    `).bind(id, row.id, now(), now(), row.id).run();
    if ((result.meta.changes ?? 0) === 1) {
      jobIds.push(id);
      c.executionCtx.waitUntil(runPublish(c.env, id, row.id));
    }
  }
  return c.json({ queued: jobIds.length, job_ids: jobIds });
});

adminContentRouter.get('/content/publish-jobs/:jobId', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const job = await createDb(c.env.DB).select().from(publishJobs).where(eq(publishJobs.id, c.req.param('jobId'))).get();
  if (!job) return c.json({ detail: 'Job not found' }, 404);
  const chapter = await createDb(c.env.DB).select({ title: chapters.title }).from(chapters).where(eq(chapters.id, job.chapterId)).get();
  return c.json({
    job_id: job.id, chapter_id: job.chapterId, chapter_title: chapter?.title ?? null,
    status: job.status, error: job.errorLog ?? null, steps: parseJson<JobStep[]>(job.progress, []),
    created_at: job.createdAt ? new Date(job.createdAt * 1000).toISOString() : null,
    started_at: null, finished_at: job.completedAt ? new Date(job.completedAt * 1000).toISOString() : null,
  });
});

adminContentRouter.post('/content/publish-jobs/:jobId/retry', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const db = createDb(c.env.DB);
  const job = await db.select().from(publishJobs).where(eq(publishJobs.id, c.req.param('jobId'))).get();
  if (!job) return c.json({ detail: 'Job not found' }, 404);
  if (!['failed', 'partial'].includes(job.status ?? '')) {
    return c.json({ detail: `Job status is '${job.status}'; only 'failed' or 'partial' jobs can be retried` }, 400);
  }
  const claimed = await c.env.DB.prepare(`
    UPDATE publish_jobs
    SET status = 'pending', progress = '[]', error_log = NULL, completed_at = NULL, updated_at = ?
    WHERE id = ? AND status IN ('failed', 'partial')
  `).bind(now(), job.id).run();
  if ((claimed.meta.changes ?? 0) !== 1) {
    return c.json({ detail: 'This publish job was already retried by another request.' }, 409);
  }
  c.executionCtx.waitUntil(runPublish(c.env, job.id, job.chapterId));
  return c.json({ job_id: job.id, status: 'queued' });
});

adminContentRouter.post('/content/seed-notes', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return launchSeed(c, 'en');
});
adminContentRouter.post('/content/seed-assamese', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return launchSeed(c, 'as');
});

adminContentRouter.get('/content/seed-notes/history', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const limit = Math.max(1, Math.min(Number(c.req.query('limit') ?? 20), 100));
  const rows = await createDb(c.env.DB).select().from(seedRuns).orderBy(desc(seedRuns.startedAt)).limit(limit);
  return c.json(rows.map(run => ({
    id: run.id, status: run.status, run_type: run.medium === 'as' ? 'assamese' : 'notes',
    total: run.totalChapters, completed: run.processed, failed: run.failed, skipped: 0,
    errors: parseJson<SeedLog[]>(run.log, []).filter(entry => entry.status === 'failed'),
    failed_ids: parseJson<SeedLog[]>(run.log, []).filter(entry => entry.status === 'failed').map(entry => entry.chapter_id),
    started_at: run.startedAt ? new Date(run.startedAt * 1000).toISOString() : null,
    finished_at: run.completedAt ? new Date(run.completedAt * 1000).toISOString() : null,
  })));
});

adminContentRouter.get('/content/seed-notes/stuck', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const rows = await createDb(c.env.DB).select().from(seedRuns).orderBy(desc(seedRuns.startedAt)).limit(20);
  const stuck = rows.flatMap(run => parseJson<SeedLog[]>(run.log, []).filter(entry => entry.status === 'failed')
    .map(entry => ({ ...entry, key: `${run.id}:${entry.chapter_id}`, medium: run.medium })));
  return c.json({ stuck, count: stuck.length });
});

adminContentRouter.post('/content/seed-notes/stuck/retry', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const body = await safeBody(c);
  const stuck = Array.isArray(body.stuck) ? body.stuck : [];
  const chapterIds = stuck.map(entry => entry && typeof entry === 'object' ? (entry as Record<string, unknown>).chapter_id : null)
    .filter((id): id is string => typeof id === 'string').slice(0, 200);
  if (!chapterIds.length) return c.json({ detail: 'No stuck chapters provided.' }, 400);
  const response = await launchSeed(new Proxy(c, {
    get(target, key) {
      if (key === 'req') return new Proxy(target.req, {
        get(request, reqKey) {
          if (reqKey === 'json') return async () => ({ chapter_ids: chapterIds, force: true });
          return Reflect.get(request, reqKey);
        },
      });
      return Reflect.get(target, key);
    },
  }) as Context<{ Bindings: Env }>, 'en');
  return response;
});

adminContentRouter.post('/content/seed-notes/stuck/clear', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return c.json({ cleared: 0, message: 'Seed run history is immutable; resolved failures are excluded automatically.' });
});

function cronAuthorized(c: Context<{ Bindings: Env }>): boolean {
  const supplied = extractBearer(c.req.header('Authorization') ?? null);
  return Boolean(supplied && c.env.TRANSLATE_CRON_SECRET && supplied === c.env.TRANSLATE_CRON_SECRET);
}
let bulkReindexStatus: { running: boolean; total: number; processed: number; skipped: number; errors: string[] } = {
  running: false, total: 0, processed: 0, skipped: 0, errors: [],
};

adminContentRouter.post('/cron/bulk-mirror-rag', async c => {
  if (!(await cronOrAdminAuthorized(c))) return c.json({ detail: 'Admin session or valid TRANSLATE_CRON_SECRET required' }, 401);
  const limit = Math.max(1, Math.min(Number(c.req.query('limit') ?? 100), 200));
  const subjectId = c.req.query('subject_id');
  const force = c.req.query('force') === 'true';
  const rows = await createDb(c.env.DB).select({
    id: chapters.id, notesEn: chapters.notesEn, ragSectionsEn: chapters.ragSectionsEn,
  }).from(chapters).where(subjectId ? eq(chapters.subjectId, subjectId) : undefined).limit(limit);
  let processed = 0; let skipped = 0; const noHeadings: string[] = [];
  for (const row of rows) {
    if (!force && row.ragSectionsEn && row.ragSectionsEn !== '[]') { skipped++; continue; }
    const parts = row.notesEn?.split(/^##\s+/m).map(item => item.trim()).filter(Boolean) ?? [];
    if (!parts.length) { noHeadings.push(row.id); continue; }
    await createDb(c.env.DB).update(chapters).set({
      ragSectionsEn: JSON.stringify(parts.map((content, index) => ({ id: `${row.id}-${index}`, content }))),
      ragUpdatedAt: now(), updatedAt: now(),
    }).where(eq(chapters.id, row.id));
    processed++;
  }
  return c.json({ processed, skipped, no_headings: noHeadings.length, no_headings_list: noHeadings.map(chapter_id => ({ chapter_id })), errors: [] });
});

adminContentRouter.post('/cron/bulk-reindex', async c => {
  if (!(await cronOrAdminAuthorized(c))) return c.json({ detail: 'Admin session or valid TRANSLATE_CRON_SECRET required' }, 401);
  if (bulkReindexStatus.running) return c.json({ detail: 'A bulk-reindex job is already running.' }, 409);
  const limit = Math.max(1, Math.min(Number(c.req.query('limit') ?? 50), 100));
  const subjectId = c.req.query('subject_id');
  const rows = await createDb(c.env.DB).select({
    id: chapters.id, subjectId: chapters.subjectId, notesEn: chapters.notesEn, notesAs: chapters.notesAs,
  }).from(chapters).where(subjectId ? eq(chapters.subjectId, subjectId) : undefined).limit(limit);
  bulkReindexStatus = { running: true, total: rows.length, processed: 0, skipped: 0, errors: [] };
  c.executionCtx.waitUntil((async () => {
    for (const row of rows) {
      try {
        const result = await runNativeReindex(c.env, row);
        if (result.status === 'skipped') bulkReindexStatus.skipped++;
        else bulkReindexStatus.processed++;
      } catch (error) { bulkReindexStatus.errors.push(error instanceof Error ? error.message : String(error)); }
    }
    bulkReindexStatus.running = false;
  })());
  return c.json({ job: rows.length ? 'started' : 'nothing_to_do', total_queued: rows.length });
});
adminContentRouter.get('/cron/bulk-reindex/status', async c => {
  if (!(await cronOrAdminAuthorized(c))) return c.json({ detail: 'Admin session or valid TRANSLATE_CRON_SECRET required' }, 401);
  return c.json(bulkReindexStatus);
});
// Existing Content Editor alias; retain it without reintroducing a Cloud Run route.
adminContentRouter.post('/rag/bulk-reindex', async c => {
  if (!(await cronOrAdminAuthorized(c))) return c.json({ detail: 'Admin session or valid TRANSLATE_CRON_SECRET required' }, 401);
  return c.redirect(new URL(`/api/v1/admin/cron/bulk-reindex${new URL(c.req.url).search}`, c.req.url).toString(), 307);
});

// Scheduled callers use a dedicated secret; they never inherit the browser
// session cookie and the edge proxy no longer substitutes Cloud Run OIDC.
adminContentRouter.post('/cron/seed-notes', async c => {
  if (!cronAuthorized(c)) return c.json({ detail: 'Valid TRANSLATE_CRON_SECRET required' }, 401);
  return launchSeed(c, 'en');
});
adminContentRouter.post('/cron/seed-assamese', async c => {
  if (!cronAuthorized(c)) return c.json({ detail: 'Valid TRANSLATE_CRON_SECRET required' }, 401);
  return launchSeed(c, 'as');
});
// Legacy scheduled translation uses this path. Translation is now the
// Assamese seed job and therefore returns its queued run envelope immediately.
adminContentRouter.post('/cron/translate', async c => {
  if (!cronAuthorized(c)) return c.json({ detail: 'Valid TRANSLATE_CRON_SECRET required' }, 401);
  return launchSeed(c, 'as');
});
adminContentRouter.get('/cron/seed-notes/status', async c => {
  if (!cronAuthorized(c)) return c.json({ detail: 'Valid TRANSLATE_CRON_SECRET required' }, 401);
  const run = await createDb(c.env.DB).select().from(seedRuns).where(eq(seedRuns.medium, 'en'))
    .orderBy(desc(seedRuns.startedAt)).get();
  if (!run) return c.json({ running: false, message: 'No seed-notes job has been started yet.' });
  return c.json({ running: run.status === 'running' || run.status === 'queued', run_id: run.id, total: run.totalChapters,
    completed: run.processed, failed: run.failed, status: run.status });
});
adminContentRouter.get('/cron/seed-assamese/status', async c => {
  if (!cronAuthorized(c)) return c.json({ detail: 'Valid TRANSLATE_CRON_SECRET required' }, 401);
  const run = await createDb(c.env.DB).select().from(seedRuns).where(eq(seedRuns.medium, 'as'))
    .orderBy(desc(seedRuns.startedAt)).get();
  if (!run) return c.json({ running: false, message: 'No seed-assamese job has been started yet.' });
  return c.json({ running: run.status === 'running' || run.status === 'queued', run_id: run.id, total: run.totalChapters,
    completed: run.processed, failed: run.failed, status: run.status });
});