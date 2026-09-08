/**
 * Staff content routes — D1-backed, requires role=staff|admin JWT.
 *
 * Mirrors the MongoDB-backed /api/v1/staff/* routes in the Cloud Run backend
 * so the admin panel works identically against the D1 Worker after cutover.
 *
 * Routes:
 *   GET  /staff/content/boards
 *   GET  /staff/content/classes
 *   GET  /staff/content/streams
 *   GET  /staff/content/subjects          — all subjects + resolved board/class context
 *   POST /staff/content/subjects          — create
 *   PATCH /staff/content/subjects/:id     — update
 *   DELETE /staff/content/subjects/:id    — delete
 *
 *   GET  /staff/content/chapters/:subjectId   — list chapters for a subject
 *   POST /staff/content/chapters              — create chapter
 *   GET  /staff/content/chapter/:chapterId    — full chapter detail for editing
 *   PATCH /staff/content/chapter/:chapterId   — update chapter fields
 *   DELETE /staff/content/chapter/:chapterId  — delete chapter
 *   POST /staff/content/chapters/:id/reindex  — embed + upsert to Vectorize, update D1 indexed_at
 *
 *   GET  /staff/content/subject/:subjectId/pyq-papers
 *   POST /staff/content/subject/:subjectId/pyq-papers
 *   PATCH /staff/content/subject/:subjectId/pyq-papers/:paperId
 *   DELETE /staff/content/subject/:subjectId/pyq-papers/:paperId
 */

import { Hono, type Context } from 'hono';
import { eq, and } from 'drizzle-orm';
import { createDb } from '../db/client';
import { boards, classes, streams, subjects, chapters, chunks, contentAuditLog, users } from '../db/schema';
import { extractBearer, hashPassword, isSessionValid, verifyAdminToken, verifyPassword, verifyToken } from '../middleware/auth';
import { purgeChapterRag, purgeRagScope, reindexChapterRag } from '../services/rag-indexing';
import { serializePublicChapterList } from '../services/public-chapter-list';
import type { Env, JwtPayload } from '../types';
import type { JWTPayload } from 'jose';

export const staffRouter = new Hono<{ Bindings: Env }>();

// ── Auth guard ─────────────────────────────────────────────────────────────────
//
// Enforces three conditions:
//   1. Bearer token present and valid HS256 signature
//   2. payload.type === 'access'  ← refresh tokens are explicitly rejected
//   3. payload.role is 'staff' or 'admin'
//
// Returns the verified payload on success, or writes the appropriate error
// response to `c.res` and returns null so callers can `if (!auth) return c.res`.

type AuthPayload = JWTPayload & JwtPayload & { jti?: string };

async function guard(c: Context<{ Bindings: Env }>): Promise<AuthPayload | null> {
  const cookie = c.req.header('Cookie') ?? '';
  const cookieToken = cookie.split(';').map(part => part.trim())
    .find(part => part.startsWith('syrabit_admin_session='))
    ?.slice('syrabit_admin_session='.length);
  const bearer = extractBearer(c.req.header('Authorization') ?? null);

  // The existing staff API accepts user access tokens. The legacy admin panel
  // deliberately uses an HttpOnly ADMIN_JWT_SECRET session cookie instead.
  // Accept that established contract here so mounting this router under
  // /api/v1/admin does not require a coordinated frontend auth migration.
  for (const adminToken of [cookieToken, bearer].filter((token): token is string => Boolean(token))) {
    const admin = await verifyAdminToken(adminToken, c.env.ADMIN_JWT_SECRET);
    if (admin) {
      if (!(await isSessionValid(c.env.DB, admin.sub, admin.iat))) {
        const response = c.json({ detail: 'Session expired after password change. Sign in again.' }, 401);
        if (cookieToken) response.headers.set('Set-Cookie', 'syrabit_admin_session=; Path=/api/; Max-Age=0; HttpOnly; SameSite=Lax');
        c.res = response;
        return null;
      }
      return admin as unknown as AuthPayload;
    }
  }

  if (!bearer) {
    c.res = c.json({ detail: 'Authentication required' }, 401);
    return null;
  }
  const payload = await verifyToken(bearer, c.env.JWT_SECRET);
  if (!payload) {
    c.res = c.json({ detail: 'Invalid or expired token' }, 401);
    return null;
  }
  // Reject refresh tokens — they are persisted in localStorage and must not
  // grant write access even when signed with the same secret.
  if ((payload as { type?: string }).type !== 'access') {
    c.res = c.json({ detail: 'Access token required' }, 401);
    return null;
  }
  if (!['staff', 'admin'].includes(payload.role ?? '')) {
    c.res = c.json({ detail: 'Staff access required' }, 403);
    return null;
  }
  if (!(await isSessionValid(c.env.DB, payload.sub ?? '', payload.iat))) {
    c.res = c.json({ detail: 'Session expired after password change. Sign in again.' }, 401);
    return null;
  }
  return payload;
}

const ANALYTICS_MAX_DAYS = 90;

function analyticsDays(value: string | undefined): number | null {
  if (value === undefined) return 30;
  if (!/^\d{1,3}$/.test(value)) return null;
  const days = Number(value);
  return days >= 1 && days <= ANALYTICS_MAX_DAYS ? days : null;
}

/**
 * Privacy-safe, staff-only totals. These queries intentionally return neither
 * user/session identifiers nor chat/content text. They are mounted under both
 * /staff and /admin by routes/index.ts.
 */
staffRouter.get('/analytics/aggregates/:metric', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'analytics:read'); if (denied) return denied;
  const days = analyticsDays(c.req.query('days'));
  if (days === null) {
    return c.json({ detail: `days must be an integer between 1 and ${ANALYTICS_MAX_DAYS}` }, 422);
  }
  const metric = c.req.param('metric');
  const since = Math.floor(Date.now() / 1000) - (days * 24 * 60 * 60);
  try {
    let data: Record<string, number | null> | null = null;
    if (metric === 'users') {
      data = await c.env.DB.prepare(`
        SELECT COUNT(*) AS registered,
          COALESCE(SUM(CASE WHEN onboarding_done = 1 THEN 1 ELSE 0 END), 0) AS onboarded
        FROM users WHERE created_at >= ?
      `).bind(since).first<Record<string, number | null>>();
    } else if (metric === 'sessions') {
      data = await c.env.DB.prepare(`
        SELECT COUNT(*) AS page_views,
          COUNT(DISTINCT json_extract(payload, '$.session_id')) AS sessions
        FROM analytics_events
        WHERE event_subtype = 'page_view'
          AND classification = 'optional_analytics'
          AND created_at >= ?
      `).bind(since).first<Record<string, number | null>>();
    } else if (metric === 'chat') {
      data = await c.env.DB.prepare(`
        SELECT COUNT(*) AS messages,
          COUNT(DISTINCT session_id) AS sessions,
          COALESCE(SUM(CASE WHEN role = 'user' THEN 1 ELSE 0 END), 0) AS user_messages
        FROM chats WHERE created_at >= ?
      `).bind(since).first<Record<string, number |null>>();
    } else if (metric === 'content') {
      data = await c.env.DB.prepare(`
        SELECT COUNT(*) AS chapters_created,
          COALESCE(SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END), 0) AS published_chapters
        FROM chapters WHERE created_at >= ?
      `).bind(since).first<Record<string, number | null>>();
    } else {
      return c.json({ detail: 'Unknown aggregate metric' }, 404);
    }
    return c.json({ metric, days, since, data: data ?? {} });
  } catch {
    return c.json({ detail: 'Analytics aggregate unavailable' }, 503);
  }
});

type CommandCenterRow = Record<string, number | string | null>;

function numericSummary(row: CommandCenterRow | null | undefined): Record<string, number> {
  if (!row) return {};
  return Object.fromEntries(
    Object.entries(row).map(([key, value]) => [key, Number(value ?? 0)]),
  );
}

/**
 * One request powers the staff command center. The response deliberately
 * contains aggregate counts only: no identifiers, route payloads, chat text,
 * audit diffs, or other drill-down data leave this endpoint.
 */
staffRouter.get('/analytics/command-center', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'analytics:read'); if (denied) return denied;
  const days = analyticsDays(c.req.query('days'));
  if (days === null) {
    return c.json({ detail: `days must be an integer between 1 and ${ANALYTICS_MAX_DAYS}` }, 422);
  }

  const now = Math.floor(Date.now() / 1000);
  const since = now - (days * 24 * 60 * 60);
  try {
    const results = await c.env.DB.batch<CommandCenterRow>([
      c.env.DB.prepare(`
        SELECT
          COUNT(*) AS registered,
          COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) AS new_users,
          COALESCE(SUM(CASE WHEN onboarding_done = 1 THEN 1 ELSE 0 END), 0) AS onboarded,
          COALESCE(SUM(CASE WHEN deleted_at IS NULL THEN 1 ELSE 0 END), 0) AS active_accounts
        FROM users
      `).bind(since),
      c.env.DB.prepare(`
        SELECT
          COUNT(*) AS total,
          COALESCE(SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END), 0) AS published,
          COALESCE(SUM(CASE WHEN status != 'published' THEN 1 ELSE 0 END), 0) AS unpublished,
          COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) AS created_in_period
        FROM chapters
      `).bind(since),
      c.env.DB.prepare(`
        SELECT
          COALESCE(SUM(CASE WHEN rag_indexed_at IS NOT NULL
            AND (rag_updated_at IS NULL OR rag_indexed_at >= rag_updated_at)
            THEN 1 ELSE 0 END), 0) AS indexed,
          COALESCE(SUM(CASE WHEN rag_indexed_at IS NOT NULL
            AND rag_updated_at IS NOT NULL AND rag_indexed_at < rag_updated_at
            THEN 1 ELSE 0 END), 0) AS stale,
          COALESCE(SUM(CASE WHEN rag_indexed_at IS NULL THEN 1 ELSE 0 END), 0) AS unindexed,
          (SELECT COUNT(*) FROM chunks) AS chunks
        FROM chapters
      `),
      c.env.DB.prepare(`
        SELECT
          COALESCE(SUM(CASE WHEN event_subtype = 'chat_completion' THEN 1 ELSE 0 END), 0) AS completions,
          COALESCE(SUM(CASE WHEN event_subtype = 'chat_failure' THEN 1 ELSE 0 END), 0) AS failures,
          COALESCE(ROUND(AVG(CASE WHEN event_subtype = 'chat_completion'
            THEN CAST(json_extract(payload, '$.latency_ms') AS REAL) END)), 0) AS average_latency_ms,
          COALESCE(SUM(CASE WHEN event_subtype = 'chat_completion'
            AND CAST(json_extract(payload, '$.source_coverage') AS INTEGER) > 0
            THEN 1 ELSE 0 END), 0) AS sourced_completions
        FROM analytics_events
        WHERE classification = 'essential_operational' AND created_at >= ?
      `).bind(since),
      c.env.DB.prepare(`
        SELECT
          COALESCE(SUM(CASE WHEN event_subtype = 'ad_slot_viewed' THEN 1 ELSE 0 END), 0) AS slot_views,
          COUNT(DISTINCT CASE WHEN event_subtype = 'ad_slot_viewed'
            THEN json_extract(payload, '$.placement') END) AS active_placements
        FROM analytics_events
        WHERE classification = 'optional_analytics' AND created_at >= ?
      `).bind(since),
      c.env.DB.prepare(`
        SELECT
          COALESCE(SUM(CASE WHEN event_subtype = 'consent_granted' THEN 1 ELSE 0 END), 0) AS granted,
          COALESCE(SUM(CASE WHEN event_subtype = 'consent_declined' THEN 1 ELSE 0 END), 0) AS declined,
          COALESCE(ROUND(100.0 * SUM(CASE WHEN event_subtype = 'consent_granted' THEN 1 ELSE 0 END)
            / NULLIF(SUM(CASE WHEN event_subtype IN ('consent_granted', 'consent_declined') THEN 1 ELSE 0 END), 0), 1), 0)
            AS consent_rate
        FROM analytics_events
        WHERE created_at >= ?
      `).bind(since),
      c.env.DB.prepare(`
        SELECT
          COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_publish_jobs,
          COALESCE(SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END), 0) AS partial_publish_jobs,
          COALESCE(SUM(CASE WHEN status IN ('pending', 'running') THEN 1 ELSE 0 END), 0) AS active_publish_jobs,
          (SELECT COUNT(*) FROM dead_letters WHERE created_at >= ?) AS dead_letters
        FROM publish_jobs
        WHERE created_at >= ?
      `).bind(since, since),
      c.env.DB.prepare(`
        SELECT
          COUNT(*) AS actions,
          COALESCE(SUM(CASE WHEN action LIKE '%publish%' THEN 1 ELSE 0 END), 0) AS publish_actions,
          COALESCE(SUM(CASE WHEN action LIKE '%reindex%' THEN 1 ELSE 0 END), 0) AS reindex_actions
        FROM content_audit_log
        WHERE created_at >= ?
      `).bind(since),
    ]);

    const rows = results.map(result => result.results[0] ?? null);
    return c.json({
      generated_at: new Date(now * 1000).toISOString(),
      days,
      users: numericSummary(rows[0]),
      content: numericSummary(rows[1]),
      rag: numericSummary(rows[2]),
      chat: numericSummary(rows[3]),
      ads: numericSummary(rows[4]),
      consent: numericSummary(rows[5]),
      incidents: numericSummary(rows[6]),
      audit: numericSummary(rows[7]),
    });
  } catch (error) {
    console.error('[staff analytics] command center unavailable', error);
    return c.json({ detail: 'Command-center analytics unavailable' }, 503);
  }
});

// ── Staff account actions ──────────────────────────────────────────────────────

