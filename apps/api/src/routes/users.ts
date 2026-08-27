/**
 * User profile routes — D1-backed, auth required.
 *
 * Implements the API contract served by Cloud Run at /api/v1/users/* and
 * /api/v1/user/* (alias). Covers GET/PATCH profile, onboarding, memories,
 * stats, and credits — returning the same field shapes the frontend expects.
 *
 * Routes implemented here (Phase 6):
 *   GET  /me               → full profile (same as /profile)
 *   GET  /profile          → full profile
 *   PATCH /profile         → update name / language / board / ads preference
 *   PUT  /me               → update name / language (legacy)
 *   POST /onboarding       → save onboarding preferences
 *   GET  /memories         → paginated memory list
 *   DELETE /memories       → delete all memories
 *   DELETE /memories/:id   → delete single memory
 *   GET  /stats            → usage stats
 *   GET  /credits          → credit balance
 *   POST /saved-subjects/:id → toggle saved subject
 *
 * Routes still on Cloud Run fallback (Phase 7):
 *   DELETE /me, /account   — account deletion (needs GDPR cascade across
 *                            chats, feedback, dead-letters, hard-delete job)
 *   POST /account/cancel-delete — same cascade dependency
 */

import { Hono, type Context } from 'hono';
import { eq, and, like, sql } from 'drizzle-orm';
import { createDb } from '../db/client';
import { users, chats, memoryBrain } from '../db/schema';
import { isSessionValid, verifyToken, extractBearer } from '../middleware/auth';
import { ANONYMOUS_MONTHLY_LIMIT, anonUserId, anonymousQuotaKey } from '../services/anonymous';
import type { Env } from '../types';

export const usersRouter = new Hono<{ Bindings: Env }>();

// Credit limits — authoritative, must match billing pipeline
const CREDITS_LIMITS: Record<string, number> = {
  free: ANONYMOUS_MONTHLY_LIMIT,
  starter: 500,
  pro: 7000,
  premium: 9999,
};

// ── Auth middleware ────────────────────────────────────────────────────────────

async function requireUser(
  c: Context<{ Bindings: Env }>,
): Promise<{ id: string; error?: Response }> {
  const token = extractBearer(c.req.header('Authorization') ?? null);
  if (!token) return { id: '', error: c.json({ detail: 'Not authenticated' }, 401) as Response };

  const payload = await verifyToken(token, c.env.JWT_SECRET);
  if (!payload || payload.type !== 'access') {
    return { id: '', error: c.json({ detail: 'Invalid or expired token' }, 401) as Response };
  }
  if (!(await isSessionValid(c.env.DB, payload.sub ?? '', payload.iat))) {
    return { id: '', error: c.json({ detail: 'Session expired after password change. Sign in again.' }, 401) as Response };
  }
  return { id: payload.sub! };
}

// ── Build profile response (matches Cloud Run _build_profile_response) ─────────

function buildProfileResponse(user: typeof users.$inferSelect): Record<string, unknown> {
  const tier = user.subscriptionTier ?? 'free';
  const creditsLimit = CREDITS_LIMITS[tier] ?? 30; // 30 = free tier default
  const creditsUsed  = user.creditsUsed ?? 0;
  const creditsRemaining = user.creditsRemaining != null
    ? user.creditsRemaining
    : Math.max(0, creditsLimit - creditsUsed);

  let status = 'active';
  let deletionHardAt: string | null = null;
  if (user.deletedAt) {
    status = 'pending_deletion';
    // Soft-delete grace: 72h after scheduled
    deletionHardAt = new Date((user.deletedAt + 72 * 3600) * 1000).toISOString();
  }

  let savedSubjects: string[] = [];
  try { savedSubjects = JSON.parse(user.savedSubjects ?? '[]') as string[]; } catch { /* leave empty */ }

  return {
    id:                    user.id,
    name:                  user.name ?? '',
    email:                 user.email ?? '',
    role:                  user.role,
    subscription_tier:     tier,
    plan:                  tier,             // alias expected by frontend
    monthly_message_count: user.monthlyMessageCount,
    preferred_language:    user.preferredLanguage,
    onboarding_done:       Boolean(user.onboardingDone),
    ads_opt_out:           Boolean(user.adsOptOut),
    saved_subjects:        savedSubjects,
    phone:                 user.phone ?? null,
    board_id:              user.boardId ?? null,
    board_name:            user.boardName ?? null,
    class_id:              user.classId ?? null,
    class_name:            user.className ?? null,
    stream_id:             user.streamId ?? null,
    stream_name:           user.streamName ?? null,
    credits_used:          creditsUsed,
    credits_limit:         creditsLimit,
    credits_remaining:     creditsRemaining,
    status,
    deletion_hard_at:      deletionHardAt,
  };
}

