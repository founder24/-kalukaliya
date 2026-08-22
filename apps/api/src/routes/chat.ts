/**
 * POST /v1/chat/stream — Workers SSE chat pipeline (Phase 4)
 *
 * Pipeline (mirrors apps/backend/app/api/v1/chat.py streaming endpoint):
 *   1. Auth (optional JWT) + quota check (D1 for authed, KV for anon)
 *   2. Language detection — Assamese Unicode range or explicit lang param
 *   3. Two-phase RAG:
 *      a. Embed query via Workers AI @cf/baai/bge-m3
 *      b. Query Vectorize (filter by medium), gate by cosine threshold
 *      c. D1 fast path: fetch chapter content with fallback chain
 *         ragSectionsEn → ragText → notesEn  (same for As variant)
 *   4. Build system prompt (curriculum context + history + question)
 *   5. Emit source_card SSE event before LLM tokens
 *   6. Stream via Workers AI (primary: @cf/zai-org/glm-4.7-flash, fallback: @cf/qwen/qwen3-30b-a3b-fp8)
 *   7. Emit syrabit_done event (latency, model, route_trace, credits)
 *   8. waitUntil: persist user+assistant messages to D1, increment quota
 */

import { Hono } from 'hono';
import { eq, and, desc } from 'drizzle-orm';
import { createDb } from '../db/client';
import {
  users,
  chats,
  chapters as chaptersTable,
  quotaUsage,
} from '../db/schema';
import { verifyToken, extractBearer } from '../middleware/auth';
import { streamGenerate, AI_MODEL_PRIMARY } from '../services/ai';
import type { Env } from '../types';

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const CONFIDENCE_HIGH = 0.80;
const CONFIDENCE_LOW  = 0.50;

const MONTHLY_LIMITS: Record<string, number> = {
  free:    20,
  starter: 100,
  pro:     500,
  premium: 10_000,
};

const CONTEXT_CHAR_CAP = 10_000; // max characters of chapter content per request
const HISTORY_MSG_CAP  = 10;     // last N messages to load from D1

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

interface ChatRequest {
  message: string;
  lang?: 'en' | 'as';
  session_id?: string;
  conversation_id?: string; // frontend legacy alias for session_id
  chapter_id?: string;
  chapter_name?: string;
  subject_id?: string;
  source_type?: string;
  board_name?: string;
  class_name?: string;
  board_id?: string;
  class_id?: string;
  context_messages?: { role: string; content: string }[];
}

interface ContextChunk {
  chapterId: string;
  chapterTitle: string;
  // exactOptionalPropertyTypes: explicit | undefined so callers can pass undefined
  subjectId?: string | undefined;
  content: string;
  score: number;
}

interface RagSection {
  content: string;
  heading?: string;
}