staffRouter.post('/auth/change-password', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const body = await safeBody(c);
  const currentPassword = String(body['current_password'] ?? '');
  const newPassword = String(body['new_password'] ?? '');

  if (newPassword.length < 8) {
    return c.json({ detail: 'Password must be at least 8 characters' }, 400);
  }
  if (!currentPassword) {
    return c.json({ detail: 'Current password is required' }, 400);
  }

  const db = createDb(c.env.DB);
  const user = await db.select({
    id: users.id,
    hashedPassword: users.hashedPassword,
  }).from(users).where(eq(users.id, auth.sub ?? '')).get();

  if (!user?.hashedPassword) {
    return c.json({ detail: 'No password is set for this account' }, 400);
  }
  if (!(await verifyPassword(currentPassword, user.hashedPassword))) {
    return c.json({ detail: 'Current password is incorrect' }, 400);
  }

  // Advance beyond both the current clock and any earlier cutoff. This keeps
  // every previously issued token stale even when reset/change flows happen
  // within the same JWT timestamp second.
  const validAfter = Math.floor(Date.now() / 1000) + 1;
  await c.env.DB.prepare(`
    UPDATE users
    SET hashed_password = ?,
        session_valid_after = MAX(session_valid_after + 1, ?)
    WHERE id = ?
  `).bind(await hashPassword(newPassword), validAfter, user.id).run();
  await auditLog(c.env, auth.sub ?? '', 'change_password', 'user', user.id);
  const response = c.json({ ok: true, message: 'Password updated' });
  if ((c.req.header('Cookie') ?? '').includes('syrabit_admin_session=')) {
    response.headers.set('Set-Cookie', 'syrabit_admin_session=; Path=/api/; Max-Age=0; HttpOnly; SameSite=Lax');
  }
  return response;
});

// ── Helpers ────────────────────────────────────────────────────────────────────

function safeParse<T = unknown>(json: string | null | undefined): T | null {
  if (!json) return null;
  try { return JSON.parse(json) as T; } catch { return null; }
}

function makeSlug(name: string): string {
  return name.toLowerCase()
    .replace(/[^\w\s-]/g, '')
    .replace(/[\s_-]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

function nowTs(): number { return Math.floor(Date.now() / 1000); }
async function previewSignature(secret: string, payload: string): Promise<string> {
  const key = await crypto.subtle.importKey('raw', new TextEncoder().encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  const bytes = new Uint8Array(await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(payload)));
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
const CHAPTER_STATUSES = new Set(['draft', 'published', 'unpublished', 'archived']);
const CHAPTER_TYPES = new Set(['standard', 'notes', 'qa', 'question_paper', 'formula', 'summary', 'solution', 'reference']);
function validChapterNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0;
}

async function safeBody(c: Context<{ Bindings: Env }>): Promise<Record<string, unknown>> {
  try { return await c.req.json<Record<string, unknown>>(); } catch { return {}; }
}

type StaffCapability = 'analytics:read' | 'history:read' | 'content:edit' | 'content:publish' | 'rag:reindex' | 'content:delete';
/** NULL capabilities is the pre-capability, fully trusted staff contract. */
async function capabilityDenied(c: Context<{ Bindings: Env }>, auth: AuthPayload, capability: StaffCapability): Promise<Response | null> {
  if (auth.role === 'admin') return null;
  const row = await c.env.DB.prepare('SELECT capabilities FROM users WHERE id = ?').bind(auth.sub ?? '').first<{ capabilities: string | null }>();
  if (!row || row.capabilities === null) return null;
  const granted = safeParse<string[]>(row.capabilities) ?? [];
  if (granted.includes(capability)) return null;
  return c.json({ detail: `Missing staff capability: ${capability}`, capability }, 403);
}

/** Write the chapter list for a subject to CONTENT_KV for CDN prewarm. */
async function kvPrewarm(env: Env, subjectId: string): Promise<void> {
  try {
    const db = createDb(env.DB);
    const chaps = await db.select({
      id: chapters.id, title: chapters.title, titleAs: chapters.titleAs, slug: chapters.slug, slugAs: chapters.slugAs,
      chapterNumber: chapters.chapterNumber, status: chapters.status,
      notesEn: chapters.notesEn, notesAs: chapters.notesAs, qaEn: chapters.qaEn,
      publishedTopics: chapters.publishedTopics, pyqPdfUrl: chapters.pyqPdfUrl, pyqPapers: chapters.pyqPapers,
    }).from(chapters).where(eq(chapters.subjectId, subjectId)).orderBy(chapters.chapterNumber);

    const payload = serializePublicChapterList(chaps);

    await env.CONTENT_KV.put(
      `subject:${subjectId}:chapters`,
      JSON.stringify(payload),
      { expirationTtl: 86400 * 7 },
    );
  } catch { /* prewarm is best-effort */ }
}

/** Log a content audit event to D1 (fire-and-forget). */
function auditLog(
  env: Env,
  userId: string,
  action: string,
  targetType: string,
  targetId: string,
  diff?: unknown,
): Promise<void> {
  const db  = createDb(env.DB);
  const now = nowTs();
  return db.insert(contentAuditLog).values({
    id: crypto.randomUUID(),
    userId, action, targetType, targetId,
    diff: diff ? JSON.stringify(diff) : null,
    expiresAt: now + 86400 * 180,
    createdAt: now,
  }).run().then(() => undefined);
}

/** R2 has no transaction with D1. Complete object removal before metadata removal. */
async function deleteR2Prefix(env: Env, prefix: string): Promise<number> {
  let cursor: string | undefined;
  let removed = 0;
  do {
    const page = await env.R2_BUCKET.list(cursor ? { prefix, cursor } : { prefix });
    if (page.objects.length) {
      await env.R2_BUCKET.delete(page.objects.map(object => object.key));
      removed += page.objects.length;
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  return removed;
}

// ── Text chunking for Vectorize ────────────────────────────────────────────────

function chunkText(text: string, maxWords = 400, overlapWords = 50): string[] {
  const words = text.trim().split(/\s+/);
  if (words.length <= maxWords) return words.length > 0 ? [text.trim()] : [];
  const chunks: string[] = [];
  let start = 0;
  while (start < words.length) {
    chunks.push(words.slice(start, start + maxWords).join(' '));
    start += maxWords - overlapWords;
  }
  return chunks;
}

/** Workers AI embedding + Vectorize upsert for a single language/scope.
 *  Also writes a row per chunk to the D1 chunks table so callers can later
 *  query and delete stale vectors by chapter/scope without scanning Vectorize.
 */
async function ingestToVectorize(
  env: Env,
  chapterId: string,
  subjectId: string,
  text: string,
  medium: 'english' | 'assamese',
  sourceType: 'notes' | 'important_questions' | 'pyq',
): Promise<number> {
  const rawChunks = chunkText(text);
  if (rawChunks.length === 0) return 0;

  // Must match the model used by the chat retrieval pipeline in chat.ts.
  // bge-m3 (1024-dim) returns { data: [{ values: number[], shape: number[] }] }
  type BgeM3Result = { data: Array<{ values: number[]; shape: number[] }> };
  const embResult = await (env.AI as unknown as {
    run(model: string, input: { text: string[] }): Promise<BgeM3Result>
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  }).run('@cf/baai/bge-m3' as any, { text: rawChunks });

  // Build vector records including the original chunk text for D1 storage.
  type VectorRecord = {
    id: string; values: number[];
    metadata: Record<string, string>;
    content: string;
  };
  const vectorRecords: VectorRecord[] = rawChunks
    .map((chunk, i) => {
      // bge-m3 response shape: { data: [{ values: number[], shape: number[] }] }
      const values = embResult.data[i]?.values;
      if (!values || values.length === 0) return null;
      return {
        id:     `${chapterId}_${medium}_${sourceType}_${i}`,
        values,
        metadata: {
          chapterId, subjectId, medium, sourceType,
          chunkType: 'text',
          content: chunk.slice(0, 512),
        },
        content: chunk,
      };
    })
    .filter((v): v is NonNullable<typeof v> => v !== null);

  if (vectorRecords.length === 0) return 0;

  // Upsert vectors to Vectorize (strip the D1-only content field).
  await env.VECTORIZE.upsert(
    vectorRecords.map(({ content: _c, ...v }) => v),
  );

  // Write chunk rows to D1 so stale-vector cleanup can find them by chapter+scope.
  // Awaited via Promise.all — if D1 persistence fails the mapping is missing and the
  // next reindex cannot find these vectors for deletion.  Compensate by removing the
  // vectors we just upserted so Vectorize stays consistent, then re-throw so the
  // caller surfaces an error rather than claiming successful indexing.
  const db  = createDb(env.DB);
  const now = nowTs();
  try {
    await Promise.all(
      vectorRecords.map(vr =>
        db.insert(chunks).values({
          id: crypto.randomUUID(),
          chapterId, subjectId, sourceType, medium,
          chunkType: 'text',
          content:  vr.content,
          vectorId: vr.id,
          metadata: JSON.stringify({ chapterId, subjectId, medium, sourceType }),
          createdAt: now,
        }).run(),
      ),
    );
  } catch (d1Err) {
    // D1 write failed: remove the vectors we just upserted so Vectorize does not
    // accumulate unmapped entries that can never be cleaned up on a future reindex.
    await env.VECTORIZE.deleteByIds(vectorRecords.map(v => v.id)).catch(() => { /* best-effort */ });
    throw d1Err;
  }

  return vectorRecords.length;
}

/**
 * Delete all Vectorize vectors (and their D1 chunk rows) for a given
 * chapter + sourceType before a re-index so orphaned old-chunk vectors
 * are never returned by the RAG retrieval pipeline.
 *
 * Strategy — handles both legacy and post-deployment vectors:
 *
 *   Legacy (pre-D1-mapping): vector IDs are deterministic strings of the form
 *     `${chapterId}_${medium}_${sourceType}_${chunkIndex}`.
 *     We sweep the full possible index range (0..MAX_CHUNKS-1) for every
 *     medium.  Vectorize silently ignores IDs that do not exist, so this is
 *     safe and removes tail-chunk orphans left by shorter replacements.
 *
 *   Post-deployment: D1 chunks table also records vectorIds; these overlap
 *     with the deterministic sweep and are included for completeness.
 *
 * Throws on Vectorize failure — callers must NOT proceed to upsert unless
 * this succeeds, otherwise stale tail-chunk vectors survive the reindex.
 * D1 chunk rows are deleted only after Vectorize deletion succeeds.
 */
async function deleteStaleVectors(
  env: Env,
  chapterId: string,
  sourceType: 'notes' | 'important_questions' | 'pyq',
): Promise<void> {
  const mediums: Array<'english' | 'assamese'> = ['english', 'assamese'];

  // Upper bound on chunks per medium/scope.
  // chunkText uses maxWords=400, overlapWords=50 → step=350.
  // 500 * 350 = 175,000 words — well above any real chapter.
  const MAX_CHUNKS = 500;

  // Build the deterministic ID sweep covering all possible prior chunk positions.
  const deterministicIds: string[] = [];
  for (const medium of mediums) {
    for (let i = 0; i < MAX_CHUNKS; i++) {
      deterministicIds.push(`${chapterId}_${medium}_${sourceType}_${i}`);
    }
  }

  // Also include IDs recorded in D1 (post-deployment vectors, may extend beyond
  // MAX_CHUNKS if ever raised, or have non-standard patterns).
  const db = createDb(env.DB);
  const d1Rows = await db
    .select({ vectorId: chunks.vectorId })
    .from(chunks)
    .where(and(eq(chunks.chapterId, chapterId), eq(chunks.sourceType, sourceType)));

  const d1Ids = d1Rows.map(r => r.vectorId).filter((id): id is string => Boolean(id));

  // Union of both sets — duplicates are harmless but wastes budget.
  const allIds = [...new Set([...deterministicIds, ...d1Ids])];

  // Vectorize deleteByIds silently ignores non-existent IDs.
  // Batch to 1000 per call to stay within API limits.
  const BATCH = 1000;
  for (let i = 0; i < allIds.length; i += BATCH) {
    await env.VECTORIZE.deleteByIds(allIds.slice(i, i + BATCH));
  }

  // Delete D1 chunk rows only after Vectorize deletion succeeds.
  // New rows will be inserted after the subsequent upsert.
  await db
    .delete(chunks)
    .where(and(eq(chunks.chapterId, chapterId), eq(chunks.sourceType, sourceType)));
}

// ── RAG section flatteners ─────────────────────────────────────────────────────

function flattenNotesSections(sections: Array<Record<string, string>>): string {
  return sections.map(s =>
    [s['title'] ? `## ${s['title']}` : '', s['content'] ?? ''].filter(Boolean).join('\n'),
  ).join('\n\n');
}

function flattenQaSections(sections: Array<Record<string, string>>): string {
  return sections.map(s => [
    s['section']  ? `Section: ${s['section']}`   : '',
    s['question'] ? `Q: ${s['question']}`         : '',
    s['answer']   ? `A: ${s['answer']}`           : '',
    s['solution'] ? `Solution: ${s['solution']}` : '',
  ].filter(Boolean).join('\n')).join('\n\n');
}

// ─────────────────────────────────────────────────────────────────────────────
// Boards
// ─────────────────────────────────────────────────────────────────────────────

staffRouter.get('/content/boards', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const db = createDb(c.env.DB);
  const rows = await db.select({ id: boards.id, name: boards.name, slug: boards.slug }).from(boards);
  return c.json(rows.map(b => ({ id: b.id, name: b.name, slug: b.slug, status: 'published' })));
});

// ─────────────────────────────────────────────────────────────────────────────
// Classes
// ─────────────────────────────────────────────────────────────────────────────

staffRouter.get('/content/classes', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const db = createDb(c.env.DB);
  const rows = await db.select({ id: classes.id, name: classes.name, boardId: classes.boardId }).from(classes);
  return c.json(rows.map(r => ({ id: r.id, name: r.name, board_id: r.boardId, status: 'published' })));
});

// ─────────────────────────────────────────────────────────────────────────────
// Streams
// ─────────────────────────────────────────────────────────────────────────────

staffRouter.get('/content/streams', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const db = createDb(c.env.DB);
  const [allStreams, allClasses] = await Promise.all([
    db.select({ id: streams.id, name: streams.name, classId: streams.classId }).from(streams),
    db.select({ id: classes.id, boardId: classes.boardId }).from(classes),
  ]);
  const classMap = new Map(allClasses.map(cls => [cls.id, cls]));
  return c.json(allStreams.map(s => {
    const cls = classMap.get(s.classId);
    return { id: s.id, name: s.name, status: 'published', class_id: s.classId, board_id: cls?.boardId ?? null };
  }));
});

// ─────────────────────────────────────────────────────────────────────────────
// Subjects — list (all, not just published)
// ─────────────────────────────────────────────────────────────────────────────

staffRouter.get('/content/subjects', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const db = createDb(c.env.DB);
  const [allSubjects, allStreams, allClasses] = await Promise.all([
    db.select({
      id: subjects.id, name: subjects.name, streamId: subjects.streamId,
      isPublished: subjects.isPublished, slug: subjects.slug,
      description: subjects.description, updatedAt: subjects.updatedAt,
    }).from(subjects),
    db.select({ id: streams.id, name: streams.name, classId: streams.classId }).from(streams),
    db.select({ id: classes.id, boardId: classes.boardId, name: classes.name }).from(classes),
  ]);
  const streamMap = new Map(allStreams.map(s => [s.id, s]));
  const classMap  = new Map(allClasses.map(cls => [cls.id, cls]));

  return c.json(allSubjects.map(s => {
    const stream = s.streamId ? streamMap.get(s.streamId) : undefined;
    const cls    = stream ? classMap.get(stream.classId) : undefined;
    return {
      id:          s.id,
      name:        s.name,
      slug:        s.slug,
      status:      s.isPublished ? 'published' : 'draft',
      stream_id:   s.streamId ?? null,
      stream_name: stream?.name ?? null,
      class_id:    cls?.id ?? null,
      board_id:    cls?.boardId ?? null,
      description: s.description ?? null,
      updated_at:  s.updatedAt ? new Date(s.updatedAt * 1000).toISOString() : null,
    };
  }));
});

