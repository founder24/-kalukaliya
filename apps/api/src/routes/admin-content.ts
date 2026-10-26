/**
 * Worker-native admin publishing and content-seeding routes.
 *
 * This module deliberately retains the established /api/v1/admin/content/*
 * contracts. It uses D1, KV, Vectorize and Workers AI directly; no request is
 * forwarded to Cloud Run and no Google identity token is needed.
 */

import { Hono, type Context } from 'hono';
import { and, desc, eq, inArray, isNotNull, isNull, lt, or } from 'drizzle-orm';
import { createDb } from '../db/client';
import { boards, classes, chapters, publishJobs, seedRuns, streams, subjects, users } from '../db/schema';
import { extractBearer, isSessionValid, sessionIssuedAt, signAdminToken, verifyAdminToken, verifyPassword, verifyToken } from '../middleware/auth';
import { generate } from '../services/ai';
import { enforceAuthRateLimit } from '../services/auth-rate-limit';
import { reindexChapterRag } from '../services/rag-indexing';
import { publicChapterListWhere, serializePublicChapterList } from '../services/public-chapter-list';
import {
  buildTopicUrlInventory,
  classifyDiscoveryReferrer,
  TOPIC_TRAFFIC_INVENTORY_VERSION,
  TOPIC_TRAFFIC_REPORT_VERSION,
  TOPIC_TRAFFIC_TARGET_PER_7D,
  TOPIC_TRAFFIC_TARGET_PER_DAY,
  type TopicInventorySourceRow,
  type TopicUrlInventoryItem,
} from '../services/topic-traffic';
import type { Env } from '../types';
import { runRagJob } from './staff';
import type { CloudflareAnalyticsHealthResult } from '../types';

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

function metricCount(value: number | string | null | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
}

function parseAdSenseFailures(value: string | null | undefined): string[] {
  try {
    const parsed = JSON.parse(value ?? '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is string => typeof item === 'string')
      .map(message => message
        .replace(/Bearer\s+\S+/gi, 'Bearer [redacted]')
        .replace(/https?:\/\/\S+/gi, '[provider]')
        .slice(0, 512))
      .slice(0, 16);
  } catch {
    return [];
  }
}

const DEFAULT_NOTIFICATION_PREFS = {
  sound_enabled: true,
  push_enabled: false,
  chime_tone: 'default',
  sound_severities: ['high_error_rate', 'high_latency', 'high_fallback_rate', 'endpoint_down'],
  push_severities: ['high_error_rate', 'endpoint_down'],
  email_failing_csv_enabled: true,
  email_seo_daily_summary_enabled: true,
  custom_chime_url: null,
};

const DEFAULT_ALERT_SETTINGS = {
  thresholds: {},
  expiration: { enabled: false, days: 7 },
};

async function readAdminConfig<T>(env: Env, key: string, fallback: T): Promise<T> {
  const row = await env.DB.prepare('SELECT value FROM admin_config WHERE key = ?')
    .bind(key).first<{ value: string | null }>();
  return parseJson(row?.value, fallback);
}

async function writeAdminConfig(env: Env, key: string, value: unknown): Promise<void> {
  await env.DB.prepare(`
    INSERT INTO admin_config (key, value, updated_at) VALUES (?, ?, ?)
    ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
  `).bind(key, JSON.stringify(value), now()).run();
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
  // The unified staff control center uses normal access tokens for both staff
  // and admin roles. Admin-cookie sessions remain admin-only.
  if (bearer) {
    const access = await verifyToken(bearer, c.env.JWT_SECRET);
    if (access?.type === 'access' && (access.role === 'admin' || access.role === 'staff') && access.sub
      && await isSessionValid(c.env.DB, access.sub, access.iat)) return access.sub;
  }
  return c.json({ detail: bearer || session ? 'Invalid or expired admin session' : 'Authentication required' }, 401);
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
  const rateLimitResponse = await enforceAuthRateLimit(c.env.DB, c.req.raw, 'admin-login');
  if (rateLimitResponse) return rateLimitResponse;

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
  // Logout requires the same session boundary as every other admin route so
  // anonymous callers cannot use it as an apparent authenticated admin probe.
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  // Bump the account-wide session cutoff so the presented admin cookie or
  // staff/admin bearer token (and any other outstanding session for this
  // account) is rejected by isSessionValid() on the next request. Previously
  // this route only cleared the client cookie and never revoked anything
  // server-side, so a copied cookie or bearer token stayed valid until it
  // naturally expired.
  let revoked = true;
  try {
    const validAfter = Math.floor(Date.now() / 1000) + 1;
    await c.env.DB.prepare(
      `UPDATE users SET session_valid_after = MAX(session_valid_after + 1, ?) WHERE id = ?`,
    ).bind(validAfter, actor).run();
  } catch (err) {
    console.error('[admin-content] session revocation on logout unavailable:', err);
    revoked = false;
  }
  const response = c.json({ status: 'ok', message: 'Logged out', server_revocation: revoked });
  response.headers.set('Set-Cookie', 'syrabit_admin_session=; Path=/api/; Max-Age=0; HttpOnly; SameSite=Lax');
  return response;
});

adminContentRouter.get('/dashboard', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;

  c.header('Cache-Control', 'private, no-store');
  const nowSeconds = now();
  const todayStart = Math.floor(nowSeconds / 86_400) * 86_400;

  const [userStats, chatStats, subjectStats, feedbackStats] = await Promise.all([
    c.env.DB.prepare(`
      SELECT COUNT(*) AS total_users,
             SUM(CASE WHEN updated_at >= ? THEN 1 ELSE 0 END) AS active_today,
             SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS signups_today,
             SUM(CASE WHEN subscription_tier = 'pro' THEN 1 ELSE 0 END) AS pro_users,
             SUM(CASE WHEN subscription_tier = 'free' THEN 1 ELSE 0 END) AS free_users,
             SUM(CASE WHEN subscription_tier = 'starter' THEN 1 ELSE 0 END) AS starter_users,
             SUM(CASE WHEN subscription_tier = 'premium' THEN 1 ELSE 0 END) AS premium_users
      FROM users
      WHERE deleted_at IS NULL
    `).bind(todayStart, todayStart).first<{
      total_users: number | string | null;
      active_today: number | string | null;
      signups_today: number | string | null;
      pro_users: number | string | null;
      free_users: number | string | null;
      starter_users: number | string | null;
      premium_users: number | string | null;
    }>(),
    c.env.DB.prepare(`
      SELECT COUNT(*) AS total_messages,
             SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS messages_today,
             COUNT(DISTINCT session_id) AS total_conversations,
             COUNT(DISTINCT session_id) AS conversations_with_messages,
             COUNT(DISTINCT user_id) AS unique_chatters
      FROM chats
      WHERE expires_at IS NULL OR expires_at > ?
    `).bind(todayStart, nowSeconds).first<{
      total_messages: number | string | null;
      messages_today: number | string | null;
      total_conversations: number | string | null;
      conversations_with_messages: number | string | null;
      unique_chatters: number | string | null;
    }>(),
    c.env.DB.prepare('SELECT COUNT(*) AS total_subjects FROM subjects')
      .first<{ total_subjects: number | string | null }>(),
    c.env.DB.prepare(`
      SELECT COUNT(*) AS total,
             SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) AS positive
      FROM chat_feedback
      WHERE expires_at IS NULL OR expires_at > ?
    `).bind(nowSeconds).first<{
      total: number | string | null;
      positive: number | string | null;
    }>(),
  ]);

  const asCount = (value: number | string | null | undefined): number => {
    const parsed = Number(value ?? 0);
    return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
  };
  const feedbackTotal = asCount(feedbackStats?.total);
  const positiveFeedback = asCount(feedbackStats?.positive);

  return c.json({
    total_users: asCount(userStats?.total_users),
    active_today: asCount(userStats?.active_today),
    total_messages: asCount(chatStats?.total_messages),
    messages_today: asCount(chatStats?.messages_today),
    total_conversations: asCount(chatStats?.total_conversations),
    conversations_with_messages: asCount(chatStats?.conversations_with_messages),
    unique_chatters: asCount(chatStats?.unique_chatters),
    total_subjects: asCount(subjectStats?.total_subjects),
    pro_users: asCount(userStats?.pro_users),
    free_users: asCount(userStats?.free_users),
    plan_distribution: {
      free: asCount(userStats?.free_users),
      starter: asCount(userStats?.starter_users),
      pro: asCount(userStats?.pro_users) + asCount(userStats?.premium_users),
    },
    signups_today: asCount(userStats?.signups_today),
    system_health: 'ok',
    feedback: {
      total: feedbackTotal,
      positive: positiveFeedback,
      positive_rate: feedbackTotal ? Math.round((positiveFeedback / feedbackTotal) * 1000) / 1000 : 0,
    },
  });
});

