import { Hono } from 'hono';
import { eq } from 'drizzle-orm';
import { createDb } from '../db/client';
import { users, passwordResetTokens } from '../db/schema';
import {
  hashPassword,
  verifyPassword,
  isLongPassword,
  signAccessToken,
  signRefreshToken,
  verifyToken,
  extractBearer,
  hashResetToken,
  revokedRtKey,
  REFRESH_TOKEN_TTL_S,
  isSessionValid,
  sessionIssuedAt,
} from '../middleware/auth';
import type { Env } from '../types';

export const authRouter = new Hono<{ Bindings: Env }>();

// ── POST /v1/auth/signup ──────────────────────────────────────────────────────
authRouter.post('/signup', async (c) => {
  const db = createDb(c.env.DB);
  let body: { email?: string; password?: string; name?: string };

  try {
    body = await c.req.json();
  } catch {
    return c.json({ detail: 'Invalid JSON' }, 400);
  }

  const email = body.email?.toLowerCase().trim();
  const password = body.password;
  if (!email || !password) {
    return c.json({ detail: 'email and password are required' }, 422);
  }
  if (password.length < 8) {
    return c.json({ detail: 'Password must be at least 8 characters' }, 422);
  }

  const existing = await db.select({ id: users.id })
    .from(users)
    .where(eq(users.email, email))
    .get();

  if (existing) {
    return c.json({ detail: 'An account with this email already exists' }, 409);
  }

  const id = crypto.randomUUID();
  const hashedPw = await hashPassword(password);
  const now = Math.floor(Date.now() / 1000);

  await db.insert(users).values({
    id,
    email,
    hashedPassword: hashedPw,
    authProvider: 'local',
    role: 'student',
    createdAt: now,
    updatedAt: now,
    name: body.name?.trim() ?? null,
  });

  const accessToken = await signAccessToken(id, 'student', c.env.JWT_SECRET);
  const { token: refreshToken } = await signRefreshToken(id, 'student', c.env.JWT_SECRET);

  return c.json({
    access_token: accessToken,
    refresh_token: refreshToken,
    token_type: 'bearer',
    user: {
      id,
      email,
      name: body.name?.trim() ?? null,
      role: 'student',
      subscription_tier: 'free',
      preferred_language: 'as',
    },
  }, 201);
});

// ── POST /v1/auth/login ───────────────────────────────────────────────────────
authRouter.post('/login', async (c) => {
  const db = createDb(c.env.DB);
  let body: { email?: string; password?: string };

  try {
    body = await c.req.json();
  } catch {
    return c.json({ detail: 'Invalid JSON' }, 400);
  }

  const email = body.email?.toLowerCase().trim();
  const password = body.password;
  if (!email || !password) {
    return c.json({ detail: 'email and password are required' }, 422);
  }

  const user = await db.select().from(users).where(eq(users.email, email)).get();
  if (!user) {
    return c.json({ detail: 'Invalid credentials' }, 401);
  }
  if (user.deletedAt) {
    return c.json({ detail: 'Account has been deleted' }, 403);
  }
  if (!user.hashedPassword) {
    return c.json({ detail: 'Password login not available for this account' }, 400);
  }

  const valid = await verifyPassword(password, user.hashedPassword);
  if (!valid) {
    // Detect migrated users whose >72-byte password hash cannot be verified.
    // This is a migration artifact; the Python bcrypt and Workers bcrypt handle
    // long passwords differently. These users must reset their password.
    if (isLongPassword(password)) {
      return c.json({
        detail: 'Your password cannot be verified after the platform migration. Please reset it.',
        error_code: 'password_reset_required',
      }, 400);
    }
    return c.json({ detail: 'Invalid credentials' }, 401);
  }

  const role = user.role ?? 'student';
  const issuedAt = await sessionIssuedAt(c.env.DB, user.id);
  const accessToken = await signAccessToken(user.id, role, c.env.JWT_SECRET, issuedAt);
  const { token: refreshToken } = await signRefreshToken(user.id, role, c.env.JWT_SECRET, issuedAt);

  await c.env.DB.prepare(
    'UPDATE users SET updated_at = ? WHERE id = ?'
  ).bind(Math.floor(Date.now() / 1000), user.id).run();

  return c.json({
    access_token: accessToken,
    refresh_token: refreshToken,
    token_type: 'bearer',
    user: {
      id: user.id,
      email: user.email,
      name: user.name,
      role,
      subscription_tier: user.subscriptionTier,
      subscription_status: user.subscriptionStatus,
      preferred_language: user.preferredLanguage,
      voice_enabled: !!user.voiceEnabled,
      theme: user.theme,
      credits_remaining: user.creditsRemaining,
      monthly_message_count: user.monthlyMessageCount,
      onboarding_done: !!user.onboardingDone,
      board_id: user.boardId,
      board_name: user.boardName,
      class_id: user.classId,
      class_name: user.className,
      stream_id: user.streamId,
      stream_name: user.streamName,
    },
  });
});

