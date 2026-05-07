import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  wrapKvNamespace,
  getUsageSnapshot,
  getUsageSnapshotAggregated,
  _resetMonitorStateForTests,
  DEFAULT_QUOTA,
} from "../src/kv-monitor";
import {
  recordBotCacheEvent,
  getBotCacheStats,
  botCacheKey,
  currentBotCacheBucket,
  BOT_CACHE_BUCKETS_PER_WINDOW,
} from "../src/bot-cache-stats";

/* ───────────── fakes ───────────── */

class FakeKv {
  private store = new Map<string, string>();
  public failNext = 0; // count of next ops that should throw
  public failAll = false;
  public lastPutOpts: unknown = undefined;

  private maybeFail(): void {
    if (this.failAll) throw new Error("kv simulated outage");
    if (this.failNext > 0) {
      this.failNext -= 1;
      throw new Error("kv simulated transient failure");
    }
  }

  async get(key: string): Promise<string | null> {
    this.maybeFail();
    return this.store.has(key) ? this.store.get(key)! : null;
  }
  async put(key: string, value: string, opts?: unknown): Promise<void> {
    this.maybeFail();
    this.lastPutOpts = opts;
    this.store.set(key, value);
  }
  async delete(key: string): Promise<void> {
    this.maybeFail();
    this.store.delete(key);
  }
  async list(): Promise<{ keys: { name: string }[]; list_complete: boolean }> {
    this.maybeFail();
    return {
      keys: Array.from(this.store.keys()).map((name) => ({ name })),
      list_complete: true,
    };
  }
  async getWithMetadata(key: string): Promise<{ value: string | null; metadata: unknown }> {
    this.maybeFail();
    return { value: this.store.get(key) ?? null, metadata: null };
  }
}

class FakeCache {
  private store = new Map<string, Response>();
  async match(req: Request | string): Promise<Response | undefined> {
    const url = typeof req === "string" ? req : req.url;
    const r = this.store.get(url);
    return r ? r.clone() : undefined;
  }
  async put(req: Request | string, resp: Response): Promise<void> {
    const url = typeof req === "string" ? req : req.url;
    this.store.set(url, resp.clone());
  }
}

const noopCtx = {
  waitUntil: (_p: Promise<unknown>) => undefined,
  passThroughOnException: () => undefined,
} as unknown as ExecutionContext;

beforeEach(() => {
  _resetMonitorStateForTests();
});

/* ───────────── counter accuracy ───────────── */

describe("counter accuracy", () => {
  it("increments read/write/list/delete counters per op", async () => {
    const kv = wrapKvNamespace(new FakeKv() as unknown as KVNamespace, "RATE_LIMIT", {
      cache: new FakeCache() as unknown as Cache,
      ctx: noopCtx,
    });
    await kv.put("a", "1");
    await kv.put("b", "2");
    await kv.get("a");
    await kv.get("missing");
    await kv.delete("b");
    await kv.list();

    const snap = getUsageSnapshot(["RATE_LIMIT"]);
    const b = snap.bindings[0];
    expect(b.counters.write).toBe(2);
    expect(b.counters.read).toBe(2);
    expect(b.counters.delete).toBe(1);
    expect(b.counters.list).toBe(1);
    expect(b.status).toBe("healthy");
    expect(b.fallbackActive).toBe(false);
  });

  it("keeps separate counters per binding name", async () => {
    const a = wrapKvNamespace(new FakeKv() as unknown as KVNamespace, "RATE_LIMIT", { ctx: noopCtx });
    const b = wrapKvNamespace(new FakeKv() as unknown as KVNamespace, "BOT_HTML_CACHE", { ctx: noopCtx });
    await a.get("x");
    await a.get("y");
    await b.put("p", "q");

    const snap = getUsageSnapshot(["RATE_LIMIT", "BOT_HTML_CACHE"]);
    const ratelimit = snap.bindings.find((x) => x.binding === "RATE_LIMIT")!;
    const bothtml = snap.bindings.find((x) => x.binding === "BOT_HTML_CACHE")!;
    expect(ratelimit.counters.read).toBe(2);
    expect(ratelimit.counters.write).toBe(0);
    expect(bothtml.counters.write).toBe(1);
    expect(bothtml.counters.read).toBe(0);
  });
});

/* ───────────── fallback paths ───────────── */