// ── Subject create ─────────────────────────────────────────────────────────────

staffRouter.post('/content/subjects', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:edit'); if (denied) return denied;
  const body = await safeBody(c);
  if (body.status === 'published') { const publishDenied = await capabilityDenied(c, auth, 'content:publish'); if (publishDenied) return publishDenied; }
  const name = String(body['name'] ?? '').trim();
  if (!name) return c.json({ detail: 'name is required' }, 422);

  const db       = createDb(c.env.DB);
  const slug     = makeSlug(name);
  const now      = nowTs();
  const id       = crypto.randomUUID();
  const streamId = String(body['stream_id'] ?? '').trim() || null;

  // Resolve context for response
  let streamName: string | null = null;
  let classId: string | null    = null;
  let boardId: string | null    = null;

  if (streamId) {
    const streamRow = await db.select({ id: streams.id, name: streams.name, classId: streams.classId })
      .from(streams).where(eq(streams.id, streamId)).get();
    if (streamRow) {
      streamName = streamRow.name;
      const clsRow = await db.select({ id: classes.id, boardId: classes.boardId })
        .from(classes).where(eq(classes.id, streamRow.classId)).get();
      if (clsRow) { classId = clsRow.id; boardId = clsRow.boardId; }
    }
  }

  await db.insert(subjects).values({
    id, name, slug,
    streamId: streamId ?? undefined,
    description: String(body['description'] ?? '').trim() || null,
    imageUrl:    String(body['image_url']   ?? '').trim() || null,
    isPublished: body['status'] === 'published' ? 1 : 0,
    pyqPapers:   '[]',
    createdAt:   now, updatedAt: now,
  });

  await auditLog(c.env, auth.sub ?? '', 'create_subject', 'subject', id, { name, streamId });
  return c.json({ id, name, slug, status: body['status'] === 'published' ? 'published' : 'draft',
    stream_id: streamId, stream_name: streamName, class_id: classId, board_id: boardId }, 201);
});

// ── Subject update ─────────────────────────────────────────────────────────────

staffRouter.patch('/content/subjects/:id', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:edit'); if (denied) return denied;
  const subjectId = c.req.param('id');
  const body = await safeBody(c);
  if ('status' in body) { const publishDenied = await capabilityDenied(c, auth, 'content:publish'); if (publishDenied) return publishDenied; }
  const db = createDb(c.env.DB);

  const existing = await db.select({ id: subjects.id, slug: subjects.slug })
    .from(subjects).where(eq(subjects.id, subjectId)).get();
  if (!existing) return c.json({ detail: 'Subject not found' }, 404);

  type SubjectUpdate = Partial<{
    name: string; slug: string; description: string | null; imageUrl: string | null;
    isPublished: number; streamId: string | null; updatedAt: number;
  }>;
  const updates: SubjectUpdate = { updatedAt: nowTs() };

  if ('name' in body)        updates.name        = String(body['name'] ?? '').trim();
  if ('description' in body) updates.description = String(body['description'] ?? '').trim() || null;
  if ('image_url' in body)   updates.imageUrl    = String(body['image_url'] ?? '').trim() || null;
  if ('status' in body)      updates.isPublished = body['status'] === 'published' ? 1 : 0;
  if ('stream_id' in body)   updates.streamId    = String(body['stream_id'] ?? '').trim() || null;
  if ('slug' in body)        updates.slug        = String(body['slug'] ?? '').trim() || existing.slug;

  await db.update(subjects).set(updates).where(eq(subjects.id, subjectId));
  await auditLog(c.env, auth.sub ?? '', 'update_subject', 'subject', subjectId, updates);

  // When publish state changes, invalidate the public chapters KV cache so the
  // unpublished subject is no longer served from cache on the next request.
  if ('status' in body) {
    c.env.CONTENT_KV.delete(`subject:${subjectId}:chapters`).catch(() => { /* best-effort */ });
  }

  return c.json({ ok: true });
});

// ── Subject delete ─────────────────────────────────────────────────────────────

staffRouter.delete('/content/subjects/:id', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:delete'); if (denied) return denied;
  const subjectId = c.req.param('id');
  const db = createDb(c.env.DB);

  const existing = await db.select({ id: subjects.id, pyqPapers: subjects.pyqPapers })
    .from(subjects).where(eq(subjects.id, subjectId)).get();
  if (!existing) return c.json({ detail: 'Subject not found' }, 404);

  const dependent = await db.select({ id: chapters.id }).from(chapters).where(eq(chapters.subjectId, subjectId));
  // Fail closed: D1 source metadata remains available for retry until all
  // external namespaces have been removed.
  try {
    for (const chapter of dependent) {
      await purgeChapterRag(c.env, chapter.id);
      await deleteR2Prefix(c.env, `pyq/${chapter.id}/`);
    }
    for (const paper of safeParse<PYQPaper[]>(existing.pyqPapers) ?? []) {
      await purgeRagScope(c.env, paper.id, 'pyq');
      await deleteR2Prefix(c.env, `pyq/subjects/${subjectId}/${paper.id}/`);
    }
    await c.env.CONTENT_KV.delete(`subject:${subjectId}:chapters`);
  } catch (error) {
    await auditLog(c.env, auth.sub ?? '', 'delete_subject_partial', 'subject', subjectId, { chapters: dependent.length, error: String(error) });
    return c.json({ detail: 'External content cleanup failed; subject metadata was retained for retry.', chapters: dependent.length }, 502);
  }
  // D1 deletes are transactional within the service and happen last.
  await c.env.DB.batch([
    c.env.DB.prepare('DELETE FROM chunks WHERE subject_id = ?').bind(subjectId),
    c.env.DB.prepare('DELETE FROM rag_documents WHERE subject_id = ?').bind(subjectId),
    c.env.DB.prepare('DELETE FROM chapters WHERE subject_id = ?').bind(subjectId),
    c.env.DB.prepare('DELETE FROM subjects WHERE id = ?').bind(subjectId),
  ]);
  await auditLog(c.env, auth.sub ?? '', 'delete_subject', 'subject', subjectId, { chapters: dependent.length });
  return c.json({ ok: true, deleted: { chapters: dependent.length } });
});

// ─────────────────────────────────────────────────────────────────────────────
// Chapters — list
// ─────────────────────────────────────────────────────────────────────────────

staffRouter.get('/content/chapters/:subjectId', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const subjectId = c.req.param('subjectId');
  const db = createDb(c.env.DB);

  const rows = await db.select({
    id: chapters.id, title: chapters.title, titleAs: chapters.titleAs, slug: chapters.slug, slugAs: chapters.slugAs,
    status: chapters.status, contentType: chapters.contentType,
    chapterNumber: chapters.chapterNumber,
    notesEn: chapters.notesEn, notesAs: chapters.notesAs,
    ragText: chapters.ragText, ragTextAs: chapters.ragTextAs,
    ragSectionsEn: chapters.ragSectionsEn, ragSectionsAs: chapters.ragSectionsAs,
    qaEn: chapters.qaEn, qaAs: chapters.qaAs,
    wordCountEn: chapters.wordCountEn,
    ragUpdatedAt: chapters.ragUpdatedAt, ragIndexedAt: chapters.ragIndexedAt,
    updatedAt: chapters.updatedAt,
  }).from(chapters).where(eq(chapters.subjectId, subjectId)).orderBy(chapters.chapterNumber);

  const isStale = (u: number | null, i: number | null) => Boolean(u && (!i || u > i));
  const ts = (v: number | null) => v ? new Date(v * 1000).toISOString() : null;

  return c.json(rows.map(ch => ({
    id:               ch.id,
    title:            ch.title,
    title_as:         ch.titleAs ?? null,
    slug:             ch.slug,
    status:           ch.status ?? 'draft',
    content_type:     ch.contentType ?? 'standard',
    chapter_number:   ch.chapterNumber ?? null,
    has_notes_en:     Boolean(ch.notesEn),
    has_notes_as:     Boolean(ch.notesAs),
    has_qa_en:        Boolean(ch.qaEn && ch.qaEn !== '[]'),
    has_qa_as:        Boolean(ch.qaAs && ch.qaAs !== '[]'),
    has_rag_en:       Boolean(ch.ragText),
    has_rag_as:       Boolean(ch.ragTextAs),
    has_rag_sections: Boolean(ch.ragSectionsEn && ch.ragSectionsEn !== '[]'),
    word_count:       ch.wordCountEn ?? 0,
    rag_updated_at:   ts(ch.ragUpdatedAt),
    rag_indexed_at:   ts(ch.ragIndexedAt),
    notes_rag_stale:  isStale(ch.ragUpdatedAt, ch.ragIndexedAt),
    updated_at:       ts(ch.updatedAt),
  })));
});

// ─────────────────────────────────────────────────────────────────────────────
// Chapter — create
// ─────────────────────────────────────────────────────────────────────────────

staffRouter.post('/content/chapters', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:edit'); if (denied) return denied;
  const body = await safeBody(c);
  if ('status' in body && body.status !== 'draft') { const publishDenied = await capabilityDenied(c, auth, 'content:publish'); if (publishDenied) return publishDenied; }

  const title     = String(body['title']      ?? '').trim();
  const subjectId = String(body['subject_id'] ?? '').trim();
  if (!title)     return c.json({ detail: 'title is required' }, 422);
  if (!subjectId) return c.json({ detail: 'subject_id is required' }, 422);

  const db  = createDb(c.env.DB);
  const now = nowTs();

  if ('status' in body && (!CHAPTER_STATUSES.has(String(body.status)))) return c.json({ detail: 'Invalid chapter status' }, 422);
  if ('content_type' in body && (!CHAPTER_TYPES.has(String(body.content_type)))) return c.json({ detail: 'Invalid chapter content type' }, 422);
  if ('chapter_number' in body && !validChapterNumber(body.chapter_number)) return c.json({ detail: 'chapter_number must be a positive integer' }, 422);
  const subject = await db.select({ id: subjects.id }).from(subjects).where(eq(subjects.id, subjectId)).get();
  if (!subject) return c.json({ detail: 'Subject not found' }, 404);
  let chapterNumber = validChapterNumber(body['chapter_number']) ? body['chapter_number'] : undefined;
  if (!chapterNumber) {
    const maxRow = await c.env.DB
      .prepare('SELECT MAX(chapter_number) as mx FROM chapters WHERE subject_id = ?')
      .bind(subjectId).first<{ mx: number | null }>();
    chapterNumber = (maxRow?.mx ?? 0) + 1;
  }

  const slug = makeSlug(String(body['slug'] ?? title));
  const id   = crypto.randomUUID();

  await db.insert(chapters).values({
    id, title, titleAs: typeof body.title_as === 'string' ? body.title_as.trim() || null : null,
    subjectId, slug, slugAs: typeof body.slug_as === 'string' ? body.slug_as.trim() || null : null,
    metaDescription: typeof body.meta_description === 'string' ? body.meta_description.trim() || null : null,
    metaDescriptionAs: typeof body.meta_description_as === 'string' ? body.meta_description_as.trim() || null : null,
    keywords: typeof body.keywords === 'string' ? body.keywords.trim() || null : null,
    keywordsAs: typeof body.keywords_as === 'string' ? body.keywords_as.trim() || null : null,
    chapterNumber,
    contentType:     String(body['content_type'] ?? 'standard'),
    status:          String(body['status'] ?? 'draft'),
    notesEn: typeof body.notes_en === 'string' ? body.notes_en : (typeof body.content === 'string' ? body.content : null),
    notesAs: typeof body.notes_as === 'string' ? body.notes_as : (typeof body.content_as === 'string' ? body.content_as : null),
    ragText: typeof body.rag_text_en === 'string' ? body.rag_text_en : null,
    ragTextAs: typeof body.rag_text_as === 'string' ? body.rag_text_as : null,
    ragSectionsEn: Array.isArray(body.rag_sections_en) ? JSON.stringify(body.rag_sections_en) : '[]',
    ragSectionsAs: Array.isArray(body.rag_sections_as) ? JSON.stringify(body.rag_sections_as) : '[]',
    publishedTopics: Array.isArray(body.topics) ? JSON.stringify(body.topics) : '[]',
    qaEn: Array.isArray(body.qa_en) ? JSON.stringify(body.qa_en) : (typeof body.qa_rag_text_en === 'string' || typeof body.qa_text_en === 'string' ? JSON.stringify([{ content: body.qa_rag_text_en ?? body.qa_text_en ?? '' }]) : '[]'),
    qaAs: Array.isArray(body.qa_as) ? JSON.stringify(body.qa_as) : (typeof body.qa_rag_text_as === 'string' || typeof body.qa_text_as === 'string' ? JSON.stringify([{ content: body.qa_rag_text_as ?? body.qa_text_as ?? '' }]) : '[]'),
    pyqPdfUrl: null,
    ragUpdatedAt: ['notes_en', 'notes_as', 'content', 'content_as', 'rag_text_en', 'rag_text_as', 'rag_sections_en', 'rag_sections_as', 'qa_en', 'qa_as', 'qa_text_en', 'qa_text_as', 'qa_rag_text_en', 'qa_rag_text_as'].some(key => key in body) ? now : null,
    createdAt: now, updatedAt: now,
  });

  await auditLog(c.env, auth.sub ?? '', 'create_chapter', 'chapter', id, { title, subjectId });

  // Prewarm/invalidate KV so the public chapters list reflects the new chapter
  // immediately — even if status is 'draft', purging avoids a stale cache miss
  // on first publish and keeps the list consistent.
  kvPrewarm(c.env, subjectId).catch(() => { /* best-effort */ });

  return c.json({
    id, title, title_as: body['title_as'] ?? null, slug, slug_as: body['slug_as'] ?? null,
    status:         body['status'] ?? 'draft',
    content_type:   body['content_type'] ?? 'standard',
    chapter_number: chapterNumber,
    subject_id:     subjectId,
    has_notes_en: false, has_notes_as: false,
    has_qa_en: false,    has_qa_as: false,
    has_rag_en: false,   has_rag_as: false,
    has_rag_sections: false,
    word_count: 0,
    rag_updated_at: null, rag_indexed_at: null,
    notes_rag_stale: false,
    updated_at: new Date(now * 1000).toISOString(),
  }, 201);
});

