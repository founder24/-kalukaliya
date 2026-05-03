/**
 * Task #311 — alert on-call when the AI Gateway 24h embed cache-hit-rate
 * drops below the documented floor.
 *
 * Why this exists
 * ---------------
 * Tasks #306 / #307 routed every `env.AI.run(...)` call through the
 * `syrabit-ai-gw` AI Gateway specifically so that deterministic
 * embedding requests would be served from the gateway's response cache
 * instead of re-billing the $5,000 Cloudflare-for-Startups credit pool.
 * The embeddings route is overridden in the dashboard to a 24h TTL
 * (input → vector mapping is stable for at least that long) and the
 * activation runbook documents that we expect the embed hit-rate to
 * sit comfortably above ~50% in steady state.
 *
 * The catch is the same one Task #898 hit with the bot cache: the
 * signal only fires if a human happens to look at AI Gateway → Logs.
 * If a deploy invalidates the cache (e.g. a request-shape change
 * perturbs the cache key) or someone hand-edits the dashboard cache
 * policy and forgets to revert, the hit-rate can fall to 0% silently
 * and the next month's invoice picks up the cost.
 *
 * This module is the watchdog. It runs inside the same `syrabit-edge`
 * Worker cron as the synthetic / cf-block / bot-cache probes, queries
 * the Cloudflare GraphQL Analytics API for the last 24h of
 * `aiGatewayRequestsAdaptiveGroups` rows on our gateway filtered to
 * `metadata.tag = workers-ai-fallback:embed`, and pages on a single
 * floor signal:
 *
 *   - **Embed cache-hit-rate below floor** —
 *     `cached_requests / total_requests` over the last 24h is below
 *     `AI_GATEWAY_CACHE_HIT_RATE_FLOOR_PCT` (default 50%, matching
 *     the floor documented in `docs/ops/ai-gateway-activation.md`).
 *     A single page is sufficient: this is a slow-moving signal
 *     (24h window) and the on-call action is the rollback documented
 *     in that runbook, not a real-time mitigation.
 *
 * Sample-size guard
 * -----------------
 * A 0-volume window has no meaningful hit-rate. We require at least
 * `AI_GATEWAY_CACHE_ALERT_MIN_SAMPLE` total embed requests in the
 * 24h window (default 50) before evaluating the signal — otherwise
 * we'd page on every quiet 24h period (e.g. a regional outage that
 * stops embed traffic entirely).
 *
 * Skip-if no gateway
 * ------------------
 * If `WORKERS_AI_GATEWAY_ID` is unset on this worker, every
 * `env.AI.run(...)` call goes direct (no tagging, no caching) — there
 * are no embed-tagged rows to evaluate. Skipping in that case avoids
 * a misleading "0% hit rate" page when the operator simply hasn't run
 * Step 2 of the activation runbook yet.
 *
 * Cooldown
 * --------
 * After firing, the alert enters a 6-hour cooldown so a persistent
 * regression doesn't spam the channel every 15 minutes for a full
 * day. Six hours is short enough that an unacknowledged page during a
 * working day will repeat at least once, and long enough that on-call
 * isn't paged 96 times before they wake up.
 *
 * Cron cadence
 * ------------
 * The dispatcher in src/index.ts only invokes this watchdog on
 * minutes where `now.getMinutes() % 15 === 0` (i.e. 4 times per
 * hour). The 24h window moves slowly; running this every minute would
 * burn ~1,440 GraphQL calls/day for no signal benefit.
 *
 * Configuration (all on the worker via `wrangler secret put` / vars):
 *   - AI_GATEWAY_CACHE_ALERT_DISABLED         (var, "true" to skip)
 *   - AI_GATEWAY_CACHE_HIT_RATE_FLOOR_PCT     (var, default "50")
 *   - AI_GATEWAY_CACHE_ALERT_MIN_SAMPLE       (var, default "50")
 *   - AI_GATEWAY_CACHE_ALERT_EMBED_TAG        (var, default
 *       "workers-ai-fallback:embed" — matches aiGatewayOpts() in
 *       src/index.ts; override only if a future PR renames the tag)
 *   - WORKERS_AI_GATEWAY_ID                   (secret, set by Step 2
 *       of docs/ops/ai-gateway-activation.md)
 *   - AI_GATEWAY_ANALYTICS_TOKEN              (secret, CF API token
 *       with `AI Gateway: Read` scope on account
 *       d66e40eac539fff1db270fddf384a5ec — distinct from
 *       CF_ANALYTICS_TOKEN which is scoped to Workers Analytics)
 *   - SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL    (secret, shared with
 *       the synthetic / cf-block / bot-cache probes)
 *
 * Rollback runbook: docs/ops/ai-gateway-activation.md
 */