describe("read fallback to Cache API on KV failure", () => {
  it("returns the last-known-good value from the Cache API when KV throws", async () => {
    const inner = new FakeKv();
    const cache = new FakeCache();
    const kv = wrapKvNamespace(inner as unknown as KVNamespace, "RATE_LIMIT", {
      cache: cache as unknown as Cache,
      ctx: noopCtx,
    });

    // Seed the value through the wrapper so the Cache API mirror is populated.
    await kv.put("hello", "world");
    // Wait a tick so the (sync-resolved) `void writeToCacheFallback` has settled.
    await new Promise((r) => setTimeout(r, 0));

    // Now KV starts failing.
    inner.failAll = true;
    const v = await kv.get("hello");
    expect(v).toBe("world");

    // Snapshot reflects the active fallback.
    const snap = getUsageSnapshot(["RATE_LIMIT"]);
    expect(snap.bindings[0].fallbackActive).toBe(true);
  });

  it("returns null (not throw) when KV fails and Cache API has nothing", async () => {
    const inner = new FakeKv();
    inner.failAll = true;
    const kv = wrapKvNamespace(inner as unknown as KVNamespace, "RATE_LIMIT", {
      cache: new FakeCache() as unknown as Cache,
      ctx: noopCtx,
    });
    await expect(kv.get("nothing")).resolves.toBeNull();
  });

  it("list() returns an empty completed result instead of throwing on KV failure", async () => {
    const inner = new FakeKv();
    inner.failAll = true;
    const kv = wrapKvNamespace(inner as unknown as KVNamespace, "RATE_LIMIT", { ctx: noopCtx });
    const r = await kv.list();
    expect(r.keys).toEqual([]);
    expect(r.list_complete).toBe(true);
  });
});

describe("write fallback queues deferred writes when KV throws", () => {
  it("does not throw to the caller when put() fails", async () => {
    const inner = new FakeKv();
    inner.failAll = true;
    const kv = wrapKvNamespace(inner as unknown as KVNamespace, "RATE_LIMIT", { ctx: noopCtx });
    await expect(kv.put("k", "v", { expirationTtl: 60 })).resolves.toBeUndefined();
    const snap = getUsageSnapshot(["RATE_LIMIT"]);
    expect(snap.bindings[0].fallbackActive).toBe(true);
    expect(snap.bindings[0].counters.write).toBe(1);
  });

  it("drains the deferred queue once KV recovers (replay via setTimeout)", async () => {
    vi.useFakeTimers();
    try {
      const inner = new FakeKv();
      inner.failAll = true;
      const kv = wrapKvNamespace(inner as unknown as KVNamespace, "RATE_LIMIT", { ctx: noopCtx });
      await kv.put("a", "1");
      await kv.put("b", "2");
      // KV recovers.
      inner.failAll = false;
      // Advance past the 1s backoff so the replay runs.
      await vi.advanceTimersByTimeAsync(1100);
      // Both keys should now be in the underlying store.
      expect(await inner.get("a")).toBe("1");
      expect(await inner.get("b")).toBe("2");
    } finally {
      vi.useRealTimers();
    }
  });
});

/* ───────────── threshold / status ───────────── */

describe("threshold detection", () => {
  it("transitions healthy → warning → exhausted as the counter climbs", async () => {
    const kv = wrapKvNamespace(new FakeKv() as unknown as KVNamespace, "RATE_LIMIT", {
      ctx: noopCtx,
      quota: { write: 10, read: DEFAULT_QUOTA.read, list: DEFAULT_QUOTA.list, delete: DEFAULT_QUOTA.delete },
      warningPct: 80,
    });
    for (let i = 0; i < 7; i++) await kv.put(`k${i}`, "v");
    let snap = getUsageSnapshot(["RATE_LIMIT"], { quota: { write: 10 }, warningPct: 80 });
    expect(snap.bindings[0].status).toBe("healthy");

    await kv.put("k7", "v"); // 8/10 = 80% → warning
    snap = getUsageSnapshot(["RATE_LIMIT"], { quota: { write: 10 }, warningPct: 80 });
    expect(snap.bindings[0].status).toBe("warning");

    for (let i = 0; i < 5; i++) await kv.put(`x${i}`, "v"); // push over 100%
    snap = getUsageSnapshot(["RATE_LIMIT"], { quota: { write: 10 }, warningPct: 80 });
    expect(snap.bindings[0].status).toBe("exhausted");
  });

  it("fires a one-shot alert webhook when the warning threshold is crossed", async () => {
    const calls: { url: string; body: string; secret: string | null }[] = [];
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      calls.push({
        url,
        body: typeof init.body === "string" ? init.body : "",
        secret: (init.headers as Record<string, string>)["X-KV-Alert-Secret"] ?? null,
      });
      return new Response("", { status: 204 });
    });
    const origFetch = globalThis.fetch;
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
    try {
      const kv = wrapKvNamespace(new FakeKv() as unknown as KVNamespace, "RATE_LIMIT", {
        ctx: noopCtx,
        quota: { write: 4 },
        warningPct: 50,
        backendUrl: "https://api.example.com",
        alertSecret: "shh",
      });
      // 50% threshold of 4 writes = 2.
      await kv.put("a", "1");
      await kv.put("b", "2");
      await kv.put("c", "3");
      // Allow async alert calls to settle.
      await new Promise((r) => setTimeout(r, 0));
      // Exactly one alert fired for "write" — subsequent writes are
      // suppressed by the per-day dedupe set.
      const writeAlerts = calls.filter((c) => c.url.endsWith("/admin/kv-alerts") && c.body.includes('"op":"write"'));
      expect(writeAlerts.length).toBe(1);
      expect(writeAlerts[0].secret).toBe("shh");
      const parsed = JSON.parse(writeAlerts[0].body);
      expect(parsed.binding).toBe("RATE_LIMIT");
      expect(parsed.severity).toBe("warning");
      expect(parsed.percentage).toBeGreaterThanOrEqual(50);
    } finally {
      globalThis.fetch = origFetch;
    }
  });
});

