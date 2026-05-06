/**
 * Task #455 — End-to-end coverage for the Task #424 counter-bumping +
 * threshold-cross alert dispatching wired into the artifacts edge
 * worker (artifacts/syrabit/workers/edge-proxy/src/index.ts).
 *
 * Scope:
 *   - dispatchKvCache GET/PUT/DELETE each bump CF_EDGE_CACHE counter
 *     exactly once per successful op.
 *   - A failed PUT/DELETE (KV throws) does NOT bump the counter.
 *   - dispatchKvUsage requires X-Edge-Admin-Secret = D1_SYNC_SECRET and
 *     returns the CF_EDGE_CACHE row in the same shape the kv-monitor
 *     uses (binding/utcDay/counters/quota/percentages/status/...).
 *   - Crossing warning + exhausted thresholds POSTs once each to
 *     `${AZURE_BACKEND_URL}/admin/kv-alerts` with the expected body.
 *   - Alerts deduplicate within the same UTC day (and the dedupe is
 *     keyed per (op, severity), so warning -> exhausted both fire but
 *     never a second warning).
 *
 * Reference test style: workers/edge-proxy/tests/kv-monitor.test.ts.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// The artifacts edge worker — module-scoped counters live inside this
// module, so we reset them in beforeEach via the exported test hook.
import worker, {
  _resetKvCountersForTests,
} from "../../../artifacts/syrabit/workers/edge-proxy/src/index";

/* ───────────── fakes ───────────── */

interface KvStoredEntry {
  value: string;
  metadata: unknown;
  expirationTtl?: number;
}

class FakeCfEdgeCache {
  store = new Map<string, KvStoredEntry>();
  failPut = false;
  failDelete = false;
  failGet = false;
  putCalls = 0;
  deleteCalls = 0;
  getCalls = 0;

  async getWithMetadata(
    key: string,
    _opts?: unknown,
  ): Promise<{ value: unknown; metadata: unknown }> {
    this.getCalls += 1;
    if (this.failGet) throw new Error("kv get failure");
    const entry = this.store.get(key);
    if (!entry) return { value: null, metadata: null };
    let parsed: unknown = entry.value;
    try {
      parsed = JSON.parse(entry.value);
    } catch {
      // leave as raw string
    }
    return { value: parsed, metadata: entry.metadata };
  }

  async get(key: string): Promise<string | null> {
    if (this.failGet) throw new Error("kv get failure");
    return this.store.get(key)?.value ?? null;
  }

  async put(
    key: string,
    value: string,
    opts?: { expirationTtl?: number; metadata?: unknown },
  ): Promise<void> {
    this.putCalls += 1;
    if (this.failPut) throw new Error("kv put failure");
    this.store.set(key, {
      value,
      metadata: opts?.metadata ?? null,
      expirationTtl: opts?.expirationTtl,
    });
  }

  async delete(key: string): Promise<void> {
    this.deleteCalls += 1;
    if (this.failDelete) throw new Error("kv delete failure");
    this.store.delete(key);
  }

  async list(): Promise<{ keys: { name: string }[]; list_complete: boolean }> {
    return {
      keys: Array.from(this.store.keys()).map((name) => ({ name })),
      list_complete: true,
    };
  }
}

interface RecordedAlert {
  url: string;
  body: { binding: string; op: string; used: number; quota: number; percentage: number; severity: string; utc_day: string };
  headers: Record<string, string>;
}

function makeCtx(): { ctx: ExecutionContext; settled: () => Promise<void> } {
  const pending: Promise<unknown>[] = [];
  const ctx = {
    waitUntil(p: Promise<unknown>) {
      pending.push(p);
    },
    passThroughOnException() {},
  } as unknown as ExecutionContext;
  return {
    ctx,
    settled: async () => {
      // Drain the queue — alerts may schedule additional waits, so
      // loop until the queue is stable.
      while (pending.length) {
        const batch = pending.splice(0, pending.length);
        await Promise.allSettled(batch);
      }
    },
  };
}

