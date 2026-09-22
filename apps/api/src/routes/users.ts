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
import {
  CHAT_RPM_LIMIT,
  anonUserId,
  currentQuotaMinutePeriod,
  isBrowserAnonId,
} from '../services/anonymous';
import { getAnonQuotaUsage } from './chat';
import {
  currentQuotaMonthPeriod,
  monthResetAt,
  monthlyChatLimit,
} from '../services/consumer-referrals';
import type { Env } from '../types';

export const usersRouter = new Hono<{ Bindings: Env }>();

// Single source of truth for the account-deletion grace window. The GET
// /profile response and the DELETE /account scheduler must agree on this
// value — they previously used 72 hours and 14 days respectively, so a
// reloaded profile page showed a hard-delete date three times too soon.
const ACCOUNT_DELETION_GRACE_DAYS = 14;

// Credit limits — authoritative, must match billing pipeline
const CHAT_REQUESTS_PER_MINUTE = CHAT_RPM_LIMIT;
const CREDITS_LIMITS: Record<string, number> = {
  free: 30,
  starter: 100,
  pro: 300,
  premium: 600,
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

function buildProfileResponse(
  user: typeof users.$inferSelect,
  monthlyUsed = 0,
  now = Math.floor(Date.now() / 1000),
): Record<string, unknown> {
  const tier = user.subscriptionTier ?? 'free';
  const creditsLimit = monthlyChatLimit(tier, user.referralUpgradeUntil, now)
    || CREDITS_LIMITS[tier]
    || 30;
  const creditsUsed  = user.creditsUsed ?? 0;
  const creditsRemaining = user.creditsRemaining != null
    ? user.creditsRemaining
    : Math.max(0, creditsLimit - creditsUsed);

  let status = 'active';
  let deletionHardAt: string | null = null;
  if (user.deletedAt) {
    status = 'pending_deletion';
    deletionHardAt = new Date(
      (user.deletedAt + ACCOUNT_DELETION_GRACE_DAYS * 24 * 3600) * 1000,
    ).toISOString();
  }

  let savedSubjects: string[] = [];
  try { savedSubjects = JSON.parse(user.savedSubjects ?? '[]') as string[]; } catch { /* leave empty */ }
  let capabilities: string[] | null = null;
  try { capabilities = user.capabilities === null ? null : JSON.parse(user.capabilities ?? '[]') as string[]; } catch { capabilities = []; }
  let selectedSubjects: Array<{ id: string; name: string }> = [];
  try { selectedSubjects = JSON.parse(user.selectedSubjects ?? '[]') as Array<{ id: string; name: string }>; } catch { /* leave empty */ }

  return {
    id:                    user.id,
    name:                  user.name ?? '',
    email:                 user.email ?? '',
    role:                  user.role,
    is_admin:              user.role === 'admin',
    avatar_url:            user.avatarUrl ?? null,
    created_at:            user.createdAt != null ? new Date(user.createdAt * 1000).toISOString() : null,
    // null explicitly communicates the backwards-compatible full-staff policy.
    capabilities,
    subscription_tier:     tier,
    plan:                  tier,             // alias expected by frontend
    monthly_message_count: user.monthlyMessageCount,
    preferred_language:    user.preferredLanguage,
    onboarding_done:       Boolean(user.onboardingDone),
    ads_opt_out:           Boolean(user.adsOptOut),
    ads_free_until:        user.referralAdsFreeUntil ?? null,
    referral_points:       user.referralPoints ?? 0,
    referral_visitors_verified: user.referralVisitorsVerified ?? 0,
    referral_upgrade_until: user.referralUpgradeUntil ?? null,
    saved_subjects:        savedSubjects,
    phone:                 user.phone ?? null,
    board_id:              user.boardId ?? null,
    board_name:            user.boardName ?? null,
    class_id:              user.classId ?? null,
    class_name:            user.className ?? null,
    stream_id:             user.streamId ?? null,
    stream_name:           user.streamName ?? null,
    course_type:           user.courseType ?? null,
    selected_subjects:     selectedSubjects,
    credits_used:          monthlyUsed,
    credits_limit:         creditsLimit,
    credits_remaining:     Math.max(0, creditsLimit - monthlyUsed),
    monthly_chat_limit:    creditsLimit,
    monthly_chats_used:    monthlyUsed,
    monthly_chats_remaining: Math.max(0, creditsLimit - monthlyUsed),
    monthly_reset_at:      new Date(monthResetAt(now) * 1000).toISOString(),
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

  const now = Math.floor(Date.now() / 1000);
  const monthlyUsage = await c.env.DB.prepare(
    'SELECT count FROM monthly_quota_usage WHERE user_id = ? AND period = ?',
  ).bind(id, currentQuotaMonthPeriod(now)).first<{ count: number }>();
  return c.json(buildProfileResponse(user, monthlyUsage?.count ?? 0, now)) as Response;
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
    course_type?: string;
    selected_subjects?: Array<{ id: string; name: string }>;
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
  // These two were previously accepted by the client and silently dropped
  // here — the UI reported success but nothing was ever persisted.
  if (body.course_type       != null) updates.courseType        = body.course_type;
  if (body.selected_subjects != null) updates.selectedSubjects  = JSON.stringify(body.selected_subjects);

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

function memoryDisplayText(key: string, value: string | null): string {
  if (!value) return '';
  try {
    const parsed = JSON.parse(value) as { question?: string };
    if (parsed.question) return parsed.question;
  } catch {
    // Legacy memories are stored as plain text.
  }
  return value;
}

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
      text: memoryDisplayText(r.key, r.value),
      kind: r.key.startsWith('qa:') ? 'qa' : r.key,
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
  // Optional auth — anonymous users read the same D1 quota reserved by chat.
  const authHeader = c.req.header('Authorization');
  const token = extractBearer(authHeader ?? null);
  let tier = 'free';
  let creditsRemaining = 0;
  let creditsUsed = 0;
  let anonymousId: string | null = null;
  let authenticated = false;
  let authenticatedUserId: string | null = null;

  if (token) {
    const payload = await verifyToken(token, c.env.JWT_SECRET);
    if (payload?.type === 'access' && payload.sub
      && await isSessionValid(c.env.DB, payload.sub, payload.iat)) {
      const db = createDb(c.env.DB);
      const user = await db.select({
        subscriptionTier: users.subscriptionTier,
        referralUpgradeUntil: users.referralUpgradeUntil,
        referralAdsFreeUntil: users.referralAdsFreeUntil,
        referralPoints: users.referralPoints,
      }).from(users).where(eq(users.id, payload.sub)).get();

      if (user) {
        authenticated = true;
        authenticatedUserId = payload.sub;
        tier = user.subscriptionTier ?? 'free';
      }
    }
  }

  // Match chat's optional-auth behavior: a stale or invalid token is treated
  // as anonymous, and therefore still resolves the browser's persistent ID.
  if (!authenticated) {
    // A credits read does not reserve quota. Accept the browser's validated
    // anonymous ID when the API is called directly (the edge still signs and
    // validates this identity for all mutating chat requests). This keeps the
    // read contract useful in local/API-worker tests without weakening chat
    // identity enforcement.
    const requestedAnonId = c.req.header('x-anon-id')?.trim() ?? null;
    anonymousId = isBrowserAnonId(requestedAnonId)
      ? requestedAnonId
      : await anonUserId(c.req.raw, c.env.EDGE_SHARED_SECRET);
    creditsUsed = Math.max(
      0,
      await getAnonQuotaUsage(c.env.DB, c.env.RATE_LIMIT_KV, anonymousId),
    );
  } else if (authenticatedUserId) {
    const row = await c.env.DB.prepare(
      'SELECT count FROM quota_usage WHERE user_id = ? AND period = ?',
    ).bind(authenticatedUserId, currentQuotaMinutePeriod()).first<{ count: number }>();
    creditsUsed = row?.count ?? 0;
  }

  const rpmLimit = CHAT_REQUESTS_PER_MINUTE;
  let monthlyUsed = 0;
  let monthlyLimit = 30;
  let monthlyResetAt = monthResetAt();
  let adsFreeUntil: number | null = null;
  let referralPoints = 0;
  if (authenticated && authenticatedUserId) {
    const user = await c.env.DB.prepare(`
      SELECT subscription_tier, referral_upgrade_until, referral_ads_free_until,
             referral_points
      FROM users WHERE id = ?
    `).bind(authenticatedUserId).first<{
      subscription_tier: string | null;
      referral_upgrade_until: number | null;
      referral_ads_free_until: number | null;
      referral_points: number | null;
    }>();
    monthlyLimit = monthlyChatLimit(
      user?.subscription_tier,
      user?.referral_upgrade_until,
    );
    const currentPeriod = currentQuotaMonthPeriod();
    const monthRow = await c.env.DB.prepare(
      'SELECT count FROM monthly_quota_usage WHERE user_id = ? AND period = ?',
    ).bind(authenticatedUserId, currentPeriod).first<{ count: number }>();
    monthlyUsed = monthRow?.count ?? 0;
    monthlyResetAt = monthResetAt();
    adsFreeUntil = user?.referral_ads_free_until ?? null;
    referralPoints = user?.referral_points ?? 0;
  }
  const nowMs = Date.now();
  const resetAt = (Math.floor(nowMs / 60_000) + 1) * 60_000;
  // The edge owns reservations for production chat requests. This explicit
  // per-language envelope lets clients render independent EN/AS allowances
  // without exposing identity or falling back to the retired daily contract.
  const languageQuota = () => ({
    limit: rpmLimit,
    reset_at: new Date(resetAt).toISOString(),
  });
  return c.json({
    credits_remaining: Math.max(0, rpmLimit - creditsUsed),
    credits_used: creditsUsed,
    rpm_limit: rpmLimit,
    quota_period: 'minute',
    reset_at: new Date(resetAt).toISOString(),
    languages: {
      // Edge-owned buckets are independent. Their usage is intentionally not
      // copied from the D1 compatibility counter (which has no language
      // dimension); successful chat responses provide the authoritative
      // per-language remaining/reset headers.
      en: languageQuota(),
      as: languageQuota(),
    },
    tier,
    monthly_chat_limit: monthlyLimit,
    monthly_chats_used: monthlyUsed,
    monthly_chats_remaining: Math.max(0, monthlyLimit - monthlyUsed),
    monthly_reset_at: new Date(monthlyResetAt * 1000).toISOString(),
    ads_free_until: adsFreeUntil,
    referral_points: referralPoints,
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