// ── GET /me ────────────────────────────────────────────────────────────────────
// ── GET /profile ───────────────────────────────────────────────────────────────

async function getProfile(
  c: Context<{ Bindings: Env }>,
): Promise<Response> {
  const { id, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);
  const user = await db.select().from(users).where(eq(users.id, id)).get();
  if (!user || user.deletedAt) return c.json({ detail: 'User not found' }, 404) as Response;

  return c.json(buildProfileResponse(user)) as Response;
}

usersRouter.get('/me',      getProfile);
usersRouter.get('/profile', getProfile);

// ── PUT /me (legacy update) ────────────────────────────────────────────────────

usersRouter.put('/me', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);
  let body: { name?: string; preferred_language?: string };
  try { body = await c.req.json() as typeof body; } catch { return c.json({ detail: 'Invalid JSON' }, 400); }

  const updates: Partial<typeof users.$inferInsert> = { updatedAt: Math.floor(Date.now() / 1000) };
  if (body.name)               updates.name = body.name;
  if (body.preferred_language) updates.preferredLanguage = body.preferred_language;

  if (Object.keys(updates).length > 1) {
    await db.update(users).set(updates).where(eq(users.id, id));
  }
  return c.json({ status: 'success', message: 'Profile updated' });
});

// ── PATCH /profile ─────────────────────────────────────────────────────────────

usersRouter.patch('/profile', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);
  let body: {
    name?: string;
    preferred_language?: string;
    ads_opt_out?: boolean;
    board_id?: string;
    board_name?: string;
    class_id?: string;
    class_name?: string;
    stream_id?: string;
    stream_name?: string;
    phone?: string;
  };
  try { body = await c.req.json() as typeof body; } catch { return c.json({ detail: 'Invalid JSON' }, 400); }

  const updates: Partial<typeof users.$inferInsert> = { updatedAt: Math.floor(Date.now() / 1000) };
  if (body.name              != null) updates.name              = body.name;
  if (body.preferred_language != null) updates.preferredLanguage = body.preferred_language;
  if (body.ads_opt_out       != null) updates.adsOptOut         = body.ads_opt_out ? 1 : 0;
  if (body.board_id          != null) updates.boardId           = body.board_id;
  if (body.board_name        != null) updates.boardName         = body.board_name;
  if (body.class_id          != null) updates.classId           = body.class_id;
  if (body.class_name        != null) updates.className         = body.class_name;
  if (body.stream_id         != null) updates.streamId          = body.stream_id;
  if (body.stream_name       != null) updates.streamName        = body.stream_name;
  if (body.phone             != null) updates.phone             = body.phone;

  if (Object.keys(updates).length > 1) {
    await db.update(users).set(updates).where(eq(users.id, id));
  }
  return c.json({ status: 'success', message: 'Profile updated' });
});

// ── POST /onboarding ───────────────────────────────────────────────────────────

usersRouter.post('/onboarding', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);
  let body: { language?: string; grade?: string; board?: string; stream?: string };
  try { body = await c.req.json() as typeof body; } catch { return c.json({ detail: 'Invalid JSON' }, 400); }

  const updates: Partial<typeof users.$inferInsert> = {
    onboardingDone: 1,
    updatedAt: Math.floor(Date.now() / 1000),
  };
  if (body.language) updates.preferredLanguage = body.language;
  if (body.grade)    updates.grade = body.grade;

  await db.update(users).set(updates).where(eq(users.id, id));
  return c.json({ status: 'success', message: 'Onboarding preferences saved' });
});