// ─────────────────────────────────────────────────────────────────────────────
// Chapter — detail (full edit view)
// ─────────────────────────────────────────────────────────────────────────────

staffRouter.get('/content/chapter/:chapterId', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const chapterId = c.req.param('chapterId');
  const db = createDb(c.env.DB);

  const ch = await db.select().from(chapters).where(eq(chapters.id, chapterId)).get();
  if (!ch) return c.json({ detail: 'Chapter not found' }, 404);

  const ts = (v: number | null) => v ? new Date(v * 1000).toISOString() : null;
  const isStale = (u: number | null, i: number | null) => Boolean(u && (!i || u > i));

  // qa_rag_sections_en/as — dashboard field names for structured Q&A sections.
  // D1 stores these in qaEn/qaAs (same column, same JSON shape).
  // Expose both field names so the dashboard round-trips without changes.
  const qaEnArr = safeParse<Array<Record<string, unknown>>>(ch.qaEn) ?? [];
  const qaAsArr = safeParse<Array<Record<string, unknown>>>(ch.qaAs) ?? [];

  return c.json({
    id:             ch.id,
    title:          ch.title,
    title_as:       ch.titleAs ?? null,
    slug:           ch.slug ?? '',
    slug_as:        ch.slugAs ?? null,
    meta_description: ch.metaDescription ?? '',
    meta_description_as: ch.metaDescriptionAs ?? '',
    keywords:       ch.keywords ?? '',
    keywords_as:    ch.keywordsAs ?? '',
    status:         ch.status ?? 'draft',
    content_type:   ch.contentType ?? 'standard',
    chapter_number: ch.chapterNumber ?? null,
    subject_id:     ch.subjectId,
    notes_en:       ch.notesEn ?? '',
    notes_as:       ch.notesAs ?? '',
    rag_text_en:    ch.ragText ?? '',
    rag_text_as:    ch.ragTextAs ?? '',
    rag_sections_en:     safeParse(ch.ragSectionsEn) ?? [],
    rag_sections_as:     safeParse(ch.ragSectionsAs) ?? [],
    // Dashboard fields for structured Q&A — aliases to qaEn/qaAs
    qa_rag_sections_en:  qaEnArr,
    qa_rag_sections_as:  qaAsArr,
    qa_en:               qaEnArr,
    qa_as:               qaAsArr,
    qa_text_en:          typeof qaEnArr[0] === 'object' && typeof (qaEnArr[0] as Record<string, unknown>).content === 'string' ? (qaEnArr[0] as Record<string, string>).content : '',
    qa_text_as:          typeof qaAsArr[0] === 'object' && typeof (qaAsArr[0] as Record<string, unknown>).content === 'string' ? (qaAsArr[0] as Record<string, string>).content : '',
    qa_rag_text_en:      typeof qaEnArr[0] === 'object' && typeof (qaEnArr[0] as Record<string, unknown>).content === 'string' ? (qaEnArr[0] as Record<string, string>).content : '',
    qa_rag_text_as:      typeof qaAsArr[0] === 'object' && typeof (qaAsArr[0] as Record<string, unknown>).content === 'string' ? (qaAsArr[0] as Record<string, string>).content : '',
    published_topics:    safeParse(ch.publishedTopics) ?? [],
    // PYQ fields
    pyq_pdf_url:         ch.pyqPdfUrl ?? '',
    pyq_papers:          safeParse(ch.pyqPapers) ?? [],
    has_pyq_pdf:         Boolean(ch.pyqPdfUrl),
    has_pyq_papers:      Boolean(ch.pyqPapers && ch.pyqPapers !== '[]'),
    pyq_papers_count:    (safeParse<unknown[]>(ch.pyqPapers) ?? []).length,
    rag_updated_at:  ts(ch.ragUpdatedAt),
    rag_indexed_at:  ts(ch.ragIndexedAt),
    rag_stale:       isStale(ch.ragUpdatedAt, ch.ragIndexedAt),
    notes_rag_stale: isStale(ch.ragUpdatedAt, ch.ragIndexedAt),
    updated_at:      ts(ch.updatedAt),
    created_at:      ts(ch.createdAt),
    word_count:      ch.wordCountEn ?? 0,
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Chapter — update (PATCH)
// ─────────────────────────────────────────────────────────────────────────────

staffRouter.patch('/content/chapter/:chapterId', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:edit'); if (denied) return denied;
  const chapterId = c.req.param('chapterId');
  const body = await safeBody(c);
  if ('status' in body) { const publishDenied = await capabilityDenied(c, auth, 'content:publish'); if (publishDenied) return publishDenied; }
  if ('published_topics' in body) return c.json({ detail: 'Use the dedicated topic APIs to modify published_topics.' }, 400);
  if ('pyq_pdf_url' in body && auth.role !== 'admin') return c.json({ detail: 'Use the authenticated PYQ upload endpoint to modify pyq_pdf_url.' }, 403);
  const db   = createDb(c.env.DB);

  const ch = await db.select({
    id: chapters.id, subjectId: chapters.subjectId,
    ragUpdatedAt: chapters.ragUpdatedAt, ragIndexedAt: chapters.ragIndexedAt,
    notesEn: chapters.notesEn,
  }).from(chapters).where(eq(chapters.id, chapterId)).get();
  if (!ch) return c.json({ detail: 'Chapter not found' }, 404);

  const now = nowTs();
  let ragChanged     = false;
  let contentChanged = false;

  type ChapterUpdate = Partial<{
    title: string; titleAs: string | null; slug: string; slugAs: string | null;
    metaDescription: string | null; metaDescriptionAs: string | null;
    keywords: string | null; keywordsAs: string | null;
    chapterNumber: number; status: string; contentType: string;
    notesEn: string; notesAs: string;
    ragText: string; ragTextAs: string;
    ragSectionsEn: string; ragSectionsAs: string;
    publishedTopics: string; qaEn: string; qaAs: string;
    ragUpdatedAt: number; wordCountEn: number; updatedAt: number; pyqPdfUrl: string | null;
  }>;

  const updates: ChapterUpdate = { updatedAt: now };

  if ('title' in body && !String(body.title ?? '').trim()) return c.json({ detail: 'title cannot be empty' }, 422);
  if ('title' in body && body['title'] !== undefined)          updates.title         = String(body['title']).trim();
  if ('title_as' in body && body['title_as'] !== undefined)    updates.titleAs       = String(body['title_as'] ?? '').trim() || null;
  if ('slug' in body && !makeSlug(String(body.slug ?? ''))) return c.json({ detail: 'slug cannot be empty' }, 422);
  if ('slug' in body  && body['slug'] !== undefined)           updates.slug          = makeSlug(String(body['slug']));
  if ('slug_as' in body && body['slug_as'] !== undefined)      updates.slugAs        = String(body['slug_as'] ?? '').trim() || null;
  if ('meta_description' in body && body['meta_description'] !== undefined) updates.metaDescription = String(body['meta_description'] ?? '').trim() || null;
  if ('meta_description_as' in body && body['meta_description_as'] !== undefined) updates.metaDescriptionAs = String(body['meta_description_as'] ?? '').trim() || null;
  if ('keywords' in body && body['keywords'] !== undefined) updates.keywords = String(body['keywords'] ?? '').trim() || null;
  if ('keywords_as' in body && body['keywords_as'] !== undefined) updates.keywordsAs = String(body['keywords_as'] ?? '').trim() || null;
  if ('chapter_number' in body && !validChapterNumber(body.chapter_number)) return c.json({ detail: 'chapter_number must be a positive integer' }, 422);
  if ('chapter_number' in body) updates.chapterNumber = body['chapter_number'] as number;
  if ('status' in body && !CHAPTER_STATUSES.has(String(body.status))) return c.json({ detail: 'Invalid chapter status' }, 422);
  if ('status' in body) updates.status = String(body.status);
  if ('content_type' in body && !CHAPTER_TYPES.has(String(body.content_type))) return c.json({ detail: 'Invalid chapter content type' }, 422);
  if ('content_type' in body) updates.contentType = String(body.content_type);

  // Presence means intent: explicit empty strings clear editor fields.
  if ('notes_en' in body && body['notes_en'] !== undefined) {
    updates.notesEn = String(body['notes_en'] ?? '');
    contentChanged = ragChanged = true;
  }
  if ('notes_as' in body && body['notes_as'] !== undefined) {
    updates.notesAs = String(body['notes_as'] ?? '');
    contentChanged = ragChanged = true;
  }

  // RAG blob fields
  if ('rag_text_en' in body && body['rag_text_en'] !== undefined) {
    updates.ragText = String(body['rag_text_en']); ragChanged = true;
  }
  if ('rag_text_as' in body && body['rag_text_as'] !== undefined) {
    updates.ragTextAs = String(body['rag_text_as']); ragChanged = true;
  }

  if ('rag_sections_en' in body && Array.isArray(body['rag_sections_en'])) {
    updates.ragSectionsEn = JSON.stringify(body['rag_sections_en']); ragChanged = true;
  }
  if ('rag_sections_as' in body && Array.isArray(body['rag_sections_as'])) {
    updates.ragSectionsAs = JSON.stringify(body['rag_sections_as']); ragChanged = true;
  }

  // Published topics (non-empty list replaces)

  // Q&A — accept both qa_en/as (canonical) and qa_rag_sections_en/as (dashboard alias).
  // D1 stores all structured Q&A in qaEn/qaAs regardless of field name used by caller.
  // qa_rag_sections_en takes priority when both are provided in the same request.
  if ('qa_en' in body && Array.isArray(body['qa_en'])) {
    updates.qaEn = JSON.stringify(body['qa_en']); ragChanged = true;
  }
  if ('qa_rag_sections_en' in body && Array.isArray(body['qa_rag_sections_en']) && (body['qa_rag_sections_en'] as unknown[]).length > 0) {
    updates.qaEn = JSON.stringify(body['qa_rag_sections_en']); ragChanged = true;
  }
  if ('qa_as' in body && Array.isArray(body['qa_as'])) {
    updates.qaAs = JSON.stringify(body['qa_as']); ragChanged = true;
  }
  if ('qa_rag_sections_as' in body && Array.isArray(body['qa_rag_sections_as']) && (body['qa_rag_sections_as'] as unknown[]).length > 0) {
    updates.qaAs = JSON.stringify(body['qa_rag_sections_as']); ragChanged = true;
  }
  if ('qa_text_en' in body && typeof body.qa_text_en === 'string') { updates.qaEn = JSON.stringify(body.qa_text_en ? [{ content: body.qa_text_en }] : []); ragChanged = true; }
  if ('qa_text_as' in body && typeof body.qa_text_as === 'string') { updates.qaAs = JSON.stringify(body.qa_text_as ? [{ content: body.qa_text_as }] : []); ragChanged = true; }
  if ('qa_rag_text_en' in body && typeof body.qa_rag_text_en === 'string') { updates.qaEn = JSON.stringify(body.qa_rag_text_en ? [{ content: body.qa_rag_text_en }] : []); ragChanged = true; }
  if ('qa_rag_text_as' in body && typeof body.qa_rag_text_as === 'string') { updates.qaAs = JSON.stringify(body.qa_rag_text_as ? [{ content: body.qa_rag_text_as }] : []); ragChanged = true; }
  if ('pyq_pdf_url' in body && typeof body.pyq_pdf_url === 'string') updates.pyqPdfUrl = body.pyq_pdf_url || null;

  if (ragChanged)     updates.ragUpdatedAt = now;
  if (contentChanged) {
    const notesSrc = (updates.notesEn ?? ch.notesEn ?? '').trim();
    updates.wordCountEn = notesSrc ? notesSrc.split(/\s+/).length : 0;
  }

  try {
    await db.update(chapters).set(updates).where(eq(chapters.id, chapterId));
  } catch (error) {
    console.error(`[staff] Failed to update chapter ${chapterId}:`, error);
    return c.json({ detail: 'Failed to save chapter' }, 500);
  }
  await auditLog(c.env, auth.sub ?? '', 'update_chapter', 'chapter', chapterId);

  // Every editor save can affect the public chapter list (titles, slugs,
  // topics, availability), so refresh its long-lived KV entry immediately.
  await kvPrewarm(c.env, ch.subjectId);

  return c.json({ ok: true });
});

// ─────────────────────────────────────────────────────────────────────────────
// Chapter — delete
// ─────────────────────────────────────────────────────────────────────────────

staffRouter.delete('/content/chapter/:chapterId', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:delete'); if (denied) return denied;
  const chapterId = c.req.param('chapterId');
  const db = createDb(c.env.DB);

  const ch = await db.select({ id: chapters.id, subjectId: chapters.subjectId })
    .from(chapters).where(eq(chapters.id, chapterId)).get();
  if (!ch) return c.json({ detail: 'Chapter not found' }, 404);

  try {
    await purgeChapterRag(c.env, chapterId);
    const r2Objects = await deleteR2Prefix(c.env, `pyq/${chapterId}/`);
    await c.env.DB.batch([
      c.env.DB.prepare('DELETE FROM rag_documents WHERE chapter_id = ?').bind(chapterId),
      c.env.DB.prepare('DELETE FROM chapters WHERE id = ?').bind(chapterId),
    ]);
    await kvPrewarm(c.env, ch.subjectId);
    await auditLog(c.env, auth.sub ?? '', 'delete_chapter', 'chapter', chapterId, { r2_objects: r2Objects });
    return c.json({ ok: true, deleted: { r2_objects: r2Objects } });
  } catch (error) {
    await auditLog(c.env, auth.sub ?? '', 'delete_chapter_partial', 'chapter', chapterId, { error: String(error) });
    return c.json({ detail: 'External cleanup failed; chapter metadata was retained for retry.' }, 502);
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// RAG Reindex — embed chapter content and upsert to Vectorize
// ─────────────────────────────────────────────────────────────────────────────

// Both the plural form (/chapters/:id/reindex) and the singular form
// (/chapter/:chapterId/reindex) used by the staff dashboard share the same handler.
staffRouter.post('/content/chapters/:id/reindex',         async (c) => { const auth = await guard(c); if (!auth) return c.res; return handleChapterReindex(c, c.req.param('id')); });
staffRouter.post('/content/chapter/:chapterId/reindex',   async (c) => { const auth = await guard(c); if (!auth) return c.res; return handleChapterReindex(c, c.req.param('chapterId')); });

// ─────────────────────────────────────────────────────────────────────────────
// Subject-level PYQ papers (JSON blob in subjects.pyq_papers)
// ─────────────────────────────────────────────────────────────────────────────

type PYQPaper = {
  id: string; name: string;
  class_name?: string | undefined; year?: number | null | undefined;
  description?: string | undefined; rag_text?: string | undefined; rag_text_as?: string | undefined;
  rag_updated_at?: string | null | undefined; rag_indexed_at?: string | null | undefined;
  pages?: Array<{ id: string; url: string; uploaded_at?: string | undefined }> | undefined;
  created_at?: string | undefined;
};

async function loadSubjectPapers(
  env: Env, subjectId: string,
): Promise<{ row: typeof subjects.$inferSelect | undefined; papers: PYQPaper[] }> {
  const db = createDb(env.DB);
  const row = await db.select().from(subjects).where(eq(subjects.id, subjectId)).get();
  return { row, papers: safeParse<PYQPaper[]>(row?.pyqPapers) ?? [] };
}

async function saveSubjectPapers(env: Env, subjectId: string, papers: PYQPaper[]): Promise<void> {
  const db = createDb(env.DB);
  await db.update(subjects)
    .set({ pyqPapers: JSON.stringify(papers), updatedAt: nowTs() })
    .where(eq(subjects.id, subjectId));
}

// GET /staff/content/subject/:subjectId/pyq-papers
staffRouter.get('/content/subject/:subjectId/pyq-papers', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const { row, papers } = await loadSubjectPapers(c.env, c.req.param('subjectId'));
  if (!row) return c.json({ detail: 'Subject not found' }, 404);
  return c.json({ pyq_papers: papers });
});

// POST /staff/content/subject/:subjectId/pyq-papers
staffRouter.post('/content/subject/:subjectId/pyq-papers', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:edit'); if (denied) return denied;
  const subjectId = c.req.param('subjectId');
  const body = await safeBody(c);
  const name = String(body['name'] ?? '').trim();
  if (!name) return c.json({ detail: 'name is required' }, 422);

  const { row, papers } = await loadSubjectPapers(c.env, subjectId);
  if (!row) return c.json({ detail: 'Subject not found' }, 404);

  const paper: PYQPaper = {
    id:          crypto.randomUUID(), name,
    class_name:  String(body['class_name']  ?? '').trim() || undefined,
    year:        typeof body['year'] === 'number' ? body['year'] : null,
    description: String(body['description'] ?? '').trim() || undefined,
    rag_text:    String(body['rag_text']    ?? '').trim() || undefined,
    rag_text_as: String(body['rag_text_as'] ?? '').trim() || undefined,
    rag_updated_at: null, rag_indexed_at: null,
    pages: [],
    created_at: new Date().toISOString(),
  };

  papers.push(paper);
  await saveSubjectPapers(c.env, subjectId, papers);
  await auditLog(c.env, auth.sub ?? '', 'create_subject_pyq_paper', 'pyq_paper', paper.id, { subject_id: subjectId });
  return c.json({ ok: true, paper, pyq_papers: papers }, 201);
});

// PATCH /staff/content/subject/:subjectId/pyq-papers/:paperId
staffRouter.patch('/content/subject/:subjectId/pyq-papers/:paperId', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:edit'); if (denied) return denied;
  const { subjectId, paperId } = c.req.param();
  const body = await safeBody(c);

  const { row, papers } = await loadSubjectPapers(c.env, subjectId);
  if (!row) return c.json({ detail: 'Subject not found' }, 404);

  const paper = papers.find(p => p.id === paperId);
  if (!paper) return c.json({ detail: 'Paper not found' }, 404);

  let ragChanged = false;
  if ('name' in body)        paper.name        = String(body['name']        ?? '').trim();
  if ('class_name' in body)  paper.class_name  = String(body['class_name']  ?? '').trim();
  if ('year' in body)        paper.year        = typeof body['year'] === 'number' ? body['year'] : null;
  if ('description' in body) paper.description = String(body['description'] ?? '').trim();
  if ('rag_text' in body) {
    const v = String(body['rag_text'] ?? '').trim();
    if (v !== (paper.rag_text ?? '')) { paper.rag_text = v; ragChanged = true; }
  }
  if ('rag_text_as' in body) {
    const v = String(body['rag_text_as'] ?? '').trim();
    if (v !== (paper.rag_text_as ?? '')) { paper.rag_text_as = v; ragChanged = true; }
  }
  if (ragChanged) paper.rag_updated_at = new Date().toISOString();

  await saveSubjectPapers(c.env, subjectId, papers);
  await auditLog(c.env, auth.sub ?? '', 'update_subject_pyq_paper', 'pyq_paper', paperId, { subject_id: subjectId });
  return c.json({ ok: true, pyq_papers: papers });
});

// DELETE /staff/content/subject/:subjectId/pyq-papers/:paperId
staffRouter.delete('/content/subject/:subjectId/pyq-papers/:paperId', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:delete'); if (denied) return denied;
  const { subjectId, paperId } = c.req.param();
  const { row, papers } = await loadSubjectPapers(c.env, subjectId);
  if (!row) return c.json({ detail: 'Subject not found' }, 404);

  const filtered = papers.filter(p => p.id !== paperId);
  const paperExists = filtered.length !== papers.length;

  // Subject-level PYQ vectors use the paper ID as their chapter namespace.
  // Keep the metadata until Vectorize and its D1 mappings are both purged, so
  // a transient cleanup failure leaves staff something they can retry rather
  // than deleting the only record of the stale namespace.
  if (paperExists) {
    try {
      await deleteStaleVectors(c.env, paperId, 'pyq');
      await deleteR2Prefix(c.env, `pyq/subjects/${subjectId}/${paperId}/`);
    } catch {
      return c.json({
        detail: 'Could not remove the paper from RAG. The paper was not deleted; please retry.',
      }, 502);
    }
  }

  await saveSubjectPapers(c.env, subjectId, filtered);
  await auditLog(c.env, auth.sub ?? '', 'delete_subject_pyq_paper', 'pyq_paper', paperId, { subject_id: subjectId });
  return c.json({ ok: true, pyq_papers: filtered });
});

