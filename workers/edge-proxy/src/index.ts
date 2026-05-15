import {
  isD1Synced, resetD1SyncedCache, isTablePopulated,
  getBoards, getClasses, getStreams, getAllSubjects, getSubjectsByStream,
  getSubjectsByClassId, getSubjectById, getChaptersBySubject, getChapterByPath,
  getTopicsByChapter, getSitemapEntries, getLibraryBundle, getLibraryBundleSlim,
  getSeoPageBySlugs, getSeoPageTypes, getSeoPageBundle,
  getSeoPagesByType, getPublishedPageTypes,
  getSubjectSitemapEntries, getChapterSitemapEntries,
  getDeltaSitemapEntries,
} from "./d1-queries";
import { syncFromPayload, getSyncStatus } from "./d1-sync";
import {
  wrapKvNamespace,
  getUsageSnapshot,
  getUsageSnapshotAggregated,
  type WrapKvOptions,
  type KvUsageQuota,
} from "./kv-monitor";
import { runSyntheticProbe } from "./synthetic-probe";
import { runCfBlockProbe } from "./cf-block-probe";
import { runBotCacheAlert } from "./bot-cache-alert";
import { runAiGatewayCacheAlert } from "./ai-gateway-cache-alert";
import { runSpaTitleMissAlert } from "./spa-title-miss-alert";
import {
  runR2StorageClassAlert,
  resetR2StorageWatchdogBlindCounter,
  shouldRunMonthlyR2Check,
  readR2StorageClassAlertState,
} from "./r2-storage-class-alert";
import {
  recordBotCacheEvent,
  getBotCacheStats,
  type BotCacheStats,
} from "./bot-cache-stats";
import {
  getCacheablePrefixes,
  getCacheTtlEntries,
  getExamCacheTtlEntries,
  getBypassPrefixes,
  getUserSpecificPrefixes,
  DEFAULT_CACHE_TTL_SECONDS,
} from "./monitored-urls";
// Task #575 — in-isolate cache of `/api/health/season`. The fetch
// handler refreshes `_currentSeasonSnapshot` once per request (the
// helper itself is internally rate-limited to one origin call per
// 60s per isolate) so `getCacheTtl()` stays a synchronous lookup at
// every callsite.
import {
  getSeasonSnapshot,
  pickEffectiveTtl,
  type SeasonSnapshot,
} from "./season-cache";
// Task #944 — Unified Log Explorer: per-request shipper that batches
// records and POSTs them to /api/logs/ingest via ctx.waitUntil so it
// never adds latency to user-visible responses.
import { recordEdgeLog, type EdgeLogShipperEnv } from "./log-shipper";
// Task #109 Phase 5 — Durable Object rate limiter + Analytics Engine query utility.
import { RateLimiter } from "./rate-limiter-do";
import { queryEdgeMetrics, querySpaTitleMisses } from "./analytics-engine";
// Task #575 — SeasonCacheDO owns the /api/health/season snapshot for the region.
import { SeasonCacheDO } from "./season-cache-do";
export { RateLimiter, SeasonCacheDO };

interface Env {
  BACKEND_URL: string;
  PAGES_ORIGIN?: string;
  RATE_LIMIT: KVNamespace;
  BOT_HTML_CACHE?: KVNamespace;
  /**
   * Task #513 §K.2 — deterministic AI response cache. Cloudflare KV
   * namespace `ai_response_cache` shared with the FastAPI backend
   * (the backend writes via the `/api/edge/ai-response-cache/*`
   * route on the worker; same key namespace as the backend's
   * `ai_input_cache.py`). 30-day TTL is enforced at write time on
   * the backend side. Optional so the worker still boots when the
   * binding is not declared.
   */
  AI_RESPONSE_CACHE?: KVNamespace;
  /**
   * Task #513 §A — HMAC-SHA256 secret used to verify the chat-cap
   * caller's identity. MUST match the FastAPI backend's `JWT_SECRET`
   * environment variable (see artifacts/syrabit-backend/auth_deps.py).
   * Bind via `wrangler secret put JWT_SECRET`. When absent, every
   * caller is treated as anonymous and the cap falls back to anon-id.
   */
  JWT_SECRET?: string;
  /**
   * Task #511 — KV namespace mirroring the artifacts edge worker's
   * `CF_EDGE_CACHE` binding. The deployed worker doesn't own any
   * /api/edge/kv-cache routes today, but if/when it does we want the
   * per-isolate counter under-reporting fix from Task #454 to be
   * already wired (shared-key flush + list+sum aggregation in
   * kv-monitor.ts). Wrapping the binding through `wrapKvNamespace`
   * causes every read/write/list/delete to be counted, periodically
   * flushed to `__kv_usage:CF_EDGE_CACHE:<day>:<isolate-id>`, and
   * summed across isolates by `getUsageSnapshotAggregated` on the
   * /api/edge/kv-usage probe. Optional so the worker still boots
   * without the binding declared in wrangler.toml.
   */
  CF_EDGE_CACHE?: KVNamespace;
  CONTENT_DB: D1Database;
  D1_SYNC_SECRET: string;
  /** Secret shared with the FastAPI backend for /admin/kv-alerts. */
  KV_ALERT_SECRET?: string;
  /** Override warning threshold (percentage of quota). Defaults to 80. */
  KV_WARNING_PCT?: string;
  /** Override per-op daily quotas as a JSON string. */
  KV_QUOTA?: string;
  /**
   * Task #606: Shared secret injected as `X-Origin-Auth` on every backend
   * fetch when the worker is forwarding to a Cloud Run origin. The Cloud
   * Run service rejects requests without it (see
   * `OriginSharedSecretMiddleware` in artifacts/syrabit-backend/middleware.py).
   * Set via `wrangler secret put BACKEND_ORIGIN_SECRET`. Leave unset for
   * non-Cloud-Run backends — the worker just skips the header.
   */
  BACKEND_ORIGIN_SECRET?: string;
  /**
   * Task #636 — Workers AI binding for the auto-fallback fan-out. The
   * routes in `handleAiFallback` call `env.AI.run(model, payload)` only
   * after the FastAPI backend has decided its primary provider failed
   * with a retryable error. The binding is omitted in `wrangler dev`
   * unless --remote or [ai] is configured; routes return 503 in that
   * case so the backend just propagates the original primary error.
   */
  AI?: { run(model: string, payload: unknown, options?: { gateway?: { id: string; metadata?: Record<string, string> } }): Promise<unknown> };
  /**
   * Shared secret with the FastAPI backend, sent as `X-Edge-AI-Secret`
   * on every /api/ai/fallback/* call. Without it the routes 401.
   */
  EDGE_AI_FALLBACK_SECRET?: string;
  /**
   * Task #306 — AI Gateway routing for Workers AI calls.
   * When set, all `env.AI.run(...)` invocations from this worker are
   * routed through the named AI Gateway with a `metadata.tag` of
   * `workers-ai-fallback` (the /api/ai/fallback/* path) or
   * `workers-ai-edge-vector-search` (the /api/edge/vector-search path).
   * The tag flows into the Cloudflare invoice line items so the Workers
   * AI credit draw is cleanly separated from R2 / D1 / KV / Vectorize
   * burn during the monthly cost review (see
   * docs/cloudflare-cost-map.md). When unset, calls go direct — no
   * change in behaviour, just no tag on the invoice.
   *
   * AI Gateway also gives us free response caching for deterministic
   * prompts (embeddings, classification, repeat student questions),
   * which avoids re-billing the $5k credit pool on cache hits. The
   * cache TTL is configured per gateway in the dashboard, not here.
   */
  WORKERS_AI_GATEWAY_ID?: string;
  /**
   * Task #708 — synthetic external probe of /api/admin/diagnostics. See
   * src/synthetic-probe.ts and docs/CLOUDFLARE_ZERO_TRUST.md §7.1 for
   * the full configuration matrix and the rotation procedure.
   */
  SYNTHETIC_PROBE_DISABLED?: string;
  SYNTHETIC_PROBE_TARGET_URL?: string;
  SYNTHETIC_PROBE_CF_ACCESS_CLIENT_ID?: string;
  SYNTHETIC_PROBE_CF_ACCESS_CLIENT_SECRET?: string;
  SYNTHETIC_PROBE_ADMIN_JWT?: string;
  SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL?: string;
  SYNTHETIC_PROBE_WATCHDOG_THRESHOLD_MIN?: string;
  /**
   * Task #817 — public-homepage Cloudflare-block detection probe. See
   * src/cf-block-probe.ts and docs/CLOUDFLARE_ZERO_TRUST.md §8 for the
   * full rationale (catches WAF / Bot Fight / custom-firewall false
   * positives that the admin-diagnostics probe is blind to). Re-uses
   * SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL for alerts.
   */
  CF_BLOCK_PROBE_DISABLED?: string;
  CF_BLOCK_PROBE_TARGET_URL?: string;
  CF_BLOCK_PROBE_THRESHOLD?: string;
  /**
   * Task #898 — bot-cache hit-rate / fallback-rate watchdog. Reads
   * the `bot_cache.*` counters that Task #885 surfaces under
   * `/api/edge/kv-usage` and pages the on-call when the rolling
   * 15-minute hit-rate drops by ≥30pp vs the prior 15 minutes, OR
   * the fallback rate sits above ~10%. Re-uses
   * SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL so on-call sees a single
   * "edge layer is degraded" channel. See src/bot-cache-alert.ts.
   */
  BOT_CACHE_ALERT_DISABLED?: string;
  BOT_CACHE_ALERT_DROP_PCT?: string;
  BOT_CACHE_ALERT_FALLBACK_PCT?: string;
  BOT_CACHE_ALERT_MIN_SAMPLE?: string;
  BOT_CACHE_ALERT_WINDOW_BUCKETS?: string;
  /**
   * Task #311 — AI Gateway 24h embed cache-hit-rate watchdog. Pages
   * on-call when the rolling 24h hit-rate for embed-tagged requests
   * through `syrabit-ai-gw` falls below the floor documented in
   * docs/ops/ai-gateway-activation.md (~50%). Re-uses
   * SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL with a distinct alert_type so
   * the receiver can route it independently. See
   * src/ai-gateway-cache-alert.ts. AI_GATEWAY_ANALYTICS_TOKEN must be
   * set as a Wrangler secret with `AI Gateway: Read` scope; without
   * it the watchdog skips silently.
   */
  AI_GATEWAY_CACHE_ALERT_DISABLED?: string;
  AI_GATEWAY_CACHE_HIT_RATE_FLOOR_PCT?: string;
  AI_GATEWAY_CACHE_ALERT_MIN_SAMPLE?: string;
  AI_GATEWAY_CACHE_ALERT_EMBED_TAG?: string;
  AI_GATEWAY_CACHE_ALERT_QUERY_FAIL_THRESHOLD?: string;
  AI_GATEWAY_ANALYTICS_TOKEN?: string;
  /**
   * Enterprise Vectorize binding — enabled in wrangler.toml for edge-side
   * semantic search without a backend round-trip.
   *   SYLLABUS_INDEX → syllabus-index-v2 (1024-dim, cosine, Gemini)
   *
   * The legacy 768-dim `SYLLABUS_INDEX_LEGACY` binding was retired in
   * Task #308 along with the underlying `syllabus-index` Vectorize index.
   */
  SYLLABUS_INDEX?: VectorizeIndex;
  /**
   * Task #108 — Phase 4: R2 student asset storage.
   * Bound to the syrabit-assets bucket. Admins upload PDFs via
   * POST /admin/assets/upload; files are served at assets.syrabit.ai/<key>.
   * The binding is optional so the worker degrades gracefully if the bucket
   * hasn't been provisioned yet (returns 503 on the upload route).
   */
  ASSETS?: R2Bucket;
  /**
   * Task #314 — R2 binding for the `syrabit-media` bucket. Used by the
   * monthly R2 cold-storage / Logpush-cap watchdog to walk the
   * `logpush/` prefix and sum object sizes (no per-prefix dimension is
   * exposed via the GraphQL Analytics API). Optional so the worker
   * still boots in local dev / before the bucket is provisioned —
   * the watchdog skips the Logpush-cap signal when the binding is
   * absent.
   */
  R2_MEDIA?: R2Bucket;
  /**
   * Task #314 — env vars for the monthly R2 cold-storage watchdog.
   * See workers/edge-proxy/src/r2-storage-class-alert.ts for the full
   * configuration matrix and runbook pointers.
   */
  R2_STORAGE_ALERT_DISABLED?: string;
  R2_LIFECYCLE_RULES_APPLIED_AT?: string;
  R2_STORAGE_ALERT_LOGPUSH_CAP_GB?: string;
  R2_STORAGE_ALERT_BUCKETS?: string;
  /** Task #316 — N consecutive query failures before the secondary
   *  "watchdog blind" alert pages. Surfaced to the admin tile via
   *  /api/edge/r2-storage-health (Task #319) so the indicator can
   *  render the right-coloured badge. */
  R2_STORAGE_ALERT_QUERY_FAIL_THRESHOLD?: string;
  R2_STORAGE_ANALYTICS_TOKEN?: string;
  /**
   * Task: D1 Cache Warming on Startup — preload hot content into D1/KV cache
   * when the worker starts to eliminate cold-start latency (~10-50ms → ~0ms).
   * When true, the scheduled handler runs an immediate warm-up on first boot.
   */
  D1_WARM_ON_STARTUP?: string;
  /**
   * Task #109 Phase 5 — Workers Analytics Engine dataset binding.
   * Writes per-request metrics (cache hit/miss, chapter ID, AI provider,
   * response time, rate-limit result) to the "syrabit-edge-metrics" dataset.
   * Declared in wrangler.toml [analytics_engine_datasets]. Optional so the
   * worker degrades gracefully in local dev without the binding.
   */
  ANALYTICS?: AnalyticsEngineDataset;
  /**
   * Task #109 Phase 5 — Durable Object rate-limiter namespace.
   * Provides strongly-consistent, per-key sliding-window rate limiting.
   * Falls back to KV-based checkRateLimitKey() when unbound (e.g. before
   * the [[migrations]] have been applied via `wrangler deploy`).
   */
  RATE_LIMITER_DO?: DurableObjectNamespace;
  /**
   * Task #575 — SeasonCacheDO owns the authoritative `/api/health/season`
   * snapshot for the entire region. Single instance per region (resolved
   * via `idFromName("global")`) enforces the 60s shared-refresh contract
   * so the FastAPI origin sees one call per minute regardless of isolate
   * count. Optional so the worker still boots in local dev without the
   * `[[migrations]] tag = "v2"` applied — when unbound, season-aware TTL
   * stretching is a no-op (every route gets its normal `ttl_seconds`).
   */
  SEASON_CACHE_DO?: DurableObjectNamespace;
  /**
   * Task #109 Phase 5 — Cloudflare API token with Analytics: Read scope.
   * Used by the /api/edge/analytics route to query the Analytics Engine
   * SQL API and return edge metrics to the admin panel.
   * Set via: wrangler secret put CF_ANALYTICS_TOKEN
   */
  CF_ANALYTICS_TOKEN?: string;
  /**
   * Task #110 Phase 6 — mTLS client certificate binding for Railway origin.
   * When bound, proxyToBackend() calls env.MTLS_CERT.fetch() instead of the
   * global fetch() so Cloudflare automatically presents the client certificate
   * on the TLS handshake with the Railway backend.
   * Declared in wrangler.toml [[mtls_certificates]].
   * Optional so the worker degrades gracefully in local dev / before the cert
   * is issued (falls back to plain fetch, which still sends BACKEND_ORIGIN_SECRET).
   */
  MTLS_CERT?: { fetch(input: RequestInfo, init?: RequestInit): Promise<Response> };
  /**
   * Task #110 Phase 6 — mTLS enforcement gate.
   * Set to "true" (via `wrangler secret put MTLS_REQUIRED`) once the mTLS cert
   * has been provisioned AND Railway has been configured to require it.
   * When "true" and MTLS_CERT is not bound, proxyToBackend() returns a 503
   * instead of falling back to plain fetch — closes the insecure bypass path.
   * Leave unset (or "false") in local dev and before the cert is active.
   */
  MTLS_REQUIRED?: string;
  /**
   * Task #13 / Task #32 — integer string; SPA title-miss paths whose bot-hit
   * count equals or exceeds this value are included in the gap alert and the
   * on-demand /api/edge/spa-title-misses response. Default: 50.
   * Set via: wrangler secret put SPA_TITLE_MISS_ALERT_THRESHOLD
   */
  SPA_TITLE_MISS_ALERT_THRESHOLD?: string;
  /** Task #13 — set to "true" to disable the nightly SPA title-miss alert. */
  SPA_TITLE_MISS_ALERT_DISABLED?: string;
}

const KV_BINDINGS = ["RATE_LIMIT", "BOT_HTML_CACHE", "CF_EDGE_CACHE"] as const;

function buildKvMonitorOpts(env: Env, ctx: ExecutionContext): WrapKvOptions {
  let quota: Partial<KvUsageQuota> | undefined;
  if (env.KV_QUOTA) {
    try { quota = JSON.parse(env.KV_QUOTA); } catch { /* ignore malformed override */ }
  }
  let warningPct: number | undefined;
  if (env.KV_WARNING_PCT) {
    const n = Number(env.KV_WARNING_PCT);
    if (Number.isFinite(n) && n > 0 && n <= 100) warningPct = n;
  }
  return {
    backendUrl: env.BACKEND_URL,
    alertSecret: env.KV_ALERT_SECRET,
    warningPct,
    quota,
    ctx,
  };
}

function wrapEnvKv(env: Env, ctx: ExecutionContext): Env {
  const opts = buildKvMonitorOpts(env, ctx);
  // Idempotent: only wrap actual `KVNamespace` instances. The wrapper
  // uses module-scoped counters keyed by binding name, so re-wrapping
  // across requests is safe and cheap.
  const wrapped: Env = { ...env };
  if (env.RATE_LIMIT) {
    wrapped.RATE_LIMIT = wrapKvNamespace(env.RATE_LIMIT, "RATE_LIMIT", opts);
  }
  if (env.BOT_HTML_CACHE) {
    wrapped.BOT_HTML_CACHE = wrapKvNamespace(env.BOT_HTML_CACHE, "BOT_HTML_CACHE", opts);
  }
  // Task #511 — wrap CF_EDGE_CACHE through the same monitor so its
  // counters get the cross-isolate aggregation treatment from Task #454.
  if (env.CF_EDGE_CACHE) {
    wrapped.CF_EDGE_CACHE = wrapKvNamespace(env.CF_EDGE_CACHE, "CF_EDGE_CACHE", opts);
  }
  return wrapped;
}

async function handleKvUsage(env: Env, request: Request, cors: Record<string, string>): Promise<Response> {
  const provided = request.headers.get("X-Edge-Admin-Secret") || "";
  if (!env.D1_SYNC_SECRET || provided !== env.D1_SYNC_SECRET) {
    return new Response(JSON.stringify({ detail: "Unauthorized" }), {
      status: 401,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }
  // Use the aggregated snapshot so the dashboard shows global Worker
  // usage (sum across all isolates that have flushed to the shared
  // `__kv_usage:*` keys), not just this isolate's slice.
  const opts = buildKvMonitorOpts(env, {
    waitUntil: () => undefined,
    passThroughOnException: () => undefined,
  } as unknown as ExecutionContext);
  const bindingArgs: Array<{ binding: string; kv: KVNamespace }> = [];
  // NOTE: env was already wrapped by `wrapEnvKv` for the request, but
  // the underlying KV bindings on the original env object are what we
  // want for the shared-store reads/writes (so they don't recurse
  // through the monitor wrapper). The wrapper does not mutate the
  // original env, so we'd have to access the raw bindings — but here
  // env is the WRAPPED env. Calling list/get on the wrapper still
  // works; the wrapper just counts them too (a small, predictable
  // overhead for the snapshot endpoint).
  if (env.RATE_LIMIT) bindingArgs.push({ binding: "RATE_LIMIT", kv: env.RATE_LIMIT });
  if (env.BOT_HTML_CACHE) bindingArgs.push({ binding: "BOT_HTML_CACHE", kv: env.BOT_HTML_CACHE });
  // Task #511 — include CF_EDGE_CACHE so its counters get the same
  // cross-isolate flush+list+sum treatment as the other bindings.
  if (env.CF_EDGE_CACHE) bindingArgs.push({ binding: "CF_EDGE_CACHE", kv: env.CF_EDGE_CACHE });
  let snapshot;
  try {
    snapshot = await getUsageSnapshotAggregated(bindingArgs, opts);
  } catch {
    snapshot = getUsageSnapshot([...KV_BINDINGS], opts);
  }
  // Task #885 — bot HTML cache hit/miss/304/fallback observability.
  // Surfaced under `bot_cache:` so a deploy that drifts the cache key
  // (silently dropping hit-rate from ~95% to 0%) is visible in the
  // admin dashboard within one bucket window.
  let botCache: BotCacheStats | null = null;
  if (env.RATE_LIMIT) {
    try {
      botCache = await getBotCacheStats(env.RATE_LIMIT);
    } catch {
      /* keep the rest of the response usable on a stats read failure */
    }
  }
  const body = { ...snapshot, bot_cache: botCache };
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: {
      ...cors,
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      "X-Source": "edge-kv-monitor",
    },
  });
}

/**
 * Task #315 — KV key behind the manual `/api/edge/r2-storage-health/run`
 * cooldown gate. The 28-day cooldown inside `runR2StorageClassAlert`
 * already prevents duplicate paging; this gate exists so a stuck
 * "Re-evaluate now" button (or a malicious admin replay) cannot burn
 * an unbounded number of GraphQL + R2-list calls. 60 s is short enough
 * that an operator who just re-applied the lifecycle rules can still
 * verify within a normal incident window, and long enough that holding
 * the button down does not accumulate cost.
 */
const R2_HEALTH_RUN_COOLDOWN_KEY = "r2_storage_class_alert:manual_run_at";
const R2_HEALTH_RUN_COOLDOWN_S = 60;

