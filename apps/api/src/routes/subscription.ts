/**
 * Subscription routes — D1-backed, matches Cloud Run /api/v1/subscription/* contract.
 *
 * GET  /plans     — public, returns plan tiers (static data)
 * GET  /status    — auth required, reads D1 users table
 * POST /create-order — auth required, creates a one-time Razorpay Order
 * POST /cancel    — retained for compatibility; one-time plans cannot cancel
 *
 * The cron /cron/downgrade-expired is intentionally excluded; it runs
 * server-side and is mounted in the admin/cron router (Cloud Run, Phase 7).
 */

import { Hono, type Context } from 'hono';
import { eq } from 'drizzle-orm';
import { createDb } from '../db/client';
import { users } from '../db/schema';
import { isSessionValid, verifyToken, extractBearer } from '../middleware/auth';
import type { Env } from '../types';

export const subscriptionRouter = new Hono<{ Bindings: Env }>();

// Daily credit limits — must match the public pricing contract.
const RATE_LIMIT_FREE_TIER = 30;
const RATE_LIMIT_STARTER_TIER = 500;
const RATE_LIMIT_PRO_TIER  = 4000;

// ── Auth helper ───────────────────────────────────────────────────────────────

async function requireUser(c: Context<{ Bindings: Env }>): Promise<{ id: string; error?: Response }> {
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

// ── GET /plans ─────────────────────────────────────────────────────────────────
// Public one-time plan offers. Razorpay Orders are used; no recurring product
// is advertised or created.

subscriptionRouter.get('/plans', (c) => {
  return c.json({
    plans: [
      {
        id: 'free',
        name: 'Free',
        price_inr: 0,
        price_label: '₹0',
        billing: 'forever',
        message_limit: RATE_LIMIT_FREE_TIER,
        message_label: `${RATE_LIMIT_FREE_TIER} credits/day`,
        cta: 'Get started free',
        popular: false,
        features: [
          { label: 'AHSEC & SEBA content',          included: true },
          { label: `${RATE_LIMIT_FREE_TIER} AI credits/day`, included: true },
          { label: 'English & Assamese chat',         included: true },
          { label: 'Study library access',            included: true },
          { label: 'Priority AI responses',           included: false },
          { label: 'Unlimited AI messages',           included: false },
        ],
      },
      {
        id: 'starter',
        name: 'Starter',
        price_inr: 99,
        price_label: '₹99',
        billing: 'one-time',
        message_limit: RATE_LIMIT_STARTER_TIER,
        message_label: `${RATE_LIMIT_STARTER_TIER} credits/day`,
        cta: 'Buy Starter',
        popular: true,
        features: [
          { label: `${RATE_LIMIT_STARTER_TIER} AI credits/day`, included: true },
          { label: 'Priority AI responses', included: true },
          { label: 'Limited document access', included: true },
        ],
      },
      {
        id: 'pro',
        name: 'Pro',
        price_inr: 999,
        price_label: '₹999',
        billing: 'one-time',
        message_limit: RATE_LIMIT_PRO_TIER,
        message_label: `${RATE_LIMIT_PRO_TIER.toLocaleString()} credits/day`,
        cta: 'Upgrade to Pro',
        popular: true,
        features: [
          { label: 'AHSEC & SEBA content',           included: true },
          { label: `${RATE_LIMIT_PRO_TIER.toLocaleString()} AI credits/day`, included: true },
          { label: 'English & Assamese chat',         included: true },
          { label: 'Study library access',            included: true },
          { label: 'Priority AI responses',           included: true },
          { label: 'Early access to new features',    included: true },
        ],
      },
    ],
    currency: 'INR',
  });
});

// ── GET /status ────────────────────────────────────────────────────────────────
// Auth required. Reads current subscription state from D1.

subscriptionRouter.get('/status', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);
  const user = await db.select({
    subscriptionTier: users.subscriptionTier,
    subscriptionStatus: users.subscriptionStatus,
    currentPeriodEnd: users.currentPeriodEnd,
    monthlyMessageCount: users.monthlyMessageCount,
  }).from(users).where(eq(users.id, id)).get();

  if (!user) return c.json({ detail: 'User not found' }, 404);

  const tier = user.subscriptionTier ?? 'free';
  const dailyLimit = tier === 'pro' ? RATE_LIMIT_PRO_TIER : tier === 'starter' ? RATE_LIMIT_STARTER_TIER : RATE_LIMIT_FREE_TIER;
  const periodEnd = user.currentPeriodEnd
    ? new Date(user.currentPeriodEnd * 1000).toISOString()
    : '';

  return c.json({
    tier: user.subscriptionTier ?? 'free',
    status: user.subscriptionStatus ?? 'active',
    current_period_end: periodEnd,
    monthly_message_count: user.monthlyMessageCount ?? 0,
    monthly_limit: dailyLimit,
  });
});

