/**
 * POST /api/v1/internal/generate
 *
 * Authenticated internal endpoint for Workers-to-Workers text generation.
 * Authentication: timing-safe Bearer token comparison against EDGE_SHARED_SECRET.
 *
 * Request body:
 *   { system_prompt: string, user_message: string, max_output_tokens?: number }
 *
 * Response:
 *   { text: string, model: string }
 */

import { Hono }     from 'hono';
import { generate } from '../services/ai';
import type { Env }  from '../types';

// Maximum body field lengths — guard against oversized payloads
const MAX_SYSTEM_PROMPT  = 32_000;
const MAX_USER_MESSAGE   = 8_000;
const MAX_OUTPUT_TOKENS  = 4_096;

export const internalRouter = new Hono<{ Bindings: Env }>();

internalRouter.post('/generate', async (c) => {
  // ── 1. Fail closed when the service secret is unavailable ──────────────────
  const secret = c.env.EDGE_SHARED_SECRET ?? '';
  if (!secret) {
    // Never permit an empty shared secret: an empty `Bearer` value would
    // otherwise compare equal and expose the private generation route.
    return c.json({ detail: 'Generation service is not configured' }, 503);
  }

  // ── 2. Bearer token auth (timing-safe) ─────────────────────────────────────
  const authHeader = c.req.header('Authorization') ?? '';
  if (!authHeader.startsWith('Bearer ')) {
    return c.json({ detail: 'Unauthorized' }, 401);
  }
  const token  = authHeader.slice(7).trim();

  // Timing-safe comparison using Web Crypto HMAC to avoid timing attacks.
  // We sign the incoming token with the secret and compare HMACs rather than
  // comparing raw strings, so both paths always exercise the same crypto path.
  const enc      = new TextEncoder();
  const keyBytes = enc.encode(secret);

  let authorized = false;
  try {
    const key = await crypto.subtle.importKey(
      'raw',
      keyBytes,
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['sign'],
    );
    const expectedSig = await crypto.subtle.sign('HMAC', key, enc.encode(token));
    const secretSig   = await crypto.subtle.sign('HMAC', key, enc.encode(secret));
    // Compare digests of (token) and (secret) — equal only when token === secret
    authorized = token.length === secret.length &&
      timingSafeEqual(new Uint8Array(expectedSig), new Uint8Array(secretSig));
  } catch {
    return c.json({ detail: 'Unauthorized' }, 401);
  }

  if (!authorized) {
    return c.json({ detail: 'Unauthorized' }, 401);
  }

  // ── 2. Parse & validate body ────────────────────────────────────────────────
  let body: { system_prompt?: unknown; user_message?: unknown; max_output_tokens?: unknown };
  try {
    body = await c.req.json();
  } catch {
    return c.json({ detail: 'Invalid JSON body' }, 400);
  }

  if (typeof body.system_prompt !== 'string' || !body.system_prompt.trim()) {
    return c.json({ detail: 'system_prompt is required and must be a non-empty string' }, 422);
  }
  if (typeof body.user_message !== 'string' || !body.user_message.trim()) {
    return c.json({ detail: 'user_message is required and must be a non-empty string' }, 422);
  }

  const systemPrompt = body.system_prompt.trim().slice(0, MAX_SYSTEM_PROMPT);
  const userMessage  = body.user_message.trim().slice(0, MAX_USER_MESSAGE);

  let maxTokens: number | undefined;
  if (body.max_output_tokens !== undefined) {
    const n = Number(body.max_output_tokens);
    if (!Number.isInteger(n) || n < 1 || n > MAX_OUTPUT_TOKENS) {
      return c.json(
        { detail: `max_output_tokens must be an integer between 1 and ${MAX_OUTPUT_TOKENS}` },
        422,
      );
    }
    maxTokens = n;
  }

  // ── 3. Generate ─────────────────────────────────────────────────────────────
  try {
    const result = await generate(c.env.AI, {
      systemPrompt,
      userMessage,
      ...(maxTokens !== undefined && { maxTokens }),
    });

    return c.json({ text: result.text, model: result.model });
  } catch (err) {
    console.error('[internal/generate] AI generation failed:', err);
    return c.json({ detail: 'AI service temporarily unavailable' }, 503);
  }
});

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Constant-time comparison of two Uint8Arrays.
 * Returns true only if both have identical length and contents.
 */
function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.byteLength !== b.byteLength) return false;
  let diff = 0;
  for (let i = 0; i < a.byteLength; i++) {
    // eslint-disable-next-line @typescript-eslint/no-non-null-assertion
    diff |= a[i]! ^ b[i]!;
  }
  return diff === 0;
}
