/**
 * Auth middleware — password hashing, JWT signing, token helpers
 *
 * === Bcrypt + SHA-256 path for >72-byte passwords ===
 *
 * bcryptjs internally UTF-8 encodes its string input (utf8Array in index.js)
 * before passing bytes to the Blowfish key schedule. Python's bcrypt library
 * accepts raw bytes. For passwords ≤72 UTF-8 bytes both paths are identical
 * because UTF-8 of ASCII/short strings === raw bytes.
 *
 * For passwords >72 UTF-8 bytes Python passes the raw 32-byte SHA-256 digest.
 * bcryptjs would UTF-8-re-encode that digest string, changing bytes 128-255
 * into 2-byte sequences — a different key schedule → different hash.
 *
 * Resolution:
 *   • Passwords ≤72 bytes  — direct string → bcryptjs. Perfect
 *     cross-language compatibility with all existing Python hashes.
 *   • New registrations >72 bytes — SHA-256 → base64url (44 ASCII chars,
 *     all ≤127, UTF-8 = ASCII, no encoding shift). Consistent & correct.
 *   • Migrated Python hashes for >72-byte passwords — incompatible.
 *     verifyPassword() returns false; the caller must surface a
 *     "please reset your password" response. In practice these users are
 *     vanishingly rare (>72 bytes ≈ >72 ASCII chars or ~24-36 CJK chars).
 *
 * Test vectors (compile-time cross-check via _selfTest()):
 *   "hello"          → bcrypt("hello", rounds)                 [ASCII, ≤72]
 *   "αβγδεζηθ"       → bcrypt("αβγδεζηθ", rounds)             [multibyte, ≤72]
 *   72-byte ASCII    → bcrypt(password, rounds)                [boundary, ≤72]
 *   73-byte ASCII    → bcrypt(base64url(sha256(raw)), rounds)  [>72, new path]
 */

import { SignJWT, jwtVerify, type JWTPayload } from 'jose';
import * as bcrypt from 'bcryptjs';
import type { JwtPayload, AdminJwtPayload } from '../types';

const BCRYPT_MAX = 72;           // max UTF-8 bytes bcrypt processes before truncation
const ACCESS_TOKEN_TTL = 60 * 60 * 24 * 7;     // 7 days  (seconds)
const REFRESH_TOKEN_TTL = 60 * 60 * 24 * 30;   // 30 days (seconds)
const BCRYPT_ROUNDS = 12;

// ── Password helpers ──────────────────────────────────────────────────────────

/**
 * Returns the string to pass to bcryptjs.
 *
 * For ≤72 UTF-8 bytes: returns the password as-is.
 *   bcryptjs utf8Array(password) === Python password.encode("utf-8")
 *   → identical byte sequences → identical hashes.
 *
 * For >72 UTF-8 bytes: returns base64url(sha256(raw_utf8_bytes)).
 *   All 44 chars are ASCII (≤127) so UTF-8 = ASCII; bcryptjs sees the same
 *   bytes as any other environment. Not compatible with legacy Python hashes
 *   that used raw-digest bytes — those users must reset their password.
 */