adminContentRouter.get('/dashboard/metrics', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  c.header('Cache-Control', 'private, no-store');

  const startedAt = Date.now();
  const nowSeconds = now();
  const todayStart = Math.floor(nowSeconds / 86_400) * 86_400;
  const since = nowSeconds - 30 * 86_400;
  const [users, chapters, traffic, dailyTraffic] = await Promise.all([
    c.env.DB.prepare(`
      SELECT COUNT(*) AS total,
        SUM(CASE WHEN subscription_tier = 'free' THEN 1 ELSE 0 END) AS free,
        SUM(CASE WHEN subscription_tier = 'starter' THEN 1 ELSE 0 END) AS starter,
        SUM(CASE WHEN subscription_tier = 'pro' THEN 1 ELSE 0 END) AS pro,
        SUM(CASE WHEN subscription_tier = 'premium' THEN 1 ELSE 0 END) AS premium
      FROM users WHERE deleted_at IS NULL
    `).first<Record<string, number | string | null>>(),
    c.env.DB.prepare(`
      SELECT COUNT(*) AS total,
        SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) AS published,
        SUM(CASE WHEN notes_en IS NOT NULL AND TRIM(notes_en) != '' THEN 1 ELSE 0 END) AS english_notes,
        SUM(CASE WHEN notes_as IS NOT NULL AND TRIM(notes_as) != '' THEN 1 ELSE 0 END) AS assamese_notes
      FROM chapters
    `).first<Record<string, number | string | null>>(),
    c.env.DB.prepare(`
      SELECT COUNT(*) AS page_views,
        SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS page_views_today,
        COUNT(DISTINCT json_extract(payload, '$.session_id')) AS sessions,
        COUNT(DISTINCT CASE WHEN created_at >= ?
          THEN json_extract(payload, '$.session_id') END) AS sessions_today
      FROM analytics_events
      WHERE event_subtype = 'page_view'
        AND classification = 'optional_analytics'
        AND created_at >= ?
    `).bind(todayStart, todayStart, since)
      .first<Record<string, number | string | null>>(),
    c.env.DB.prepare(`
      SELECT date(created_at, 'unixepoch') AS date,
        COUNT(*) AS page_views,
        COUNT(DISTINCT json_extract(payload, '$.session_id')) AS sessions
      FROM analytics_events
      WHERE event_subtype = 'page_view'
        AND classification = 'optional_analytics'
        AND created_at >= ?
      GROUP BY date(created_at, 'unixepoch')
      ORDER BY date(created_at, 'unixepoch')
    `).bind(since).all<{ date: string; page_views: number | string; sessions: number | string }>(),
  ]);

  const pageViews = metricCount(traffic?.page_views);
  const sessionCount = metricCount(traffic?.sessions);
  const daily = (dailyTraffic.results ?? []).map(row => ({
    date: row.date,
    page_views: metricCount(row.page_views),
    sessions: metricCount(row.sessions),
  }));
  const responseTimeMs = Date.now() - startedAt;
  const totalChapters = metricCount(chapters?.total);
  const publishedChapters = metricCount(chapters?.published);

  return c.json({
    source: 'd1',
    generated_at: new Date(nowSeconds * 1000).toISOString(),
    response_time_ms: responseTimeMs,
    dependencies: { d1: { status: 'ok', latency: responseTimeMs } },
    users: {
      total: metricCount(users?.total),
      free: metricCount(users?.free),
      starter: metricCount(users?.starter),
      pro: metricCount(users?.pro),
      premium: metricCount(users?.premium),
    },
    seo: {
      published_pages: publishedChapters,
      topics: totalChapters,
      published_chapters: publishedChapters,
      chapters_with_english_notes: metricCount(chapters?.english_notes),
      chapters_with_assamese_notes: metricCount(chapters?.assamese_notes),
    },
    visitor_stats: {
      source: 'analytics_events',
      window_days: 30,
      has_data: pageViews > 0,
      page_views: pageViews,
      page_views_today: metricCount(traffic?.page_views_today),
      sessions: sessionCount,
      sessions_today: metricCount(traffic?.sessions_today),
      daily,
    },
  });
});

const DISCOVERY_AUTOMATION_UA = /bot|crawler|spider|slurp|fetcher|preview|monitor|headless/i;

function eventCount(value: number | string | null | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? Math.max(0, Math.trunc(parsed)) : 0;
}

function dateIso(seconds: number): string {
  return new Date(seconds * 1000).toISOString();
}

type TopicPageEvent = {
  route_path: string | null;
  date: string;
  page_view_events: number | string | null;
  sessions: number | string | null;
};

type TopicLandingEvent = {
  route_path: string | null;
  session_id: string | null;
  created_at: number | string | null;
  referrer: string | null;
  user_agent: string | null;
};

function metricMapKey(path: string, date: string): string {
  return `${path}\n${date}`;
}

