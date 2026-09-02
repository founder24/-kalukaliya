/**
 * Payments routes — D1-backed, matches Cloud Run /api/v1/payments/* contract.
 *
 * All payment flows use D1 (payments, payments_pending, transactions tables)
 * and the Razorpay REST API directly (no SDK). Redis deduplication is replaced
 * by the D1 payments_pending table (idempotent INSERT OR IGNORE on verify).
 *
 * POST /create-order        — subscription plan upgrade order
 * POST /verify              — HMAC verify + update subscription in D1
 * POST /recover             — resume a failed/pending order
 * POST /credit-topup        — AI credit pack order
 * POST /credit-topup/verify — HMAC verify + add credits in D1
 * GET  /history             — paginated payment history from D1
 */

import { Hono, type Context } from 'hono';
import { eq, desc } from 'drizzle-orm';
import { createDb } from '../db/client';
import { users, payments, transactions, paymentsPending } from '../db/schema';
import { isSessionValid, verifyToken, extractBearer } from '../middleware/auth';
import type { Env } from '../types';

export const paymentsRouter = new Hono<{ Bindings: Env }>();

// One-time plan prices (paise), matching the public pricing contract.
const PLAN_PRICES: Record<string, number> = { starter: 9900, pro: 99900 };

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

// ── HMAC verification ─────────────────────────────────────────────────────────

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

// ── GET /test-mode-status ─────────────────────────────────────────────────────
// Authenticated, non-mutating preflight for the release validator. Razorpay key
// IDs are already returned by order creation, but this lets the validator refuse
// live credentials before it creates any external order or D1 pending record.

paymentsRouter.get('/test-mode-status', async (c) => {
  const { error } = await requireUser(c);
  if (error) return error;

  const keyId = c.env.RAZORPAY_KEY_ID;
  return c.json({
    configured: Boolean(keyId && c.env.RAZORPAY_KEY_SECRET),
    key_id: keyId || null,
    test_mode: keyId.startsWith('rzp_test_'),
  });
});

// ── POST /create-order ─────────────────────────────────────────────────────────
// Create a Razorpay order for subscription plan upgrade.

paymentsRouter.post('/create-order', async (c) => {
  const { id, error } = await requireUser(c);
  if (error) return error;

  if (!c.env.RAZORPAY_KEY_ID || !c.env.RAZORPAY_KEY_SECRET) {
    return c.json({ detail: 'Payment service not configured' }, 503);
  }

  let body: { plan?: string };
  try { body = await c.req.json() as typeof body; } catch { return c.json({ detail: 'Invalid JSON' }, 400); }

  const plan = body.plan?.toLowerCase();
  const amount = plan ? PLAN_PRICES[plan] : undefined;
  if (!plan || amount == null) {
    return c.json({ detail: 'Invalid plan' }, 400);
  }

  // Create Razorpay order
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
    console.error('Razorpay create-order error:', await orderRes.text());
    return c.json({ detail: 'Payment gateway error' }, 502);
  }

  const order = await orderRes.json() as Record<string, unknown>;
  const orderId = order.id as string;

  // Store plan + amount in D1 payments_pending (replaces Redis SET)
  const now = Math.floor(Date.now() / 1000);
  await c.env.DB.prepare(
    `INSERT OR REPLACE INTO payments_pending (id, order_id, user_id, metadata, expires_at, created_at)
     VALUES (?, ?, ?, ?, ?, ?)`
  ).bind(
    crypto.randomUUID(), orderId, id,
    JSON.stringify({ plan, amount }),
    now + 86400, now,
  ).run();

  const planLabels: Record<string, string> = { starter: 'Starter', pro: 'Pro' };
  return c.json({
    order_id: orderId,
    amount,
    currency: 'INR',
    key_id: c.env.RAZORPAY_KEY_ID,
    plan_label: planLabels[plan] ?? plan,
  });
});

// ── POST /verify ───────────────────────────────────────────────────────────────
// Verify Razorpay payment HMAC, look up order metadata from D1, upgrade subscription.