interface TestEnv {
  CF_EDGE_CACHE: FakeCfEdgeCache;
  D1_SYNC_SECRET: string;
  AZURE_BACKEND_URL: string;
  KV_ALERT_SECRET: string;
  DISPATCH_SHARED_SECRET: string;
  KV_QUOTA?: string;
  KV_WARNING_PCT?: string;
  ORIGIN_TARGET?: string;
}

const D1_SECRET = "edge-admin-secret-xyz";
const ALERT_SECRET = "kv-alert-secret-abc";
const BACKEND_URL = "https://backend.example.com";

function makeEnv(overrides: Partial<TestEnv> = {}): TestEnv {
  return {
    CF_EDGE_CACHE: new FakeCfEdgeCache(),
    D1_SYNC_SECRET: D1_SECRET,
    AZURE_BACKEND_URL: BACKEND_URL,
    KV_ALERT_SECRET: ALERT_SECRET,
    DISPATCH_SHARED_SECRET: "dispatch-secret",
    ORIGIN_TARGET: "azure",
    ...overrides,
  };
}

function kvCacheUrl(key: string): string {
  return `https://api.syrabit.ai/api/edge/kv-cache/${key}`;
}

const KV_USAGE_URL = "https://api.syrabit.ai/api/edge/kv-usage";

/* ───────────── lifecycle ───────────── */

let origFetch: typeof globalThis.fetch;
let alerts: RecordedAlert[];
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  _resetKvCountersForTests();
  alerts = [];
  origFetch = globalThis.fetch;
  fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    if (url.endsWith("/admin/kv-alerts")) {
      const body = typeof init?.body === "string" ? init.body : "";
      const hdrs = init?.headers as Record<string, string> | undefined;
      alerts.push({
        url,
        body: JSON.parse(body),
        headers: hdrs ?? {},
      });
      return new Response("", { status: 204 });
    }
    // Anything else (e.g. backend proxy) — return a 200 so the worker
    // doesn't error on unhandled paths.
    return new Response("ok", { status: 200 });
  });
  globalThis.fetch = fetchMock as unknown as typeof globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = origFetch;
});

/* ───────────── counter bumping ───────────── */

describe("CF_EDGE_CACHE counter bumping (Task #424)", () => {
  it("GET (hit), PUT, and DELETE each bump the matching counter exactly once", async () => {
    const env = makeEnv();
    const { ctx, settled } = makeCtx();

    // PUT a value first so the GET below is a hit (the worker still
    // bumps `read` on a miss; we exercise both paths for clarity).
    let resp = await worker.fetch(
      new Request(kvCacheUrl("alpha"), {
        method: "PUT",
        headers: {
          "X-Edge-Admin-Secret": D1_SECRET,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ value: { hello: "world" }, ttl_s: 120 }),
      }),
      env as unknown as Parameters<typeof worker.fetch>[1],
      ctx,
    );
    expect(resp.status).toBe(200);

    resp = await worker.fetch(
      new Request(kvCacheUrl("alpha"), {
        method: "GET",
        headers: { "X-Edge-Admin-Secret": D1_SECRET },
      }),
      env as unknown as Parameters<typeof worker.fetch>[1],
      ctx,
    );
    expect(resp.status).toBe(200);

    resp = await worker.fetch(
      new Request(kvCacheUrl("alpha"), {
        method: "DELETE",
        headers: { "X-Edge-Admin-Secret": D1_SECRET },
      }),
      env as unknown as Parameters<typeof worker.fetch>[1],
      ctx,
    );
    expect(resp.status).toBe(200);

    await settled();

    // Snapshot via the public usage endpoint to confirm the counters.
    const snapResp = await worker.fetch(
      new Request(KV_USAGE_URL, {
        method: "GET",
        headers: { "X-Edge-Admin-Secret": D1_SECRET },
      }),
      env as unknown as Parameters<typeof worker.fetch>[1],
      ctx,
    );
    expect(snapResp.status).toBe(200);
    const snap = (await snapResp.json()) as {
      bindings: Array<{ binding: string; counters: Record<string, number> }>;
    };
    const row = snap.bindings.find((b) => b.binding === "CF_EDGE_CACHE");
    expect(row).toBeDefined();
    expect(row!.counters.read).toBe(1);
    expect(row!.counters.write).toBe(1);
    expect(row!.counters.delete).toBe(1);
    expect(row!.counters.list).toBe(0);
  });

  it("GET miss still bumps the read counter exactly once", async () => {
    const env = makeEnv();
    const { ctx, settled } = makeCtx();

    const resp = await worker.fetch(
      new Request(kvCacheUrl("ghost"), {
        method: "GET",
        headers: { "X-Edge-Admin-Secret": D1_SECRET },
      }),
      env as unknown as Parameters<typeof worker.fetch>[1],
      ctx,
    );
    expect(resp.status).toBe(404);
    await settled();

    const snap = await readSnapshot(env, ctx);
    expect(snap.counters.read).toBe(1);
    expect(snap.counters.write).toBe(0);
    expect(snap.counters.delete).toBe(0);
  });
});