adminContentRouter.get('/seo/topic-traffic', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  c.header('Cache-Control', 'private, no-store');

  const page = Math.max(1, Math.trunc(Number(c.req.query('page')) || 1));
  const pageSize = Math.min(250, Math.max(1, Math.trunc(Number(c.req.query('page_size')) || 100)));
  const generatedAtSeconds = now();
  const todayStart = Math.floor(generatedAtSeconds / 86_400) * 86_400;
  const dailyStart = todayStart - 6 * 86_400;
  const rollingStart = generatedAtSeconds - 7 * 86_400;
  const todayDate = new Date(todayStart * 1000).toISOString().slice(0, 10);
  const dateKeys = Array.from({ length: 7 }, (_, index) =>
    new Date((dailyStart + index * 86_400) * 1000).toISOString().slice(0, 10));
  const dates = dateKeys.map((date, index) => ({
    date,
    partial_day: date === todayDate,
    day_start: dailyStart + index * 86_400,
  }));

  const inventoryRows = await c.env.DB.prepare(`
    SELECT c.id AS chapter_id, c.title AS chapter_title, c.slug AS chapter_slug,
           c.status AS chapter_status, c.published_topics,
           s.id AS subject_id, s.name AS subject_name, s.slug AS subject_slug,
           s.is_published AS subject_published,
           b.slug AS board_slug, cl.slug AS class_slug
    FROM chapters c
    LEFT JOIN subjects s ON s.id = c.subject_id
    LEFT JOIN streams str ON str.id = s.stream_id
    LEFT JOIN classes cl ON cl.id = str.class_id
    LEFT JOIN boards b ON b.id = cl.board_id
    ORDER BY s.name, c.title, c.id
  `).all<TopicInventorySourceRow>();
  const inventory = buildTopicUrlInventory(inventoryRows.results ?? []);
  const eligiblePathSet = new Set(
    inventory.filter(item => item.eligible && item.canonical_path).map(item => item.canonical_path as string),
  );

  let analyticsQueryAvailable = true;
  let earliestEvent: number | null = null;
  let latestEvent: number | null = null;
  const dailyPageViews = new Map<string, { page_view_events: number; sessions: number }>();
  const rollingPageViews = new Map<string, { page_view_events: number; sessions: number }>();
  const dailyEntryCounts = new Map<string, number>();
  const rollingEntryCounts = new Map<string, number>();
  const dailyLandings = new Map<string, { organic: number; ai: number }>();
  const rollingLandings = new Map<string, { organic: number; ai: number }>();

  try {
    const [coverage, dailyRows, rollingRows, firstLandingRows] = await Promise.all([
      c.env.DB.prepare(`
        SELECT MIN(created_at) AS earliest, MAX(created_at) AS latest
        FROM analytics_events
        WHERE event_subtype = 'page_view' AND classification = 'optional_analytics'
      `).first<{ earliest: number | string | null; latest: number | string | null }>(),
      c.env.DB.prepare(`
        SELECT route_path, date(created_at, 'unixepoch') AS date,
               COUNT(*) AS page_view_events,
               COUNT(DISTINCT json_extract(payload, '$.session_id')) AS sessions
        FROM analytics_events
        WHERE event_subtype = 'page_view'
          AND classification = 'optional_analytics'
          AND created_at >= ?
          AND route_path LIKE '%/topic/%'
        GROUP BY route_path, date(created_at, 'unixepoch')
      `).bind(dailyStart).all<TopicPageEvent>(),
      c.env.DB.prepare(`
        SELECT route_path, '' AS date,
               COUNT(*) AS page_view_events,
               COUNT(DISTINCT json_extract(payload, '$.session_id')) AS sessions
        FROM analytics_events
        WHERE event_subtype = 'page_view'
          AND classification = 'optional_analytics'
          AND created_at >= ?
          AND route_path LIKE '%/topic/%'
        GROUP BY route_path
      `).bind(rollingStart).all<TopicPageEvent>(),
      c.env.DB.prepare(`
        WITH ranked_entries AS (
          SELECT route_path, created_at,
                 json_extract(payload, '$.session_id') AS session_id,
                 json_extract(payload, '$.referrer') AS referrer,
                 json_extract(payload, '$.user_agent') AS user_agent,
                 ROW_NUMBER() OVER (
                   PARTITION BY json_extract(payload, '$.session_id')
                   ORDER BY created_at ASC, id ASC
                 ) AS entry_rank
          FROM analytics_events
          WHERE event_subtype = 'page_view'
            AND classification = 'optional_analytics'
            AND created_at >= ?
            AND json_extract(payload, '$.is_session_entry') = 1
            AND NULLIF(json_extract(payload, '$.session_id'), '') IS NOT NULL
        )
        SELECT route_path, session_id, created_at, referrer, user_agent
        FROM ranked_entries
        WHERE entry_rank = 1
      `).bind(rollingStart).all<TopicLandingEvent>(),
    ]);

    earliestEvent = coverage?.earliest == null ? null : eventCount(coverage.earliest);
    latestEvent = coverage?.latest == null ? null : eventCount(coverage.latest);
    for (const row of dailyRows.results ?? []) {
      if (!row.route_path || !eligiblePathSet.has(row.route_path)) continue;
      dailyPageViews.set(metricMapKey(row.route_path, row.date), {
        page_view_events: eventCount(row.page_view_events),
        sessions: eventCount(row.sessions),
      });
    }
    for (const row of rollingRows.results ?? []) {
      if (!row.route_path || !eligiblePathSet.has(row.route_path)) continue;
      rollingPageViews.set(row.route_path, {
        page_view_events: eventCount(row.page_view_events),
        sessions: eventCount(row.sessions),
      });
    }
    for (const row of firstLandingRows.results ?? []) {
      if (!row.route_path || !eligiblePathSet.has(row.route_path)) continue;
      const date = row.created_at == null
        ? ''
        : new Date(eventCount(row.created_at) * 1000).toISOString().slice(0, 10);
      const dailyEntryKey = metricMapKey(row.route_path, date);
      dailyEntryCounts.set(dailyEntryKey, (dailyEntryCounts.get(dailyEntryKey) ?? 0) + 1);
      rollingEntryCounts.set(row.route_path, (rollingEntryCounts.get(row.route_path) ?? 0) + 1);
      if (DISCOVERY_AUTOMATION_UA.test(row.user_agent ?? '')) continue;
      const source = classifyDiscoveryReferrer(row.referrer);
      if (source !== 'organic_search' && source !== 'ai_referral') continue;
      const dailyKey = metricMapKey(row.route_path, date);
      const dailyCounts = dailyLandings.get(dailyKey) ?? { organic: 0, ai: 0 };
      const rollingCounts = rollingLandings.get(row.route_path) ?? { organic: 0, ai: 0 };
      if (source === 'organic_search') {
        dailyCounts.organic += 1;
        rollingCounts.organic += 1;
      } else {
        dailyCounts.ai += 1;
        rollingCounts.ai += 1;
      }
      dailyLandings.set(dailyKey, dailyCounts);
      rollingLandings.set(row.route_path, rollingCounts);
    }
  } catch (error) {
    analyticsQueryAvailable = false;
    console.warn('Topic traffic analytics source unavailable:', error);
  }

  // The event ledger has no verified collection-start marker. Report directly
  // observed values, but do not turn a missing URL/day into zero or evaluate a
  // full-window target without proof that the source covered that window.
  const rollingHistoryComplete = false;
  const topics = inventory.map(item => {
    const path = item.canonical_path;
    const rollingPage = path ? rollingPageViews.get(path) : undefined;
    const rollingLanding = path ? rollingLandings.get(path) : undefined;
    const rollingPageObserved = item.eligible && analyticsQueryAvailable && rollingPage !== undefined;
    const rollingObserved = item.eligible && analyticsQueryAvailable && path !== null
      && rollingEntryCounts.has(path);
    const rollingOrganic = rollingObserved ? (rollingLanding?.organic ?? 0) : null;
    const rollingAi = rollingObserved ? (rollingLanding?.ai ?? 0) : null;
    const rollingHuman = rollingOrganic === null || rollingAi === null
      ? null
      : rollingOrganic + rollingAi;
    const rollingStatus = item.eligible ? 'not_available' : 'not_eligible';

    const daily = dates.map(({ date, partial_day }) => {
      const page = path ? dailyPageViews.get(metricMapKey(path, date)) : undefined;
      const landings = path ? dailyLandings.get(metricMapKey(path, date)) : undefined;
      const entryObserved = item.eligible && analyticsQueryAvailable && path !== null
        && dailyEntryCounts.has(metricMapKey(path, date));
      const organic = entryObserved ? (landings?.organic ?? 0) : null;
      const ai = entryObserved ? (landings?.ai ?? 0) : null;
      const human = organic === null || ai === null ? null : organic + ai;
      const pageObserved = item.eligible && analyticsQueryAvailable && page !== undefined;
      return {
        date,
        partial_day,
        page_view_events: pageObserved ? page.page_view_events : null,
        site_sessions: pageObserved ? page.sessions : null,
        organic_search_landings: organic,
        ai_referral_landings: ai,
        human_discovery_visits: human,
        human_target_per_day: TOPIC_TRAFFIC_TARGET_PER_DAY,
        human_target_status: !item.eligible
          ? 'not_eligible'
          : partial_day && human !== null ? 'in_progress' : 'not_available',
        server_page_requests: null,
        verified_crawler_fetches: null,
        crawler_target_per_day: TOPIC_TRAFFIC_TARGET_PER_DAY,
        crawler_target_status: 'not_available',
      };
    });

    return {
      ...item,
      metrics: {
        daily,
        rolling_7d: {
          window_start: dateIso(rollingStart),
          window_end: dateIso(generatedAtSeconds),
          page_view_events: rollingPageObserved ? rollingPage.page_view_events : null,
          site_sessions: rollingPageObserved ? rollingPage.sessions : null,
          organic_search_landings: rollingOrganic,
          ai_referral_landings: rollingAi,
          human_discovery_visits: rollingHuman,
          human_target: TOPIC_TRAFFIC_TARGET_PER_7D,
          human_target_status: rollingStatus,
          server_page_requests: null,
          verified_crawler_fetches: null,
          crawler_target: TOPIC_TRAFFIC_TARGET_PER_7D,
          crawler_target_status: 'not_available',
        },
        search_console: {
          clicks: null,
          impressions: null,
          status: 'not_connected',
        },
        crawler_response_outcomes: {
          successful_html: null,
          blocked: null,
          failed: null,
          non_html: null,
          unverified_identity: null,
          response_status_counts: null,
          status: 'not_ingested',
        },
      },
      diagnostics: [
        ...(!item.eligible ? [{
          code: item.exclusion_reason,
          message: `Excluded from the eligible URL denominator: ${item.exclusion_reason}.`,
        }] : []),
        ...(item.eligible ? [{
          code: 'human_telemetry_coverage_unverified',
          message: 'Observed consented sessions are shown, but the collection start is not recorded, so a complete daily or 7-day target result cannot be confirmed.',
        }] : []),
      ],
    };
  }).sort((left, right) =>
    (left.subject_name ?? '').localeCompare(right.subject_name ?? '') ||
    left.chapter_title.localeCompare(right.chapter_title) ||
    (left.topic_title ?? '').localeCompare(right.topic_title ?? '') ||
    left.language.localeCompare(right.language));

  const byExclusionReason: Record<string, number> = {};
  for (const item of inventory) {
    if (!item.eligible && item.exclusion_reason) {
      byExclusionReason[item.exclusion_reason] = (byExclusionReason[item.exclusion_reason] ?? 0) + 1;
    }
  }

  const subjectGroups = new Map<string, {
    subject_id: string | null;
    subject_name: string | null;
    language: string;
    candidate_urls: number;
    eligible_urls: number;
    excluded_urls: number;
    human_discovery_visits_7d: number | null;
    human_discovery_coverage: 'partial_unverified';
    verified_crawler_fetches_7d: null;
  }>();
  for (const topic of topics) {
    const key = `${topic.subject_id ?? 'unknown'}\n${topic.language}`;
    const group = subjectGroups.get(key) ?? {
      subject_id: topic.subject_id,
      subject_name: topic.subject_name,
      language: topic.language,
      candidate_urls: 0,
      eligible_urls: 0,
      excluded_urls: 0,
      human_discovery_visits_7d: null,
      human_discovery_coverage: 'partial_unverified',
      verified_crawler_fetches_7d: null,
    };
    group.candidate_urls += 1;
    if (topic.eligible) {
      group.eligible_urls += 1;
      const observedHuman = topic.metrics.rolling_7d.human_discovery_visits;
      if (observedHuman !== null) {
        group.human_discovery_visits_7d = (group.human_discovery_visits_7d ?? 0) + observedHuman;
      }
    } else {
      group.excluded_urls += 1;
    }
    subjectGroups.set(key, group);
  }

  const total = topics.length;
  const start = (page - 1) * pageSize;
  return c.json({
    schema_version: TOPIC_TRAFFIC_REPORT_VERSION,
    generated_at: dateIso(generatedAtSeconds),
    timezone: 'UTC',
    inventory: {
      version: TOPIC_TRAFFIC_INVENTORY_VERSION,
      source: 'D1 chapters.published_topics joined to the public curriculum hierarchy',
      candidate_url_count: total,
      eligible_url_count: inventory.filter(item => item.eligible).length,
      excluded_url_count: inventory.filter(item => !item.eligible).length,
      exclusions_by_reason: byExclusionReason,
      pages: { page, page_size: pageSize, total_pages: Math.ceil(total / pageSize) },
    },
    definitions: {
      organic_search_landings: 'Distinct consented page-view beacons explicitly marked by the browser as the first recorded page in that session, with a referrer hostname matching a known search provider.',
      ai_referral_landings: 'Distinct consented page-view beacons explicitly marked by the browser as the first recorded page in that session, with a referrer hostname matching a known AI answer provider.',
      human_discovery_visits: 'Organic-search plus AI-referral landing sessions. Browser telemetry is a proxy, not a verified-human count.',
      site_sessions: 'Distinct consented session IDs with a page-view event on this URL; not added to page-view events, Search Console clicks, or HTTP requests.',
      page_view_events: 'Consent-based browser page-view beacons for this URL; not server-side HTTP page requests.',
      search_console: 'Impressions and clicks are separate Search Console measurements and are N/A until Search Console reporting is connected.',
      verified_crawler_fetches: 'Requires authoritative Cloudflare verified-bot identity, a successful final HTTP response, text/html content, and an eligible canonical topic URL. User-Agent alone is insufficient.',
    },
    targets: {
      human_discovery_visits_per_eligible_url_per_day: TOPIC_TRAFFIC_TARGET_PER_DAY,
      verified_crawler_fetches_per_eligible_url_per_day: TOPIC_TRAFFIC_TARGET_PER_DAY,
      rolling_7d_per_eligible_url: TOPIC_TRAFFIC_TARGET_PER_7D,
      status: 'aspirational',
    },
    window: {
      daily_dates: dateKeys,
      daily_window_start: dateIso(dailyStart),
      daily_window_end: dateIso(generatedAtSeconds),
      rolling_7d_start: dateIso(rollingStart),
      rolling_7d_end: dateIso(generatedAtSeconds),
      current_day_is_partial: true,
    },
    source_coverage: {
      first_party_analytics: {
        status: !analyticsQueryAvailable ? 'unavailable' : earliestEvent === null ? 'no_data' : 'partial',
        source: 'D1 analytics_events (optional_analytics page_view)',
        coverage_start: null,
        coverage_start_verified: false,
        earliest_observed_event_at: earliestEvent === null ? null : dateIso(earliestEvent),
        latest_event_at: latestEvent === null ? null : dateIso(latestEvent),
        rolling_7d_complete: rollingHistoryComplete,
        freshness: latestEvent === null
          ? 'unknown'
          : latestEvent < generatedAtSeconds - 86_400 ? 'stale_over_24h' : 'recent',
        note: !analyticsQueryAvailable
          ? 'D1 page-view telemetry could not be queried; no traffic values are inferred.'
          : earliestEvent === null
            ? 'No consented page-view history is available. Metrics are N/A, not zero.'
            : 'Counts show only observed page-view beacons from visitors who granted optional analytics consent. Discovery attribution uses the new first-consented-page marker; older beacons without it remain N/A. The ledger has no verified collection-start marker, so full-window targets are not evaluated. Human identity is not independently verified.',
      },
      search_console: {
        status: 'not_connected',
        clicks: null,
        impressions: null,
        latest_data_at: null,
        freshness: 'unknown',
        note: 'Connect Search Console reporting for the verified syrabit.ai property. The existing indexing-stats stub does not provide search-performance clicks or impressions.',
      },
      cloudflare_request_logs: {
        status: 'not_ingested',
        source: null,
        latest_event_at: null,
        freshness: 'unknown',
        successful_html_fetches: null,
        blocked: null,
        failed: null,
        non_html: null,
        unverified_identity: null,
        response_status_counts: null,
        note: 'Connect request-level Logpush records to a privacy-safe Worker/D1 aggregation path. Count only Cloudflare VerifiedBotCategory evidence plus final 200 text/html responses for eligible canonical URLs; do not infer success from aggregate bot totals or User-Agent.',
      },
      tollbit: {
        status: 'unverified',
        attributed_requests: null,
        paid_requests: null,
        revenue: null,
        note: 'No Tollbit attribution or paid-access claim is made without verified publisher integration and supported request-identity evidence.',
      },
    },
    subject_language_breakdown: [...subjectGroups.values()].sort((left, right) =>
      (left.subject_name ?? '').localeCompare(right.subject_name ?? '') ||
      left.language.localeCompare(right.language)),
    diagnostics: [
      ...Object.entries(byExclusionReason).map(([reason, count]) => ({
        code: reason,
        count,
        priority: reason === 'topic_not_reviewed' || reason.includes('incomplete') || reason.includes('under_40')
          ? 'content_review'
          : 'inventory_review',
        message: `${count} topic URL candidate(s) are excluded because of ${reason}. Review content or curriculum metadata; no pages are auto-published.`,
      })),
      ...(!analyticsQueryAvailable || earliestEvent === null || !rollingHistoryComplete ? [{
        code: 'human_telemetry_incomplete',
        priority: 'measurement_setup',
        message: !analyticsQueryAvailable
          ? 'First-party page-view telemetry is unavailable; repair the D1 analytics_events query path.'
          : earliestEvent === null
            ? 'No first-party page-view history is available; verify consented browser analytics ingestion before evaluating discovery targets.'
            : 'First-party events exist, but no source collection-start marker is stored. Observed counts are partial and target status remains N/A until full-window coverage can be established.',
      }] : []),
      {
        code: 'search_console_not_connected',
        priority: 'measurement_setup',
        message: 'Connect Search Console search-performance reporting to evaluate impressions, clicks, and indexing gaps by canonical topic URL.',
      },
      {
        code: 'crawler_logs_not_ingested',
        priority: 'measurement_setup',
        message: 'Route request-level Logpush evidence into a safe aggregation path before evaluating verified crawler fetches or response failures.',
      },
      {
        code: 'tollbit_unverified',
        priority: 'policy_verification',
        message: 'Do not report Tollbit or paid access until the publisher integration and request identity are verified against supported requirements.',
      },
    ],
    topics: topics.slice(start, start + pageSize),
  });
});