const ACCOUNT_ID = "d66e40eac539fff1db270fddf384a5ec";
const GQL_URL = "https://api.cloudflare.com/client/v4/graphql";
const ALERT_STATE_KEY = "ai_gateway_cache_alert:state";
const WEBHOOK_TIMEOUT_MS = 10_000;
const GQL_TIMEOUT_MS = 15_000;
const DEFAULT_FLOOR_PCT = 50;
const DEFAULT_MIN_SAMPLE = 50;
const DEFAULT_EMBED_TAG = "workers-ai-fallback:embed";
const COOLDOWN_MS = 6 * 60 * 60 * 1000;
const WINDOW_SECONDS = 24 * 60 * 60;
/** N consecutive `query_failed` evaluations that trip the secondary
 *  "watchdog itself is blind" alert. At one evaluation every 15 min,
 *  6 in a row ≈ 90 minutes of monitoring blindness — long enough to
 *  rule out a transient CF GraphQL hiccup, short enough that a token
 *  scope drift or schema change is surfaced inside the working day. */
const DEFAULT_QUERY_FAIL_THRESHOLD = 6;
/** Same 6h cooldown as the primary signal so a persistently broken
 *  token doesn't spam the channel for a full day. */
const QUERY_FAIL_COOLDOWN_MS = 6 * 60 * 60 * 1000;
/** Direct dashboard URL — included in the payload so on-call can
 *  one-click into the AI Gateway logs view filtered to our gateway. */
const DASHBOARD_URL =
  "https://dash.cloudflare.com/" +
  ACCOUNT_ID +
  "/ai/ai-gateway/syrabit-ai-gw/logs";

export interface AiGatewayCacheAlertEnv {
  RATE_LIMIT?: KVNamespace;
  AI_GATEWAY_CACHE_ALERT_DISABLED?: string;
  AI_GATEWAY_CACHE_HIT_RATE_FLOOR_PCT?: string;
  AI_GATEWAY_CACHE_ALERT_MIN_SAMPLE?: string;
  AI_GATEWAY_CACHE_ALERT_EMBED_TAG?: string;
  /** Override the consecutive-failure threshold for the secondary
   *  "watchdog blind" alert. Default 6 ≈ 90 min of monitoring
   *  blindness before paging. */
  AI_GATEWAY_CACHE_ALERT_QUERY_FAIL_THRESHOLD?: string;
  /** From Task #307; without it, embed calls don't go through the
   *  gateway at all and there's nothing to evaluate. */
  WORKERS_AI_GATEWAY_ID?: string;
  /** CF API token with `AI Gateway: Read` scope. Distinct from
   *  CF_ANALYTICS_TOKEN so the two scopes can be rotated
   *  independently. */
  AI_GATEWAY_ANALYTICS_TOKEN?: string;
  /** Reused from the synthetic probe so on-call sees one consistent
   *  delivery channel for "the edge layer is degraded". */
  SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL?: string;
}

export interface AiGatewayCacheAlertState {
  /** ISO timestamp of the last evaluation (success or skip). */
  last_evaluated_at: string | null;
  /** ISO timestamp the floor alert last fired (cooldown anchor). */
  floor_last_fired_at: string | null;
  /** Most recent computed embed cache-hit-rate over the 24h window. */
  last_hit_rate: number | null;
  /** Most recent total embed sample size (cached + uncached). */
  last_sample: number | null;
  /** Number of consecutive `query_failed` evaluations. Reset to 0 on
   *  any successful query. Drives the secondary "watchdog blind"
   *  alert. */
  consecutive_query_failures: number;
  /** ISO timestamp the "watchdog blind" alert last fired. */
  query_fail_last_fired_at: string | null;
}