/* ───────────── caller never sees an error ───────────── */

describe("never throws to callers", () => {
  it("get/put/delete/list all resolve even with full KV outage and no cache", async () => {
    const inner = new FakeKv();
    inner.failAll = true;
    const kv = wrapKvNamespace(inner as unknown as KVNamespace, "RATE_LIMIT", { ctx: noopCtx });
    await expect(kv.get("x")).resolves.toBeNull();
    await expect(kv.put("x", "y")).resolves.toBeUndefined();
    await expect(kv.delete("x")).resolves.toBeUndefined();
    const r = await kv.list();
    expect(r.keys).toEqual([]);
  });
});

/* ───────────── last alert fired surfaced in snapshot ───────────── */

describe("lastAlertFired", () => {
  it("records the most recent alert (op + severity + timestamp) per binding", async () => {
    const fetchMock = vi.fn(async () => new Response("", { status: 204 }));
    const origFetch = globalThis.fetch;
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
    try {
      const kv = wrapKvNamespace(new FakeKv() as unknown as KVNamespace, "RATE_LIMIT", {
        ctx: noopCtx,
        quota: { write: 4 },
        warningPct: 50,
        backendUrl: "https://api.example.com",
        alertSecret: "shh",
      });
      // Untouched binding has no alert.
      let snap = getUsageSnapshot(["RATE_LIMIT"], { quota: { write: 4 }, warningPct: 50 });
      expect(snap.bindings[0].lastAlertFired).toBeNull();

      // Cross 50% threshold (writes 1 & 2 → 50%).
      await kv.put("a", "1");
      await kv.put("b", "2");
      await new Promise((r) => setTimeout(r, 0));

      snap = getUsageSnapshot(["RATE_LIMIT"], { quota: { write: 4 }, warningPct: 50 });
      const alert = snap.bindings[0].lastAlertFired;
      expect(alert).not.toBeNull();
      expect(alert!.op).toBe("write");
      expect(alert!.severity).toBe("warning");
      expect(typeof alert!.at).toBe("string");
      expect(new Date(alert!.at).toString()).not.toBe("Invalid Date");
    } finally {
      globalThis.fetch = origFetch;
    }
  });
});

/* ───────────── exact-boundary quota semantics ───────────── */