// ── POST /v1/auth/logout ──────────────────────────────────────────────────────
authRouter.post('/logout', async (c) => {
  // Revoke the refresh token's jti so it cannot be reused.
  const authHeader = c.req.header('Authorization');
  const token = extractBearer(authHeader ?? null);
  if (token) {
    const payload = await verifyToken(token, c.env.JWT_SECRET);
    if (payload?.type === 'refresh' && payload.jti) {
      // Mark as revoked in KV for the token's remaining TTL
      const remainingTtl = payload.exp
        ? Math.max(1, payload.exp - Math.floor(Date.now() / 1000))
        : REFRESH_TOKEN_TTL_S;
      await c.env.RATE_LIMIT_KV.put(
        revokedRtKey(payload.jti),
        '1',
        { expirationTtl: remainingTtl },
      ).catch(() => { /* non-blocking */ });
    }
  }
  return c.json({ message: 'Logged out successfully' });
});

// ── POST /v1/auth/refresh ─────────────────────────────────────────────────────
authRouter.post('/refresh', async (c) => {
  const db = createDb(c.env.DB);
  let body: { refresh_token?: string };

  try {
    body = await c.req.json();
  } catch {
    return c.json({ detail: 'Invalid JSON' }, 400);
  }

  const token = body.refresh_token;
  if (!token) return c.json({ detail: 'refresh_token is required' }, 422);

  const payload = await verifyToken(token, c.env.JWT_SECRET);
  if (!payload || payload.type !== 'refresh') {
    return c.json({ detail: 'Invalid or expired refresh token' }, 401);
  }

  // Enforce single-use: check KV revocation list
  if (payload.jti) {
    const revoked = await c.env.RATE_LIMIT_KV.get(revokedRtKey(payload.jti));
    if (revoked !== null) {
      return c.json({ detail: 'Refresh token has already been used or revoked' }, 401);
    }

    // Revoke this token immediately — rotate to new one below
    const remainingTtl = payload.exp
      ? Math.max(1, payload.exp - Math.floor(Date.now() / 1000))
      : REFRESH_TOKEN_TTL_S;
    await c.env.RATE_LIMIT_KV.put(
      revokedRtKey(payload.jti),
      '1',
      { expirationTtl: remainingTtl },
    ).catch(() => { /* non-blocking if KV fails — token still valid; log only */ });
  }

  const user = await db.select({ id: users.id, role: users.role, deletedAt: users.deletedAt })
    .from(users)
    .where(eq(users.id, payload.sub!))
    .get();

  if (!user || user.deletedAt) {
    return c.json({ detail: 'User not found' }, 401);
  }
  if (!(await isSessionValid(c.env.DB, user.id, payload.iat))) {
    return c.json({ detail: 'Session expired after password change. Sign in again.' }, 401);
  }

  const role = user.role ?? 'student';
  const issuedAt = await sessionIssuedAt(c.env.DB, user.id);
  const accessToken = await signAccessToken(user.id, role, c.env.JWT_SECRET, issuedAt);
  const { token: refreshToken } = await signRefreshToken(user.id, role, c.env.JWT_SECRET, issuedAt);

  return c.json({
    access_token: accessToken,
    refresh_token: refreshToken,
    token_type: 'bearer',
  });
});

// ── GET /v1/auth/me ───────────────────────────────────────────────────────────
authRouter.get('/me', async (c) => {
  const db = createDb(c.env.DB);
  const authHeader = c.req.header('Authorization');
  const token = extractBearer(authHeader ?? null);

  if (!token) return c.json({ detail: 'Not authenticated' }, 401);

  const payload = await verifyToken(token, c.env.JWT_SECRET);
  if (!payload || payload.type !== 'access') {
    return c.json({ detail: 'Invalid or expired token' }, 401);
  }
  if (!(await isSessionValid(c.env.DB, payload.sub ?? '', payload.iat))) {
    return c.json({ detail: 'Session expired after password change. Sign in again.' }, 401);
  }

  const user = await db.select().from(users).where(eq(users.id, payload.sub!)).get();
  if (!user || user.deletedAt) {
    return c.json({ detail: 'User not found' }, 401);
  }

  return c.json({
    id: user.id,
    email: user.email,
    name: user.name,
    role: user.role,
    subscription_tier: user.subscriptionTier,
    subscription_status: user.subscriptionStatus,
    preferred_language: user.preferredLanguage,
    voice_enabled: !!user.voiceEnabled,
    theme: user.theme,
    credits_remaining: user.creditsRemaining,
    monthly_message_count: user.monthlyMessageCount,
    total_lifetime_messages: user.totalLifetimeMessages,
    onboarding_done: !!user.onboardingDone,
    ads_opt_out: !!user.adsOptOut,
    consent_dpdp: !!user.consentDpdp,
    saved_subjects: JSON.parse(user.savedSubjects ?? '[]') as string[],
    board_id: user.boardId,
    board_name: user.boardName,
    class_id: user.classId,
    class_name: user.className,
    stream_id: user.streamId,
    stream_name: user.streamName,
    avatar_url: user.avatarUrl,
    phone: user.phone,
    created_at: user.createdAt,
  });
});

