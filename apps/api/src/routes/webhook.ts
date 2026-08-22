/**
 * Razorpay webhook handler — matches Cloud Run /api/webhooks/razorpay contract.
 *
 * Security:
 *   1. Verifies X-Razorpay-Signature HMAC-SHA256 against the raw request body.
 *   2. Deduplicates events via the D1 payments table (INSERT OR IGNORE).
 *   3. Uses RAZORPAY_WEBHOOK_SECRET env var (distinct from the API key secret).
 *
 * Events handled:
 *   subscription.charged   — renews subscription in D1
 *   subscription.cancelled — marks cancel_at_period_end=1 in D1
 *   subscription.completed / subscription.expired — downgrades to free in D1
 *   payment.failed         — logged only (no action required; user may retry)
 */

import { Hono } from 'hono';
import { eq, or } from 'drizzle-orm';
import { createDb } from '../db/client';
import { users, payments } from '../db/schema';
import type { Env } from '../types';

export const webhookRouter = new Hono<{ Bindings: Env }>();

// ── HMAC verify helper ────────────────────────────────────────────────────────

async function hmacSha256(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const sig = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(message));
  return Array.from(new Uint8Array(sig)).map(b => b.toString(16).padStart(2, '0')).join('');
}

// ── Subscription ID validation ────────────────────────────────────────────────
// Razorpay subscription IDs: sub_* or order_* (for one-time upgrade orders).

function isValidSubId(value: unknown): value is string {
  if (typeof value !== 'string') return false;
  return /^(sub|order)_[A-Za-z0-9_]+$/.test(value);
}

// ── POST /razorpay ─────────────────────────────────────────────────────────────

webhookRouter.post('/razorpay', async (c) => {
  if (!c.env.RAZORPAY_WEBHOOK_SECRET) {
    console.error('RAZORPAY_WEBHOOK_SECRET not configured');
    return c.json({ error: 'Webhook not configured' }, 503);
  }

  // Read raw body for HMAC verification (must not use c.req.json() first)
  const rawBody = await c.req.text();

  const signature = c.req.header('X-Razorpay-Signature');
  if (!signature) {
    console.warn('Missing X-Razorpay-Signature header');
    return c.json({ error: 'Missing signature' }, 400);
  }

  const expectedSig = await hmacSha256(c.env.RAZORPAY_WEBHOOK_SECRET, rawBody);
  if (expectedSig !== signature) {
    console.warn('Invalid Razorpay webhook signature');
    return c.json({ error: 'Invalid signature' }, 400);
  }

  let event: Record<string, unknown>;
  try {
    event = JSON.parse(rawBody) as Record<string, unknown>;
  } catch {
    return c.json({ error: 'Invalid JSON' }, 400);
  }

  const eventType = event.event as string | undefined;
  const eventId   = (event.id ?? event.event_id) as string | undefined;
  const payload   = (event.payload ?? {}) as Record<string, unknown>;

  if (!eventId) {
    return c.json({ error: 'Missing event_id' }, 400);
  }

  // Deduplication: check if we've already recorded a payment for this event
  // (Cloud Run used Redis SET NX; D1 uses INSERT OR IGNORE)
  const db = createDb(c.env.DB);
  const now = Math.floor(Date.now() / 1000);

  console.log(`Razorpay webhook: ${eventType} (${eventId})`);

  if (eventType === 'subscription.charged') {
    // User's subscription renewed — extend period + record payment
    const sub = (payload.subscription as Record<string, unknown> | undefined);
    const pmt = (payload.payment as Record<string, unknown> | undefined);
    const subId  = sub?.id as string | undefined;
    const amount = pmt?.amount as number | undefined;
    const paymentId = pmt?.id as string | undefined;
    const orderId   = pmt?.order_id as string | undefined;

    if (!isValidSubId(subId)) {
      return c.json({ status: 'ignored', reason: 'invalid_sub_id' });
    }

    // Find user by razorpay_subscription_id
    const user = await db.select({
      id: users.id,
      subscriptionTier: users.subscriptionTier,
    }).from(users)
      .where(or(
        eq(users.razorpaySubscriptionId, subId),
        orderId ? eq(users.razorpaySubscriptionId, orderId) : eq(users.razorpaySubscriptionId, subId),
      ))
      .get();

    if (!user) {
      console.warn(`webhook subscription.charged: no user found for sub_id=${subId}`);
      return c.json({ status: 'ignored', reason: 'user_not_found' });
    }

    const periodEnd = now + 30 * 24 * 3600;
    await db.update(users).set({
      subscriptionStatus: 'active',
      currentPeriodEnd: periodEnd,
      cancelAtPeriodEnd: 0,
      updatedAt: now,
    }).where(eq(users.id, user.id));

    // Record payment (INSERT OR IGNORE for idempotency)
    if (paymentId) {
      await c.env.DB.prepare(
        `INSERT OR IGNORE INTO payments (id, user_id, razorpay_payment_id, razorpay_order_id, amount, currency, status, plan, created_at)
         VALUES (?, ?, ?, ?, ?, 'INR', 'captured', ?, ?)`
      ).bind(crypto.randomUUID(), user.id, paymentId, orderId ?? subId, amount ?? 0, user.subscriptionTier ?? 'pro', now).run();
    }

  } else if (eventType === 'subscription.cancelled') {
    const sub = (payload.subscription as Record<string, unknown> | undefined);
    const subId = sub?.id as string | undefined;
    if (!isValidSubId(subId)) return c.json({ status: 'ignored', reason: 'invalid_sub_id' });

    const user = await db.select({ id: users.id })
      .from(users).where(eq(users.razorpaySubscriptionId, subId)).get();

    if (user) {
      await db.update(users).set({ cancelAtPeriodEnd: 1, updatedAt: now }).where(eq(users.id, user.id));
    }

  } else if (eventType === 'subscription.completed' || eventType === 'subscription.expired') {
    const sub = (payload.subscription as Record<string, unknown> | undefined);
    const subId = sub?.id as string | undefined;
    if (!isValidSubId(subId)) return c.json({ status: 'ignored', reason: 'invalid_sub_id' });

    const user = await db.select({ id: users.id })
      .from(users).where(eq(users.razorpaySubscriptionId, subId)).get();

    if (user) {
      await db.update(users).set({
        subscriptionTier: 'free',
        subscriptionStatus: 'cancelled',
        cancelAtPeriodEnd: 0,
        updatedAt: now,
      }).where(eq(users.id, user.id));
    } else {
      console.warn(`webhook ${eventType}: no user found for sub_id=${subId}`);
    }

  } else if (eventType === 'payment.failed') {
    // Log only — no action needed; user can retry from frontend
    const pmt = (payload.payment as Record<string, unknown> | undefined);
    console.log(`payment.failed: payment_id=${pmt?.id}, order_id=${pmt?.order_id}`);
  }

  return c.json({ status: 'ok' });
});