/* ───────────── failed ops do NOT bump ───────────── */

describe("failed CF_EDGE_CACHE ops do not bump counters", () => {
  it("a PUT that fails inside KV.put returns 502 and leaves write at 0", async () => {
    const env = makeEnv();
    env.CF_EDGE_CACHE.failPut = true;
    const { ctx, settled } = makeCtx();

    const resp = await worker.fetch(
      new Request(kvCacheUrl("beta"), {
        method: "PUT",
        headers: {
          "X-Edge-Admin-Secret": D1_SECRET,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ value: 42, ttl_s: 120 }),
      }),
      env as unknown as Parameters<typeof worker.fetch>[1],
      ctx,
    );
    expect(resp.status).toBe(502);
    expect(env.CF_EDGE_CACHE.putCalls).toBe(1);
    await settled();

    const snap = await readSnapshot(env, ctx);
    expect(snap.counters.write).toBe(0);
    // No alert dispatched either, since the counter never moved.
    expect(alerts.filter((a) => a.body.binding === "CF_EDGE_CACHE")).toEqual([]);
  });

  it("a DELETE that fails inside KV.delete returns 502 and leaves delete at 0", async () => {
    const env = makeEnv();
    env.CF_EDGE_CACHE.failDelete = true;
    const { ctx, settled } = makeCtx();

    const resp = await worker.fetch(
      new Request(kvCacheUrl("gamma"), {
        method: "DELETE",
        headers: { "X-Edge-Admin-Secret": D1_SECRET },
      }),
      env as unknown as Parameters<typeof worker.fetch>[1],
      ctx,
    );
    expect(resp.status).toBe(502);
    expect(env.CF_EDGE_CACHE.deleteCalls).toBe(1);
    await settled();

    const snap = await readSnapshot(env, ctx);
    expect(snap.counters.delete).toBe(0);
  });
});

/* ───────────── /api/edge/kv-usage handshake + shape ───────────── */