async function preparePwForBcrypt(password: string): Promise<string> {
  const raw = new TextEncoder().encode(password);
  if (raw.length <= BCRYPT_MAX) {
    return password;                             // identity — all ≤127 paths trivially safe
  }
  const hashBuf = await crypto.subtle.digest('SHA-256', raw);
  // base64url — alphabet is [A-Za-z0-9-_=], every char is pure ASCII (≤127)
  const base64 = btoa(String.fromCharCode(...new Uint8Array(hashBuf)));
  return base64.replace(/\+/g, '-').replace(/\//g, '_');
}

export async function hashPassword(password: string): Promise<string> {
  const prepared = await preparePwForBcrypt(password);
  return bcrypt.hash(prepared, BCRYPT_ROUNDS);
}

/**
 * Verifies a password against a stored bcrypt hash.
 *
 * Returns false for all of these:
 *   - wrong password
 *   - corrupted hash
 *   - migrated Python hash for a >72-byte password (fundamentally incompatible)
 *
 * Callers should distinguish this case by checking password UTF-8 length > 72
 * and returning HTTP 400 with "password_reset_required" rather than 401.
 */
export async function verifyPassword(password: string, hash: string): Promise<boolean> {
  try {
    const prepared = await preparePwForBcrypt(password);
    return bcrypt.compare(prepared, hash);
  } catch {
    return false;
  }
}

/** True when password is >72 UTF-8 bytes — these hashes may not survive migration. */
export function isLongPassword(password: string): boolean {
  return new TextEncoder().encode(password).length > BCRYPT_MAX;
}

// ── JWT helpers ───────────────────────────────────────────────────────────────

function secretKey(secret: string): Uint8Array {
  return new TextEncoder().encode(secret);
}

export async function signAccessToken(
  userId: string,
  role: string,
  secret: string,
  issuedAt = Math.floor(Date.now() / 1000),
): Promise<string> {
  return new SignJWT({ role, type: 'access' })
    .setProtectedHeader({ alg: 'HS256' })
    .setSubject(userId)
    .setIssuedAt(issuedAt)
    .setExpirationTime(`${ACCESS_TOKEN_TTL}s`)
    .sign(secretKey(secret));
}

/** Sign the httpOnly admin-session token used by the legacy admin UI. */
export async function signAdminToken(
  userId: string,
  secret: string,
  issuedAt = Math.floor(Date.now() / 1000),
): Promise<string> {
  return new SignJWT({ role: 'admin', type: 'admin' })
    .setProtectedHeader({ alg: 'HS256' })
    .setSubject(userId)
    .setIssuedAt(issuedAt)
    .setExpirationTime('8h')
    .sign(secretKey(secret));
}

/**
 * Signs a refresh token with a unique jti (JWT ID).
 * The jti is used to implement single-use refresh tokens via KV revocation.
 */
export async function signRefreshToken(
  userId: string,
  role: string,
  secret: string,
  issuedAt = Math.floor(Date.now() / 1000),
): Promise<{ token: string; jti: string }> {
  const jti = crypto.randomUUID();
  const token = await new SignJWT({ role, type: 'refresh', jti })
    .setProtectedHeader({ alg: 'HS256' })
    .setSubject(userId)
    .setIssuedAt(issuedAt)
    .setExpirationTime(`${REFRESH_TOKEN_TTL}s`)
    .sign(secretKey(secret));
  return { token, jti };
}

export async function verifyToken(
  token: string,
  secret: string,
): Promise<(JWTPayload & JwtPayload & { jti?: string }) | null> {
  try {
    const { payload } = await jwtVerify(token, secretKey(secret));
    return payload as JWTPayload & JwtPayload & { jti?: string };
  } catch {
    return null;
  }
}

export async function verifyAdminToken(
  token: string,
  secret: string,
): Promise<(JWTPayload & AdminJwtPayload) | null> {
  try {
    const { payload } = await jwtVerify(token, secretKey(secret));
    // Cloud Run's established admin-session contract uses `type: "admin"`.
    // `admin_access` is kept for Worker-issued sessions during the transition.
    // Both are signed by the isolated ADMIN_JWT_SECRET and must be admin-only.
    if (!['admin', 'admin_access'].includes(String(payload['type']))
      || payload['role'] !== 'admin') return null;
    return payload as JWTPayload & AdminJwtPayload;
  } catch {
    return null;
  }
}

/** Extract Bearer token from Authorization header */
export function extractBearer(authHeader: string | null): string | null {
  if (!authHeader?.startsWith('Bearer ')) return null;
  return authHeader.slice(7).trim() || null;
}

/** SHA-256 hex digest of a reset token for safe storage */
export async function hashResetToken(token: string): Promise<string> {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token));
  return Array.from(new Uint8Array(buf))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

// ── KV key helpers ────────────────────────────────────────────────────────────

/** KV key for a revoked refresh token entry */
export function revokedRtKey(jti: string): string {
  return `revoked_rt:${jti}`;
}

/** TTL (seconds) to store in KV for a revoked refresh token */
export const REFRESH_TOKEN_TTL_S = REFRESH_TOKEN_TTL;

/**
 * Atomically claim a refresh token's jti.
 *
 * D1 serializes writes and jti is the primary key, so exactly one concurrent
 * caller can insert the claim. Storage errors are deliberately allowed to
 * propagate: refresh must fail closed rather than mint tokens without rotation.
 */
export async function claimRefreshToken(
  db: D1Database,
  jti: string,
  userId: string,
  expiresAt: number,
): Promise<boolean> {
  const result = await db.prepare(`
    INSERT INTO refresh_token_claims (jti, user_id, expires_at, claimed_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT (jti) DO NOTHING
  `).bind(
    jti,
    userId,
    expiresAt,
    Math.floor(Date.now() / 1000),
  ).run();

  return result.meta.changes === 1;
}

/** Returns false when a token predates an account-wide session cutoff. */
export async function isSessionValid(
  db: D1Database,
  userId: string,
  issuedAt: number | undefined,
): Promise<boolean> {
  const row = await db.prepare(
    'SELECT session_valid_after FROM users WHERE id = ?',
  ).bind(userId).first<{ session_valid_after: number }>();
  return (issuedAt ?? 0) >= (row?.session_valid_after ?? Number.POSITIVE_INFINITY);
}

/** Timestamp used when issuing a fresh token after a password change. */
export async function sessionIssuedAt(db: D1Database, userId: string): Promise<number> {
  const row = await db.prepare(
    'SELECT session_valid_after FROM users WHERE id = ?',
  ).bind(userId).first<{ session_valid_after: number }>();
  return Math.max(Math.floor(Date.now() / 1000), row?.session_valid_after ?? 0);
}