paymentsRouter.post('/verify', async (c) => {
  const { id: userId, error } = await requireUser(c);
  if (error) return error;

  if (!c.env.RAZORPAY_KEY_SECRET) return c.json({ detail: 'Payment service not configured' }, 503);

  let body: { razorpay_order_id?: string; razorpay_payment_id?: string; razorpay_signature?: string; plan?: string };
  try { body = await c.req.json() as typeof body; } catch { return c.json({ detail: 'Invalid JSON' }, 400); }

  const { razorpay_order_id, razorpay_payment_id, razorpay_signature } = body;
  if (!razorpay_order_id || !razorpay_payment_id || !razorpay_signature) {
    return c.json({ detail: 'Missing required payment fields' }, 422);
  }

  // HMAC verify
  const expected = await hmacSha256(c.env.RAZORPAY_KEY_SECRET, `${razorpay_order_id}|${razorpay_payment_id}`);
  if (expected !== razorpay_signature) {
    return c.json({ detail: 'Invalid payment signature' }, 400);
  }

  // Resolve plan from D1 payments_pending BEFORE any mutation (same pattern as credit-topup)
  const pending = await c.env.DB.prepare(
    `SELECT metadata FROM payments_pending WHERE order_id = ? LIMIT 1`
  ).bind(razorpay_order_id).first<{ metadata: string }>();

  let purchasedPlan: string | null = null;
  let paymentAmount: number | null = null;

  if (pending?.metadata) {
    try {
      const meta = JSON.parse(pending.metadata) as { plan?: string; amount?: number };
      purchasedPlan = meta.plan ?? null;
      paymentAmount = meta.amount ?? null;
    } catch { /* ignore parse errors */ }
  }

  // If D1 metadata is missing, fetch from Razorpay API as authoritative fallback
  if (!purchasedPlan && c.env.RAZORPAY_KEY_ID) {
    try {
      const rzRes = await fetch(`https://api.razorpay.com/v1/orders/${razorpay_order_id}`, {
        headers: { Authorization: `Basic ${btoa(`${c.env.RAZORPAY_KEY_ID}:${c.env.RAZORPAY_KEY_SECRET}`)}` },
      });
      if (rzRes.ok) {
        const rzOrder = await rzRes.json() as Record<string, unknown>;
        const notes = rzOrder.notes as Record<string, string> | undefined;
        purchasedPlan = notes?.plan ?? null;
        if (purchasedPlan && rzOrder.amount) paymentAmount = Number(rzOrder.amount);
      }
    } catch (e) {
      console.error('Razorpay order fetch fallback failed:', e);
    }
  }

  // Validate ownership: the order must belong to this authenticated user
  if (pending) {
    const ownerCheck = await c.env.DB.prepare(
      `SELECT user_id FROM payments_pending WHERE order_id = ? LIMIT 1`
    ).bind(razorpay_order_id).first<{ user_id: string }>();
    if (ownerCheck && ownerCheck.user_id !== userId) {
      return c.json({ detail: 'Order does not belong to this account' }, 403);
    }
  }

  // Fail closed: never upgrade without verified plan metadata
  if (!purchasedPlan) {
    return c.json({ detail: 'Order metadata unavailable; please contact support if payment was charged' }, 503);
  }

  // Validate amount matches expected plan price
  const expectedAmount = PLAN_PRICES[purchasedPlan];
  if (expectedAmount != null && paymentAmount != null && paymentAmount !== expectedAmount) {
    console.error(`Amount mismatch: stored=${paymentAmount}, expected=${expectedAmount} plan=${purchasedPlan}`);
    return c.json({ detail: 'Payment amount mismatch' }, 400);
  }

  // ── Atomic idempotency claim via INSERT OR IGNORE ────────────────────────
  // The UNIQUE constraint on payments.razorpay_order_id (migration 0003) means
  // only one concurrent INSERT succeeds.  The loser gets changes=0 and returns
  // already_processed WITHOUT running the subscription UPDATE — eliminating the
  // check-then-act race that allowed duplicate upgrades from concurrent retries.
  const db = createDb(c.env.DB);
  const now = Math.floor(Date.now() / 1000);
  const claimId = crypto.randomUUID();
  const claimResult = await c.env.DB.prepare(
    `INSERT OR IGNORE INTO payments (id, user_id, razorpay_payment_id, razorpay_order_id, amount, currency, status, plan, created_at)
     VALUES (?, ?, ?, ?, ?, 'INR', 'captured', ?, ?)`
  ).bind(claimId, userId, razorpay_payment_id, razorpay_order_id, paymentAmount ?? expectedAmount ?? 0, purchasedPlan, now).run();

  if (claimResult.meta.changes === 0) {
    return c.json({ status: 'already_processed', message: 'Subscription upgrade already processed for this order' });
  }

  // This request won the race — update user subscription in D1
  await db.update(users).set({
    subscriptionTier: purchasedPlan,
    subscriptionStatus: 'active',
    razorpaySubscriptionId: razorpay_order_id,
    razorpayCustomerId: razorpay_payment_id,
    // Plans are paid once, not Razorpay recurring subscriptions.
    currentPeriodStart: now,
    currentPeriodEnd: null,
    cancelAtPeriodEnd: 0,
    updatedAt: now,
  }).where(eq(users.id, userId));

  // Clean up payments_pending
  await c.env.DB.prepare(`DELETE FROM payments_pending WHERE order_id = ?`).bind(razorpay_order_id).run();

  // Generate receipt token (matches Cloud Run _make_receipt_token logic)
  const receiptInput = `receipt:${razorpay_order_id}:${razorpay_payment_id}`;
  const receiptToken = (await hmacSha256(c.env.RAZORPAY_KEY_SECRET, receiptInput)).slice(0, 32);

  return c.json({ status: 'success', message: 'Payment verified', receipt_token: receiptToken });
});