// ── GET /memories ──────────────────────────────────────────────────────────────
// Returns paginated memory list from the memory_brain table.

usersRouter.get('/memories', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);
  const page = Math.max(1, parseInt(c.req.query('page') ?? '1', 10));
  const limit = Math.min(50, Math.max(1, parseInt(c.req.query('limit') ?? '20', 10)));
  const q = c.req.query('q');

  let condition = eq(memoryBrain.userId, id);
  if (q) {
    condition = and(condition, like(memoryBrain.value, `%${q}%`))!;
  }

  const total = await db.select({ n: sql<number>`COUNT(*)` })
    .from(memoryBrain).where(condition).get();
  const totalCount = total?.n ?? 0;
  const pages = Math.ceil(totalCount / limit);

  const rows = await db.select({
    id: memoryBrain.id,
    key: memoryBrain.key,
    value: memoryBrain.value,
    updatedAt: memoryBrain.updatedAt,
  }).from(memoryBrain)
    .where(condition)
    .limit(limit)
    .offset((page - 1) * limit);

  return c.json({
    items: rows.map(r => ({
      id: r.id,
      text: r.value ?? '',
      kind: r.key,
      subject_id: null,
      subject_name: null,
      chapter_name: null,
      event: null,
      created_at: r.updatedAt
        ? new Date(r.updatedAt * 1000).toISOString()
        : null,
    })),
    total: totalCount,
    has_more: page < pages,
    page,
    pages,
  });
});

// ── DELETE /memories ───────────────────────────────────────────────────────────

usersRouter.delete('/memories', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);
  const result = await db.delete(memoryBrain).where(eq(memoryBrain.userId, id));
  const deleted = result.meta?.changes ?? 0;
  return c.json({ status: 'success', deleted });
});

// ── DELETE /memories/:id ───────────────────────────────────────────────────────

usersRouter.delete('/memories/:memId', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);
  const memId = c.req.param('memId');
  const result = await db.delete(memoryBrain)
    .where(and(eq(memoryBrain.id, memId), eq(memoryBrain.userId, id)));

  if ((result.meta?.changes ?? 0) === 0) {
    return c.json({ detail: 'Memory not found' }, 404);
  }
  return c.json({ status: 'success', message: 'Memory deleted' });
});

// ── GET /stats ─────────────────────────────────────────────────────────────────

usersRouter.get('/stats', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);

  const user = await db.select({
    savedSubjects: users.savedSubjects,
    totalTokensUsed: users.totalTokensUsed,
    creditsUsed: users.creditsUsed,
  }).from(users).where(eq(users.id, id)).get();

  if (!user) return c.json({ detail: 'User not found' }, 404);

  // Count distinct sessions as "conversations"
  const convCount = await db.select({ n: sql<number>`COUNT(DISTINCT session_id)` })
    .from(chats)
    .where(eq(chats.userId, id))
    .get();

  let savedSubjects: string[] = [];
  try { savedSubjects = JSON.parse(user.savedSubjects ?? '[]') as string[]; } catch { /* leave empty */ }

  return c.json({
    conversations:   convCount?.n ?? 0,
    saved_subjects:  savedSubjects.length,
    total_tokens:    user.totalTokensUsed ?? 0,
    credits_used:    user.creditsUsed ?? 0,
  });
});

// ── GET /credits ───────────────────────────────────────────────────────────────

