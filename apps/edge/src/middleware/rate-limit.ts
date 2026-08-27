/**
 * Per-Language Rate Limiting via Cloudflare Durable Objects
 *
 * Tracks request counts per (userId + lang) combination using hourly windows.
 * Prevents Assamese quota exhaustion independently from English.
 * One Durable Object instance owns each identity/language/window bucket, so
 * concurrent requests are serialized and cannot overwrite one another.
 */

export interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  resetAt: number; // Unix timestamp (ms) when the window resets
}

interface RateLimitCommand {
  limit: number;
  resetAt: number;
}

const BROWSER_ANON_ID_PATTERN = /^anon_[a-f0-9]{32}$/;

/**
 * Resolve the anonymous browser identity used by the edge burst limiter.
 * This mirrors the API Worker's anonymous identity contract so one browser
 * does not share a global limiter bucket with every other anonymous student.
 */
export function anonymousRateLimitIdentity(request: Request): string {
  const browserId = request.headers.get('x-anon-id')?.trim();
  if (browserId && BROWSER_ANON_ID_PATTERN.test(browserId)) return browserId;

  // Cloudflare overwrites this connection metadata. Do not trust forwarding
  // headers supplied by a caller when choosing a rate-limit bucket.
  const ip = request.headers.get('CF-Connecting-IP') ?? 'unknown';
  const normalizedIp = ip.trim().toLowerCase().replace(/[^a-z0-9]/g, '_').slice(0, 55);
  return `ip_${normalizedIp}`;
}

/**
 * Check if a request is within the rate limit for a given user + language.
 *
 * @param namespace - Durable Object namespace for strongly-consistent counters
 * @param userId - Authenticated user ID (or "anonymous")
 * @param lang - Language code ("en" or "as")
 * @param limit - Max requests per window per language (default: 30 for free tier)
 * @returns RateLimitResult with allowed status, remaining count, and reset time
 */
export async function checkRateLimit(
  namespace: DurableObjectNamespace,
  userId: string,
  lang: string,
  limit: number = 30
): Promise<RateLimitResult> {
  const now = Date.now();
  const windowMs = 60 * 60 * 1000; // 1-hour sliding window
  const windowKey = Math.floor(now / windowMs);
  const resetAt = (windowKey + 1) * windowMs;

  const bucket = `rl:${userId}:${lang}:${windowKey}`;
  const stub = namespace.get(namespace.idFromName(bucket));
  const response = await stub.fetch('https://rate-limit.internal/check', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ limit, resetAt } satisfies RateLimitCommand),
  });

  if (!response.ok) {
    throw new Error(`Atomic rate-limit store returned ${response.status}`);
  }

  return response.json<RateLimitResult>();
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

/**
 * Strongly-consistent hourly counter. A bucket name maps to one object, and a
 * Durable Object processes requests one at a time. The alarm removes expired
 * state after the hour rolls over.
 */
export class RateLimitDurableObject {
  constructor(private readonly state: DurableObjectState) {}

  async fetch(request: Request): Promise<Response> {
    if (request.method !== 'POST') {
      return Response.json({ error: 'Method not allowed' }, { status: 405 });
    }

    let command: RateLimitCommand;
    try {
      command = await request.json<RateLimitCommand>();
    } catch {
      return Response.json({ error: 'Invalid request' }, { status: 400 });
    }

    if (!Number.isSafeInteger(command.limit) || command.limit < 1
      || !Number.isFinite(command.resetAt) || command.resetAt <= Date.now()) {
      return Response.json({ error: 'Invalid rate-limit command' }, { status: 400 });
    }

    const result = await this.state.storage.transaction(async (txn) => {
      const count = await txn.get<number>('count') ?? 0;
      if (count >= command.limit) {
        return {
          allowed: false,
          remaining: 0,
          resetAt: command.resetAt,
        } satisfies RateLimitResult;
      }

      const nextCount = count + 1;
      await txn.put('count', nextCount);
      return {
        allowed: true,
        remaining: command.limit - nextCount,
        resetAt: command.resetAt,
      } satisfies RateLimitResult;
    });
    await this.state.storage.setAlarm(command.resetAt);

    return Response.json(result);
  }

  async alarm(): Promise<void> {
    await this.state.storage.deleteAll();
  }
}