// ── POST /recover ──────────────────────────────────────────────────────────────
// Resume a failed payment — returns pending order info for retry.

paymentsRouter.post('/recover', async (c) => {
  const { id: userId, error } = await requireUser(c);
  if (error) return error;

  const pending = await c.env.DB.prepare(
    `SELECT order_id, metadata FROM payments_pending WHERE user_id = ? ORDER BY created_at DESC LIMIT 1`
  ).bind(userId).first<{ order_id: string; metadata: string }>();

  if (!pending) {
    return c.json({ detail: 'No pending payment found' }, 404);
  }

  let meta: { plan?: string; amount?: number } = {};
  try { meta = JSON.parse(pending.metadata) as typeof meta; } catch { /* ignore */ }

  return c.json({
    order_id: pending.order_id,
    plan: meta.plan ?? null,
    amount: meta.amount ?? null,
    currency: 'INR',
    key_id: c.env.RAZORPAY_KEY_ID,
  });
});

// ── POST /credit-topup ─────────────────────────────────────────────────────────
// Create a Razorpay order for AI credit top-up (fixed pack sizes).

const CREDIT_PACKS: Record<number, number> = {
  100: 4900,   // 100 credits for ₹49
  500: 19900,  // 500 credits for ₹199
  1000: 34900, // 1,000 credits for ₹349
};