adminContentRouter.get('/rag/accuracy', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  const nowSeconds = now();
  const since = nowSeconds - 30 * 86_400;
  const row = await c.env.DB.prepare(`
    SELECT COUNT(*) AS feedback_count,
      SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) AS positive_count,
      AVG(rating) AS average_rating
    FROM chat_feedback
    WHERE created_at >= ? AND (expires_at IS NULL OR expires_at > ?)
  `).bind(since, nowSeconds)
    .first<{ feedback_count: number | string | null; positive_count: number | string | null; average_rating: number | string | null }>();
  const feedbackCount = metricCount(row?.feedback_count);
  const positiveCount = metricCount(row?.positive_count);
  return c.json({
    source: 'chat_feedback',
    metric: 'positive_feedback_rate',
    window_days: 30,
    has_data: feedbackCount > 0,
    feedback_count: feedbackCount,
    positive_count: positiveCount,
    average_rating: Number(row?.average_rating ?? 0),
    accuracy_pct: feedbackCount ? Math.round(positiveCount * 1000 / feedbackCount) / 10 : 0,
  });
});

adminContentRouter.get('/vector/stats', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  const row = await c.env.DB.prepare(`
    SELECT COUNT(*) AS total,
      SUM(CASE WHEN vector_id IS NOT NULL AND TRIM(vector_id) != '' THEN 1 ELSE 0 END) AS embedded,
      SUM(CASE WHEN chapter_id IS NULL THEN 1 ELSE 0 END) AS other_total,
      SUM(CASE WHEN chapter_id IS NULL AND vector_id IS NOT NULL AND TRIM(vector_id) != '' THEN 1 ELSE 0 END) AS other_embedded,
      SUM(CASE WHEN chapter_id IS NOT NULL THEN 1 ELSE 0 END) AS chapter_total,
      SUM(CASE WHEN chapter_id IS NOT NULL AND vector_id IS NOT NULL AND TRIM(vector_id) != '' THEN 1 ELSE 0 END) AS chapter_embedded
    FROM chunks
  `).first<Record<string, number | string | null>>();
  const total = metricCount(row?.total);
  const embedded = metricCount(row?.embedded);
  const otherTotal = metricCount(row?.other_total);
  const otherEmbedded = metricCount(row?.other_embedded);
  const chapterTotal = metricCount(row?.chapter_total);
  const chapterEmbedded = metricCount(row?.chapter_embedded);
  const coverage = (count: number, denominator: number) => denominator
    ? Math.round(count * 1000 / denominator) / 10
    : 0;
  return c.json({
    source: 'chunks',
    has_data: total > 0,
    total,
    embedded,
    overall_coverage_pct: coverage(embedded, total),
    pages: {
      total: otherTotal,
      embedded: otherEmbedded,
      coverage_pct: coverage(otherEmbedded, otherTotal),
    },
    chapters: {
      total: chapterTotal,
      embedded: chapterEmbedded,
      coverage_pct: coverage(chapterEmbedded, chapterTotal),
    },
  });
});

adminContentRouter.get('/perf/latency', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  const since = now() - 7 * 86_400;
  const predicate = `
    FROM analytics_events
    WHERE event_subtype = 'chat_completion'
      AND classification = 'essential_operational'
      AND created_at >= ?
      AND CAST(json_extract(payload, '$.latency_ms') AS REAL) > 0
  `;
  const [overall, daily] = await Promise.all([
    c.env.DB.prepare(`
      WITH samples AS (
        SELECT CAST(json_extract(payload, '$.latency_ms') AS REAL) AS latency_ms
        ${predicate}
      ), ranked AS (
        SELECT latency_ms,
          ROW_NUMBER() OVER (ORDER BY latency_ms) AS rank_no,
          COUNT(*) OVER () AS sample_count
        FROM samples
      )
      SELECT MAX(sample_count) AS sample_count,
        ROUND(AVG(latency_ms), 1) AS avg_ms,
        MAX(CASE WHEN rank_no = CAST((sample_count * 95 + 99) / 100 AS INTEGER)
          THEN latency_ms END) AS p95_ms
      FROM ranked
    `).bind(since).first<{ sample_count: number | string | null; avg_ms: number | string | null; p95_ms: number | string | null }>(),
    c.env.DB.prepare(`
      WITH samples AS (
        SELECT date(created_at, 'unixepoch') AS date,
          CAST(json_extract(payload, '$.latency_ms') AS REAL) AS latency_ms
        ${predicate}
      ), ranked AS (
        SELECT date, latency_ms,
          ROW_NUMBER() OVER (PARTITION BY date ORDER BY latency_ms) AS rank_no,
          COUNT(*) OVER (PARTITION BY date) AS sample_count
        FROM samples
      )
      SELECT date, MAX(sample_count) AS samples,
        ROUND(AVG(latency_ms), 1) AS avg_ms,
        MAX(CASE WHEN rank_no = CAST((sample_count * 95 + 99) / 100 AS INTEGER)
          THEN latency_ms END) AS p95_ms
      FROM ranked
      GROUP BY date
      ORDER BY date
    `).bind(since).all<{ date: string; samples: number | string; avg_ms: number | string; p95_ms: number | string }>(),
  ]);
  const sampleCount = metricCount(overall?.sample_count);
  return c.json({
    source: 'analytics_events',
    window_days: 7,
    has_data: sampleCount > 0,
    sample_count: sampleCount,
    avg_ms: Number(overall?.avg_ms ?? 0),
    p95_ms: Number(overall?.p95_ms ?? 0),
    daily: (daily.results ?? []).map(row => ({
      date: row.date,
      samples: metricCount(row.samples),
      avg_ms: Number(row.avg_ms ?? 0),
      p95_ms: Number(row.p95_ms ?? 0),
    })),
  });
});

