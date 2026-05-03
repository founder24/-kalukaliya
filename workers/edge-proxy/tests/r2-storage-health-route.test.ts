/**
 * Task #324 — route-dispatch tests for the Task #322 admin
 * `POST /api/edge/r2-storage-health/reset-watchdog` endpoint.
 *
 * The KV-mutation logic (`resetR2StorageWatchdogBlindCounter`) is
 * already covered at the helper level in
 * `r2-storage-class-alert.test.ts`. These tests instead exercise the
 * worker fetch handler so the routing block in `src/index.ts`
 * (pathname + HTTP method + auth gate + KV-binding guard) is itself
 * protected against silent regressions if the dispatch is ever
 * reordered or refactored.
 *
 * Coverage:
 *   - 401 when the X-Edge-Admin-Secret header is missing
 *   - 401 when the header is present but does not match D1_SYNC_SECRET
 *   - 503 when the worker has no RATE_LIMIT KV binding
 *   - 200 with the canonical `{ ok: true, state }` payload on success,
 *     and the secondary watchdog fields actually zeroed in KV
 *   - GET on the same path is NOT routed here (POST-only contract)
 */
import { describe, it, expect } from "vitest";
import worker from "../src/index";
import {
  _R2_STORAGE_CLASS_ALERT_STATE_KEY,
  type R2StorageClassAlertState,
} from "../src/r2-storage-class-alert";

class FakeKv {
  store = new Map<string, string>();
  async get(key: string): Promise<string | null> {
    return this.store.has(key) ? this.store.get(key)! : null;
  }
  async put(key: string, value: string, _opts?: unknown): Promise<void> {
    this.store.set(key, value);
  }
  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }
}

const ctx = {
  waitUntil: () => undefined,
  passThroughOnException: () => undefined,
} as unknown as ExecutionContext;

function makeEnv(over: Record<string, unknown> = {}) {
  return {
    BACKEND_URL: "https://example.invalid",
    D1_SYNC_SECRET: "topsecret",
    RATE_LIMIT: new FakeKv() as unknown as KVNamespace,
    ...over,
  } as unknown as Parameters<typeof worker.fetch>[1];
}

const URL_PATH = "https://api.syrabit.ai/api/edge/r2-storage-health/reset-watchdog";

