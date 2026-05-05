/**
 * RateLimiter Durable Object — Task #386.
 *
 * Atomic per-key rate-limit counter. The backend posts:
 *
 *   POST /do/rate-limiter/check
 *     body: { key: "feature:identity", limit: 30, window_s: 60 }
 *     resp: { allowed: true, remaining: 29, window_resets_at: <ms> }
 *
 * Implementation is a fixed-window counter per ``key`` — when the
 * current window expires the counter is reset to 1 on the next call.
 * The Durable Object guarantees serialised access so two concurrent
 * checks for the same key do not both pass the limit.
 *
 * Multi-key isolation: this DO is intentionally singleton across the
 * whole zone (one ID name) and stores N keys internally. That keeps
 * cross-region routing latency deterministic for the chat gate (we'd
 * rather pay one fixed RTT than have the DO migrate per key).
 */

interface BucketEntry {
  count: number;
  window_resets_at: number;
}

export class RateLimiter {
  state: DurableObjectState;
  env: unknown;

  constructor(state: DurableObjectState, env: unknown) {
    this.state = state;
    this.env = env;
  }

  async fetch(request: Request): Promise<Response> {
    if (request.method !== 'POST') {
      return new Response('method not allowed', { status: 405 });
    }
    const body = await request.json().catch(() => null) as
      | { key?: string; limit?: number; window_s?: number }
      | null;
    if (!body || !body.key || !body.limit) {
      return json({ allowed: false, reason: 'bad_request' }, 400);
    }
    const key = String(body.key);
    const limit = Math.max(1, Number(body.limit));
    const windowS = Math.max(1, Number(body.window_s ?? 60));
    return this._check(key, limit, windowS);
  }

  private async _check(key: string, limit: number, windowS: number): Promise<Response> {
    const now = Date.now();
    const stored = await this.state.storage.get<BucketEntry>(`bucket:${key}`);
    if (!stored || stored.window_resets_at <= now) {
      const next: BucketEntry = { count: 1, window_resets_at: now + windowS * 1000 };
      await this.state.storage.put(`bucket:${key}`, next);
      return json({
        allowed: true,
        remaining: Math.max(0, limit - 1),
        window_resets_at: next.window_resets_at,
      });
    }
    if (stored.count >= limit) {
      return json({
        allowed: false,
        remaining: 0,
        window_resets_at: stored.window_resets_at,
      });
    }
    const updated: BucketEntry = {
      count: stored.count + 1,
      window_resets_at: stored.window_resets_at,
    };
    await this.state.storage.put(`bucket:${key}`, updated);
    return json({
      allowed: true,
      remaining: Math.max(0, limit - updated.count),
      window_resets_at: updated.window_resets_at,
    });
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