adminContentRouter.get('/analytics/queries', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  const since = now() - 7 * 86_400;
  const [totals, rows] = await Promise.all([
    c.env.DB.prepare(`
      SELECT COUNT(DISTINCT lower(trim(content))) AS total_unique
      FROM chats
      WHERE role = 'user' AND created_at >= ?
        AND (expires_at IS NULL OR expires_at > ?)
        AND length(trim(content)) > 0
    `).bind(since, now()).first<{ total_unique: number | string | null }>(),
    c.env.DB.prepare(`
      SELECT MIN(trim(content)) AS query, COUNT(*) AS count
      FROM chats
      WHERE role = 'user' AND created_at >= ?
        AND (expires_at IS NULL OR expires_at > ?)
        AND length(trim(content)) > 0
      GROUP BY lower(trim(content))
      ORDER BY count DESC, query COLLATE NOCASE
      LIMIT 10
    `).bind(since, now()).all<{ query: string; count: number | string }>(),
  ]);
  const topQueries = (rows.results ?? []).map(row => ({ query: row.query, count: metricCount(row.count) }));
  return c.json({
    source: 'chats',
    window_days: 7,
    has_data: metricCount(totals?.total_unique) > 0,
    total_unique: metricCount(totals?.total_unique),
    top_queries: topQueries,
  });
});

adminContentRouter.get('/monetization/funnel', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  const rows = await c.env.DB.prepare(`
    SELECT COALESCE(subscription_tier, 'free') AS tier, COUNT(*) AS count
    FROM users
    WHERE deleted_at IS NULL AND role NOT IN ('admin', 'staff')
    GROUP BY COALESCE(subscription_tier, 'free')
  `).all<{ tier: string; count: number | string }>();
  const counts = new Map((rows.results ?? []).map(row => [row.tier, metricCount(row.count)]));
  const funnel = [
    { stage: 'Free-tier records', count: counts.get('free') ?? 0 },
    { stage: 'Starter-tier records', count: counts.get('starter') ?? 0 },
    { stage: 'Pro / premium-tier records', count: (counts.get('pro') ?? 0) + (counts.get('premium') ?? 0) },
  ];
  const totalCount = funnel.reduce((sum, row) => sum + row.count, 0);
  return c.json({
    source: 'users.subscription_tier',
    note: 'Historical account labels only; this is not billing or conversion data.',
    has_data: totalCount > 0,
    total_count: totalCount,
    funnel,
  });
});

// These reporting endpoints deliberately expose finalized provider receipts
// only. Browser ad beacons and manually entered estimates are not earnings.
adminContentRouter.get('/ads/revenue', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  c.header('Cache-Control', 'private, no-store');

  const requestedDays = Number(c.req.query('days') ?? 30);
  const days = [7, 30, 90].includes(requestedDays) ? requestedDays : 30;
  const generatedAt = now();
  const since = generatedAt - days * 86_400;
  const [reportRows, reconciliation] = await Promise.all([
    c.env.DB.prepare(`
      SELECT id, period_start, period_end, settlement_period, currency,
        gross_revenue_paise, adjustments_paise, provider_fees_paise,
        net_revenue_paise, monetized_impressions, finalized,
        finalized_through_at, fetched_at, freshness_expires_at,
        source_reference, created_at
      FROM ad_revenue_reports
      WHERE network = 'adsense' AND finalized = 1 AND period_end >= ?
      ORDER BY period_end DESC, fetched_at DESC
      LIMIT 100
    `).bind(since).all<Record<string, number | string>>(),
    c.env.DB.prepare(`
      SELECT status, started_at, completed_at, weeks, fetched, imported,
        idempotent, calculated, failures_json
      FROM adsense_reconciliation_status
      WHERE id = 'singleton'
    `).first<{
      status: 'running' | 'completed' | 'skipped' | 'failed';
      started_at: number;
      completed_at: number | null;
      weeks: number;
      fetched: number;
      imported: number;
      idempotent: number;
      calculated: number;
      failures_json: string;
    }>(),
  ]);

  const receipts = (reportRows.results ?? []).map(row => ({
    id: row.id,
    period_start: metricCount(row.period_start),
    period_end: metricCount(row.period_end),
    settlement_period: row.settlement_period,
    currency: row.currency,
    gross_revenue_paise: metricCount(row.gross_revenue_paise),
    adjustments_paise: metricCount(row.adjustments_paise),
    provider_fees_paise: metricCount(row.provider_fees_paise),
    net_revenue_paise: Number(row.net_revenue_paise ?? 0),
    monetized_impressions: metricCount(row.monetized_impressions),
    finalized_through_at: metricCount(row.finalized_through_at),
    fetched_at: metricCount(row.fetched_at),
    freshness_expires_at: metricCount(row.freshness_expires_at),
    source_reference: row.source_reference,
    stale: metricCount(row.freshness_expires_at) <= generatedAt,
  }));
  const totals = receipts.reduce((sum, report) => ({
    gross_revenue_paise: sum.gross_revenue_paise + report.gross_revenue_paise,
    adjustments_paise: sum.adjustments_paise + report.adjustments_paise,
    provider_fees_paise: sum.provider_fees_paise + report.provider_fees_paise,
    net_revenue_paise: sum.net_revenue_paise + report.net_revenue_paise,
    monetized_impressions: sum.monetized_impressions + report.monetized_impressions,
  }), {
    gross_revenue_paise: 0,
    adjustments_paise: 0,
    provider_fees_paise: 0,
    net_revenue_paise: 0,
    monetized_impressions: 0,
  });

  const failures = parseAdSenseFailures(reconciliation?.failures_json);
  const endpointConfigured = Boolean(c.env.ADSENSE_REPORT_URL?.trim());
  return c.json({
    source: 'ad_revenue_reports',
    revenue_basis: 'finalized_provider_receipts',
    range_days: days,
    generated_at: generatedAt,
    endpoint_configured: endpointConfigured,
    token_configured: Boolean(c.env.ADSENSE_REPORT_TOKEN?.trim()),
    reconciliation: reconciliation ? {
      status: reconciliation.status,
      started_at: reconciliation.started_at,
      completed_at: reconciliation.completed_at,
      weeks: reconciliation.weeks,
      fetched: reconciliation.fetched,
      imported: reconciliation.imported,
      idempotent: reconciliation.idempotent,
      calculated: reconciliation.calculated,
      failures,
    } : null,
    receipts,
    totals,
    note: 'Totals include finalized aggregate AdSense provider receipts only. They are not estimates and are not attributed by placement.',
  });
});

adminContentRouter.get('/seo/pipeline-status', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  const row = await c.env.DB.prepare(`
    SELECT COUNT(*) AS total_chapters,
      SUM(CASE WHEN status = 'published' THEN 1 ELSE 0 END) AS published,
      SUM(CASE WHEN notes_en IS NOT NULL AND TRIM(notes_en) != '' THEN 1 ELSE 0 END) AS with_english_notes,
      SUM(CASE WHEN notes_as IS NOT NULL AND TRIM(notes_as) != '' THEN 1 ELSE 0 END) AS with_assamese_notes
    FROM chapters
  `).first<Record<string, number | string | null>>();
  const total = metricCount(row?.total_chapters);
  const published = metricCount(row?.published);
  const english = metricCount(row?.with_english_notes);
  const assamese = metricCount(row?.with_assamese_notes);
  return c.json({
    source: 'chapters',
    total_chapters: total,
    total_topics: total,
    pages_total: published,
    published,
    has_content: english,
    with_assamese_notes: assamese,
    needs_english_notes: Math.max(0, total - english),
  });
});

adminContentRouter.get('/notification-prefs', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  const stored = await readAdminConfig(c.env, 'notification-prefs', DEFAULT_NOTIFICATION_PREFS);
  return c.json({ ...DEFAULT_NOTIFICATION_PREFS, ...stored });
});