paymentsRouter.post('/credit-topup', async (c) => {
  const { id: userId, error } = await requireUser(c);
  if (error) return error;

  if (!c.env.RAZORPAY_KEY_ID || !c.env.RAZORPAY_KEY_SECRET) {
    return c.json({ detail: 'Payment service not configured' }, 503);
  }

  let body: { credits?: number };
  try { body = await c.req.json() as typeof body; } catch { return c.json({ detail: 'Invalid JSON' }, 400); }

  const credits = body.credits;
  const amount = credits != null ? CREDIT_PACKS[credits] : undefined;
  if (!credits || amount == null) {
    return c.json({ detail: 'Invalid credit pack' }, 400);
  }

  const orderRes = await fetch('https://api.razorpay.com/v1/orders', {
    method: 'POST',
    headers: {
      Authorization: `Basic ${btoa(`${c.env.RAZORPAY_KEY_ID}:${c.env.RAZORPAY_KEY_SECRET}`)}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      amount,
      currency: 'INR',
      receipt: `credits_${userId}_${credits}`,
      notes: { user_id: userId, credits: String(credits), type: 'credit_topup' },
    }),
  });

  if (!orderRes.ok) {
    console.error('Razorpay credit-topup error:', await orderRes.text());
    return c.json({ detail: 'Payment gateway error' }, 502);
  }

  const order = await orderRes.json() as Record<string, unknown>;
  const orderId = order.id as string;

  const now = Math.floor(Date.now() / 1000);
  await c.env.DB.prepare(
    `INSERT OR REPLACE INTO payments_pending (id, order_id, user_id, metadata, expires_at, created_at)
     VALUES (?, ?, ?, ?, ?, ?)`
  ).bind(crypto.randomUUID(), orderId, userId, JSON.stringify({ credits, amount, type: 'credit_topup' }), now + 86400, now).run();

  return c.json({ order_id: orderId, amount, currency: 'INR', key_id: c.env.RAZORPAY_KEY_ID, credits });
});

// ── POST /credit-topup/verify ──────────────────────────────────────────────────
// Verify credit top-up payment and add credits to user account in D1.

paymentsRouter.post('/credit-topup/verify', async (c) => {
  const { id: userId, error } = await requireUser(c);
  if (error) return error;

  if (!c.env.RAZORPAY_KEY_SECRET) return c.json({ detail: 'Payment service not configured' }, 503);

  let body: { razorpay_order_id?: string; razorpay_payment_id?: string; razorpay_signature?: string };
  try { body = await c.req.json() as typeof body; } catch { return c.json({ detail: 'Invalid JSON' }, 400); }

  const { razorpay_order_id, razorpay_payment_id, razorpay_signature } = body;
  if (!razorpay_order_id || !razorpay_payment_id || !razorpay_signature) {
    return c.json({ detail: 'Missing required payment fields' }, 422);
  }

  // HMAC verify
  const expected = await hmacSha256(c.env.RAZORPAY_KEY_SECRET, `${razorpay_order_id}|${razorpay_payment_id}`);
  if (expected !== razorpay_signature) return c.json({ detail: 'Invalid payment signature' }, 400);

  // Resolve credits from D1 payments_pending BEFORE any mutation
  const pending = await c.env.DB.prepare(
    `SELECT metadata FROM payments_pending WHERE order_id = ? LIMIT 1`
  ).bind(razorpay_order_id).first<{ metadata: string }>();

  let creditAmount = 0;
  let paidAmount = 0;
  if (pending?.metadata) {
    try {
      const meta = JSON.parse(pending.metadata) as { credits?: number; amount?: number };
      creditAmount = meta.credits ?? 0;
      paidAmount = meta.amount ?? 0;
    } catch { /* ignore */ }
  }

  // Fallback: fetch from Razorpay API when D1 metadata is missing
  if (!creditAmount && c.env.RAZORPAY_KEY_ID) {
    try {
      const rzRes = await fetch(`https://api.razorpay.com/v1/orders/${razorpay_order_id}`, {
        headers: { Authorization: `Basic ${btoa(`${c.env.RAZORPAY_KEY_ID}:${c.env.RAZORPAY_KEY_SECRET}`)}` },
      });
      if (rzRes.ok) {
        const rzOrder = await rzRes.json() as Record<string, unknown>;
        const notes = rzOrder.notes as Record<string, string> | undefined;
        creditAmount = parseInt(notes?.credits ?? '0', 10);
        paidAmount = Number(rzOrder.amount ?? 0);
      }
    } catch (e) {
      console.error('Razorpay order fetch fallback failed:', e);
    }
  }

  if (!creditAmount) return c.json({ detail: 'Order metadata unavailable; please contact support if payment was charged' }, 503);

  // ── Atomic idempotency claim via INSERT OR IGNORE ────────────────────────
  // Two concurrent retries both pass HMAC and both read creditAmount above.
  // INSERT OR IGNORE is the sole serialization point: SQLite guarantees exactly
  // one INSERT wins (UNIQUE on razorpay_order_id). The loser gets changes=0
  // and returns already_processed WITHOUT touching the credits balance.
  // This eliminates the check-then-act race that permitted duplicate credit grants.
  const now = Math.floor(Date.now() / 1000);
  const claimId = crypto.randomUUID();
  const claimResult = await c.env.DB.prepare(
    `INSERT OR IGNORE INTO payments (id, user_id, razorpay_payment_id, razorpay_order_id, amount, currency, status, plan, created_at)
     VALUES (?, ?, ?, ?, ?, 'INR', 'captured', 'credit_topup', ?)`
  ).bind(claimId, userId, razorpay_payment_id, razorpay_order_id, paidAmount, now).run();

  if (claimResult.meta.changes === 0) {
    // Another request already claimed this payment — idempotent response
    return c.json({ status: 'already_processed', message: 'Credits already added for this order' });
  }

  // This request won the race — apply credit grant using SQL arithmetic to avoid
  // a read-then-write race on the balance itself.  credits_remaining = credits_remaining + N
  // is atomic in SQLite; no OCC loop needed.
  await c.env.DB.prepare(
    `UPDATE users SET credits_remaining = COALESCE(credits_remaining, 0) + ?, updated_at = ? WHERE id = ?`
  ).bind(creditAmount, now, userId).run();

  // Read back the new balance for the response
  const updatedUser = await c.env.DB.prepare(
    `SELECT credits_remaining FROM users WHERE id = ? LIMIT 1`
  ).bind(userId).first<{ credits_remaining: number }>();

  await c.env.DB.prepare(
    `INSERT INTO transactions (id, user_id, type, amount, metadata, created_at)
     VALUES (?, ?, 'credit_topup', ?, ?, ?)`
  ).bind(crypto.randomUUID(), userId, creditAmount, JSON.stringify({ order_id: razorpay_order_id }), now).run();

  // Clean up pending
  await c.env.DB.prepare(`DELETE FROM payments_pending WHERE order_id = ?`).bind(razorpay_order_id).run();

  return c.json({
    status: 'success',
    message: `${creditAmount} credits added`,
    credits_added: creditAmount,
    credits_remaining: updatedUser?.credits_remaining ?? creditAmount,
  });
});