describe("dispatchKvUsage admin handshake", () => {
  it("rejects requests without X-Edge-Admin-Secret = D1_SYNC_SECRET", async () => {
    const env = makeEnv();
    const { ctx } = makeCtx();

    // No header.
    let resp = await worker.fetch(
      new Request(KV_USAGE_URL, { method: "GET" }),
      env as unknown as Parameters<typeof worker.fetch>[1],
      ctx,
    );
    expect(resp.status).toBe(401);

    // Wrong header.
    resp = await worker.fetch(
      new Request(KV_USAGE_URL, {
        method: "GET",
        headers: { "X-Edge-Admin-Secret": "not-the-secret" },
      }),
      env as unknown as Parameters<typeof worker.fetch>[1],
      ctx,
    );
    expect(resp.status).toBe(401);

    // Correct header → 200 + JSON shape.
    resp = await worker.fetch(
      new Request(KV_USAGE_URL, {
        method: "GET",
        headers: { "X-Edge-Admin-Secret": D1_SECRET },
      }),
      env as unknown as Parameters<typeof worker.fetch>[1],
      ctx,
    );
    expect(resp.status).toBe(200);
    const snap = (await resp.json()) as {
      utcDay: string;
      warningPct: number;
      bindings: Array<{
        binding: string;
        utcDay: string;
        counters: Record<string, number>;
        quota: Record<string, number>;
        percentages: Record<string, number>;
        status: string;
        fallbackActive: boolean;
      }>;
    };
    expect(typeof snap.utcDay).toBe("string");
    expect(snap.utcDay).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(typeof snap.warningPct).toBe("number");
    const row = snap.bindings.find((b) => b.binding === "CF_EDGE_CACHE");
    expect(row).toBeDefined();
    // Same shape kv-monitor uses (counters/quota/percentages/status/fallbackActive).
    expect(Object.keys(row!.counters).sort()).toEqual([
      "delete",
      "list",
      "read",
      "write",
    ]);
    expect(Object.keys(row!.quota).sort()).toEqual([
      "delete",
      "list",
      "read",
      "write",
    ]);
    expect(Object.keys(row!.percentages).sort()).toEqual([
      "delete",
      "list",
      "read",
      "write",
    ]);
    expect(["healthy", "warning", "exhausted"]).toContain(row!.status);
    expect(typeof row!.fallbackActive).toBe("boolean");
  });

  it("snapshot path itself does NOT bump CF_EDGE_CACHE counters", async () => {
    const env = makeEnv();
    const { ctx } = makeCtx();

    // Three back-to-back snapshot calls.
    for (let i = 0; i < 3; i += 1) {
      const r = await worker.fetch(
        new Request(KV_USAGE_URL, {
          method: "GET",
          headers: { "X-Edge-Admin-Secret": D1_SECRET },
        }),
        env as unknown as Parameters<typeof worker.fetch>[1],
        ctx,
      );
      expect(r.status).toBe(200);
    }

    const snap = await readSnapshot(env, ctx);
    expect(snap.counters.read).toBe(0);
    expect(snap.counters.write).toBe(0);
    expect(snap.counters.delete).toBe(0);
    expect(snap.counters.list).toBe(0);
  });
});

/* ───────────── threshold-cross alert dispatch ───────────── */