// ── POST /v1/auth/reset-password/request ─────────────────────────────────────
authRouter.post('/reset-password/request', async (c) => {
  const db = createDb(c.env.DB);
  let body: { email?: string; cutover_nonce?: string };

  try {
    body = await c.req.json();
  } catch {
    return c.json({ detail: 'Invalid JSON' }, 400);
  }

  const email = body.email?.toLowerCase().trim();
  if (!email) return c.json({ detail: 'email is required' }, 422);
  // The optional nonce lets the post-deploy validator bind a delivered link to
  // the reset request it just made. It is deliberately opaque, public-safe
  // metadata; invalid values are ignored so the public contract stays stable.
  const cutoverNonce = typeof body.cutover_nonce === 'string'
    && /^[A-Za-z0-9_-]{16,128}$/.test(body.cutover_nonce)
    ? body.cutover_nonce
    : null;

  // Always return success to prevent email enumeration
  const user = await db.select({ id: users.id }).from(users)
    .where(eq(users.email, email)).get();

  if (user && c.env.RESEND_API_KEY) {
    const token = crypto.randomUUID() + '-' + crypto.randomUUID(); // 73-char, unguessable
    const tokenHash = await hashResetToken(token);
    const expiresAt = Math.floor(Date.now() / 1000) + 60 * 60; // 1 hour

    await db.insert(passwordResetTokens).values({
      id: crypto.randomUUID(),
      userId: user.id,
      tokenHash,
      cutoverNonce,
      expiresAt,
    });

    const resetUrl = new URL('https://syrabit.ai/reset-password');
    resetUrl.searchParams.set('token', token);
    if (cutoverNonce) resetUrl.searchParams.set('cutover_nonce', cutoverNonce);
    const resetHref = resetUrl.toString().replace(/&/g, '&amp;');

    await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${c.env.RESEND_API_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        from: 'Syrabit <noreply@syrabit.ai>',
        to: [email],
        subject: 'Reset your Syrabit password',
        html: `<p>Click to reset your password: <a href="${resetHref}">Reset Password</a></p><p>This link expires in 1 hour.</p><p>If you did not request this, ignore this email.</p>`,
      }),
    }).catch(() => { /* non-blocking */ });
  }

  return c.json({ message: 'If an account exists, a reset email has been sent' });
});

// ── POST /v1/auth/reset-password/confirm ─────────────────────────────────────
authRouter.post('/reset-password/confirm', async (c) => {
  let body: { token?: string; password?: string; cutover_nonce?: string };

  try {
    body = await c.req.json();
  } catch {
    return c.json({ detail: 'Invalid JSON' }, 400);
  }

  const { token, password } = body;
  if (!token || !password) {
    return c.json({ detail: 'token and password are required' }, 422);
  }
  if (password.length < 8) {
    return c.json({ detail: 'Password must be at least 8 characters' }, 422);
  }

  const tokenHash = await hashResetToken(token);
  const cutoverNonce = typeof body.cutover_nonce === 'string'
    && /^[A-Za-z0-9_-]{16,128}$/.test(body.cutover_nonce)
    ? body.cutover_nonce
    : null;
  const now = Math.floor(Date.now() / 1000);

  // ── Atomic: mark token as used if (and only if) it is unused and not expired.
  //    SQLite serialises writes; only one concurrent caller sees changes = 1.
  //    The UPDATE is the "claim" — we check changes before touching the user row.
  const markResult = await c.env.DB.prepare(`
    UPDATE password_reset_tokens
    SET used_at = ?
    WHERE token_hash = ?
      AND used_at IS NULL
      AND expires_at >= ?
      AND (
        (cutover_nonce IS NULL AND ? IS NULL)
        OR cutover_nonce = ?
      )
  `).bind(now, tokenHash, now, cutoverNonce, cutoverNonce).run();

  if (markResult.meta.changes === 0) {
    // Either the token doesn't exist, is already used, or has expired.
    // Return the same error message to prevent oracle attacks.
    return c.json({ detail: 'Invalid or expired reset token' }, 400);
  }

  // Retrieve the user ID now that we've exclusively claimed the token.
  const record = await c.env.DB.prepare(
    'SELECT user_id FROM password_reset_tokens WHERE token_hash = ?'
  ).bind(tokenHash).first<{ user_id: string }>();

  if (!record) {
    // Should be impossible since we just updated it, but guard anyway.
    return c.json({ detail: 'Invalid reset token' }, 400);
  }

  const newHash = await hashPassword(password);
  const validAfter = now + 1;
  await c.env.DB.prepare(
    `UPDATE users
     SET hashed_password = ?,
         updated_at = ?,
         session_valid_after = MAX(session_valid_after + 1, ?)
     WHERE id = ?`
  ).bind(newHash, now, validAfter, record.user_id).run();

  return c.json({ message: 'Password reset successfully' });
});