adminContentRouter.put('/notification-prefs', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  const body = await c.req.json<Record<string, unknown>>();
  const allowed = Object.keys(DEFAULT_NOTIFICATION_PREFS);
  const current = await readAdminConfig(c.env, 'notification-prefs', DEFAULT_NOTIFICATION_PREFS);
  const updates = Object.fromEntries(Object.entries(body).filter(([key]) => allowed.includes(key)));
  if (!Object.keys(updates).length) return c.json({ detail: 'No valid fields to update' }, 400);
  const saved = { ...current, ...updates };
  await writeAdminConfig(c.env, 'notification-prefs', saved);
  return c.json({ ...DEFAULT_NOTIFICATION_PREFS, ...saved });
});

adminContentRouter.get('/alert-settings', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  const stored = await readAdminConfig(c.env, 'alert-settings', DEFAULT_ALERT_SETTINGS);
  const settings = {
    thresholds: { ...DEFAULT_ALERT_SETTINGS.thresholds, ...(stored.thresholds ?? {}) },
    expiration: { ...DEFAULT_ALERT_SETTINGS.expiration, ...(stored.expiration ?? {}) },
  };
  return c.json({ ...settings, defaults: DEFAULT_ALERT_SETTINGS, source: 'd1' });
});

adminContentRouter.put('/alert-settings', async c => {
  const actor = await requireAdmin(c);
  if (actor instanceof Response) return actor;
  const body = await c.req.json<{ thresholds?: Record<string, unknown>; expiration?: Record<string, unknown> }>();
  if (!body || typeof body !== 'object' || !body.thresholds || !body.expiration) {
    return c.json({ detail: 'thresholds and expiration are required' }, 400);
  }
  const saved = {
    thresholds: body.thresholds,
    expiration: body.expiration,
  };
  await writeAdminConfig(c.env, 'alert-settings', saved);
  return c.json({ ...saved, defaults: DEFAULT_ALERT_SETTINGS, source: 'd1' });
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
    titleAs: chapters.titleAs,
    slug: chapters.slug,
    slugAs: chapters.slugAs,
    chapterNumber: chapters.chapterNumber,
    status: chapters.status,
    contentType: chapters.contentType,
    notesEn: chapters.notesEn,
    notesAs: chapters.notesAs,
    qaEn: chapters.qaEn,
    publishedTopics: chapters.publishedTopics,
    pyqPdfUrl: chapters.pyqPdfUrl,
    pyqPapers: chapters.pyqPapers,
  }).from(chapters).where(publicChapterListWhere(subjectId)).orderBy(chapters.chapterNumber);

  const payload = serializePublicChapterList(rows);
  await env.CONTENT_KV.put(`subject:${subjectId}:chapters`, JSON.stringify(payload), { expirationTtl: 86400 * 7 });
}

function step(steps: JobStep[], name: string): JobStep {
  const found = steps.find(item => item.name === name);
  if (!found) throw new Error(`Unknown publish step ${name}`);
  return found;
}

async function runNativeReindex(env: Env, chapter: {
  id: string; subjectId: string; notesEn: string | null; notesAs: string | null;
}): Promise<Record<string, unknown>> {
  // Publishing and staff editing intentionally share the same cleanup-first
  // implementation. Do not mark a publish complete when a requested RAG
  // scope failed: runPublish records this step as failed/partial.
  const results = await reindexChapterRag(env, chapter.id, ['notes', 'qa', 'pyq']);
  const failed = Object.entries(results).filter(([, result]) => result.error);
  if (failed.length) throw new Error(`RAG reindex failed: ${failed.map(([scope, result]) => `${scope}: ${result.error}`).join('; ')}`);
  return { status: 'done', vectors: Object.values(results).reduce((total, result) => total + result.chunks, 0), results };
  /*
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
  */
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
    await run('status_update', async () => {
      await db.update(chapters).set({ status: 'published', updatedAt: now() }).where(eq(chapters.id, chapter.id));
      return { status: 'done' };
    }, true);
    await run('cloudflare', async () => {
      await prewarmSubject(env, chapter.subjectId);
      return { status: 'done', cache: 'CONTENT_KV' };
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
    // Search visibility is a required publish outcome, not an optional
    // telemetry step. A failed cleanup/index must leave the job retryable.
    await run('rag_reindex', () => runNativeReindex(env, chapter), true);
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
  if (requested.length) {
    if (medium === 'as') {
      const placeholders = requested.map(() => '?').join(', ');
      const eligible = await c.env.DB.prepare(
        `SELECT id FROM chapters
         WHERE id IN (${placeholders})
           AND notes_en IS NOT NULL AND TRIM(notes_en) != ''`,
      ).bind(...requested).all<{ id: string }>();
      const eligibleIds = new Set((eligible.results ?? []).map(row => row.id));
      candidateIds = requested.filter(id => eligibleIds.has(id));
    } else {
      candidateIds = requested;
    }
  } else {
    const field = medium === 'en' ? 'notes_en' : 'notes_as';
    const subject = typeof body.subject === 'string' ? body.subject.trim() : '';
    const board = typeof body.board === 'string' ? body.board.trim() : '';
    const predicates = [
      !force ? `(c.${field} IS NULL OR TRIM(c.${field}) = '')` : '1 = 1',
      medium === 'as' ? `(c.notes_en IS NOT NULL AND TRIM(c.notes_en) != '')` : '1 = 1',
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
  const chapterIds = candidateIds;
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
  return c.redirect(new URL(`/api/v1/staff/content/chapters/${encodeURIComponent(subjectId)}`, c.req.url).toString(), 307);
});
adminContentRouter.patch('/content/chapters/:chapterId', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return c.redirect(new URL(`/api/v1/staff/content/chapter/${encodeURIComponent(c.req.param('chapterId'))}`, c.req.url).toString(), 307);
});
adminContentRouter.delete('/content/chapters/:chapterId', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return c.redirect(new URL(`/api/v1/staff/content/chapter/${encodeURIComponent(c.req.param('chapterId'))}`, c.req.url).toString(), 307);
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
    const content = sanitizeGeneratedNotes(result.text);
    await db.update(chapters).set({ notesEn: content, ragUpdatedAt: now(), updatedAt: now() })
      .where(eq(chapters.id, chapter.id));
    return c.json({
      status: 'generated',
      chapter_id: chapter.id,
      model: result.model,
      content,
      notes_en: content,
      word_count: content.split(/\s+/).filter(Boolean).length,
    });
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

// Cleans up content pasted from PDFs/Word docs: rebuilds broken LaTeX math
// delimiters and reflows ASCII-art tables into proper Markdown tables,
// without paraphrasing or altering the underlying text.
adminContentRouter.post('/content/format-text', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const body = await safeBody(c);
  const text = typeof body.text === 'string' ? body.text : '';
  if (!text.trim()) return c.json({ detail: 'text is required' }, 422);
  if (text.length > 12000) return c.json({ detail: 'text is too long to format in one pass (max 12000 characters)' }, 422);
  try {
    const result = await generate(c.env.AI, {
      systemPrompt: [
        'You are a formatting-only editor for study notes pasted from PDFs and Word documents.',
        'Fix ONLY structural formatting damage introduced by copy-pasting:',
        '- Rebuild broken math into valid inline ($...$) or block ($$...$$) LaTeX (fix stray spaces, split symbols, missing delimiters, garbled exponents/subscripts/fractions).',
        '- Reflow misaligned or ASCII-art tables into proper GitHub-flavoured Markdown tables (header row + |---| separator).',
        '- Fix broken Markdown headings, list markers, and line breaks that were mangled by the paste.',
        'Never rewrite, summarize, translate, shorten, or add content. Preserve every fact, number, and word.',
        'Return ONLY the corrected Markdown — no commentary, no code fences, no explanation.',
      ].join(' '),
      userMessage: text,
      maxTokens: 2048,
    });
    return c.json({ status: 'formatted', formatted_text: result.text.trim(), model: result.model });
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'Formatting failed' }, 502);
  }
});

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

adminContentRouter.get('/content/assamese/coverage', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const rows = await c.env.DB.prepare(`
    SELECT c.id, c.title, c.chapter_number, c.status,
           CASE
             WHEN c.notes_as IS NOT NULL AND TRIM(c.notes_as) != '' THEN 1
             ELSE 0
           END AS has_assamese,
           s.id AS subject_id, s.name AS subject_name
    FROM chapters c
    JOIN subjects s ON s.id = c.subject_id
    WHERE c.notes_en IS NOT NULL AND TRIM(c.notes_en) != ''
    ORDER BY s.name, c.chapter_number, c.title
  `).all<{
    id: string; title: string; chapter_number: number | null; status: string | null;
    has_assamese: number; subject_id: string; subject_name: string;
  }>();
  const chaptersWithEnglish = rows.results ?? [];
  const translated = chaptersWithEnglish.filter(row => Number(row.has_assamese) === 1).length;
  const groups = new Map<string, {
    subject_id: string; subject_name: string; total: number; translated: number;
    missing: number; chapters: Array<{ id: string; title: string; chapter_number: number | null; status: string | null }>;
  }>();
  for (const row of chaptersWithEnglish) {
    const hasAssamese = Number(row.has_assamese) === 1;
    const group = groups.get(row.subject_id) ?? {
      subject_id: row.subject_id, subject_name: row.subject_name,
      total: 0, translated: 0, missing: 0, chapters: [],
    };
    group.total++;
    if (hasAssamese) {
      group.translated++;
    } else {
      group.missing++;
      group.chapters.push({
        id: row.id, title: row.title, chapter_number: row.chapter_number, status: row.status,
      });
    }
    groups.set(row.subject_id, group);
  }
  const total = chaptersWithEnglish.length;
  return c.json({
    total, translated, missing: total - translated,
    progress: total ? Math.round(translated * 100 / total) : 0,
    ratio: total ? translated / total : 0,
    subjects: [...groups.values()],
  });
});

