/**
 * Syrabit API — Cloudflare Workers Entry Point
 *
 * Stack: Hono + Drizzle ORM + D1 (SQLite)
 * Replaces the legacy Python and MongoDB deployment.
 *
 * Migration status:
 *   ✅ Phase 1 — Foundation + D1 schema
 *   ✅ Phase 2 — Auth layer (signup, login, refresh, me, reset)
 *   🚧 Phase 3 — Content API (chapters, subjects, PYQ)
 *   🚧 Phase 4 — Chat pipeline (SSE, Gemini, Sarvam, Vectorize)
 *   🚧 Phase 5 — Payments + Admin
 *   🚧 Phase 6 — Data migration + production cutover
 */

import { Hono } from 'hono';
import { applyCors } from './middleware/cors';
import { api } from './routes/index';
import type { Env } from './types';
import { createDb } from './db/client';
import { sql } from 'drizzle-orm';
import { resumePublishJobs, resumeSeedRuns } from './routes/admin-content';

const app = new Hono<{ Bindings: Env }>();

// ── CORS preflight ────────────────────────────────────────────────────────────
app.options('*', (c) => {
  const origin = c.req.header('Origin') ?? '';
  const res = new Response(null, { status: 204 });
  applyCors(res.headers, origin, c.env.ALLOWED_ORIGINS ?? '');
  return res;
});

// ── Apply CORS to all responses ───────────────────────────────────────────────
app.use('*', async (c, next) => {
  await next();
  const origin = c.req.header('Origin') ?? '';
  applyCors(c.res.headers, origin, c.env.ALLOWED_ORIGINS ?? '');
  // Mark responses handled by this Worker for operational diagnostics.
  if (!c.res.headers.has('X-Syrabit-Route')) {
    c.res.headers.set('X-Syrabit-Route', 'worker-native');
  }
});

// ── Request ID ────────────────────────────────────────────────────────────────
app.use('*', async (c, next) => {
  const requestId = c.req.header('X-Request-ID') ?? crypto.randomUUID();
  c.res.headers.set('X-Request-ID', requestId);
  await next();
});

// ── API routes ────────────────────────────────────────────────────────────────
app.route('/', api);

// ── Global error handler ──────────────────────────────────────────────────────
app.onError((err, c) => {
  console.error('[syrabit-api] Unhandled error:', err.message, err.stack);
  return c.json({
    detail: 'Internal server error',
    request_id: c.res.headers.get('X-Request-ID'),
  }, 500);
});

// ── Scheduled handler (Cron Triggers) ─────────────────────────────────────────
async function handleScheduled(controller: ScheduledController, env: Env): Promise<void> {
  const db = createDb(env.DB);
  const now = Math.floor(Date.now() / 1000);
  const cronExpr = controller.cron;
  // Resume D1-backed seed plans independently of any request lifetime.
  await resumeSeedRuns(env).catch(err => console.error('[cron] seed resume error:', err));
  await resumePublishJobs(env).catch(err => console.error('[cron] publish resume error:', err));

  // ── Hourly: clean up expired TTL records ──────────────────────────────────
  if (cronExpr === '0 * * * *') {
    try {
      const tables = [
        'email_failure_events',
        'payments_pending',
        'password_reset_tokens',
        'refresh_token_claims',
        'ai_usage_logs',
        'chat_feedback',
        'dead_letters',
        'content_audit_log',
        'seed_runs',
        'chats',
        'chat_request_claims',
      ];

      for (const table of tables) {
        await env.DB.prepare(
          `DELETE FROM ${table} WHERE expires_at IS NOT NULL AND expires_at < ?`
        ).bind(now).run().catch((e: unknown) => {
          console.warn(`[cron] TTL cleanup failed for ${table}:`, e);
        });
      }

      console.log('[cron] TTL cleanup complete');
    } catch (err) {
      console.error('[cron] TTL cleanup error:', err);
    }
  }

  // ── Daily: reset monthly message counts, expire subscriptions ────────────
  if (cronExpr === '0 0 * * *') {
    try {
      // Reset monthly counts for users whose last_reset_date was in a previous month
      const startOfMonth = new Date();
      startOfMonth.setUTCDate(1);
      startOfMonth.setUTCHours(0, 0, 0, 0);
      const startOfMonthTs = Math.floor(startOfMonth.getTime() / 1000);

      await env.DB.prepare(`
        UPDATE users
        SET monthly_message_count = 0,
            last_reset_date = ?
        WHERE last_reset_date < ?
      `).bind(now, startOfMonthTs).run();

      // Expire subscriptions whose period has ended
      await env.DB.prepare(`
        UPDATE users
        SET subscription_tier = 'free',
            subscription_status = 'cancelled'
        WHERE subscription_status = 'active'
          AND current_period_end IS NOT NULL
          AND current_period_end < ?
          AND subscription_tier != 'free'
      `).bind(now).run();

      console.log('[cron] Daily maintenance complete');
    } catch (err) {
      console.error('[cron] Daily maintenance error:', err);
    }
  }
}

export default {
  fetch: app.fetch,
  scheduled: handleScheduled,
} satisfies ExportedHandler<Env>;
