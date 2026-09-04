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
function cronErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message.slice(0, 512) : String(error).slice(0, 512);
}

export async function handleScheduled(controller: ScheduledController, env: Env): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  const cronExpr = controller.cron;
  const scheduledAt = Math.floor((controller.scheduledTime || Date.now()) / 1000);
  const runId = crypto.randomUUID();
  const failures: string[] = [];

  // An execution record begins before work so an interrupted invocation remains
  // visible as running rather than silently disappearing.
  try {
    await env.DB.prepare(`
      INSERT INTO cron_runs (id, cron_expression, scheduled_at, started_at, status)
      VALUES (?, ?, ?, ?, 'running')
    `).bind(runId, cronExpr, scheduledAt, now).run();
  } catch (err) {
    console.error('[cron] could not create execution record:', err);
  }

  const runTask = async (name: string, task: () => Promise<unknown>): Promise<void> => {
    try {
      await task();
    } catch (err) {
      const message = cronErrorMessage(err);
      failures.push(`${name}: ${message}`);
      console.error(`[cron] ${name} error:`, err);
    }
  };

  // Resume D1-backed seed plans independently of any request lifetime.
  await runTask('seed resume', () => resumeSeedRuns(env));
  await runTask('publish resume', () => resumePublishJobs(env));

  // ── Hourly: clean up expired TTL records ──────────────────────────────────
  if (cronExpr === '0 * * * *') {
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
      await runTask(`TTL cleanup for ${table}`, async () => {
        await env.DB.prepare(
          `DELETE FROM ${table} WHERE expires_at IS NOT NULL AND expires_at < ?`
        ).bind(now).run();
      });
    }
    if (failures.length === 0) console.log('[cron] TTL cleanup complete');
  }

  // ── Daily: reset monthly message counts, expire subscriptions ────────────
  if (cronExpr === '0 0 * * *') {
    // Reset monthly counts for users whose last_reset_date was in a previous month
    await runTask('monthly usage reset', async () => {
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
    });

    // Expire subscriptions whose period has ended
    await runTask('subscription expiry', async () => {
      await env.DB.prepare(`
        UPDATE users
        SET subscription_tier = 'free',
            subscription_status = 'cancelled'
        WHERE subscription_status = 'active'
          AND current_period_end IS NOT NULL
          AND current_period_end < ?
          AND subscription_tier != 'free'
      `).bind(now).run();
    });

    if (failures.length === 0) console.log('[cron] Daily maintenance complete');
  }

  const completedAt = Math.floor(Date.now() / 1000);
  const failed = failures.length > 0;
  const errorSummary = failed ? failures.join('\n').slice(0, 2_048) : null;
  try {
    await env.DB.prepare(`
      UPDATE cron_runs
      SET completed_at = ?, status = ?, failure_count = ?, error_summary = ?
      WHERE id = ?
    `).bind(completedAt, failed ? 'failed' : 'succeeded', failures.length, errorSummary, runId).run();

    await env.DB.prepare(`
      INSERT INTO cron_alert_state (
        id, alert_active, consecutive_failures, last_failure_at, last_success_at,
        last_alert_at, alert_reason, updated_at
      ) VALUES ('singleton', ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        alert_active = excluded.alert_active,
        consecutive_failures = CASE
          WHEN excluded.alert_active = 1 THEN cron_alert_state.consecutive_failures + 1
          ELSE 0
        END,
        last_failure_at = CASE WHEN excluded.alert_active = 1 THEN excluded.last_failure_at ELSE cron_alert_state.last_failure_at END,
        last_success_at = CASE WHEN excluded.alert_active = 0 THEN excluded.last_success_at ELSE cron_alert_state.last_success_at END,
        last_alert_at = CASE
          WHEN excluded.alert_active = 1
            AND (cron_alert_state.alert_active = 0
              OR cron_alert_state.last_alert_at IS NULL
              OR cron_alert_state.last_alert_at < excluded.last_failure_at - 600)
          THEN excluded.last_failure_at
          ELSE cron_alert_state.last_alert_at
        END,
        alert_reason = CASE WHEN excluded.alert_active = 1 THEN excluded.alert_reason ELSE NULL END,
        updated_at = excluded.updated_at
    `).bind(
      failed ? 1 : 0,
      failed ? 1 : 0,
      failed ? completedAt : null,
      failed ? null : completedAt,
      failed ? completedAt : null,
      errorSummary,
      completedAt,
    ).run();
  } catch (err) {
    console.error('[cron] could not finalize execution record:', err);
  }
}

export default {
  fetch: app.fetch,
  scheduled: handleScheduled,
} satisfies ExportedHandler<Env>;
