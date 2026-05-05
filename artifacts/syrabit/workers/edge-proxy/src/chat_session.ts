/**
 * ChatSession Durable Object — Task #386.
 *
 * Holds per-session chat state at the edge so a multi-PoP deployment
 * doesn't need a round-trip to the origin Redis on every turn. The
 * backend speaks to this DO over HTTPS via the edge proxy:
 *
 *   GET    /do/chat-session/<id>     → { session: {...} | null }
 *   PUT    /do/chat-session/<id>     → { ok: true }
 *   DELETE /do/chat-session/<id>     → { ok: true }
 *
 * Auth: every request must carry the shared `Authorization: Bearer
 * <DISPATCH_SHARED_SECRET>` header. The proxy enforces the bearer
 * before routing to the DO (see `src/index.ts`).
 *
 * Storage layout — one row per session id:
 *   key:  "session"
 *   val:  { ...payload, _expires_at: epoch_ms }
 *
 * TTL: the DO does not run cron, so expiry is enforced lazily on the
 * next read. This matches the in-process fallback semantics in
 * `do_chat.py` (no background sweeper either).
 */

export interface ChatSessionPayload {
  user_id?: string;
  history?: Array<{ role: string; content: string; ts?: number }>;
  metadata?: Record<string, unknown>;
  [k: string]: unknown;
}

interface StoredSession extends ChatSessionPayload {
  _expires_at: number;
}

export class ChatSession {
  state: DurableObjectState;
  env: unknown;

  constructor(state: DurableObjectState, env: unknown) {
    this.state = state;
    this.env = env;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    // Typing-indicator channel — SPA polls /typing while a stream runs.
    if (url.pathname.endsWith('/typing')) {
      if (request.method === 'GET') {
        return this._getTyping();
      }
      if (request.method === 'PUT') {
        const body = await request.json().catch(() => null) as
          | { typing?: boolean; actor?: string; ttl_ms?: number }
          | null;
        if (!body) {
          return json({ ok: false, reason: 'bad_body' }, 400);
        }
        return this._putTyping(
          Boolean(body.typing),
          String(body.actor ?? 'assistant'),
          Math.max(500, Math.min(60_000, Number(body.ttl_ms ?? 5000))),
        );
      }
      return new Response('method not allowed', { status: 405 });
    }
    if (request.method === 'GET') {
      return this._get();
    }
    if (request.method === 'PUT') {
      const body = await request.json().catch(() => null) as
        | { session?: ChatSessionPayload; ttl?: number }
        | null;
      if (!body || !body.session) {
        return json({ ok: false, reason: 'missing_session' }, 400);
      }
      return this._put(body.session, Number(body.ttl ?? 1800));
    }
    if (request.method === 'DELETE') {
      return this._delete();
    }
    return new Response('method not allowed', { status: 405 });
  }

  private async _getTyping(): Promise<Response> {
    const stored = await this.state.storage.get<{ typing: boolean; actor: string; expires_at: number }>('typing');
    if (!stored || (stored.expires_at && stored.expires_at < Date.now())) {
      return json({ typing: false, actor: '', expires_at: 0 });
    }
    return json(stored);
  }

  private async _putTyping(typing: boolean, actor: string, ttlMs: number): Promise<Response> {
    const expiresAt = typing ? Date.now() + ttlMs : 0;
    await this.state.storage.put('typing', { typing, actor, expires_at: expiresAt });
    return json({ ok: true, typing, actor, expires_at: expiresAt });
  }

  private async _get(): Promise<Response> {
    const stored = await this.state.storage.get<StoredSession>('session');
    if (!stored) {
      return json({ session: null });
    }
    if (stored._expires_at && stored._expires_at < Date.now()) {
      await this.state.storage.delete('session');
      return json({ session: null });
    }
    const { _expires_at, ...payload } = stored;
    return json({ session: payload });
  }

  private async _put(payload: ChatSessionPayload, ttlSeconds: number): Promise<Response> {
    const expiresAt = ttlSeconds > 0 ? Date.now() + ttlSeconds * 1000 : 0;
    const stored: StoredSession = { ...payload, _expires_at: expiresAt };
    await this.state.storage.put('session', stored);
    return json({ ok: true, expires_at: expiresAt });
  }

  private async _delete(): Promise<Response> {
    const existed = await this.state.storage.delete('session');
    return json({ ok: true, existed });
  }
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}
