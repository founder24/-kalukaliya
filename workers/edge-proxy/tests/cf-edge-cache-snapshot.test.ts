/**
 * Task #545 — End-to-end smoke check that confirms a write through the
 * deployed worker's `CF_EDGE_CACHE` KV binding shows up as a
 * CF_EDGE_CACHE row on the next `/api/edge/kv-usage` snapshot.
 *
 * The binding is declared in workers/edge-proxy/wrangler.toml so the
 * admin /admin/kv-health panel can render the row alongside RATE_LIMIT
 * and BOT_HTML_CACHE; the per-isolate counter aggregation was already
 * wired in Task #511 (kv-monitor.ts shared-key flush +
 * getUsageSnapshotAggregated list+sum).
 */

import { describe, it, expect, beforeEach } from "vitest";
import workerHandler from "../src/index";
import { _resetMonitorStateForTests, wrapKvNamespace } from "../src/kv-monitor";

interface KvEntry { value: string; expiresAt: number | null }

function makeKv() {
  const store = new Map<string, KvEntry>();
  return {
    store,
    async get(k: string): Promise<string | null> {
      const e = store.get(k);
      if (!e) return null;
      if (e.expiresAt !== null && Date.now() >= e.expiresAt) {
        store.delete(k);
        return null;
      }
      return e.value;
    },
    async put(k: string, v: string, opts?: { expirationTtl?: number }): Promise<void> {
      const ttl = opts?.expirationTtl;
      const expiresAt = typeof ttl === "number" && ttl > 0 ? Date.now() + ttl * 1000 : null;
      store.set(k, { value: v, expiresAt });
    },
    async delete(k: string): Promise<void> { store.delete(k); },
    async list() {
      return {
        keys: Array.from(store.keys()).map((name) => ({ name })),
        list_complete: true,
      };
    },
    async getWithMetadata(k: string) {
      return { value: store.get(k)?.value ?? null, metadata: null };
    },
  };
}

const ctxNoop = {
  waitUntil: (_p: Promise<unknown>) => undefined,
  passThroughOnException: () => undefined,
} as unknown as ExecutionContext;

function makeEnv(over: Record<string, unknown> = {}) {
  return {
    BACKEND_URL: "https://backend.test",
    PAGES_ORIGIN: "https://pages.test",
    RATE_LIMIT: makeKv(),
    BOT_HTML_CACHE: makeKv(),
    CF_EDGE_CACHE: makeKv(),
    CONTENT_DB: undefined,
    D1_SYNC_SECRET: "smoke-secret",
    JWT_SECRET: "test-jwt-secret",
    ...over,
  } as unknown as Parameters<typeof workerHandler.fetch>[1];
}

async function getSnapshot(env: ReturnType<typeof makeEnv>) {
  const req = new Request("https://api.syrabit.ai/api/edge/kv-usage", {
    method: "GET",
    headers: { "X-Edge-Admin-Secret": "smoke-secret" },
  });
  const resp = await workerHandler.fetch(req, env, ctxNoop);
  expect(resp.status).toBe(200);
  return (await resp.json()) as {
    bindings: Array<{
      binding: string;
      counters: { read: number; write: number; list: number; delete: number };
      percentages?: { read: number; write: number; list: number; delete: number };
    }>;
  };
}

describe("CF_EDGE_CACHE binding surfaces on /api/edge/kv-usage", () => {
  beforeEach(() => { _resetMonitorStateForTests(); });

  it("renders a CF_EDGE_CACHE row alongside RATE_LIMIT and BOT_HTML_CACHE", async () => {
    const env = makeEnv();
    const snap = await getSnapshot(env);
    const names = snap.bindings.map((b) => b.binding);
    expect(names).toContain("RATE_LIMIT");
    expect(names).toContain("BOT_HTML_CACHE");
    expect(names).toContain("CF_EDGE_CACHE");
  });

  it("reflects a write through the binding in the next snapshot", async () => {
    const env = makeEnv();

    const before = await getSnapshot(env);
    const beforeRow = before.bindings.find((b) => b.binding === "CF_EDGE_CACHE")!;
    const beforeWrites = beforeRow.counters.write;

    // Issue a write through the same wrapKvNamespace path that
    // wrapEnvKv uses inside the worker handler. The wrapper increments
    // module-scoped counters that getUsageSnapshotAggregated then
    // surfaces on the next /api/edge/kv-usage probe.
    const wrapped = wrapKvNamespace(
      (env as unknown as { CF_EDGE_CACHE: KVNamespace }).CF_EDGE_CACHE,
      "CF_EDGE_CACHE",
      { ctx: ctxNoop },
    );
    await wrapped.put("smoke-key", "smoke-value");

    const after = await getSnapshot(env);
    const afterRow = after.bindings.find((b) => b.binding === "CF_EDGE_CACHE")!;
    expect(afterRow).toBeDefined();
    // Strictly assert the WRITE counter moved forward — proves the
    // write-through path itself is observable on the next probe, not
    // just incidental read/list activity from the snapshot endpoint.
    expect(afterRow.counters.write).toBeGreaterThan(beforeWrites);
  });

  it("omits the row when the binding is not declared (graceful)", async () => {
    const env = makeEnv({ CF_EDGE_CACHE: undefined });
    const snap = await getSnapshot(env);
    const names = snap.bindings.map((b) => b.binding);
    expect(names).toContain("RATE_LIMIT");
    expect(names).toContain("BOT_HTML_CACHE");
    expect(names).not.toContain("CF_EDGE_CACHE");
  });
});