adminContentRouter.get('/content/assamese/progress', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const run = await createDb(c.env.DB).select().from(seedRuns)
    .where(eq(seedRuns.medium, 'as')).orderBy(desc(seedRuns.startedAt)).limit(1).get();
  if (!run) return c.json({ running: false, run: null });
  const log = parseJson<SeedLog[]>(run.log, []);
  const queued = log.filter(entry => entry.status === 'queued' || entry.status === 'running').length;
  return c.json({
    running: run.status === 'queued' || run.status === 'running',
    run: {
      id: run.id, status: run.status, total: run.totalChapters,
      completed: run.processed, failed: run.failed, queued,
      started_at: run.startedAt ? new Date(run.startedAt * 1000).toISOString() : null,
      finished_at: run.completedAt ? new Date(run.completedAt * 1000).toISOString() : null,
      errors: log.filter(entry => entry.status === 'failed'),
    },
  });
});

adminContentRouter.post('/content/assamese/backfill', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return launchSeed(c, 'as');
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
  const total = metricCount(row?.total as number | string | null | undefined);
  const notes = metricCount(row?.notes as number | string | null | undefined);
  const assamese = metricCount(row?.assamese as number | string | null | undefined);
  return c.json({
    source: 'd1',
    has_data: total > 0,
    total,
    notes,
    assamese,
    english_coverage_pct: total ? Math.round(notes * 1000 / total) / 10 : 0,
    assamese_coverage_pct: total ? Math.round(assamese * 1000 / total) / 10 : 0,
  });
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
  const chapterId = c.req.param('chapterId');
  const existing = await db.select({ id: chapters.id }).from(chapters).where(eq(chapters.id, chapterId)).get();
  if (!existing) return c.json({ detail: 'Chapter not found' }, 404);
  const changedAt = now();
  await db.update(chapters).set({
    ragText: typeof body.rag_text_en === 'string' ? body.rag_text_en : undefined,
    ragTextAs: typeof body.rag_text_as === 'string' ? body.rag_text_as : undefined,
    // qa_rag_text is a visible editor input. Preserve it as a canonical
    // one-section QA document so the staff detail serializer can round-trip it.
    qaEn: typeof body.qa_rag_text_en === 'string' ? JSON.stringify(body.qa_rag_text_en ? [{ content: body.qa_rag_text_en }] : []) : undefined,
    qaAs: typeof body.qa_rag_text_as === 'string' ? JSON.stringify(body.qa_rag_text_as ? [{ content: body.qa_rag_text_as }] : []) : undefined,
    ragUpdatedAt: changedAt,
    updatedAt: changedAt,
  }).where(eq(chapters.id, chapterId));
  // Deliberately do not claim freshness until the explicit reindex endpoint has
  // successfully replaced all scope mappings.
  return c.json({ status: 'saved', chapter_id: chapterId, reindex_required: true });
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

async function publishSubjectBlog(c: Context<{ Bindings: Env }>): Promise<Response> {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const body = await safeBody(c);
  const requested = Array.isArray(body.chapter_ids) ? body.chapter_ids.filter((id): id is string => typeof id === 'string') : [];
  const subjectId = c.req.param('subjectId') as string;
  const subject = await createDb(c.env.DB).select({ id: subjects.id, name: subjects.name })
    .from(subjects).where(eq(subjects.id, subjectId)).get();
  if (!subject) return c.json({ detail: 'Subject not found' }, 404);
  const rows = requested.length
    ? await createDb(c.env.DB).select({
      id: chapters.id, title: chapters.title, chapterNumber: chapters.chapterNumber, notes: chapters.notesEn,
    }).from(chapters)
      .where(and(eq(chapters.subjectId, subjectId), inArray(chapters.id, requested)))
    : await createDb(c.env.DB).select({
      id: chapters.id, title: chapters.title, chapterNumber: chapters.chapterNumber, notes: chapters.notesEn,
    }).from(chapters).where(eq(chapters.subjectId, subjectId)).orderBy(chapters.chapterNumber);
  if (requested.length && rows.length !== new Set(requested).size) {
    return c.json({ detail: 'Every requested chapter must belong to the destination subject.' }, 422);
  }
  const orderedRows = [...rows].sort(
    (left, right) => (left.chapterNumber ?? Number.MAX_SAFE_INTEGER) - (right.chapterNumber ?? Number.MAX_SAFE_INTEGER),
  );
  const mergeableRows = orderedRows.filter(row => row.notes?.trim());
  const headings = mergeableRows.map(row => ({
    level: 2,
    text: row.title,
    anchor: slugify(row.title),
  }));
  const mergedMarkdown = mergeableRows
    .map(row => `## ${row.title}\n\n${row.notes!.trim()}`)
    .join('\n\n---\n\n');
  if (!mergedMarkdown) return c.json({ detail: 'No chapter notes are available to publish.' }, 422);
  const wordCount = mergedMarkdown.split(/\s+/).filter(Boolean).length;
  const documentId = `subject-blog:${subjectId}`;
  const publishedAt = now();
  const document = {
    document_type: 'subject_blog',
    subject_id: subject.id,
    title: `${subject.name} Complete Study Guide`,
    merged_md: mergedMarkdown,
    headings: JSON.stringify(headings),
    chapter_count: orderedRows.length,
    word_count: wordCount,
    published_at: new Date(publishedAt * 1000).toISOString(),
  };
  await c.env.DB.prepare(`
    INSERT INTO cms_documents (id, data, status, created_at, updated_at)
    VALUES (?, ?, 'published', ?, ?)
    ON CONFLICT(id) DO UPDATE SET data = excluded.data, status = 'published', updated_at = excluded.updated_at
  `).bind(documentId, JSON.stringify(document), publishedAt, publishedAt).run();
  const jobIds: string[] = [];
  for (const row of orderedRows) {
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
  return c.json({
    subject_id: subject.id,
    subject_name: subject.name,
    document_id: documentId,
    status: 'published',
    chapter_count: orderedRows.length,
    word_count: wordCount,
    queued: jobIds.length,
    job_ids: jobIds,
  });
}

adminContentRouter.post('/content/subjects/:subjectId/blog-publish', publishSubjectBlog);

adminContentRouter.post('/content/subjects/:subjectId/bulk-publish', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const body = await safeBody(c);
  const requested = Array.isArray(body.chapter_ids) ? body.chapter_ids.filter((id): id is string => typeof id === 'string') : [];
  const subjectId = c.req.param('subjectId');
  const rows = requested.length
    ? await createDb(c.env.DB).select({ id: chapters.id }).from(chapters)
      .where(and(eq(chapters.subjectId, subjectId), inArray(chapters.id, requested)))
    : await createDb(c.env.DB).select({ id: chapters.id }).from(chapters).where(eq(chapters.subjectId, subjectId));
  if (requested.length && rows.length !== new Set(requested).size) {
    return c.json({ detail: 'Every requested chapter must belong to the destination subject.' }, 422);
  }
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

function validateCloudflareAnalyticsHealthResult(
  body: Record<string, unknown>,
): CloudflareAnalyticsHealthResult | null {
  const status = body.status;
  const checkedAt = body.checked_at;
  const error = body.error;
  const remediation = body.remediation;
  const needsRotation = body.needs_rotation;
  const hourlyBucketsReturned = body.hourly_buckets_returned;
  const hourlyBucketCount = body.hourly_bucket_count;
  const uniqueVisitorsSupported = body.unique_visitors_supported;

  if (status !== 'healthy' && status !== 'unhealthy') return null;
  if (
    typeof checkedAt !== 'string'
    || checkedAt.length > 64
    || Number.isNaN(Date.parse(checkedAt))
  ) return null;
  if (!(error === null || (typeof error === 'string' && error.length <= 500))) return null;
  if (!(remediation === null || (typeof remediation === 'string' && remediation.length <= 500))) return null;
  if (typeof needsRotation !== 'boolean' || typeof hourlyBucketsReturned !== 'boolean') return null;
  if (!(uniqueVisitorsSupported === null || typeof uniqueVisitorsSupported === 'boolean')) return null;

  const hasHourlyBucketCount = typeof hourlyBucketCount !== 'undefined';
  if (status === 'healthy') {
    if (
      !hasHourlyBucketCount
      || typeof hourlyBucketCount !== 'number'
      || !Number.isInteger(hourlyBucketCount)
      || hourlyBucketCount < 0
      || hourlyBucketCount > 10_000
    ) return null;
    if (error !== null || remediation !== null || hourlyBucketsReturned !== true) return null;
  } else {
    if (hasHourlyBucketCount && hourlyBucketCount !== null) {
      if (
        typeof hourlyBucketCount !== 'number'
        || !Number.isInteger(hourlyBucketCount)
        || hourlyBucketCount < 0
        || hourlyBucketCount > 10_000
      ) return null;
    }
    if (typeof error !== 'string' || !error.trim() || typeof remediation !== 'string' || !remediation.trim()) return null;
    if (hourlyBucketsReturned !== false) return null;
  }

  return {
    status,
    checked_at: checkedAt,
    error: error as string | null,
    remediation: remediation as string | null,
    needs_rotation: needsRotation,
    hourly_buckets_returned: hourlyBucketsReturned,
    ...(typeof hourlyBucketCount === 'number' ? { hourly_bucket_count: hourlyBucketCount } : {}),
    unique_visitors_supported: uniqueVisitorsSupported as boolean | null,
  };
}

function cloudflareAnalyticsStatusResponse(row: {
  status: string;
  checked_at: string;
  error: string | null;
  remediation: string | null;
  needs_rotation: number;
  hourly_buckets_returned: number;
  hourly_bucket_count: number | null;
  unique_visitors_supported: number | null;
  consecutive_failures: number;
} | null) {
  if (!row) {
    return {
      configured: false,
      auth_ok: false,
      needs_rotation: false,
      last_error: null,
      last_check_at: null,
      blocked_for_seconds: 0,
      consecutive_failures: 0,
      rotation_hint: 'No Cloudflare analytics health result has been received yet.',
      status: 'never_observed',
    };
  }
  return {
    configured: true,
    auth_ok: row.status === 'healthy',
    needs_rotation: row.needs_rotation === 1,
    last_error: row.error,
    last_check_at: row.checked_at,
    blocked_for_seconds: 0,
    consecutive_failures: row.consecutive_failures,
    rotation_hint: row.remediation,
    status: row.status,
    checked_at: row.checked_at,
    error: row.error,
    remediation: row.remediation,
    hourly_buckets_returned: row.hourly_buckets_returned === 1,
    hourly_bucket_count: row.hourly_bucket_count,
    unique_visitors_supported: row.unique_visitors_supported === null
      ? null
      : row.unique_visitors_supported === 1,
  };
}

// The admin banner's native read surface. It is deliberately admin-session
// protected; the GitHub workflow writes through the separate cron route below.
adminContentRouter.get('/analytics/cf-status', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  const row = await c.env.DB.prepare(`
    SELECT status, checked_at, error, remediation, needs_rotation,
           hourly_buckets_returned, hourly_bucket_count,
           unique_visitors_supported, consecutive_failures
    FROM cloudflare_analytics_health
    WHERE id = 'singleton'
  `).first<{
    status: string;
    checked_at: string;
    error: string | null;
    remediation: string | null;
    needs_rotation: number;
    hourly_buckets_returned: number;
    hourly_bucket_count: number | null;
    unique_visitors_supported: number | null;
    consecutive_failures: number;
  }>();
  return c.json(cloudflareAnalyticsStatusResponse(row ?? null));
});

// GitHub Actions posts the result after every probe, including failures. Do
// not let a malformed or unauthenticated handoff alter the durable state.
adminContentRouter.post('/cron/cloudflare-analytics-result', async c => {
  if (!cronAuthorized(c)) return c.json({ detail: 'Valid TRANSLATE_CRON_SECRET required' }, 401);
  const result = validateCloudflareAnalyticsHealthResult(await safeBody(c));
  if (!result) {
    return c.json({ detail: 'Malformed Cloudflare analytics health result' }, 422);
  }

  const timestamp = now();
  await c.env.DB.prepare(`
    INSERT INTO cloudflare_analytics_health (
      id, status, checked_at, error, remediation, needs_rotation,
      hourly_buckets_returned, hourly_bucket_count, unique_visitors_supported,
      consecutive_failures, updated_at
    ) VALUES (
      'singleton', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    )
    ON CONFLICT(id) DO UPDATE SET
      status = excluded.status,
      checked_at = excluded.checked_at,
      error = excluded.error,
      remediation = excluded.remediation,
      needs_rotation = excluded.needs_rotation,
      hourly_buckets_returned = excluded.hourly_buckets_returned,
      hourly_bucket_count = excluded.hourly_bucket_count,
      unique_visitors_supported = excluded.unique_visitors_supported,
      consecutive_failures = CASE
        WHEN excluded.status = 'unhealthy' THEN cloudflare_analytics_health.consecutive_failures + 1
        ELSE 0
      END,
      updated_at = excluded.updated_at
  `).bind(
    result.status,
    result.checked_at,
    result.error,
    result.remediation,
    result.needs_rotation ? 1 : 0,
    result.hourly_buckets_returned ? 1 : 0,
    result.hourly_bucket_count ?? null,
    result.unique_visitors_supported === null ? null : (result.unique_visitors_supported ? 1 : 0),
    result.status === 'unhealthy' ? 1 : 0,
    timestamp,
  ).run();

  return c.json({ status: 'saved', checked_at: result.checked_at });
});

async function bulkMirrorRag(c: Context<{ Bindings: Env }>) {
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
}

async function startBulkReindex(c: Context<{ Bindings: Env }>) {
  const limit = Math.max(1, Math.min(Number(c.req.query('limit') ?? 50), 100));
  const subjectId = c.req.query('subject_id');
  const force = c.req.query('force') === 'true';
  const rows = await createDb(c.env.DB).select({
    id: chapters.id,
  }).from(chapters).where(and(
    subjectId ? eq(chapters.subjectId, subjectId) : undefined,
    force ? undefined : or(
      isNull(chapters.ragIndexedAt),
      and(isNotNull(chapters.ragUpdatedAt), lt(chapters.ragIndexedAt, chapters.ragUpdatedAt)),
    ),
  )).limit(limit);
  if (!rows.length) return c.json({ job: 'nothing_to_do', total_queued: 0 });

  const jobId = crypto.randomUUID();
  const items = rows.map(row => ({ chapter_id: row.id, scopes: ['notes'], status: 'pending' }));
  const created = await c.env.DB.prepare(`
    INSERT INTO rag_reindex_jobs (id, actor_id, status, requested_scopes, items, created_at, updated_at)
    SELECT ?, ?, 'pending', '["notes"]', ?, ?, ?
    WHERE NOT EXISTS (
      SELECT 1 FROM rag_reindex_jobs WHERE status IN ('pending', 'running')
    )
  `).bind(jobId, null, JSON.stringify(items), now(), now()).run();
  if ((created.meta.changes ?? 0) !== 1) {
    return c.json({ detail: 'A bulk-reindex job is already running.' }, 409);
  }
  c.executionCtx.waitUntil(runRagJob(c.env, jobId));
  return c.json({ job: 'started', job_id: jobId, total_queued: rows.length });
}

async function bulkReindexJobStatus(c: Context<{ Bindings: Env }>) {
  const job = await c.env.DB.prepare(`
    SELECT id, status, items, error_log, created_at, updated_at, completed_at
    FROM rag_reindex_jobs ORDER BY created_at DESC LIMIT 1
  `).first<{
    id: string; status: string; items: string; error_log: string | null;
    created_at: number | null; updated_at: number | null; completed_at: number | null;
  }>();
  if (!job) {
    return c.json({ running: false, total: 0, processed: 0, skipped: 0, errors: [] });
  }
  const items = parseJson<Array<{ status?: string; error?: string }>>(job.items, []);
  return c.json({
    job_id: job.id,
    job: job.status,
    running: job.status === 'pending' || job.status === 'running',
    total: items.length,
    processed: items.filter(item => item.status === 'done').length,
    skipped: 0,
    errors: items.filter(item => item.status === 'failed').map(item => item.error || 'RAG reindex failed'),
    created_at: job.created_at,
    updated_at: job.updated_at,
    completed_at: job.completed_at,
  });
}

// Browser-facing RAG Mirror controls use the same staff bearer token as the
// rest of the Content Hub. Cron credentials never cross into frontend code.
adminContentRouter.post('/content/rag/mirror', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return bulkMirrorRag(c);
});
adminContentRouter.post('/content/rag/reindex', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return startBulkReindex(c);
});
adminContentRouter.get('/content/rag/reindex/status', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return bulkReindexJobStatus(c);
});