export interface AiGatewayCacheAlertResult {
  ok: boolean;
  skipped: boolean;
  reason?: string;
  hit_rate: number | null;
  sample: number;
  cached_requests: number;
  uncached_requests: number;
  floor_pct: number;
  floor_alert_fired: boolean;
  /** Running count of consecutive query failures *after* this
   *  evaluation. Useful for tests and dashboard surfacing. */
  consecutive_query_failures: number;
  /** True if THIS evaluation tripped the "watchdog blind" alert
   *  (i.e. consecutive_query_failures crossed the threshold and
   *  cooldown was clear). */
  query_fail_alert_fired: boolean;
}

const EMPTY_STATE: AiGatewayCacheAlertState = {
  last_evaluated_at: null,
  floor_last_fired_at: null,
  last_hit_rate: null,
  last_sample: null,
  consecutive_query_failures: 0,
  query_fail_last_fired_at: null,
};

async function readState(kv: KVNamespace): Promise<AiGatewayCacheAlertState> {
  try {
    const raw = await kv.get(ALERT_STATE_KEY);
    if (!raw) return { ...EMPTY_STATE };
    const parsed = JSON.parse(raw) as Partial<AiGatewayCacheAlertState>;
    return { ...EMPTY_STATE, ...parsed };
  } catch {
    return { ...EMPTY_STATE };
  }
}

async function writeState(
  kv: KVNamespace,
  state: AiGatewayCacheAlertState,
): Promise<void> {
  try {
    await kv.put(ALERT_STATE_KEY, JSON.stringify(state), {
      expirationTtl: 7 * 24 * 3600,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "unknown";
    console.warn(`[ai-gateway-cache-alert] state write failed: ${msg.slice(0, 200)}`);
  }
}

function readNumberVar(raw: string | undefined, fallback: number, min = 0): number {
  if (!raw) return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, n);
}

interface GqlRow {
  count: number;
  dimensions: { cached: string };
}

interface GqlResponse {
  data?: {
    viewer: {
      accounts: {
        aiGatewayRequestsAdaptiveGroups: GqlRow[];
      }[];
    };
  };
  errors?: { message: string }[];
}

/**
 * Query the Cloudflare GraphQL Analytics API for the embed cache-hit
 * rate over the last 24 h. Returns `null` on transport / GraphQL error
 * (caller treats null as "skip with reason=query_failed", not "page
 * because rate is 0").
 */
async function queryEmbedHitRate(
  token: string,
  gatewayId: string,
  embedTag: string,
  now: Date,
): Promise<{ cached: number; uncached: number } | null> {
  const datetimeLeq = new Date(now.getTime()).toISOString().replace(/\.\d+Z$/, "Z");
  const datetimeGeq = new Date(now.getTime() - WINDOW_SECONDS * 1000)
    .toISOString()
    .replace(/\.\d+Z$/, "Z");

  // `aiGatewayRequestsAdaptiveGroups` is the Cloudflare-documented
  // dataset for AI Gateway analytics. We group on `cached` (boolean,
  // surfaced as a string dimension) and filter to the embed tag set
  // by `aiGatewayOpts(env, "workers-ai-fallback:embed")` in src/index.ts.
  const query = `
    query EmbedCacheHitRate(
      $accountTag: String!
      $gatewayId: String!
      $embedTag: String!
      $datetimeGeq: Time!
      $datetimeLeq: Time!
    ) {
      viewer {
        accounts(filter: { accountTag: $accountTag }) {
          aiGatewayRequestsAdaptiveGroups(
            filter: {
              gateway: $gatewayId
              metadataTag: $embedTag
              datetime_geq: $datetimeGeq
              datetime_leq: $datetimeLeq
            }
            limit: 10
          ) {
            count
            dimensions { cached }
          }
        }
      }
    }
  `;

  const variables = {
    accountTag: ACCOUNT_ID,
    gatewayId,
    embedTag,
    datetimeGeq,
    datetimeLeq,
  };

  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), GQL_TIMEOUT_MS);
    const res = await fetch(GQL_URL, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ query, variables }),
      signal: ctrl.signal,
    });
    clearTimeout(t);

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      console.warn(
        `[ai-gateway-cache-alert] GraphQL HTTP ${res.status}: ${text.slice(0, 200)}`,
      );
      return null;
    }

    const json = (await res.json()) as GqlResponse;
    if (json.errors?.length) {
      const msgs = json.errors.map((e) => e.message).join("; ");
      console.warn(`[ai-gateway-cache-alert] GraphQL error: ${msgs.slice(0, 300)}`);
      return null;
    }
    const rows =
      json.data?.viewer?.accounts?.[0]?.aiGatewayRequestsAdaptiveGroups ?? [];

    let cached = 0;
    let uncached = 0;
    for (const row of rows) {
      // The `cached` dimension comes back as a string per CF's GraphQL
      // schema; treat anything truthy/"true"/"1" as cached.
      const dim = String(row.dimensions?.cached ?? "").toLowerCase();
      const n = row.count ?? 0;
      if (dim === "true" || dim === "1") cached += n;
      else uncached += n;
    }
    return { cached, uncached };
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "unknown";
    console.warn(`[ai-gateway-cache-alert] GraphQL fetch failed: ${msg.slice(0, 200)}`);
    return null;
  }
}

