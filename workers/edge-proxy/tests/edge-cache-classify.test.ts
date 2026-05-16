import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import worker, { isCacheable, isBypass, isUserSpecific } from "../src/index";

// ─────────────────────────────────────────────────────────────────────────────
// In-memory Cache API stub (mirrors Cloudflare's Cache interface)
// ─────────────────────────────────────────────────────────────────────────────
class InMemoryCache {
  private store = new Map<string, Response>();

  async match(req: Request | string): Promise<Response | undefined> {
    const key = typeof req === "string" ? req : req.url;
    const stored = this.store.get(key);
    if (!stored) return undefined;
    return stored.clone();
  }

  async put(req: Request | string, response: Response): Promise<void> {
    const key = typeof req === "string" ? req : req.url;
    this.store.set(key, response.clone());
  }

  async delete(req: Request | string): Promise<boolean> {
    const key = typeof req === "string" ? req : req.url;
    return this.store.delete(key);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// In-memory KV stub (permissive rate-limit: always returns 0 for any key)
// ─────────────────────────────────────────────────────────────────────────────
class FakeKv {
  private store = new Map<string, string>();
  async get(key: string): Promise<string | null> {
    return this.store.get(key) ?? null;
  }
  async put(key: string, value: string): Promise<void> {
    this.store.set(key, value);
  }
  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }
  async list(): Promise<{ keys: { name: string }[]; list_complete: boolean }> {
    return { keys: [], list_complete: true };
  }
  getWithMetadata(key: string) {
    return Promise.resolve({ value: this.store.get(key) ?? null, metadata: null });
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// ExecutionContext stub — runs waitUntil tasks synchronously so the cache.put
// from the first request completes before we make the second request.
// ─────────────────────────────────────────────────────────────────────────────
class FakeCtx {
  private pending: Promise<unknown>[] = [];

  waitUntil(p: Promise<unknown>) {
    this.pending.push(p);
  }

  async flush() {
    await Promise.allSettled(this.pending);
    this.pending = [];
  }
}

function makeEnv(kv: FakeKv): Record<string, unknown> {
  return {
    BACKEND_URL: "https://backend.test",
    BACKEND_ORIGIN_SECRET: undefined,
    PAGES_ORIGIN: "https://pages.test",
    RATE_LIMIT: kv,
    BOT_HTML_CACHE: undefined,
    CONTENT_DB: undefined, // omit D1 so cache path goes straight to backend fetch
    D1_SYNC_SECRET: "",
    JWT_SECRET: "test-jwt-secret",
  };
}

const BASE = "https://syrabit.ai";
const JSON_BODY = JSON.stringify({ data: "ok" });

// ─────────────────────────────────────────────────────────────────────────────
// Helper: sends a GET request through the worker and awaits ctx.waitUntil
// ─────────────────────────────────────────────────────────────────────────────
async function hit(
  url: string,
  env: Record<string, unknown>,
  ctx: FakeCtx,
): Promise<Response> {
  const req = new Request(url, { method: "GET" });
  const resp = await worker.fetch(
    req,
    env as unknown as Parameters<typeof worker.fetch>[1],
    ctx as unknown as ExecutionContext,
  );
  await ctx.flush();
  return resp;
}

// ─────────────────────────────────────────────────────────────────────────────
// Static classification checks (fast guard — if these fail the warm-request
// tests below will also fail for predictable reasons)
// ─────────────────────────────────────────────────────────────────────────────
describe("route classification", () => {
  // Cache config is sourced from `monitored-urls.json` (Task #900). Every
  // path here MUST have a `backend_paths` entry with `edge_cache.behavior =
  // "cacheable"`. Stale prefixes that never matched a real FastAPI route
  // (e.g. /api/sitemap, /api/notes/public, /api/mcq/, /api/cms/articles,
  // /api/flashcards/, /api/content/syllabus/, /api/robots.txt) were
  // removed in Task #900 — they were dead config that cached nothing.
  const CACHED = [
    "/api/content/boards",
    "/api/content/subjects",
    "/api/content/chapters/some-slug",
    "/api/content/library-bundle",
    "/api/seo/keyword-index",
    "/api/pyq/123",
    "/api/edu/allowlist",
  ];
  const BYPASS = ["/api/ai/chat/stream", "/api/webhooks/razorpay", "/api/auth/login"];
  const USER_SPECIFIC = ["/api/user/stats"];
  const PASSTHROUGH = [
    "/api/health",
    "/api/admin/diagnostics",
    "/api/analytics/event",
    "/api/conversations/abc",
    "/api/user/profile",
  ];

  for (const p of CACHED) {
    it(`${p} → cacheable, not bypass, not user-specific`, () => {
      expect(isCacheable(p)).toBe(true);
      expect(isBypass(p)).toBe(false);
      expect(isUserSpecific(p)).toBe(false);
    });
  }

  for (const p of BYPASS) {
    it(`${p} → bypass, not cacheable`, () => {
      expect(isBypass(p)).toBe(true);
      expect(isCacheable(p)).toBe(false);
    });
  }

  for (const p of USER_SPECIFIC) {
    it(`${p} → cacheable AND user-specific`, () => {
      expect(isCacheable(p)).toBe(true);
      expect(isUserSpecific(p)).toBe(true);
      expect(isBypass(p)).toBe(false);
    });
  }

  for (const p of PASSTHROUGH) {
    it(`${p} → not cached, not bypass (intentional passthrough)`, () => {
      expect(isCacheable(p)).toBe(false);
      expect(isBypass(p)).toBe(false);
    });
  }

  it("does NOT cache /api/notes/private (only /api/notes/public is listed)", () => {
    expect(isCacheable("/api/notes/private")).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Warm-second-request matrix
//
// Each test sends the same GET request TWICE through the live worker handler
// with a shared in-memory cache.  The first call should be a MISS (X-Cache:
// MISS, X-Source: backend).  After ctx.flush() the cache.put is committed, so
// the second call hits the in-memory cache and should return X-Cache: HIT.
// ─────────────────────────────────────────────────────────────────────────────
describe("warm second request – X-Cache header assertions", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  let fakeCache: InMemoryCache;
  let kv: FakeKv;
  let env: Record<string, unknown>;

  beforeEach(() => {
    fakeCache = new InMemoryCache();
    kv = new FakeKv();
    env = makeEnv(kv);

    fetchMock = vi.fn().mockImplementation(async (_url: unknown) => {
      return new Response(JSON_BODY, {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    vi.stubGlobal("caches", { default: fakeCache });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const CACHE_ROUTES = [
    "/api/content/boards",
    "/api/content/subjects",
    "/api/content/chapters/some-chapter",
    "/api/content/library-bundle",
    "/api/seo/some-slug",
    "/api/pyq/2024",
  ];

  for (const path of CACHE_ROUTES) {
    it(`${path}: first request is MISS, second is HIT`, async () => {
      const ctx = new FakeCtx();
      const url = `${BASE}${path}`;

      const first = await hit(url, env, ctx);
      expect(first.headers.get("X-Cache")).toBe("MISS");
      expect(first.headers.get("X-Source")).toBe("backend");

      const second = await hit(url, env, ctx);
      expect(second.headers.get("X-Cache")).toBe("HIT");
      expect(second.headers.get("X-Source")).toBe("cf-cache");
    });
  }

  // Bypass routes must always return X-Cache: BYPASS, X-Source: backend —
  // never a cache hit — because proxyToBackend() sets those headers explicitly.
  const BYPASS_ROUTES = [
    "/api/auth/logout",
    "/api/webhooks/stripe",
  ];

  for (const path of BYPASS_ROUTES) {
    it(`${path}: bypass route – X-Cache: BYPASS, X-Source: backend on every request`, async () => {
      const ctx = new FakeCtx();
      const url = `${BASE}${path}`;

      const first = await hit(url, env, ctx);
      expect(first.headers.get("X-Cache")).toBe("BYPASS");
      expect(first.headers.get("X-Source")).toBe("backend");

      const second = await hit(url, env, ctx);
      expect(second.headers.get("X-Cache")).toBe("BYPASS");
      expect(second.headers.get("X-Source")).toBe("backend");
    });
  }

  // /api/health is served by the worker itself with X-Source: edge (no cache)
  it("/api/health: served fresh by edge, no X-Cache: HIT", async () => {
    const ctx = new FakeCtx();
    const url = `${BASE}/api/health`;

    await hit(url, env, ctx);
    const second = await hit(url, env, ctx);
    expect(second.headers.get("X-Source")).toBe("edge");
    expect(second.headers.get("X-Cache")).not.toBe("HIT");
  });
});