describe("threshold-cross alert dispatch (Task #424)", () => {
  it("crossing warning then exhausted POSTs to /admin/kv-alerts once each", async () => {
    // Tight quota so we can hit warning at 1 PUT and exhausted at 2 PUTs.
    const env = makeEnv({
      KV_QUOTA: JSON.stringify({
        read: 100_000,
        write: 2,
        list: 1_000,
        delete: 1_000,
      }),
      KV_WARNING_PCT: "50",
    });
    const { ctx, settled } = makeCtx();

    const put = async (key: string) =>
      worker.fetch(
        new Request(kvCacheUrl(key), {
          method: "PUT",
          headers: {
            "X-Edge-Admin-Secret": D1_SECRET,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ value: key, ttl_s: 120 }),
        }),
        env as unknown as Parameters<typeof worker.fetch>[1],
        ctx,
      );

    let r = await put("a"); // 1/2 = 50% → warning
    expect(r.status).toBe(200);
    await settled();
    r = await put("b"); // 2/2 = 100% → exhausted
    expect(r.status).toBe(200);
    await settled();

    const writeAlerts = alerts.filter(
      (a) => a.body.binding === "CF_EDGE_CACHE" && a.body.op === "write",
    );
    expect(writeAlerts.length).toBe(2);
    const severities = writeAlerts.map((a) => a.body.severity).sort();
    expect(severities).toEqual(["exhausted", "warning"]);

    const warning = writeAlerts.find((a) => a.body.severity === "warning")!;
    expect(warning.url).toBe(`${BACKEND_URL}/admin/kv-alerts`);
    expect(warning.headers["X-KV-Alert-Secret"]).toBe(ALERT_SECRET);
    expect(warning.headers["Content-Type"]).toBe("application/json");
    expect(warning.body.binding).toBe("CF_EDGE_CACHE");
    expect(warning.body.op).toBe("write");
    expect(warning.body.quota).toBe(2);
    expect(warning.body.used).toBe(1);
    expect(warning.body.percentage).toBeGreaterThanOrEqual(50);
    expect(warning.body.percentage).toBeLessThan(100);
    expect(warning.body.utc_day).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    const exhausted = writeAlerts.find((a) => a.body.severity === "exhausted")!;
    expect(exhausted.body.used).toBe(2);
    expect(exhausted.body.percentage).toBeGreaterThanOrEqual(100);
  });

  it("alerts deduplicate within the same UTC day (warning fires once even after many writes)", async () => {
    // warningPct = 50, write quota = 10 → warning at 5/10. Subsequent
    // writes through 9/10 (still <100%) must NOT emit additional
    // warning alerts.
    const env = makeEnv({
      KV_QUOTA: JSON.stringify({
        read: 100_000,
        write: 10,
        list: 1_000,
        delete: 1_000,
      }),
      KV_WARNING_PCT: "50",
    });
    const { ctx, settled } = makeCtx();

    for (let i = 0; i < 9; i += 1) {
      const r = await worker.fetch(
        new Request(kvCacheUrl(`k${i}`), {
          method: "PUT",
          headers: {
            "X-Edge-Admin-Secret": D1_SECRET,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ value: i, ttl_s: 120 }),
        }),
        env as unknown as Parameters<typeof worker.fetch>[1],
        ctx,
      );
      expect(r.status).toBe(200);
      await settled();
    }

    const warningAlerts = alerts.filter(
      (a) =>
        a.body.binding === "CF_EDGE_CACHE" &&
        a.body.op === "write" &&
        a.body.severity === "warning",
    );
    // Exactly one warning even though we crossed it 5 times in a row
    // (5/10, 6/10, 7/10, 8/10, 9/10). The per-day dedupe set absorbs
    // the rest.
    expect(warningAlerts.length).toBe(1);
    // No exhausted alert yet — we never hit 100%.
    const exhaustedAlerts = alerts.filter(
      (a) =>
        a.body.binding === "CF_EDGE_CACHE" &&
        a.body.op === "write" &&
        a.body.severity === "exhausted",
    );
    expect(exhaustedAlerts.length).toBe(0);
  });

  it("exhausted alert deduplicates too: a third over-quota PUT does not refire", async () => {
    const env = makeEnv({
      KV_QUOTA: JSON.stringify({
        read: 100_000,
        write: 1,
        list: 1_000,
        delete: 1_000,
      }),
      KV_WARNING_PCT: "50",
    });
    const { ctx, settled } = makeCtx();

    for (let i = 0; i < 3; i += 1) {
      const r = await worker.fetch(
        new Request(kvCacheUrl(`k${i}`), {
          method: "PUT",
          headers: {
            "X-Edge-Admin-Secret": D1_SECRET,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ value: i, ttl_s: 120 }),
        }),
        env as unknown as Parameters<typeof worker.fetch>[1],
        ctx,
      );
      expect(r.status).toBe(200);
      await settled();
    }

    const writeAlerts = alerts.filter(
      (a) => a.body.binding === "CF_EDGE_CACHE" && a.body.op === "write",
    );
    // Only the (write, exhausted) tuple — write quota was 1 so the very
    // first PUT lands at 100%, jumping straight past warning into
    // exhausted. The next two PUTs are deduped.
    expect(writeAlerts.length).toBe(1);
    expect(writeAlerts[0].body.severity).toBe("exhausted");
  });
});

/* ───────────── helpers ───────────── */

async function readSnapshot(
  env: TestEnv,
  ctx: ExecutionContext,
): Promise<{
  binding: string;
  counters: Record<string, number>;
  quota: Record<string, number>;
  percentages: Record<string, number>;
  status: string;
}> {
  const resp = await worker.fetch(
    new Request(KV_USAGE_URL, {
      method: "GET",
      headers: { "X-Edge-Admin-Secret": D1_SECRET },
    }),
    env as unknown as Parameters<typeof worker.fetch>[1],
    ctx,
  );
  if (resp.status !== 200) {
    throw new Error(`snapshot fetch failed: ${resp.status}`);
  }
  const body = (await resp.json()) as {
    bindings: Array<{
      binding: string;
      counters: Record<string, number>;
      quota: Record<string, number>;
      percentages: Record<string, number>;
      status: string;
    }>;
  };
  const row = body.bindings.find((b) => b.binding === "CF_EDGE_CACHE");
  if (!row) throw new Error("CF_EDGE_CACHE row missing from snapshot");
  return row;
}
