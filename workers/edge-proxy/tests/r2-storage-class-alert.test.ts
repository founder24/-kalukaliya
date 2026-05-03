import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  runR2StorageClassAlert,
  shouldRunMonthlyR2Check,
  _readR2StorageClassAlertStateForTests,
  _R2_STORAGE_CLASS_ALERT_STATE_KEY,
  _R2_STORAGE_CLASS_ALERT_DEFAULTS,
  type R2StorageClassAlertEnv,
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

interface FakeR2Object {
  key: string;
  size: number;
}

class FakeR2Bucket {
  objects: FakeR2Object[] = [];
  listError: Error | null = null;
  /** Page size used when truncating responses. Helps tests exercise
   *  the cursor pagination path without producing 1000s of objects. */
  pageLimit = 1000;

  async list(opts: {
    prefix?: string;
    limit?: number;
    cursor?: string;
  }): Promise<{
    objects: FakeR2Object[];
    truncated: boolean;
    cursor?: string;
  }> {
    if (this.listError) throw this.listError;
    const prefix = opts.prefix ?? "";
    const all = this.objects.filter((o) => o.key.startsWith(prefix));
    const limit = Math.min(opts.limit ?? this.pageLimit, this.pageLimit);
    const start = opts.cursor ? parseInt(opts.cursor, 10) : 0;
    const slice = all.slice(start, start + limit);
    const next = start + slice.length;
    const truncated = next < all.length;
    return {
      objects: slice,
      truncated,
      cursor: truncated ? String(next) : undefined,
    };
  }
}

interface GqlRow {
  max: { payloadSize: number };
  dimensions: { bucketName: string; storageClass: string };
}