describe("POST /api/edge/r2-storage-health/reset-watchdog (Task #324)", () => {
  it("returns 401 when the admin secret header is missing", async () => {
    const env = makeEnv();
    const res = await worker.fetch(new Request(URL_PATH, { method: "POST" }), env, ctx);
    expect(res.status).toBe(401);
    // KV must not have been mutated — auth must reject before we touch
    // any state. (Read the underlying FakeKv from the env we built.)
    const kv = (env as unknown as { RATE_LIMIT: FakeKv }).RATE_LIMIT;
    expect(kv.store.has(_R2_STORAGE_CLASS_ALERT_STATE_KEY)).toBe(false);
  });

  it("returns 401 when the admin secret header does not match D1_SYNC_SECRET", async () => {
    const env = makeEnv();
    const res = await worker.fetch(
      new Request(URL_PATH, {
        method: "POST",
        headers: { "X-Edge-Admin-Secret": "wrong-secret" },
      }),
      env,
      ctx,
    );
    expect(res.status).toBe(401);
    const kv = (env as unknown as { RATE_LIMIT: FakeKv }).RATE_LIMIT;
    expect(kv.store.has(_R2_STORAGE_CLASS_ALERT_STATE_KEY)).toBe(false);
  });

  it("returns 503 with no_kv_binding when RATE_LIMIT is unbound", async () => {
    const env = makeEnv({ RATE_LIMIT: undefined });
    const res = await worker.fetch(
      new Request(URL_PATH, {
        method: "POST",
        headers: { "X-Edge-Admin-Secret": "topsecret" },
      }),
      env,
      ctx,
    );
    expect(res.status).toBe(503);
    const body = (await res.json()) as { ok: boolean; reason: string };
    expect(body.ok).toBe(false);
    expect(body.reason).toBe("no_kv_binding");
  });

  it("returns 200 with { ok: true, state } and zeros the watchdog fields on success", async () => {
    // Pre-populate KV with a tripped watchdog state so we can prove the
    // endpoint actually wrote zeros back, not just returned them.
    const kv = new FakeKv();
    await kv.put(
      _R2_STORAGE_CLASS_ALERT_STATE_KEY,
      JSON.stringify({
        last_evaluated_at: "2026-04-01T00:00:00Z",
        ia_share_last_fired_at: "2026-03-15T12:00:00Z",
        logpush_last_fired_at: null,
        last_ia_share: 0.42,
        last_total_gb: 80,
        last_logpush_gb: 1.2,
        consecutive_query_failures: 3,
        query_fail_last_fired_at: "2026-04-01T00:00:00Z",
      } satisfies R2StorageClassAlertState),
    );
    const env = makeEnv({ RATE_LIMIT: kv as unknown as KVNamespace });

    const res = await worker.fetch(
      new Request(URL_PATH, {
        method: "POST",
        headers: { "X-Edge-Admin-Secret": "topsecret" },
      }),
      env,
      ctx,
    );

    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toMatch(/application\/json/);
    expect(res.headers.get("X-Source")).toBe("edge-r2-storage-health");
    expect(res.headers.get("Cache-Control")).toBe("no-store");

    const body = (await res.json()) as {
      ok: boolean;
      state: R2StorageClassAlertState;
    };
    expect(body.ok).toBe(true);
    // Watchdog fields zeroed.
    expect(body.state.consecutive_query_failures).toBe(0);
    expect(body.state.query_fail_last_fired_at).toBeNull();
    // Cost-flavoured fields preserved so the IA / Logpush tiles stay
    // populated after the reset (not blown away by the route).
    expect(body.state.last_ia_share).toBe(0.42);
    expect(body.state.last_total_gb).toBe(80);
    expect(body.state.ia_share_last_fired_at).toBe("2026-03-15T12:00:00Z");

    // And the persisted KV value matches the response (next GET is
    // the same as what we just told the UI).
    const persisted = JSON.parse(
      kv.store.get(_R2_STORAGE_CLASS_ALERT_STATE_KEY) || "{}",
    ) as R2StorageClassAlertState;
    expect(persisted.consecutive_query_failures).toBe(0);
    expect(persisted.query_fail_last_fired_at).toBeNull();
    expect(persisted.last_ia_share).toBe(0.42);
  });

  it("does not route GET on the reset-watchdog path (POST-only contract)", async () => {
    // GET on the same path must NOT hit the reset handler. The exact
    // status the proxy fall-through returns (404 vs 502 against the
    // unreachable BACKEND_URL) isn't load-bearing — what matters is
    // that we do NOT see the reset handler's 200 success envelope or
    // its X-Source header, and that no KV mutation happened.
    const kv = new FakeKv();
    await kv.put(
      _R2_STORAGE_CLASS_ALERT_STATE_KEY,
      JSON.stringify({
        last_evaluated_at: null,
        ia_share_last_fired_at: null,
        logpush_last_fired_at: null,
        last_ia_share: null,
        last_total_gb: null,
        last_logpush_gb: null,
        consecutive_query_failures: 5,
        query_fail_last_fired_at: "2026-04-01T00:00:00Z",
      } satisfies R2StorageClassAlertState),
    );
    const env = makeEnv({ RATE_LIMIT: kv as unknown as KVNamespace });

    const res = await worker.fetch(
      new Request(URL_PATH, {
        method: "GET",
        headers: { "X-Edge-Admin-Secret": "topsecret" },
      }),
      env,
      ctx,
    );

    expect(res.headers.get("X-Source")).not.toBe("edge-r2-storage-health");
    // The watchdog state must be untouched: a GET to the wrong path
    // cannot accidentally clear the counter.
    const persisted = JSON.parse(
      kv.store.get(_R2_STORAGE_CLASS_ALERT_STATE_KEY) || "{}",
    ) as R2StorageClassAlertState;
    expect(persisted.consecutive_query_failures).toBe(5);
    expect(persisted.query_fail_last_fired_at).toBe("2026-04-01T00:00:00Z");
  });
});