// ── POST /create-order ─────────────────────────────────────────────────────────
// Auth required. Creates a Razorpay order and stores plan metadata in D1
// payments_pending for idempotent verification at /payments/verify.
// One-time plan prices (paise): starter = ₹99, pro = ₹999.

const PLAN_PRICES: Record<string, number> = { starter: 9900, pro: 99900 };
const PLAN_LABELS: Record<string, string>  = { starter: 'Starter', pro: 'Pro' };

subscriptionRouter.post('/create-order', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  if (!c.env.RAZORPAY_KEY_ID || !c.env.RAZORPAY_KEY_SECRET) {
    return c.json({ detail: 'Payment service not configured' }, 503);
  }

  let body: { plan?: unknown };
  try { body = await c.req.json() as typeof body; } catch { return c.json({ detail: 'Invalid JSON' }, 400); }
  const plan = typeof body.plan === 'string' ? body.plan.toLowerCase() : '';
  if (!PLAN_PRICES[plan]) return c.json({ detail: 'Invalid plan' }, 400);
  const amount = PLAN_PRICES[plan];

  // Create order via Razorpay REST API (no SDK needed — pure fetch)
  const orderRes = await fetch('https://api.razorpay.com/v1/orders', {
    method: 'POST',
    headers: {
      Authorization: `Basic ${btoa(`${c.env.RAZORPAY_KEY_ID}:${c.env.RAZORPAY_KEY_SECRET}`)}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      amount,
      currency: 'INR',
      receipt: `user_${id}_${plan}`,
      notes: { user_id: id, plan, purchase_type: 'one_time_plan' },
    }),
  });

  if (!orderRes.ok) {
    const errText = await orderRes.text();
    console.error('Razorpay create-order error:', errText);
    return c.json({ detail: 'Payment gateway error' }, 502);
  }

  const order = await orderRes.json() as Record<string, unknown>;

  // Store plan + amount in D1 payments_pending for idempotent /verify
  const orderId = order.id as string;
  const expiresAt = Math.floor(Date.now() / 1000) + 86400; // 24h TTL
  await c.env.DB.prepare(
    `INSERT OR REPLACE INTO payments_pending (id, order_id, user_id, metadata, expires_at, created_at)
     VALUES (?, ?, ?, ?, ?, ?)`
  ).bind(
    crypto.randomUUID(),
    orderId,
    id,
    JSON.stringify({ plan, amount }),
    expiresAt,
    Math.floor(Date.now() / 1000),
  ).run();

  return c.json({
    order_id: orderId,
    amount,
    currency: 'INR',
    key_id: c.env.RAZORPAY_KEY_ID,
    plan_label: PLAN_LABELS[plan] ?? plan,
  });
});

// ── POST /cancel ───────────────────────────────────────────────────────────────
// The commercial product is a one-time purchase. Do not send an order ID to
// Razorpay's subscriptions API: it is neither a subscription nor cancellable.

subscriptionRouter.post('/cancel', async (c) => {
  const { error } = await requireUser(c);
  if (error) return error;
  return c.json({ detail: 'One-time plans have no recurring subscription to cancel' }, 400);
});
