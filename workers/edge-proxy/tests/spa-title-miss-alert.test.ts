import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  runSpaTitleMissAlert,
  type SpaTitleMissAlertEnv,
  type SpaTitleMissAlertDeps,
} from "../src/spa-title-miss-alert";

// ─── helpers ──────────────────────────────────────────────────────────────────

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

/** Mock querySpaTitleMisses in analytics-engine via vitest module mock. */
vi.mock("../src/analytics-engine", async (importOriginal) => {
  const orig = await importOriginal<typeof import("../src/analytics-engine")>();
  return { ...orig, querySpaTitleMisses: vi.fn() };
});
import { querySpaTitleMisses } from "../src/analytics-engine";
const mockQuery = querySpaTitleMisses as ReturnType<typeof vi.fn>;

/** Minimal deps that resolve nothing (every path is an uncovered gap). */
function noopDeps(): SpaTitleMissAlertDeps {
  return {
    resolveMeta: () => null,
    slugToTitle: (slug) =>
      slug
        .split("-")
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
        .join(" "),
  };
}

/** Deps that cover paths matching /notes/* as already resolved. */
function notesCoveredDeps(): SpaTitleMissAlertDeps {
  return {
    resolveMeta: (pathname) =>
      pathname.startsWith("/notes/")
        ? { title: "Notes page | Syrabit.ai" }
        : null,
    slugToTitle: (slug) => slug,
  };
}

function baseEnv(
  over: Partial<SpaTitleMissAlertEnv> = {},
): SpaTitleMissAlertEnv & { RATE_LIMIT: FakeKv } {
  const kv = new FakeKv();
  return {
    RATE_LIMIT:                         kv as unknown as KVNamespace,
    CF_ANALYTICS_TOKEN:                 "fake-cf-token",
    SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL: "https://hooks.example.com/watchdog",
    ...over,
  } as SpaTitleMissAlertEnv & { RATE_LIMIT: FakeKv };
}

const NOW = new Date("2026-05-12T01:00:00Z");

// ─── tests ────────────────────────────────────────────────────────────────────