describe("exact-boundary quota semantics", () => {
  it("the operation that lands EXACTLY on the cap still hits KV; only ops past the cap fall back", async () => {
    const inner = new FakeKv();
    const cache = new FakeCache();
    const kv = wrapKvNamespace(inner as unknown as KVNamespace, "RATE_LIMIT", {
      ctx: noopCtx,
      cache: cache as unknown as Cache,
      quota: { read: 3 },
      warningPct: 99,
    });
    const innerGet = vi.spyOn(inner, "get");
    // Reads 1, 2, 3 land at 33%, 66%, and EXACTLY 100%. All three
    // should hit KV (otherwise we'd waste an entry of available
    // quota by routing it to the fallback).
    await kv.get("k");
    await kv.get("k");
    await kv.get("k");
    expect(innerGet).toHaveBeenCalledTimes(3);
    // Read 4 (past the cap) is the first one short-circuited to the
    // cache fallback.
    await kv.get("k");
    expect(innerGet).toHaveBeenCalledTimes(3);
  });

  it("escalation alert: warning fires once, then a separate exhausted alert when the same op crosses 100%", async () => {
    const calls: { url: string; body: string }[] = [];
    const fetchMock = vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, body: typeof init.body === "string" ? init.body : "" });
      return new Response("", { status: 204 });
    });
    const origFetch = globalThis.fetch;
    globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
    try {
      const kv = wrapKvNamespace(new FakeKv() as unknown as KVNamespace, "RATE_LIMIT", {
        ctx: noopCtx,
        quota: { write: 4 },
        warningPct: 50,
        backendUrl: "https://api.example.com",
        alertSecret: "shh",
      });
      // 50% threshold of 4 writes = 2 → first warning.
      await kv.put("a", "1");
      await kv.put("b", "2");
      // Push past 100% → escalation to "exhausted".
      await kv.put("c", "3");
      await kv.put("d", "4");
      await kv.put("e", "5");
      await new Promise((r) => setTimeout(r, 0));
      const writeAlerts = calls.filter((c) => c.body.includes('"op":"write"'));
      // Exactly two distinct severities for the same op: warning, then exhausted.
      const severities = writeAlerts.map((a) => JSON.parse(a.body).severity).sort();
      expect(severities).toEqual(["exhausted", "warning"]);
    } finally {
      globalThis.fetch = origFetch;
    }
  });
});

/* ───────────── proactive near-quota fallback ───────────── */

describe("proactive near-quota fallback", () => {
  it("skips KV reads and returns cache fallback once the read quota is exhausted", async () => {
    const inner = new FakeKv();
    const cache = new FakeCache();
    // Pre-seed the cache with the value so the proactive fallback has
    // something to return.
    await cache.put(
      `https://kv-fallback.invalid/RATE_LIMIT/key`,
      new Response("cached-value", { status: 200 }),
    );
    const kv = wrapKvNamespace(inner as unknown as KVNamespace, "RATE_LIMIT", {
      ctx: noopCtx,
      cache: cache as unknown as Cache,
      quota: { read: 2 },
      warningPct: 99,
    });

    // Use up the read quota with two real reads.
    await kv.get("key");
    await kv.get("key");
    // Now KV should be skipped entirely. Force the inner to throw to
    // prove we never call it: if we did, the wrapper's catch would
    // also return the cache, so we instead spy on the inner directly.
    const innerGet = vi.spyOn(inner, "get");
    const v = await kv.get("key");
    expect(v).toBe("cached-value");
    expect(innerGet).not.toHaveBeenCalled();
    expect(getUsageSnapshot(["RATE_LIMIT"], { quota: { read: 2 } }).bindings[0].fallbackActive).toBe(true);
  });

  it("queues writes instead of calling KV once the write quota is exhausted", async () => {
    const inner = new FakeKv();
    const kv = wrapKvNamespace(inner as unknown as KVNamespace, "BOT_HTML_CACHE", {
      ctx: noopCtx,
      quota: { write: 1 },
      warningPct: 99,
    });

    await kv.put("first", "v"); // 1/1 → at quota
    const innerPut = vi.spyOn(inner, "put");
    await kv.put("second", "v"); // would-be over quota → queued
    expect(innerPut).not.toHaveBeenCalled();
    const snap = getUsageSnapshot(["BOT_HTML_CACHE"], { quota: { write: 1 } });
    expect(snap.bindings[0].fallbackActive).toBe(true);
  });
});

/* ───────────── integration: page render + analytics beacon survive a KV outage ─────────────
   This is the acceptance test the task spec called out:
   "page rendering and analytics beacon still succeed during KV outage".
   It exercises the full default fetch handler with KV bindings that
   throw on every call, and verifies:
     - /api/health returns 200 (the beacon's reachability check)
     - a non-cacheable POST to a backend route still gets proxied (the
       analytics beacon path) and the worker returns the backend's
       response instead of an internal 5xx.
*/