adminContentRouter.post('/cron/bulk-mirror-rag', async c => {
  if (!cronAuthorized(c)) return c.json({ detail: 'Valid TRANSLATE_CRON_SECRET required' }, 401);
  return bulkMirrorRag(c);
});

adminContentRouter.post('/cron/bulk-reindex', async c => {
  if (!cronAuthorized(c)) return c.json({ detail: 'Valid TRANSLATE_CRON_SECRET required' }, 401);
  return startBulkReindex(c);
});
adminContentRouter.get('/cron/bulk-reindex/status', async c => {
  if (!cronAuthorized(c)) return c.json({ detail: 'Valid TRANSLATE_CRON_SECRET required' }, 401);
  return bulkReindexJobStatus(c);
});
// Existing Content Editor alias; retain it without reintroducing a Cloud Run route.
adminContentRouter.post('/rag/bulk-reindex', async c => {
  const actor = await requireAdmin(c); if (actor instanceof Response) return actor;
  return startBulkReindex(c);
});

// Scheduled callers use a dedicated secret; they never inherit the browser
// session cookie and the edge proxy no longer substitutes Cloud Run OIDC.
adminContentRouter.post('/cron/translate/probe', async c => {
  if (!cronAuthorized(c)) return c.json({ detail: 'Valid TRANSLATE_CRON_SECRET required' }, 401);
  // Keep this probe deliberately side-effect free. It exists only to prove
  // that the GitHub secret and Worker binding agree before the bulk route is
  // allowed to enqueue a seed run.
  return c.json({
    authenticated: true,
    mutation_free: true,
    work_enqueued: false,
    probe: 'translation-cron-authentication',
  });
});

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