usersRouter.get('/credits', async (c) => {
  // Optional auth — anonymous users read the same KV quota reserved by chat.
  const authHeader = c.req.header('Authorization');
  const token = extractBearer(authHeader ?? null);
  let tier = 'free';
  let creditsRemaining = 0;
  let creditsUsed = 0;
  let anonymousId: string | null = null;
  let authenticated = false;

  if (token) {
    const payload = await verifyToken(token, c.env.JWT_SECRET);
    if (payload?.type === 'access' && payload.sub
      && await isSessionValid(c.env.DB, payload.sub, payload.iat)) {
      const db = createDb(c.env.DB);
      const user = await db.select({
        subscriptionTier: users.subscriptionTier,
        creditsRemaining: users.creditsRemaining,
        creditsUsed: users.creditsUsed,
      }).from(users).where(eq(users.id, payload.sub)).get();

      if (user) {
        authenticated = true;
        tier = user.subscriptionTier ?? 'free';
        creditsUsed = user.creditsUsed ?? 0;
        const limit = CREDITS_LIMITS[tier] ?? CREDITS_LIMITS.free;
        creditsRemaining = user.creditsRemaining != null
          ? user.creditsRemaining
          : Math.max(0, (limit ?? 30) - creditsUsed);
      }
    }
  }

  // Match chat's optional-auth behavior: a stale or invalid token is treated
  // as anonymous, and therefore still resolves the browser's persistent ID.
  if (!authenticated) {
    anonymousId = anonUserId(c.req.raw);
    const rawCount = await c.env.RATE_LIMIT_KV.get(anonymousQuotaKey(anonymousId));
    const parsedCount = rawCount ? Number.parseInt(rawCount, 10) : 0;
    creditsUsed = Number.isFinite(parsedCount) && parsedCount > 0 ? parsedCount : 0;
    const limit = CREDITS_LIMITS.free ?? 30;
    creditsRemaining = Math.max(0, limit - creditsUsed);
  }

  const monthlyLimit = CREDITS_LIMITS[tier] ?? CREDITS_LIMITS.free;
  return c.json({
    credits_remaining: creditsRemaining,
    credits_used: creditsUsed,
    monthly_limit: monthlyLimit,
    tier,
    ...(anonymousId ? { anon_id: anonymousId } : {}),
  });
});

// ── POST /saved-subjects/:subjectId ───────────────────────────────────────────

usersRouter.post('/saved-subjects/:subjectId', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);
  const subjectId = c.req.param('subjectId');

  const user = await db.select({ savedSubjects: users.savedSubjects })
    .from(users).where(eq(users.id, id)).get();
  if (!user) return c.json({ detail: 'User not found' }, 404);

  let saved: string[] = [];
  try { saved = JSON.parse(user.savedSubjects ?? '[]') as string[]; } catch { /* leave empty */ }

  let action: 'added' | 'removed';
  if (saved.includes(subjectId)) {
    saved = saved.filter(s => s !== subjectId);
    action = 'removed';
  } else {
    saved.push(subjectId);
    action = 'added';
  }

  await db.update(users).set({
    savedSubjects: JSON.stringify(saved),
    updatedAt: Math.floor(Date.now() / 1000),
  }).where(eq(users.id, id));

  return c.json({ status: 'success', action, saved_subjects: saved });
});

// ── DELETE /account ────────────────────────────────────────────────────────────
// Schedule account deletion (GDPR/DPDP compliance). Marks the account with a
// soft-delete timestamp; a cron job hard-deletes after the grace period.
// Matches Cloud Run DELETE /users/account and DELETE /user/account contracts.

usersRouter.delete('/account', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);
  const now = Math.floor(Date.now() / 1000);
  const gracePeriodDays = 14;
  const hardDeleteAt = now + gracePeriodDays * 24 * 3600;

  await db.update(users).set({
    deletedAt: now,
    updatedAt: now,
  }).where(eq(users.id, id));

  return c.json({
    status: 'success',
    message: `Account scheduled for deletion. You have ${gracePeriodDays} days to cancel.`,
    deletion_scheduled_at: new Date(now * 1000).toISOString(),
    deletion_hard_at: new Date(hardDeleteAt * 1000).toISOString(),
  });
});

// ── POST /account/cancel-delete ────────────────────────────────────────────────
// Cancel a scheduled account deletion within the grace window.

usersRouter.post('/account/cancel-delete', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);
  const user = await db.select({ deletedAt: users.deletedAt })
    .from(users).where(eq(users.id, id)).get();

  if (!user?.deletedAt) {
    return c.json({ detail: 'No deletion scheduled for this account' }, 400);
  }

  await db.update(users).set({
    deletedAt: null,
    updatedAt: Math.floor(Date.now() / 1000),
  }).where(eq(users.id, id));

  return c.json({ status: 'success', message: 'Account deletion cancelled' });
});