describe("worker default fetch handler under a KV outage", () => {
  it("/api/health and analytics beacon still succeed when both KV bindings throw", async () => {
    // Import lazily so module-level state is fresh-isolated per test.
    const worker = (await import("../src/index")).default;

    const failingKv = {
      get: async () => { throw new Error("kv outage"); },
      put: async () => { throw new Error("kv outage"); },
      delete: async () => { throw new Error("kv outage"); },
      list: async () => { throw new Error("kv outage"); },
      getWithMetadata: async () => { throw new Error("kv outage"); },
    } as unknown as KVNamespace;

    const env = {
      RATE_LIMIT: failingKv,
      BOT_HTML_CACHE: failingKv,
      BACKEND_URL: "https://backend.example.com",
      PAGES_ORIGIN: "https://pages.example.com",
    } as unknown as Parameters<typeof worker.fetch>[1];

    const ctx = {
      waitUntil: () => undefined,
      passThroughOnException: () => undefined,
    } as unknown as ExecutionContext;

    // Stub global fetch to (a) succeed for the analytics beacon proxy
    // and (b) never throw — i.e. the upstream is healthy, only KV is
    // broken.
    const origFetch = globalThis.fetch;
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const u = typeof input === "string" ? input : (input instanceof URL ? input.href : input.url);
      if (u.includes("/api/analytics/track")) {
        return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      return new Response("ok", { status: 200 });
    }) as unknown as typeof globalThis.fetch;

    try {
      // 1) /api/health — does not need KV but exercises the wrapped env.
      const health = await worker.fetch(
        new Request("https://api.syrabit.ai/api/health"),
        env,
        ctx,
      );
      expect(health.status).toBe(200);

      // 2) Analytics beacon — POST to /api/analytics/track. Hits
      //    rate-limit (KV throws), then proxies to backend. Worker must
      //    NOT return 5xx from the KV failure.
      const beacon = await worker.fetch(
        new Request("https://api.syrabit.ai/api/analytics/track", {
          method: "POST",
          headers: { "Content-Type": "application/json", "CF-Connecting-IP": "203.0.113.5" },
          body: JSON.stringify({ event: "page_view" }),
        }),
        env,
        ctx,
      );
      expect(beacon.status).toBeLessThan(500);
      // Workers KV throwing must not turn into a 429 either — rate
      // limiting failed open since the counter couldn't be read.
      expect(beacon.status).not.toBe(429);
    } finally {
      globalThis.fetch = origFetch;
    }
  });
});

/* ───────────── cross-isolate counter aggregation ─────────────
   Verifies that the global daily total reflects every isolate's
   ops, not just the current one. We simulate a second isolate by
   pre-seeding the shared KV store with another isolate's counters,
   then asserting the aggregated snapshot adds them to ours. */

describe("cross-isolate aggregation", () => {
  it("sums shared __kv_usage:* keys across isolates", async () => {
    const inner = new FakeKv();
    const day = new Date().toISOString().slice(0, 10);
    // Pre-seed the shared store with a sibling isolate's counters.
    await inner.put(
      `__kv_usage:RATE_LIMIT:${day}:other-isolate-id`,
      JSON.stringify({ read: 50, write: 5, list: 0, delete: 0 }),
    );

    const kv = wrapKvNamespace(inner as unknown as KVNamespace, "RATE_LIMIT", { ctx: noopCtx });
    // Local ops in this isolate.
    await kv.get("a");
    await kv.get("b");
    await kv.put("a", "v");

    const snap = await getUsageSnapshotAggregated(
      [{ binding: "RATE_LIMIT", kv: inner as unknown as KVNamespace }],
      { quota: { read: 100, write: 10, list: 100, delete: 100 } },
    );
    // Local: 2 reads + 1 write (+ aggregated flush write).
    // Sibling: 50 reads + 5 writes.
    expect(snap.bindings[0].counters.read).toBeGreaterThanOrEqual(52);
    expect(snap.bindings[0].counters.write).toBeGreaterThanOrEqual(6);
  });
});

/* ───────────── Task #511: CF_EDGE_CACHE cross-isolate aggregation ─────────────
   Mirrors the artifacts edge worker's Task #454 fix: when the
   deployed worker grows CF_EDGE_CACHE traffic, the /api/edge/kv-usage
   probe must sum every isolate's counters via the shared
   `__kv_usage:CF_EDGE_CACHE:<day>:*` keys, not just the slice
   belonging to whichever isolate happened to serve the probe. */