// ── GET /history ───────────────────────────────────────────────────────────────
// Auth required. Returns paginated payment history from D1.

paymentsRouter.get('/history', async (c) => {
  const { id: userId, error } = await requireUser(c);
  if (error) return error;

  const db = createDb(c.env.DB);
  const page  = Math.max(1, parseInt(c.req.query('page')  ?? '1',  10));
  const limit = Math.min(50, parseInt(c.req.query('limit') ?? '20', 10));

  const rows = await db.select().from(payments)
    .where(eq(payments.userId, userId))
    .orderBy(desc(payments.createdAt))
    .limit(limit)
    .offset((page - 1) * limit);

  const refundRows = await c.env.DB.prepare(
    `SELECT payment_id, status, created_at FROM refund_requests
     WHERE user_id = ? AND payment_id IS NOT NULL`
  ).bind(userId).all<{ payment_id: string; status: string; created_at: number }>();
  const refunds = new Map((refundRows.results ?? []).map(refund => [refund.payment_id, refund]));

  return c.json({
    payments: rows.map(p => {
      const refund = refunds.get(p.id);
      return ({
      id: p.id,
      amount: p.amount,
      currency: p.currency,
      status: p.status === 'captured' ? 'completed' : p.status,
      plan: p.plan,
      description: p.plan === 'credit_topup' ? 'Credit top-up' : `${(p.plan ?? 'Plan').replace(/^./, char => char.toUpperCase())} Plan`,
      refund_status: refund ? (refund.status === 'pending' ? 'requested' : refund.status) : null,
      refund_requested_at: refund?.created_at ? new Date(refund.created_at * 1000).toISOString() : null,
      razorpay_payment_id: p.razorpayPaymentId,
      razorpay_order_id: p.razorpayOrderId,
      created_at: p.createdAt ? new Date(p.createdAt * 1000).toISOString() : null,
      });
    }),
    page,
  });
});

// ── POST /refund-request ───────────────────────────────────────────────────────
// Requests are recorded for staff review only. This endpoint never calls
// Razorpay, so a user request cannot trigger a live refund automatically.

paymentsRouter.post('/refund-request', async (c) => {
  const { id: userId, error } = await requireUser(c);
  if (error) return error;

  let body: { payment_id?: unknown; reason?: unknown };
  try { body = await c.req.json() as typeof body; } catch { return c.json({ detail: 'Invalid JSON' }, 400); }

  if (typeof body.payment_id !== 'string' || !body.payment_id.trim() || body.payment_id.length > 128) {
    return c.json({ detail: 'A valid payment_id is required' }, 422);
  }
  if (body.reason !== undefined && (typeof body.reason !== 'string' || body.reason.length > 1000)) {
    return c.json({ detail: 'Reason must be a string of at most 1000 characters' }, 422);
  }

  const paymentId = body.payment_id.trim();
  const payment = await c.env.DB.prepare(
    `SELECT id FROM payments WHERE id = ? AND user_id = ? AND status = 'captured' LIMIT 1`
  ).bind(paymentId, userId).first<{ id: string }>();
  if (!payment) return c.json({ detail: 'Eligible payment not found' }, 404);

  const now = Math.floor(Date.now() / 1000);
  const result = await c.env.DB.prepare(
    `INSERT OR IGNORE INTO refund_requests (id, user_id, payment_id, reason, status, created_at, updated_at)
     VALUES (?, ?, ?, ?, 'pending', ?, ?)`
  ).bind(crypto.randomUUID(), userId, paymentId, typeof body.reason === 'string' ? body.reason.trim() || null : null, now, now).run();

  if (result.meta.changes === 0) {
    return c.json({ status: 'already_requested', message: 'A refund request already exists for this payment' });
  }
  return c.json({ status: 'submitted', message: 'Refund request submitted' }, 201);
});