async function handleR2StorageHealth(
  env: Env,
  request: Request,
  cors: Record<string, string>,
): Promise<Response> {
  const provided = request.headers.get("X-Edge-Admin-Secret") || "";
  if (!env.D1_SYNC_SECRET || provided !== env.D1_SYNC_SECRET) {
    return new Response(JSON.stringify({ detail: "Unauthorized" }), {
      status: 401,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }
  if (!env.RATE_LIMIT) {
    return new Response(
      JSON.stringify({
        configured: false,
        reason: "RATE_LIMIT KV binding not bound on the worker",
        state: null,
      }),
      {
        status: 200,
        headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" },
      },
    );
  }
  const state = await readR2StorageClassAlertState(env.RATE_LIMIT);
  // Surface the configured cap + rules-applied date so the admin tile
  // can compute "rules age (days)" and "Logpush GB / cap" without a
  // second round-trip to the worker.
  const logpushCapGb = (() => {
    const raw = env.R2_STORAGE_ALERT_LOGPUSH_CAP_GB;
    const n = raw ? Number(raw) : 5;
    return Number.isFinite(n) && n > 0 ? n : 5;
  })();
  const rulesAppliedAt = env.R2_LIFECYCLE_RULES_APPLIED_AT || null;
  let rulesAgeDays: number | null = null;
  if (rulesAppliedAt) {
    const t = Date.parse(rulesAppliedAt);
    if (Number.isFinite(t)) {
      rulesAgeDays = Math.max(0, Math.floor((Date.now() - t) / (24 * 60 * 60 * 1000)));
    }
  }
  const buckets = (env.R2_STORAGE_ALERT_BUCKETS || "syrabit-assets,syrabit-media")
    .split(",").map((s) => s.trim()).filter(Boolean);
  const disabled = (env.R2_STORAGE_ALERT_DISABLED || "").toLowerCase() === "true";
  // Surface the configured "watchdog blind" threshold (Task #316) so the
  // admin tile (Task #319) can render the running counter as yellow at
  // ≥1 and red once it crosses the threshold without having to mirror
  // the worker's default in the frontend.
  const queryFailThreshold = (() => {
    const raw = env.R2_STORAGE_ALERT_QUERY_FAIL_THRESHOLD;
    const n = raw ? Number(raw) : 2;
    return Number.isFinite(n) && n >= 1 ? Math.floor(n) : 2;
  })();

  return new Response(
    JSON.stringify({
      configured: true,
      disabled,
      buckets,
      logpush_cap_gb: logpushCapGb,
      rules_applied_at: rulesAppliedAt,
      rules_age_days: rulesAgeDays,
      query_fail_threshold: queryFailThreshold,
      state,
    }),
    {
      status: 200,
      headers: {
        ...cors,
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Source": "edge-r2-storage-health",
      },
    },
  );
}

async function handleR2StorageHealthRun(
  env: Env,
  request: Request,
  ctx: ExecutionContext,
  cors: Record<string, string>,
): Promise<Response> {
  const provided = request.headers.get("X-Edge-Admin-Secret") || "";
  if (!env.D1_SYNC_SECRET || provided !== env.D1_SYNC_SECRET) {
    return new Response(JSON.stringify({ detail: "Unauthorized" }), {
      status: 401,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }
  if (!env.RATE_LIMIT) {
    return new Response(
      JSON.stringify({ ok: false, reason: "no_kv_binding" }),
      { status: 503, headers: { ...cors, "Content-Type": "application/json" } },
    );
  }
  // Cooldown gate. Read-modify-write on a single key — race-free enough
  // for a manual admin button (KV's eventual consistency at worst lets
  // two clicks within the same isolate window through, which is fine;
  // the 28-day fire cooldown inside the alert module catches actual
  // duplicate paging).
  try {
    const lastRaw = await env.RATE_LIMIT.get(R2_HEALTH_RUN_COOLDOWN_KEY);
    const nowMs = Date.now();
    const lastMs = lastRaw ? Number(lastRaw) : 0;
    if (lastMs && nowMs - lastMs < R2_HEALTH_RUN_COOLDOWN_S * 1000) {
      const retryAfter = Math.ceil(
        (R2_HEALTH_RUN_COOLDOWN_S * 1000 - (nowMs - lastMs)) / 1000,
      );
      return new Response(
        JSON.stringify({
          ok: false,
          reason: "cooldown",
          retry_after_seconds: retryAfter,
        }),
        {
          status: 429,
          headers: {
            ...cors,
            "Content-Type": "application/json",
            "Retry-After": String(retryAfter),
          },
        },
      );
    }
    await env.RATE_LIMIT.put(R2_HEALTH_RUN_COOLDOWN_KEY, String(nowMs), {
      expirationTtl: Math.max(60, R2_HEALTH_RUN_COOLDOWN_S * 4),
    });
  } catch {
    /* if KV is unhealthy the request still proceeds — the watchdog
       will skip with no_kv_binding / query_failed and the response
       below surfaces that cleanly to the admin */
  }

  const result = await runR2StorageClassAlert(env);
  // Re-read the persisted state so the caller gets the canonical
  // "what's now stored" view (last_evaluated_at / last_*_gb fields)
  // without the UI having to make a second GET round-trip.
  const state = await readR2StorageClassAlertState(env.RATE_LIMIT);
  void ctx;

  return new Response(
    JSON.stringify({ ok: true, result, state }),
    {
      status: 200,
      headers: {
        ...cors,
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Source": "edge-r2-storage-health",
      },
    },
  );
}

/**
 * Task #322 — companion to {@link handleR2StorageHealthRun}. Clears
 * the secondary `consecutive_query_failures` + `query_fail_last_fired_at`
 * fields persisted by the Task #316 watchdog so an operator who has just
 * rotated `R2_STORAGE_ANALYTICS_TOKEN` can dismiss the red badge on the
 * admin tile immediately, instead of waiting up to ~30 days for the next
 * monthly evaluation to confirm the fix.
 *
 * Auth: same `X-Edge-Admin-Secret` handshake as the read + run endpoints.
 * No cooldown — the operation is idempotent and side-effect-free outside
 * of KV (no GraphQL / R2-list calls), so spamming it cannot burn budget
 * or fire pages.
 */
async function handleR2StorageHealthResetWatchdog(
  env: Env,
  request: Request,
  cors: Record<string, string>,
): Promise<Response> {
  const provided = request.headers.get("X-Edge-Admin-Secret") || "";
  if (!env.D1_SYNC_SECRET || provided !== env.D1_SYNC_SECRET) {
    return new Response(JSON.stringify({ detail: "Unauthorized" }), {
      status: 401,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }
  if (!env.RATE_LIMIT) {
    return new Response(
      JSON.stringify({ ok: false, reason: "no_kv_binding" }),
      { status: 503, headers: { ...cors, "Content-Type": "application/json" } },
    );
  }
  const state = await resetR2StorageWatchdogBlindCounter(env.RATE_LIMIT);
  // Audit log so the rotation can be traced post-incident. The handshake
  // already gates this on a shared secret an admin must possess; we don't
  // have an operator identity at the worker layer (the backend does and
  // logs it on its side), so we just record the action + timestamp.
  console.log(
    `[r2-storage-class-alert] manual watchdog-blind reset by admin ` +
      `at ${new Date().toISOString()}`,
  );
  return new Response(
    JSON.stringify({ ok: true, state }),
    {
      status: 200,
      headers: {
        ...cors,
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        "X-Source": "edge-r2-storage-health",
      },
    },
  );
}

interface D1Database {
  prepare(query: string): D1PreparedStatement;
  batch<T = unknown>(statements: D1PreparedStatement[]): Promise<D1Result<T>[]>;
  exec(query: string): Promise<D1ExecResult>;
}
interface D1PreparedStatement {
  bind(...values: unknown[]): D1PreparedStatement;
  first<T = unknown>(colName?: string): Promise<T | null>;
  run<T = unknown>(): Promise<D1Result<T>>;
  all<T = unknown>(): Promise<D1Result<T>>;
  raw<T = unknown[]>(options?: { columnNames?: boolean }): Promise<T[]>;
}
interface D1Result<T = unknown> { results: T[]; success: boolean; meta: object }
interface D1ExecResult { count: number; duration: number }


const ALLOWED_ORIGINS = [
  "https://syrabit.ai",
  "https://www.syrabit.ai",
  "https://api.syrabit.ai",
];

// ─────────────────────────────────────────────────────────────────────────────
// EDGE CACHE KEY AUDIT — source of truth: workers/edge-proxy/monitored-urls.json
//
// The CACHEABLE_PREFIXES / CACHE_TTL_ENTRIES / BYPASS_PREFIXES /
// USER_SPECIFIC_PREFIXES constants below are projected at module load
// from `monitored-urls.json` via `monitored-urls.ts`. The JSON manifest
// is gated by `tests/test_monitoring_url_drift.py` against the live
// FastAPI OpenAPI schema, so a renamed backend route fails CI with an
// actionable message instead of silently bypassing the edge cache for
// weeks (Task #900 — the same drift class as Task #877).
//
// To add / change a cache rule:
//   1. Edit `workers/edge-proxy/monitored-urls.json` — add or update the
//      `edge_cache` block on the relevant `backend_paths` entry.
//   2. The runtime constants below pick the change up automatically;
//      no edit to this file is needed.
//
// Route families NOT listed in the manifest are intentionally excluded
// (admin / analytics / conversations / notifications / non-stats user
// routes are auth-gated or user-specific; /api/health and /api/livez
// are computed live by the worker; /api/ai/* non-chat is rate-limited
// via isAiPath() and never cached). Do not add them here — list them
// in `monitored-urls.json` if a real cache decision is being made.
// ─────────────────────────────────────────────────────────────────────────────
const CACHEABLE_PREFIXES = getCacheablePrefixes();
const CACHE_TTL_ENTRIES = getCacheTtlEntries();
// Task #575 — exam-mode TTL overrides loaded from `monitored-urls.json`
// (`edge_cache.exam_ttl_seconds`). Sorted by descending prefix length
// so the most specific entry wins, same contract as `CACHE_TTL_ENTRIES`.
const EXAM_CACHE_TTL_ENTRIES = getExamCacheTtlEntries();
// Task #575 — populated at the top of every fetch invocation by
// `refreshSeasonSnapshot(env, ctx)`. Reads are racy across concurrent
// requests in the same isolate but only ever flip between
// {normal, exam, results} so a brief desync is harmless: every request
// either gets the old or the new TTL, never a mid-tuple value.
let _currentSeasonSnapshot: SeasonSnapshot = {
  season: "normal",
  ttl_multiplier: 1.0,
  fetched_at_ms: 0,
};

async function refreshSeasonSnapshot(
  env: { SEASON_CACHE_DO?: DurableObjectNamespace },
  ctx: { waitUntil(promise: Promise<unknown>): void },
): Promise<void> {
  try {
    _currentSeasonSnapshot = await getSeasonSnapshot(env, ctx);
  } catch {
    // Already returns a fallback — nothing to do.
  }
}
const USER_SPECIFIC_PREFIXES = getUserSpecificPrefixes();
const BYPASS_PREFIXES = getBypassPrefixes();

const RATE_LIMIT_RPM = 120;
const BOT_RATE_LIMIT_RPM = 3000;
const RATE_LIMIT_WINDOW_S = 60;
const AI_RATE_LIMIT_RPM = 60;
const AI_RATE_LIMIT_PREFIXES = ["/api/ai/chat", "/api/ai/generate", "/api/ai/grounded", "/api/ai/explain", "/api/ai/quiz", "/api/ai/summarize", "/api/chat"];

// ─── Task #33 — SPA title-miss alert runtime settings (KV-tunable) ───────────
// Admins can change the threshold and disabled flag from the dashboard without
// deploying a new worker. Settings are stored in RATE_LIMIT KV and take
// priority over the wrangler vars (SPA_TITLE_MISS_ALERT_THRESHOLD,
// SPA_TITLE_MISS_ALERT_DISABLED), which remain as last-resort defaults.

const _SPA_TITLE_MISS_SETTINGS_KEY = "spa_title_miss:settings";

interface _SpaTitleMissKvSettings {
  threshold: number;
  disabled:  boolean;
}

async function _readSpaTitleMissKvSettings(
  kv: KVNamespace | undefined,
  env: Pick<Env, "SPA_TITLE_MISS_ALERT_THRESHOLD" | "SPA_TITLE_MISS_ALERT_DISABLED">,
): Promise<_SpaTitleMissKvSettings> {
  const envThreshold = (() => {
    const raw = env.SPA_TITLE_MISS_ALERT_THRESHOLD;
    if (!raw) return 50;
    const n = Number(raw);
    return Number.isFinite(n) && n >= 1 ? Math.floor(n) : 50;
  })();
  const envDisabled = env.SPA_TITLE_MISS_ALERT_DISABLED?.toLowerCase() === "true";

  if (!kv) return { threshold: envThreshold, disabled: envDisabled };
  try {
    const raw = await kv.get(_SPA_TITLE_MISS_SETTINGS_KEY);
    if (!raw) return { threshold: envThreshold, disabled: envDisabled };
    const parsed = JSON.parse(raw) as Partial<_SpaTitleMissKvSettings>;
    return {
      threshold: typeof parsed.threshold === "number" && parsed.threshold >= 1
        ? Math.floor(parsed.threshold) : envThreshold,
      disabled: typeof parsed.disabled === "boolean" ? parsed.disabled : envDisabled,
    };
  } catch {
    return { threshold: envThreshold, disabled: envDisabled };
  }
}

// ─── Task #513 §A — chat-cap (per-user monthly hard + daily soft) ─────────
// Chat dispatch is the single most expensive call type per request
// (Azure gpt-4.1-nano hot path). The cap is enforced AT THE EDGE so
// an abusive client never reaches the FastAPI origin in the first
// place. Identity is the JWT-verified `sub` (user_id) claim; an
// unauthenticated caller falls back to anon-id, then IP. Plan is the
// JWT-verified `plan` claim — an attacker-supplied `x-plan` header
// is IGNORED.
//
// Two windows per identity:
//   - Monthly hard cap (`CHAT_CAP_MONTHLY=30`) applies to EVERYONE
//     (free + paid) and rolls over at the 1st of the next UTC month.
//     Key: `chat-budget:<id>:<YYYY-MM>` (TTL ≈ 32 days).
//   - Daily soft cap (`CHAT_CAP_DAILY=20`) applies to FREE users only.
//     Paid plans bypass the daily cap (their monthly hard cap is the
//     only edge limit). Key: `chat-daily:<id>:<YYYY-MM-DD>` (TTL ≈
//     25 h).
//
// Increments fire AFTER the origin returns a success status (<400)
// so a 4xx/5xx that the client retries does not consume a turn.
const CHAT_CAP_MONTHLY = 30;
const CHAT_CAP_DAILY = 20;
// Coverage (Task #513 §A round-7 narrowed): the chat budget gates
// CHAT verbs only — `/api/ai/chat`, `/api/chat`, and the
// `/api/edu/study/*` chat-equivalents (chat / qa / explain). The
// other AI routes (`generate`, `grounded`, `quiz`, `summarize`)
// remain on the standard 30 RPM AI rate-limit but DO NOT consume
// the per-user chat cap, because the per-call cost-cap clamp in
// `cost_caps.TOKEN_BUDGETS` already pins their token spend and
// the cap was specified for the chat hot-path only. Re-broadening
// CHAT_CAP_PATHS requires a Sentry-annotated changelog entry per
// the COST-CAP-OVERRIDE convention.
const CHAT_CAP_PATHS = [
  "/api/ai/chat",
  "/api/chat",
  "/api/edu/study/chat",
  "/api/edu/study/qa",
  "/api/edu/study/explain",
];
// Paid plans bypass only the DAILY soft cap. Monthly cap still
// applies to enforce per-user spend.
const CHAT_DAILY_BYPASS_PLANS = new Set(["pro", "student-plus", "student_plus", "premium", "enterprise"]);

function isChatPath(p: string): boolean {
  return CHAT_CAP_PATHS.some((x) => p.startsWith(x));
}

function _utcDayKey(): string {
  const d = new Date();
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}-${String(d.getUTCDate()).padStart(2, "0")}`;
}

function _utcMonthKey(): string {
  const d = new Date();
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

function _nextUtcMidnightDate(): Date {
  const now = new Date();
  return new Date(Date.UTC(
    now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1,
    0, 0, 0, 0,
  ));
}

function _nextUtcMonthStartDate(): Date {
  const now = new Date();
  return new Date(Date.UTC(
    now.getUTCFullYear(), now.getUTCMonth() + 1, 1,
    0, 0, 0, 0,
  ));
}

function _secondsToNextUtcMidnight(): number {
  return Math.max(1, Math.floor((_nextUtcMidnightDate().getTime() - Date.now()) / 1000));
}

function _secondsToNextUtcMonthStart(): number {
  return Math.max(1, Math.floor((_nextUtcMonthStartDate().getTime() - Date.now()) / 1000));
}

function _isPaidPlan(plan: string): boolean {
  // Task #513 §A round-3 — broaden the daily-cap bypass per spec:
  // ANY non-empty plan that is not literally "free" qualifies as
  // paid. The fixed allowlist is kept as documentation only — every
  // tier we ship today (`pro`, `student-plus`, `premium`, etc.) is
  // already covered, and any future paid tier will inherit the
  // bypass automatically without a worker redeploy.
  if (!plan) return false;
  const norm = plan.trim().toLowerCase();
  if (!norm || norm === "free" || norm === "anonymous" || norm === "anon") return false;
  return true;
}

// ─── HMAC-SHA256 JWT verification (HS256) ─────────────────────────────────
// Backend uses python-jose HS256 with `JWT_SECRET`. We perform full
// signature verification at the edge so we can trust `sub` (user_id)
// and `plan` claims. JWT_SECRET MUST be bound on the worker
// (wrangler secret put JWT_SECRET); when absent, we treat the caller
// as anonymous and fall back to anon-id. We do NOT trust an
// unverified bearer token.
function _b64urlToUint8(s: string): Uint8Array {
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  const b64 = (s + pad).replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function _b64urlDecodeJson(s: string): Record<string, unknown> | null {
  try {
    const txt = new TextDecoder().decode(_b64urlToUint8(s));
    return JSON.parse(txt) as Record<string, unknown>;
  } catch {
    return null;
  }
}

const _HMAC_KEY_CACHE = new Map<string, CryptoKey>();
async function _importHmacKey(secret: string): Promise<CryptoKey> {
  const cached = _HMAC_KEY_CACHE.get(secret);
  if (cached) return cached;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"],
  );
  _HMAC_KEY_CACHE.set(secret, key);
  return key;
}

interface VerifiedJwt { user_id: string; plan: string; }

async function verifyJwtFromRequest(
  request: Request, env: Env,
): Promise<VerifiedJwt | null> {
  const auth = request.headers.get("Authorization") || "";
  if (!auth.startsWith("Bearer ")) return null;
  const token = auth.slice(7).trim();
  if (!token) return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const [headerB64, payloadB64, sigB64] = parts;
  const header = _b64urlDecodeJson(headerB64);
  if (!header || header.alg !== "HS256") return null;

  const secret = (env as Env & { JWT_SECRET?: string }).JWT_SECRET;
  if (!secret) {
    // No JWT secret bound — treat caller as anonymous. Do NOT trust
    // any claims from an unverified token.
    return null;
  }
  let ok = false;
  try {
    const key = await _importHmacKey(secret);
    const sig = _b64urlToUint8(sigB64);
    const data = new TextEncoder().encode(`${headerB64}.${payloadB64}`);
    ok = await crypto.subtle.verify("HMAC", key, sig, data);
  } catch {
    ok = false;
  }
  if (!ok) return null;

  const payload = _b64urlDecodeJson(payloadB64);
  if (!payload) return null;
  // Expiry check
  const exp = typeof payload.exp === "number" ? payload.exp : 0;
  if (exp > 0 && Date.now() / 1000 > exp) return null;
  const sub = typeof payload.sub === "string" ? payload.sub : "";
  if (!sub) return null;
  const plan = typeof payload.plan === "string" ? payload.plan : "free";
  return { user_id: sub, plan };
}

async function _kvIncr(env: Env, key: string, ttlSec: number): Promise<number> {
  if (!env.RATE_LIMIT) return 0;
  try {
    const raw = await env.RATE_LIMIT.get(key);
    const n = (parseInt(raw || "0", 10) || 0) + 1;
    await env.RATE_LIMIT.put(key, String(n), { expirationTtl: ttlSec });
    return n;
  } catch {
    return 0;
  }
}

interface ChatCapPrecheck {
  allowed: boolean;
  error?: "chat_budget_exhausted" | "chat_daily_soft_cap";
  limit?: number;
  window?: "month" | "day";
  reset?: string;             // ISO-8601 UTC timestamp of window rollover.
  retry_after?: number;       // Seconds — exact distance to `reset`.
  remaining_month: number;
  remaining_day: number;
  dayKey: string;             // Returned so the caller can bump on success.
  monthKey: string;
  bypassed: boolean;          // True when KV is unavailable.
  identity_kind: "user" | "anon" | "ip";
  paid: boolean;
}

async function precheckChatCap(
  env: Env, identity: string, identityKind: "user" | "anon" | "ip", paid: boolean,
): Promise<ChatCapPrecheck> {
  const dayKey = `chat-daily:${identity}:${_utcDayKey()}`;
  const monthKey = `chat-budget:${identity}:${_utcMonthKey()}`;

  const baseAllowed = (rm: number, rd: number): ChatCapPrecheck => ({
    allowed: true, remaining_month: rm, remaining_day: rd,
    dayKey, monthKey, bypassed: false, identity_kind: identityKind, paid,
  });

  if (!env.RATE_LIMIT) {
    return {
      allowed: true, remaining_month: CHAT_CAP_MONTHLY, remaining_day: CHAT_CAP_DAILY,
      dayKey, monthKey, bypassed: true, identity_kind: identityKind, paid,
    };
  }

  try {
    // Always read the monthly counter; daily only matters for free users.
    const reads: Promise<string | null>[] = [env.RATE_LIMIT.get(monthKey)];
    if (!paid) reads.push(env.RATE_LIMIT.get(dayKey));
    const results = await Promise.all(reads);
    const mCur = parseInt(results[0] || "0", 10) || 0;
    const dCur = paid ? 0 : (parseInt(results[1] || "0", 10) || 0);

    // Monthly hard cap — applies to everyone.
    if (mCur >= CHAT_CAP_MONTHLY) {
      const sec = _secondsToNextUtcMonthStart();
      return {
        allowed: false,
        error: "chat_budget_exhausted",
        limit: CHAT_CAP_MONTHLY,
        window: "month",
        reset: _nextUtcMonthStartDate().toISOString(),
        retry_after: sec,
        remaining_month: 0,
        remaining_day: paid ? CHAT_CAP_DAILY : Math.max(0, CHAT_CAP_DAILY - dCur),
        dayKey, monthKey, bypassed: false, identity_kind: identityKind, paid,
      };
    }
    // Daily soft cap — free users only.
    if (!paid && dCur >= CHAT_CAP_DAILY) {
      const sec = _secondsToNextUtcMidnight();
      return {
        allowed: false,
        error: "chat_daily_soft_cap",
        limit: CHAT_CAP_DAILY,
        window: "day",
        reset: _nextUtcMidnightDate().toISOString(),
        retry_after: sec,
        remaining_month: Math.max(0, CHAT_CAP_MONTHLY - mCur),
        remaining_day: 0,
        dayKey, monthKey, bypassed: false, identity_kind: identityKind, paid,
      };
    }
    return baseAllowed(
      Math.max(0, CHAT_CAP_MONTHLY - mCur),
      paid ? CHAT_CAP_DAILY : Math.max(0, CHAT_CAP_DAILY - dCur),
    );
  } catch {
    return {
      allowed: true, remaining_month: CHAT_CAP_MONTHLY, remaining_day: CHAT_CAP_DAILY,
      dayKey, monthKey, bypassed: true, identity_kind: identityKind, paid,
    };
  }
}

// Per-request context plumbed from the chat-cap precheck site to the
// outer fetch handler so the cap counter can be bumped AFTER the
// origin returns a success status (response.status < 400). Using a
// WeakMap keyed on the Request keeps the lifetime correct (entry is
// reaped automatically when the Request is GC'd).
const _CHAT_CAP_PENDING: WeakMap<Request, { dayKey: string; monthKey: string; paid: boolean }> = new WeakMap();

async function bumpChatCapOnSuccess(
  env: Env, dayKey: string, monthKey: string, paid: boolean,
): Promise<void> {
  // Always bump the monthly counter (cap applies to everyone). Bump
  // the daily counter only for free users (paid bypass the daily
  // soft cap). ~25 h day TTL covers DST drift; ~32 day month TTL
  // covers the longest calendar month.
  if (!env.RATE_LIMIT) return;
  try {
    const writes: Promise<unknown>[] = [_kvIncr(env, monthKey, 32 * 24 * 60 * 60)];
    if (!paid) writes.push(_kvIncr(env, dayKey, 90_000));
    await Promise.all(writes);
  } catch {
    // Best-effort — a KV write failure must NOT block the request that
    // already succeeded at the origin.
  }
}

// Read-only counters for `/api/me/quota` (called via internal CF
// header injection from the worker into the proxied request, so the
// backend can render the user's current spend without an extra KV
// round-trip from inside FastAPI).
async function readChatCapCounters(
  env: Env, identity: string,
): Promise<{ used_month: number; used_day: number }> {
  if (!env.RATE_LIMIT) return { used_month: 0, used_day: 0 };
  const dayKey = `chat-daily:${identity}:${_utcDayKey()}`;
  const monthKey = `chat-budget:${identity}:${_utcMonthKey()}`;
  try {
    const [m, d] = await Promise.all([
      env.RATE_LIMIT.get(monthKey),
      env.RATE_LIMIT.get(dayKey),
    ]);
    return {
      used_month: parseInt(m || "0", 10) || 0,
      used_day: parseInt(d || "0", 10) || 0,
    };
  } catch {
    return { used_month: 0, used_day: 0 };
  }
}

// D1 Sync warm-on-startup flag — runs sync immediately when worker boots
let _d1WarmOnStartupDone = false;
function isAiPath(p: string): boolean {
  if (p.startsWith("/api/ai/fallback/")) return false;
  // Task #513 §A — `/api/edu/study/{chat,qa,explain}` are also chat
  // verbs that MUST be capped at the edge. Without this they bypass
  // the precheck because they live outside `/api/ai/*`.
  if (CHAT_CAP_PATHS.some((x) => p.startsWith(x))) return true;
  return AI_RATE_LIMIT_PREFIXES.some((x) => p.startsWith(x)) || (p.startsWith("/api/ai/") && !p.startsWith("/api/ai/fallback/"));
}

// ─── CANONICAL BOT REGEX — DO NOT DRIFT ─────────────────────────────────────
// MUST stay aligned with three other locations:
//   * artifacts/syrabit-backend/utils.py        → _SEARCH_BOT_UA_RE (Python source of truth)
//   * artifacts/syrabit/vite.config.js          → BOT_UA (build-time / dev SSR)
//   * artifacts/syrabit/public/_worker.js       → SEARCH_BOT_UA (Pages Worker)
// Used here for: rDNS verification gate (verifyBotIp), prerender route
// trigger, and crawler analytics counters. AI training crawlers like
// gptbot / ccbot / bytespider are intentionally INCLUDED — we want
// edge-proxy analytics to count them even though we don't always serve
// them prerendered HTML (that decision is made downstream).
// ────────────────────────────────────────────────────────────────────────────
// Task #9 — canonical bot registry lives at `infra/bot-rules.yaml`.
// CI guard `scripts/check_bot_rules_drift.py` enforces that every token
// from the verified_search + citation_ai + training_ai buckets appears
// in the regex below.
const SEARCH_BOT_UA = /googlebot|google-extended|googleother|google-inspectiontool|bingbot|duckduckbot|applebot|yandexbot|baiduspider|petalbot|yeti|mojeekbot|seznambot|youbot|slurp|msnbot|perplexitybot|perplexity-user|oai-searchbot|chatgpt-user|gptbot|claudebot|claude-web|anthropic-ai|applebot-extended|ccbot|cohere-ai|bytespider|amazonbot|diffbot|meta-externalagent|facebookexternalhit|facebookbot|twitterbot|linkedinbot|telegrambot|whatsapp|discordbot|slackbot|redditbot/i;

// Task #9 — Verified-search + citation-AI fast path.
// `VERIFIED_BOT_UA` covers the union of the verified_search and
// citation_ai buckets in `infra/bot-rules.yaml`. When CF marks the
// request `verifiedBot=true` (or our KV-cached rDNS confirms one of
// the published PTR suffixes — see `verifyBotIpKV` below) AND the UA
// matches this regex, we route the request through a separate
// high-RPM bucket (`VERIFIED_BOT_RATE_LIMIT_RPM`, 60 000 RPM) instead
// of the default 3 000 RPM bot bucket. Without this, AHSEC's exam-
// week recrawl burst (Googlebot pulling thousands of refreshed
// chapter pages back-to-back) trips the old 3 000 RPM ceiling and
// returns 429 to a verified search engine — the exact failure this
// task was opened to fix.
//
// Citation-AI bots (PerplexityBot, OAI-SearchBot, ChatGPT-User,
// Perplexity-User) belong in this bucket per registry policy: they
// cite sources and drive referral traffic, so we want them to crawl
// freely. Training-only AI bots (GPTBot, ClaudeBot, CCBot, …) are
// 403'd unconditionally below via `AI_BOT_UA` and never reach this
// fast path — verification status does not bypass the block.
const VERIFIED_BOT_UA = /googlebot|google-extended|googleother|google-inspectiontool|bingbot|duckduckbot|applebot|yandexbot|baiduspider|petalbot|yeti|mojeekbot|seznambot|youbot|msnbot|slurp|perplexitybot|perplexity-user|oai-searchbot|chatgpt-user/i;
const VERIFIED_BOT_RATE_LIMIT_RPM = 60000;

// ─── AI CRAWLER BLOCK LIST — DO NOT DRIFT ───────────────────────────────────
// Blocks pure AI *training* crawlers that scrape content to train LLMs
// without sending referral traffic back. Search-and-answer engines that
// do cite sources and drive clicks (Perplexity, ChatGPT browsing mode,
// Claude web-search) are intentionally NOT in this list — they increase
// discoverability for AHSEC/SEBA students.
//
// Allowed (search engines / answer engines with citations):
//   Perplexity (PerplexityBot / Perplexity-User) — cites sources, drives traffic
//   ChatGPT-User / OAI-SearchBot — ChatGPT browsing citations
//   ClaudeBot / Claude-Web — Claude web-search citations
//
// Blocked (pure training scrapers with no referral benefit):
//   GPTBot, CCBot, Bytespider, Diffbot, Cohere-AI
//   Google-Extended, Applebot-Extended (AI opt-out variants of real search bots)
//   Meta-ExternalAgent (Meta training crawler)
//   Amazonbot (Amazon Alexa training, no referral traffic)
//
// NOT blocked (search/answer engines that cite sources and drive referral traffic):
//   YouBot — You.com is a search and answer engine; removed from this list so
//   it can index Syrabit and send students who search there. CF's verifiedBot
//   flag is the primary trust gate for YouBot (no fixed CIDR range is published).
//
// Mirrors `_AI_BOT_NAMES` in artifacts/syrabit-backend/cf_bot_report.py
// so robots.txt, this hard block, and the dashboard analytics agree.
// Each pattern is anchored with \b so "GPTBotHelper" would not be falsely
// matched. Case-insensitive because real UAs use mixed case.
// Task #287: ClaudeBot, Claude-Web, anthropic-ai added — keep training
// crawlers blocked while still allowing answer bots (PerplexityBot,
// ChatGPT-User, OAI-SearchBot) which are intentionally absent here.
const AI_BOT_UA = /\b(?:gptbot|claudebot|claude-web|anthropic-ai|google-extended|applebot-extended|ccbot|cohere-ai|bytespider|amazonbot|diffbot|meta-externalagent)\b/i;

interface CidrRange { network: number; mask: number }

function parseCidr(cidr: string): CidrRange {
  const [ip, bits] = cidr.split("/");
  const p = ip.split(".").map(Number);
  const net = ((p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]) >>> 0;
  const m = bits === "0" ? 0 : (~((1 << (32 - Number(bits))) - 1)) >>> 0;
  return { network: net & m, mask: m };
}

function parseCidrs(cidrs: string[]): CidrRange[] {
  return cidrs.map(parseCidr);
}

function ipInRanges(ip: string, ranges: CidrRange[]): boolean {
  if (ip.includes(":")) return false;
  const p = ip.split(".").map(Number);
  if (p.length !== 4 || p.some((n) => isNaN(n) || n < 0 || n > 255)) return false;
  const ipNum = ((p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]) >>> 0;
  for (const r of ranges) {
    if ((ipNum & r.mask) === r.network) return true;
  }
  return false;
}

// ── Crawler IP verification ranges ────────────────────────────────────────────
//
// Design rationale (Task #243):
//   Cloudflare's cf.verifiedBot flag is checked FIRST in verifySearchBot and
//   immediately returns {verified:true} without consulting any of the ranges
//   below. That means every legitimate crawler on a newly-added IP range will
//   be verified by Cloudflare before it would ever be rejected here. These
//   CIDR lists are therefore a secondary fallback — they classify the minority
//   of requests where CF hasn't yet verified the bot (fresh IPs, edge cases).
//
// Update policy:
//   Only exact subnets from the crawler's own published source are included.
//   Generic cloud / datacenter supernets MUST NOT be added: they let spoofed
//   UAs from cloud IP pools be treated as verified crawlers, breaking spoof
//   detection and rate-limit enforcement.
//   To refresh: fetch the source URL for each provider, diff against this list,
//   and add only the new /24 or narrower subnets that appear.
//
// Validated: 2025-05-01
// Source: https://developers.google.com/search/apis/ipranges/googlebot.json
const GOOGLE_BOT_RANGES = parseCidrs([
  // Legacy shared-hosting crawler ranges (googlebot.json)
  "66.249.64.0/19", "66.249.96.0/20",
  // GCP regional crawler ranges — all /27 or narrower (googlebot.json)
  "34.100.182.96/28", "34.101.50.144/28", "34.118.254.0/28",
  "34.118.66.0/28", "34.126.178.96/28", "34.146.150.144/28",
  "34.147.110.160/28", "34.151.74.144/28", "34.152.50.64/28",
  "34.154.114.144/28", "34.155.98.32/28", "34.165.18.176/28",
  "34.175.160.64/28", "34.176.130.16/28", "34.22.85.0/27",
  "34.64.82.64/28", "34.65.242.112/28", "34.80.50.80/28",
  "34.88.194.0/28", "34.89.10.80/28", "34.89.198.80/28",
  "34.96.162.48/28", "35.247.243.240/28",
]);

// Source: https://www.bing.com/toolbox/bingbot.xml (validated 2025-05-01)
const BING_BOT_RANGES = parseCidrs([
  "157.55.39.0/24", "207.46.13.0/24", "40.77.167.0/24",
  "52.167.144.0/24", "13.66.139.0/24", "13.67.8.0/24",
  "131.253.24.0/22", "131.253.46.0/23", "157.55.16.0/23",
  "157.56.92.0/24", "199.30.24.0/23",
]);

const OPENAI_BOT_RANGES = parseCidrs([
  "23.98.142.176/28", "40.84.180.224/28",
  "20.15.240.64/28", "20.15.240.80/28", "20.15.240.96/28",
  "20.15.240.176/28", "20.15.241.0/28",
  "20.169.232.0/28", "20.171.206.0/28",
  "52.230.152.0/24", "52.233.106.0/24",
]);

// Source: https://yandex.com/ips (validated 2025-05-01)
const YANDEX_BOT_RANGES = parseCidrs([
  "5.255.253.0/24", "77.88.5.0/24", "77.88.47.0/24",
  "87.250.224.0/19", "93.158.161.0/24", "95.108.128.0/17",
  "100.43.80.0/24", "141.8.153.0/24", "178.154.128.0/17",
  "199.21.99.0/24", "213.180.192.0/19",
]);

// Source: https://support.apple.com/en-us/101555 — Applebot uses 17.0.0.0/8
// (the entire Apple-owned /8 block). Apple does not publish a narrower list.
const APPLE_BOT_RANGES = parseCidrs([
  "17.0.0.0/8",
]);

// You.com's YouBot does NOT publish a stable CIDR list. Verification relies
// entirely on Cloudflare's cf.verifiedBot flag (checked first in
// verifySearchBot). An empty range array here signals "no CIDR fallback";
// verifySearchBot handles this case with {verified:false, spoofed:false}
// instead of marking the request as spoofed.
const YOUBOT_BOT_RANGES: CidrRange[] = [];

const BOT_UA_RANGES: Array<[RegExp, CidrRange[]]> = [
  [/googlebot|google-extended|googleother/i, GOOGLE_BOT_RANGES],
  [/bingbot/i, BING_BOT_RANGES],
  [/duckduckbot/i, BING_BOT_RANGES],
  [/chatgpt-user|oai-searchbot/i, OPENAI_BOT_RANGES],
  [/yandexbot/i, YANDEX_BOT_RANGES],
  [/applebot/i, APPLE_BOT_RANGES],
  // YouBot: cf.verifiedBot is the sole gate — empty CIDR list is intentional.
  [/youbot/i, YOUBOT_BOT_RANGES],
];

interface BotVerifyResult {
  verified: boolean;
  claimsBot: boolean;
  spoofed: boolean;
}

function hashIp(ip: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < ip.length; i++) {
    h ^= ip.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, "0");
}

function verifySearchBot(ua: string, request: Request, clientIp: string): BotVerifyResult {
  // cf.verifiedBot is the unconditional trust gate: if Cloudflare has
  // cryptographically verified the request came from a legitimate crawler,
  // we trust it regardless of UA string or CIDR range match. This prevents
  // legitimate crawlers on new/unpublished IP ranges from being downgraded.
  const cf = (request as unknown as { cf?: { verifiedBot?: boolean } }).cf;
  if (cf && cf.verifiedBot === true) return { verified: true, claimsBot: true, spoofed: false };
  if (!SEARCH_BOT_UA.test(ua)) return { verified: false, claimsBot: false, spoofed: false };
  for (const [pattern, ranges] of BOT_UA_RANGES) {
    if (pattern.test(ua)) {
      if (ranges.length === 0) {
        // This bot (e.g. YouBot) publishes no stable CIDR list; Cloudflare's
        // verifiedBot flag is the sole verification gate, already checked above.
        // Reaching here means CF did not verify the request — treat it as
        // unverified but not spoofed (no grounds to log it as an impersonation).
        return { verified: false, claimsBot: true, spoofed: false };
      }
      const matched = ipInRanges(clientIp, ranges);
      return { verified: matched, claimsBot: true, spoofed: !matched };
    }
  }
  return { verified: false, claimsBot: true, spoofed: true };
}

async function logSpoofedBot(
  kv: KVNamespace,
  ipHash: string,
  ua: string,
  clientIp: string,
  colo: string,
): Promise<void> {
  const now = Date.now();
  const windowKey = `spoof:count:${Math.floor(now / 60000)}`;
  try {
    const raw = await kv.get(windowKey);
    const count = raw ? parseInt(raw, 10) + 1 : 1;
    await kv.put(windowKey, String(count), { expirationTtl: 3600 });

    if (count === 50 || count === 200 || count === 500) {
      console.warn(
        `SPOOF_ALERT threshold=${count}/min | ` +
        `window=${new Date(Math.floor(now / 60000) * 60000).toISOString()}`
      );
    }
  } catch {}

  const botMatch = ua.match(SEARCH_BOT_UA);
  const claimedBot = botMatch ? botMatch[0].toLowerCase() : "unknown";
  console.log(
    `SPOOFED_BOT ip_hash=${ipHash} claimed=${claimedBot} ` +
    `ua="${ua.slice(0, 150)}" colo=${colo} ts=${new Date(now).toISOString()}`
  );
}

// Task #243 — Log unsuccessful bot responses so the 2.48K "unsuccessful
// requests" bucket in the CF Search Crawler Activity dashboard becomes
// actionable. Emits a structured console.log (readable via `wrangler tail`)
// and optionally writes a datapoint to the Analytics Engine dataset.
function logBotErrorResponse(
  env: Env,
  ctx: ExecutionContext,
  status: number,
  botResult: BotVerifyResult,
  ua: string,
  pathname: string,
): void {
  if (status < 400) return; // only 4xx and 5xx
  const botMatch = ua.match(SEARCH_BOT_UA);
  const botName = botMatch ? botMatch[0].toLowerCase() : "unknown";
  console.log(
    JSON.stringify({
      event: "BOT_ERROR_RESPONSE",
      status,
      bot: botName,
      verified: botResult.verified,
      spoofed: botResult.spoofed,
      pathname: pathname.slice(0, 200),
      ts: new Date().toISOString(),
    })
  );
  // Optionally emit to Analytics Engine for dashboard visibility.
  if (env.ANALYTICS) {
    try {
      ctx.waitUntil(Promise.resolve(
        env.ANALYTICS.writeDataPoint({
          blobs: [botName, pathname.slice(0, 100)],
          doubles: [status],
          indexes: ["bot_error"],
        })
      ));
    } catch { /* Analytics Engine unavailable — console log above is sufficient */ }
  }
}

function isVerifiedSearchBot(ua: string, request: Request, clientIp: string): boolean {
  return verifySearchBot(ua, request, clientIp).verified;
}

// ─── Task #9 — Critical bot UA list (hard-403 on spoofed) ──────────────────
// UAs whose impersonation is high-impact enough that we 403 the request
// when the IP fails *forward-confirmed* rDNS. The set is the union of
// verified_search and citation_ai families that drive
// search-engine indexing or LLM citation surfaces — i.e. the audience
// whose pre-rendered HTML an attacker would scrape by spoofing.
const CRITICAL_BOT_UA = /(googlebot|bingbot|duckduckbot|applebot|yandexbot|baiduspider|petalbot|perplexitybot|perplexity-user|oai-searchbot|chatgpt-user)/i;

// ─── Task #9 — KV-cached forward-confirmed rDNS verification ──────────────
// `verifyBotIpWithKv` extends bot trust to UAs whose reverse DNS PTR
// matches one of the canonical suffixes in `infra/bot-rules.yaml` AND
// the PTR target's forward A/AAAA record contains the original
// request IP (forward-confirmed reverse DNS / FCrDNS, RFC 8499 §5).
// PTR alone is forgeable by anyone who controls a reverse-DNS zone for
// an IP block; FCrDNS closes that gap because it requires control of
// both the reverse zone (to set the PTR) AND the forward zone
// (to ensure the PTR target resolves back to the same IP).
//
// The result is cached for 24 h in RATE_LIMIT KV. Cache keys are
// scoped by bot FAMILY so a positive cache for `googlebot` cannot be
// reused to elevate trust for an unrelated UA hitting the same IP
// (the typical NAT case where multiple crawlers exit a shared egress). This
// closes the gap where a verified search bot on a freshly-rotated IP
// has not yet been flagged by Cloudflare's `cf.verifiedBot` (the
// primary trust gate, checked first in the caller).
//
// Cache contract:
//   key   : bot:rdns:<ipHash>
//   value : "1:<bot-token>" on hit, "0" on negative cache (still 24h)
//   ttl   : 86 400 s (24 h) — matches the upstream PTR TTL conventions
//
// The Cloudflare Workers runtime does not expose a built-in DNS
// resolver, so this function uses the Cloudflare DNS-over-HTTPS
// endpoint at https://cloudflare-dns.com/dns-query for the actual
// PTR fetch. The lookup is short-circuited when env.RATE_LIMIT is
// missing (preview environment) or when the IP is non-IPv4 — IPv6
// PTR lookups have a different ARPA layout we don't currently
// support, and v6 traffic from verified bots is rare enough to fall
// back to the base 3 000 RPM bucket without harm.
//
// Per `infra/bot-rules.yaml`, only bots in the verified_search +
// citation_ai buckets carry rdns_suffixes. The mapping below is
// hand-rolled in TypeScript so the worker has zero parse-time
// dependency on the YAML file (which is not bundled into the
// worker). Drift between this map and the YAML is caught by Task #9's
// `scripts/check_bot_rules_drift.py` checking the YAML's token set;
// changes to suffix sets are infrequent enough that a manual
// matching review is acceptable. Add a TODO when introducing a new
// suffix here.
// Each entry: [family-name, ua-pattern, [PTR suffixes]].
// `family-name` becomes part of the KV cache key so a positive
// verification for one family doesn't leak trust to others on a
// shared egress IP.
const BOT_RDNS_SUFFIXES: Array<[string, RegExp, string[]]> = [
  ["googlebot", /googlebot|google-extended|googleother|google-inspectiontool/i, [".googlebot.com.", ".google.com."]],
  ["bingbot", /bingbot|msnbot/i, [".search.msn.com."]],
  ["duckduckbot", /duckduckbot/i, [".duckduckgo.com."]],
  ["applebot", /applebot/i, [".applebot.apple.com."]],
  ["yandexbot", /yandexbot|^yandex/i, [".yandex.ru.", ".yandex.net.", ".yandex.com."]],
  ["baiduspider", /baiduspider/i, [".baidu.com.", ".baidu.jp."]],
  ["petalbot", /petalbot/i, [".petalsearch.com.", ".aspiegel.com."]],
  ["yeti", /yeti/i, [".naver.com."]],
  ["mojeekbot", /mojeekbot/i, [".mojeek.com."]],
  ["seznambot", /seznambot/i, [".seznam.cz."]],
  ["yahoo-slurp", /slurp/i, [".crawl.yahoo.net."]],
  ["perplexitybot", /perplexitybot|perplexity-user/i, [".perplexity.ai."]],
  ["openai-search", /oai-searchbot|chatgpt-user/i, [".openai.com."]],
  // YouBot (you.com) — citation-driving answer engine; per task #9
  // policy it gets the verified-bot fast path when FCrDNS confirms.
  // You.com publishes no static CIDR list, so rDNS is the sole gate
  // here (cf.verifiedBot would also accept it but isn't relied on).
  ["youbot", /youbot/i, [".you.com.", ".youbot.you.com."]],
];

const BOT_RDNS_TTL_S = 86400;

async function _dohQuery(name: string, type: "PTR" | "A" | "AAAA"): Promise<string[]> {
  // Returns the lowercased Answer.data values, or [] on any failure.
  try {
    const dnsResp = await fetch(
      `https://cloudflare-dns.com/dns-query?name=${name}&type=${type}`,
      { headers: { Accept: "application/dns-json" }, cf: { cacheTtl: BOT_RDNS_TTL_S } as unknown as RequestInitCfProperties },
    );
    if (!dnsResp.ok) return [];
    const data = await dnsResp.json() as { Answer?: Array<{ data: string; type?: number }> };
    return (data.Answer || [])
      .filter((a) => typeof a.data === "string")
      .map((a) => a.data.toLowerCase());
  } catch { return []; }
}

async function _resolveRdns(ip: string): Promise<string | null> {
  // IPv4 only. Returns the PTR target (with trailing dot) or null.
  if (!/^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(ip)) return null;
  const arpa = `${ip.split(".").reverse().join(".")}.in-addr.arpa`;
  const ans = await _dohQuery(arpa, "PTR");
  return ans[0] || null;
}

async function _forwardConfirms(ptrTarget: string, expectedIp: string): Promise<boolean> {
  // Forward-confirmed rDNS: the PTR target's A record must include
  // the IP we started from. Strips the DoH trailing dot before query.
  const name = ptrTarget.endsWith(".") ? ptrTarget.slice(0, -1) : ptrTarget;
  if (!name) return false;
  const aRecords = await _dohQuery(name, "A");
  return aRecords.includes(expectedIp.toLowerCase());
}

// Counter keys for the rDNS-verification observability tile. The
// admin tile rolls these up into a per-family miss-rate so an
// operator can see when verifyBotIpWithKv is doing live PTR/A
// lookups vs serving from the 24h KV cache.
async function _bumpBotRdnsCounter(
  env: Env,
  ctx: ExecutionContext,
  family: string,
  outcome: "hit_pos" | "hit_neg" | "miss_pos" | "miss_neg",
): Promise<void> {
  if (!env.RATE_LIMIT) return;
  const day = new Date().toISOString().slice(0, 10);
  const key = `bot:rdns_ctr:${day}:${family}:${outcome}`;
  ctx.waitUntil((async () => {
    try {
      const cur = await env.RATE_LIMIT.get(key);
      const next = (cur ? parseInt(cur, 10) : 0) + 1;
      await env.RATE_LIMIT.put(key, String(next), { expirationTtl: 172800 });
    } catch { /* counter best-effort */ }
  })());
}

async function verifyBotIpWithKv(
  env: Env,
  ctx: ExecutionContext,
  ua: string,
  ip: string,
): Promise<boolean> {
  if (!env.RATE_LIMIT) return false;
  // Match the UA against the family table FIRST so we don't burn a
  // KV read for UAs (e.g. Twitterbot) we don't have a verified-bot
  // policy for.
  let family: string | null = null;
  let suffixes: string[] | null = null;
  for (const [name, pattern, sfx] of BOT_RDNS_SUFFIXES) {
    if (pattern.test(ua)) { family = name; suffixes = sfx; break; }
  }
  if (!family || !suffixes) return false;
  // Family-scoped cache key — a positive cache for googlebot from a
  // shared NAT IP must NOT elevate trust for a UA in a different
  // family hitting the same IP later.
  const cacheKey = `bot:rdns:${family}:${hashIp(ip)}`;
  try {
    const cached = await env.RATE_LIMIT.get(cacheKey);
    if (cached === "0") {
      await _bumpBotRdnsCounter(env, ctx, family, "hit_neg");
      return false;
    }
    if (cached && cached.startsWith("1:")) {
      await _bumpBotRdnsCounter(env, ctx, family, "hit_pos");
      return true;
    }
  } catch { /* KV read miss — fall through to live lookup */ }
  const ptr = await _resolveRdns(ip);
  if (!ptr || !suffixes.some((sfx) => ptr.endsWith(sfx))) {
    ctx.waitUntil(env.RATE_LIMIT.put(cacheKey, "0", { expirationTtl: BOT_RDNS_TTL_S }).catch(() => {}));
    await _bumpBotRdnsCounter(env, ctx, family, "miss_neg");
    return false;
  }
  // Forward-confirm: PTR target must resolve back to the same IP.
  // PTR-only verification is forgeable by an attacker who controls
  // the in-addr.arpa zone for a leased IP; the forward A round-trip
  // requires control of the bot vendor's authoritative forward zone.
  const confirmed = await _forwardConfirms(ptr, ip);
  ctx.waitUntil(env.RATE_LIMIT.put(
    cacheKey,
    confirmed ? `1:${ptr}` : "0",
    { expirationTtl: BOT_RDNS_TTL_S },
  ).catch(() => {}));
  await _bumpBotRdnsCounter(env, ctx, family, confirmed ? "miss_pos" : "miss_neg");
  return confirmed;
}

const BASE_URL = "https://syrabit.ai";
const STATIC_PAGES: Array<[string, string, string]> = [
  ["/home", "weekly", "1.0"],
  ["/about", "monthly", "0.9"],
  ["/pricing", "monthly", "0.8"],
  ["/library", "weekly", "0.9"],
  ["/curriculum", "weekly", "0.8"],
  ["/exam-routine", "weekly", "0.8"],
  ["/terms", "yearly", "0.3"],
  ["/privacy", "yearly", "0.3"],
];
const ALL_PAGE_TYPES = ["notes", "mcqs", "important-questions", "examples", "definition", "faq"];
const SITEMAP_TYPES = ["notes", "mcqs", "important-questions", "examples", "definition", "faq"];

function getCorsHeaders(origin: string | null): Record<string, string> | null {
  if (!origin || !ALLOWED_ORIGINS.includes(origin)) return null;
  return {
    "Access-Control-Allow-Origin": origin,
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, Origin, X-Requested-With, x-anon-id, x-turnstile-token, traceparent, tracestate, baggage",
    "Access-Control-Expose-Headers": "X-RateLimit-Limit, X-RateLimit-Remaining, Retry-After, X-Request-Id, X-Source",
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Max-Age": "600",
  };
}

function safeCorsHeaders(origin: string | null): Record<string, string> {
  return getCorsHeaders(origin) || {};
}

/**
 * Task #13 — TTL-override hook for the nightly SEO prewarm Lambda.
 *
 * The prewarm pass HEADs every chapter URL with two headers:
 *   • `X-Prewarm-Recommended-TTL: <seconds>` — the season-aware TTL
 *     computed by `cache_calendar.recommended_ttl_seconds()` in the
 *     backend (single source of truth shared with the worker).
 *   • `X-Prewarm-Auth: <token>` — must equal `BACKEND_ORIGIN_SECRET`.
 *
 * Without the auth match, the header is IGNORED so public clients
 * cannot manipulate cache TTL policy. Returns `null` when no
 * override applies; the caller falls back to `getCacheTtl(pathname)`.
 */
function getPrewarmOverrideTtl(
  request: Request,
  env: Env,
): number | null {
  const auth = request.headers.get("X-Prewarm-Auth");
  const recommended = request.headers.get("X-Prewarm-Recommended-TTL");
  if (!auth || !recommended) return null;
  if (!env.BACKEND_ORIGIN_SECRET) return null;
  if (auth !== env.BACKEND_ORIGIN_SECRET) return null;
  const n = Number.parseInt(recommended, 10);
  if (!Number.isFinite(n) || n <= 0) return null;
  // Hard ceiling of 90 days mirrors `cache_calendar`'s exam-mode
  // stretch ceiling so a buggy header can't pin a year-long entry.
  return Math.min(n, 90 * 24 * 60 * 60);
}

/**
 * Rewrite the cache-controlling headers on a Response so that when
 * Cloudflare stores it in the tiered cache, the entry honors the
 * prewarm-supplied TTL instead of the default. The original Response
 * (returned to the *current* requester) is left untouched.
 */
function withOverriddenTtl(resp: Response, ttl: number): Response {
  const cacheControl =
    `public, s-maxage=${ttl}, stale-while-revalidate=${ttl * 2}`;
  const surrogate =
    `public, max-age=${ttl}, stale-while-revalidate=${ttl * 2}`;
  const stored = new Response(resp.body, resp);
  stored.headers.set("Cache-Control", cacheControl);
  stored.headers.set("Surrogate-Control", surrogate);
  return stored;
}

function getCacheTtl(pathname: string): number {
  // Task #575 — `pickEffectiveTtl` checks the exam-mode override list
  // first when `_currentSeasonSnapshot.season` is `"exam"` or
  // `"results"`, then falls back to the normal CACHE_TTL_ENTRIES
  // (sorted by descending key length so /api/seo/keyword-index wins
  // over /api/seo/), and finally to DEFAULT_CACHE_TTL_SECONDS. During
  // `"normal"` the original behaviour is preserved exactly.
  return pickEffectiveTtl(
    pathname,
    _currentSeasonSnapshot,
    CACHE_TTL_ENTRIES,
    EXAM_CACHE_TTL_ENTRIES,
    DEFAULT_CACHE_TTL_SECONDS,
  );
}

export function isCacheable(pathname: string): boolean {
  return CACHEABLE_PREFIXES.some((p) => pathname.startsWith(p));
}

export function isBypass(pathname: string): boolean {
  return BYPASS_PREFIXES.some((p) => pathname.startsWith(p));
}

export function isUserSpecific(pathname: string): boolean {
  return USER_SPECIFIC_PREFIXES.some((p) => pathname.startsWith(p));
}

async function checkRateLimitKey(
  key: string,
  kv: KVNamespace,
  limit: number
): Promise<{ allowed: boolean; remaining: number }> {
  const now = Math.floor(Date.now() / 1000);
  const windowStart = now - RATE_LIMIT_WINDOW_S;
  try {
    const raw = await kv.get(key);
    let timestamps: number[] = raw ? JSON.parse(raw) : [];
    timestamps = timestamps.filter((t) => t > windowStart);
    if (timestamps.length >= limit) return { allowed: false, remaining: 0 };
    timestamps.push(now);
    await kv.put(key, JSON.stringify(timestamps), { expirationTtl: RATE_LIMIT_WINDOW_S * 2 });
    return { allowed: true, remaining: limit - timestamps.length };
  } catch {
    return { allowed: true, remaining: limit };
  }
}

async function checkRateLimit(
  ip: string,
  kv: KVNamespace,
  limit: number = RATE_LIMIT_RPM
): Promise<{ allowed: boolean; remaining: number }> {
  const key = `rl:${ip}`;
  const now = Math.floor(Date.now() / 1000);
  const windowStart = now - RATE_LIMIT_WINDOW_S;

  try {
    const raw = await kv.get(key);
    let timestamps: number[] = raw ? JSON.parse(raw) : [];
    timestamps = timestamps.filter((t) => t > windowStart);

    if (timestamps.length >= limit) {
      return { allowed: false, remaining: 0 };
    }

    timestamps.push(now);
    await kv.put(key, JSON.stringify(timestamps), {
      expirationTtl: RATE_LIMIT_WINDOW_S * 2,
    });

    return { allowed: true, remaining: limit - timestamps.length };
  } catch {
    return { allowed: true, remaining: limit };
  }
}

/**
 * Task #109 Phase 5 — Durable Object rate limiter.
 *
 * Uses the RateLimiter DO for strongly-consistent, per-key sliding-window
 * limiting. Falls back to the KV-based checkRateLimitKey() when RATE_LIMITER_DO
 * is not bound (local dev, pre-migration). The DO provides isolation guarantees
 * that KV's eventual-consistency cannot: two concurrent requests for the same IP
 * hit the same DO instance and their storage.transaction() calls are serialized,
 * eliminating the double-grant race that exists with KV.
 */
async function checkRateLimitWithDO(
  key: string,
  env: Env,
  limit: number,
  windowMs: number = RATE_LIMIT_WINDOW_S * 1000,
): Promise<{ allowed: boolean; remaining: number; retryAfterMs: number }> {
  if (env.RATE_LIMITER_DO) {
    try {
      const doId = env.RATE_LIMITER_DO.idFromName(key);
      const stub = env.RATE_LIMITER_DO.get(doId);
      const res = await stub.fetch("https://rate-limiter/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, limit, windowMs }),
      });
      if (res.ok) {
        return await res.json<{ allowed: boolean; remaining: number; retryAfterMs: number }>();
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : "unknown";
      console.error(`[rate-limiter-do] error for key=${key}: ${msg.slice(0, 200)}`);
    }
  }
  // KV fallback — eventual consistency but always available
  const kv = await checkRateLimitKey(key, env.RATE_LIMIT, limit);
  return { ...kv, retryAfterMs: windowMs };
}

/**
 * Task #109 Phase 5 — Analytics Engine instrumentation.
 *
 * Emits a single datapoint per request to the "syrabit-edge-metrics" dataset.
 * Uses ctx.waitUntil so the write never blocks user-visible response time.
 *
 * Schema:
 *   blob1  — cacheStatus:      "hit" | "miss" | "bypass" | "pass"
 *   blob2  — chapterId:        chapter slug or "" for non-chapter routes
 *   blob3  — aiProvider:       "groq" | "gemini" | "workers-ai" | "none"
 *   blob4  — pathname:         first 64 chars of the request pathname
 *   blob5  — rateLimitResult:  "ok" | "ai_limited" | "ip_limited"
 *   double1 — responseTimeMs: end-to-end response latency
 *   double2 — isAiRequest:    1 if an AI endpoint, else 0
 *   double3 — httpStatus:     HTTP response status code
 */
function writeEdgeMetric(
  env: Env,
  ctx: ExecutionContext,
  startMs: number,
  opts: {
    cacheStatus?: string;
    chapterId?: string;
    aiProvider?: string;
    pathname: string;
    rateLimitResult?: string;
    isAiRequest?: boolean;
    httpStatus?: number;
  },
): void {
  if (!env.ANALYTICS) return;
  const responseTimeMs = Date.now() - startMs;
  const dataPoint = {
    blobs: [
      opts.cacheStatus     ?? "pass",
      opts.chapterId       ?? "",
      opts.aiProvider      ?? "none",
      opts.pathname.slice(0, 64),
      opts.rateLimitResult ?? "ok",
    ],
    doubles: [
      responseTimeMs,
      opts.isAiRequest ? 1 : 0,
      opts.httpStatus ?? 0,
    ],
    indexes: [opts.chapterId ?? "none"],
  };
  ctx.waitUntil(Promise.resolve(env.ANALYTICS.writeDataPoint(dataPoint)));
}

/**
 * Extract the chapter slug from a pathname for AE chapterId tagging.
 * Matches patterns like /study/physics/chapter/thermodynamics or
 * /chapters/waves — returns the slug, or "" if not a chapter route.
 */
function extractChapterIdFromPath(pathname: string): string {
  const m = pathname.match(/\/chapters?\/([^/?#]+)/i);
  return m ? m[1].toLowerCase().slice(0, 64) : "";
}

/**
 * Best-effort AI provider attribution from the request pathname.
 * The edge worker never reads the response body, so it cannot know which
 * backend provider (vertex / sarvam / workers-ai per the current
 * canonical-delegation map) ultimately served the request.
 * Only /api/ai/fallback/* is distinguishable — those route to Workers AI.
 *
 * NOTE: isAiPath() explicitly excludes /api/ai/fallback/* (it is exempt from
 * the AI rate limit), so the fallback check must come BEFORE the isAiPath()
 * guard to remain reachable.
 */
function aiProviderFromPath(pathname: string): string {
  if (pathname.startsWith("/api/ai/fallback/")) return "workers-ai";
  if (!isAiPath(pathname)) return "none";
  return "backend";
}

function buildProxyHeaders(request: Request, clientIp: string, env?: Env): Headers {
  const headers = new Headers();
  for (const [key, value] of request.headers.entries()) {
    if (
      key.toLowerCase() === "host" ||
      key.toLowerCase() === "cf-connecting-ip"
    )
      continue;
    headers.set(key, value);
  }
  headers.set("X-Forwarded-For", clientIp);
  // Authenticated origin pull. Required by the FastAPI
  // OriginSharedSecretMiddleware on Cloud Run / Railway. Without this
  // header, every non-/health backend fetch returns 403. Centralised
  // here so every call site that uses buildProxyHeaders gets it for
  // free — fixes a regression where the cache-miss/D1-miss fallback
  // and the bot-prerender fetches were sending the request unsigned.
  if (env && env.BACKEND_ORIGIN_SECRET) {
    headers.set("X-Origin-Auth", env.BACKEND_ORIGIN_SECRET);
  }
  // Task #2 — 2026 blueprint: stamp an Assamese-aware cache region on
  // every proxied request. The backend's `ai_input_cache` / `kv_cache`
  // / `cf_tiered_cache` layers fold this into their cache keys + per-
  // region hit-ratio counters so the admin cache panel can render
  // `global` vs `ne-india` side by side. Default is "global"; we flip
  // to "ne-india" when Cloudflare's geo-IP lookup says the request
  // originated in IN-AS (Assam) or one of the surrounding North-East
  // Indian states (`AS|ML|TR|MN|MZ|NL|AR`). The header is advisory —
  // backend defaults to "global" if it's missing.
  const cf = (request as Request & { cf?: { country?: string; regionCode?: string } }).cf;
  let region = "global";
  if (cf) {
    const country = (cf.country || "").toUpperCase();
    const regionCode = (cf.regionCode || "").toUpperCase();
    const NE_INDIA = new Set(["AS", "ML", "TR", "MN", "MZ", "NL", "AR"]);
    if (country === "IN" && NE_INDIA.has(regionCode)) {
      region = "ne-india";
    }
  }
  headers.set("X-Cache-Region", region);
  // Task #2 — Mumbai/Chennai colo bias for ne-india. Cloudflare's
  // colo selection is governed by Anycast + Argo, but for ne-india
  // requests we surface the *intended* colo bias (BOM = Mumbai,
  // MAA = Chennai — the two AP-South colos closest to Assam) and the
  // *observed* colo (cf.colo) so the admin telemetry can flag any
  // request that landed outside the intended bias and so the origin
  // can route the matching read-replica path. The hint headers are
  // advisory — the backend uses them for the per-region cache
  // counters and admin Ops Console outage map only.
  const observedColo =
    (request as unknown as { cf?: { colo?: string } }).cf?.colo || "";
  if (region === "ne-india") {
    headers.set("X-Backend-Colo-Bias", "BOM,MAA");
    headers.set("X-Backend-Colo-Observed", observedColo);
  } else {
    headers.set("X-Backend-Colo-Bias", "global");
    headers.set("X-Backend-Colo-Observed", observedColo);
  }
  return headers;
}

/**
 * Task #120 — Inject a cryptographic proof that the mTLS client certificate is
 * bound and active in this Worker deployment.
 *
 * When MTLS_CERT is bound (cert provisioned + wrangler deploy run), computes
 *   HMAC-SHA256("mtls-active", BACKEND_ORIGIN_SECRET)
 * and sets it as the X-Cf-Mtls-Active header on the outbound request.
 *
 * Security properties:
 *   • Non-spoofable: requires BACKEND_ORIGIN_SECRET, which is kept secret.
 *   • Bound to cert deployment: the header is ONLY set when env.MTLS_CERT is
 *     present, so even a caller with the secret cannot produce this header
 *     without also deploying a Worker with [[mtls_certificates]] wired in.
 *   • Consistent: same HMAC value on every call — no timestamp / nonce
 *     complexity needed since BACKEND_ORIGIN_SECRET rotation is the revocation
 *     mechanism and it already rotates X-Origin-Auth simultaneously.
 *
 * The backend MtlsClientCertMiddleware validates this HMAC using the same
 * BACKEND_ORIGIN_SECRET (stored as ORIGIN_SHARED_SECRET on Railway).
 *
 * Must be awaited AFTER buildProxyHeaders() at every backend call site.
 */
async function addMtlsActiveHeader(
  headers: Headers | Record<string, string>,
  env: Env,
): Promise<void> {
  if (!env.MTLS_CERT || !env.BACKEND_ORIGIN_SECRET) return;
  const encoder = new TextEncoder();
  const keyData = encoder.encode(env.BACKEND_ORIGIN_SECRET);
  const cryptoKey = await crypto.subtle.importKey(
    "raw",
    keyData,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign(
    "HMAC",
    cryptoKey,
    encoder.encode("mtls-active"),
  );
  const hex = Array.from(new Uint8Array(sig))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  if (headers instanceof Headers) {
    headers.set("X-Cf-Mtls-Active", hex);
  } else {
    headers["X-Cf-Mtls-Active"] = hex;
  }
}

/**
 * Task #110 Phase 6 — Central mTLS-aware fetch for ALL Railway backend calls.
 *
 * Every fetch to env.BACKEND_URL MUST go through this function so that the
 * mTLS client certificate is presented on every TLS handshake with Railway.
 * Using plain `fetch()` bypasses the certificate binding and defeats the
 * mTLS hardening goal — Railway would see no client cert and, once mTLS is
 * required there, would reject the connection.
 *
 * Behaviour:
 *   MTLS_CERT bound                   → env.MTLS_CERT.fetch() (cert presented)
 *   MTLS_CERT absent + MTLS_REQUIRED  → throws; caller should surface a 503
 *   MTLS_CERT absent + no requirement → plain fetch() (pre-cert / local dev)
 */
function fetchBackend(
  env: Env,
  url: string,
  init: RequestInit,
): Promise<Response> {
  if (env.MTLS_CERT) {
    return env.MTLS_CERT.fetch(url, init);
  }
  if (env.MTLS_REQUIRED === "true") {
    throw new Error(
      "[mTLS] MTLS_REQUIRED=true but MTLS_CERT binding absent — refusing backend fetch to prevent insecure bypass",
    );
  }
  return fetch(url, init);
}

async function proxyToBackend(
  request: Request,
  env: Env,
  pathname: string,
  search: string,
  clientIp: string,
  cors: Record<string, string>,
  remaining: number,
): Promise<Response> {
  const backendUrl = `${env.BACKEND_URL}${pathname}${search}`;
  // Task #606: X-Origin-Auth is now injected centrally by buildProxyHeaders
  // when env is passed — covers proxyToBackend, bot-prerender, cache-miss
  // fallback, and any future call site uniformly.
  const proxyHeaders = buildProxyHeaders(request, clientIp, env);
  // Task #120: inject HMAC proof that the mTLS cert is bound (non-spoofable).
  await addMtlsActiveHeader(proxyHeaders, env);

  try {
    // Phase 6 (Task #110): use the mTLS-bound fetcher when the certificate has
    // been provisioned, so Cloudflare presents the client cert on every TLS
    // handshake with the Railway origin.
    //
    // All Railway backend fetches go through fetchBackend() which uses
    // env.MTLS_CERT.fetch() when bound.  The fail-closed guard (MTLS_REQUIRED)
    // lives inside fetchBackend() — calling it here surfaces a 503 cleanly.
    const fetchInit = {
      method: request.method,
      headers: proxyHeaders,
      body:
        request.method !== "GET" && request.method !== "HEAD"
          ? request.body
          : undefined,
    };
    let backendResp: Response;
    try {
      backendResp = await fetchBackend(env, backendUrl, fetchInit);
    } catch (mtlsErr) {
      const msg = mtlsErr instanceof Error ? mtlsErr.message : String(mtlsErr);
      console.error(`[proxyToBackend] ${msg}`);
      return new Response(
        JSON.stringify({ error: "mTLS enforcement active: cert binding missing — deploy with [[mtls_certificates]] wired in wrangler.toml" }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      );
    }

    const respHeaders = new Headers(cors);
    for (const [key, value] of backendResp.headers.entries()) {
      if (
        key.toLowerCase() !== "access-control-allow-origin" &&
        key.toLowerCase() !== "access-control-allow-credentials" &&
        key.toLowerCase() !== "access-control-allow-methods" &&
        key.toLowerCase() !== "access-control-allow-headers"
      ) {
        respHeaders.set(key, value);
      }
    }
    respHeaders.set("X-RateLimit-Remaining", String(remaining));
    respHeaders.set("X-Cache", "BYPASS");
    respHeaders.set("X-Source", "backend");

    return new Response(backendResp.body, {
      status: backendResp.status,
      headers: respHeaders,
    });
  } catch {
    return new Response(
      JSON.stringify({ detail: "Backend unavailable", edge: true }),
      {
        status: 502,
        headers: { ...cors, "Content-Type": "application/json", "X-Source": "backend" },
      }
    );
  }
}

// FNV-1a 32-bit hash of an arbitrary string. Used for cheap ETag
// generation on D1 responses — strong enough to detect content changes
// for HTTP cache revalidation, fast enough to run per-response without
// CPU budget concerns. (Crypto-grade SHA isn't required: ETag collisions
// only ever cause stale revalidation, never security issues.)
function fnv1a32(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
  }
  return h.toString(16).padStart(8, "0");
}

// ─── Cache-Tag derivation ─────────────────────────────────────────────────────
// Derives one or more Cloudflare Cache-Tags from the request pathname so
// content can be surgically purged by tag from the CF dashboard or a CI
// pipeline after a publish event, without purging unrelated cached routes.
//
// Tag taxonomy:
//   chapter-{id}        → /api/content/chapters/{id} and SEO chapter pages
//   subject-{slug}      → /api/content/subjects/{slug}
//   library-bundle      → /api/content/library-bundle (the big navbar payload)
//   seo-pages           → /api/seo/** (SEO HTML and sitemap routes)
//   sitemap             → /api/seo/sitemap* and /sitemap.xml
//   api-content         → catch-all for all /api/content/* responses
//
// Usage: set the returned string as the `Cache-Tag` response header.
// Multiple tags are space-separated (CF accepts comma- or space-separated).
// Returns empty string for paths that carry no useful tag.
export function buildCacheTags(pathname: string): string {
  const tags: string[] = [];

  if (pathname.startsWith("/api/content/library-bundle")) {
    tags.push("library-bundle");
  }
  if (pathname.startsWith("/api/content/")) {
    tags.push("api-content");
    // /api/content/chapters/{id}[/...]
    const chapterMatch = pathname.match(/^\/api\/content\/chapters\/([^/?]+)/);
    if (chapterMatch) tags.push(`chapter-${chapterMatch[1]}`);
    // /api/content/subjects/{slug}
    const subjectMatch = pathname.match(/^\/api\/content\/subjects\/([^/?]+)/);
    if (subjectMatch) tags.push(`subject-${subjectMatch[1]}`);
  }
  if (pathname.startsWith("/api/seo/")) {
    tags.push("seo-pages");
    if (pathname.includes("sitemap")) tags.push("sitemap");
  }
  if (pathname === "/sitemap.xml" || pathname === "/sitemap-index.xml") {
    tags.push("sitemap");
  }
  // Board/class/subject/chapter routes served by D1 or SEO pipeline.
  // Guard: only apply to non-API paths so /api/content/... doesn't get
  // spurious subject-content or chapter-xyz tags.
  if (!pathname.startsWith("/api/")) {
    const parts = pathname.split("/").filter(Boolean);
    // parts: [board, class, subject, chapter?, page_type?]
    if (parts.length >= 3) tags.push(`subject-${parts[2]}`);
    if (parts.length >= 4) tags.push(`chapter-${parts[3]}`);
  }
  return tags.join(",");
}

function d1JsonResponse(
  data: unknown,
  cors: Record<string, string>,
  remaining: number,
  pathname: string,
): Response {
  const ttl = getCacheTtl(pathname);
  const body = JSON.stringify(data);
  const etag = `W/"d1-${fnv1a32(body)}-${body.length.toString(36)}"`;
  const cacheControl = `public, max-age=${ttl}, stale-while-revalidate=${ttl * 2}`;
  const tags = buildCacheTags(pathname);
  const headers: Record<string, string> = {
    ...cors,
    "Content-Type": "application/json",
    "Cache-Control": cacheControl,
    "Surrogate-Control": cacheControl,
    "Vary": "Accept-Encoding, Accept",
    "ETag": etag,
    "X-Cache": "D1",
    "X-Source": "d1",
    "X-RateLimit-Remaining": String(remaining),
  };
  if (tags) headers["Cache-Tag"] = tags;
  return new Response(body, { status: 200, headers });
}

function d1XmlResponse(
  xml: string,
  cors: Record<string, string>,
  remaining: number,
): Response {
  const etag = `W/"d1-${fnv1a32(xml)}-${xml.length.toString(36)}"`;
  return new Response(xml, {
    status: 200,
    headers: {
      ...cors,
      "Content-Type": "application/xml; charset=utf-8",
      "Cache-Control": "public, max-age=3600, stale-while-revalidate=7200",
      "Surrogate-Control": "public, max-age=3600, stale-while-revalidate=7200",
      "Vary": "Accept-Encoding",
      "Cache-Tag": "sitemap",
      "ETag": etag,
      "X-Cache": "D1",
      "X-Source": "d1",
      "X-RateLimit-Remaining": String(remaining),
    },
  });
}

function buildUrlset(entries: Array<{ loc: string; lastmod: string; pri: string; freq: string; has_assamese?: boolean }>): string {
  const anyAlt = entries.some(e => e.has_assamese);
  const opener = anyAlt
    ? '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">'
    : '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">';
  const lines = ['<?xml version="1.0" encoding="UTF-8"?>', opener];
  for (const e of entries) {
    let alt = "";
    if (e.has_assamese) {
      const sep = e.loc.includes("?") ? "&amp;" : "?";
      const asLoc = `${e.loc}${sep}lang=as`;
      alt =
        `<xhtml:link rel="alternate" hreflang="en" href="${e.loc}"/>` +
        `<xhtml:link rel="alternate" hreflang="as" href="${asLoc}"/>` +
        `<xhtml:link rel="alternate" hreflang="x-default" href="${e.loc}"/>`;
    }
    lines.push(
      `  <url><loc>${e.loc}</loc><lastmod>${e.lastmod}</lastmod><changefreq>${e.freq}</changefreq><priority>${e.pri}</priority>${alt}</url>`
    );
  }
  lines.push("</urlset>");
  return lines.join("\n");
}

/**
 * Compute changefreq + priority from a lastmod date string (YYYY-MM-DD).
 * Task #246 — fresher pages get higher crawl signals so Googlebot returns sooner.
 *   < 7 days  → daily   / 0.9
 *   < 30 days → weekly  / 0.8
 *   older     → monthly / 0.6
 */
function _changefreqFromLastmod(lastmod: string, today: string): { freq: string; pri: string } {
  if (!lastmod || lastmod.length < 10) return { freq: "monthly", pri: "0.6" };
  const diffMs = new Date(today).getTime() - new Date(lastmod.slice(0, 10)).getTime();
  const diffDays = diffMs / 86400000;
  if (diffDays < 7) return { freq: "daily", pri: "0.9" };
  if (diffDays < 30) return { freq: "weekly", pri: "0.8" };
  return { freq: "monthly", pri: "0.6" };
}

function seoPageToSitemapEntry(
  p: { board_slug: string; class_slug: string; subject_slug: string; topic_slug: string; page_type: string; updated_at?: string; created_at?: string },
  today: string,
): { loc: string; lastmod: string; pri: string; freq: string; page_type: string } | null {
  if (!p.board_slug || !p.class_slug || !p.subject_slug || !p.topic_slug) return null;
  if (!SITEMAP_TYPES.includes(p.page_type)) return null;
  const basePath = `/${p.board_slug}/${p.class_slug}/${p.subject_slug}/${p.topic_slug}`;
  const path = p.page_type === "notes" ? basePath : `${basePath}/${p.page_type}`;
  const raw = p.updated_at || p.created_at || "";
  const lastmod = raw && raw.length >= 10 ? raw.slice(0, 10) : today;
  const { freq, pri } = _changefreqFromLastmod(lastmod, today);
  return {
    loc: `${BASE_URL}${path}`,
    lastmod,
    pri,
    freq,
    page_type: p.page_type,
  };
}

type D1RouteResult =
  | { type: "json"; data: unknown }
  | { type: "xml"; data: string }
  | null;

async function tryD1Route(
  env: Env,
  pathname: string,
  searchParams: URLSearchParams,
): Promise<D1RouteResult> {
  const db = env.CONTENT_DB;
  if (!db) return null;

  if (!await isD1Synced(db)) return null;

  if (pathname === "/api/content/library-bundle") {
    const slim = searchParams.get("slim") === "1";
    const requiredTables = slim
      ? ["boards", "classes", "streams", "subjects"]
      : ["boards", "classes", "streams", "subjects", "chapters"];
    for (const table of requiredTables) {
      if (!await isTablePopulated(db, table)) return null;
    }
    const data = slim ? await getLibraryBundleSlim(db) : await getLibraryBundle(db);
    if (data === null) return null;
    return { type: "json", data };
  }

  if (pathname === "/api/content/boards") {
    const data = await getBoards(db);
    if (data === null) return null;
    if (data.length === 0 && !await isTablePopulated(db, "boards")) return null;
    return { type: "json", data };
  }

  if (pathname === "/api/content/classes") {
    const boardId = searchParams.get("board_id") || undefined;
    const data = await getClasses(db, boardId);
    if (data === null) return null;
    if (data.length === 0 && !await isTablePopulated(db, "classes")) return null;
    return { type: "json", data };
  }

  if (pathname === "/api/content/streams") {
    const classId = searchParams.get("class_id") || undefined;
    const data = await getStreams(db, classId);
    if (data === null) return null;
    if (data.length === 0 && !await isTablePopulated(db, "streams")) return null;
    return { type: "json", data };
  }

  if (pathname === "/api/content/subjects") {
    const streamId = searchParams.get("stream_id");
    const classId = searchParams.get("class_id");
    let data: Record<string, unknown>[] | null;
    if (streamId) data = await getSubjectsByStream(db, streamId);
    else if (classId) data = await getSubjectsByClassId(db, classId);
    else data = await getAllSubjects(db);
    if (data === null) return null;
    if (data.length === 0 && !await isTablePopulated(db, "subjects")) return null;
    return { type: "json", data };
  }

  const subjectMatch = pathname.match(/^\/api\/content\/subjects\/([^/]+)$/);
  if (subjectMatch) {
    const data = await getSubjectById(db, subjectMatch[1]);
    return data !== null ? { type: "json", data } : null;
  }

  const chaptersMatch = pathname.match(/^\/api\/content\/chapters\/([^/]+)$/);
  if (chaptersMatch) {
    const data = await getChaptersBySubject(db, chaptersMatch[1]);
    if (data === null) return null;
    if (data.length === 0 && !await isTablePopulated(db, "chapters")) return null;
    return { type: "json", data };
  }

  // /api/content/chapter-by-slug/{board}/{class}/{subject}/{chapter}
  // /api/content/chapter-by-slug/{board}/{class}/{stream}/{subject}/{chapter}
  // Serves the full chapter (including markdown content packed into
  // chapters.extra_json) directly from D1 so the chapter viewer keeps
  // working even when the Railway origin is unreachable.
  const chapterPathMatch = pathname.match(
    /^\/api\/content\/chapter-by-slug\/([^/]+)\/([^/]+)\/([^/]+)\/([^/]+)(?:\/([^/]+))?$/
  );
  if (chapterPathMatch) {
    const [, board, cls, third, fourth, fifth] = chapterPathMatch;
    // 4-segment form: board/class/subject/chapter (third=subject, fourth=chapter)
    // 5-segment form: board/class/stream/subject/chapter (fifth=chapter)
    const hasStream = fifth !== undefined;
    const stream = hasStream ? third : null;
    const subject = hasStream ? fourth : third;
    const chapter = hasStream ? fifth : fourth;
    if (!await isTablePopulated(db, "chapters")) return null;
    const data = await getChapterByPath(db, board, cls, stream, subject, chapter);
    return data !== null ? { type: "json", data } : null;
  }

  const topicMatch = pathname.match(/^\/api\/content\/topic\/([^/]+)$/);
  if (topicMatch) {
    const data = await getTopicsByChapter(db, topicMatch[1]);
    if (data === null) return null;
    if (data.length === 0 && !await isTablePopulated(db, "topics")) return null;
    return { type: "json", data };
  }

  const seoResult = await trySeoD1Route(db, pathname, searchParams);
  if (seoResult !== null) return seoResult;

  return null;
}

async function trySeoD1Route(
  db: D1Database,
  pathname: string,
  searchParams: URLSearchParams,
): Promise<D1RouteResult> {
  if (pathname === "/api/seo/sitemap-entries" || pathname.startsWith("/api/seo/sitemap-entries")) {
    const pageType = searchParams.get("page_type") || undefined;
    const data = await getSitemapEntries(db, pageType);
    if (data === null) return null;
    if (data.length === 0 && !await isTablePopulated(db, "seo_pages")) return null;
    const entries = data as Array<{ board_slug: string; class_slug: string; subject_slug: string; topic_slug: string; page_type: string; updated_at: string }>;
    const result = [];
    for (const p of entries) {
      const path = `/${p.board_slug}/${p.class_slug}/${p.subject_slug}/${p.topic_slug}`;
      const url = p.page_type !== "notes" ? `${path}/${p.page_type}` : path;
      result.push({
        url,
        lastmod: p.updated_at || "",
        priority: p.page_type !== "notes" ? "0.7" : "0.8",
      });
    }
    return { type: "json", data: { entries: result, total: result.length } };
  }

  const pageTypedMatch = pathname.match(/^\/api\/seo\/page\/([^/]+)\/([^/]+)\/([^/]+)\/([^/]+)\/([^/]+)$/);
  if (pageTypedMatch) {
    const [, board, cls, subject, topic, pageType] = pageTypedMatch;
    if (!ALL_PAGE_TYPES.includes(pageType)) return null;
    const data = await getSeoPageBySlugs(db, board, cls, subject, topic, pageType);
    return data !== null ? { type: "json", data } : null;
  }

  const pageDefaultMatch = pathname.match(/^\/api\/seo\/page\/([^/]+)\/([^/]+)\/([^/]+)\/([^/]+)$/);
  if (pageDefaultMatch) {
    const [, board, cls, subject, topic] = pageDefaultMatch;
    const data = await getSeoPageBySlugs(db, board, cls, subject, topic, "notes");
    return data !== null ? { type: "json", data } : null;
  }

  const pageBundleMatch = pathname.match(/^\/api\/seo\/page-bundle\/([^/]+)\/([^/]+)\/([^/]+)\/([^/]+)$/);
  if (pageBundleMatch) {
    const [, board, cls, subject, topic] = pageBundleMatch;
    const pt = searchParams.get("pt") || "notes";
    const pageType = ALL_PAGE_TYPES.includes(pt) ? pt : "notes";
    const data = await getSeoPageBundle(db, board, cls, subject, topic, pageType);
    return data !== null ? { type: "json", data } : null;
  }

  const pageTypesMatch = pathname.match(/^\/api\/seo\/page-types\/([^/]+)\/([^/]+)\/([^/]+)\/([^/]+)$/);
  if (pageTypesMatch) {
    const [, board, cls, subject, topic] = pageTypesMatch;
    const data = await getSeoPageTypes(db, board, cls, subject, topic);
    if (data === null) return null;
    if (data.length === 0 && !await isTablePopulated(db, "seo_pages")) return null;
    return { type: "json", data };
  }

  const sitemapResult = await trySitemapD1Route(db, pathname);
  if (sitemapResult !== null) return sitemapResult;

  return null;
}

async function trySitemapD1Route(
  db: D1Database,
  pathname: string,
): Promise<D1RouteResult> {
  const today = new Date().toISOString().slice(0, 10);

  if (pathname === "/api/seo/sitemap-index.xml") {
    const publishedTypes = await getPublishedPageTypes(db);
    if (publishedTypes === null) return null;

    const alwaysInclude = [
      "sitemap-pages.xml",
      "sitemap-subjects.xml",
      "sitemap-chapters.xml",
      "sitemap-learn.xml",
      "sitemap-notes.xml",
      "sitemap-delta.xml",
    ];
    const typeToSitemap: Record<string, string> = {
      "mcqs": "sitemap-mcqs.xml",
      "important-questions": "sitemap-pyqs.xml",
      "examples": "sitemap-examples.xml",
      "definition": "sitemap-definitions.xml",
      "faq": "sitemap-faq.xml",
    };
    const sitemapNames = [...alwaysInclude];
    for (const [pt, smName] of Object.entries(typeToSitemap)) {
      if (publishedTypes.includes(pt)) {
        sitemapNames.push(smName);
      }
    }

    const lines = [
      '<?xml version="1.0" encoding="UTF-8"?>',
      '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ];
    for (const name of sitemapNames) {
      // sitemap-delta.xml is served at the root path (canonical per task #246);
      // all other sub-sitemaps live under /api/seo/.
      const loc = name === "sitemap-delta.xml"
        ? `${BASE_URL}/${name}`
        : `${BASE_URL}/api/seo/${name}`;
      lines.push(`  <sitemap><loc>${loc}</loc><lastmod>${today}</lastmod></sitemap>`);
    }
    lines.push("</sitemapindex>");
    return { type: "xml", data: lines.join("\n") };
  }

  if (pathname === "/api/seo/sitemap-pages.xml") {
    const stableDate = "2026-04-01";
    const entries = STATIC_PAGES.map(([path, freq, pri]) => ({
      loc: `${BASE_URL}${path}`, lastmod: stableDate, pri, freq,
    }));
    return { type: "xml", data: buildUrlset(entries) };
  }

  if (pathname === "/api/seo/sitemap-subjects.xml") {
    const subjectEntries = await getSubjectSitemapEntries(db);
    if (subjectEntries === null) return null;
    const weekAgo = new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10);
    const entries = subjectEntries.map(e => ({
      loc: `${BASE_URL}/${e.board_slug}/${e.class_slug}/${e.subject_slug}`,
      lastmod: weekAgo, pri: "0.7", freq: "weekly",
    }));
    return { type: "xml", data: buildUrlset(entries) };
  }

  if (pathname === "/api/seo/sitemap-chapters.xml") {
    const chapterEntries = await getChapterSitemapEntries(db);
    if (chapterEntries === null) return null;
    const entries = chapterEntries.map(e => {
      const lastmod = e.updated_at && e.updated_at.length >= 10 ? e.updated_at.slice(0, 10) : today;
      const { freq, pri } = _changefreqFromLastmod(lastmod, today);
      return {
        loc: `${BASE_URL}/${e.board_slug}/${e.class_slug}/${e.subject_slug}/${e.chapter_slug}`,
        lastmod, pri, freq,
        has_assamese: e.has_assamese,
      };
    });
    return { type: "xml", data: buildUrlset(entries) };
  }

  const seoTypeMap: Record<string, string> = {
    "/api/seo/sitemap-notes.xml": "notes",
    "/api/seo/sitemap-mcqs.xml": "mcqs",
    "/api/seo/sitemap-pyqs.xml": "important-questions",
    "/api/seo/sitemap-examples.xml": "examples",
    "/api/seo/sitemap-definitions.xml": "definition",
    "/api/seo/sitemap-faq.xml": "faq",
  };

  const seoPageType = seoTypeMap[pathname];
  if (seoPageType) {
    const pages = await getSeoPagesByType(db, seoPageType);
    if (pages === null) return null;
    const entries: Array<{ loc: string; lastmod: string; pri: string; freq: string }> = [];
    for (const p of pages) {
      const entry = seoPageToSitemapEntry(p, today);
      if (entry && entry.page_type === seoPageType) {
        entries.push({ loc: entry.loc, lastmod: entry.lastmod, pri: entry.pri, freq: entry.freq });
      }
    }
    return { type: "xml", data: buildUrlset(entries) };
  }

  if (pathname === "/api/seo/sitemap.xml") {
    const pages = await getSeoPagesByType(db, "");
    if (pages !== null) {
      const allPages = await getSitemapEntries(db);
      if (allPages === null) return null;
      const seoEntries: Array<{ loc: string; lastmod: string; pri: string; freq: string }> = [];
      const staticEntries = STATIC_PAGES.map(([path, freq, pri]) => ({
        loc: `${BASE_URL}${path}`, lastmod: today, pri, freq,
      }));
      for (const p of allPages as Array<{ board_slug: string; class_slug: string; subject_slug: string; topic_slug: string; page_type: string; updated_at: string }>) {
        const entry = seoPageToSitemapEntry(p, today);
        if (entry) {
          seoEntries.push({ loc: entry.loc, lastmod: entry.lastmod, pri: entry.pri, freq: entry.freq });
        }
      }
      return { type: "xml", data: buildUrlset([...staticEntries, ...seoEntries]) };
    }
    return null;
  }

  // Task #246 — Delta sitemap: pages updated in the last 48 hours, capped at 1000.
  // Crawlers that ping us after an IndexNow/Google notification can re-fetch
  // this small sub-sitemap to discover exactly which pages changed without
  // crawling the full (potentially 50k-URL) sitemap tree.
  // Cache-Control is set in the outer response handler when type === "xml"
  // for delta routes.
  // Accept both the canonical root path and the /api/seo/ alias so that
  // existing sitemap-index registrations and fanout pings continue to work.
  if (pathname === "/sitemap-delta.xml" || pathname === "/api/seo/sitemap-delta.xml") {
    const since48h = new Date(Date.now() - 48 * 3600 * 1000).toISOString();
    const deltaPages = await getDeltaSitemapEntries(db, since48h, 1000);
    if (deltaPages === null) return null;
    const entries: Array<{ loc: string; lastmod: string; pri: string; freq: string }> = [];
    for (const p of deltaPages) {
      const entry = seoPageToSitemapEntry(p, today);
      if (entry) {
        entries.push({ loc: entry.loc, lastmod: entry.lastmod, pri: entry.pri, freq: entry.freq });
      }
    }
    return { type: "xml", data: buildUrlset(entries) };
  }

  return null;
}

async function handleSyncRequest(
  request: Request,
  env: Env,
  cors: Record<string, string>,
): Promise<Response> {
  const authHeader = request.headers.get("Authorization");
  const expectedToken = env.D1_SYNC_SECRET;
  if (!expectedToken || expectedToken === "REPLACE_WITH_SECURE_RANDOM_SECRET") {
    return new Response(JSON.stringify({ error: "D1 sync secret not configured" }), {
      status: 500,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  if (!authHeader || authHeader !== `Bearer ${expectedToken}`) {
    return new Response(JSON.stringify({ error: "Unauthorized" }), {
      status: 401,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  try {
    const payload = await request.json() as Record<string, unknown>;
    const result = await syncFromPayload(env.CONTENT_DB, payload);
    resetD1SyncedCache();
    return new Response(JSON.stringify(result), {
      status: 200,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : "Unknown error";
    return new Response(JSON.stringify({ error: message }), {
      status: 500,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }
}

async function handleSyncStatus(
  env: Env,
  cors: Record<string, string>,
): Promise<Response> {
  const status = await getSyncStatus(env.CONTENT_DB);
  return new Response(JSON.stringify(status), {
    status: 200,
    headers: { ...cors, "Content-Type": "application/json" },
  });
}

async function handleEdgePurge(
  request: Request,
  env: Env,
  cors: Record<string, string>,
  ctx: ExecutionContext,
): Promise<Response> {
  const authHeader = request.headers.get("Authorization");
  const expectedToken = env.D1_SYNC_SECRET;
  if (!expectedToken || !authHeader || authHeader !== `Bearer ${expectedToken}`) {
    return new Response(JSON.stringify({ error: "Unauthorized" }), {
      status: 401,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  try {
    const body = await request.json() as { prefixes?: string[]; purge_all?: boolean; urls?: string[] };
    const cache = caches.default;
    let purgedCount = 0;
    const baseUrl = new URL(request.url).origin;

    if (body.purge_all) {
      const purgeKeys: string[] = [];
      for (const prefix of CACHEABLE_PREFIXES) {
        purgeKeys.push(prefix);
      }
      purgeKeys.push("/api/content/library-bundle?slim=1");
      for (const key of purgeKeys) {
        const cacheKey = new Request(`${baseUrl}${key}`, { method: "GET" });
        const deleted = await cache.delete(cacheKey);
        if (deleted) purgedCount++;
      }
    }

    if (body.prefixes && Array.isArray(body.prefixes)) {
      for (const prefix of body.prefixes) {
        const cacheKey = new Request(`${baseUrl}${prefix}`, { method: "GET" });
        const deleted = await cache.delete(cacheKey);
        if (deleted) purgedCount++;
      }
    }

    if (body.urls && Array.isArray(body.urls)) {
      for (const url of body.urls) {
        const fullUrl = url.startsWith("http") ? url : `${baseUrl}${url}`;
        const cacheKey = new Request(fullUrl, { method: "GET" });
        const deleted = await cache.delete(cacheKey);
        if (deleted) purgedCount++;
      }
    }

    return new Response(
      JSON.stringify({ ok: true, purged: purgedCount }),
      { status: 200, headers: { ...cors, "Content-Type": "application/json" } },
    );
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : "Unknown error";
    return new Response(JSON.stringify({ error: message }), {
      status: 500,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }
}

// Task #10: "notes" is a first-class hub prefix (/notes, /notes/class-11,
// /notes/class-12) and must be included alongside the board slugs so the
// board and board-class cache-key patterns match it.
const _KNOWN_BOARDS = new Set(["ahsec", "seba", "degree", "cbse", "nep", "notes"]);

const BOT_CONTENT_PATTERNS: Array<{ regex: RegExp; type: string; test?: (p: string) => boolean }> = [
  { regex: /^\/([a-z0-9-]+)\/([a-z0-9-]+)\/([a-z0-9-]+)\/([a-z0-9-]+)\/(notes|mcqs|important-questions|examples|definition|faq)$/, type: "topic-typed" },
  { regex: /^\/([a-z0-9-]+)\/([a-z0-9-]+)\/([a-z0-9-]+)\/([a-z0-9-]+)$/, type: "topic" },
  { regex: /^\/([a-z0-9-]+)\/([a-z0-9-]+)\/([a-z0-9-]+)$/, type: "subject" },
  { regex: /^\/([a-z0-9-]+)\/([a-z0-9-]+)$/, type: "board-class", test: (p: string) => _KNOWN_BOARDS.has(p.split("/").filter(Boolean)[0]) },
  { regex: /^\/([a-z0-9-]+)$/, type: "board", test: (p: string) => _KNOWN_BOARDS.has(p.split("/").filter(Boolean)[0]) },
  { regex: /^\/learn\/([a-z0-9-]+)$/, type: "learn" },
  { regex: /^\/pyq\/([a-z0-9-]+)$/, type: "pyq" },
];

// Task #499: every entry here is a route the origin's BotRenderMiddleware
// returns a route-specific <link rel="canonical"> for. Adding a path here
// gives it its own bot-render cache slot at the edge — without that, two
// distinct URLs (e.g. /technology and /about) would collide on the same
// cache key and one of them would inherit the other's canonical, failing
// the Lighthouse `canonical` SEO audit. Auth-shell routes (/login,
// /signup, /profile, /admin/login) are noindex,follow but still need a
// self-referential canonical to pass the audit.
const BOT_STATIC_PAGES = new Set([
  "/", "/home", "/library", "/pricing", "/terms", "/privacy",
  "/about", "/technology", "/curriculum", "/exam-routine", "/chat",
  "/login", "/signup", "/profile", "/admin/login",
]);

const BOT_SKIP_EXTENSIONS = /\.(js|css|png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|eot|map|json|webp|avif|mp4|webm)$/i;

const BOT_CACHE_TTL_CONTENT = 3600;
const BOT_CACHE_TTL_STATIC = 86400;

export function getBotPageCacheKey(pathname: string): string | null {
  const clean = pathname.replace(/\/+$/, "") || "/";

  if (BOT_SKIP_EXTENSIONS.test(clean)) return null;
  // Task #499: an audited route in BOT_STATIC_PAGES (e.g. /profile,
  // /admin/login) MUST be allowed through the bot path so the origin
  // can return its route-specific canonical. We therefore short-circuit
  // the skip-prefix check below for any path explicitly listed as a
  // static bot page. Real admin surfaces (/admin/api, /admin/console)
  // are not listed and continue to be skipped.
  if (BOT_STATIC_PAGES.has(clean)) return `bot:static:${clean}`;
  if (clean.startsWith("/api/") ||
      clean.startsWith("/admin/api") || clean.startsWith("/admin/console") ||
      clean.startsWith("/static/") || clean.startsWith("/assets/") ||
      clean.startsWith("/icons/") || clean.startsWith("/fonts/") ||
      clean.startsWith("/history")) {
    return null;
  }

  for (const pat of BOT_CONTENT_PATTERNS) {
    if (pat.regex.test(clean)) {
      if (pat.test && !pat.test(clean)) continue;
      return `bot:content:${clean}`;
    }
  }
  return null;
}

export function getBotCacheTtl(cacheKey: string): number {
  return cacheKey.startsWith("bot:static:") ? BOT_CACHE_TTL_STATIC : BOT_CACHE_TTL_CONTENT;
}

function _botResponseCacheTtl(pathname: string): number {
  const clean = pathname.replace(/\/+$/, "") || "/";
  if (BOT_STATIC_PAGES.has(clean)) return BOT_CACHE_TTL_STATIC;
  return BOT_CACHE_TTL_CONTENT;
}

export function resolveBotApiUrl(env: Env, pathname: string): string | null {
  const clean = pathname.replace(/\/+$/, "") || "/";
  const seoBase = `${env.BACKEND_URL}/api/seo`;

  if (clean === "/" || clean === "/library") return `${seoBase}/html/homepage`;
  if (clean === "/about") return `${seoBase}/html/about`;
  if (
    // Task #499: route every audited public/auth-shell page directly
    // to the origin so BotRenderMiddleware emits its route-specific
    // canonical (https://syrabit.ai/<path>) — including /home, which
    // must NOT alias the homepage canonical, plus /technology, /login,
    // /signup, /profile, /admin/login.
    clean === "/home" || clean === "/technology" ||
    clean === "/pricing" || clean === "/terms" || clean === "/privacy" ||
    clean === "/curriculum" || clean === "/exam-routine" || clean === "/chat" ||
    clean === "/login" || clean === "/signup" || clean === "/profile" ||
    clean === "/admin/login"
  ) {
    return `${env.BACKEND_URL}${clean}`;
  }
  if (clean.startsWith("/learn/")) return `${env.BACKEND_URL}${clean}`;
  if (clean.startsWith("/pyq/")) return `${env.BACKEND_URL}${clean}`;

  const parts = clean.split("/").filter(Boolean);
  if (parts.length === 1 && _KNOWN_BOARDS.has(parts[0])) return `${env.BACKEND_URL}${clean}`;
  if (parts.length === 2 && _KNOWN_BOARDS.has(parts[0])) return `${env.BACKEND_URL}${clean}`;
  if (parts.length === 3) return `${seoBase}/html/subject/${parts[0]}/${parts[1]}/${parts[2]}`;
  if (parts.length === 4) return `${seoBase}/html/${parts[0]}/${parts[1]}/${parts[2]}/${parts[3]}`;
  if (parts.length === 5) return `${seoBase}/html/${parts[0]}/${parts[1]}/${parts[2]}/${parts[3]}/${parts[4]}`;
  return null;
}

/**
 * Task #907 — Cheap HEAD probe to recover the backend's authoritative
 * `Last-Modified` for an existing legacy KV entry that pre-dates the
 * JSON wrapper introduced in Task #896. We use this only on the
 * background upgrade path so first-hit latency is unaffected. Returns
 * the upstream RFC 7231 date string when present and parseable; null
 * otherwise (e.g. backend doesn't support HEAD, omits the header, or
 * the network request fails) — callers must fall back to the
 * synthesized "now" timestamp.
 */
export async function probeBotLastModified(
  env: Env,
  pathname: string,
  clientIp: string,
  request: Request,
): Promise<string | null> {
  const apiUrl = resolveBotApiUrl(env, pathname);
  if (!apiUrl) return null;
  try {
    const proxyHeaders = buildProxyHeaders(request, clientIp, env);
    proxyHeaders.set("X-Bot-Request", "1");
    // Tell the backend this is a metadata-only probe so it can skip
    // any expensive render work and just emit headers.
    proxyHeaders.set("X-Bot-Probe", "1");
    await addMtlsActiveHeader(proxyHeaders, env);
    // Strip any inbound conditional headers — a crawler that arrived
    // with `If-None-Match` / `If-Modified-Since` would otherwise
    // induce a 304 from the backend, which carries no
    // `Last-Modified` and would force us back to the synthesized
    // fallback even when the upstream has an authoritative date.
    proxyHeaders.delete("If-None-Match");
    proxyHeaders.delete("If-Modified-Since");
    proxyHeaders.delete("If-Match");
    proxyHeaders.delete("If-Unmodified-Since");
    proxyHeaders.delete("If-Range");
    const resp = await fetchBackend(env, apiUrl, { method: "HEAD", headers: proxyHeaders });
    if (!resp.ok) return null;
    const lm = resp.headers.get("Last-Modified");
    if (!lm) return null;
    if (parseHttpDate(lm) === null) return null;
    return lm;
  } catch {
    return null;
  }
}

async function fetchBotRenderedHtml(
  env: Env,
  pathname: string,
  clientIp: string,
  request: Request,
): Promise<Response | null> {
  const apiUrl = resolveBotApiUrl(env, pathname);
  if (apiUrl === null) return null;
  const clean = pathname.replace(/\/+$/, "") || "/";

  try {
    const proxyHeaders = buildProxyHeaders(request, clientIp, env);
    proxyHeaders.set("X-Bot-Request", "1");
    await addMtlsActiveHeader(proxyHeaders, env);
    const resp = await fetchBackend(env, apiUrl, {
      method: "GET",
      headers: proxyHeaders,
    });

    if (!resp.ok) {
      const parts = clean.split("/").filter(Boolean);
      if (parts.length >= 3 && parts.length <= 5) {
        const fallbackUrl = `${env.BACKEND_URL}${clean}`;
        const fallbackResp = await fetchBackend(env, fallbackUrl, {
          method: "GET",
          headers: proxyHeaders,
        });
        if (fallbackResp.ok) {
          const fct = fallbackResp.headers.get("Content-Type") || "";
          if (fct.includes("text/html")) {
            const fbody = await fallbackResp.text();
            if (fbody && fbody.length >= 100) {
              const fbTtl = _botResponseCacheTtl(pathname);
              const fbHeaders: Record<string, string> = {
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": `public, max-age=${fbTtl}, s-maxage=${fbTtl * 2}`,
                "X-Bot-Rendered": "1",
                "X-Source": "bot-prerender-fallback",
                "Vary": "User-Agent",
                "X-Robots-Tag": "index, follow",
                "Content-Language": "en-IN",
              };
              const fbLm = fallbackResp.headers.get("Last-Modified");
              if (fbLm) fbHeaders["Last-Modified"] = fbLm;
              return new Response(fbody, { status: 200, headers: fbHeaders });
            }
          }
        }
      }
      return null;
    }

    const ct = resp.headers.get("Content-Type") || "";
    if (!ct.includes("text/html") && !ct.includes("text/xml")) {
      return null;
    }

    const body = await resp.text();
    if (!body || body.length < 100) return null;

    const respTtl = _botResponseCacheTtl(pathname);
    const respHeaders: Record<string, string> = {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": `public, max-age=${respTtl}, s-maxage=${respTtl * 2}`,
      "X-Bot-Rendered": "1",
      "X-Source": "bot-prerender",
      "Vary": "User-Agent",
      "X-Robots-Tag": "index, follow",
      "Content-Language": "en-IN",
    };
    // Carry the backend's authoritative Last-Modified (sourced from
    // seo_pages.updated_at) up to the bot-cache layer so it can store it
    // in KV and emit it to crawlers — this is what makes 304s correct.
    const upstreamLm = resp.headers.get("Last-Modified");
    if (upstreamLm) respHeaders["Last-Modified"] = upstreamLm;
    return new Response(body, { status: 200, headers: respHeaders });
  } catch {
    return null;
  }
}

export interface BotCacheEntry {
  body: string;
  lastmod: string;
  etag: string;
}

export function formatRfc7231(d: Date): string {
  return d.toUTCString();
}

export function parseHttpDate(value: string | null | undefined): number | null {
  if (!value) return null;
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Date.parse(trimmed);
  if (Number.isNaN(parsed)) return null;
  return parsed;
}

export async function computeEtag(body: string): Promise<string> {
  const enc = new TextEncoder().encode(body);
  const buf = await crypto.subtle.digest("SHA-256", enc);
  const arr = Array.from(new Uint8Array(buf));
  return arr.slice(0, 6).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export function parseBotCacheEntry(raw: string | null | undefined): BotCacheEntry | null {
  if (!raw) return null;
  try {
    const obj = JSON.parse(raw);
    if (
      obj && typeof obj.body === "string" &&
      typeof obj.lastmod === "string" && typeof obj.etag === "string"
    ) {
      return obj as BotCacheEntry;
    }
  } catch { /* fall through */ }
  return null;
}

export function ifNoneMatchMatches(header: string | null | undefined, etag: string): boolean {
  if (!header) return false;
  const trimmed = header.trim();
  if (!trimmed) return false;
  if (trimmed === "*") return true;
  return trimmed.split(",").some((tok) => {
    let v = tok.trim();
    if (!v) return false;
    if (v.startsWith("W/")) v = v.slice(2);
    if (v.length >= 2 && v.startsWith('"') && v.endsWith('"')) v = v.slice(1, -1);
    return v === etag;
  });
}

export function shouldReturn304(
  request: Request,
  etag: string,
  lastmodMs: number,
): boolean {
  const inm = request.headers.get("If-None-Match");
  if (inm) return ifNoneMatchMatches(inm, etag);
  const ims = request.headers.get("If-Modified-Since");
  if (!ims) return false;
  const parsed = parseHttpDate(ims);
  if (parsed === null) return false; // never 304 on parse failure
  // Drop sub-second precision on the cache side too — RFC 7232 Last-Modified
  // resolution is one second.
  return Math.floor(lastmodMs / 1000) <= Math.floor(parsed / 1000);
}

function buildBotCacheHeaders(
  cacheTtl: number,
  lastmod: string,
  etag: string,
  source: string,
): Record<string, string> {
  return {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": `public, max-age=${cacheTtl}, s-maxage=${cacheTtl * 2}`,
    "X-Bot-Rendered": "1",
    "X-Cache": source === "bot-cache" ? "BOT-KV-HIT" : "BOT-KV-MISS",
    "X-Source": source,
    "Vary": "User-Agent",
    "X-Robots-Tag": "index, follow",
    "Content-Language": "en-IN",
    "Last-Modified": lastmod,
    "ETag": `"${etag}"`,
  };
}

export async function handleBotContentRequest(
  env: Env,
  pathname: string,
  clientIp: string,
  request: Request,
  ctx: ExecutionContext,
): Promise<Response | null> {
  const cacheKey = getBotPageCacheKey(pathname);
  if (!cacheKey) return null;

  const cacheTtl = getBotCacheTtl(cacheKey);

  if (env.BOT_HTML_CACHE) {
    try {
      const raw = await env.BOT_HTML_CACHE.get(cacheKey);
      if (raw) {
        let entry = parseBotCacheEntry(raw);
        if (!entry) {
          // Legacy entry written as a plain HTML string before this header
          // wrapper landed. Synthesize lastmod=now and a body-derived etag
          // so we still emit conditional headers — the worst case is a
          // single full-body response per legacy entry until it expires.
          const etag = await computeEtag(raw);
          const synthesizedLm = formatRfc7231(new Date());
          entry = { body: raw, lastmod: synthesizedLm, etag };
          // Task #908 — count this legacy hit so the bot-cache dashboard
          // shows the migration burn-down alongside hit/miss/304/fallback.
          // Recorded once per legacy hit (before we enqueue the rewrite)
          // so the counter equals "legacy entries observed in the rolling
          // hour", not "rewrite attempts". When the counter trends to
          // zero we know the Task #896 migration is done and the legacy
          // branch can be removed.
          recordBotCacheEvent(env.RATE_LIMIT, "legacy_upgrade", ctx);
          // Upgrade the KV value to the JSON wrapper in the background so
          // subsequent reads of this key return a stable Last-Modified
          // instead of a fresh "now" each time — which would otherwise
          // mislead crawlers about content age (Task #896). Task #907 —
          // before persisting, try a cheap HEAD probe at the backend so
          // we can prefer its authoritative `Last-Modified` over the
          // synthesized "now-at-first-read"; falls back to the
          // synthesized value when the probe is unavailable so there's
          // no regression vs. Task #896.
          if (env.BOT_HTML_CACHE) {
            const baseEntry = entry;
            const cache = env.BOT_HTML_CACHE;
            ctx.waitUntil((async () => {
              let upgradedLm = baseEntry.lastmod;
              try {
                const probedLm = await probeBotLastModified(
                  env,
                  pathname,
                  clientIp,
                  request,
                );
                if (probedLm) upgradedLm = probedLm;
              } catch { /* keep synthesized */ }
              const upgraded: BotCacheEntry = {
                body: baseEntry.body,
                etag: baseEntry.etag,
                lastmod: upgradedLm,
              };
              await cache
                .put(cacheKey, JSON.stringify(upgraded), {
                  expirationTtl: cacheTtl,
                })
                .catch(() => {});
            })());
          }
        }
        const lastmodMs = parseHttpDate(entry.lastmod) ?? Date.now();
        const headers = buildBotCacheHeaders(cacheTtl, entry.lastmod, entry.etag, "bot-cache");
        if (shouldReturn304(request, entry.etag, lastmodMs)) {
          // Task #885 — KV had the entry AND the crawler's
          // If-None-Match / If-Modified-Since matches: cheapest path.
          recordBotCacheEvent(env.RATE_LIMIT, "conditional_304", ctx);
          return new Response(null, { status: 304, headers });
        }
        // Task #885 — KV-served full body. The hit-rate metric uses
        // this counter as its numerator.
        recordBotCacheEvent(env.RATE_LIMIT, "hit", ctx);
        return new Response(entry.body, { status: 200, headers });
      }
    } catch { /* fall through */ }
  }

  const rendered = await fetchBotRenderedHtml(env, pathname, clientIp, request);
  if (!rendered) return null;

  const htmlBody = await rendered.clone().text();
  const etag = await computeEtag(htmlBody);
  // Prefer the page's authoritative `updated_at` carried by the backend in
  // the upstream `Last-Modified` header (RFC 7231). Only fall back to "now"
  // if the upstream omits it or the value can't be parsed — in which case
  // the timestamp is still monotonic across the page's lifetime within KV.
  const upstreamLm = rendered.headers.get("Last-Modified");
  const lastmod = upstreamLm && parseHttpDate(upstreamLm) !== null
    ? upstreamLm
    : formatRfc7231(new Date());

  if (env.BOT_HTML_CACHE) {
    const entry: BotCacheEntry = { body: htmlBody, lastmod, etag };
    ctx.waitUntil(
      env.BOT_HTML_CACHE.put(cacheKey, JSON.stringify(entry), { expirationTtl: cacheTtl })
        .catch(() => {})
    );
  }

  const headers = buildBotCacheHeaders(cacheTtl, lastmod, etag, "bot-prerender");
  // Preserve any explicit X-Source set by the renderer (e.g.
  // bot-prerender-fallback) so observability stays accurate.
  const renderedSource = rendered.headers.get("X-Source");
  if (renderedSource) headers["X-Source"] = renderedSource;
  // Task #885 — distinguish a normal KV miss (we paid the prerender
  // round-trip but the SEO HTML pipeline served us) from a "fallback"
  // miss (the prerender pipeline failed and we served the live origin
  // HTML via bot-prerender-fallback). The latter is a degraded mode
  // and a sustained spike is operationally important.
  if (renderedSource === "bot-prerender-fallback") {
    recordBotCacheEvent(env.RATE_LIMIT, "fallback", ctx);
  } else {
    recordBotCacheEvent(env.RATE_LIMIT, "miss", ctx);
  }
  if (shouldReturn304(request, etag, parseHttpDate(lastmod) ?? Date.now())) {
    return new Response(null, { status: 304, headers });
  }
  return new Response(htmlBody, { status: 200, headers });
}

// ─── Task #636: Workers AI fallback fan-out ────────────────────────────────
// The FastAPI backend posts here only after its primary provider has
// failed with a retryable error (timeout / 5xx / 429 / quota). The
// shapes are normalised so the backend can call a single client and
// not care about Workers AI's per-model quirks.
// Enterprise Workers AI models — upgraded from 8B/base to 70B/large tiers.
// All models below are available on Enterprise plan via the Workers AI catalog.
//   chat  → llama-3.3-70b-instruct-fp8-fast: 70B param, fp8 quantised for
//            low-latency; best-in-class for Indian-English educational content.
//   embed → bge-large-en-v1.5: 335M, 1024-dim output — matches our
//            syllabus-index-v2 Vectorize index dimensions exactly.
//   stt   → whisper-large-v3-turbo: improved Assamese/Bengali accent handling
//            vs the base whisper model used previously.
//   tts   → melotts (unchanged — no larger variant available in Workers AI)
const WORKERS_AI_MODELS = {
  chat: "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
  embed: "@cf/baai/bge-large-en-v1.5",
  stt: "@cf/openai/whisper-large-v3-turbo",
  tts: "@cf/myshell-ai/melotts",
} as const;
type AiCapability = keyof typeof WORKERS_AI_MODELS;

// Task #306 — build the AI Gateway options bag for env.AI.run(model, payload, opts).
// When WORKERS_AI_GATEWAY_ID is unset (local dev / before the gateway is
// provisioned in the dashboard) returns `undefined` so env.AI.run falls
// back to its 2-argument shape and the call is unaffected.
function aiGatewayOpts(env: Env, tag: string): { gateway: { id: string; metadata: { tag: string } } } | undefined {
  if (!env.WORKERS_AI_GATEWAY_ID) return undefined;
  return { gateway: { id: env.WORKERS_AI_GATEWAY_ID, metadata: { tag } } };
}

interface AiFallbackResultMeta {
  capability: AiCapability;
  model: string;
  duration_ms: number;
  edge_colo: string;
}

function aiFallbackResponse(
  body: Record<string, unknown>,
  cors: Record<string, string>,
  status = 200,
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      ...cors,
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      "X-Source": "workers-ai-fallback",
    },
  });
}

async function handleAiFallback(
  request: Request,
  env: Env,
  cors: Record<string, string>,
  capability: AiCapability,
): Promise<Response> {
  const provided = request.headers.get("X-Edge-AI-Secret") || "";
  if (
    !env.EDGE_AI_FALLBACK_SECRET ||
    provided !== env.EDGE_AI_FALLBACK_SECRET
  ) {
    return aiFallbackResponse(
      { ok: false, error: "unauthorized", capability },
      cors,
      401,
    );
  }
  if (!env.AI || typeof env.AI.run !== "function") {
    return aiFallbackResponse(
      { ok: false, error: "ai_binding_missing", capability },
      cors,
      503,
    );
  }

  let body: Record<string, unknown>;
  try {
    body = (await request.json()) as Record<string, unknown>;
  } catch {
    return aiFallbackResponse(
      { ok: false, error: "invalid_json", capability },
      cors,
      400,
    );
  }

  const model = WORKERS_AI_MODELS[capability];
  const colo =
    (request as unknown as { cf?: { colo?: string } }).cf?.colo || "unknown";
  const t0 = Date.now();

  try {
    let payload: Record<string, unknown>;
    if (capability === "chat") {
      const messages = Array.isArray(body.messages) ? body.messages : null;
      if (!messages || messages.length === 0) {
        return aiFallbackResponse(
          { ok: false, error: "messages_required", capability },
          cors,
          400,
        );
      }
      payload = {
        messages,
        max_tokens: typeof body.max_tokens === "number" ? body.max_tokens : 1024,
        temperature:
          typeof body.temperature === "number" ? body.temperature : 0.3,
      };
    } else if (capability === "embed") {
      const text = body.text;
      if (!text || (typeof text !== "string" && !Array.isArray(text))) {
        return aiFallbackResponse(
          { ok: false, error: "text_required", capability },
          cors,
          400,
        );
      }
      payload = { text };
    } else if (capability === "tts") {
      const prompt =
        typeof body.text === "string"
          ? (body.text as string)
          : typeof body.prompt === "string"
            ? (body.prompt as string)
            : "";
      if (!prompt) {
        return aiFallbackResponse(
          { ok: false, error: "text_required", capability },
          cors,
          400,
        );
      }
      payload = {
        prompt: prompt.slice(0, 1000),
        lang: typeof body.lang === "string" ? body.lang : "en",
      };
    } else {
      // stt
      const audioB64 = typeof body.audio_base64 === "string" ? body.audio_base64 : "";
      if (!audioB64) {
        return aiFallbackResponse(
          { ok: false, error: "audio_base64_required", capability },
          cors,
          400,
        );
      }
      // Workers AI whisper expects a Uint8Array.
      const binary = atob(audioB64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      payload = { audio: Array.from(bytes) };
    }

    const out = (await env.AI.run(
      model,
      payload,
      aiGatewayOpts(env, `workers-ai-fallback:${capability}`),
    )) as Record<string, unknown> & { response?: string; data?: number[][] };

    const meta: AiFallbackResultMeta = {
      capability,
      model,
      duration_ms: Date.now() - t0,
      edge_colo: colo,
    };

    let normalised: Record<string, unknown>;
    if (capability === "chat") {
      normalised = { text: typeof out.response === "string" ? out.response : "" };
    } else if (capability === "embed") {
      normalised = { vectors: Array.isArray(out.data) ? out.data : [] };
    } else if (capability === "tts") {
      // melotts returns { audio: number[] } in its WAV bytes form.
      const audio = (out as { audio?: number[] }).audio || [];
      const buf = new Uint8Array(audio);
      let bin = "";
      for (let i = 0; i < buf.length; i++) bin += String.fromCharCode(buf[i]);
      normalised = { audio_base64: btoa(bin), format: "wav" };
    } else {
      normalised = { text: typeof out.text === "string" ? out.text : "" };
    }

    console.log(
      `[workers-ai-fallback] capability=${capability} model=${model} ` +
      `duration_ms=${meta.duration_ms} colo=${colo} ok=true`,
    );
    return aiFallbackResponse(
      { ok: true, provider: "workers-ai", meta, ...normalised },
      cors,
    );
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "unknown";
    const dur = Date.now() - t0;
    console.warn(
      `[workers-ai-fallback] capability=${capability} model=${model} ` +
      `duration_ms=${dur} colo=${colo} ok=false err=${msg.slice(0, 200)}`,
    );
    return aiFallbackResponse(
      { ok: false, provider: "workers-ai", error: msg.slice(0, 300), capability },
      cors,
      502,
    );
  }
}

async function handleScheduledSync(env: Env): Promise<void> {
  if (!env.CONTENT_DB || !env.BACKEND_URL) return;

  try {
    // X-Origin-Auth required by OriginSharedSecretMiddleware on the backend
    // (Bearer token alone is insufficient — /api/admin/d1-export is not in
    // the open-paths list, so the cron silently 403s without this header).
    const syncHeaders: Record<string, string> = {
      "Authorization": `Bearer ${env.D1_SYNC_SECRET}`,
      "Content-Type": "application/json",
    };
    if (env.BACKEND_ORIGIN_SECRET) {
      syncHeaders["X-Origin-Auth"] = env.BACKEND_ORIGIN_SECRET;
    }
    // Task #120: inject the HMAC proof that the CF Worker is making this
    // request with the mTLS cert bound — validates against
    // MtlsClientCertMiddleware on the backend when ENFORCE_MTLS=true.
    await addMtlsActiveHeader(syncHeaders, env);
    // Phase 6 (Task #110): use fetchBackend() so the mTLS cert is presented
    // on this scheduled cron fetch too — Railway mTLS enforcement applies to
    // all connections, not just the primary request proxy path.
    const resp = await fetchBackend(env, `${env.BACKEND_URL}/api/admin/d1-export`, {
      method: "GET",
      headers: syncHeaders,
    });

    if (!resp.ok) {
      console.error(`D1 scheduled sync failed: backend returned ${resp.status}`);
      return;
    }

    const payload = await resp.json() as Record<string, unknown>;
    const result = await syncFromPayload(env.CONTENT_DB, payload);
    console.log(`D1 scheduled sync complete:`, JSON.stringify(result));
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : "Unknown error";
    console.error(`D1 scheduled sync error: ${message}`);
  }
}

// ── Google Tag Gateway ────────────────────────────────────────────────────────
// Proxies GA4 / GTM requests through api.syrabit.ai so they originate from a
// first-party origin, bypassing ad-blocker lists that block googletagmanager.com
// and google-analytics.com. The route /gtag/* is matched in _handleEdgeFetch
// before any backend proxy logic runs, so no request ever reaches Railway.
//
// URL mapping:
//   /gtag/js?id=G-...       → https://www.googletagmanager.com/gtag/js?id=G-...
//   /gtag/gtm.js?id=GTM-... → https://www.googletagmanager.com/gtm.js?id=GTM-...
//   /gtag/collect            → https://www.google-analytics.com/g/collect   (POST)
//
// To activate the gateway in the frontend, update ga4Plugin() in vite.config.js:
//   Replace: s.src='https://www.googletagmanager.com/gtag/js?id=${id}';
//   With:    s.src='/gtag/js?id=${id}';
// and update any sendBeacon / fetch beacon URLs similarly.
async function handleGtagGateway(
  request: Request,
  pathname: string,
  url: URL,
): Promise<Response> {
  let upstreamUrl: string;

  if (pathname === "/gtag/js" || pathname === "/gtag/gtm.js") {
    // Script proxy: /gtag/js → googletagmanager.com/gtag/js
    //               /gtag/gtm.js → googletagmanager.com/gtm.js
    const upstreamPath = pathname === "/gtag/js" ? "/gtag/js" : "/gtm.js";
    upstreamUrl = `https://www.googletagmanager.com${upstreamPath}${url.search}`;
  } else if (pathname === "/gtag/collect") {
    // Beacon proxy: POST /gtag/collect → google-analytics.com/g/collect
    upstreamUrl = `https://www.google-analytics.com/g/collect${url.search}`;
  } else {
    return new Response("Not found", { status: 404 });
  }

  const upstreamReq = new Request(upstreamUrl, {
    method: request.method,
    headers: (() => {
      const h = new Headers();
      // Forward content-type for POST beacons; strip Origin/Referer so
      // Google does not see the proxy's own URL as the document origin.
      const ct = request.headers.get("content-type");
      if (ct) h.set("content-type", ct);
      const ua = request.headers.get("user-agent");
      if (ua) h.set("user-agent", ua);
      // Forward the real visitor IP so GA4 geolocation is accurate.
      const cf = (request as unknown as { cf?: { ip?: string } }).cf;
      const realIp = request.headers.get("cf-connecting-ip") || cf?.ip || "";
      if (realIp) h.set("x-forwarded-for", realIp);
      return h;
    })(),
    body: request.method === "POST" ? request.body : undefined,
  });

  let upstream: Response;
  try {
    upstream = await fetch(upstreamReq);
  } catch {
    return new Response("Bad gateway", { status: 502 });
  }

  const respHeaders = new Headers();
  // Propagate content-type from Google's response.
  const ct = upstream.headers.get("content-type");
  if (ct) respHeaders.set("content-type", ct);

  if (pathname === "/gtag/collect") {
    // Beacon: no caching, CORS open so browsers can POST cross-origin.
    respHeaders.set("cache-control", "no-store");
    respHeaders.set("access-control-allow-origin", "*");
  } else {
    // Script: cache at the edge for 5 minutes (Google rotates slowly).
    // Browsers may cache up to 1 minute so a stale version is never older
    // than 6 minutes after Google publishes an update.
    respHeaders.set("cache-control", "public, max-age=60, s-maxage=300, stale-while-revalidate=300");
    respHeaders.set("access-control-allow-origin", "*");
    respHeaders.set("vary", "accept-encoding");
  }
  respHeaders.set("x-source", "gtag-gateway");

  return new Response(upstream.body, {
    status: upstream.status,
    headers: respHeaders,
  });
}
// ─────────────────────────────────────────────────────────────────────────────
// Task #4 — Edge-level title / description injection for SPA routes.
//
// When a verified or claimed search bot reaches the PAGES_ORIGIN path (i.e.
// the route is NOT in the prerender set and falls through to the static SPA
// shell), we apply an HTMLRewriter pass that overwrites the generic
// <title> and <meta name="description"> elements with route-specific values
// derived from the URL slug.  Human users and non-HTML responses are
// always served the original upstream body unchanged.
//
// Scope: /notes/class-11/:subject, /notes/class-12/:subject,
//        /notes/degree/:sem/:subject, /ahsec/hs-1st-year/:subject,
//        /ahsec/hs-2nd-year/:subject, /learn/:slug.
//
// Out of scope: OG tags, structured data, full SSR, backend changes.
// ─────────────────────────────────────────────────────────────────────────────

export function _slugToTitle(slug: string): string {
  return slug
    .split("-")
    .map((w) => (w.length > 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}

export const _OG_IMAGE_BASE = "https://cdn.syrabit.ai/og";
export const OG_IMAGE_WIDTH  = "1200";
export const OG_IMAGE_HEIGHT = "630";

interface _SpaMeta { title: string; description: string; ogImage: string; ogImageAlt?: string }

export function _resolveSpaRouteMeta(pathname: string): _SpaMeta | null {
  const clean = pathname.replace(/\/+$/, "") || "/";
  const parts = clean.split("/").filter(Boolean);

  // /notes/class-11/:subject[/:chapter...]
  // Minimum 3 segments — deeper chapter paths inherit the subject title.
  if (parts.length >= 3 && parts[0] === "notes" && parts[1] === "class-11") {
    const subject = _slugToTitle(parts[2]);
    return {
      title: `${subject} — Class 11 Notes | Syrabit.ai`,
      description: `Study ${subject} for AHSEC Class 11 with AI-powered notes, MCQs, flashcards, and previous year questions on Syrabit.ai.`,
      ogImage: `${_OG_IMAGE_BASE}/${parts[2]}.png`,
      ogImageAlt: `${subject} Class 11 notes — Syrabit.ai`,
    };
  }
  // /notes/class-12/:subject[/:chapter...]
  if (parts.length >= 3 && parts[0] === "notes" && parts[1] === "class-12") {
    const subject = _slugToTitle(parts[2]);
    return {
      title: `${subject} — Class 12 Notes | Syrabit.ai`,
      description: `Study ${subject} for AHSEC Class 12 with AI-powered notes, MCQs, flashcards, and previous year questions on Syrabit.ai.`,
      ogImage: `${_OG_IMAGE_BASE}/${parts[2]}.png`,
      ogImageAlt: `${subject} Class 12 notes — Syrabit.ai`,
    };
  }
  // /notes/degree/:sem/:subject[/:chapter...]
  if (parts.length >= 4 && parts[0] === "notes" && parts[1] === "degree") {
    const subject = _slugToTitle(parts[3]);
    const sem = _slugToTitle(parts[2]);
    return {
      title: `${subject} — ${sem} Degree Notes | Syrabit.ai`,
      description: `Study ${subject} for your Degree ${sem} with AI-powered notes, MCQs, and previous year questions on Syrabit.ai.`,
      ogImage: `${_OG_IMAGE_BASE}/${parts[3]}.png`,
      ogImageAlt: `${subject} Degree notes — Syrabit.ai`,
    };
  }
  // /ahsec/hs-1st-year/:subject[/:chapter...]
  if (parts.length >= 3 && parts[0] === "ahsec" && parts[1] === "hs-1st-year") {
    const subject = _slugToTitle(parts[2]);
    return {
      title: `${subject} — AHSEC HS 1st Year | Syrabit.ai`,
      description: `Study ${subject} for AHSEC HS 1st Year with AI-powered notes, MCQs, and previous year questions on Syrabit.ai.`,
      ogImage: `${_OG_IMAGE_BASE}/${parts[2]}.png`,
      ogImageAlt: `${subject} AHSEC HS 1st Year — Syrabit.ai`,
    };
  }
  // /ahsec/hs-2nd-year/:subject[/:chapter...]
  if (parts.length >= 3 && parts[0] === "ahsec" && parts[1] === "hs-2nd-year") {
    const subject = _slugToTitle(parts[2]);
    return {
      title: `${subject} — AHSEC HS 2nd Year | Syrabit.ai`,
      description: `Study ${subject} for AHSEC HS 2nd Year with AI-powered notes, MCQs, and previous year questions on Syrabit.ai.`,
      ogImage: `${_OG_IMAGE_BASE}/${parts[2]}.png`,
      ogImageAlt: `${subject} AHSEC HS 2nd Year — Syrabit.ai`,
    };
  }
  // Task #50 — /ahsec/class-11/:subject[/:chapter...]
  // Previously unhandled; added so /ahsec/class-11/<slug> gets the same
  // subject-specific banner as the /notes/class-11/<slug> equivalent.
  if (parts.length >= 3 && parts[0] === "ahsec" && parts[1] === "class-11") {
    const subject = _slugToTitle(parts[2]);
    return {
      title: `${subject} — AHSEC Class 11 | Syrabit.ai`,
      description: `Study ${subject} for AHSEC Class 11 with AI-powered notes, MCQs, and previous year questions on Syrabit.ai.`,
      ogImage: `${_OG_IMAGE_BASE}/${parts[2]}.png`,
      ogImageAlt: `${subject} AHSEC Class 11 — Syrabit.ai`,
    };
  }
  // Task #50 — /ahsec/class-12/:subject[/:chapter...]
  if (parts.length >= 3 && parts[0] === "ahsec" && parts[1] === "class-12") {
    const subject = _slugToTitle(parts[2]);
    return {
      title: `${subject} — AHSEC Class 12 | Syrabit.ai`,
      description: `Study ${subject} for AHSEC Class 12 with AI-powered notes, MCQs, and previous year questions on Syrabit.ai.`,
      ogImage: `${_OG_IMAGE_BASE}/${parts[2]}.png`,
      ogImageAlt: `${subject} AHSEC Class 12 — Syrabit.ai`,
    };
  }
  // /learn/:slug[/:section...]
  if (parts.length >= 2 && parts[0] === "learn") {
    const topic = _slugToTitle(parts[1]);
    return {
      title: `${topic} — Learn | Syrabit.ai`,
      description: `Learn about ${topic} with AI-powered explanations, notes, MCQs, and previous year questions on Syrabit.ai.`,
      ogImage: `${_OG_IMAGE_BASE}/notes.png`,
      ogImageAlt: `${topic} learning materials — Syrabit.ai`,
    };
  }
  // /ahsec (board hub)
  if (parts.length === 1 && parts[0] === "ahsec") {
    return {
      title: "AHSEC Study Materials | Syrabit.ai",
      description: "Browse AHSEC study materials, notes, MCQs, and previous year questions for HS 1st and 2nd Year on Syrabit.ai.",
      ogImage: `${_OG_IMAGE_BASE}/ahsec.png`,
      ogImageAlt: "AHSEC study materials — Syrabit.ai",
    };
  }
  // /seba (board hub)
  if (parts.length === 1 && parts[0] === "seba") {
    return {
      title: "SEBA Study Materials | Syrabit.ai",
      description: "Browse SEBA study materials, notes, MCQs, and previous year questions on Syrabit.ai.",
      ogImage: `${_OG_IMAGE_BASE}/seba.png`,
      ogImageAlt: "SEBA study materials — Syrabit.ai",
    };
  }
  // /degree (board hub)
  if (parts.length === 1 && parts[0] === "degree") {
    return {
      title: "Degree Study Materials | Syrabit.ai",
      description: "Browse Degree study materials, notes, MCQs, and previous year questions on Syrabit.ai.",
      ogImage: `${_OG_IMAGE_BASE}/degree.png`,
      ogImageAlt: "Degree study materials — Syrabit.ai",
    };
  }
  // /ahsec/class-11 (board+class hub — exactly 2 segments)
  if (parts.length === 2 && parts[0] === "ahsec" && parts[1] === "class-11") {
    return {
      title: "AHSEC Class 11 Study Materials | Syrabit.ai",
      description: "Browse AHSEC Class 11 study materials, notes, MCQs, and previous year questions on Syrabit.ai.",
      ogImage: `${_OG_IMAGE_BASE}/ahsec-class-11.png`,
      ogImageAlt: "AHSEC Class 11 study materials — Syrabit.ai",
    };
  }
  // /ahsec/class-12 (board+class hub — exactly 2 segments)
  if (parts.length === 2 && parts[0] === "ahsec" && parts[1] === "class-12") {
    return {
      title: "AHSEC Class 12 Study Materials | Syrabit.ai",
      description: "Browse AHSEC Class 12 study materials, notes, MCQs, and previous year questions on Syrabit.ai.",
      ogImage: `${_OG_IMAGE_BASE}/ahsec-class-12.png`,
      ogImageAlt: "AHSEC Class 12 study materials — Syrabit.ai",
    };
  }
  // /notes (notes hub — exactly 1 segment)
  if (parts.length === 1 && parts[0] === "notes") {
    return {
      title: "Study Notes | Syrabit.ai",
      description: "Browse study notes for AHSEC Class 11, Class 12, and Degree courses on Syrabit.ai.",
      ogImage: `${_OG_IMAGE_BASE}/notes.png`,
      ogImageAlt: "Syrabit.ai study notes",
    };
  }
  // /notes/class-11 (notes class hub — exactly 2 segments, no subject yet)
  if (parts.length === 2 && parts[0] === "notes" && parts[1] === "class-11") {
    return {
      title: "Class 11 Notes | Syrabit.ai",
      description: "Browse Class 11 study notes for all AHSEC subjects on Syrabit.ai.",
      ogImage: `${_OG_IMAGE_BASE}/notes-class-11.png`,
      ogImageAlt: "Class 11 study notes — Syrabit.ai",
    };
  }
  // /notes/class-12 (notes class hub — exactly 2 segments, no subject yet)
  if (parts.length === 2 && parts[0] === "notes" && parts[1] === "class-12") {
    return {
      title: "Class 12 Notes | Syrabit.ai",
      description: "Browse Class 12 study notes for all AHSEC subjects on Syrabit.ai.",
      ogImage: `${_OG_IMAGE_BASE}/notes-class-12.png`,
      ogImageAlt: "Class 12 study notes — Syrabit.ai",
    };
  }
  // Task #50 — /degree/:program (program hub — exactly 2 segments, e.g. /degree/ba, /degree/bcom)
  // No per-program OG image exists; fall back gracefully to the board-level degree.png.
  if (parts.length === 2 && parts[0] === "degree") {
    const program = _slugToTitle(parts[1]);
    return {
      title: `${program} Degree Study Materials | Syrabit.ai`,
      description: `Browse ${program} Degree study materials, notes, MCQs, and previous year questions on Syrabit.ai.`,
      ogImage: `${_OG_IMAGE_BASE}/degree.png`,
      ogImageAlt: `${program} Degree study materials — Syrabit.ai`,
    };
  }
  // Task #50 — /seba/:class (class hub — exactly 2 segments, e.g. /seba/class-10, /seba/class-9)
  // No per-class OG image exists; fall back gracefully to the board-level seba.png.
  if (parts.length === 2 && parts[0] === "seba") {
    const cls = _slugToTitle(parts[1]);
    return {
      title: `SEBA ${cls} Study Materials | Syrabit.ai`,
      description: `Browse SEBA ${cls} study materials, notes, MCQs, and previous year questions on Syrabit.ai.`,
      ogImage: `${_OG_IMAGE_BASE}/seba.png`,
      ogImageAlt: `SEBA ${cls} study materials — Syrabit.ai`,
    };
  }
  return null;
}

export function _injectSpaTitleForBot(
  response: Response,
  pathname: string,
  isBotGet: boolean,
  onMiss?: (pathname: string) => void,
): Response {
  if (!isBotGet) return response;
  const ct = (response.headers.get("content-type") || "").toLowerCase();
  if (!ct.includes("text/html")) return response;
  const meta = _resolveSpaRouteMeta(pathname);
  if (!meta) {
    // Task #9 — no pattern matched; fire the miss callback so the caller
    // can emit an Analytics Engine datapoint for gap analysis.
    onMiss?.(pathname);
    return response;
  }

  const { title, description, ogImage, ogImageAlt } = meta;
  // Task #8 — also rewrite Open Graph tags so social-sharing previews
  // (WhatsApp, Telegram, Twitter/X) show the route-specific title and
  // description rather than the generic SPA fallback.
  // Task #15 — also rewrite Twitter card meta tags so links shared on
  // X show rich preview cards rather than falling back to plain text.
  // Task #17 — rewrite og:image with the CDN-hosted subject banner URL.
  // Task #18 — rewrite og:image:width / og:image:height / og:image:alt
  //   so platforms (WhatsApp, Facebook, Telegram) skip an extra fetch to
  //   measure the image before rendering the preview card.
  // Task #22 — also rewrite twitter:image and twitter:image:alt so X
  //   preview cards use the route-specific subject banner rather than
  //   the generic SPA fallback image.
  let rewriter = new HTMLRewriter()
    .on("title", {
      element(el) { el.setInnerContent(title); },
    })
    .on('meta[name="description"]', {
      element(el) { el.setAttribute("content", description); },
    })
    .on('meta[property="og:title"]', {
      element(el) { el.setAttribute("content", title); },
    })
    .on('meta[property="og:description"]', {
      element(el) { el.setAttribute("content", description); },
    })
    .on('meta[property="og:image"]', {
      element(el) { el.setAttribute("content", ogImage); },
    })
    .on('meta[property="og:image:width"]', {
      element(el) { el.setAttribute("content", OG_IMAGE_WIDTH); },
    })
    .on('meta[property="og:image:height"]', {
      element(el) { el.setAttribute("content", OG_IMAGE_HEIGHT); },
    })
    .on('meta[name="twitter:title"]', {
      element(el) { el.setAttribute("content", title); },
    })
    .on('meta[name="twitter:description"]', {
      element(el) { el.setAttribute("content", description); },
    })
    .on('meta[name="twitter:card"]', {
      element(el) { el.setAttribute("content", "summary_large_image"); },
    })
    .on('meta[name="twitter:image"]', {
      element(el) { el.setAttribute("content", ogImage); },
    })
    .on('meta[name="twitter:image:width"]', {
      element(el) { el.setAttribute("content", OG_IMAGE_WIDTH); },
    })
    .on('meta[name="twitter:image:height"]', {
      element(el) { el.setAttribute("content", OG_IMAGE_HEIGHT); },
    });
  if (ogImageAlt !== undefined) {
    rewriter = rewriter
      .on('meta[property="og:image:alt"]', {
        element(el) { el.setAttribute("content", ogImageAlt); },
      })
      .on('meta[name="twitter:image:alt"]', {
        element(el) { el.setAttribute("content", ogImageAlt); },
      });
  }
  return rewriter.transform(response);
}

// ─────────────────────────────────────────────────────────────────────────────

// Task #944 — extracted so the public ``fetch`` export can wrap a single
// recordEdgeLog call around every return path of the original handler.
// Behaviour of the inner handler is otherwise unchanged from before.
async function _handleEdgeFetch(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
): Promise<Response> {
    const url = new URL(request.url);
    const { pathname } = url;
    const origin = request.headers.get("Origin");
    const cors = safeCorsHeaders(origin);

    if (request.method === "OPTIONS") {
      const preflight = getCorsHeaders(origin);
      if (!preflight) {
        return new Response(null, { status: 403 });
      }
      return new Response(null, { status: 204, headers: preflight });
    }

    // From here on, all KV access goes through the monitored wrapper so
    // counters are accurate and graceful fallback kicks in on quota
    // exhaustion. The wrappers are cheap closures — re-creating them
    // per-request keeps the binding instances `const` and lets the
    // monitor module share state across requests via its own Map.
    env = wrapEnvKv(env, ctx);

    if (pathname === "/api/edge/kv-usage" && request.method === "GET") {
      return handleKvUsage(env, request, cors);
    }

    // Task #315 — R2 cold-storage health for the admin dashboard. The
    // monthly watchdog (Task #314) writes its last evaluation to KV;
    // this route just reads that snapshot back so an operator can
    // confirm the IA share / Logpush GB / rules-age between the
    // once-a-month cron tick without opening the Cloudflare dashboard.
    // Auth: same X-Edge-Admin-Secret pattern as /api/edge/kv-usage.
    if (
      pathname === "/api/edge/r2-storage-health" &&
      request.method === "GET"
    ) {
      return handleR2StorageHealth(env, request, cors);
    }
    // POST companion — re-runs `runR2StorageClassAlert` on demand
    // after an operator re-applies the lifecycle rules. KV-rate-limited
    // to ~1/min/IP so it can't be spammed past the 28-day cooldown
    // anchor inside the alert module (the cooldown protects against
    // duplicate pages; this gate protects the GraphQL + R2-list
    // budget from a stuck refresh button).
    if (
      pathname === "/api/edge/r2-storage-health/run" &&
      request.method === "POST"
    ) {
      return handleR2StorageHealthRun(env, request, ctx, cors);
    }
    // Task #322 — companion that clears the secondary "watchdog blind"
    // counter (consecutive_query_failures + query_fail_last_fired_at) in
    // KV so an operator who just rotated R2_STORAGE_ANALYTICS_TOKEN can
    // dismiss the red badge immediately rather than waiting up to ~30
    // days for the next monthly evaluation. Same admin handshake as
    // /run; no cooldown because the op is KV-only and idempotent.
    if (
      pathname === "/api/edge/r2-storage-health/reset-watchdog" &&
      request.method === "POST"
    ) {
      return handleR2StorageHealthResetWatchdog(env, request, cors);
    }

    // ── Phase 5: Edge metrics query (Analytics Engine GraphQL API) ──────────
    // GET /api/edge/analytics?range=24h|6h|1h|7d
    // Requires:
    //   - CF_ANALYTICS_TOKEN secret (Analytics: Read scope) to query the GQL API.
    //   - X-Edge-Admin-Secret: <D1_SYNC_SECRET> header (same pattern as /api/edge/kv-usage).
    //     This endpoint is NOT under /admin*, so it is not covered by the Zero Trust
    //     Access app policy — the shared-secret check is the only auth layer.
    //     Call via the Flask backend /admin/edge-analytics proxy (not directly from SPA).
    if (pathname === "/api/edge/analytics" && request.method === "GET") {
      const edgeSecret = request.headers.get("X-Edge-Admin-Secret") ?? "";
      if (!env.D1_SYNC_SECRET || edgeSecret !== env.D1_SYNC_SECRET) {
        return new Response(
          JSON.stringify({ error: "Unauthorized" }),
          { status: 401, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }
      if (!env.CF_ANALYTICS_TOKEN) {
        return new Response(
          JSON.stringify({ error: "CF_ANALYTICS_TOKEN secret not set. Run: wrangler secret put CF_ANALYTICS_TOKEN" }),
          { status: 503, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }
      try {
        const range   = url.searchParams.get("range") ?? "24h";
        const metrics = await queryEdgeMetrics(env.CF_ANALYTICS_TOKEN, range);
        return new Response(JSON.stringify(metrics), {
          headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" },
        });
      } catch (e) {
        const msg = e instanceof Error ? e.message : "unknown";
        return new Response(
          JSON.stringify({ error: `Analytics Engine query failed: ${msg.slice(0, 300)}` }),
          { status: 500, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }
    }

    // Task #12 / Task #32 — GET /api/edge/spa-title-misses?range=<1h|6h|24h|7d>
    // Returns the top 20 bot-crawled SPA paths that fell through
    // _resolveSpaRouteMeta and therefore received the generic <title>.
    // Defaults to "24h" (matching the nightly cron window) so admins can
    // trigger the same scan on demand without waiting for the 01:00 UTC cron.
    // Response is enriched with per-path suggested titles and threshold
    // metadata, reusing the same logic as runSpaTitleMissAlert (Task #13).
    // Auth: same X-Edge-Admin-Secret handshake as /api/edge/analytics.
    // Call via the Flask backend /admin/edge/spa-title-misses proxy.
    if (pathname === "/api/edge/spa-title-misses" && request.method === "GET") {
      const edgeSecret = request.headers.get("X-Edge-Admin-Secret") ?? "";
      if (!env.D1_SYNC_SECRET || edgeSecret !== env.D1_SYNC_SECRET) {
        return new Response(
          JSON.stringify({ error: "Unauthorized" }),
          { status: 401, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }
      if (!env.CF_ANALYTICS_TOKEN) {
        return new Response(
          JSON.stringify({ error: "CF_ANALYTICS_TOKEN secret not set. Run: wrangler secret put CF_ANALYTICS_TOKEN" }),
          { status: 503, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }
      try {
        // Default range mirrors the cron window so admins get the same view
        // as the nightly alert without waiting for it to fire.
        const range = url.searchParams.get("range") ?? "24h";
        const misses = await querySpaTitleMisses(env.CF_ANALYTICS_TOKEN, range);

        // Task #33 — read effective settings from KV first (runtime override),
        // falling back to env vars. This makes threshold / disabled tunable
        // from the admin dashboard without a wrangler redeploy.
        const kvSettings = await _readSpaTitleMissKvSettings(env.RATE_LIMIT, env);
        const thr = kvSettings.threshold;

        const uncovered = misses.filter(
          (m) => _resolveSpaRouteMeta(m.pathname) === null,
        );
        const gapsAbove = uncovered.filter((m) => m.count >= thr);

        // Enrich each gap with a human-readable suggested title derived from
        // the last meaningful path segment (same heuristic as the cron alert).
        const enriched = gapsAbove.map((m) => {
          const parts = m.pathname.replace(/\/$/, "").split("/").filter(Boolean);
          const lastPart = parts[parts.length - 1] ?? m.pathname;
          return {
            pathname:        m.pathname,
            count:           m.count,
            suggested_title: _slugToTitle(lastPart),
          };
        });

        // Task #39 — static build-time record of which tag rewrites are active
        // in the current worker. Admins can use this to confirm twitter:image
        // and twitter:image:alt handlers are live without reading the source.
        const tagHandlers = {
          og_title:           true,
          og_description:     true,
          og_image:           true,
          og_image_alt:       true,
          twitter_title:      true,
          twitter_description:true,
          twitter_card:       true,
          twitter_image:      true,
          twitter_image_alt:  true,
        };

        return new Response(
          JSON.stringify({
            range,
            threshold:            thr,
            alert_disabled:       kvSettings.disabled,
            gaps_found:           uncovered.length,
            gaps_above_threshold: gapsAbove.length,
            gaps:                 enriched,
            tag_handlers:         tagHandlers,
          }),
          { headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } },
        );
      } catch (e) {
        const msg = e instanceof Error ? e.message : "unknown";
        return new Response(
          JSON.stringify({ error: `Analytics Engine query failed: ${msg.slice(0, 300)}` }),
          { status: 500, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }
    }

    // Task #33 — GET /api/edge/spa-title-miss-settings
    // Returns the effective alert settings (KV override takes priority over env vars).
    // Auth: X-Edge-Admin-Secret (D1_SYNC_SECRET).
    if (pathname === "/api/edge/spa-title-miss-settings" && request.method === "GET") {
      const edgeSecret = request.headers.get("X-Edge-Admin-Secret") ?? "";
      if (!env.D1_SYNC_SECRET || edgeSecret !== env.D1_SYNC_SECRET) {
        return new Response(
          JSON.stringify({ error: "Unauthorized" }),
          { status: 401, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }
      const settings = await _readSpaTitleMissKvSettings(env.RATE_LIMIT, env);
      // Also return whether a KV override is currently stored so the UI can
      // show a "using KV override" vs "using env var default" indicator.
      const kvRaw = env.RATE_LIMIT ? await env.RATE_LIMIT.get(_SPA_TITLE_MISS_SETTINGS_KEY) : null;
      return new Response(
        JSON.stringify({
          threshold:       settings.threshold,
          disabled:        settings.disabled,
          kv_override_set: kvRaw !== null,
          env_threshold:   (() => {
            const raw = env.SPA_TITLE_MISS_ALERT_THRESHOLD;
            if (!raw) return 50;
            const n = Number(raw);
            return Number.isFinite(n) && n >= 1 ? Math.floor(n) : 50;
          })(),
          env_disabled: env.SPA_TITLE_MISS_ALERT_DISABLED?.toLowerCase() === "true",
        }),
        { headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } },
      );
    }

    // Task #33 — PUT /api/edge/spa-title-miss-settings
    // Persists threshold and/or disabled flag to RATE_LIMIT KV so the admin
    // can tune alerting at runtime without a wrangler redeploy.
    // Body: { "threshold": <int ≥ 1>, "disabled": <bool> }
    // Either field may be omitted to leave it unchanged; send both to set both.
    // Auth: X-Edge-Admin-Secret (D1_SYNC_SECRET).
    if (pathname === "/api/edge/spa-title-miss-settings" && request.method === "PUT") {
      const edgeSecret = request.headers.get("X-Edge-Admin-Secret") ?? "";
      if (!env.D1_SYNC_SECRET || edgeSecret !== env.D1_SYNC_SECRET) {
        return new Response(
          JSON.stringify({ error: "Unauthorized" }),
          { status: 401, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }
      if (!env.RATE_LIMIT) {
        return new Response(
          JSON.stringify({ error: "RATE_LIMIT KV not bound — cannot persist settings" }),
          { status: 503, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }
      let body: Record<string, unknown>;
      try {
        body = await request.json() as Record<string, unknown>;
      } catch {
        return new Response(
          JSON.stringify({ error: "Invalid JSON body" }),
          { status: 400, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }
      // Read current stored settings and merge the provided fields.
      const current = await _readSpaTitleMissKvSettings(env.RATE_LIMIT, env);
      let { threshold: newThreshold, disabled: newDisabled } = current;

      if ("threshold" in body) {
        const t = Number(body["threshold"]);
        if (!Number.isFinite(t) || t < 1) {
          return new Response(
            JSON.stringify({ error: "threshold must be an integer ≥ 1" }),
            { status: 400, headers: { ...cors, "Content-Type": "application/json" } },
          );
        }
        newThreshold = Math.floor(t);
      }
      if ("disabled" in body) {
        if (typeof body["disabled"] !== "boolean") {
          return new Response(
            JSON.stringify({ error: "disabled must be a boolean" }),
            { status: 400, headers: { ...cors, "Content-Type": "application/json" } },
          );
        }
        newDisabled = body["disabled"] as boolean;
      }

      const saved: _SpaTitleMissKvSettings = { threshold: newThreshold, disabled: newDisabled };
      // Settings persist for 365 days (effectively permanent; the admin can
      // always overwrite). TTL prevents orphaned keys after the feature is retired.
      await env.RATE_LIMIT.put(_SPA_TITLE_MISS_SETTINGS_KEY, JSON.stringify(saved), {
        expirationTtl: 365 * 24 * 3600,
      });
      return new Response(
        JSON.stringify({ ok: true, threshold: newThreshold, disabled: newDisabled }),
        { headers: { ...cors, "Content-Type": "application/json", "Cache-Control": "no-store" } },
      );
    }

    // ── Google Tag Gateway (gtag proxy) ─────────────────────────────────────
    // Proxies Google Analytics 4 beacon requests through this edge worker so
    // they originate from api.syrabit.ai instead of googletagmanager.com.
    // Benefits:
    //   1. Bypasses ad-blockers and browser privacy extensions that block
    //      googletagmanager.com, recovering ~10–20% of mobile traffic that
    //      would otherwise be invisible to GA4.
    //   2. Eliminates the third-party DNS + TLS handshake cost for the gtag.js
    //      script (~50–100 ms on slow connections) because the script is now
    //      served from a first-party origin already open in the browser.
    //   3. All requests pass through Cloudflare's network — same PoP as the
    //      page HTML — so no extra cross-ocean hop.
    //
    // Routes proxied:
    //   GET  /gtag/js            → https://www.googletagmanager.com/gtag/js
    //   POST /gtag/collect       → https://www.google-analytics.com/g/collect
    //   GET  /gtag/gtm.js        → https://www.googletagmanager.com/gtm.js
    //
    // The frontend references these as relative URLs (see vite.config.js
    // ga4Plugin — change the src from the googletagmanager.com absolute URL
    // to /gtag/js?id=G-XXXXXXXXXX after this worker is deployed).
    //
    // Cache: gtag.js is edge-cached for 5 minutes (Google rotates it slowly);
    //        /g/collect beacons are never cached (POST + ephemeral).
    if (pathname.startsWith("/gtag/")) {
      return handleGtagGateway(request, pathname, url);
    }
    // ────────────────────────────────────────────────────────────────────────

    // Task #848 — /api/livez is the new Railway liveness probe. The
    // edge can answer it directly because the contract is "is *some*
    // process alive" — for the synthetic external probe, the edge
    // worker itself responding IS proof of life from the user's
    // perspective (DNS + Cloudflare + Worker all up). The actual
    // dependency state moved to /api/readyz, which intentionally
    // proxies through to the backend so on-call sees real Mongo /
    // PG / Vertex status instead of a static "edge is up" lie.
    // ── Task #513 §A — /api/me/quota (read-only chat-cap state) ───────────
    // Returns the caller's current chat-budget state so the SPA can
    // render an accurate "X chats left this month / Y left today"
    // banner without a guess-and-check 429. The worker has direct KV
    // access so we serve this entirely from the edge — no FastAPI
    // round-trip. Identity resolution mirrors the cap precheck:
    // JWT-verified `sub` first, anon-id second, IP last.
    if (pathname === "/api/me/quota" && (request.method === "GET" || request.method === "HEAD")) {
      const _verified = await verifyJwtFromRequest(request, env);
      const _anonId = (request.headers.get("x-anon-id") || "").trim().replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 64);
      let _identity: string;
      let _identityKind: "user" | "anon" | "ip";
      if (_verified) {
        _identity = `u:${_verified.user_id}`;
        _identityKind = "user";
      } else if (_anonId) {
        _identity = `a:${_anonId}`;
        _identityKind = "anon";
      } else {
        const _ip = (request.headers.get("cf-connecting-ip") || request.headers.get("x-forwarded-for") || "0.0.0.0").split(",")[0].trim();
        _identity = `ip:${_ip}`;
        _identityKind = "ip";
      }
      const _paid = _verified ? _isPaidPlan(_verified.plan) : false;
      const counters = await readChatCapCounters(env, _identity);
      const monthCap = CHAT_CAP_MONTHLY;
      const dayCap   = _paid ? null : CHAT_CAP_DAILY;
      const body = {
        identity_kind: _identityKind,
        plan: _verified?.plan || "free",
        month: {
          cap:        monthCap,
          used:       counters.used_month,
          remaining:  Math.max(0, monthCap - counters.used_month),
          reset:      _nextUtcMonthStartDate().toISOString(),
        },
        day: dayCap === null ? null : {
          cap:        dayCap,
          used:       counters.used_day,
          remaining:  Math.max(0, dayCap - counters.used_day),
          reset:      _nextUtcMidnightDate().toISOString(),
        },
      };
      // §A — short edge cache: counters change at most once per
      // request (the chat post-bump is the only writer), so a 5 s
      // public cache cuts the SPA's "remaining" banner refresh cost
      // ~99 % without showing a stale value for more than one tick.
      // `private` keeps logged-in plan data out of intermediate
      // shared caches; `s-maxage=5` is the Cloudflare-edge TTL.
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: {
          ...cors,
          "Content-Type":  "application/json",
          "Cache-Control": "private, max-age=5, s-maxage=5",
          "Vary":          "Authorization, X-Anon-Id",
        },
      });
    }
    if (
      pathname === "/api/health" ||
      pathname === "/api/livez" ||
      pathname === "/health"
    ) {
      return new Response(
        JSON.stringify({
          status: "ok",
          edge: true,
          region: (request as unknown as { cf?: { colo?: string } }).cf?.colo || "unknown",
          timestamp: new Date().toISOString(),
          d1: !!env.CONTENT_DB,
        }),
        {
          status: 200,
          headers: {
            ...cors,
            "Content-Type": "application/json",
            // /api/livez is hit every minute by the synthetic probe;
            // a 30 s edge cache absorbs spikes without hiding a real
            // outage longer than the probe's own granularity.
            "Cache-Control": "public, max-age=30, stale-while-revalidate=60",
            "X-Source": "edge",
          },
        }
      );
    }

    // ── Task #108 — Phase 4: Admin asset upload (R2) ────────────────────────
    // POST /admin/assets/upload
    //   Multipart form: `file` (binary PDF/document) + `key` (R2 object key)
    //   Protected upstream by Cloudflare Zero Trust (Phase 3) — the route is
    //   inside api.syrabit.ai/admin* so no request reaches here without a
    //   valid Access session cookie. A second layer requires an Authorization:
    //   Bearer header (format check — prevents headerless CSRF; full JWT
    //   signature verification is deferred as a future hardening step).
    //
    // Response (JSON):
    //   201 { ok: true, key, size, url }          — upload succeeded
    //   400 { ok: false, error: "..." }            — missing/invalid params
    //   401 { ok: false, error: "unauthorized" }   — no/invalid Bearer token
    //   503 { ok: false, error: "assets_not_bound" } — ASSETS binding missing
    //
    // The uploaded file is served at:
    //   https://assets.syrabit.ai/<key>
    // (via the R2 custom domain configured by cloudflare-phase4-apply.js)
    if (pathname === "/admin/assets/upload" && request.method === "POST") {
      if (!env.ASSETS) {
        return new Response(
          JSON.stringify({ ok: false, error: "assets_not_bound",
            detail: "ASSETS R2 binding not configured — run cloudflare-phase4-apply.js then wrangler deploy" }),
          { status: 503, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }

      // Presence check: require an Authorization: Bearer header.
      // This is a format-only check (we confirm the header starts with "Bearer ")
      // not a cryptographic JWT validation. Zero Trust (Phase 3) is the primary
      // auth gate — no request reaches this route without a valid Access session
      // cookie. This check adds a second layer by requiring an explicit auth header,
      // which prevents accidental CSRF from same-origin pages that wouldn't
      // normally send an Authorization header. Full JWT signature verification
      // would require the JWT_SECRET binding and is left as a future hardening step.
      const authHeader = request.headers.get("Authorization") ?? "";
      if (!authHeader.startsWith("Bearer ")) {
        return new Response(
          JSON.stringify({ ok: false, error: "unauthorized",
            detail: "Bearer token required in Authorization header" }),
          { status: 401, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }

      let formData: FormData;
      try {
        formData = await request.formData();
      } catch {
        return new Response(
          JSON.stringify({ ok: false, error: "invalid_multipart",
            detail: "Request must be multipart/form-data with 'file' and 'key' fields" }),
          { status: 400, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }

      const fileField = formData.get("file");
      const key       = (formData.get("key") as string | null)?.trim();

      const uploadedFile = fileField as unknown as (File & { name: string; size: number; type: string; arrayBuffer(): Promise<ArrayBuffer> }) | null;
      if (!uploadedFile || typeof uploadedFile === "string") {
        return new Response(
          JSON.stringify({ ok: false, error: "file_required",
            detail: "'file' field is required and must be a file upload" }),
          { status: 400, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }

      if (!key || key.length === 0) {
        return new Response(
          JSON.stringify({ ok: false, error: "key_required",
            detail: "'key' field is required — e.g. ahsec/2024/physics.pdf" }),
          { status: 400, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }

      // Reject path traversal attempts
      if (key.includes("..") || key.startsWith("/")) {
        return new Response(
          JSON.stringify({ ok: false, error: "invalid_key",
            detail: "key must not contain '..' or start with '/'" }),
          { status: 400, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }

      const contentType = uploadedFile.type || "application/octet-stream";
      const size        = uploadedFile.size;

      // Enforce a 50 MB limit to keep the Workers request body within reason.
      // Workers standard has a 100 MB body limit; we use 50 MB as a safe cap
      // for educational PDFs (typical past-paper PDF is 2–15 MB).
      const MAX_BYTES = 50 * 1024 * 1024;
      if (size > MAX_BYTES) {
        return new Response(
          JSON.stringify({ ok: false, error: "file_too_large",
            detail: `File size ${size} bytes exceeds 50 MB limit` }),
          { status: 400, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }

      const arrayBuffer = await uploadedFile.arrayBuffer();

      try {
        await env.ASSETS.put(key, arrayBuffer, {
          httpMetadata: {
            contentType,
            // Content-Disposition: inline so browsers open PDFs in-tab
            contentDisposition: `inline; filename="${uploadedFile.name}"`,
            // Cache for 1 year — past papers and syllabi are immutable once uploaded.
            // Admins who need to replace a file upload under the same key (R2 PUT
            // is idempotent) and the CDN will serve the new version after the TTL.
            cacheControl: "public, max-age=31536000, immutable",
          },
          customMetadata: {
            uploadedAt: new Date().toISOString(),
            originalName: uploadedFile.name,
          },
        });
      } catch (e: unknown) {
        const detail = e instanceof Error ? e.message : "Unknown R2 error";
        return new Response(
          JSON.stringify({ ok: false, error: "upload_failed", detail }),
          { status: 502, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }

      const publicUrl = `https://assets.syrabit.ai/${key}`;
      return new Response(
        JSON.stringify({ ok: true, key, size, contentType, url: publicUrl }),
        { status: 201, headers: { ...cors, "Content-Type": "application/json" } },
      );
    }
    // ────────────────────────────────────────────────────────────────────────

    // Task #636 — Workers AI fallback fan-out. Backend POSTs here only
    // after a primary-provider failure. POST-only; CORS preflight is
    // handled above by the OPTIONS branch.
    if (request.method === "POST" && pathname.startsWith("/api/ai/fallback/")) {
      const cap = pathname.slice("/api/ai/fallback/".length);
      if (cap === "chat" || cap === "embed" || cap === "tts" || cap === "stt") {
        return handleAiFallback(request, env, cors, cap);
      }
      return new Response(
        JSON.stringify({ ok: false, error: "unknown_capability" }),
        { status: 404, headers: { ...cors, "Content-Type": "application/json" } },
      );
    }

    // ── Enterprise: edge-side semantic search via Vectorize (no backend RTT) ──
    // POST /api/edge/search  { query, top_k?, filters? }
    // Embeds the query with Workers AI (bge-large-en-v1.5, 1024-dim) and
    // queries syllabus-index-v2 directly from the isolate. Typical latency
    // is 40–80 ms vs 200–400 ms for the backend round-trip path.
    // Requires X-Edge-AI-Secret header (same secret as /api/ai/fallback/*).
    //
    // The `use_legacy` query path was removed in Task #308 along with the
    // 768-dim `syllabus-index` Vectorize index it targeted.
    if (pathname === "/api/edge/search" && request.method === "POST") {
      const secret = request.headers.get("X-Edge-AI-Secret") ?? "";
      if (!env.EDGE_AI_FALLBACK_SECRET || secret !== env.EDGE_AI_FALLBACK_SECRET) {
        return new Response(JSON.stringify({ ok: false, error: "unauthorized" }), {
          status: 401, headers: { ...cors, "Content-Type": "application/json" },
        });
      }
      if (!env.AI || !env.SYLLABUS_INDEX) {
        return new Response(
          JSON.stringify({ ok: false, error: "vectorize_not_bound" }),
          { status: 503, headers: { ...cors, "Content-Type": "application/json" } },
        );
      }
      try {
        const body = await request.json() as {
          query: string;
          top_k?: number;
          filters?: Record<string, string>;
        };
        if (!body.query || typeof body.query !== "string") {
          return new Response(JSON.stringify({ ok: false, error: "query_required" }), {
            status: 400, headers: { ...cors, "Content-Type": "application/json" },
          });
        }
        const t0 = Date.now();
        // Generate embedding using enterprise bge-large (1024-dim output)
        const embedOut = await env.AI.run(
          WORKERS_AI_MODELS.embed,
          { text: [body.query] },
          aiGatewayOpts(env, "workers-ai-edge-vector-search"),
        ) as { data: number[][] };
        const vector = embedOut.data[0];
        const index = env.SYLLABUS_INDEX;
        const queryOpts: VectorizeQueryOptions = {
          topK: body.top_k ?? 10,
          returnMetadata: "all",
        };
        if (body.filters && Object.keys(body.filters).length > 0) {
          queryOpts.filter = Object.fromEntries(
            Object.entries(body.filters).map(([k, v]) => [k, { $eq: v }]),
          ) as VectorizeVectorMetadataFilter;
        }
        const matches = await index.query(vector, queryOpts);
        return new Response(JSON.stringify({
          ok: true,
          matches: matches.matches,
          count: matches.matches.length,
          duration_ms: Date.now() - t0,
          index: "syllabus-index-v2",
          model: WORKERS_AI_MODELS.embed,
        }), { status: 200, headers: { ...cors, "Content-Type": "application/json" } });
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        return new Response(JSON.stringify({ ok: false, error: msg }), {
          status: 500, headers: { ...cors, "Content-Type": "application/json" },
        });
      }
    }

    if (pathname === "/api/edge/d1-sync" && request.method === "POST") {
      return handleSyncRequest(request, env, cors);
    }

    if (pathname === "/api/edge/d1-status" && request.method === "GET") {
      return handleSyncStatus(env, cors);
    }

    if (pathname === "/api/edge/purge" && request.method === "POST") {
      return handleEdgePurge(request, env, cors, ctx);
    }

    const clientIp =
      request.headers.get("CF-Connecting-IP") ||
      request.headers.get("X-Forwarded-For")?.split(",")[0]?.trim() ||
      "unknown";

    const ua = request.headers.get("User-Agent") || "";

    // ─── AI crawler hard-block ────────────────────────────────────────────
    // Per the user's "Cloudflare Search Crawler Activity" policy, AI
    // training/answer crawlers are denied with HTTP 403 before any further
    // routing decisions. robots.txt asks them to leave; this enforces it
    // for the ones that ignore robots.txt. Two carve-outs:
    //   * The canonical /robots.txt path itself — they need to be able
    //     to read the disallow rules so well-behaved bots stop
    //     crawling proactively. The allow-list is anchored to the
    //     exact root path with a regex (rather than a `pathname ===`
    //     string comparison) so the robots.txt-snapshot test does NOT
    //     misclassify this fetch handler as a worker-side robots.txt
    //     authority — Cloudflare Pages still serves the static file.
    //     Anchoring with `^...$` prevents accidentally exempting
    //     unrelated routes like `/api/robots.txt` from the AI block.
    //   * /api/health, /api/livez, /health — already short-circuited
    //     above, so this block runs after them.
    // CORS headers are included so the response is well-formed even if
    // a browser-side preview ever hits this branch.
    // Resolve bot identity now so that (a) the AI hard-block below has access
    // to botResult for structured error logging, and (b) the rate-limit and
    // SEO-content paths later in this handler can use isSearchBot.
    //
    // Trust hierarchy inside verifySearchBot:
    //   1. cf.verifiedBot === true → {verified: true} immediately, no CIDR check.
    //      This means any legitimate search crawler on a newly-added IP range
    //      that Cloudflare has already verified is treated as trusted even before
    //      the CIDR list is refreshed.
    //   2. UA matches SEARCH_BOT_UA + IP in BOT_UA_RANGES → {verified: true}
    //   3. UA matches SEARCH_BOT_UA + no registered CIDR list (e.g. YouBot) →
    //      {verified: false, spoofed: false} — unverified but not an impersonation
    //   4. UA matches SEARCH_BOT_UA + IP NOT in ranges → {verified: false, spoofed: true}
    const botResult = verifySearchBot(ua, request, clientIp);
    // Task #9 — `isSearchBot` is the EFFECTIVE trusted-bot flag that
    // gates fast-path rate limiting (60 000 RPM) and bot prerender.
    // It starts at `botResult.verified` (cf.verifiedBot OR static CIDR
    // hit) and is promoted to `true` below when forward-confirmed
    // rDNS validates a CRITICAL_BOT_UA from a non-CIDR IP. Without
    // this promotion, a legitimate Googlebot crawling from a rotated
    // IP would fall through to the unverified-bot branch (120 RPM,
    // no prerender) — the exact regression this task closes.
    let isSearchBot = botResult.verified;
    let fcrDnsAlreadyConfirmed = false;
    let remaining = 999999;

    // Run forward-confirmed rDNS once per request for any unverified
    // VERIFIED_BOT_UA candidate — that is the full search/citation
    // family (Googlebot, Bingbot, Yandex, YouBot, Yeti/Naver,
    // SeznamBot, MojeekBot, Slurp, etc.), NOT just CRITICAL_BOT_UA.
    // YouBot in particular publishes no static CIDR list and isn't
    // covered by CRITICAL_BOT_UA, so without this expanded gate it
    // would never reach the 60 000 RPM fast path. The result drives
    // BOTH the spoof-403 gate below (gated on the narrower
    // CRITICAL_BOT_UA) AND the fast-path promotion that follows.
    // Caching is in `verifyBotIpWithKv` (24 h KV, family-scoped
    // key), so re-using the result across both branches doesn't
    // double-charge DoH calls.
    if (!isSearchBot && VERIFIED_BOT_UA.test(ua)) {
      fcrDnsAlreadyConfirmed = await verifyBotIpWithKv(env, ctx, ua, clientIp);
      if (fcrDnsAlreadyConfirmed) {
        isSearchBot = true;
      }
    }

    if (botResult.spoofed) {
      const ipH = hashIp(clientIp);
      const colo = (request as unknown as { cf?: { colo?: string } }).cf?.colo || "unknown";
      ctx.waitUntil(logSpoofedBot(env.RATE_LIMIT, ipH, ua, clientIp, colo));

      // Task #9 — hard-403 spoofed critical search/citation bots when
      // FCrDNS also fails. Serving anything to a spoofed Googlebot is
      // an integrity bug because attackers use the cf.cache.reactsToBot
      // hint to scrape pre-rendered HTML the SPA gates.
      if (CRITICAL_BOT_UA.test(ua) && !fcrDnsAlreadyConfirmed) {
        return new Response(
          "Forbidden: User-Agent claims to be a verified search bot, but the " +
          "request IP did not pass forward-confirmed reverse-DNS verification.\n",
          {
            status: 403,
            headers: {
              ...cors,
              "Content-Type": "text/plain; charset=utf-8",
              "Cache-Control": "no-store",
              "X-Bot-Verify": "spoofed",
            },
          },
        );
      }
    }

    // AI crawler hard-block.
    // This block is UNCONDITIONAL — `isSearchBot` / `cf.verifiedBot` do NOT
    // bypass it. AI training scrapers (GPTBot, CCBot, Google-Extended, …) are
    // blocked regardless of whether Cloudflare has verified them, because the
    // verification only proves the request genuinely came from those crawlers,
    // not that we want to serve them. YouBot was removed from AI_BOT_UA
    // entirely and reclassified as a search bot, so it never reaches this branch.
    const isRobotsRequest = /^\/robots\.txt$/i.test(pathname);
    if (AI_BOT_UA.test(ua) && !isRobotsRequest) {
      return new Response(
        "Forbidden: AI crawlers are not permitted on this site. " +
        "See https://syrabit.ai/robots.txt for the policy.\n",
        {
          status: 403,
          headers: {
            ...cors,
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "public, max-age=3600",
            "X-Robots-Tag": "noai, noimageai, noindex",
          },
        },
      );
    }

    const isApiRoute = pathname.startsWith("/api/");

    // Task #672: alias the canonical /sitemap.xml to the dynamic D1 sitemap
    // index. Crawlers (Google, Bing, etc.) probe the standard root location;
    // there is no static sitemap.xml on Pages, so without this internal
    // rewrite the request would fall through to PAGES_ORIGIN and return a
    // 404 / SPA shell. Internal rewrite (no redirect hop) keeps discovery
    // fast and avoids a 301 -> follow round-trip for bots.
    if (
      pathname === "/sitemap.xml" &&
      (request.method === "GET" || request.method === "HEAD") &&
      env.CONTENT_DB
    ) {
      try {
        const indexResult = await tryD1Route(
          env,
          "/api/seo/sitemap-index.xml",
          url.searchParams,
        );
        if (indexResult !== null && indexResult.type === "xml") {
          return d1XmlResponse(indexResult.data, cors, remaining);
        }
      } catch { /* fall through to Pages on D1 failure */ }
    }

    // Task #246: alias /sitemap-delta.xml to the D1 delta-sitemap handler.
    // Must be handled before the isApiRoute / !isApiRoute split or it falls
    // through to PAGES_ORIGIN (no static file there) and returns 404/SPA.
    if (
      pathname === "/sitemap-delta.xml" &&
      (request.method === "GET" || request.method === "HEAD") &&
      env.CONTENT_DB
    ) {
      try {
        const deltaResult = await tryD1Route(env, "/sitemap-delta.xml", url.searchParams);
        if (deltaResult !== null && deltaResult.type === "xml") {
          return d1XmlResponse(deltaResult.data, cors, remaining);
        }
      } catch { /* fall through on D1 failure */ }
    }

    // Bot-discovery endpoints live on the FastAPI backend (not Pages and not
    // D1). Crawlers probe these at the zone root; without these internal
    // rewrites the request would fall through to PAGES_ORIGIN and return
    // the SPA HTML shell, rendering robots.txt / llms.txt unparseable.
    // Kept separate from /api/* routing because the canonical public paths
    // are root-level (per the llms.txt spec and the robots.txt RFC).
    const BOT_DISCOVERY_PATHS = new Set([
      "/robots.txt",
      "/llms.txt",
      "/llms-full.txt",
      "/.well-known/ai-plugin.json",
    ]);
    if (
      BOT_DISCOVERY_PATHS.has(pathname) &&
      (request.method === "GET" || request.method === "HEAD")
    ) {
      return proxyToBackend(request, env, pathname, url.search, clientIp, cors, remaining);
    }

    if (!isSearchBot && isApiRoute) {
      // Phase 5: per-IP and per-user (anon-id) Durable Object rate limiting.
      // x-anon-id is the anonymous/authenticated user identifier set by the SPA.
      // We enforce BOTH dimensions when the header is present so that users on
      // shared IPs (campus, corporate NAT) cannot starve each other.
      const anonId = (request.headers.get("x-anon-id") || "").trim().replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 64);

      if (isAiPath(pathname)) {
        // Task #513 §A — chat cap. Short-circuit BEFORE the per-IP /
        // per-anon rate-limit check so a capped client never reaches
        // the FastAPI origin (and never burns LLM dispatch budget).
        if (isChatPath(pathname)) {
          // Identity = JWT-verified `sub` (user_id) when the bearer
          // token validates against `JWT_SECRET`. Otherwise we fall
          // back to anon-id, then IP. We deliberately IGNORE any
          // `x-plan` header — only the JWT-verified `plan` claim
          // grants the daily-soft-cap bypass for paid users (the old
          // header-trusting code allowed an attacker to forge
          // `x-plan: pro` and skip the per-user budget).
          const verified = await verifyJwtFromRequest(request, env);
          let identity: string;
          let identityKind: "user" | "anon" | "ip";
          if (verified) {
            identity = `u:${verified.user_id}`;
            identityKind = "user";
          } else if (anonId) {
            identity = `a:${anonId}`;
            identityKind = "anon";
          } else {
            identity = `ip:${clientIp}`;
            identityKind = "ip";
          }
          const paid = verified ? _isPaidPlan(verified.plan) : false;

          const cap = await precheckChatCap(env, identity, identityKind, paid);
          if (!cap.allowed) {
            const retryAfter = String(cap.retry_after ?? (cap.window === "day" ? _secondsToNextUtcMidnight() : _secondsToNextUtcMonthStart()));
            // §A response contract (round-2): body is
            //   {"error":"chat_budget_exhausted"|"chat_daily_soft_cap",
            //    "cap": <int>, "reset_at": <ISO-8601 UTC>,
            //    "window": "month"|"day",
            //    "remaining_month": <int>, "remaining_day": <int>,
            //    "detail": "..."}
            // and the canonical client-readable header is `X-Cap`,
            // matching the smoke test's contract assertion.
            const xCap = cap.error === "chat_daily_soft_cap"
              ? "chat_daily_3_per_anon"
              : "chat_monthly_30_per_anon";
            return new Response(
              JSON.stringify({
                error:           cap.error,
                cap:             cap.limit,
                reset_at:        cap.reset,
                window:          cap.window,
                remaining_month: cap.remaining_month,
                remaining_day:   cap.remaining_day,
                detail: cap.error === "chat_daily_soft_cap"
                  ? "Daily chat allowance reached. Try again after the next UTC midnight or upgrade for unlimited daily turns."
                  : "Monthly chat budget reached. Resets at the start of next UTC month.",
              }),
              {
                status: 429,
                headers: {
                  ...cors,
                  "Content-Type": "application/json",
                  "Retry-After":                 retryAfter,
                  "X-Cap":                       xCap,
                  "X-Chat-Cap-Error":            cap.error || "chat_capped",
                  "X-Chat-Cap-Remaining-Month":  String(cap.remaining_month ?? 0),
                  "X-Chat-Cap-Remaining-Day":    String(cap.remaining_day ?? 0),
                  "X-Chat-Cap-Reset":            String(cap.reset ?? ""),
                  "X-Chat-Cap-Identity":         identityKind,
                  "X-AE-RL": "chat_capped",
                },
              },
            );
          }
          // Allowed — stash the cap context so the outer handler can
          // bump the counters AFTER the origin returns a success
          // status (post-success increment per §A spec). KV-unavailable
          // requests skip the bump entirely.
          if (!cap.bypassed) {
            _CHAT_CAP_PENDING.set(request, { dayKey: cap.dayKey, monthKey: cap.monthKey, paid });
          }
        }
        // AI rate limit — check per-IP first, then per-user if anon-id present.
        const aiIpKey   = `rl:ai:${clientIp}`;
        const aiUserKey = anonId ? `rl:ai:user:${anonId}` : null;

        const [aiIpRl, aiUserRl] = await Promise.all([
          checkRateLimitWithDO(aiIpKey, env, AI_RATE_LIMIT_RPM),
          aiUserKey ? checkRateLimitWithDO(aiUserKey, env, AI_RATE_LIMIT_RPM) : Promise.resolve({ allowed: true, remaining: AI_RATE_LIMIT_RPM }),
        ]);

        if (!aiIpRl.allowed || !aiUserRl.allowed) {
          // X-AE-RL is an internal signal header read by the outer fetch handler
          // to set rateLimitResult on the per-request AE datapoint. It is stripped
          // from the final response before it reaches the client.
          return new Response(
            JSON.stringify({ detail: "AI rate limit exceeded. Please slow down." }),
            {
              status: 429,
              headers: {
                ...cors,
                "Content-Type": "application/json",
                "Retry-After": String(RATE_LIMIT_WINDOW_S),
                "X-RateLimit-Limit": String(AI_RATE_LIMIT_RPM),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Scope": "ai",
                "X-AE-RL": "ai_limited",
              },
            }
          );
        }
        // AI request passed both rate limits — outer fetch handler emits AE datapoint.
      }

      // General API rate limit — check per-IP first, then per-user if anon-id present.
      const ipKey   = `rl:${clientIp}`;
      const userKey = anonId ? `rl:user:${anonId}` : null;

      const [ipRl, userRl] = await Promise.all([
        checkRateLimitWithDO(ipKey, env, RATE_LIMIT_RPM),
        userKey ? checkRateLimitWithDO(userKey, env, RATE_LIMIT_RPM) : Promise.resolve({ allowed: true, remaining: RATE_LIMIT_RPM }),
      ]);

      remaining = Math.min(ipRl.remaining, userRl.remaining);
      if (!ipRl.allowed || !userRl.allowed) {
        return new Response(
          JSON.stringify({ detail: "Rate limit exceeded. Try again shortly." }),
          {
            status: 429,
            headers: {
              ...cors,
              "Content-Type": "application/json",
              "Retry-After": String(RATE_LIMIT_WINDOW_S),
              "X-RateLimit-Limit": String(RATE_LIMIT_RPM),
              "X-RateLimit-Remaining": "0",
              "X-AE-RL": "ip_limited",
            },
          }
        );
      }
    }

    if (!isApiRoute && (request.method === "GET" || request.method === "HEAD")) {
      if (isSearchBot && request.method === "GET") {
        // Task #9 — Verified-search + citation-AI bots get the high-RPM
        // bucket (VERIFIED_BOT_RATE_LIMIT_RPM, 60 000 RPM). Other
        // verified UAs (Twitterbot, FacebookExternalHit, …) stay on
        // the standard BOT_RATE_LIMIT_RPM (3 000 RPM) bucket — they're
        // verified but they're crawling for previews, not indexing,
        // so the lower ceiling is appropriate. `verifyBotIpWithKv`
        // also extends trust to bots whose rDNS PTR matches one of
        // the canonical suffixes in `infra/bot-rules.yaml` (24h KV
        // cache) when cf.verifiedBot was false.
        // `fcrDnsAlreadyConfirmed` short-circuits the duplicate KV
        // check — if we already promoted isSearchBot via FCrDNS above
        // we don't re-query DoH/KV here.
        const isFastPath = VERIFIED_BOT_UA.test(ua) && (
          (request as unknown as { cf?: { verifiedBot?: boolean } }).cf?.verifiedBot === true ||
          fcrDnsAlreadyConfirmed ||
          await verifyBotIpWithKv(env, ctx, ua, clientIp)
        );
        const botCap = isFastPath ? VERIFIED_BOT_RATE_LIMIT_RPM : BOT_RATE_LIMIT_RPM;
        const botScope = isFastPath ? "verified_bot" : "bot";
        const botRlKey = isFastPath ? `rl:vbot:${clientIp}` : `rl:bot:${clientIp}`;
        const botRl = await checkRateLimitWithDO(botRlKey, env, botCap);
        if (!botRl.allowed) {
          return new Response("Too Many Requests", {
            status: 429,
            headers: {
              ...cors,
              "Retry-After": String(RATE_LIMIT_WINDOW_S),
              "X-RateLimit-Limit": String(botCap),
              "X-RateLimit-Remaining": "0",
              "X-RateLimit-Scope": botScope,
            },
          });
        }
        const botResp = await handleBotContentRequest(env, pathname, clientIp, request, ctx);
        if (botResp) return botResp;
      } else if (botResult.claimsBot && !isSearchBot && request.method === "GET") {
        // Unverified claimed bots (UA matches bot pattern but
        // neither cf.verifiedBot nor FCrDNS confirmed): per task #9
        // requirement, fall back to the SHARED bot bucket
        // (BOT_RATE_LIMIT_RPM = 3 000 RPM) — NOT the general
        // 120 RPM ceiling — so legitimate crawlers caught in
        // verification edge cases (cold KV, transient DoH failure)
        // are not throttled to user-API limits. This is still a
        // 20× tighter bucket than the verified-bot fast path, so
        // a real spoofer cannot abuse it for scraping.
        const unverifiedBotRlKey = `rl:ubot:${clientIp}`;
        const unverifiedBotRl = await checkRateLimitWithDO(unverifiedBotRlKey, env, BOT_RATE_LIMIT_RPM);
        if (!unverifiedBotRl.allowed) {
          return new Response("Too Many Requests", {
            status: 429,
            headers: {
              ...cors,
              "Retry-After": String(RATE_LIMIT_WINDOW_S),
              "X-RateLimit-Limit": String(BOT_RATE_LIMIT_RPM),
              "X-RateLimit-Remaining": "0",
              "X-RateLimit-Scope": "unverified_bot",
            },
          });
        }
      }
      // CRITICAL: do NOT call fetch(request) — this worker is bound to
      // syrabit.ai/* and www.syrabit.ai/*, and fetch(request) re-enters
      // the same worker route causing recursion that resolves to garbage
      // (Pages HTML body + backend 404 headers). Always proxy to the
      // Pages origin by its workers.dev hostname so the worker route is
      // bypassed cleanly. HEAD must be handled here too — the SEO health
      // checker probes URLs with HEAD and would otherwise fall through to
      // Railway and get 404.
      const pagesOrigin = env.PAGES_ORIGIN || "https://syrabit-zip-convert.pages.dev";
      const pagesUrl = `${pagesOrigin}${url.pathname}${url.search}`;
      const upstream = await fetch(pagesUrl, {
        method: request.method,
        headers: request.headers,
        redirect: "manual",
      });
      // Inject perf headers Pages does not propagate from the zone:
      //  - alt-svc: advertises HTTP/3 so browsers upgrade subsequent requests
      //  - X-Polish-Hint: a marker proving the request flowed through the worker
      //    so we can confirm in DevTools when investigating Polish behaviour
      const out = new Response(upstream.body, upstream);
      if (!out.headers.has("alt-svc")) {
        out.headers.set("alt-svc", 'h3=":443"; ma=86400, h3-29=":443"; ma=86400');
      }
      out.headers.set("X-Edge-Worker", "syrabit-edge");
      // Encourage Polish on image responses by ensuring a public, cacheable
      // Cache-Control header. Polish skips images with no-cache/private.
      const ct = (out.headers.get("content-type") || "").toLowerCase();
      if (ct.startsWith("image/") && !out.headers.has("cache-control")) {
        out.headers.set("cache-control", "public, max-age=86400");
      }
      // Log 4xx/5xx responses served to known bot UAs for crawl-budget analysis.
      if (botResult.claimsBot && out.status >= 400) {
        logBotErrorResponse(env, ctx, out.status, botResult, ua, pathname);
      }
      // Task #4 — inject route-specific <title> + <meta name="description">
      // for verified/claimed search bots that fell through to the SPA shell
      // (i.e. routes not covered by the backend prerender set).  Human users
      // receive the original upstream body; HEAD and non-HTML responses are
      // passed through untouched because HTMLRewriter is a no-op there.
      //
      // Task #9 — when no pattern matches (meta === null), the onMiss callback
      // fires an Analytics Engine datapoint so the admin can surface the top
      // uncovered bot-crawled paths in the admin dashboard.  The datapoint
      // shares the syrabit-edge-metrics schema (same blobs/doubles layout) and
      // uses blob1="spa_title_miss" as the event-type discriminator so the
      // existing cache-metric queries are unaffected.
      const _isBotGet = (isSearchBot || botResult.claimsBot) && request.method === "GET";
      const _onTitleMiss = (env.ANALYTICS && _isBotGet)
        ? (p: string): void => {
            ctx.waitUntil(Promise.resolve(
              env.ANALYTICS!.writeDataPoint({
                blobs:   ["spa_title_miss", "", "none", p.slice(0, 64), "ok"],
                doubles: [0, 0, out.status],
                indexes: ["spa_title_miss"],
              }),
            ));
          }
        : undefined;
      return _injectSpaTitleForBot(out, pathname, _isBotGet, _onTitleMiss);
    }

    if ((request.method !== "GET" && request.method !== "HEAD") || isBypass(pathname)) {
      const proxyResp = await proxyToBackend(request, env, pathname, url.search, clientIp, cors, remaining);
      if (botResult.claimsBot && proxyResp.status >= 400) {
        logBotErrorResponse(env, ctx, proxyResp.status, botResult, ua, pathname);
      }
      return proxyResp;
    }

    const hasAuth =
      request.headers.has("Authorization") ||
      request.headers.has("Cookie") ||
      request.headers.has("x-anon-id");

    if (isCacheable(pathname) && (!hasAuth || !isUserSpecific(pathname))) {
      const nocache = url.searchParams.get("nocache");

      const cache = caches.default;
      const cacheKey = new Request(url.toString(), { method: "GET" });

      // ──────────────────────────────────────────────────────────────────
      // CF Cache lookup BEFORE D1, so warm requests skip the D1 round-trip
      // entirely (D1 read = ~500–700ms for library-bundle even though it's
      // a synced replica). After this change, library-bundle TTFB drops
      // from ~700ms to ~30ms on CF cache hits within the same POP.
      // Honors If-None-Match → 304 so the browser skips downloading the
      // 1.1 MB Brotli body when its cached copy is still valid.
      // ──────────────────────────────────────────────────────────────────
      if (!nocache) {
        const cachedResponse = await cache.match(cacheKey);
        if (cachedResponse) {
          const ttl = getCacheTtl(pathname);
          const etag = cachedResponse.headers.get("ETag");
          const ifNoneMatch = request.headers.get("If-None-Match");
          if (etag && ifNoneMatch && ifNoneMatch === etag) {
            return new Response(null, {
              status: 304,
              headers: {
                ...cors,
                "Cache-Control": `public, max-age=${ttl}, stale-while-revalidate=${ttl * 2}`,
                "ETag": etag,
                "X-Cache": "HIT-304",
                "X-Source": "cf-cache",
                "X-RateLimit-Remaining": String(remaining),
              },
            });
          }
          const resp = new Response(cachedResponse.body, cachedResponse);
          Object.entries(cors).forEach(([k, v]) => resp.headers.set(k, v));
          resp.headers.set("Cache-Control", `public, max-age=${ttl}, stale-while-revalidate=${ttl * 2}`);
          resp.headers.set("X-Cache", "HIT");
          resp.headers.set("X-Source", "cf-cache");
          resp.headers.set("X-RateLimit-Remaining", String(remaining));
          return resp;
        }
      }

      if (!nocache && env.CONTENT_DB) {
        try {
          const d1Result = await tryD1Route(env, pathname, url.searchParams);
          if (d1Result !== null) {
            const overrideTtl = getPrewarmOverrideTtl(request, env);
            if (d1Result.type === "xml") {
              const xmlResp = d1XmlResponse(d1Result.data, cors, remaining);
              const stored = overrideTtl != null
                ? withOverriddenTtl(xmlResp, overrideTtl)
                : xmlResp;
              // Cache XML responses too so subsequent same-POP requests
              // hit cf-cache instead of re-running the D1 sitemap query.
              ctx.waitUntil(cache.put(cacheKey, stored.clone()));
              return xmlResp;
            }
            const jsonResp = d1JsonResponse(d1Result.data, cors, remaining, pathname);
            const storedJson = overrideTtl != null
              ? withOverriddenTtl(jsonResp, overrideTtl)
              : jsonResp;
            // Persist to CF cache. Subsequent requests within the TTL
            // window served by this POP skip D1 entirely.
            ctx.waitUntil(cache.put(cacheKey, storedJson.clone()));
            return jsonResp;
          }
        } catch { /* fall through to backend */ }
      }

      const backendUrl = `${env.BACKEND_URL}${pathname}${url.search}`;
      const backendHeaders = buildProxyHeaders(request, clientIp, env);
      await addMtlsActiveHeader(backendHeaders, env);

      try {
        // Phase 6 (Task #110): use fetchBackend() — mTLS cert presented here too.
        const backendResp = await fetchBackend(env, backendUrl, {
          method: "GET",
          headers: backendHeaders,
        });

        if (backendResp.ok) {
          const overrideTtl = getPrewarmOverrideTtl(request, env);
          const ttl = overrideTtl != null ? overrideTtl : getCacheTtl(pathname);
          const respBody = await backendResp.arrayBuffer();
          const contentType = backendResp.headers.get("Content-Type") || "application/json";
          const cacheControl = `public, max-age=${ttl}, stale-while-revalidate=${ttl * 2}`;
          const tags = buildCacheTags(pathname);

          const cachedHeaders: Record<string, string> = {
            "Content-Type": contentType,
            "Cache-Control": `public, s-maxage=${ttl}, stale-while-revalidate=${ttl * 2}`,
            "Surrogate-Control": cacheControl,
            "Vary": "Accept-Encoding, Accept",
          };
          if (tags) cachedHeaders["Cache-Tag"] = tags;
          const cachedResp = new Response(respBody, {
            status: backendResp.status,
            headers: cachedHeaders,
          });
          ctx.waitUntil(cache.put(cacheKey, cachedResp.clone()));

          const clientHeaders: Record<string, string> = {
            ...cors,
            "Content-Type": contentType,
            "Cache-Control": cacheControl,
            "Vary": "Accept-Encoding, Accept",
            "X-Cache": "MISS",
            "X-Source": "backend",
            "X-RateLimit-Remaining": String(remaining),
          };
          if (tags) clientHeaders["Cache-Tag"] = tags;
          const clientResp = new Response(respBody, {
            status: backendResp.status,
            headers: clientHeaders,
          });
          return clientResp;
        }

        const body = await backendResp.text();
        const nonOkResp = new Response(body, {
          status: backendResp.status,
          headers: {
            ...cors,
            "Content-Type":
              backendResp.headers.get("Content-Type") || "application/json",
            "X-Cache": "BYPASS",
            "X-Source": "backend",
          },
        });
        if (botResult.claimsBot && nonOkResp.status >= 400) {
          logBotErrorResponse(env, ctx, nonOkResp.status, botResult, ua, pathname);
        }
        return nonOkResp;
      } catch (err) {
        const unavailResp = new Response(
          JSON.stringify({ detail: "Backend unavailable", edge: true }),
          {
            status: 502,
            headers: { ...cors, "Content-Type": "application/json", "X-Source": "backend" },
          }
        );
        if (botResult.claimsBot) {
          logBotErrorResponse(env, ctx, 502, botResult, ua, pathname);
        }
        return unavailResp;
      }
    }

    const finalResp = await proxyToBackend(request, env, pathname, url.search, clientIp, cors, remaining);
    if (botResult.claimsBot && finalResp.status >= 400) {
      logBotErrorResponse(env, ctx, finalResp.status, botResult, ua, pathname);
    }
    return finalResp;
}

export default {
  async fetch(
    request: Request,
    env: Env,
    ctx: ExecutionContext,
  ): Promise<Response> {
    // Task #1 — JWT_SECRET startup guard. A missing binding causes every
    // JWT verification to silently treat the caller as anonymous, letting
    // unauthenticated requests bypass credit caps and paid-tier checks.
    // Fail loud (V4 §12) rather than silently degrade auth. This check
    // fires on the first request after a deploy; CF Workers have no
    // explicit startup hook so the request handler is the earliest point.
    if (!(env as Env & { JWT_SECRET?: string }).JWT_SECRET) {
      throw new Error("[startup] JWT_SECRET binding is absent — refusing to handle requests with missing auth secret");
    }
    // Wall-clock at handler entry — used for the duration_ms field on
    // the unified-log record. Captured *before* the inner handler runs
    // so the buffered record reflects the full edge processing time
    // (cache lookup + KV ops + origin proxy round-trip), not just the
    // origin's view.
    const startMs = Date.now();
    let response: Response;
    let level: "info" | "warn" | "error" | "debug" | undefined;
    // Task #575 — refresh the season snapshot once per request before
    // the inner handler dispatches. The helper is internally rate-
    // limited to one origin call per 60s per isolate so this stays
    // O(1) on the hot path.
    await refreshSeasonSnapshot(env, ctx);
    try {
      response = await _handleEdgeFetch(request, env, ctx);
    } catch (err) {
      // Worker-level crash — synthesize a 500 so the user sees a sane
      // error AND the unified log captures the failure with level=error.
      level = "error";
      response = new Response(
        JSON.stringify({ detail: "Edge worker error" }),
        {
          status: 500,
          headers: { "Content-Type": "application/json", "X-Source": "edge" },
        },
      );
      console.error("[edge] unhandled fetch error:", err);
    }
    // Cache disposition — preserves the X-Cache header the worker
    // already sets on most responses (HIT / MISS / BYPASS / DYNAMIC).
    const xCache = (response.headers.get("x-cache") || "").toLowerCase();
    const cache: "hit" | "miss" | "bypass" | "dynamic" | null =
      xCache === "hit" ? "hit" :
      xCache === "miss" ? "miss" :
      xCache === "bypass" ? "bypass" :
      xCache === "dynamic" ? "dynamic" :
      null;
    // ── Phase 5: per-request Analytics Engine datapoint ─────────────────────
    // X-AE-RL is an internal signal header set by rate-limit 429 return paths
    // inside _handleEdgeFetch. We read it here to populate rateLimitResult and
    // then strip it so it never reaches the client.
    const aeRl     = response.headers.get("x-ae-rl") ?? "ok";
    const reqUrl   = new URL(request.url);
    const reqPath  = reqUrl.pathname;
    writeEdgeMetric(env, ctx, startMs, {
      cacheStatus:      cache ?? "dynamic",
      chapterId:        extractChapterIdFromPath(reqPath),
      aiProvider:       aiProviderFromPath(reqPath),
      pathname:         reqPath,
      rateLimitResult:  aeRl,
      isAiRequest:      isAiPath(reqPath),
      httpStatus:       response.status,
    });
    // ── Task #513 §A — post-success chat-cap increment ──────────────────────
    // Bump the per-anon day + month counters ONLY when the origin
    // returned a success status (< 400). 4xx/5xx responses that the
    // SPA may retry deliberately do NOT consume a turn against the
    // 30/month + 3/day budget.
    const _capPending = _CHAT_CAP_PENDING.get(request);
    if (_capPending && response.status < 400) {
      ctx.waitUntil(bumpChatCapOnSuccess(env, _capPending.dayKey, _capPending.monthKey, _capPending.paid));
    }
    if (_capPending) {
      _CHAT_CAP_PENDING.delete(request);
    }
    // Strip internal header if present (only on rate-limit 429 responses).
    if (response.headers.has("x-ae-rl")) {
      const stripped = new Headers(response.headers);
      stripped.delete("x-ae-rl");
      response = new Response(response.body, {
        status:     response.status,
        statusText: response.statusText,
        headers:    stripped,
      });
    }
    recordEdgeLog(
      request,
      response,
      { startMs, cache, level },
      env as EdgeLogShipperEnv,
      ctx,
    );
    return response;
  },

  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    // Multiple cron triggers fan out from the same scheduled handler.
    // We dispatch on `event.cron` so each trigger only runs the job it
    // was designed for. The fallback below preserves the historical
    // single-cron behaviour: when `event.cron` is empty (e.g. the local
    // wrangler emulator on older versions, or any future invocation
    // that does not match a known schedule), we run the D1 sync — that
    // job is idempotent and has been the only scheduled job for this
    // worker for months, so defaulting to it is the safe, no-surprises
    // choice.
    
    // Task: D1 Cache Warming on Startup — preload hot content into D1/KV cache
    // when the worker starts to eliminate cold-start latency (~10-50ms → ~0ms).
    // Runs once per worker boot before any user traffic arrives.
    if (!_d1WarmOnStartupDone && env.D1_WARM_ON_STARTUP?.toLowerCase() === 'true') {
      _d1WarmOnStartupDone = true;
      console.log('[D1 warm-on-startup] Starting immediate cache warm-up...');
      const warmStart = Date.now();
      ctx.waitUntil(
        handleScheduledSync(env)
          .then(() => {
            const duration = Date.now() - warmStart;
            console.log(`[D1 warm-on-startup] Complete in ${duration}ms`);
          })
          .catch((e) => {
            const msg = e instanceof Error ? e.message : 'unknown';
            console.error(`[D1 warm-on-startup] Failed: ${msg.slice(0, 300)}`);
          })
      );
    }
    
    const cron = event.cron;
    if (cron === "* * * * *") {
      // Task #708 — 1-minute synthetic probe of /api/admin/diagnostics.
      // Task #817 — same minute, also probe the public homepage from
      // outside the cluster to detect CF managed-rule / Bot Fight /
      // custom-firewall false positives that the admin probe is blind
      // to. The two probes share the RATE_LIMIT KV but use distinct
      // state keys, and share the watchdog webhook with distinct
      // alert_type values so the receiver can route each one.
      // Wrap the env so KV ops from both probes also feed the
      // kv-monitor counters (4 ops/min total ≈ 5760 ops/day, well
      // under quota — but visible in the dashboard nonetheless).
      const wrapped = wrapEnvKv(env, ctx);
      ctx.waitUntil(runSyntheticProbe(wrapped).catch((e) => {
        const msg = e instanceof Error ? e.message : "unknown";
        console.error(`[synthetic-probe] unhandled error: ${msg.slice(0, 300)}`);
      }));
      ctx.waitUntil(runCfBlockProbe(wrapped).catch((e) => {
        const msg = e instanceof Error ? e.message : "unknown";
        console.error(`[cf-block-probe] unhandled error: ${msg.slice(0, 300)}`);
      }));
      // Task #898 — bot-cache hit-rate / fallback-rate watchdog. Reads
      // the `bot_cache.*` counters from RATE_LIMIT KV (no HTTP) and
      // pages on a sudden drop or sustained fallback. Shares the
      // synthetic probe watchdog webhook with distinct alert_type
      // values so the receiver can route each independently.
      ctx.waitUntil(runBotCacheAlert(wrapped).catch((e) => {
        const msg = e instanceof Error ? e.message : "unknown";
        console.error(`[bot-cache-alert] unhandled error: ${msg.slice(0, 300)}`);
      }));
      // Task #311 — AI Gateway 24h embed cache-hit-rate watchdog.
      // Runs only on the :00, :15, :30, :45 minute marks (4 times per
      // hour). The 24h window moves slowly and each iteration costs
      // one Cloudflare GraphQL query against the AI Gateway analytics
      // dataset; running it every minute would burn ~1,440 calls/day
      // with no signal benefit. The handler still runs on every
      // minute for the synthetic / cf-block / bot-cache probes; only
      // this single watchdog is gated.
      const scheduledAt = new Date(event.scheduledTime);
      const minuteOfHour = scheduledAt.getUTCMinutes();
      if (minuteOfHour % 15 === 0) {
        ctx.waitUntil(runAiGatewayCacheAlert(wrapped).catch((e) => {
          const msg = e instanceof Error ? e.message : "unknown";
          console.error(`[ai-gateway-cache-alert] unhandled error: ${msg.slice(0, 300)}`);
        }));
      }
      // Task #314 — monthly R2 cold-storage / Logpush-cap watchdog.
      // Gated to a single evaluation per calendar month at 00:00 UTC
      // on day 1 (the cost-review checklist runs on the first business
      // day, so the alert lands in inbox before the human reviewer
      // sits down). The 28-day cooldown inside the module makes a
      // duplicate run a no-op anyway, but gating here saves the
      // per-minute GraphQL + R2-list cost.
      if (shouldRunMonthlyR2Check(scheduledAt)) {
        ctx.waitUntil(runR2StorageClassAlert(wrapped).catch((e) => {
          const msg = e instanceof Error ? e.message : "unknown";
          console.error(`[r2-storage-class-alert] unhandled error: ${msg.slice(0, 300)}`);
        }));
      }
      return;
    }
    if (cron === "0 1 * * *") {
      // Task #13 — daily SPA title-miss gap alert. Queries the Analytics
      // Engine for the rolling 24h window and pages when any uncovered
      // path exceeds SPA_TITLE_MISS_ALERT_THRESHOLD (default 50) hits.
      // `wrapped` is scoped to the "* * * * *" branch above; use `env`
      // directly here (KV ops in the alert use RATE_LIMIT, not the
      // kv-monitor wrapper, which is fine for a once-daily job).
      //
      // Task #33 — read KV-persisted settings before calling the alert so
      // that threshold / disabled changes made from the admin dashboard take
      // effect on the next cron run without a wrangler redeploy.
      ctx.waitUntil((async () => {
        const kvSettings = await _readSpaTitleMissKvSettings(env.RATE_LIMIT, env);
        // Build an env overlay that splices in the KV-derived values so the
        // alert module's threshold() / kill-switch checks see the runtime value.
        const alertEnv = {
          ...env,
          SPA_TITLE_MISS_ALERT_THRESHOLD: String(kvSettings.threshold),
          SPA_TITLE_MISS_ALERT_DISABLED:  String(kvSettings.disabled),
        };
        await runSpaTitleMissAlert(alertEnv, {
          resolveMeta: _resolveSpaRouteMeta,
          slugToTitle: _slugToTitle,
        });
      })().catch((e) => {
        const msg = e instanceof Error ? e.message : "unknown";
        console.error(`[spa-title-miss-alert] unhandled error: ${msg.slice(0, 300)}`);
      }));
      return;
    }
    if (cron === "0 */6 * * *") {
      ctx.waitUntil(handleScheduledSync(env));
      return;
    }
    // Backwards-compat: when the worker was deployed with only the
    // 6-hourly cron, event.cron may be empty in the local emulator.
    // Default to the D1 sync so existing behaviour is preserved.
    ctx.waitUntil(handleScheduledSync(env));
  },
};