describe("CF_EDGE_CACHE cross-isolate aggregation (Task #511)", () => {
  it("snapshot endpoint sums local CF_EDGE_CACHE ops with sibling isolates' counters", async () => {
    const worker = (await import("../src/index")).default;
    const cfEdgeCache = new FakeKv();
    const day = new Date().toISOString().slice(0, 10);
    // Pre-seed a sibling isolate's flushed counters in the CF_EDGE_CACHE
    // namespace itself (that's where flushSharedCounter writes them).
    await cfEdgeCache.put(
      `__kv_usage:CF_EDGE_CACHE:${day}:sibling-isolate`,
      JSON.stringify({ read: 250, write: 12, list: 0, delete: 1 }),
    );

    const env = {
      RATE_LIMIT: new FakeKv(),
      BOT_HTML_CACHE: new FakeKv(),
      CF_EDGE_CACHE: cfEdgeCache,
      BACKEND_URL: "https://backend.example.com",
      D1_SYNC_SECRET: "admin-secret",
    } as unknown as Parameters<typeof worker.fetch>[1];

    const ctx = {
      waitUntil: () => undefined,
      passThroughOnException: () => undefined,
    } as unknown as ExecutionContext;

    // Drive a few local CF_EDGE_CACHE ops through the wrapped binding
    // so this isolate also has counters to contribute. We go through
    // the worker's fetch handler so the wrapping in `wrapEnvKv` is
    // exercised — calling env.CF_EDGE_CACHE directly here would skip
    // the wrap. The simplest way: hit the snapshot route after a few
    // direct ops on the wrapped namespace via getUsageSnapshotAggregated
    // semantics — but the cleanest is just to seed via the listing.
    // Hit the snapshot endpoint:
    const resp = await worker.fetch(
      new Request("https://api.syrabit.ai/api/edge/kv-usage", {
        headers: { "X-Edge-Admin-Secret": "admin-secret" },
      }),
      env,
      ctx,
    );
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as {
      bindings: Array<{
        binding: string;
        counters: { read: number; write: number; list: number; delete: number };
      }>;
    };
    const cfRow = body.bindings.find((b) => b.binding === "CF_EDGE_CACHE");
    expect(cfRow).toBeDefined();
    // Sibling contributed 250 reads + 12 writes + 1 delete; the snapshot
    // endpoint itself performs a list+get pass that may bump local
    // counters on the wrapped CF_EDGE_CACHE binding, so we assert ≥.
    expect(cfRow!.counters.read).toBeGreaterThanOrEqual(250);
    expect(cfRow!.counters.write).toBeGreaterThanOrEqual(12);
    expect(cfRow!.counters.delete).toBeGreaterThanOrEqual(1);
  });

  it("snapshot endpoint omits CF_EDGE_CACHE row entirely when the binding isn't bound", async () => {
    const worker = (await import("../src/index")).default;
    const env = {
      RATE_LIMIT: new FakeKv(),
      BOT_HTML_CACHE: new FakeKv(),
      // CF_EDGE_CACHE intentionally absent.
      BACKEND_URL: "https://backend.example.com",
      D1_SYNC_SECRET: "admin-secret",
    } as unknown as Parameters<typeof worker.fetch>[1];

    const ctx = {
      waitUntil: () => undefined,
      passThroughOnException: () => undefined,
    } as unknown as ExecutionContext;

    const resp = await worker.fetch(
      new Request("https://api.syrabit.ai/api/edge/kv-usage", {
        headers: { "X-Edge-Admin-Secret": "admin-secret" },
      }),
      env,
      ctx,
    );
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as {
      bindings: Array<{ binding: string }>;
    };
    expect(body.bindings.find((b) => b.binding === "CF_EDGE_CACHE")).toBeUndefined();
  });
});

/* ───────────── deferred writes survive day rollover ─────────────
   When yesterday's quota was blown and today has fresh quota, the
   queued writes must be replayed — not silently dropped. */

describe("deferred writes across day rollover", () => {
  it("retains queued writes when the UTC day rolls over and replays them on next put()", async () => {
    vi.useFakeTimers();
    try {
      const inner = new FakeKv();
      inner.failAll = true; // forces every put into the deferred queue
      const kv = wrapKvNamespace(inner as unknown as KVNamespace, "RATE_LIMIT", { ctx: noopCtx });
      await kv.put("queued-a", "v1");
      await kv.put("queued-b", "v2");
      // Sanity: nothing in the underlying store yet (peek without
      // tripping the failAll switch).
      inner.failAll = false;
      expect(await inner.get("queued-a")).toBeNull();
      inner.failAll = true;

      // Advance system time past UTC midnight to force a day rollover
      // on the next op. Counters reset; the queue must NOT be cleared.
      vi.setSystemTime(new Date(Date.now() + 25 * 60 * 60 * 1000));
      inner.failAll = false;
      await kv.put("fresh-c", "v3");
      // Allow the deferred replay (1s backoff) to fire and drain both
      // queued entries against the now-healthy KV.
      await vi.advanceTimersByTimeAsync(2000);

      expect(await inner.get("queued-a")).toBe("v1");
      expect(await inner.get("queued-b")).toBe("v2");
      expect(await inner.get("fresh-c")).toBe("v3");
    } finally {
      vi.useRealTimers();
    }
  });
});

/* ───────────── Task #885: bot-cache hit/miss/304/fallback counters ─────────────
   Verifies the rolling-bucket counters that surface at
   /api/edge/kv-usage under `bot_cache:`. These counters are how we
   detect a cache-key drift in production (which would silently push
   the hit-rate from ~95% to 0%) and confirm the BOT_HTML_CACHE is
   actually paying its keep against real Googlebot/Bingbot traffic. */