async function fireWebhook(
  env: AiGatewayCacheAlertEnv,
  payload: Record<string, unknown>,
): Promise<boolean> {
  const webhook = env.SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL;
  if (!webhook) {
    console.error(
      "[ai-gateway-cache-alert] threshold reached but " +
      "SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL is not configured — " +
      "no page will be sent. Fix: " +
      "`wrangler secret put SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL` on " +
      "the syrabit-edge worker. Alert payload: " +
      JSON.stringify(payload).slice(0, 500),
    );
    return false;
  }
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), WEBHOOK_TIMEOUT_MS);
    const resp = await fetch(webhook, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: ctrl.signal,
    });
    clearTimeout(t);
    if (!resp.ok) {
      console.warn(
        `[ai-gateway-cache-alert] watchdog webhook returned ${resp.status} — ` +
        `alert may not have been delivered`,
      );
      return false;
    }
    return true;
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "unknown";
    console.warn(`[ai-gateway-cache-alert] watchdog webhook failed: ${msg.slice(0, 200)}`);
    return false;
  }
}

function pct(rate: number): string {
  return (rate * 100).toFixed(1) + "%";
}

/**
 * Run one iteration of the AI Gateway 24h embed cache-hit-rate
 * watchdog. Idempotent and safe to call from either a cron trigger or
 * an ad-hoc fetch handler.
 */
