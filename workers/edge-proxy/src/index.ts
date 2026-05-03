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
import {
  recordBotCacheEvent,
  getBotCacheStats,
  type BotCacheStats,
} from "./bot-cache-stats";
import {
  getCacheablePrefixes,
  getCacheTtlEntries,
  getBypassPrefixes,
  getUserSpecificPrefixes,
  DEFAULT_CACHE_TTL_SECONDS,
} from "./monitored-urls";
// Task #944 — Unified Log Explorer: per-request shipper that batches
// records and POSTs them to /api/logs/ingest via ctx.waitUntil so it
// never adds latency to user-visible responses.
import { recordEdgeLog, type EdgeLogShipperEnv } from "./log-shipper";
// Task #109 Phase 5 — Durable Object rate limiter + Analytics Engine query utility.
import { RateLimiter } from "./rate-limiter-do";
import { queryEdgeMetrics } from "./analytics-engine";
export { RateLimiter };

interface Env {
  BACKEND_URL: string;
  PAGES_ORIGIN?: string;
  RATE_LIMIT: KVNamespace;
  BOT_HTML_CACHE?: KVNamespace;
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
}

const KV_BINDINGS = ["RATE_LIMIT", "BOT_HTML_CACHE"] as const;

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
const USER_SPECIFIC_PREFIXES = getUserSpecificPrefixes();
const BYPASS_PREFIXES = getBypassPrefixes();

const RATE_LIMIT_RPM = 120;
const BOT_RATE_LIMIT_RPM = 3000;
const RATE_LIMIT_WINDOW_S = 60;
const AI_RATE_LIMIT_RPM = 30;
const AI_RATE_LIMIT_PREFIXES = ["/api/ai/chat", "/api/ai/generate", "/api/ai/grounded", "/api/ai/explain", "/api/ai/quiz", "/api/ai/summarize", "/api/chat"];

// D1 Sync warm-on-startup flag — runs sync immediately when worker boots
let _d1WarmOnStartupDone = false;
function isAiPath(p: string): boolean {
  if (p.startsWith("/api/ai/fallback/")) return false;
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
const SEARCH_BOT_UA = /googlebot|google-extended|googleother|google-inspectiontool|bingbot|yandexbot|duckduckbot|slurp|baiduspider|applebot|applebot-extended|chatgpt-user|oai-searchbot|gptbot|perplexitybot|perplexity-user|claudebot|claude-web|anthropic-ai|meta-externalagent|bytespider|ccbot|amazonbot|facebookexternalhit|facebookbot|twitterbot|linkedinbot|whatsapp|telegrambot|discordbot|youbot/i;

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
const AI_BOT_UA = /\b(?:GPTBot|CCBot|Google-Extended|Applebot-Extended|Meta-ExternalAgent|Bytespider|Amazonbot|Cohere-AI|Diffbot|ClaudeBot|Claude-Web|anthropic-ai)\b/i;

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

function getCacheTtl(pathname: string): number {
  // CACHE_TTL_ENTRIES is sorted by descending key length so the most
  // specific prefix wins (e.g. /api/seo/keyword-index before /api/seo/).
  for (const [prefix, ttl] of CACHE_TTL_ENTRIES) {
    if (pathname.startsWith(prefix)) return ttl;
  }
  return DEFAULT_CACHE_TTL_SECONDS;
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
 * backend provider (groq/gemini/cerebras) ultimately served the request.
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

const _KNOWN_BOARDS = new Set(["ahsec", "seba", "degree", "cbse", "nep"]);

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

function resolveBotApiUrl(env: Env, pathname: string): string | null {
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
    const isSearchBot = botResult.verified;
    let remaining = 999999;

    if (botResult.spoofed) {
      const ipH = hashIp(clientIp);
      const colo = (request as unknown as { cf?: { colo?: string } }).cf?.colo || "unknown";
      ctx.waitUntil(logSpoofedBot(env.RATE_LIMIT, ipH, ua, clientIp, colo));
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
        // Verified bots (cf.verifiedBot === true): rate-cap at BOT_RATE_LIMIT_RPM (3000 RPM)
        // per IP to prevent aggressive re-crawl from overwhelming the backend.
        const botRlKey = `rl:bot:${clientIp}`;
        const botRl = await checkRateLimitWithDO(botRlKey, env, BOT_RATE_LIMIT_RPM);
        if (!botRl.allowed) {
          return new Response("Too Many Requests", {
            status: 429,
            headers: {
              ...cors,
              "Retry-After": String(RATE_LIMIT_WINDOW_S),
              "X-RateLimit-Limit": String(BOT_RATE_LIMIT_RPM),
              "X-RateLimit-Remaining": "0",
              "X-RateLimit-Scope": "bot",
            },
          });
        }
        const botResp = await handleBotContentRequest(env, pathname, clientIp, request, ctx);
        if (botResp) return botResp;
      } else if (botResult.claimsBot && !isSearchBot && request.method === "GET") {
        // Unverified claimed bots (UA matches bot pattern but cf.verifiedBot is false):
        // enforce the same 120 RPM ceiling as general API traffic to prevent scraping.
        const unverifiedBotRlKey = `rl:${clientIp}`;
        const unverifiedBotRl = await checkRateLimitWithDO(unverifiedBotRlKey, env, RATE_LIMIT_RPM);
        if (!unverifiedBotRl.allowed) {
          return new Response("Too Many Requests", {
            status: 429,
            headers: {
              ...cors,
              "Retry-After": String(RATE_LIMIT_WINDOW_S),
              "X-RateLimit-Limit": String(RATE_LIMIT_RPM),
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
      return out;
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
            if (d1Result.type === "xml") {
              const xmlResp = d1XmlResponse(d1Result.data, cors, remaining);
              // Cache XML responses too so subsequent same-POP requests
              // hit cf-cache instead of re-running the D1 sitemap query.
              ctx.waitUntil(cache.put(cacheKey, xmlResp.clone()));
              return xmlResp;
            }
            const jsonResp = d1JsonResponse(d1Result.data, cors, remaining, pathname);
            // Persist to CF cache. Subsequent requests within the TTL
            // window served by this POP skip D1 entirely.
            ctx.waitUntil(cache.put(cacheKey, jsonResp.clone()));
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
          const ttl = getCacheTtl(pathname);
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
    // Wall-clock at handler entry — used for the duration_ms field on
    // the unified-log record. Captured *before* the inner handler runs
    // so the buffered record reflects the full edge processing time
    // (cache lookup + KV ops + origin proxy round-trip), not just the
    // origin's view.
    const startMs = Date.now();
    let response: Response;
    let level: "info" | "warn" | "error" | "debug" | undefined;
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