// ─────────────────────────────────────────────────────────────────────────────
// Chapter reindex — singular-form alias used by the staff dashboard
// POST /staff/content/chapter/:chapterId/reindex  (matches dashboard calls)
// POST /staff/content/chapters/:id/reindex        (plural, kept for symmetry)
// Both share the same handler, extracted as a named async function.
// ─────────────────────────────────────────────────────────────────────────────

async function handleChapterReindex(c: import('hono').Context<{ Bindings: Env }>, chapterId: string) {
  // The aliases authenticate before this shared handler; capability evaluation
  // remains here so both aliases cannot drift.
  const capabilityAuth = await guard(c);
  if (!capabilityAuth) return c.res;
  const denied = await capabilityDenied(c, capabilityAuth, 'rag:reindex');
  if (denied) return denied;
  const scope = (c.req.query('scope') ?? 'notes') as string;
  // 'pyq' scope: chapter-level PYQ content is stored in ragText (same field as notes).
  // Indexing it separately lets retrieval filter by sourceType='pyq'.
  if (!['notes', 'qa', 'pyq', 'all'].includes(scope))
    return c.json({ detail: 'scope must be notes | qa | pyq | all' }, 400);

  // This is deliberately the same implementation used by admin publishing.
  // It removes old vectors before inserting replacements and treats a missing
  // D1 mapping write as an indexing failure (with vector compensation).
  const scopes = scope === 'all' ? ['notes', 'qa', 'pyq'] as const : [scope as 'notes' | 'qa' | 'pyq'];
  try {
    const results = await reindexChapterRag(c.env, chapterId, [...scopes]);
    const ok = Object.values(results).every(result => !result.error);
    await auditLog(c.env, capabilityAuth.sub ?? '', 'reindex_chapter', 'chapter', chapterId, { scope, results });
    return c.json({ ok, chapter_id: chapterId, scopes, results, indexed_at: new Date().toISOString() }, ok ? 200 : 502);
  } catch (error) {
    if (error instanceof Error && error.message === 'Chapter not found') return c.json({ detail: error.message }, 404);
    return c.json({ ok: false, chapter_id: chapterId, detail: error instanceof Error ? error.message : String(error) }, 502);
  }

  const db = createDb(c.env.DB);
  const ch = await db.select({
    id: chapters.id, subjectId: chapters.subjectId,
    notesEn: chapters.notesEn, notesAs: chapters.notesAs,
    ragText: chapters.ragText, ragTextAs: chapters.ragTextAs,
    ragSectionsEn: chapters.ragSectionsEn, ragSectionsAs: chapters.ragSectionsAs,
    qaEn: chapters.qaEn, qaAs: chapters.qaAs,
  }).from(chapters).where(eq(chapters.id, chapterId)).get();
  if (!ch) return c.json({ detail: 'Chapter not found' }, 404);

  const getNotesText = (lang: 'en' | 'as'): string | null => {
    if (lang === 'en') {
      const secs = safeParse<Array<Record<string, string>>>(ch!.ragSectionsEn);
      if (secs && secs.length > 0) return flattenNotesSections(secs);
      return ch!.ragText ?? ch!.notesEn ?? null;
    } else {
      const secs = safeParse<Array<Record<string, string>>>(ch!.ragSectionsAs);
      if (secs && secs.length > 0) return flattenNotesSections(secs);
      return ch!.ragTextAs ?? ch!.notesAs ?? null;
    }
  };

  const getQaText = (lang: 'en' | 'as'): string | null => {
    const raw = lang === 'en' ? ch!.qaEn : ch!.qaAs;
    const arr = safeParse<Array<Record<string, string>>>(raw);
    if (arr && arr.length > 0) return flattenQaSections(arr);
    return null;
  };

  // For the 'pyq' scope, chapter-level PYQ content lives in ragText (the same
  // column staff paste PYQ material into). We index it under sourceType='pyq'
  // so the chat pipeline can filter by source when needed.
  const getPyqText = (lang: 'en' | 'as'): string | null =>
    lang === 'en' ? (ch!.ragText ?? null) : (ch!.ragTextAs ?? null);

  type ScopeKey = 'notes' | 'qa' | 'pyq';
  const scopesToRun: ScopeKey[] = scope === 'all'
    ? ['notes', 'qa', 'pyq']
    : [scope as ScopeKey];

  const hasContent = (s: ScopeKey): boolean => {
    if (s === 'notes') return Boolean(getNotesText('en') || getNotesText('as'));
    if (s === 'qa')    return Boolean(getQaText('en') || getQaText('as'));
    return Boolean(getPyqText('en') || getPyqText('as'));
  };

  // Map scope name to the sourceType stored in Vectorize metadata and D1 chunks.
  const scopeSourceType: Record<ScopeKey, 'notes' | 'important_questions' | 'pyq'> = {
    notes: 'notes',
    qa:    'important_questions',
    pyq:   'pyq',
  };

  const now      = nowTs();
  const subjectId = ch!.subjectId;
  type ScopeResult = { chunks: number; error?: string; skipped?: string };
  const results: Record<string, ScopeResult> = {};

  for (const s of scopesToRun) {
    // Always delete stale vectors for every requested scope — even when the scope
    // has no content to upsert.  A scope whose content was cleared still has old
    // vectors in Vectorize that would otherwise keep surfacing in RAG results.
    // If deletion fails, record the error and skip the upsert: proceeding would
    // leave stale tail-chunk vectors alongside the newly inserted ones.
    try {
      await deleteStaleVectors(c.env, chapterId, scopeSourceType[s]);
    } catch (delErr) {
      results[s] = { chunks: 0, error: `delete failed: ${String(delErr)}` };
      continue;
    }

    // After cleanup, if there is no content there is nothing to upsert.
    if (!hasContent(s)) {
      results[s] = { chunks: 0, skipped: 'no content' };
      continue;
    }

    try {
      let total = 0;
      if (s === 'notes') {
        const en = getNotesText('en'); if (en) total += await ingestToVectorize(c.env, chapterId, subjectId, en!, 'english',  'notes');
        const as = getNotesText('as'); if (as) total += await ingestToVectorize(c.env, chapterId, subjectId, as!, 'assamese', 'notes');
      } else if (s === 'qa') {
        const en = getQaText('en'); if (en) total += await ingestToVectorize(c.env, chapterId, subjectId, en!, 'english',  'important_questions');
        const as = getQaText('as'); if (as) total += await ingestToVectorize(c.env, chapterId, subjectId, as!, 'assamese', 'important_questions');
      } else {
        const en = getPyqText('en'); if (en) total += await ingestToVectorize(c.env, chapterId, subjectId, en!, 'english',  'pyq');
        const as = getPyqText('as'); if (as) total += await ingestToVectorize(c.env, chapterId, subjectId, as!, 'assamese', 'pyq');
      }
      results[s] = { chunks: total };
    } catch (err) {
      results[s] = { chunks: 0, error: String(err) };
    }
  }

  // Update ragIndexedAt only when the notes scope succeeded (it is the primary scope).
  if (results['notes'] && !results['notes']!.error && !results['notes']!.skipped) {
    await db.update(chapters)
      .set({ ragIndexedAt: now, updatedAt: now })
      .where(eq(chapters.id, chapterId));
  }

  await auditLog(c.env, 'system', 'reindex_chapter', 'chapter', chapterId, { scope, results });

  const allOk    = Object.values(results).every(r => !r.error);
  const anyUpserted = Object.values(results).some(r => !r.error && !r.skipped && r.chunks > 0);
  const anySkipped  = Object.values(results).every(r => r.skipped);

  return c.json({
    ok: allOk,
    chapter_id: chapterId,
    scopes: scopesToRun,
    results,
    indexed_at: new Date(now * 1000).toISOString(),
    ...(anySkipped && !anyUpserted ? { note: 'No content found for requested scopes; stale vectors were cleaned up.' } : {}),
  });
}