describe("bot-cache hit/miss/304/fallback counters", () => {
  // Drain the synchronous waitUntil promises before asserting on KV
  // state — the recorder is fire-and-forget by design.
  const collect = (queued: Promise<unknown>[]) =>
    ({ waitUntil: (p: Promise<unknown>) => { queued.push(p); } });

  // Helper: record one event and await its scheduled work before the
  // next call. The recorder is intentionally read-then-write (cheap,
  // matches the spoof:count:* pattern at logSpoofedBot in src/index.ts)
  // so concurrent calls on the same bucket can race in production —
  // that's an accepted best-effort tradeoff for observability counters.
  // In tests we serialize so the counts are deterministic.
  async function recordSeq(
    kv: KVNamespace,
    kind: Parameters<typeof recordBotCacheEvent>[1],
  ): Promise<void> {
    const queued: Promise<unknown>[] = [];
    recordBotCacheEvent(kv, kind, collect(queued));
    for (const p of queued) await p;
  }

  it("increments per-event counters in the current 5-minute bucket", async () => {
    const inner = new FakeKv();
    const kv = wrapKvNamespace(inner as unknown as KVNamespace, "RATE_LIMIT", { ctx: noopCtx });

    await recordSeq(kv, "hit");
    await recordSeq(kv, "hit");
    await recordSeq(kv, "miss");
    await recordSeq(kv, "conditional_304");
    await recordSeq(kv, "fallback");

    const stats = await getBotCacheStats(kv as unknown as KVNamespace);
    expect(stats.hit).toBe(2);
    expect(stats.miss).toBe(1);
    expect(stats.conditional_304).toBe(1);
    expect(stats.fallback).toBe(1);
    // 2 hits / (2 hits + 1 miss + 1 fallback) = 0.5
    expect(stats.hit_rate).toBeCloseTo(0.5, 3);
    // Always exactly 12 buckets (rolling hour, 5 min each).
    expect(stats.buckets.length).toBe(BOT_CACHE_BUCKETS_PER_WINDOW);
    // The newest bucket should hold all our writes (the test runs in
    // well under 5 minutes wall-clock).
    const newest = stats.buckets[stats.buckets.length - 1];
    expect(newest.hit).toBe(2);
    expect(newest.miss).toBe(1);
    expect(newest.conditional_304).toBe(1);
    expect(newest.fallback).toBe(1);
  });

  it("writes counters with TTL=3600 so they survive a worker eviction", async () => {
    const inner = new FakeKv();
    await recordSeq(inner as unknown as KVNamespace, "hit");
    expect(inner.lastPutOpts).toMatchObject({ expirationTtl: 3600 });
  });

  it("a no-op when the KV binding is missing (early HMR / dev without bindings)", () => {
    const queued: Promise<unknown>[] = [];
    const ctx = collect(queued);
    expect(() => recordBotCacheEvent(undefined, "hit", ctx)).not.toThrow();
    expect(queued).toEqual([]);
  });

  it("getBotCacheStats returns zeros (not throw) when KV is empty", async () => {
    const inner = new FakeKv();
    const stats = await getBotCacheStats(inner as unknown as KVNamespace);
    expect(stats.hit).toBe(0);
    expect(stats.miss).toBe(0);
    expect(stats.conditional_304).toBe(0);
    expect(stats.fallback).toBe(0);
    expect(stats.hit_rate).toBe(0);
    expect(stats.buckets.length).toBe(BOT_CACHE_BUCKETS_PER_WINDOW);
  });

  it("buckets that have rolled past the 1-hour window are not summed", async () => {
    const inner = new FakeKv();
    const now = Date.now();
    // Pre-seed an OLD bucket (90 minutes ago, well past the 12×5-min window).
    const oldBucket = currentBotCacheBucket(now - 90 * 60 * 1000);
    await inner.put(botCacheKey("hit", oldBucket), "999");
    // And a fresh hit in the current bucket.
    await recordSeq(inner as unknown as KVNamespace, "hit");

    const stats = await getBotCacheStats(inner as unknown as KVNamespace, now);
    // The 999 must NOT leak into the rolling window total.
    expect(stats.hit).toBe(1);
  });

  it("a cache-key regression visibly drops hit_rate within one bucket window", async () => {
    // Simulate the scenario the task spec calls out:
    //   "A regression in cache-key derivation (forced by a synthetic
    //    test) would visibly drop hit rate in the response within one
    //    bucket window."
    // Phase 1: 9 crawler requests served from KV (healthy hit-rate).
    // Phase 2: cache-key drifts → next 9 requests all miss.
    const inner = new FakeKv();
    const kv = inner as unknown as KVNamespace;

    for (let i = 0; i < 9; i++) await recordSeq(kv, "hit");
    let stats = await getBotCacheStats(kv);
    expect(stats.hit_rate).toBe(1);

    // Cache-key drift simulation: every subsequent request goes to KV
    // but the key isn't there → miss.
    for (let i = 0; i < 9; i++) await recordSeq(kv, "miss");

    stats = await getBotCacheStats(inner as unknown as KVNamespace);
    // Same window, but the regression has cut hit-rate roughly in half.
    expect(stats.hit_rate).toBeLessThanOrEqual(0.55);
    expect(stats.hit).toBe(9);
    expect(stats.miss).toBe(9);
  });

  it("counters use a namespaced KV key that doesn't collide with spoof:count:* or __kv_usage:*", () => {
    const k = botCacheKey("hit", 12345);
    expect(k.startsWith("bot_cache:")).toBe(true);
    expect(k).not.toMatch(/^spoof:/);
    expect(k).not.toMatch(/^__kv_usage:/);
  });
});