function gqlResponse(rows: GqlRow[]): Response {
  return new Response(
    JSON.stringify({
      data: {
        viewer: {
          accounts: [{ r2StorageAdaptiveGroups: rows }],
        },
      },
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

const NOW = new Date(Date.UTC(2026, 6, 1, 0, 0, 0)); // 2026-07-01T00:00:00Z
const RULES_APPLIED_60D_AGO = new Date(NOW.getTime() - 60 * 24 * 60 * 60 * 1000)
  .toISOString();
const RULES_APPLIED_10D_AGO = new Date(NOW.getTime() - 10 * 24 * 60 * 60 * 1000)
  .toISOString();
const GB = _R2_STORAGE_CLASS_ALERT_DEFAULTS.BYTES_PER_GB;

function baseEnv(
  over: Partial<R2StorageClassAlertEnv> = {},
): R2StorageClassAlertEnv & { RATE_LIMIT: FakeKv; R2_MEDIA: FakeR2Bucket } {
  const kv = new FakeKv();
  const media = new FakeR2Bucket();
  return {
    RATE_LIMIT: kv as unknown as KVNamespace,
    R2_MEDIA: media as unknown as R2Bucket,
    SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL: "https://hooks.example.com/watchdog",
    R2_STORAGE_ANALYTICS_TOKEN: "test-token",
    R2_LIFECYCLE_RULES_APPLIED_AT: RULES_APPLIED_60D_AGO,
    ...over,
  } as R2StorageClassAlertEnv & { RATE_LIMIT: FakeKv; R2_MEDIA: FakeR2Bucket };
}

describe("r2-storage-class-alert", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // ── Skip / configuration paths ───────────────────────────────────────

  it("skips with disabled_by_var when R2_STORAGE_ALERT_DISABLED=true", async () => {
    const env = baseEnv({ R2_STORAGE_ALERT_DISABLED: "true" });
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.skipped).toBe(true);
    expect(res.reason).toBe("disabled_by_var");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("skips with no_kv_binding when RATE_LIMIT is absent", async () => {
    const env = baseEnv();
    delete (env as Partial<R2StorageClassAlertEnv>).RATE_LIMIT;
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.skipped).toBe(true);
    expect(res.reason).toBe("no_kv_binding");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("skips with no_analytics_token when R2_STORAGE_ANALYTICS_TOKEN is unset", async () => {
    const env = baseEnv();
    delete (env as Partial<R2StorageClassAlertEnv>).R2_STORAGE_ANALYTICS_TOKEN;
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.skipped).toBe(true);
    expect(res.reason).toBe("no_analytics_token");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  // ── IA-share signal ──────────────────────────────────────────────────

  it("does not page on IA share when rules are <30 days old", async () => {
    const env = baseEnv({ R2_LIFECYCLE_RULES_APPLIED_AT: RULES_APPLIED_10D_AGO });
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { max: { payloadSize: 50 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "Standard" } },
        { max: { payloadSize: 50 * GB }, dimensions: { bucketName: "syrabit-media", storageClass: "Standard" } },
      ]),
    );
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.ia_share).toBe(0);
    expect(res.rules_age_days).toBe(10);
    expect(res.ia_alert_fired).toBe(false);
    // Only the GraphQL query — no webhook for IA, and no logpush
    // objects so no logpush webhook either.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not page when IA share is non-zero", async () => {
    const env = baseEnv();
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { max: { payloadSize: 30 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "Standard" } },
        { max: { payloadSize: 20 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "InfrequentAccess" } },
        { max: { payloadSize: 10 * GB }, dimensions: { bucketName: "syrabit-media", storageClass: "Standard" } },
        { max: { payloadSize: 5 * GB }, dimensions: { bucketName: "syrabit-media", storageClass: "InfrequentAccess" } },
      ]),
    );
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.ia_share).toBeGreaterThan(0);
    expect(res.ia_alert_fired).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("pages when IA share is 0 after the 30-day grace window", async () => {
    const env = baseEnv();
    fetchMock
      .mockResolvedValueOnce(
        gqlResponse([
          { max: { payloadSize: 50 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "Standard" } },
          { max: { payloadSize: 30 * GB }, dimensions: { bucketName: "syrabit-media", storageClass: "Standard" } },
        ]),
      )
      .mockResolvedValueOnce(new Response("", { status: 200 }));

    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.ia_share).toBe(0);
    expect(res.standard_gb).toBeCloseTo(80, 5);
    expect(res.infrequent_access_gb).toBe(0);
    expect(res.ia_alert_fired).toBe(true);
    expect(res.logpush_alert_fired).toBe(false);

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [webhookUrl, webhookInit] = fetchMock.mock.calls[1];
    expect(webhookUrl).toBe("https://hooks.example.com/watchdog");
    const payload = JSON.parse((webhookInit as RequestInit).body as string);
    expect(payload.alert_type).toBe("r2_ia_share_zero");
    expect(payload.severity).toBe("critical");
    expect(payload.buckets).toEqual([..._R2_STORAGE_CLASS_ALERT_DEFAULTS.BUCKETS]);
    expect(payload.rules_age_days).toBe(60);
    expect(payload.runbook).toBe("docs/cloudflare-monthly-cost-review.md#step-5");

    const state = await _readR2StorageClassAlertStateForTests(
      env.RATE_LIMIT as unknown as KVNamespace,
    );
    expect(state.ia_share_last_fired_at).toBe(NOW.toISOString());
  });

  it("does not page on IA share when total volume is below 1GB (fresh deploy)", async () => {
    const env = baseEnv();
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { max: { payloadSize: 100 * 1024 }, dimensions: { bucketName: "syrabit-assets", storageClass: "Standard" } },
      ]),
    );
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.ia_share).toBe(0);
    expect(res.ia_alert_fired).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not page on IA share when R2_LIFECYCLE_RULES_APPLIED_AT is unset", async () => {
    const env = baseEnv();
    delete (env as Partial<R2StorageClassAlertEnv>).R2_LIFECYCLE_RULES_APPLIED_AT;
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { max: { payloadSize: 50 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "Standard" } },
      ]),
    );
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.ia_alert_fired).toBe(false);
    expect(res.rules_age_days).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("respects the 28-day cooldown between IA-share firings", async () => {
    const env = baseEnv();
    const tenDaysAgo = new Date(NOW.getTime() - 10 * 24 * 60 * 60 * 1000);
    await env.RATE_LIMIT.put(
      _R2_STORAGE_CLASS_ALERT_STATE_KEY,
      JSON.stringify({
        last_evaluated_at: tenDaysAgo.toISOString(),
        ia_share_last_fired_at: tenDaysAgo.toISOString(),
        last_ia_share: 0,
      }),
    );
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { max: { payloadSize: 50 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "Standard" } },
      ]),
    );
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.ia_share).toBe(0);
    expect(res.ia_alert_fired).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  // ── Logpush-cap signal ───────────────────────────────────────────────

  it("pages when the Logpush prefix exceeds the 5GB cap", async () => {
    const env = baseEnv();
    // 6GB across 6 objects under logpush/, well above the 5GB cap.
    for (let i = 0; i < 6; i++) {
      env.R2_MEDIA.objects.push({ key: `logpush/2026-07-${i}.json.gz`, size: GB });
    }
    // Healthy IA split so the IA signal does not interfere.
    fetchMock
      .mockResolvedValueOnce(
        gqlResponse([
          { max: { payloadSize: 30 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "Standard" } },
          { max: { payloadSize: 20 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "InfrequentAccess" } },
        ]),
      )
      .mockResolvedValueOnce(new Response("", { status: 200 }));

    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.logpush_gb).toBeCloseTo(6, 5);
    expect(res.logpush_alert_fired).toBe(true);
    expect(res.ia_alert_fired).toBe(false);

    const [, webhookInit] = fetchMock.mock.calls[1];
    const payload = JSON.parse((webhookInit as RequestInit).body as string);
    expect(payload.alert_type).toBe("r2_logpush_storage_high");
    expect(payload.severity).toBe("warning");
    expect(payload.bucket).toBe("syrabit-media");
    expect(payload.prefix).toBe(_R2_STORAGE_CLASS_ALERT_DEFAULTS.LOGPUSH_PREFIX);
    expect(payload.logpush_cap_gb).toBe(5);
  });

  it("does not page on Logpush prefix when under cap", async () => {
    const env = baseEnv();
    env.R2_MEDIA.objects.push({ key: "logpush/2026-07-01.json.gz", size: 2 * GB });
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { max: { payloadSize: 30 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "Standard" } },
        { max: { payloadSize: 20 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "InfrequentAccess" } },
      ]),
    );
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.logpush_gb).toBeCloseTo(2, 5);
    expect(res.logpush_alert_fired).toBe(false);
    // Only the GraphQL query.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("paginates the Logpush listing across multiple pages", async () => {
    const env = baseEnv();
    env.R2_MEDIA.pageLimit = 2; // force pagination
    for (let i = 0; i < 7; i++) {
      env.R2_MEDIA.objects.push({ key: `logpush/${i}.json.gz`, size: GB });
    }
    fetchMock
      .mockResolvedValueOnce(
        gqlResponse([
          { max: { payloadSize: 10 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "InfrequentAccess" } },
        ]),
      )
      .mockResolvedValueOnce(new Response("", { status: 200 }));

    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.logpush_gb).toBeCloseTo(7, 5);
    expect(res.logpush_alert_fired).toBe(true);
  });

  it("ignores objects outside the logpush/ prefix", async () => {
    const env = baseEnv();
    env.R2_MEDIA.objects.push({ key: "og/2026/page.png", size: 10 * GB });
    env.R2_MEDIA.objects.push({ key: "logpush/2026.json.gz", size: 1 * GB });
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { max: { payloadSize: 10 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "InfrequentAccess" } },
      ]),
    );
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.logpush_gb).toBeCloseTo(1, 5);
    expect(res.logpush_alert_fired).toBe(false);
  });

  it("skips logpush check when R2_MEDIA binding is absent", async () => {
    const env = baseEnv();
    delete (env as Partial<R2StorageClassAlertEnv>).R2_MEDIA;
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { max: { payloadSize: 10 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "InfrequentAccess" } },
      ]),
    );
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.ok).toBe(true);
    expect(res.logpush_gb).toBeNull();
    expect(res.logpush_alert_fired).toBe(false);
  });

  // ── Failure modes ────────────────────────────────────────────────────

  it("returns query_failed when GraphQL errors but still walks logpush", async () => {
    const env = baseEnv();
    env.R2_MEDIA.objects.push({ key: "logpush/x.json.gz", size: 6 * GB });
    fetchMock
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ errors: [{ message: "scope missing" }] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(new Response("", { status: 200 }));
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.skipped).toBe(true);
    expect(res.reason).toBe("query_failed");
    // Logpush signal still evaluated and fired despite the GraphQL
    // failure — these are independent checks.
    expect(res.logpush_alert_fired).toBe(true);
    expect(res.logpush_gb).toBeCloseTo(6, 5);
  });

  it("does not fire when SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL is missing", async () => {
    const env = baseEnv();
    delete (env as Partial<R2StorageClassAlertEnv>).SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL;
    fetchMock.mockResolvedValueOnce(
      gqlResponse([
        { max: { payloadSize: 50 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "Standard" } },
      ]),
    );
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.ok).toBe(true);
    // Threshold met but no webhook → fireWebhook returns false.
    expect(res.ia_alert_fired).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("respects R2_STORAGE_ALERT_LOGPUSH_CAP_GB override", async () => {
    const env = baseEnv({ R2_STORAGE_ALERT_LOGPUSH_CAP_GB: "1" });
    env.R2_MEDIA.objects.push({ key: "logpush/x.json.gz", size: 2 * GB });
    fetchMock
      .mockResolvedValueOnce(
        gqlResponse([
          { max: { payloadSize: 10 * GB }, dimensions: { bucketName: "syrabit-assets", storageClass: "InfrequentAccess" } },
        ]),
      )
      .mockResolvedValueOnce(new Response("", { status: 200 }));
    const res = await runR2StorageClassAlert(env, NOW);
    expect(res.logpush_cap_gb).toBe(1);
    expect(res.logpush_alert_fired).toBe(true);
  });
});

describe("shouldRunMonthlyR2Check", () => {
  it("returns true on day 1 at 00:00 UTC", () => {
    expect(shouldRunMonthlyR2Check(new Date(Date.UTC(2026, 6, 1, 0, 0, 0)))).toBe(true);
    expect(shouldRunMonthlyR2Check(new Date(Date.UTC(2027, 0, 1, 0, 0, 0)))).toBe(true);
  });

  it("returns false on any other day", () => {
    expect(shouldRunMonthlyR2Check(new Date(Date.UTC(2026, 6, 2, 0, 0, 0)))).toBe(false);
    expect(shouldRunMonthlyR2Check(new Date(Date.UTC(2026, 6, 15, 0, 0, 0)))).toBe(false);
  });

  it("returns false on day 1 at any non-00:00 minute", () => {
    expect(shouldRunMonthlyR2Check(new Date(Date.UTC(2026, 6, 1, 0, 1, 0)))).toBe(false);
    expect(shouldRunMonthlyR2Check(new Date(Date.UTC(2026, 6, 1, 1, 0, 0)))).toBe(false);
    expect(shouldRunMonthlyR2Check(new Date(Date.UTC(2026, 6, 1, 12, 0, 0)))).toBe(false);
  });
});