type RagJobItem = { chapter_id: string; scopes: RagScope[]; status: 'pending' | 'running' | 'done' | 'failed'; results?: unknown; error?: string };
type Topic = { id: string; title: string; title_as?: string; content?: string; content_as?: string; status?: 'draft' | 'published'; [key: string]: unknown };
type RagScope = 'notes' | 'qa' | 'pyq';

export async function runRagJob(env: Env, jobId: string): Promise<void> {
  const token = crypto.randomUUID();
  const claimed = await env.DB.prepare(`
    UPDATE rag_reindex_jobs SET status='running', lease_token=?, lease_expires_at=?, updated_at=?
    WHERE id=? AND (status IN ('pending','partial') OR (status='running' AND lease_expires_at < ?))
  `).bind(token, nowTs() + 900, nowTs(), jobId, nowTs()).run();
  // Another request/cron owns this durable job. Never execute unclaimed work.
  if ((claimed.meta.changes ?? 0) !== 1) return;
  const job = await env.DB.prepare('SELECT items FROM rag_reindex_jobs WHERE id = ?').bind(jobId).first<{ items: string }>();
  if (!job) return;
  const items = (safeParse<RagJobItem[]>(job.items) ?? []).map(item => item.status === 'running' ? { ...item, status: 'pending' as const } : item);
  const write = () => env.DB.prepare('UPDATE rag_reindex_jobs SET items=?, updated_at=?, lease_expires_at=? WHERE id=? AND lease_token=?')
    .bind(JSON.stringify(items), nowTs(), nowTs() + 900, jobId, token).run();
  if (((await write()).meta.changes ?? 0) !== 1) return;
  for (const item of items) {
    if (item.status !== 'pending') continue;
    item.status = 'running';
    if (((await write()).meta.changes ?? 0) !== 1) return;
    try {
      const results = await reindexChapterRag(env, item.chapter_id, item.scopes);
      item.results = results;
      const failed = Object.values(results).some(result => result.error);
      item.status = failed ? 'failed' : 'done';
      if (failed) item.error = 'One or more requested RAG scopes failed';
    } catch (error) { item.status = 'failed'; item.error = error instanceof Error ? error.message : String(error); }
    if (((await write()).meta.changes ?? 0) !== 1) return;
  }
  const failures = items.filter(item => item.status === 'failed');
  await env.DB.prepare('UPDATE rag_reindex_jobs SET status=?, error_log=?, updated_at=?, completed_at=?, lease_token=NULL, lease_expires_at=NULL WHERE id=? AND lease_token=?')
    .bind(failures.length ? (failures.length === items.length ? 'failed' : 'partial') : 'done', failures.length ? `${failures.length} item(s) failed` : null, nowTs(), nowTs(), jobId, token).run();
}

/** Scheduler recovery for request-lifetime RAG jobs. Bounded to avoid cron overruns. */
export async function resumeRagReindexJobs(env: Env): Promise<void> {
  const rows = await env.DB.prepare(`
    SELECT id FROM rag_reindex_jobs
    WHERE status IN ('pending','partial') OR (status='running' AND lease_expires_at < ?)
    ORDER BY updated_at ASC LIMIT 10
  `).bind(nowTs()).all<{ id: string }>();
  for (const row of rows.results ?? []) await runRagJob(env, row.id);
}

staffRouter.post('/content/reindex-jobs', async c => {
  const auth = await guard(c); if (!auth) return c.res;
  const forbidden = await capabilityDenied(c, auth, 'rag:reindex'); if (forbidden) return forbidden;
  const body = await safeBody(c);
  const ids = Array.isArray(body.chapter_ids) ? body.chapter_ids.filter((id): id is string => typeof id === 'string' && id.length <= 100).slice(0, 100) : [];
  const scopes = (Array.isArray(body.scopes) ? body.scopes : ['notes']).filter((scope): scope is RagScope => scope === 'notes' || scope === 'qa' || scope === 'pyq');
  if (!ids.length || !scopes.length) return c.json({ detail: 'chapter_ids (1-100) and valid scopes are required' }, 422);
  const id = crypto.randomUUID();
  const items: RagJobItem[] = ids.map(chapter_id => ({ chapter_id, scopes, status: 'pending' }));
  await c.env.DB.prepare('INSERT INTO rag_reindex_jobs (id, actor_id, status, requested_scopes, items, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)')
    .bind(id, auth.sub ?? null, 'pending', JSON.stringify(scopes), JSON.stringify(items), nowTs(), nowTs()).run();
  await auditLog(c.env, auth.sub ?? '', 'create_reindex_job', 'rag_job', id, { count: items.length, scopes });
  c.executionCtx.waitUntil(runRagJob(c.env, id));
  return c.json({ job_id: id, status: 'pending', items }, 202);
});
staffRouter.get('/content/reindex-jobs/:id', async c => {
  const auth = await guard(c); if (!auth) return c.res;
  const forbidden = await capabilityDenied(c, auth, 'history:read'); if (forbidden) return forbidden;
  const job = await c.env.DB.prepare('SELECT * FROM rag_reindex_jobs WHERE id=?').bind(c.req.param('id')).first();
  return job ? c.json(job) : c.json({ detail: 'RAG job not found' }, 404);
});
staffRouter.get('/content/reindex-jobs', async c => {
  const auth = await guard(c); if (!auth) return c.res;
  const forbidden = await capabilityDenied(c, auth, 'history:read'); if (forbidden) return forbidden;
  const rows = await c.env.DB.prepare('SELECT * FROM rag_reindex_jobs ORDER BY created_at DESC LIMIT 100').all();
  return c.json({ jobs: rows.results });
});
staffRouter.post('/content/reindex-jobs/:id/retry-failed', async c => {
  const auth = await guard(c); if (!auth) return c.res;
  const forbidden = await capabilityDenied(c, auth, 'rag:reindex'); if (forbidden) return forbidden;
  const old = await c.env.DB.prepare('SELECT requested_scopes, items FROM rag_reindex_jobs WHERE id=?').bind(c.req.param('id')).first<{ requested_scopes: string; items: string }>();
  if (!old) return c.json({ detail: 'RAG job not found' }, 404);
  const failed = (safeParse<RagJobItem[]>(old.items) ?? []).filter(item => item.status === 'failed').map(item => ({ ...item, status: 'pending' as const, error: undefined, results: undefined }));
  if (!failed.length) return c.json({ detail: 'No failed items to retry' }, 409);
  const id = crypto.randomUUID();
  await c.env.DB.prepare('INSERT INTO rag_reindex_jobs (id, actor_id, status, requested_scopes, items, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)')
    .bind(id, auth.sub ?? null, 'pending', old.requested_scopes, JSON.stringify(failed), nowTs(), nowTs()).run();
  await auditLog(c.env, auth.sub ?? '', 'retry_reindex_job_failed_items', 'rag_job', id, { retry_of: c.req.param('id'), count: failed.length });
  c.executionCtx.waitUntil(runRagJob(c.env, id));
  return c.json({ job_id: id, retry_of: c.req.param('id'), retried_items: failed.length }, 202);
});

function chapterTopics(row: { publishedTopics: string | null }): Topic[] { return safeParse<Topic[]>(row.publishedTopics) ?? []; }
async function impactFingerprint(env: Env, ids: string[]): Promise<string> {
  const sorted = [...new Set(ids)].sort();
  const placeholders = sorted.map(() => '?').join(',');
  const rows = await env.DB.prepare(`SELECT id,updated_at,published_topics,pyq_papers FROM chapters WHERE id IN (${placeholders}) ORDER BY id`).bind(...sorted).all();
  const chunkRows = await env.DB.prepare(`SELECT chapter_id,COUNT(*) count,COALESCE(MAX(created_at),0) newest FROM chunks WHERE chapter_id IN (${placeholders}) GROUP BY chapter_id ORDER BY chapter_id`).bind(...sorted).all();
  return previewSignature(env.JWT_SECRET, JSON.stringify({ ids: sorted, chapters: rows.results, chunks: chunkRows.results }));
}
async function topicChapter(env: Env, chapterId: string) {
  return createDb(env.DB).select({ id: chapters.id, subjectId: chapters.subjectId, publishedTopics: chapters.publishedTopics }).from(chapters).where(eq(chapters.id, chapterId)).get();
}
staffRouter.get('/content/chapter/:chapterId/topics', async c => {
  const auth = await guard(c); if (!auth) return c.res;
  const row = await topicChapter(c.env, c.req.param('chapterId')); return row ? c.json({ topics: chapterTopics(row) }) : c.json({ detail: 'Chapter not found' }, 404);
});
staffRouter.post('/content/chapter/:chapterId/topics', async c => {
  const auth = await guard(c); if (!auth) return c.res; const denied = await capabilityDenied(c, auth, 'content:edit'); if (denied) return denied;
  const row = await topicChapter(c.env, c.req.param('chapterId')); const body = await safeBody(c); const title = typeof body.title === 'string' ? body.title.trim() : '';
  if (!row) return c.json({ detail: 'Chapter not found' }, 404); if (!title || title.length > 300) return c.json({ detail: 'title is required (max 300)' }, 422);
  const topic: Topic = {
    id: crypto.randomUUID(),
    title,
    title_as: typeof body.title_as === 'string' ? body.title_as.trim() : '',
    content: typeof body.content === 'string' ? body.content : '',
    content_as: typeof body.content_as === 'string' ? body.content_as : '',
    status: 'draft',
  };
  const topics = [...chapterTopics(row), topic];
  await createDb(c.env.DB).update(chapters).set({ publishedTopics: JSON.stringify(topics), updatedAt: nowTs(), ragUpdatedAt: nowTs() }).where(eq(chapters.id, row.id));
  await auditLog(c.env, auth.sub ?? '', 'create_topic', 'topic', topic.id, { chapter_id: row.id }); return c.json({ topic }, 201);
});
staffRouter.patch('/content/chapter/:chapterId/topics/:topicId', async c => {
  const auth = await guard(c); if (!auth) return c.res; const denied = await capabilityDenied(c, auth, 'content:edit'); if (denied) return denied;
  const row = await topicChapter(c.env, c.req.param('chapterId')); if (!row) return c.json({ detail: 'Chapter not found' }, 404);
  const body = await safeBody(c); const topics = chapterTopics(row); const topic = topics.find(item => item.id === c.req.param('topicId')); if (!topic) return c.json({ detail: 'Topic not found' }, 404);
  for (const key of ['title', 'title_as', 'content', 'content_as'] as const) if (typeof body[key] === 'string') topic[key] = body[key] as string;
  await createDb(c.env.DB).update(chapters).set({ publishedTopics: JSON.stringify(topics), updatedAt: nowTs(), ragUpdatedAt: nowTs() }).where(eq(chapters.id, row.id));
  await auditLog(c.env, auth.sub ?? '', 'update_topic', 'topic', topic.id, { chapter_id: row.id });
  return c.json({ topic });
});
staffRouter.post('/content/chapter/:chapterId/topics/:topicId/:action', async c => {
  const auth = await guard(c); if (!auth) return c.res;
  const action = c.req.param('action');
  const needed: StaffCapability = action === 'publish' || action === 'unpublish' ? 'content:publish' : action === 'reindex' ? 'rag:reindex' : 'content:edit';
  const denied = await capabilityDenied(c, auth, needed); if (denied) return denied;
  const row = await topicChapter(c.env, c.req.param('chapterId')); if (!row) return c.json({ detail: 'Chapter not found' }, 404);
  const topics = chapterTopics(row); const topic = topics.find(item => item.id === c.req.param('topicId')); if (!topic) return c.json({ detail: 'Topic not found' }, 404);
  if (action === 'publish') topic.status = 'published';
  else if (action === 'unpublish') topic.status = 'draft';
  else if (action === 'translate') {
    const body = await safeBody(c); const translated = typeof body.title_as === 'string' ? body.title_as.trim() : '';
    if (!translated) return c.json({ detail: 'title_as is required; translation providers are not invoked implicitly' }, 422);
    topic.title_as = translated; if (typeof body.content_as === 'string') topic.content_as = body.content_as;
  } else if (action === 'reindex') {
    // Topic JSON is part of the chapter document; canonical chapter indexing
    // makes it searchable without creating a second incompatible vector schema.
    const results = await reindexChapterRag(c.env, row.id, ['notes']);
    if (results.notes.error) return c.json({ ok: false, detail: results.notes.error }, 502);
  } else return c.json({ detail: 'Unknown topic action' }, 404);
  const timestamp = nowTs();
  await createDb(c.env.DB).update(chapters).set(action === 'reindex'
    ? { publishedTopics: JSON.stringify(topics), updatedAt: timestamp, ragIndexedAt: timestamp }
    : { publishedTopics: JSON.stringify(topics), updatedAt: timestamp, ragUpdatedAt: timestamp }).where(eq(chapters.id, row.id));
  await auditLog(c.env, auth.sub ?? '', `${action}_topic`, 'topic', topic.id, { chapter_id: row.id });
  return c.json({ ok: true, topic });
});
staffRouter.delete('/content/chapter/:chapterId/topics/:topicId', async c => {
  const auth = await guard(c); if (!auth) return c.res; const denied = await capabilityDenied(c, auth, 'content:delete'); if (denied) return denied;
  const row = await topicChapter(c.env, c.req.param('chapterId')); if (!row) return c.json({ detail: 'Chapter not found' }, 404);
  const topics = chapterTopics(row); const retained = topics.filter(item => item.id !== c.req.param('topicId'));
  if (retained.length === topics.length) return c.json({ detail: 'Topic not found' }, 404);
  await createDb(c.env.DB).update(chapters).set({ publishedTopics: JSON.stringify(retained), updatedAt: nowTs(), ragUpdatedAt: nowTs() }).where(eq(chapters.id, row.id));
  await auditLog(c.env, auth.sub ?? '', 'delete_topic', 'topic', c.req.param('topicId'), { chapter_id: row.id }); return c.json({ ok: true });
});