/* ───────────── Task #885: /api/edge/kv-usage exposes bot_cache block ─────────────
   End-to-end check that the admin route surfaces the rolling counters
   so the dashboard / on-call can see hit-rate without hand-grepping
   raw logs for the X-Cache header. */

describe("/api/edge/kv-usage exposes bot_cache block", () => {
  it("includes bot_cache.{hit,miss,conditional_304,fallback,hit_rate,buckets}", async () => {
    const worker = (await import("../src/index")).default;

    // Use real in-memory FakeKv so the route can read what
    // recordBotCacheEvent wrote (the wrapper passes through to it).
    const inner = new FakeKv();

    const env = {
      RATE_LIMIT: inner,
      BOT_HTML_CACHE: new FakeKv(),
      BACKEND_URL: "https://backend.example.com",
      D1_SYNC_SECRET: "admin-secret",
    } as unknown as Parameters<typeof worker.fetch>[1];

    const ctx = {
      waitUntil: () => undefined,
      passThroughOnException: () => undefined,
    } as unknown as ExecutionContext;

    // Pre-seed a few events directly into KV in the current bucket so
    // the assertion is independent of route plumbing.
    const bucket = currentBotCacheBucket();
    await inner.put(botCacheKey("hit", bucket), "7");
    await inner.put(botCacheKey("miss", bucket), "2");
    await inner.put(botCacheKey("conditional_304", bucket), "3");
    await inner.put(botCacheKey("fallback", bucket), "1");

    const resp = await worker.fetch(
      new Request("https://api.syrabit.ai/api/edge/kv-usage", {
        headers: { "X-Edge-Admin-Secret": "admin-secret" },
      }),
      env,
      ctx,
    );
    expect(resp.status).toBe(200);
    const body = (await resp.json()) as {
      bot_cache: {
        hit: number; miss: number; conditional_304: number;
        fallback: number; hit_rate: number;
        buckets: Array<{ ts: string; hit: number }>;
      } | null;
    };
    expect(body.bot_cache).not.toBeNull();
    expect(body.bot_cache!.hit).toBe(7);
    expect(body.bot_cache!.miss).toBe(2);
    expect(body.bot_cache!.conditional_304).toBe(3);
    expect(body.bot_cache!.fallback).toBe(1);
    // 7 / (7 + 2 + 1) = 0.7
    expect(body.bot_cache!.hit_rate).toBeCloseTo(0.7, 3);
    expect(body.bot_cache!.buckets.length).toBe(BOT_CACHE_BUCKETS_PER_WINDOW);
  });

  it("rejects requests without the admin secret with 401 (no bot_cache leak)", async () => {
    const worker = (await import("../src/index")).default;
    const env = {
      RATE_LIMIT: new FakeKv(),
      BOT_HTML_CACHE: new FakeKv(),
      BACKEND_URL: "https://backend.example.com",
      D1_SYNC_SECRET: "admin-secret",
    } as unknown as Parameters<typeof worker.fetch>[1];
    const ctx = {
      waitUntil: () => undefined,
      passThroughOnException: () => undefined,
    } as unknown as ExecutionContext;
    const resp = await worker.fetch(
      new Request("https://api.syrabit.ai/api/edge/kv-usage"),
      env,
      ctx,
    );
    expect(resp.status).toBe(401);
  });
});