export async function runAiGatewayCacheAlert(
  env: AiGatewayCacheAlertEnv,
  now: Date = new Date(),
): Promise<AiGatewayCacheAlertResult> {
  const floorPct = readNumberVar(env.AI_GATEWAY_CACHE_HIT_RATE_FLOOR_PCT, DEFAULT_FLOOR_PCT, 0);
  const skipResult = (
    reason: string,
    extras: Partial<AiGatewayCacheAlertResult> = {},
  ): AiGatewayCacheAlertResult => ({
    ok: false,
    skipped: true,
    reason,
    hit_rate: null,
    sample: 0,
    cached_requests: 0,
    uncached_requests: 0,
    floor_pct: floorPct,
    floor_alert_fired: false,
    consecutive_query_failures: 0,
    query_fail_alert_fired: false,
    ...extras,
  });

  if ((env.AI_GATEWAY_CACHE_ALERT_DISABLED || "").toLowerCase() === "true") {
    return skipResult("disabled_by_var");
  }
  if (!env.RATE_LIMIT) {
    console.warn("[ai-gateway-cache-alert] RATE_LIMIT KV binding missing — skipping");
    return skipResult("no_kv_binding");
  }
  if (!env.WORKERS_AI_GATEWAY_ID) {
    // Activation Step 2 hasn't been run yet — there are no
    // gateway-tagged embed rows to evaluate. Skip silently.
    return skipResult("no_gateway_configured");
  }
  if (!env.AI_GATEWAY_ANALYTICS_TOKEN) {
    console.warn(
      "[ai-gateway-cache-alert] AI_GATEWAY_ANALYTICS_TOKEN is not set — " +
      "skipping. Provision a CF API token with `AI Gateway: Read` and " +
      "`wrangler secret put AI_GATEWAY_ANALYTICS_TOKEN --name syrabit-edge`.",
    );
    return skipResult("no_analytics_token");
  }

  const minSample = Math.floor(
    readNumberVar(env.AI_GATEWAY_CACHE_ALERT_MIN_SAMPLE, DEFAULT_MIN_SAMPLE, 0),
  );
  const queryFailThreshold = Math.floor(
    readNumberVar(
      env.AI_GATEWAY_CACHE_ALERT_QUERY_FAIL_THRESHOLD,
      DEFAULT_QUERY_FAIL_THRESHOLD,
      1,
    ),
  );
  const embedTag = env.AI_GATEWAY_CACHE_ALERT_EMBED_TAG || DEFAULT_EMBED_TAG;

  const stats = await queryEmbedHitRate(
    env.AI_GATEWAY_ANALYTICS_TOKEN,
    env.WORKERS_AI_GATEWAY_ID,
    embedTag,
    now,
  );

  const state = await readState(env.RATE_LIMIT);
  state.last_evaluated_at = now.toISOString();

  if (!stats) {
    // Increment the consecutive-failure counter and, if we've crossed
    // the threshold (default 6 ≈ 90 min of blindness), page on-call
    // about the watchdog itself. This closes the observability gap
    // the reviewer flagged: a silently-failing watchdog (e.g. token
    // scope dropped, GraphQL schema renamed) is itself a critical
    // signal because the primary floor alert can't fire while the
    // query is broken.
    state.consecutive_query_failures = (state.consecutive_query_failures || 0) + 1;
    let queryFailAlertFired = false;
    if (state.consecutive_query_failures >= queryFailThreshold) {
      const lastFiredMs = state.query_fail_last_fired_at
        ? Date.parse(state.query_fail_last_fired_at)
        : 0;
      if (!lastFiredMs || now.getTime() - lastFiredMs >= QUERY_FAIL_COOLDOWN_MS) {
        const minutesBlind = state.consecutive_query_failures * 15;
        const payload = {
          text:
            `:warning: *Syrabit AI Gateway cache-hit watchdog is blind* — ` +
            `${state.consecutive_query_failures} consecutive Cloudflare ` +
            `GraphQL queries for \`aiGatewayRequestsAdaptiveGroups\` have ` +
            `failed (~${minutesBlind} min of monitoring blindness). The ` +
            `primary embed cache-hit-rate floor alert cannot fire while ` +
            `this is broken, so a real cache regression would go ` +
            `unnoticed. Likely causes: (a) AI_GATEWAY_ANALYTICS_TOKEN ` +
            `was rotated and the new value is missing the ` +
            `\`AI Gateway: Read\` scope, (b) the token expired, or (c) ` +
            `Cloudflare renamed the analytics dataset / dimensions. ` +
            `Investigate: tail Worker logs for \`[ai-gateway-cache-alert]\` ` +
            `lines to see the underlying HTTP / GraphQL error, then ` +
            `\`wrangler secret put AI_GATEWAY_ANALYTICS_TOKEN --name ` +
            `syrabit-edge\`. Dashboard: ${DASHBOARD_URL}. ` +
            `Runbook: docs/ops/ai-gateway-activation.md.`,
          severity: "warning",
          alert_type: "ai_gateway_cache_watchdog_blind",
          gateway_id: env.WORKERS_AI_GATEWAY_ID,
          consecutive_failures: state.consecutive_query_failures,
          threshold: queryFailThreshold,
          minutes_blind: minutesBlind,
          dashboard_url: DASHBOARD_URL,
          runbook: "docs/ops/ai-gateway-activation.md",
        };
        queryFailAlertFired = await fireWebhook(env, payload);
        if (queryFailAlertFired) {
          state.query_fail_last_fired_at = now.toISOString();
        }
      }
    }
    await writeState(env.RATE_LIMIT, state);
    return skipResult("query_failed", {
      consecutive_query_failures: state.consecutive_query_failures,
      query_fail_alert_fired: queryFailAlertFired,
    });
  }

  // Successful query — reset the consecutive-failure counter so a
  // future spell of failures has to cross the threshold from scratch.
  state.consecutive_query_failures = 0;

  const total = stats.cached + stats.uncached;
  const hitRate = total > 0 ? stats.cached / total : 0;
  state.last_hit_rate = hitRate;
  state.last_sample = total;

  let floorAlertFired = false;

  // Sample-size guard — a 24h window with < MIN_SAMPLE embed requests
  // is too quiet to evaluate; the rate is dominated by noise from a
  // handful of requests. This naturally suppresses the page during
  // regional outages that stop embed traffic entirely.
  if (total >= minSample && hitRate * 100 < floorPct) {
    const lastFiredMs = state.floor_last_fired_at
      ? Date.parse(state.floor_last_fired_at)
      : 0;
    if (!lastFiredMs || now.getTime() - lastFiredMs >= COOLDOWN_MS) {
      const payload = {
        text:
          `:rotating_light: *Syrabit AI Gateway embed cache-hit-rate is below floor* — ` +
          `last 24h at ${pct(hitRate)} (${stats.cached}/${total} requests cached) ` +
          `vs documented floor of ${floorPct}%. Embedding requests are ` +
          `re-billing the $5k Cloudflare-for-Startups credit pool ` +
          `instead of being served from the gateway's response cache. ` +
          `Likely causes: (a) a deploy perturbed the request shape so ` +
          `cache keys no longer match, (b) someone hand-edited the ` +
          `dashboard cache TTL on the embed route override, or (c) the ` +
          `embed model was changed without re-warming the cache. ` +
          `Investigate: ${DASHBOARD_URL} ` +
          `(filter metadata.tag = \`${embedTag}\`). ` +
          `Rollback runbook: docs/ops/ai-gateway-activation.md.`,
        severity: "critical",
        alert_type: "ai_gateway_cache_hit_rate_low",
        gateway_id: env.WORKERS_AI_GATEWAY_ID,
        embed_tag: embedTag,
        window_hours: 24,
        hit_rate: hitRate,
        floor_pct: floorPct,
        sample: total,
        cached_requests: stats.cached,
        uncached_requests: stats.uncached,
        dashboard_url: DASHBOARD_URL,
        runbook: "docs/ops/ai-gateway-activation.md",
      };
      floorAlertFired = await fireWebhook(env, payload);
      if (floorAlertFired) state.floor_last_fired_at = now.toISOString();
    }
  }

  await writeState(env.RATE_LIMIT, state);

  console.log(
    `[ai-gateway-cache-alert] hit_rate=${hitRate.toFixed(3)} ` +
    `sample=${total} cached=${stats.cached} uncached=${stats.uncached} ` +
    `floor_pct=${floorPct} floor_alert_fired=${floorAlertFired}`,
  );

  return {
    ok: true,
    skipped: false,
    hit_rate: hitRate,
    sample: total,
    cached_requests: stats.cached,
    uncached_requests: stats.uncached,
    floor_pct: floorPct,
    floor_alert_fired: floorAlertFired,
    consecutive_query_failures: 0,
    query_fail_alert_fired: false,
  };
}

/** Test-only: read the persisted alert state. */
export async function _readAiGatewayCacheAlertStateForTests(
  kv: KVNamespace,
): Promise<AiGatewayCacheAlertState> {
  return readState(kv);
}

/** Test-only: KV key the alert state is stored under. */
export const _AI_GATEWAY_CACHE_ALERT_STATE_KEY = ALERT_STATE_KEY;

/** Test-only: defaults exposed so tests can assert the configured
 *  thresholds match the documentation in this file's header. */
export const _AI_GATEWAY_CACHE_ALERT_DEFAULTS = {
  FLOOR_PCT: DEFAULT_FLOOR_PCT,
  MIN_SAMPLE: DEFAULT_MIN_SAMPLE,
  EMBED_TAG: DEFAULT_EMBED_TAG,
  COOLDOWN_MS,
  WINDOW_SECONDS,
  QUERY_FAIL_THRESHOLD: DEFAULT_QUERY_FAIL_THRESHOLD,
  QUERY_FAIL_COOLDOWN_MS,
  DASHBOARD_URL,
} as const;