staffRouter.post('/content/bulk/impact-preview', async c => {
  const auth = await guard(c); if (!auth) return c.res; const denied = await capabilityDenied(c, auth, 'content:delete'); if (denied) return denied;
  const body = await safeBody(c); const ids = Array.isArray(body.chapter_ids) ? body.chapter_ids.filter((id): id is string => typeof id === 'string').slice(0, 100) : [];
  if (!ids.length) return c.json({ detail: 'chapter_ids is required (max 100)' }, 422);
  const placeholders = ids.map(() => '?').join(',');
  const counts = await c.env.DB.prepare(`SELECT COUNT(*) chapters, COALESCE(SUM(json_array_length(pyq_papers)),0) pyqs FROM chapters WHERE id IN (${placeholders})`).bind(...ids).first<{ chapters: number; pyqs: number }>();
  const chunksCount = await c.env.DB.prepare(`SELECT COUNT(*) chunks FROM chunks WHERE chapter_id IN (${placeholders})`).bind(...ids).first<{ chunks: number }>();
  const rows = await c.env.DB.prepare(`SELECT published_topics FROM chapters WHERE id IN (${placeholders})`).bind(...ids).all<{ published_topics: string | null }>();
  const topics = (rows.results ?? []).reduce((n, row) => n + (safeParse<unknown[]>(row.published_topics)?.length ?? 0), 0);
  const sorted = [...new Set(ids)].sort();
  const impact = { chapters: counts?.chapters ?? 0, topics, pyqs: counts?.pyqs ?? 0, chunks: chunksCount?.chunks ?? 0, vectors_estimated: chunksCount?.chunks ?? 0 };
  const id = crypto.randomUUID(); const expiresAt = nowTs() + 300;
  const impactHash = await impactFingerprint(c.env, sorted);
  const payload = `${id}.${auth.sub}.${sorted.join(',')}.${impactHash}.${expiresAt}`;
  const token = `${id}.${await previewSignature(c.env.JWT_SECRET, payload)}`;
  await c.env.DB.prepare('INSERT INTO destructive_preview_tokens (id,actor_id,chapter_ids,impact_hash,expires_at,created_at) VALUES (?,?,?,?,?,?)')
    .bind(id, auth.sub ?? '', JSON.stringify(sorted), impactHash, expiresAt, nowTs()).run();
  return c.json({ ...impact, r2_prefixes: sorted.map(chapterId => `pyq/${chapterId}/`), preview_token: token, expires_at: expiresAt });
});
staffRouter.post('/content/bulk/:operation', async c => {
  const auth = await guard(c); if (!auth) return c.res;
  const operation = c.req.param('operation');
  const capability: StaffCapability = operation === 'delete' ? 'content:delete' : operation === 'reindex' ? 'rag:reindex' : operation === 'publish' || operation === 'unpublish' ? 'content:publish' : 'content:edit';
  const denied = await capabilityDenied(c, auth, capability); if (denied) return denied;
  if (!['publish', 'unpublish', 'delete', 'translate', 'reindex'].includes(operation)) return c.json({ detail: 'Unknown bulk operation' }, 404);
  const body = await safeBody(c); const ids = Array.isArray(body.chapter_ids) ? body.chapter_ids.filter((id): id is string => typeof id === 'string').slice(0, 100) : [];
  if (!ids.length) return c.json({ detail: 'chapter_ids is required (max 100)' }, 422);
  if (operation === 'translate') return c.json({ detail: 'Bulk translate is unsupported; submit explicit per-topic translations.' }, 400);
  if (operation === 'delete') {
    const supplied = typeof body.preview_token === 'string' ? body.preview_token : '';
    const [previewId, suppliedSignature] = supplied.split('.');
    const preview = previewId ? await c.env.DB.prepare('SELECT * FROM destructive_preview_tokens WHERE id=?').bind(previewId).first<{ actor_id: string; chapter_ids: string; impact_hash: string; expires_at: number; consumed_at: number | null }>() : null;
    const sorted = [...new Set(ids)].sort();
    const expectedSignature = preview ? await previewSignature(c.env.JWT_SECRET, `${previewId}.${preview.actor_id}.${sorted.join(',')}.${preview.impact_hash}.${preview.expires_at}`) : '';
    const currentFingerprint = preview ? await impactFingerprint(c.env, sorted) : '';
    if (!preview || currentFingerprint !== preview.impact_hash || suppliedSignature !== expectedSignature || preview.actor_id !== (auth.sub ?? '') || preview.expires_at < nowTs() || preview.consumed_at || JSON.stringify(sorted) !== preview.chapter_ids) return c.json({ detail: 'A valid, matching, fresh, unused impact preview token is required.' }, 409);
    const consumed = await c.env.DB.prepare('UPDATE destructive_preview_tokens SET consumed_at=? WHERE id=? AND consumed_at IS NULL AND expires_at>=?').bind(nowTs(), previewId, nowTs()).run();
    if ((consumed.meta.changes ?? 0) !== 1) return c.json({ detail: 'Impact preview token was already consumed or expired.' }, 409);
  }
  const outcomes: Array<{ chapter_id: string; status: 'done' | 'failed'; error?: string }> = [];
  const affectedSubjects = new Set<string>();
  for (const id of ids) {
    try {
      const row = await topicChapter(c.env, id); if (!row) throw new Error('Chapter not found');
      if (operation === 'delete') {
        await purgeChapterRag(c.env, id); await deleteR2Prefix(c.env, `pyq/${id}/`);
        await c.env.DB.batch([c.env.DB.prepare('DELETE FROM rag_documents WHERE chapter_id=?').bind(id), c.env.DB.prepare('DELETE FROM chapters WHERE id=?').bind(id)]);
        affectedSubjects.add(row.subjectId);
      } else if (operation === 'reindex') {
        const results = await reindexChapterRag(c.env, id, ['notes', 'qa', 'pyq']);
        if (Object.values(results).some(result => result.error)) throw new Error('RAG scope failed');
      } else if (operation === 'publish') {
        const results = await reindexChapterRag(c.env, id, ['notes', 'qa', 'pyq']);
        if (Object.values(results).some(result => result.error)) throw new Error('Required RAG publish step failed');
        await createDb(c.env.DB).update(chapters).set({ status: 'published', updatedAt: nowTs() }).where(eq(chapters.id, id));
        await kvPrewarm(c.env, row.subjectId);
      } else {
        await purgeChapterRag(c.env, id);
        await createDb(c.env.DB).update(chapters).set({ status: 'unpublished', updatedAt: nowTs() }).where(eq(chapters.id, id));
        await kvPrewarm(c.env, row.subjectId);
      }
      outcomes.push({ chapter_id: id, status: 'done' });
    } catch (error) { outcomes.push({ chapter_id: id, status: 'failed', error: error instanceof Error ? error.message : String(error) }); }
  }
  for (const subjectId of affectedSubjects) await kvPrewarm(c.env, subjectId);
  const failed = outcomes.filter(outcome => outcome.status === 'failed').length;
  const status = failed === ids.length ? 'failed' : failed ? 'partial' : 'complete';
  await auditLog(c.env, auth.sub ?? '', `bulk_${operation}`, 'chapter', 'bulk', { requested: ids.length, failed });
  return c.json({ status, outcomes, counts: { requested: ids.length, succeeded: ids.length - failed, failed } }, failed === ids.length ? 502 : 200);
});

// ─────────────────────────────────────────────────────────────────────────────
// KV prewarm — explicit trigger
// ─────────────────────────────────────────────────────────────────────────────

staffRouter.post('/content/kv-prewarm/:subjectId', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:publish'); if (denied) return denied;
  const subjectId = c.req.param('subjectId');
  await kvPrewarm(c.env, subjectId);
  return c.json({ ok: true, subject_id: subjectId, message: 'KV prewarm complete' });
});

// ─────────────────────────────────────────────────────────────────────────────
// Subject PYQ paper — page upload (R2), delete, reindex
// ─────────────────────────────────────────────────────────────────────────────

const ALLOWED_IMAGE_TYPES = new Set([
  'image/jpeg', 'image/png', 'image/webp', 'image/gif',
]);
const EXT_MAP: Record<string, string> = {
  jpg: 'image/jpeg', jpeg: 'image/jpeg', png: 'image/png', webp: 'image/webp', gif: 'image/gif',
};

/** Upload a binary to R2 and return its public URL (requires R2_PUBLIC_URL env var).
 *
 * R2_PUBLIC_URL is validated BEFORE writing to R2 so a missing config does not
 * leave an orphaned object in the bucket with no persisted URL.
 */
async function uploadToR2(
  env: Env,
  key: string,
  data: ArrayBuffer,
  contentType: string,
): Promise<string> {
  const base = (env.R2_PUBLIC_URL ?? '').replace(/\/$/, '');
  if (!base) throw new Error('R2_PUBLIC_URL is not configured — set it via `wrangler secret put R2_PUBLIC_URL`');
  await env.R2_BUCKET.put(key, data, { httpMetadata: { contentType } });
  return `${base}/${key}`;
}

function ownedR2Key(env: Env, url: string | null | undefined, prefix: string): string | null {
  if (!url || !env.R2_PUBLIC_URL) return null;
  try {
    const expected = new URL(env.R2_PUBLIC_URL);
    const actual = new URL(url);
    if (actual.origin !== expected.origin) return null;
    const key = actual.pathname.replace(/^\//, '');
    return key.startsWith(prefix) && !key.includes('..') ? key : null;
  } catch { return null; }
}

// POST /staff/content/subject/:subjectId/pyq-papers/:paperId/pages
staffRouter.post('/content/subject/:subjectId/pyq-papers/:paperId/pages', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:edit'); if (denied) return denied;
  const { subjectId, paperId } = c.req.param();

  const { row, papers } = await loadSubjectPapers(c.env, subjectId);
  if (!row) return c.json({ detail: 'Subject not found' }, 404);
  const paper = papers.find(p => p.id === paperId);
  if (!paper) return c.json({ detail: 'Paper not found' }, 404);

  // Parse multipart
  const formData = await c.req.parseBody();
  const rawFile = formData['file'];
  // Narrow to a single File object — reject strings and arrays
  if (!rawFile || typeof rawFile === 'string' || Array.isArray(rawFile))
    return c.json({ detail: 'file field is required (multipart, single file)' }, 400);
  const file = rawFile as File;

  if (file.size > 20 * 1024 * 1024) return c.json({ detail: 'File too large (max 20 MB)' }, 413);

  const ext = (file.name ?? '').split('.').pop()?.toLowerCase() ?? 'jpg';
  const ct  = EXT_MAP[ext] ?? file.type ?? 'image/jpeg';
  if (!ALLOWED_IMAGE_TYPES.has(ct)) return c.json({ detail: 'Only image files accepted (JPG, PNG, WEBP, GIF)' }, 400);

  const pageId = crypto.randomUUID();
  const key    = `pyq/subjects/${subjectId}/${paperId}/${pageId}.${ext}`;

  let url: string;
  try {
    url = await uploadToR2(c.env, key, await file.arrayBuffer(), ct);
  } catch (err) {
    return c.json({ detail: `Upload failed: ${String(err)}` }, 502);
  }

  const page = { id: pageId, url, uploaded_at: new Date().toISOString() };
  paper.pages = [...(paper.pages ?? []), page];

  try {
    await saveSubjectPapers(c.env, subjectId, papers);
  } catch (error) {
    // R2 succeeded but metadata did not: compensate immediately so there is
    // no unreferenced student document left in storage.
    await c.env.R2_BUCKET.delete(key).catch(() => undefined);
    return c.json({ detail: `Could not persist uploaded page; R2 object was removed: ${String(error)}` }, 502);
  }
  await auditLog(c.env, auth.sub ?? '', 'upload_subject_pyq_page', 'pyq_paper', paperId, { subject_id: subjectId, page_id: pageId });
  return c.json({ ok: true, page, pyq_papers: papers }, 201);
});

