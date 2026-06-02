/**
 * Per-Language Rate Limiting via Cloudflare KV
 *
 * Tracks request counts per (userId + lang) combination using hourly windows.
 * Prevents Assamese quota exhaustion independently from English.
 * Uses KV with TTL for automatic cleanup — no manual expiry management needed.
 */

export interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  resetAt: number; // Unix timestamp (ms) when the window resets
}

/**
 * Check if a request is within the rate limit for a given user + language.
 *
 * @param kv - Cloudflare KV namespace binding
 * @param userId - Authenticated user ID (or "anonymous")
 * @param lang - Language code ("en" or "as")
 * @param limit - Max requests per window per language (default: 30 for free tier)
 * @returns RateLimitResult with allowed status, remaining count, and reset time
 */
export async function checkRateLimit(
  kv: KVNamespace,
  userId: string,
  lang: string,
  limit: number = 30
): Promise<RateLimitResult> {
  const now = Date.now();
  const windowMs = 60 * 60 * 1000; // 1-hour sliding window
  const windowKey = Math.floor(now / windowMs);
  const resetAt = (windowKey + 1) * windowMs;

  // Key format: rl:{userId}:{lang}:{hourWindow}
  const key = `rl:${userId}:${lang}:${windowKey}`;

  // Read current count
  const current = await kv.get(key);
  const count = current ? parseInt(current, 10) : 0;

  if (count >= limit) {
    return {
      allowed: false,
      remaining: 0,
      resetAt,
    };
  }

  // Increment counter (eventual consistency is acceptable for rate limiting)
  // TTL of 2 hours ensures cleanup even if window rolls over
  // Note: Read-then-write with KV eventual consistency means concurrent requests
  // may both pass the check. This is an accepted trade-off for edge rate limiting:
  // - KV is eventually consistent across PoPs (100-200ms propagation)
  // - Two concurrent requests from the same user may both read count=29 and both pass
  // - Worst case: a user sends ~2x their limit in a single burst before convergence
  // - For strong consistency guarantees, migrate to Cloudflare Durable Objects
  //   (single-threaded per-user actor with transactional storage)
  // The backend's monthly quota (apps/backend/app/api/deps/rate_limit.py) is the
  // authoritative billing enforcement; this edge limit is only burst protection.
  await kv.put(key, String(count + 1), { expirationTtl: 7200 });

  return {
    allowed: true,
    remaining: limit - count - 1,
    resetAt,
  };
}

/**
 * Build rate limit response headers for the client.
 */
export function rateLimitHeaders(result: RateLimitResult, limit: number = 30): Record<string, string> {
  return {
    'X-RateLimit-Limit': String(limit),
    'X-RateLimit-Remaining': String(Math.max(0, result.remaining)),
    'X-RateLimit-Reset': String(Math.floor(result.resetAt / 1000)),
    ...(result.allowed ? {} : {
      'Retry-After': String(Math.ceil((result.resetAt - Date.now()) / 1000)),
    }),
  };
}