// VectorizeMatch metadata shape (camelCase — see syrabit-rag-v2.md)
interface ChunkMeta {
  chapterId?: string;
  subjectId?: string;
  topicId?: string;
  medium?: string;
  sourceType?: string;
  chunkType?: string;
  content?: string;
  chapterTitle?: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// Pure helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Detect whether a message is Assamese (Bengali script U+0980–U+09FF).
 * Uses explicit override when provided; otherwise falls back to character ratio.
 */
function detectLang(text: string, explicit?: 'en' | 'as'): 'en' | 'as' {
  if (explicit === 'en' || explicit === 'as') return explicit;
  const assamese = (text.match(/[\u0980-\u09FF]/g) ?? []).length;
  return assamese / Math.max(text.length, 1) > 0.15 ? 'as' : 'en';
}

/** Strip null bytes + dangerous control chars; hard-cap at 2000 chars. */
function sanitize(text: string): string {
  return text
    .replace(/\x00/g, '')
    .replace(/[\x01-\x08\x0B\x0C\x0E-\x1F]/g, '')
    .trim()
    .slice(0, 2000);
}

function sseEvent(payload: unknown): string {
  return `data: ${JSON.stringify(payload)}\n\n`;
}

function currentPeriod(): string {
  const d = new Date();
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

function tryJson<T>(s: string | null | undefined, fallback: T): T {
  if (!s) return fallback;
  try { return JSON.parse(s) as T; } catch { return fallback; }
}

/**
 * Stable anonymous user ID derived from Cloudflare's CF-Connecting-IP header.
 * Not cryptographically secure — used only for rate-limiting.
 */
function anonUserId(req: Request): string {
  const ip =
    req.headers.get('CF-Connecting-IP') ??
    req.headers.get('X-Real-IP') ??
    req.headers.get('X-Forwarded-For')?.split(',')[0]?.trim() ??
    'unknown';
  // btoa gives a stable base64 string; strip non-alphanumeric for KV key safety
  return `anon-${btoa(ip).replace(/[^a-z0-9]/gi, '').slice(0, 20)}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Quota helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Atomically reserve one quota slot for an authenticated user BEFORE invoking the LLM.
 * Uses a SQLite INSERT-or-increment with RETURNING so the check and the increment
 * are a single statement — eliminates the check-then-increment race window that
 * would allow parallel requests to all pass the same count.
 *
 * If the new count exceeds the limit the increment is rolled back and the caller
 * receives allowed:false.  admin/staff bypass: always allowed without touching D1.
 */
async function reserveAuthQuota(
  d1: D1Database,
  userId: string,
  tier: string,
  role: string,
): Promise<{ allowed: boolean; count: number; limit: number }> {
  if (role === 'admin' || role === 'staff') {
    return { allowed: true, count: 0, limit: 999_999 };
  }

  // noUncheckedIndexedAccess: Record indexing gives number | undefined; fall back to 20
  const limit: number = MONTHLY_LIMITS[tier] ?? 20;
  const period = currentPeriod();
  const now = Math.floor(Date.now() / 1000);
  const rowId = `${userId}:${period}`;

  // Single atomic UPSERT — safe under concurrent Workers isolates
  const result = await d1.prepare(`
    INSERT INTO quota_usage (id, user_id, period, count, updated_at)
    VALUES (?, ?, ?, 1, ?)
    ON CONFLICT (user_id, period) DO UPDATE
      SET count = quota_usage.count + 1, updated_at = excluded.updated_at
    RETURNING count
  `).bind(rowId, userId, period, now).first<{ count: number }>();

  const newCount = result?.count ?? 1;

  if (newCount > limit) {
    // Roll back — keep the row consistent; don't expose a slot that won't be used
    await d1.prepare(
      'UPDATE quota_usage SET count = count - 1, updated_at = ? WHERE user_id = ? AND period = ? AND count > 0',
    ).bind(now, userId, period).run();
    return { allowed: false, count: newCount - 1, limit };
  }

  // Slot reserved; expose the pre-increment count so callers can display "messages used"
  return { allowed: true, count: newCount - 1, limit };
}

/**
 * Reserve one quota slot for an anonymous user before invoking the LLM.
 * KV does not support atomic CAS, so we read → write before the LLM call to
 * shrink the race window from the full LLM latency (~3-10 s) to the KV round-trip
 * (~ms). Slight over-counting is possible but bounded and acceptable for anon users.
 */
async function reserveAnonQuota(
  kv: KVNamespace,
  anonId: string,
): Promise<{ allowed: boolean; count: number; limit: number }> {
  // noUncheckedIndexedAccess: use literal fallback, not MONTHLY_LIMITS['free']
  const limit: number = MONTHLY_LIMITS['free'] ?? 20;
  const period = currentPeriod();
  const key = `anon_quota:${anonId}:${period}`;
  const val = await kv.get(key);
  const count = val ? parseInt(val, 10) : 0;

  if (count >= limit) {
    return { allowed: false, count, limit };
  }

  // Reserve slot before LLM — expire at start of the month after next
  const now = new Date();
  const endOfNextMonth = new Date(now.getUTCFullYear(), now.getUTCMonth() + 2, 1);
  const ttl = Math.max(3600, Math.floor((endOfNextMonth.getTime() - now.getTime()) / 1000));
  await kv.put(key, String(count + 1), { expirationTtl: ttl });

  return { allowed: true, count, limit };
}

/**
 * Update per-user lifetime stats in the users table after a successful stream.
 * quota_usage was already incremented atomically in reserveAuthQuota before streaming,
 * so only the denormalised users counters need updating here.
 */
async function updateAuthStats(d1: D1Database, userId: string): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  await d1.prepare(`
    UPDATE users
    SET monthly_message_count   = monthly_message_count + 1,
        total_lifetime_messages = total_lifetime_messages + 1,
        updated_at              = ?
    WHERE id = ?
  `).bind(now, userId).run();
}

// ─────────────────────────────────────────────────────────────────────────────
// RAG retrieval
// ─────────────────────────────────────────────────────────────────────────────

/** Embed a query string using Workers AI @cf/baai/bge-m3. */
async function embedQuery(ai: Ai, text: string): Promise<number[]> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const result = await ai.run('@cf/baai/bge-m3' as any, { text: [text] });
  // bge-m3 returns { data: [{ values: number[], shape: number[] }] }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const data = (result as any).data as { values: number[] }[] | undefined;
  if (!data?.[0]?.values?.length) throw new Error('bge-m3 returned no embedding');
  return data[0].values;
}

/** Query Vectorize, filtered by medium and optional metadata fields. */
async function queryVectorize(
  vectorize: VectorizeIndex,
  embedding: number[],
  lang: 'en' | 'as',
  extraFilters: Record<string, string>,
): Promise<VectorizeMatch[]> {
  const medium = lang === 'as' ? 'assamese' : 'english';
  const filter: Record<string, string> = { medium, ...extraFilters };

  const result = await vectorize.query(embedding, {
    topK: 8,
    returnMetadata: 'all',
    filter,
  });

  return (result.matches ?? []).filter((m: VectorizeMatch) => m.score >= CONFIDENCE_LOW);
}

/**
 * Fetch full chapter text from D1 using the confidence-aware fallback chain:
 *   ragSectionsEn/As → ragText/As → notesEn/As → English fallback for Assamese
 *
 * Mirrors the Python ChatService.retrieve_context_from_chapter() fallback chain
 * documented in syrabit-rag-v2.md and rag-field-priority.md.
 */
async function fetchChapterContent(
  db: ReturnType<typeof createDb>,
  chapterId: string,
  lang: 'en' | 'as',
): Promise<string | null> {
  const row = await db
    .select({
      ragSectionsEn: chaptersTable.ragSectionsEn,
      ragSectionsAs: chaptersTable.ragSectionsAs,
      ragText:       chaptersTable.ragText,
      ragTextAs:     chaptersTable.ragTextAs,
      notesEn:       chaptersTable.notesEn,
      notesAs:       chaptersTable.notesAs,
      title:         chaptersTable.title,
    })
    .from(chaptersTable)
    .where(eq(chaptersTable.id, chapterId))
    .get();

  if (!row) return null;

  if (lang === 'as') {
    // Assamese fallback chain
    const sections = tryJson<RagSection[]>(row.ragSectionsAs, []);
    if (sections.length > 0) return sections.map(s => s.content).join('\n\n');
    if (row.ragTextAs) return row.ragTextAs;
    if (row.notesAs)   return row.notesAs;
    // Fall through to English when Assamese content is missing
  }

  // English fallback chain
  const sections = tryJson<RagSection[]>(row.ragSectionsEn, []);
  if (sections.length > 0) return sections.map(s => s.content).join('\n\n');
  if (row.ragText) return row.ragText;
  if (row.notesEn) return row.notesEn;
  return null;
}

// ─────────────────────────────────────────────────────────────────────────────
// Conversation history
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Load the last N messages for a session from D1, formatted for the system prompt.
 *
 * SECURITY: always filter by both sessionId AND userId so a caller who supplies
 * an arbitrary session_id cannot read another user's conversation history.
 * Anon users are keyed by their stable IP-derived hash so ownership still holds.
 */
async function loadHistory(
  db: ReturnType<typeof createDb>,
  sessionId: string | null | undefined,
  userId: string | null | undefined,
): Promise<string> {
  // Require both — an unknown session or identity gets an empty history
  if (!sessionId || !userId) return '';

  const rows = await db
    .select({ role: chats.role, content: chats.content })
    .from(chats)
    .where(and(eq(chats.sessionId, sessionId), eq(chats.userId, userId)))
    .orderBy(desc(chats.createdAt))
    .limit(HISTORY_MSG_CAP)
    .all();

  if (rows.length === 0) return '';

  return rows
    .reverse()
    .map(r => `${r.role === 'user' ? 'Student' : 'Syrabit'}: ${r.content.slice(0, 500)}`)
    .join('\n');
}

// ─────────────────────────────────────────────────────────────────────────────
// System prompt
// ─────────────────────────────────────────────────────────────────────────────

function buildSystemPrompt(opts: {
  lang: 'en' | 'as';
  contextText: string;
  history: string;
  // exactOptionalPropertyTypes: explicit | undefined so callers can pass string | undefined
  boardName?: string | undefined;
  className?: string | undefined;
  subjectName?: string | undefined;
  chapterName?: string | undefined;
  question: string;
}): string {
  const { lang, contextText, history, boardName, className, subjectName, chapterName, question } = opts;
  const boardInfo = [boardName, className].filter(Boolean).join(', ');
  const hasCtx = contextText.trim().length > 0;
  const hasHistory = history.trim().length > 0;

  if (lang === 'as') {
    const lines = [
      `তুমি Syrabit AI, এজন বিশেষজ্ঞ শিক্ষা সহায়ক${boardInfo ? ` (${boardInfo})` : ''}।`,
    ];
    if (subjectName) lines.push(`বিষয়: ${subjectName}${chapterName ? `, অধ্যায়: ${chapterName}` : ''}`);
    lines.push('');
    if (hasCtx) {
      lines.push('## পাঠ্যক্রমৰ প্ৰসংগ');
      lines.push('তলৰ পাঠ্যক্রম সামগ্ৰী ব্যৱহাৰ কৰি সঠিক উত্তৰ দিয়া:');
      lines.push('');
      lines.push(contextText);
      lines.push('');
    }
    if (hasHistory) {
      lines.push('## আগৰ কথোপকথন');
      lines.push(history);
      lines.push('');
    }
    lines.push(
      '## নিৰ্দেশনা',
      '- সম্পূৰ্ণ অসমীয়াত উত্তৰ দিয়া।',
      '- পাঠ্যক্রমৰ প্ৰসংগ থাকিলে তাৰ ওপৰত ভিত্তি কৰি উত্তৰ দিয়া।',
      '- চমু, স্পষ্ট আৰু সহজ ভাষা ব্যৱহাৰ কৰা।',
      '- নিশ্চিত নহ\'লে সেইটো কোৱা।',
      '',
      `## ছাত্ৰৰ প্ৰশ্ন`,
      question,
    );
    return lines.join('\n');
  }