describe("runSpaTitleMissAlert", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(new Response("", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    mockQuery.mockResolvedValue([]);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.clearAllMocks();
  });

  // ── kill-switch ──────────────────────────────────────────────────────────

  it("skips entirely when SPA_TITLE_MISS_ALERT_DISABLED=true", async () => {
    const env = baseEnv({ SPA_TITLE_MISS_ALERT_DISABLED: "true" });
    const result = await runSpaTitleMissAlert(env, noopDeps(), NOW);
    expect(result.skipped).toBe(true);
    expect(result.reason).toMatch(/DISABLED/);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(mockQuery).not.toHaveBeenCalled();
  });

  // ── missing config ───────────────────────────────────────────────────────

  it("skips when CF_ANALYTICS_TOKEN is missing", async () => {
    const env = baseEnv({ CF_ANALYTICS_TOKEN: undefined });
    const result = await runSpaTitleMissAlert(env, noopDeps(), NOW);
    expect(result.skipped).toBe(true);
    expect(result.reason).toMatch(/CF_ANALYTICS_TOKEN/);
    expect(mockQuery).not.toHaveBeenCalled();
  });

  it("skips when RATE_LIMIT KV is not bound", async () => {
    const env = baseEnv({ RATE_LIMIT: undefined as unknown as KVNamespace });
    const result = await runSpaTitleMissAlert(env, noopDeps(), NOW);
    expect(result.skipped).toBe(true);
    expect(result.reason).toMatch(/RATE_LIMIT/);
    expect(mockQuery).not.toHaveBeenCalled();
  });

  // ── no gaps above threshold ──────────────────────────────────────────────

  it("returns ok without firing when no paths are above threshold", async () => {
    // All misses below threshold of 50.
    mockQuery.mockResolvedValue([
      { pathname: "/seba/class-10/history", count: 10 },
      { pathname: "/degree/ba/economics",   count: 49 },
    ]);
    const env = baseEnv();
    const result = await runSpaTitleMissAlert(env, noopDeps(), NOW);
    expect(result.ok).toBe(true);
    expect(result.skipped).toBe(false);
    expect(result.gaps_above_threshold).toBe(0);
    expect(result.alert_fired).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("respects custom threshold from env var", async () => {
    // Path has 30 hits — below default 50 but above custom 20.
    mockQuery.mockResolvedValue([
      { pathname: "/seba/class-10/history", count: 30 },
    ]);
    const env = baseEnv({ SPA_TITLE_MISS_ALERT_THRESHOLD: "20" });
    const result = await runSpaTitleMissAlert(env, noopDeps(), NOW);
    expect(result.gaps_above_threshold).toBe(1);
    expect(result.alert_fired).toBe(true);
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  // ── gaps above threshold → webhook fires ─────────────────────────────────

  it("fires webhook when an uncovered path exceeds the threshold", async () => {
    mockQuery.mockResolvedValue([
      { pathname: "/ahsec/hs-2nd-year/political-science", count: 150 },
    ]);
    const env = baseEnv();
    const result = await runSpaTitleMissAlert(env, noopDeps(), NOW);
    expect(result.ok).toBe(true);
    expect(result.gaps_above_threshold).toBe(1);
    expect(result.alert_fired).toBe(true);

    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("https://hooks.example.com/watchdog");
    const body = JSON.parse(init.body as string);
    expect(body.alert_type).toBe("spa_title_miss_gap");
    expect(body.gap_count).toBe(1);
    expect(body.gaps[0].pathname).toBe("/ahsec/hs-2nd-year/political-science");
    expect(body.gaps[0].count).toBe(150);
    expect(typeof body.gaps[0].suggested_title).toBe("string");
    expect(body.gaps[0].suggested_title.length).toBeGreaterThan(0);
  });

  it("includes all qualifying gaps in one consolidated webhook payload", async () => {
    mockQuery.mockResolvedValue([
      { pathname: "/seba/class-10/history",   count: 200 },
      { pathname: "/degree/ba/sociology",     count: 80 },
      { pathname: "/ahsec/some-new-route",    count: 10 },  // below threshold
    ]);
    const env = baseEnv();
    const result = await runSpaTitleMissAlert(env, noopDeps(), NOW);
    expect(result.gaps_found).toBe(3);           // 3 uncovered total
    expect(result.gaps_above_threshold).toBe(2); // 2 above threshold
    // Only ONE webhook call, not one per gap.
    expect(fetchMock).toHaveBeenCalledOnce();
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.gaps).toHaveLength(2);
  });

  // ── already-covered paths are excluded ───────────────────────────────────

  it("does not alert for paths that already have a resolveMeta match", async () => {
    mockQuery.mockResolvedValue([
      { pathname: "/notes/class-11/physics", count: 999 }, // covered
      { pathname: "/seba/class-10/history",  count: 100 }, // uncovered
    ]);
    const env = baseEnv();
    const result = await runSpaTitleMissAlert(env, notesCoveredDeps(), NOW);
    expect(result.gaps_found).toBe(1);           // only seba path
    expect(result.gaps_above_threshold).toBe(1);
    expect(result.alert_fired).toBe(true);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.gaps[0].pathname).toBe("/seba/class-10/history");
  });

  it("skips completely when all paths above threshold are already covered", async () => {
    mockQuery.mockResolvedValue([
      { pathname: "/notes/class-11/physics",  count: 999 },
      { pathname: "/notes/class-12/biology",  count: 500 },
    ]);
    const env = baseEnv();
    const result = await runSpaTitleMissAlert(env, notesCoveredDeps(), NOW);
    expect(result.gaps_above_threshold).toBe(0);
    expect(result.alert_fired).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // ── cooldown ─────────────────────────────────────────────────────────────

  it("respects the 23-hour cooldown after firing", async () => {
    mockQuery.mockResolvedValue([
      { pathname: "/seba/class-10/history", count: 100 },
    ]);
    const env = baseEnv();

    // First run — fires.
    const r1 = await runSpaTitleMissAlert(env, noopDeps(), NOW);
    expect(r1.alert_fired).toBe(true);

    // Second run 1 hour later — still within cooldown.
    const laterSameDay = new Date(NOW.getTime() + 60 * 60 * 1000);
    const r2 = await runSpaTitleMissAlert(env, noopDeps(), laterSameDay);
    expect(r2.skipped).toBe(true);
    expect(r2.reason).toMatch(/cooldown/);
    // fetch was only called once total.
    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("fires again after the 23-hour cooldown has elapsed", async () => {
    mockQuery.mockResolvedValue([
      { pathname: "/seba/class-10/history", count: 100 },
    ]);
    const env = baseEnv();

    // First run — fires.
    await runSpaTitleMissAlert(env, noopDeps(), NOW);
    expect(fetchMock).toHaveBeenCalledOnce();

    // Second run 23.5 hours later — cooldown has elapsed.
    const nextDay = new Date(NOW.getTime() + 23.5 * 60 * 60 * 1000);
    const r2 = await runSpaTitleMissAlert(env, noopDeps(), nextDay);
    expect(r2.skipped).toBe(false);
    expect(r2.alert_fired).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  // ── webhook failures ─────────────────────────────────────────────────────

  it("returns alert_fired=false and does not update state when webhook fails", async () => {
    mockQuery.mockResolvedValue([
      { pathname: "/seba/class-10/history", count: 100 },
    ]);
    fetchMock.mockResolvedValue(new Response("", { status: 500 }));
    const env = baseEnv();

    const result = await runSpaTitleMissAlert(env, noopDeps(), NOW);
    expect(result.alert_fired).toBe(false);
    // State should NOT have been written (no last_fired_at), so the next
    // run is not blocked by a false-positive cooldown.
    const stateRaw = await (env.RATE_LIMIT as unknown as FakeKv)
      .get("spa_title_miss_alert:state");
    if (stateRaw) {
      const state = JSON.parse(stateRaw);
      expect(state.last_fired_at).toBeNull();
    }
  });

  it("logs to console.error (not silently drops) when webhook URL is missing", async () => {
    mockQuery.mockResolvedValue([
      { pathname: "/seba/class-10/history", count: 100 },
    ]);
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const env = baseEnv({ SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL: undefined });

    const result = await runSpaTitleMissAlert(env, noopDeps(), NOW);
    expect(result.alert_fired).toBe(false);
    expect(errorSpy).toHaveBeenCalledWith(
      expect.stringContaining("PAGING-DARK"),
    );
    errorSpy.mockRestore();
  });

  // ── analytics engine failure ─────────────────────────────────────────────

  it("returns ok=false when the Analytics Engine query throws", async () => {
    mockQuery.mockRejectedValue(new Error("GQL 500"));
    const env = baseEnv();

    const result = await runSpaTitleMissAlert(env, noopDeps(), NOW);
    expect(result.ok).toBe(false);
    expect(result.alert_fired).toBe(false);
    expect(result.reason).toMatch(/GQL 500/);
  });

  // ── suggested title derivation ───────────────────────────────────────────

  it("derives a non-empty suggested title for each gap path", async () => {
    mockQuery.mockResolvedValue([
      { pathname: "/ahsec/hs-1st-year/political-science", count: 60 },
    ]);
    const env = baseEnv();
    await runSpaTitleMissAlert(env, noopDeps(), NOW);

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    // Should be derived from the last segment "political-science"
    expect(body.gaps[0].suggested_title).toBeTruthy();
    expect(body.gaps[0].suggested_title).not.toBe("");
  });
});