// DELETE /staff/content/subject/:subjectId/pyq-papers/:paperId/pages/:pageId
staffRouter.delete('/content/subject/:subjectId/pyq-papers/:paperId/pages/:pageId', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:delete'); if (denied) return denied;
  const { subjectId, paperId, pageId } = c.req.param();

  const { row, papers } = await loadSubjectPapers(c.env, subjectId);
  if (!row) return c.json({ detail: 'Subject not found' }, 404);
  const paper = papers.find(p => p.id === paperId);
  if (!paper) return c.json({ detail: 'Paper not found' }, 404);

  const removed = (paper.pages ?? []).find(pg => pg.id === pageId);
  if (!removed) return c.json({ detail: 'Page not found' }, 404);
  const removedKey = ownedR2Key(c.env, removed.url, `pyq/subjects/${subjectId}/${paperId}/`);
  if (!removedKey) return c.json({ detail: 'Stored page URL is not owned by this paper.' }, 409);
  try { await c.env.R2_BUCKET.delete(removedKey); }
  catch { return c.json({ detail: 'Could not remove page object; metadata retained for retry.' }, 502); }
  paper.pages = (paper.pages ?? []).filter(pg => pg.id !== pageId);
  await saveSubjectPapers(c.env, subjectId, papers);
  await auditLog(c.env, auth.sub ?? '', 'delete_subject_pyq_page', 'pyq_paper', paperId, { subject_id: subjectId, page_id: pageId });
  return c.json({ ok: true, pyq_papers: papers });
});

// ─────────────────────────────────────────────────────────────────────────────
// Chapter-level PYQ upload endpoints
//
//   POST   /staff/content/chapter/:id/upload-pyq        — PDF or image → R2, saves pyq_pdf_url
//   POST   /staff/content/chapter/:id/pyq-papers        — image → R2, appends to chapter.pyq_papers
//   DELETE /staff/content/chapter/:id/pyq-papers/:paperId — removes a page entry
//
// Mirrors apps/backend/app/api/v1/staff_content.py lines 887–1021.
// ─────────────────────────────────────────────────────────────────────────────

const ALLOWED_PYQ_CONTENT_TYPES = new Set([
  'application/pdf',
  'image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/tiff',
]);

const PYQ_EXT_MAP: Record<string, string> = {
  pdf: 'application/pdf',
  jpg: 'image/jpeg', jpeg: 'image/jpeg',
  png: 'image/png',  webp: 'image/webp',
  gif: 'image/gif',  tiff: 'image/tiff', tif: 'image/tiff',
};

type ChapterPYQPage = { id: string; title?: string; year?: number | null; url: string; uploaded_at: string };

/** Load pyq_papers JSON array from a chapter row. */
async function loadChapterPapers(
  env: Env,
  chapterId: string,
): Promise<{ ch: typeof chapters.$inferSelect | undefined; papers: ChapterPYQPage[] }> {
  const db = createDb(env.DB);
  const ch = await db.select({
    id: chapters.id, subjectId: chapters.subjectId,
    pyqPapers: chapters.pyqPapers, pyqPdfUrl: chapters.pyqPdfUrl,
    updatedAt: chapters.updatedAt,
  }).from(chapters).where(eq(chapters.id, chapterId)).get();
  return { ch: ch as typeof chapters.$inferSelect | undefined, papers: safeParse<ChapterPYQPage[]>(ch?.pyqPapers) ?? [] };
}

// POST /staff/content/chapter/:id/upload-pyq
// Accepts PDF or image; stores in R2 under pyq/{chapterId}/{filename}; saves URL to chapter.pyq_pdf_url.
staffRouter.post('/content/chapter/:id/upload-pyq', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:edit'); if (denied) return denied;
  const chapterId = c.req.param('id');

  const { ch } = await loadChapterPapers(c.env, chapterId);
  if (!ch) return c.json({ detail: 'Chapter not found' }, 404);

  const formData = await c.req.parseBody();
  const rawFile  = formData['file'];
  if (!rawFile || typeof rawFile === 'string' || Array.isArray(rawFile))
    return c.json({ detail: 'file field is required (multipart, single file)' }, 400);
  const file = rawFile as File;

  if (file.size > 25 * 1024 * 1024)
    return c.json({ detail: 'File too large (max 25 MB)' }, 413);

  const filename = file.name || 'upload';
  const ext      = filename.includes('.') ? filename.split('.').pop()?.toLowerCase() ?? '' : '';
  const ct       = PYQ_EXT_MAP[ext] ?? (file.type || 'application/octet-stream');
  if (!ALLOWED_PYQ_CONTENT_TYPES.has(ct))
    return c.json({ detail: 'Only PDF and image files are allowed for PYQ upload' }, 400);

  const key = `pyq/${chapterId}/single/${crypto.randomUUID()}.${ext || 'bin'}`;
  let publicUrl: string;
  try {
    publicUrl = await uploadToR2(c.env, key, await file.arrayBuffer(), ct);
  } catch (err) {
    return c.json({ detail: `Upload failed: ${String(err)}` }, 502);
  }

  const db  = createDb(c.env.DB);
  try {
    await db.update(chapters)
      .set({ pyqPdfUrl: publicUrl, updatedAt: nowTs() })
      .where(eq(chapters.id, chapterId));
  } catch (error) {
    await c.env.R2_BUCKET.delete(key).catch(() => undefined);
    return c.json({ detail: `Could not persist PYQ upload; R2 object was removed: ${String(error)}` }, 502);
  }

  const oldKey = ownedR2Key(c.env, ch.pyqPdfUrl, `pyq/${chapterId}/`);
  if (oldKey && oldKey !== key) await c.env.R2_BUCKET.delete(oldKey).catch(() => undefined);
  await auditLog(c.env, auth.sub ?? '', 'upload_pyq', 'chapter', chapterId, { key, ct });
  return c.json({ ok: true, pyq_pdf_url: publicUrl, key });
});

// POST /staff/content/chapter/:id/pyq-papers
// Accepts an image file; appends a page entry to chapter.pyq_papers.
staffRouter.post('/content/chapter/:id/pyq-papers', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:edit'); if (denied) return denied;
  const chapterId = c.req.param('id');

  const { ch, papers } = await loadChapterPapers(c.env, chapterId);
  if (!ch) return c.json({ detail: 'Chapter not found' }, 404);

  const formData = await c.req.parseBody();
  const rawFile  = formData['file'];
  if (!rawFile || typeof rawFile === 'string' || Array.isArray(rawFile))
    return c.json({ detail: 'file field is required (multipart, single file)' }, 400);
  const file = rawFile as File;

  if (file.size > 20 * 1024 * 1024)
    return c.json({ detail: 'File too large (max 20 MB)' }, 413);

  const filename = file.name || 'page.jpg';
  const ext      = filename.includes('.') ? filename.split('.').pop()?.toLowerCase() ?? 'jpg' : 'jpg';
  const ct       = EXT_MAP[ext] ?? (file.type || 'image/jpeg');
  if (!ALLOWED_IMAGE_TYPES.has(ct))
    return c.json({ detail: 'Only image files accepted (JPG, PNG, WEBP, GIF)' }, 400);

  const paperId = crypto.randomUUID();
  const key     = `pyq/${chapterId}/papers/${paperId}.${ext}`;
  let url: string;
  try {
    url = await uploadToR2(c.env, key, await file.arrayBuffer(), ct);
  } catch (err) {
    return c.json({ detail: `Upload failed: ${String(err)}` }, 502);
  }

  // Optional metadata from form fields
  const title = typeof formData['title'] === 'string' ? formData['title'].trim() : undefined;
  const yearRaw = typeof formData['year'] === 'string' ? parseInt(formData['year'], 10) : undefined;
  const year = yearRaw && !isNaN(yearRaw) ? yearRaw : undefined;

  const paper: ChapterPYQPage = { id: paperId, url, uploaded_at: new Date().toISOString() };
  if (title) paper.title = title;
  if (year)  paper.year  = year;

  papers.push(paper);

  const db = createDb(c.env.DB);
  try {
    await db.update(chapters)
      .set({ pyqPapers: JSON.stringify(papers), updatedAt: nowTs() })
      .where(eq(chapters.id, chapterId));
  } catch (error) {
    await c.env.R2_BUCKET.delete(key).catch(() => undefined);
    return c.json({ detail: `Could not persist PYQ page; R2 object was removed: ${String(error)}` }, 502);
  }

  await auditLog(c.env, auth.sub ?? '', 'add_pyq_paper', 'chapter', chapterId, { paperId });
  return c.json({ ok: true, paper, pyq_papers: papers }, 201);
});

// DELETE /staff/content/chapter/:id/pyq-papers/:paperId
// Removes a page entry from chapter.pyq_papers and clears the chapter's PYQ
// Vectorize scope. PYQ chunks are chapter-scoped, so retaining an indexed page
// after its metadata is deleted would allow stale question-paper context into RAG.
staffRouter.delete('/content/chapter/:id/pyq-papers/:paperId', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'content:delete'); if (denied) return denied;
  const { id: chapterId, paperId } = c.req.param();

  const { ch, papers } = await loadChapterPapers(c.env, chapterId);
  if (!ch) return c.json({ detail: 'Chapter not found' }, 404);

  const filtered = papers.filter(p => p.id !== paperId);
  const removed = papers.find(p => p.id === paperId);
  const paperExists = filtered.length !== papers.length;

  // Keep deletion idempotent for unknown page IDs, but never remove known page
  // metadata until its chapter-scoped PYQ vectors have been purged successfully.
  // Otherwise RAG can continue returning content for a page staff have deleted.
  if (paperExists) {
    try {
      await deleteStaleVectors(c.env, chapterId, 'pyq');
    } catch {
      return c.json({
        detail: 'Could not remove the page from RAG. The page was not deleted; please retry.',
      }, 502);
    }
  }

  if (removed) {
    const removedKey = ownedR2Key(c.env, removed.url, `pyq/${chapterId}/papers/`);
    if (!removedKey) return c.json({ detail: 'Stored page URL is not owned by this chapter.' }, 409);
    try { await c.env.R2_BUCKET.delete(removedKey); }
    catch { return c.json({ detail: 'Could not remove R2 page object; metadata retained for retry.' }, 502); }
  }

  const db = createDb(c.env.DB);
  await db.update(chapters)
    .set({ pyqPapers: JSON.stringify(filtered), updatedAt: nowTs() })
    .where(eq(chapters.id, chapterId));

  await auditLog(c.env, auth.sub ?? '', 'delete_pyq_paper', 'chapter', chapterId, { paperId });
  return c.json({ ok: true, pyq_papers: filtered });
});

// POST /staff/content/subject/:subjectId/pyq-papers/:paperId/reindex
// Embed the paper's rag_text into Vectorize (uses paper_id as chapter namespace).
staffRouter.post('/content/subject/:subjectId/pyq-papers/:paperId/reindex', async (c) => {
  const auth = await guard(c); if (!auth) return c.res;
  const denied = await capabilityDenied(c, auth, 'rag:reindex'); if (denied) return denied;
  const { subjectId, paperId } = c.req.param();

  const { row, papers } = await loadSubjectPapers(c.env, subjectId);
  if (!row) return c.json({ detail: 'Subject not found' }, 404);
  const paper = papers.find(p => p.id === paperId);
  if (!paper) return c.json({ detail: 'Paper not found' }, 404);

  const ragEn = (paper.rag_text    ?? '').trim();
  const ragAs = (paper.rag_text_as ?? '').trim();
  let totalChunks = 0;
  const errors: string[] = [];
  try { await purgeRagScope(c.env, paperId, 'pyq'); }
  catch (error) { return c.json({ ok: false, detail: `PYQ cleanup failed: ${String(error)}` }, 502); }

  if (ragEn) {
    try { totalChunks += await ingestToVectorize(c.env, paperId, subjectId, ragEn, 'english', 'pyq'); }
    catch (err) { errors.push(`english: ${String(err)}`); }
  }
  if (ragAs) {
    try { totalChunks += await ingestToVectorize(c.env, paperId, subjectId, ragAs, 'assamese', 'pyq'); }
    catch (err) { errors.push(`assamese: ${String(err)}`); }
  }

  if (errors.length === 0) {
    const now = new Date().toISOString();
    paper.rag_indexed_at = now;
    await saveSubjectPapers(c.env, subjectId, papers);
    await auditLog(c.env, auth.sub ?? '', 'reindex_subject_pyq_paper', 'pyq_paper', paperId, { subject_id: subjectId, chunks: totalChunks });
  }

  return c.json({
    ok: errors.length === 0,
    paper_id: paperId, subject_id: subjectId,
    chunks: totalChunks,
    ...(errors.length > 0 ? { errors } : {}),
    pyq_papers: papers,
  });
});