  // English
  const lines = [
    `You are Syrabit AI, an expert educational assistant for Indian board exam students${boardInfo ? ` (${boardInfo})` : ''}.`,
  ];
  if (subjectName) lines.push(`Subject: ${subjectName}${chapterName ? `, Chapter: ${chapterName}` : ''}`);
  lines.push('');
  if (hasCtx) {
    lines.push('## Curriculum Context');
    lines.push('Use the following curriculum content to answer accurately. Prefer this over general knowledge:');
    lines.push('');
    lines.push(contextText);
    lines.push('');
  }
  if (hasHistory) {
    lines.push('## Conversation History');
    lines.push(history);
    lines.push('');
  }
  lines.push(
    '## Instructions',
    '- Answer clearly and concisely. Use the curriculum context above when available.',
    '- Align answers with Indian board exam syllabus and expected formats.',
    '- Break complex concepts into simple, numbered steps.',
    '- If unsure, say so rather than hallucinating.',
    '',
    '## Student Question',
    question,
  );
  return lines.join('\n');
}

// ─────────────────────────────────────────────────────────────────────────────
// Chat persistence
// ─────────────────────────────────────────────────────────────────────────────

async function saveChat(
  d1: D1Database,
  opts: {
    userId: string;
    sessionId: string;
    userMessage: string;
    assistantResponse: string;
    lang: 'en' | 'as';
    modelUsed: string;
    // exactOptionalPropertyTypes: explicit | undefined so callers can pass string | undefined
    chapterId?: string | undefined;
    subjectId?: string | undefined;
  },
): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  const expiresAt = now + 90 * 24 * 3600; // 90-day TTL (cleaned by cron)

  const userMsgId  = crypto.randomUUID();
  const assistId   = crypto.randomUUID();
  const sid        = opts.sessionId;
  const uid        = opts.userId;
  const lang       = opts.lang;
  const chId       = opts.chapterId ?? null;
  const subId      = opts.subjectId ?? null;

  // Insert user message and assistant response in a single batch
  await d1.batch([
    d1.prepare(`
      INSERT INTO chats (id, user_id, session_id, role, content, lang, chapter_id, subject_id, expires_at, created_at)
      VALUES (?, ?, ?, 'user', ?, ?, ?, ?, ?, ?)
    `).bind(userMsgId, uid, sid, opts.userMessage.slice(0, 4000), lang, chId, subId, expiresAt, now),

    d1.prepare(`
      INSERT INTO chats (id, user_id, session_id, role, content, lang, chapter_id, subject_id, metadata, expires_at, created_at)
      VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?, ?, ?, ?)
    `).bind(assistId, uid, sid, opts.assistantResponse.slice(0, 8000), lang, chId, subId, JSON.stringify({ model: opts.modelUsed }), expiresAt, now + 1),
  ]);
}

