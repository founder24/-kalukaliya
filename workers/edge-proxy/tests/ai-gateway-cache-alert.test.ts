import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  runAiGatewayCacheAlert,
  _readAiGatewayCacheAlertStateForTests,
  _AI_GATEWAY_CACHE_ALERT_STATE_KEY,
  _AI_GATEWAY_CACHE_ALERT_DEFAULTS,
  type AiGatewayCacheAlertEnv,
} from "../src/ai-gateway-cache-alert";

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

interface GqlRow {
  count: number;
  dimensions: { cached: string };
}

/** Build a CF-shaped GraphQL response body with the given rows. */
function gqlResponse(rows: GqlRow[]): Response {
  return new Response(
    JSON.stringify({
      data: {
        viewer: {
          accounts: [{ aiGatewayRequestsAdaptiveGroups: rows }],
        },
      },
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

const NOW = new Date(Date.UTC(2026, 4, 3, 12, 0, 0));

function baseEnv(over: Partial<AiGatewayCacheAlertEnv> = {}): AiGatewayCacheAlertEnv & { RATE_LIMIT: FakeKv } {
  const kv = new FakeKv();
  return {
    RATE_LIMIT: kv as unknown as KVNamespace,
    SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL: "https://hooks.example.com/watchdog",
    WORKERS_AI_GATEWAY_ID: "syrabit-ai-gw",
    AI_GATEWAY_ANALYTICS_TOKEN: "test-token",
    ...over,
  } as AiGatewayCacheAlertEnv & { RATE_LIMIT: FakeKv };
}

describe("ai-gateway-cache-alert", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("skips with disabled_by_var when AI_GATEWAY_CACHE_ALERT_DISABLED=true", async () => {
    const env = baseEnv({ AI_GATEWAY_CACHE_ALERT_DISABLED: "true" });
    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.skipped).toBe(true);
    expect(res.reason).toBe("disabled_by_var");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("skips with no_kv_binding when RATE_LIMIT is absent", async () => {
    const env = baseEnv();
    delete (env as Partial<AiGatewayCacheAlertEnv>).RATE_LIMIT;
    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.skipped).toBe(true);
    expect(res.reason).toBe("no_kv_binding");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("skips with no_gateway_configured when WORKERS_AI_GATEWAY_ID is unset", async () => {
    const env = baseEnv();
    delete (env as Partial<AiGatewayCacheAlertEnv>).WORKERS_AI_GATEWAY_ID;
    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.skipped).toBe(true);
    expect(res.reason).toBe("no_gateway_configured");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("skips with no_analytics_token when AI_GATEWAY_ANALYTICS_TOKEN is unset", async () => {
    const env = baseEnv();
    delete (env as Partial<AiGatewayCacheAlertEnv>).AI_GATEWAY_ANALYTICS_TOKEN;
    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.skipped).toBe(true);
    expect(res.reason).toBe("no_analytics_token");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("does not page when the 24h sample is below MIN_SAMPLE", async () => {
    const env = baseEnv();
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { count: 2, dimensions: { cached: "true" } },
        { count: 5, dimensions: { cached: "false" } },
      ]),
    );
    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.sample).toBe(7);
    expect(res.sample).toBeLessThan(_AI_GATEWAY_CACHE_ALERT_DEFAULTS.MIN_SAMPLE);
    expect(res.floor_alert_fired).toBe(false);
    // Only the GraphQL query, no webhook call.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not page when hit rate is above the floor", async () => {
    const env = baseEnv();
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { count: 800, dimensions: { cached: "true" } },
        { count: 200, dimensions: { cached: "false" } },
      ]),
    );
    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.sample).toBe(1000);
    expect(res.hit_rate).toBeCloseTo(0.8, 5);
    expect(res.floor_alert_fired).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("pages when hit rate is below the floor and sample is sufficient", async () => {
    const env = baseEnv();
    fetchMock
      .mockResolvedValueOnce(
        gqlResponse([
          { count: 100, dimensions: { cached: "true" } },
          { count: 900, dimensions: { cached: "false" } },
        ]),
      )
      .mockResolvedValueOnce(new Response("", { status: 200 }));

    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.hit_rate).toBeCloseTo(0.1, 5);
    expect(res.floor_alert_fired).toBe(true);

    // Webhook called as the 2nd fetch — verify payload shape.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [webhookUrl, webhookInit] = fetchMock.mock.calls[1];
    expect(webhookUrl).toBe("https://hooks.example.com/watchdog");
    const payload = JSON.parse((webhookInit as RequestInit).body as string);
    expect(payload.alert_type).toBe("ai_gateway_cache_hit_rate_low");
    expect(payload.severity).toBe("critical");
    expect(payload.gateway_id).toBe("syrabit-ai-gw");
    expect(payload.embed_tag).toBe("workers-ai-fallback:embed");
    expect(payload.window_hours).toBe(24);
    expect(payload.cached_requests).toBe(100);
    expect(payload.uncached_requests).toBe(900);
    expect(payload.floor_pct).toBe(_AI_GATEWAY_CACHE_ALERT_DEFAULTS.FLOOR_PCT);
    expect(payload.runbook).toBe("docs/ops/ai-gateway-activation.md");

    // State persisted with floor_last_fired_at set.
    const state = await _readAiGatewayCacheAlertStateForTests(env.RATE_LIMIT as unknown as KVNamespace);
    expect(state.floor_last_fired_at).toBe(NOW.toISOString());
    expect(state.last_hit_rate).toBeCloseTo(0.1, 5);
    expect(state.last_sample).toBe(1000);
  });

  it("respects the 6-hour cooldown between firings", async () => {
    const env = baseEnv();
    // Pre-seed state with a recent firing 1h ago.
    const oneHourAgo = new Date(NOW.getTime() - 60 * 60 * 1000);
    await env.RATE_LIMIT.put(
      _AI_GATEWAY_CACHE_ALERT_STATE_KEY,
      JSON.stringify({
        last_evaluated_at: oneHourAgo.toISOString(),
        floor_last_fired_at: oneHourAgo.toISOString(),
        last_hit_rate: 0.1,
        last_sample: 1000,
      }),
    );

    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { count: 50, dimensions: { cached: "true" } },
        { count: 950, dimensions: { cached: "false" } },
      ]),
    );

    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.hit_rate).toBeCloseTo(0.05, 5);
    // Cooldown gates the page.
    expect(res.floor_alert_fired).toBe(false);
    // Only the GraphQL query — no webhook.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("fires again once the cooldown has elapsed", async () => {
    const env = baseEnv();
    const longAgo = new Date(NOW.getTime() - 7 * 60 * 60 * 1000);
    await env.RATE_LIMIT.put(
      _AI_GATEWAY_CACHE_ALERT_STATE_KEY,
      JSON.stringify({
        last_evaluated_at: longAgo.toISOString(),
        floor_last_fired_at: longAgo.toISOString(),
        last_hit_rate: 0.1,
        last_sample: 1000,
      }),
    );

    fetchMock
      .mockResolvedValueOnce(
        gqlResponse([
          { count: 50, dimensions: { cached: "true" } },
          { count: 950, dimensions: { cached: "false" } },
        ]),
      )
      .mockResolvedValueOnce(new Response("", { status: 200 }));

    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.floor_alert_fired).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("skips with query_failed when the GraphQL endpoint errors", async () => {
    const env = baseEnv();
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ errors: [{ message: "scope missing" }] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.skipped).toBe(true);
    expect(res.reason).toBe("query_failed");
    // No webhook attempt.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not fire 'watchdog blind' alert until the consecutive failure threshold is crossed", async () => {
    const env = baseEnv();
    const failResp = () =>
      new Response(JSON.stringify({ errors: [{ message: "scope missing" }] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    // 5 consecutive failures (default threshold = 6) — no alert yet.
    for (let i = 0; i < 5; i++) {
      fetchMock.mockResolvedValueOnce(failResp());
      const res = await runAiGatewayCacheAlert(env, NOW);
      expect(res.skipped).toBe(true);
      expect(res.reason).toBe("query_failed");
      expect(res.consecutive_query_failures).toBe(i + 1);
      expect(res.query_fail_alert_fired).toBe(false);
    }
    // Only GraphQL fetches so far, no webhook.
    expect(fetchMock).toHaveBeenCalledTimes(5);

    // 6th consecutive failure — webhook fires.
    fetchMock.mockResolvedValueOnce(failResp());
    fetchMock.mockResolvedValueOnce(new Response("", { status: 200 }));
    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.consecutive_query_failures).toBe(6);
    expect(res.query_fail_alert_fired).toBe(true);

    expect(fetchMock).toHaveBeenCalledTimes(7);
    const [webhookUrl, webhookInit] = fetchMock.mock.calls[6];
    expect(webhookUrl).toBe("https://hooks.example.com/watchdog");
    const payload = JSON.parse((webhookInit as RequestInit).body as string);
    expect(payload.alert_type).toBe("ai_gateway_cache_watchdog_blind");
    expect(payload.severity).toBe("warning");
    expect(payload.consecutive_failures).toBe(6);
    expect(payload.threshold).toBe(_AI_GATEWAY_CACHE_ALERT_DEFAULTS.QUERY_FAIL_THRESHOLD);
    expect(payload.minutes_blind).toBe(90);
    expect(payload.dashboard_url).toBe(_AI_GATEWAY_CACHE_ALERT_DEFAULTS.DASHBOARD_URL);
    expect(payload.runbook).toBe("docs/ops/ai-gateway-activation.md");
  });

  it("resets the consecutive-failure counter on a successful query", async () => {
    const env = baseEnv();
    const failResp = () =>
      new Response(JSON.stringify({ errors: [{ message: "tx" }] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });

    // 3 failures then a success — counter resets to 0.
    for (let i = 0; i < 3; i++) {
      fetchMock.mockResolvedValueOnce(failResp());
      const r = await runAiGatewayCacheAlert(env, NOW);
      expect(r.consecutive_query_failures).toBe(i + 1);
    }
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { count: 800, dimensions: { cached: "true" } },
        { count: 200, dimensions: { cached: "false" } },
      ]),
    );
    const okRes = await runAiGatewayCacheAlert(env, NOW);
    expect(okRes.ok).toBe(true);
    expect(okRes.consecutive_query_failures).toBe(0);

    const state = await _readAiGatewayCacheAlertStateForTests(env.RATE_LIMIT as unknown as KVNamespace);
    expect(state.consecutive_query_failures).toBe(0);
  });

  it("respects the watchdog-blind cooldown and does not re-page on the next failure", async () => {
    const env = baseEnv();
    // Pre-seed state as if we just paged the watchdog-blind alert
    // 1 hour ago — well inside the 6h QUERY_FAIL_COOLDOWN_MS, so the
    // 7th consecutive failure must NOT re-fire the page.
    const oneHourAgo = new Date(NOW.getTime() - 60 * 60 * 1000);
    await env.RATE_LIMIT.put(
      _AI_GATEWAY_CACHE_ALERT_STATE_KEY,
      JSON.stringify({
        last_evaluated_at: oneHourAgo.toISOString(),
        consecutive_query_failures: 6,
        query_fail_last_fired_at: oneHourAgo.toISOString(),
      }),
    );
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ errors: [{ message: "scope missing" }] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.reason).toBe("query_failed");
    expect(res.consecutive_query_failures).toBe(7);
    // Cooldown active → no second webhook attempt.
    expect(res.query_fail_alert_fired).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // KV cooldown anchor unchanged.
    const state = await _readAiGatewayCacheAlertStateForTests(
      env.RATE_LIMIT as unknown as KVNamespace,
    );
    expect(state.query_fail_last_fired_at).toBe(oneHourAgo.toISOString());
  });

  it("re-fires the watchdog-blind alert once the cooldown has elapsed", async () => {
    const env = baseEnv();
    // Pre-seed a stale watchdog-blind firing > 6h ago — the next
    // failure should be allowed to re-page.
    const longAgo = new Date(
      NOW.getTime() - (_AI_GATEWAY_CACHE_ALERT_DEFAULTS.QUERY_FAIL_COOLDOWN_MS + 1000),
    );
    await env.RATE_LIMIT.put(
      _AI_GATEWAY_CACHE_ALERT_STATE_KEY,
      JSON.stringify({
        last_evaluated_at: longAgo.toISOString(),
        consecutive_query_failures: 6,
        query_fail_last_fired_at: longAgo.toISOString(),
      }),
    );
    fetchMock
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ errors: [{ message: "scope missing" }] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(new Response("", { status: 200 }));
    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.consecutive_query_failures).toBe(7);
    expect(res.query_fail_alert_fired).toBe(true);
    // Cooldown anchor advanced to NOW.
    const state = await _readAiGatewayCacheAlertStateForTests(
      env.RATE_LIMIT as unknown as KVNamespace,
    );
    expect(state.query_fail_last_fired_at).toBe(NOW.toISOString());
  });

  it("respects AI_GATEWAY_CACHE_ALERT_QUERY_FAIL_THRESHOLD override", async () => {
    const env = baseEnv({ AI_GATEWAY_CACHE_ALERT_QUERY_FAIL_THRESHOLD: "1" });
    fetchMock
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ errors: [{ message: "scope missing" }] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(new Response("", { status: 200 }));
    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.consecutive_query_failures).toBe(1);
    // Threshold lowered to 1 → first failure trips the page.
    expect(res.query_fail_alert_fired).toBe(true);

    // Webhook payload echoes the overridden threshold.
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, webhookInit] = fetchMock.mock.calls[1];
    const payload = JSON.parse((webhookInit as RequestInit).body as string);
    expect(payload.alert_type).toBe("ai_gateway_cache_watchdog_blind");
    expect(payload.threshold).toBe(1);
    expect(payload.consecutive_failures).toBe(1);
  });

  it("respects custom floor and embed tag overrides", async () => {
    const env = baseEnv({
      AI_GATEWAY_CACHE_HIT_RATE_FLOOR_PCT: "70",
      AI_GATEWAY_CACHE_ALERT_EMBED_TAG: "custom-embed-tag",
    });
    fetchMock
      .mockResolvedValueOnce(
        gqlResponse([
          { count: 600, dimensions: { cached: "true" } },
          { count: 400, dimensions: { cached: "false" } },
        ]),
      )
      .mockResolvedValueOnce(new Response("", { status: 200 }));

    const res = await runAiGatewayCacheAlert(env, NOW);
    // 60% < 70% floor → page.
    expect(res.floor_alert_fired).toBe(true);
    expect(res.floor_pct).toBe(70);

    // GraphQL request body carried the custom embed tag.
    const [, gqlInit] = fetchMock.mock.calls[0];
    const gqlBody = JSON.parse((gqlInit as RequestInit).body as string);
    expect(gqlBody.variables.embedTag).toBe("custom-embed-tag");
    expect(gqlBody.variables.gatewayId).toBe("syrabit-ai-gw");

    // Webhook payload echoes the same tag.
    const [, webhookInit] = fetchMock.mock.calls[1];
    const payload = JSON.parse((webhookInit as RequestInit).body as string);
    expect(payload.embed_tag).toBe("custom-embed-tag");
    expect(payload.floor_pct).toBe(70);
  });

  it("skips with default behavior when SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL is missing but threshold met", async () => {
    const env = baseEnv();
    delete (env as Partial<AiGatewayCacheAlertEnv>).SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL;
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { count: 100, dimensions: { cached: "true" } },
        { count: 900, dimensions: { cached: "false" } },
      ]),
    );
    const res = await runAiGatewayCacheAlert(env, NOW);
    expect(res.ok).toBe(true);
    // Threshold met but webhook missing → fireWebhook returns false.
    expect(res.floor_alert_fired).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
