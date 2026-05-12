/**
 * Task #33 / Task #47 — unit tests for the edge-worker settings API.
 *
 * Tests GET and PUT /api/edge/spa-title-miss-settings, which persist the
 * alert threshold and on/off switch to RATE_LIMIT KV so the admin can tune
 * alerting at runtime without a wrangler redeploy.
 *
 * Coverage:
 *   GET  — auth rejection (missing / wrong secret), env-var defaults when KV
 *           is empty, KV override takes priority, kv_override_set flag,
 *           env_threshold / env_disabled reflect wrangler vars
 *   PUT  — auth rejection, 503 when RATE_LIMIT is absent, 400 for invalid
 *           JSON / threshold < 1 / disabled not boolean, happy path (both
 *           fields), partial update (threshold only, disabled only), KV key
 *           verified after write, GET-after-PUT round-trip
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import workerHandler from "../src/index";
import { _resetMonitorStateForTests } from "../src/kv-monitor";

// ─── constants ────────────────────────────────────────────────────────────────

const SETTINGS_KEY = "spa_title_miss:settings";
const SECRET       = "test-d1-secret";
const WRONG_SECRET = "bad-secret";

// ─── fake KV ─────────────────────────────────────────────────────────────────

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
      const ttlSec   = opts?.expirationTtl;
      const expiresAt = typeof ttlSec === "number" && ttlSec > 0
        ? Date.now() + ttlSec * 1000
        : null;
      store.set(k, { value: v, expiresAt });
    },
    async delete(k: string): Promise<void> { store.delete(k); },
    async list() {
      return {
        keys: Array.from(store.keys()).map((name) => ({ name })),
        list_complete: true,
      };
    },
  };
}

type FakeKv = ReturnType<typeof makeKv>;

// ─── env builder ─────────────────────────────────────────────────────────────

function makeEnv(
  opts: {
    rateLimit?: FakeKv | null;
    threshold?: string;
    disabled?: string;
  } = {},
): Parameters<typeof workerHandler.fetch>[1] {
  const rateLimit =
    opts.rateLimit === null ? undefined : (opts.rateLimit ?? makeKv());
  return {
    BACKEND_URL:  "https://backend.test",
    PAGES_ORIGIN: "https://pages.test",
    RATE_LIMIT:   rateLimit as unknown,
    BOT_HTML_CACHE: undefined as unknown,
    CONTENT_DB:   undefined as unknown,
    D1_SYNC_SECRET: SECRET,
    ...(opts.threshold !== undefined
      ? { SPA_TITLE_MISS_ALERT_THRESHOLD: opts.threshold }
      : {}),
    ...(opts.disabled !== undefined
      ? { SPA_TITLE_MISS_ALERT_DISABLED: opts.disabled }
      : {}),
  } as unknown as Parameters<typeof workerHandler.fetch>[1];
}

const ctxNoop = {
  waitUntil: (p: Promise<unknown>) => { void p; },
  passThroughOnException: () => {},
} as unknown as ExecutionContext;

// ─── request helpers ──────────────────────────────────────────────────────────

function settingsGet(secret: string | null = SECRET): Request {
  return new Request("https://syrabit.ai/api/edge/spa-title-miss-settings", {
    method: "GET",
    headers: secret !== null ? { "X-Edge-Admin-Secret": secret } : {},
  });
}

function settingsPut(
  body: unknown,
  secret: string | null = SECRET,
  rawBody?: string,
): Request {
  const isRaw = rawBody !== undefined;
  return new Request("https://syrabit.ai/api/edge/spa-title-miss-settings", {
    method: "PUT",
    headers: {
      ...(secret !== null ? { "X-Edge-Admin-Secret": secret } : {}),
      "Content-Type": "application/json",
    },
    body: isRaw ? rawBody : JSON.stringify(body),
  });
}

// ─── test suite ───────────────────────────────────────────────────────────────

describe("GET /api/edge/spa-title-miss-settings (Task #33 / Task #47)", () => {
  beforeEach(() => {
    _resetMonitorStateForTests();
    // Stub global fetch to a no-op so settings tests never make real network
    // calls (the GET/PUT settings routes read/write KV only; no fetch needed).
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 200 })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("returns 401 when X-Edge-Admin-Secret header is missing", async () => {
    const resp = await workerHandler.fetch(settingsGet(null), makeEnv(), ctxNoop);
    expect(resp.status).toBe(401);
    const body = await resp.json() as { error: string };
    expect(body.error).toMatch(/[Uu]nauthorized/);
  });

  it("returns 401 when X-Edge-Admin-Secret is wrong", async () => {
    const resp = await workerHandler.fetch(settingsGet(WRONG_SECRET), makeEnv(), ctxNoop);
    expect(resp.status).toBe(401);
  });

  it("returns env-var defaults when KV has no override (kv_override_set: false)", async () => {
    const resp = await workerHandler.fetch(settingsGet(), makeEnv(), ctxNoop);
    expect(resp.status).toBe(200);
    const body = await resp.json() as Record<string, unknown>;
    expect(body.threshold).toBe(50);
    expect(body.disabled).toBe(false);
    expect(body.kv_override_set).toBe(false);
    expect(body.env_threshold).toBe(50);
    expect(body.env_disabled).toBe(false);
  });

  it("returns KV values and kv_override_set: true when an override is stored", async () => {
    const kv = makeKv();
    await kv.put(SETTINGS_KEY, JSON.stringify({ threshold: 100, disabled: true }));
    const resp = await workerHandler.fetch(settingsGet(), makeEnv({ rateLimit: kv }), ctxNoop);
    expect(resp.status).toBe(200);
    const body = await resp.json() as Record<string, unknown>;
    expect(body.threshold).toBe(100);
    expect(body.disabled).toBe(true);
    expect(body.kv_override_set).toBe(true);
  });

  it("kv_override_set is true even when KV value happens to match env-var defaults", async () => {
    const kv = makeKv();
    // Storing the default values explicitly should still set kv_override_set: true.
    await kv.put(SETTINGS_KEY, JSON.stringify({ threshold: 50, disabled: false }));
    const resp = await workerHandler.fetch(settingsGet(), makeEnv({ rateLimit: kv }), ctxNoop);
    const body = await resp.json() as Record<string, unknown>;
    expect(body.kv_override_set).toBe(true);
  });

  it("env_threshold reflects SPA_TITLE_MISS_ALERT_THRESHOLD wrangler var", async () => {
    const resp = await workerHandler.fetch(
      settingsGet(),
      makeEnv({ threshold: "75" }),
      ctxNoop,
    );
    const body = await resp.json() as Record<string, unknown>;
    expect(body.env_threshold).toBe(75);
    // No KV override → effective threshold also comes from env var.
    expect(body.threshold).toBe(75);
  });

  it("env_disabled reflects SPA_TITLE_MISS_ALERT_DISABLED wrangler var", async () => {
    const resp = await workerHandler.fetch(
      settingsGet(),
      makeEnv({ disabled: "true" }),
      ctxNoop,
    );
    const body = await resp.json() as Record<string, unknown>;
    expect(body.env_disabled).toBe(true);
    expect(body.disabled).toBe(true);
  });

  it("KV override wins over env-var when both are set", async () => {
    const kv = makeKv();
    await kv.put(SETTINGS_KEY, JSON.stringify({ threshold: 200, disabled: true }));
    const resp = await workerHandler.fetch(
      settingsGet(),
      makeEnv({ rateLimit: kv, threshold: "75", disabled: "false" }),
      ctxNoop,
    );
    const body = await resp.json() as Record<string, unknown>;
    // Effective values come from KV, not env var.
    expect(body.threshold).toBe(200);
    expect(body.disabled).toBe(true);
    // But env_* fields still reflect the wrangler vars.
    expect(body.env_threshold).toBe(75);
    expect(body.env_disabled).toBe(false);
  });

  it("returns 200 with env-var defaults even when RATE_LIMIT KV is absent", async () => {
    // RATE_LIMIT not bound → _readSpaTitleMissKvSettings short-circuits to defaults;
    // the GET handler must not return 503.
    const resp = await workerHandler.fetch(
      settingsGet(),
      makeEnv({ rateLimit: null }),
      ctxNoop,
    );
    expect(resp.status).toBe(200);
    const body = await resp.json() as Record<string, unknown>;
    expect(body.threshold).toBe(50);
    expect(body.kv_override_set).toBe(false);
  });
});

describe("PUT /api/edge/spa-title-miss-settings (Task #33 / Task #47)", () => {
  beforeEach(() => {
    _resetMonitorStateForTests();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 200 })));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  it("returns 401 when X-Edge-Admin-Secret is missing", async () => {
    const resp = await workerHandler.fetch(
      settingsPut({ threshold: 100 }, null),
      makeEnv(),
      ctxNoop,
    );
    expect(resp.status).toBe(401);
  });

  it("returns 401 when X-Edge-Admin-Secret is wrong", async () => {
    const resp = await workerHandler.fetch(
      settingsPut({ threshold: 100 }, WRONG_SECRET),
      makeEnv(),
      ctxNoop,
    );
    expect(resp.status).toBe(401);
  });

  it("returns 503 when RATE_LIMIT KV is not bound", async () => {
    const resp = await workerHandler.fetch(
      settingsPut({ threshold: 100 }),
      makeEnv({ rateLimit: null }),
      ctxNoop,
    );
    expect(resp.status).toBe(503);
    const body = await resp.json() as { error: string };
    expect(body.error).toMatch(/RATE_LIMIT/);
  });

  it("returns 400 for a non-JSON body", async () => {
    const resp = await workerHandler.fetch(
      settingsPut({}, SECRET, "not-json"),
      makeEnv(),
      ctxNoop,
    );
    expect(resp.status).toBe(400);
    const body = await resp.json() as { error: string };
    expect(body.error).toMatch(/JSON/i);
  });

  it("returns 400 when threshold is 0 (must be ≥ 1)", async () => {
    const resp = await workerHandler.fetch(
      settingsPut({ threshold: 0 }),
      makeEnv(),
      ctxNoop,
    );
    expect(resp.status).toBe(400);
    const body = await resp.json() as { error: string };
    expect(body.error).toMatch(/threshold/i);
  });

  it("returns 400 when threshold is negative", async () => {
    const resp = await workerHandler.fetch(
      settingsPut({ threshold: -10 }),
      makeEnv(),
      ctxNoop,
    );
    expect(resp.status).toBe(400);
  });

  it("returns 400 when disabled is not a boolean", async () => {
    const resp = await workerHandler.fetch(
      settingsPut({ disabled: "yes" }),
      makeEnv(),
      ctxNoop,
    );
    expect(resp.status).toBe(400);
    const body = await resp.json() as { error: string };
    expect(body.error).toMatch(/disabled/i);
  });

  it("happy path: sets both threshold and disabled, returns ok: true", async () => {
    const kv  = makeKv();
    const env = makeEnv({ rateLimit: kv });

    const resp = await workerHandler.fetch(
      settingsPut({ threshold: 100, disabled: true }),
      env,
      ctxNoop,
    );
    expect(resp.status).toBe(200);
    const body = await resp.json() as { ok: boolean; threshold: number; disabled: boolean };
    expect(body.ok).toBe(true);
    expect(body.threshold).toBe(100);
    expect(body.disabled).toBe(true);
  });

  it("PUT writes to the correct KV key so _readSpaTitleMissKvSettings can find it", async () => {
    const kv  = makeKv();
    const env = makeEnv({ rateLimit: kv });

    await workerHandler.fetch(settingsPut({ threshold: 150, disabled: false }), env, ctxNoop);

    const stored = await kv.get(SETTINGS_KEY);
    expect(stored).not.toBeNull();
    const parsed = JSON.parse(stored!) as { threshold: number; disabled: boolean };
    expect(parsed.threshold).toBe(150);
    expect(parsed.disabled).toBe(false);
  });

  it("partial update (threshold only): preserves existing disabled value", async () => {
    const kv = makeKv();
    await kv.put(SETTINGS_KEY, JSON.stringify({ threshold: 50, disabled: true }));
    const env = makeEnv({ rateLimit: kv });

    const resp = await workerHandler.fetch(settingsPut({ threshold: 200 }), env, ctxNoop);
    expect(resp.status).toBe(200);
    const body = await resp.json() as { threshold: number; disabled: boolean };
    expect(body.threshold).toBe(200);
    expect(body.disabled).toBe(true); // preserved from KV
  });

  it("partial update (disabled only): preserves existing threshold value", async () => {
    const kv = makeKv();
    await kv.put(SETTINGS_KEY, JSON.stringify({ threshold: 75, disabled: false }));
    const env = makeEnv({ rateLimit: kv });

    const resp = await workerHandler.fetch(settingsPut({ disabled: true }), env, ctxNoop);
    expect(resp.status).toBe(200);
    const body = await resp.json() as { threshold: number; disabled: boolean };
    expect(body.threshold).toBe(75); // preserved
    expect(body.disabled).toBe(true); // updated
  });

  it("GET-after-PUT round-trip: GET reflects the values written by PUT", async () => {
    const kv  = makeKv();
    const env = makeEnv({ rateLimit: kv });

    await workerHandler.fetch(settingsPut({ threshold: 150, disabled: true }), env, ctxNoop);

    const getResp = await workerHandler.fetch(settingsGet(), env, ctxNoop);
    expect(getResp.status).toBe(200);
    const body = await getResp.json() as Record<string, unknown>;
    expect(body.threshold).toBe(150);
    expect(body.disabled).toBe(true);
    expect(body.kv_override_set).toBe(true);
  });

  it("threshold is floor'd to an integer (1.9 → 1)", async () => {
    const kv  = makeKv();
    const env = makeEnv({ rateLimit: kv });

    const resp = await workerHandler.fetch(settingsPut({ threshold: 99.9 }), env, ctxNoop);
    expect(resp.status).toBe(200);
    const body = await resp.json() as { threshold: number };
    expect(body.threshold).toBe(99);
  });
});