// ─────────────────────────────────────────────────────────────────────────────
// Router
// ─────────────────────────────────────────────────────────────────────────────

export const chatRouter = new Hono<{ Bindings: Env }>();

chatRouter.post('/stream', async (c) => {
  const startTime = Date.now();

  // ── 1. Parse & validate body ────────────────────────────────────────────────
  let body: ChatRequest;
  try {
    body = await c.req.json<ChatRequest>();
  } catch {
    return c.json({ detail: 'Invalid JSON body' }, 400);
  }

  const rawMessage = (body.message ?? '').trim();
  if (!rawMessage)           return c.json({ detail: 'message is required' }, 422);
  if (rawMessage.length > 2000) return c.json({ detail: 'message must not exceed 2000 characters' }, 422);
  const message = sanitize(rawMessage);

  // Coalesce session_id / conversation_id (frontend sends conversation_id)
  const sessionId = body.session_id ?? body.conversation_id ?? null;

  // ── 2. Auth (optional) ──────────────────────────────────────────────────────
  let userId: string;
  let authedUserId: string | null = null;
  let userTier = 'free';
  let userRole = 'student';
  let isAnon   = true;

  const token = extractBearer(c.req.header('Authorization') ?? null);
  if (token) {
    const payload = await verifyToken(token, c.env.JWT_SECRET);
    // Require an access token specifically — reject refresh tokens
    if (payload?.sub && payload.type === 'access') {
      // Load fresh user from D1 to catch deleted/deactivated accounts.
      // If the user row is missing we fall through to anon rather than
      // continuing as an authenticated user with a stale token.
      const db = createDb(c.env.DB);
      const row = await db
        .select({ subscriptionTier: users.subscriptionTier, role: users.role, deletedAt: users.deletedAt })
        .from(users)
        .where(eq(users.id, payload.sub))
        .get();

      // Reject soft-deleted users (deletedAt non-null) as well as missing rows
      if (row && !row.deletedAt) {
        authedUserId = payload.sub;
        userId       = payload.sub;
        userTier     = row.subscriptionTier ?? 'free';
        userRole     = row.role ?? 'student';
        isAnon       = false;
      } else {
        // User deleted or not found — treat as anon
        userId = anonUserId(c.req.raw);
      }
    } else {
      // Expired / invalid / non-access token → treat as anon
      userId = anonUserId(c.req.raw);
    }
  } else {
    userId = anonUserId(c.req.raw);
  }

  // ── 3. Language detection ────────────────────────────────────────────────────
  const lang = detectLang(message, body.lang);

  // ── 4. Quota — atomic pre-reservation before the LLM call ──────────────────
  // We reserve the slot here (increment before streaming) so that concurrent
  // requests cannot all pass the same count. If the limit is exceeded the
  // increment is rolled back and we return 429 without touching the LLM.
  let quotaCount: number;
  let quotaLimit: number;
  let quotaAllowed: boolean;

  if (!isAnon) {
    ({ allowed: quotaAllowed, count: quotaCount, limit: quotaLimit } =
      await reserveAuthQuota(c.env.DB, userId, userTier, userRole));
  } else {
    ({ allowed: quotaAllowed, count: quotaCount, limit: quotaLimit } =
      await reserveAnonQuota(c.env.RATE_LIMIT_KV, userId));
  }

  if (!quotaAllowed) {
    return c.json(
      {
        detail: 'Monthly message limit reached. Upgrade to Pro for more messages.',
        quota: { used: quotaCount, limit: quotaLimit },
      },
      429,
    );
  }

  // Helper to release a reserved quota slot on failure paths.
  // The same decrement logic as in reserveAuthQuota's rollback.
  const releaseQuota = (): Promise<void> => {
    const period = currentPeriod();
    const now    = Math.floor(Date.now() / 1000);
    if (isAnon) {
      const key = `anon_quota:${userId}:${period}`;
      return c.env.RATE_LIMIT_KV.get(key).then((val) => {
        const count = Math.max(0, (val ? parseInt(val, 10) : 1) - 1);
        const endOfNextMonth = new Date(
          new Date().getUTCFullYear(),
          new Date().getUTCMonth() + 2,
          1,
        );
        const ttl = Math.max(3600, Math.floor((endOfNextMonth.getTime() - Date.now()) / 1000));
        return c.env.RATE_LIMIT_KV.put(key, String(count), { expirationTtl: ttl });
      });
    }
    return c.env.DB.prepare(
      'UPDATE quota_usage SET count = count - 1, updated_at = ? WHERE user_id = ? AND period = ? AND count > 0',
    ).bind(now, userId, period).run().then(() => undefined);
  };

  // ── 5. RAG: embed + Vectorize + D1 chapter content ─────────────────────────
  const db = createDb(c.env.DB);

  let contextChunks: ContextChunk[] = [];
  let confidenceTier = 'none';
  let topScore       = 0;
  let ragPath        = 'none';
  let topChapterId: string | undefined;
  let topChapterTitle: string | undefined;
  let topSubjectId: string | undefined;
  let history = '';

  // Embed + history in parallel — zero extra latency vs serial
  // Pass userId so history is scoped to its owner (session ownership enforcement)
  const [embedResult, historyResult] = await Promise.allSettled([
    embedQuery(c.env.AI, message),
    loadHistory(db, sessionId, userId),
  ]);

  if (historyResult.status === 'fulfilled') history = historyResult.value;

  if (embedResult.status === 'fulfilled') {
    const embedding = embedResult.value;

    try {
      // Metadata filters to prevent cross-board content leakage
      // Only filter by fields that are actually indexed in Vectorize ingestion:
      // subjectId, chapterId, topicId, medium, sourceType, chunkType.
      // boardId and classId are NOT in the production metadata index — adding
      // them here would cause the query to fail or return 0 matches silently.
      const extraFilters: Record<string, string> = {};
      if (body.chapter_id) extraFilters['chapterId'] = body.chapter_id;
      if (body.subject_id) extraFilters['subjectId'] = body.subject_id;

      const matches = await queryVectorize(c.env.VECTORIZE, embedding, lang, extraFilters);

      // noUncheckedIndexedAccess: array[0] is T | undefined; guard before access
      const firstMatch = matches[0];
      if (firstMatch !== undefined && matches.length > 0) {
        topScore = firstMatch.score;

        // Confidence tier assignment
        if (topScore >= CONFIDENCE_HIGH)     confidenceTier = 'high';
        else if (topScore >= CONFIDENCE_LOW) confidenceTier = 'low';

        // Group by chapterId and pick the chapter with the highest max score
        const byChapter = new Map<string, { score: number; meta: ChunkMeta }>();
        for (const m of matches) {
          const meta = m.metadata as ChunkMeta;
          const cid = meta?.chapterId;
          if (!cid) continue;
          const existing = byChapter.get(cid);
          if (!existing || m.score > existing.score) {
            byChapter.set(cid, { score: m.score, meta });
          }
        }

        const sorted = [...byChapter.entries()].sort((a, b) => b[1].score - a[1].score);
        // noUncheckedIndexedAccess: sorted[0] is [...] | undefined; guard with at()
        const topEntry = sorted.at(0);
        if (topEntry !== undefined) {
          const [bestId, best] = topEntry;
          topChapterId = bestId;
          topSubjectId = best.meta.subjectId;

          // D1 fast path — full chapter content with fallback chain
          const content = await fetchChapterContent(db, bestId, lang);
          if (content) {
            const resolvedTitle = best.meta.chapterTitle ?? bestId;
            topChapterTitle = resolvedTitle;
            contextChunks = [{
              chapterId:    bestId,
              chapterTitle: resolvedTitle,
              ...(topSubjectId !== undefined && { subjectId: topSubjectId }),
              content:      content.slice(0, CONTEXT_CHAR_CAP),
              score:        best.score,
            }];
            ragPath = 'vectorize_d1';
          }
        }
      }
    } catch (err) {
      console.error('[chat] RAG retrieval error:', err);
      // Non-fatal: continue without context
    }
  } else {
    console.warn('[chat] Embedding failed:', embedResult.reason);
  }

  // Card-context fallback — when RAG missed but chapter_id provided by frontend
  if (contextChunks.length === 0 && body.chapter_id) {
    try {
      const content = await fetchChapterContent(db, body.chapter_id, lang);
      if (content) {
        topChapterId    = body.chapter_id;
        topChapterTitle = body.chapter_name;
        topSubjectId    = body.subject_id;
        contextChunks   = [{
          chapterId:    body.chapter_id,
          chapterTitle: body.chapter_name ?? body.chapter_id,
          // exactOptionalPropertyTypes: spread only when defined
          ...(body.subject_id !== undefined && { subjectId: body.subject_id }),
          content:      content.slice(0, CONTEXT_CHAR_CAP),
          score:        0.5,
        }];
        ragPath        = 'card_context';
        confidenceTier = 'low';
        topScore       = 0.5;
      }
    } catch (err) {
      console.warn('[chat] Card-context fallback error:', err);
    }
  }

  // ── 6. System prompt ────────────────────────────────────────────────────────
  const contextText = contextChunks
    .map((chunk, i) => `[Source ${i + 1}: ${chunk.chapterTitle}]\n${chunk.content}`)
    .join('\n\n---\n\n');

  // exactOptionalPropertyTypes: spread optional fields only when they have a value
  const chapterNameResolved = body.chapter_name ?? topChapterTitle;
  const systemPrompt = buildSystemPrompt({
    lang,
    contextText,
    history,
    ...(body.board_name        !== undefined && { boardName:   body.board_name }),
    ...(body.class_name        !== undefined && { className:   body.class_name }),
    ...(chapterNameResolved    !== undefined && { chapterName: chapterNameResolved }),
    question: message,
  });

  // ── 7. Session ID — mint before streaming so the frontend can adopt it ───────
  // The frontend sends conversation_id: null for new conversations and adopts
  // the ID from the first SSE event. We must mint here (not in waitUntil) so
  // history and persistence both use the same ID and the client learns it early.
  const effectiveSessionId: string = sessionId ?? crypto.randomUUID();

  // ── 8. Source card (emitted as the very first SSE event) ────────────────────
  // Always emitted — even for llm_only responses — so the client consistently
  // receives conversation_id and can establish the session before any tokens.
  const sourceCard = {
    event:            'source_card',
    conversation_id:  effectiveSessionId,
    source_type:      contextChunks.length > 0 ? 'rag_chapter' : 'llm_only',
    rag_path:         ragPath,
    confidence_tier:  confidenceTier,
    match_score:      topScore,
    rag_chunks:       contextChunks.length,
    rag_chapter_name: topChapterTitle,
    rag_subject_id:   topSubjectId,
    ctx_board_name:   body.board_name,
    ctx_class_level:  body.class_name,
  };

  // ── 8. SSE via TransformStream + waitUntil ──────────────────────────────────
  const { readable, writable } = new TransformStream<Uint8Array, Uint8Array>();
  const writer  = writable.getWriter();
  const encoder = new TextEncoder();

  const write = (payload: unknown) =>
    writer.write(encoder.encode(sseEvent(payload)));

  const streamTask = (async () => {
    let fullResponse = '';
    let actualModel  = AI_MODEL_PRIMARY;

    try {
      // Always emit source_card first — client uses this to learn the conversation_id
      await write(sourceCard);

      // ── Stream via Workers AI (primary → fallback handled by service) ──────
      let streamDone = false;

      try {
        for await (const chunk of streamGenerate(c.env.AI, {
          systemPrompt,
          userMessage: message,
          maxTokens: 2048,
        })) {
          // Sentinel chunk carries the resolved model name — do not forward to client
          if (chunk.startsWith('\x00model:')) {
            actualModel = chunk.slice(7);
            continue;
          }
          fullResponse += chunk;
          await write({ content: chunk, done: false });
        }
        streamDone = true;
      } catch (streamErr) {
        console.warn('[chat] streamGenerate failed:', streamErr);
        throw streamErr;
      }

      if (!streamDone || !fullResponse) {
        // Provider returned an empty response — release the reserved slot
        await write({ content: '', done: true, error: 'Empty response from AI. Please try again.' });
        await releaseQuota().catch((e) => console.error('[chat] quota release failed:', e));
        return;
      }

      // ── syrabit_done event ────────────────────────────────────────────────
      const latencyMs = Date.now() - startTime;
      await write({
        content:              '',
        done:                 true,
        event:                'syrabit_done',
        latency_ms:           latencyMs,
        model:                actualModel,
        lang,
        credits_used_total:   quotaCount + 1,
        remaining_credits:    Math.max(0, quotaLimit - quotaCount - 1),
        route_trace: {
          lang,
          model:            actualModel,
          fallback:         actualModel !== AI_MODEL_PRIMARY,
          confidence_tier:  confidenceTier,
          topic_score:      Math.round(topScore * 10000) / 10000,
          rag_path:         ragPath,
          rag_chunks:       contextChunks.length,
          matched_chapter:  topChapterTitle,
          matched_subject:  topSubjectId,
          web_used:         false,
        },
      });

      // ── Fire-and-forget: persist chat + update user stats ────────────────
      // quota_usage was already incremented atomically in reserveAuthQuota /
      // reserveAnonQuota before streaming — do not increment again here.
      await Promise.allSettled([
        saveChat(c.env.DB, {
          userId,
          sessionId:         effectiveSessionId,
          userMessage:       message,
          assistantResponse: fullResponse,
          lang,
          modelUsed:   actualModel,
          chapterId:   topChapterId ?? body.chapter_id,
          subjectId:   topSubjectId ?? body.subject_id,
        }),
        // Anon users have no users table row to update
        ...(isAnon ? [] : [updateAuthStats(c.env.DB, userId)]),
      ]);

    } catch (err) {
      console.error('[chat] Stream pipeline error:', err);
      try {
        await write({
          error: 'AI service temporarily unavailable. Please try again.',
          done: true,
        });
      } catch { /* writer may already be closed */ }
      // Release the reserved slot — provider/config errors must not consume quota
      await releaseQuota().catch((e) => console.error('[chat] quota release failed:', e));
    }
  })();

  // Register with Workers runtime so the isolate stays alive until streaming completes
  c.executionCtx.waitUntil(
    streamTask.finally(() => writer.close().catch(() => {})),
  );

  return new Response(readable, {
    status: 200,
    headers: {
      'Content-Type':           'text/event-stream',
      'Cache-Control':          'no-store',
      'X-Accel-Buffering':      'no',
      'X-Content-Type-Options': 'nosniff',
    },
  });
});
