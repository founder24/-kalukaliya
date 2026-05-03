/**
 * Task #314 — alert when the R2 cold-storage lifecycle rules silently
 * stop transitioning Standard → Infrequent Access, or when the
 * Logpush-driven R2 prefix overruns its 5GB cap.
 *
 * Why this exists
 * ---------------
 * `docs/cloudflare-r2-lifecycle.md` configures two rules per bucket:
 *   1. `assets-cold-to-ia-30d` / `media-cold-to-ia-30d` — objects
 *      untouched for 30d transition to Infrequent Access (materially
 *      cheaper per GB-month).
 *   2. `media-logpush-delete-14d` — the `logpush/` prefix is hard-
 *      capped to 14 days of retention so the dataset cannot grow
 *      unboundedly past the 5GB cap in `docs/cloudflare-cost-map.md`.
 *
 * Step 5 of `docs/cloudflare-monthly-cost-review.md` requires a human
 * reviewer to look at the Cloudflare invoice once a month and confirm
 * the IA share is non-zero and the Logpush prefix is under cap. If the
 * reviewer forgets — or, more likely, the rule got dropped during a
 * bucket rebuild and nobody noticed — the regression burns credits
 * for another month before the next manual check.
 *
 * This module is the programmatic backstop. It runs once per month
 * from the same `syrabit-edge` Worker cron as the other watchdogs,
 * queries the Cloudflare GraphQL Analytics API
 * (`r2StorageAdaptiveGroups`) for storage broken down by storage class
 * across `syrabit-assets` + `syrabit-media`, and lists the
 * `syrabit-media/logpush/` prefix for its total payload size.
 *
 * Two independent failure modes are alerted on:
 *
 *   1. **IA share is zero after rules have been live ≥30 days** —
 *      `infrequent_access_gb / total_gb` is zero across both buckets.
 *      The rules transition objects ≥30d old, so until the rules have
 *      been live for that long the metric is naturally zero (every
 *      object is ≤30d old) and the alert is suppressed. After the
 *      `R2_LIFECYCLE_RULES_APPLIED_AT` checkpoint clears 30 days, a
 *      0% IA share means the rules are not active — the operator
 *      needs to run `./infra/r2-lifecycle/apply.sh --verify` and
 *      re-apply if absent.
 *
 *   2. **Logpush prefix exceeds the cap** — the sum of
 *      `syrabit-media/logpush/*` object sizes is above
 *      `R2_STORAGE_ALERT_LOGPUSH_CAP_GB` (default 5 GB, matching
 *      `docs/cloudflare-cost-map.md`). The 14-day delete rule is the
 *      only thing keeping this bounded; if it is missing or the
 *      Logpush volume has increased past what 14 days can absorb,
 *      this alert fires before the next monthly review picks it up.
 *
 * The Logpush prefix size cannot be queried from
 * `r2StorageAdaptiveGroups` (no per-prefix dimension), so we walk the
 * `syrabit-media` R2 binding directly with `.list({ prefix: "logpush/" })`
 * and sum `size`. The 14-day cap keeps the object count bounded to a
 * few thousand at most, so paginated listing fits comfortably in the
 * Worker CPU/memory budget.
 *
 * Cron cadence
 * ------------
 * The dispatcher in src/index.ts only invokes this watchdog on the
 * first day of each calendar month at 00:00 UTC (one evaluation per
 * month). The cost-review checklist runs on the first business day,
 * so the alert is in inbox before the human reviewer sits down. A
 * second evaluation 28 days later would still hit the cooldown, so
 * the once-per-month gate is both sufficient and the cheapest way to
 * keep the GraphQL / R2-list call volume to ~12 invocations a year.
 *
 * Cooldown
 * --------
 * After a successful page, both alert families enter a 28-day
 * cooldown so a rule that stays broken across two monthly evaluations
 * doesn't double-page (the second would arrive while the first is
 * still being acted on). 28 days < 30 days so a real regression that
 * persists across two months still re-fires on the third monthly
 * evaluation.
 *
 * Configuration (all on the worker via `wrangler secret put` / vars):
 *   - R2_STORAGE_ALERT_DISABLED            (var, "true" to skip)
 *   - R2_LIFECYCLE_RULES_APPLIED_AT        (var, ISO date — when the
 *       rules were applied. Required for the IA-share signal; the
 *       alert is suppressed until ≥30 days have elapsed.)
 *   - R2_STORAGE_ALERT_LOGPUSH_CAP_GB      (var, default "5")
 *   - R2_STORAGE_ALERT_BUCKETS             (var, default
 *       "syrabit-assets,syrabit-media")
 *   - R2_STORAGE_ANALYTICS_TOKEN           (secret, CF API token with
 *       `Account Analytics: Read` scope. Distinct from
 *       CF_ANALYTICS_TOKEN / AI_GATEWAY_ANALYTICS_TOKEN so each can
 *       be rotated independently.)
 *   - R2_MEDIA                             (R2 binding for
 *       `syrabit-media`, declared in wrangler.toml. Required for the
 *       Logpush prefix signal.)
 *   - SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL (secret, shared with the
 *       other edge watchdogs.)
 *
 * Runbook: `docs/cloudflare-monthly-cost-review.md` Step 5 is the
 * primary diagnose-and-re-apply procedure (verify with
 * `./infra/r2-lifecycle/apply.sh --verify`, re-apply with
 * `./infra/r2-lifecycle/apply.sh`, ticket Cloudflare if rules are
 * present but not acting).
 */

const ACCOUNT_ID = "d66e40eac539fff1db270fddf384a5ec";
const GQL_URL = "https://api.cloudflare.com/client/v4/graphql";
const ALERT_STATE_KEY = "r2_storage_class_alert:state";
const WEBHOOK_TIMEOUT_MS = 10_000;
const GQL_TIMEOUT_MS = 15_000;
const DEFAULT_BUCKETS = ["syrabit-assets", "syrabit-media"];
const DEFAULT_LOGPUSH_CAP_GB = 5;
const LOGPUSH_PREFIX = "logpush/";
const LIFECYCLE_AGE_GRACE_DAYS = 30;
const COOLDOWN_MS = 28 * 24 * 60 * 60 * 1000;
const LIST_PAGE_LIMIT = 1000;
/** Hard ceiling on list pages so a runaway listing (e.g. the delete
 *  rule has been off for months and the prefix has tens of thousands
 *  of objects) cannot eat the worker's CPU budget. At 1000 entries per
 *  page that's 100k objects — well past the 5GB cap, which itself is
 *  the failure we are alerting on, so capping here just truncates the
 *  size estimate downward (still fires the alert, since 100k objects
 *  × any non-trivial size is far above 5GB). */
const LIST_PAGE_HARD_CAP = 100;
const BYTES_PER_GB = 1024 * 1024 * 1024;
/** N consecutive `query_failed` evaluations that trip the secondary
 *  "watchdog itself is blind" alert. The R2 watchdog only runs once per
 *  calendar month, so N=2 ≈ 60 days of monitoring blindness — long
 *  enough that a one-off CF GraphQL hiccup doesn't page (the next
 *  month's evaluation will reset the counter on success), short enough
 *  that a real token / schema regression is surfaced inside two billing
 *  cycles instead of running for a full year of silent skips. The
 *  primary R2 IA-share + Logpush-cap alerts cannot fire while the
 *  GraphQL query is broken, so this is the only line of defence
 *  against a silently-broken `R2_STORAGE_ANALYTICS_TOKEN` (e.g. rotated
 *  without re-granting `Account Analytics: Read`) or a Cloudflare-side
 *  rename of `r2StorageAdaptiveGroups`. Mirrors the
 *  `consecutive_query_failures` pattern from
 *  `ai-gateway-cache-alert.ts` (Task #311 secondary alert). */
const DEFAULT_QUERY_FAIL_THRESHOLD = 2;
/** 90-day cooldown on the watchdog-blind alert. Long enough that a
 *  persistently broken token across 3+ monthly evaluations doesn't
 *  re-page every month while the original ticket is still open, short
 *  enough that on-call is reminded once per quarter if the regression
 *  is left unfixed. */
const QUERY_FAIL_COOLDOWN_MS = 90 * 24 * 60 * 60 * 1000;
/** Direct dashboard URL — included in the payload so on-call can
 *  one-click into the R2 usage view. */
const DASHBOARD_URL =
  "https://dash.cloudflare.com/" + ACCOUNT_ID + "/r2/overview";

export interface R2StorageClassAlertEnv {
  RATE_LIMIT?: KVNamespace;
  /** R2 binding for `syrabit-media`. Declared in wrangler.toml.
   *  Required for the Logpush prefix size signal. */
  R2_MEDIA?: R2Bucket;
  R2_STORAGE_ALERT_DISABLED?: string;
  R2_LIFECYCLE_RULES_APPLIED_AT?: string;
  R2_STORAGE_ALERT_LOGPUSH_CAP_GB?: string;
  R2_STORAGE_ALERT_BUCKETS?: string;
  R2_STORAGE_ANALYTICS_TOKEN?: string;
  /** Override the consecutive-failure threshold for the secondary
   *  "watchdog blind" alert. Default 2 ≈ 60 days of monitoring
   *  blindness before paging (the watchdog runs monthly). */
  R2_STORAGE_ALERT_QUERY_FAIL_THRESHOLD?: string;
  /** Reused from the synthetic probe so on-call sees one consistent
   *  delivery channel for "the edge layer is degraded". */
  SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL?: string;
}

export interface R2StorageClassAlertState {
  /** ISO timestamp of the last evaluation (success or skip). */
  last_evaluated_at: string | null;
  /** ISO timestamp the IA-share alert last fired (cooldown anchor). */
  ia_share_last_fired_at: string | null;
  /** ISO timestamp the Logpush-cap alert last fired. */
  logpush_last_fired_at: string | null;
  /** Most recent computed IA share over the configured buckets (0..1). */
  last_ia_share: number | null;
  /** Most recent total R2 GB across the configured buckets. */
  last_total_gb: number | null;
  /** Most recent computed Logpush prefix size in GB. */
  last_logpush_gb: number | null;
  /** Number of consecutive `query_failed` evaluations. Reset to 0 on
   *  any successful GraphQL query. Drives the secondary "watchdog
   *  blind" alert (Task #316). */
  consecutive_query_failures: number;
  /** ISO timestamp the "watchdog blind" alert last fired. */
  query_fail_last_fired_at: string | null;
}

export interface R2StorageClassAlertResult {
  ok: boolean;
  skipped: boolean;
  reason?: string;
  ia_share: number | null;
  total_gb: number | null;
  standard_gb: number | null;
  infrequent_access_gb: number | null;
  logpush_gb: number | null;
  logpush_cap_gb: number;
  ia_alert_fired: boolean;
  logpush_alert_fired: boolean;
  /** Days since `R2_LIFECYCLE_RULES_APPLIED_AT`. Null if the var
   *  is unset / unparseable. The IA-share signal is suppressed
   *  until this is ≥ 30. */
  rules_age_days: number | null;
  /** Running count of consecutive query failures *after* this
   *  evaluation. 0 on any successful GraphQL query. Useful for tests
   *  and dashboard surfacing (Task #316). */
  consecutive_query_failures: number;
  /** True if THIS evaluation tripped the "watchdog blind" alert
   *  (i.e. consecutive_query_failures crossed the threshold and
   *  cooldown was clear). */
  query_fail_alert_fired: boolean;
}

const EMPTY_STATE: R2StorageClassAlertState = {
  last_evaluated_at: null,
  ia_share_last_fired_at: null,
  logpush_last_fired_at: null,
  last_ia_share: null,
  last_total_gb: null,
  last_logpush_gb: null,
  consecutive_query_failures: 0,
  query_fail_last_fired_at: null,
};

async function readState(kv: KVNamespace): Promise<R2StorageClassAlertState> {
  try {
    const raw = await kv.get(ALERT_STATE_KEY);
    if (!raw) return { ...EMPTY_STATE };
    const parsed = JSON.parse(raw) as Partial<R2StorageClassAlertState>;
    return { ...EMPTY_STATE, ...parsed };
  } catch {
    return { ...EMPTY_STATE };
  }
}

async function writeState(
  kv: KVNamespace,
  state: R2StorageClassAlertState,
): Promise<void> {
  try {
    await kv.put(ALERT_STATE_KEY, JSON.stringify(state), {
      expirationTtl: 60 * 24 * 3600,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "unknown";
    console.warn(`[r2-storage-class-alert] state write failed: ${msg.slice(0, 200)}`);
  }
}

function readNumberVar(raw: string | undefined, fallback: number, min = 0): number {
  if (!raw) return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, n);
}

function readBucketsVar(raw: string | undefined): string[] {
  if (!raw) return [...DEFAULT_BUCKETS];
  const parts = raw.split(",").map((s) => s.trim()).filter(Boolean);
  return parts.length > 0 ? parts : [...DEFAULT_BUCKETS];
}

function rulesAgeDays(raw: string | undefined, now: Date): number | null {
  if (!raw) return null;
  const t = Date.parse(raw);
  if (!Number.isFinite(t)) return null;
  const ms = now.getTime() - t;
  if (ms < 0) return 0;
  return Math.floor(ms / (24 * 60 * 60 * 1000));
}

interface GqlRow {
  max: { payloadSize: number };
  dimensions: { bucketName: string; storageClass: string };
}

interface GqlResponse {
  data?: {
    viewer: {
      accounts: {
        r2StorageAdaptiveGroups: GqlRow[];
      }[];
    };
  };
  errors?: { message: string }[];
}

interface BucketStorage {
  bucket: string;
  standard_bytes: number;
  ia_bytes: number;
}

/**
 * Query the Cloudflare GraphQL Analytics API for the most recent R2
 * storage row per (bucket, storage class) over the last 24 h. Returns
 * `null` on transport / GraphQL error so the caller can skip with
 * `reason=query_failed` instead of paging on a phantom 0% IA share.
 */
async function queryR2Storage(
  token: string,
  buckets: string[],
  now: Date,
): Promise<BucketStorage[] | null> {
  const datetimeLeq = new Date(now.getTime()).toISOString().replace(/\.\d+Z$/, "Z");
  const datetimeGeq = new Date(now.getTime() - 24 * 60 * 60 * 1000)
    .toISOString()
    .replace(/\.\d+Z$/, "Z");

  // `r2StorageAdaptiveGroups` is the Cloudflare-documented dataset for
  // R2 storage analytics. We group on `bucketName` + `storageClass`
  // and take `max(payloadSize)` so a window with multiple sample
  // points returns the high-water mark for that 24h period (the
  // billed value).
  const query = `
    query R2StorageByClass(
      $accountTag: String!
      $bucketNames: [String!]
      $datetimeGeq: Time!
      $datetimeLeq: Time!
    ) {
      viewer {
        accounts(filter: { accountTag: $accountTag }) {
          r2StorageAdaptiveGroups(
            filter: {
              bucketName_in: $bucketNames
              datetime_geq: $datetimeGeq
              datetime_leq: $datetimeLeq
            }
            limit: 100
          ) {
            max { payloadSize }
            dimensions { bucketName storageClass }
          }
        }
      }
    }
  `;

  const variables = {
    accountTag: ACCOUNT_ID,
    bucketNames: buckets,
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
        `[r2-storage-class-alert] GraphQL HTTP ${res.status}: ${text.slice(0, 200)}`,
      );
      return null;
    }

    const json = (await res.json()) as GqlResponse;
    if (json.errors?.length) {
      const msgs = json.errors.map((e) => e.message).join("; ");
      console.warn(`[r2-storage-class-alert] GraphQL error: ${msgs.slice(0, 300)}`);
      return null;
    }
    const rows =
      json.data?.viewer?.accounts?.[0]?.r2StorageAdaptiveGroups ?? [];

    const byBucket = new Map<string, BucketStorage>();
    for (const b of buckets) {
      byBucket.set(b, { bucket: b, standard_bytes: 0, ia_bytes: 0 });
    }
    for (const row of rows) {
      const bucket = row.dimensions?.bucketName ?? "";
      const cls = (row.dimensions?.storageClass ?? "").toLowerCase();
      const size = Number(row.max?.payloadSize ?? 0);
      const entry = byBucket.get(bucket);
      if (!entry || !Number.isFinite(size)) continue;
      // Cloudflare returns the storage class as either "Standard" /
      // "InfrequentAccess" or the lowercase "standard" / "infrequent_access".
      // Treat anything that mentions "infrequent" as IA; everything else
      // (including the empty-string sentinel that some pre-rule rows
      // return) as Standard.
      if (cls.includes("infrequent")) {
        entry.ia_bytes = Math.max(entry.ia_bytes, size);
      } else {
        entry.standard_bytes = Math.max(entry.standard_bytes, size);
      }
    }
    return Array.from(byBucket.values());
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "unknown";
    console.warn(`[r2-storage-class-alert] GraphQL fetch failed: ${msg.slice(0, 200)}`);
    return null;
  }
}

/**
 * Sum the size of every object under the `logpush/` prefix in the
 * `syrabit-media` bucket. Returns `null` on listing error (treated
 * the same as a GraphQL failure: skip with reason, do not page on a
 * phantom 0). Capped at `LIST_PAGE_HARD_CAP` pages — past that we
 * return the partial size, which is still well above the 5GB cap if
 * the prefix has actually run away.
 */
async function sumLogpushPrefixBytes(bucket: R2Bucket): Promise<number | null> {
  let total = 0;
  let cursor: string | undefined;
  let pages = 0;
  try {
    do {
      const list: R2Objects = await bucket.list({
        prefix: LOGPUSH_PREFIX,
        limit: LIST_PAGE_LIMIT,
        cursor,
      });
      for (const obj of list.objects) {
        total += obj.size ?? 0;
      }
      pages += 1;
      cursor = list.truncated ? list.cursor : undefined;
      if (pages >= LIST_PAGE_HARD_CAP) break;
    } while (cursor);
    return total;
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "unknown";
    console.warn(`[r2-storage-class-alert] R2 list failed: ${msg.slice(0, 200)}`);
    return null;
  }
}

async function fireWebhook(
  env: R2StorageClassAlertEnv,
  payload: Record<string, unknown>,
): Promise<boolean> {
  const webhook = env.SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL;
  if (!webhook) {
    console.error(
      "[r2-storage-class-alert] threshold reached but " +
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
        `[r2-storage-class-alert] watchdog webhook returned ${resp.status} — ` +
        `alert may not have been delivered`,
      );
      return false;
    }
    return true;
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "unknown";
    console.warn(`[r2-storage-class-alert] watchdog webhook failed: ${msg.slice(0, 200)}`);
    return false;
  }
}

function bytesToGb(bytes: number): number {
  return bytes / BYTES_PER_GB;
}

function pct(rate: number): string {
  return (rate * 100).toFixed(1) + "%";
}

/**
 * Run one iteration of the R2 cold-storage / Logpush-cap watchdog.
 * Idempotent and safe to call from either a cron trigger or an ad-hoc
 * fetch handler.
 */
export async function runR2StorageClassAlert(
  env: R2StorageClassAlertEnv,
  now: Date = new Date(),
): Promise<R2StorageClassAlertResult> {
  const logpushCapGb = readNumberVar(
    env.R2_STORAGE_ALERT_LOGPUSH_CAP_GB,
    DEFAULT_LOGPUSH_CAP_GB,
    0,
  );
  const skipResult = (
    reason: string,
    extras: Partial<R2StorageClassAlertResult> = {},
  ): R2StorageClassAlertResult => ({
    ok: false,
    skipped: true,
    reason,
    ia_share: null,
    total_gb: null,
    standard_gb: null,
    infrequent_access_gb: null,
    logpush_gb: null,
    logpush_cap_gb: logpushCapGb,
    ia_alert_fired: false,
    logpush_alert_fired: false,
    rules_age_days: rulesAgeDays(env.R2_LIFECYCLE_RULES_APPLIED_AT, now),
    consecutive_query_failures: 0,
    query_fail_alert_fired: false,
    ...extras,
  });

  if ((env.R2_STORAGE_ALERT_DISABLED || "").toLowerCase() === "true") {
    return skipResult("disabled_by_var");
  }
  if (!env.RATE_LIMIT) {
    console.warn("[r2-storage-class-alert] RATE_LIMIT KV binding missing — skipping");
    return skipResult("no_kv_binding");
  }
  if (!env.R2_STORAGE_ANALYTICS_TOKEN) {
    console.warn(
      "[r2-storage-class-alert] R2_STORAGE_ANALYTICS_TOKEN is not set — " +
      "skipping. Provision a CF API token with `Account Analytics: Read` " +
      "and `wrangler secret put R2_STORAGE_ANALYTICS_TOKEN --name syrabit-edge`.",
    );
    return skipResult("no_analytics_token");
  }

  const buckets = readBucketsVar(env.R2_STORAGE_ALERT_BUCKETS);
  const ageDays = rulesAgeDays(env.R2_LIFECYCLE_RULES_APPLIED_AT, now);
  const queryFailThreshold = Math.floor(
    readNumberVar(
      env.R2_STORAGE_ALERT_QUERY_FAIL_THRESHOLD,
      DEFAULT_QUERY_FAIL_THRESHOLD,
      1,
    ),
  );

  const storage = await queryR2Storage(
    env.R2_STORAGE_ANALYTICS_TOKEN,
    buckets,
    now,
  );

  const state = await readState(env.RATE_LIMIT);
  state.last_evaluated_at = now.toISOString();

  let iaAlertFired = false;
  let logpushAlertFired = false;
  let queryFailAlertFired = false;

  // Maintain the consecutive-failure counter that drives the secondary
  // "watchdog itself is blind" alert (Task #316). Resetting on success
  // and incrementing on failure mirrors the Task #311
  // ai-gateway-cache-alert pattern. Note we update the counter here —
  // BEFORE evaluating the IA / Logpush signals — so the success path's
  // return value reflects the post-evaluation state (= 0 after a
  // successful query) without an extra branch later.
  if (storage) {
    state.consecutive_query_failures = 0;
  } else {
    state.consecutive_query_failures =
      (state.consecutive_query_failures || 0) + 1;
    if (state.consecutive_query_failures >= queryFailThreshold) {
      const lastFiredMs = state.query_fail_last_fired_at
        ? Date.parse(state.query_fail_last_fired_at)
        : 0;
      if (!lastFiredMs || now.getTime() - lastFiredMs >= QUERY_FAIL_COOLDOWN_MS) {
        const monthsBlind = state.consecutive_query_failures;
        const payload = {
          text:
            `:warning: *Syrabit R2 cold-storage watchdog is blind* — ` +
            `${state.consecutive_query_failures} consecutive monthly ` +
            `Cloudflare GraphQL queries for \`r2StorageAdaptiveGroups\` ` +
            `have failed (~${monthsBlind} month${monthsBlind === 1 ? "" : "s"} ` +
            `of monitoring blindness). The primary IA-share + Logpush-cap ` +
            `alerts cannot fire while this is broken, so a real cold-storage ` +
            `regression would burn credits unnoticed across multiple billing ` +
            `cycles. Likely causes: (a) R2_STORAGE_ANALYTICS_TOKEN was ` +
            `rotated and the new value is missing the ` +
            `\`Account Analytics: Read\` scope, (b) the token expired, or ` +
            `(c) Cloudflare renamed the analytics dataset / dimensions. ` +
            `Investigate: tail Worker logs for \`[r2-storage-class-alert]\` ` +
            `lines to see the underlying HTTP / GraphQL error, then ` +
            `\`wrangler secret put R2_STORAGE_ANALYTICS_TOKEN --name ` +
            `syrabit-edge\`. Dashboard: ${DASHBOARD_URL}. ` +
            `Runbook: docs/cloudflare-monthly-cost-review.md#step-5.`,
          severity: "warning",
          alert_type: "r2_storage_watchdog_blind",
          consecutive_failures: state.consecutive_query_failures,
          threshold: queryFailThreshold,
          months_blind: monthsBlind,
          buckets,
          dashboard_url: DASHBOARD_URL,
          runbook: "docs/cloudflare-monthly-cost-review.md#step-5",
        };
        queryFailAlertFired = await fireWebhook(env, payload);
        if (queryFailAlertFired) {
          state.query_fail_last_fired_at = now.toISOString();
        }
      }
    }
  }

  let totalBytes = 0;
  let standardBytes = 0;
  let iaBytes = 0;
  let iaShare: number | null = null;

  if (storage) {
    for (const s of storage) {
      standardBytes += s.standard_bytes;
      iaBytes += s.ia_bytes;
    }
    totalBytes = standardBytes + iaBytes;
    iaShare = totalBytes > 0 ? iaBytes / totalBytes : 0;
    state.last_ia_share = iaShare;
    state.last_total_gb = bytesToGb(totalBytes);

    // ── IA-share signal ──────────────────────────────────────────────
    // Only evaluate after the rules have been live for ≥30 days; the
    // transition is "objects ≥30d old", so until then a 0% share is
    // expected and pageworthy alerts would be false positives.
    // Also require a non-trivial total (≥1GB across both buckets) so
    // a fresh deployment with a few KB of test objects doesn't trip
    // the signal before the buckets carry meaningful steady-state
    // volume.
    if (
      ageDays !== null &&
      ageDays >= LIFECYCLE_AGE_GRACE_DAYS &&
      totalBytes >= BYTES_PER_GB &&
      iaShare === 0
    ) {
      const lastFiredMs = state.ia_share_last_fired_at
        ? Date.parse(state.ia_share_last_fired_at)
        : 0;
      if (!lastFiredMs || now.getTime() - lastFiredMs >= COOLDOWN_MS) {
        const payload = {
          text:
            `:rotating_light: *Syrabit R2 cold-storage rules look stuck* — ` +
            `0% of R2 storage across ${buckets.join(" + ")} is in ` +
            `Infrequent Access despite the lifecycle rules having been ` +
            `live for ${ageDays} days (≥${LIFECYCLE_AGE_GRACE_DAYS} day ` +
            `transition window). Total R2: ${bytesToGb(totalBytes).toFixed(2)} GB ` +
            `Standard / 0 GB IA. ` +
            `This is the Task #314 backstop for the manual Step 5 of ` +
            `\`docs/cloudflare-monthly-cost-review.md\` — usually means ` +
            `the rules \`assets-cold-to-ia-30d\` / \`media-cold-to-ia-30d\` ` +
            `have been dropped during a bucket rebuild or never re-applied. ` +
            `Diagnose: \`./infra/r2-lifecycle/apply.sh --verify\` (re-apply ` +
            `with \`./infra/r2-lifecycle/apply.sh\` if absent). If the ` +
            `rules ARE present and enabled, ticket Cloudflare referencing ` +
            `the rule IDs — the platform is not acting on them. ` +
            `Dashboard: ${DASHBOARD_URL}.`,
          severity: "critical",
          alert_type: "r2_ia_share_zero",
          buckets,
          ia_share: iaShare,
          total_gb: bytesToGb(totalBytes),
          standard_gb: bytesToGb(standardBytes),
          infrequent_access_gb: bytesToGb(iaBytes),
          rules_age_days: ageDays,
          dashboard_url: DASHBOARD_URL,
          runbook: "docs/cloudflare-monthly-cost-review.md#step-5",
        };
        iaAlertFired = await fireWebhook(env, payload);
        if (iaAlertFired) state.ia_share_last_fired_at = now.toISOString();
      }
    }
  }

  // ── Logpush-cap signal ─────────────────────────────────────────────
  // Independent of the IA-share signal: even if the GraphQL query
  // fails we still want to walk the prefix and page if it has run
  // away. Skipped if R2_MEDIA isn't bound (local dev / before bucket
  // provisioning).
  let logpushBytes: number | null = null;
  if (env.R2_MEDIA) {
    logpushBytes = await sumLogpushPrefixBytes(env.R2_MEDIA);
    if (logpushBytes !== null) {
      state.last_logpush_gb = bytesToGb(logpushBytes);
      const logpushGb = bytesToGb(logpushBytes);
      if (logpushGb > logpushCapGb) {
        const lastFiredMs = state.logpush_last_fired_at
          ? Date.parse(state.logpush_last_fired_at)
          : 0;
        if (!lastFiredMs || now.getTime() - lastFiredMs >= COOLDOWN_MS) {
          const payload = {
            text:
              `:warning: *Syrabit R2 Logpush prefix is over cap* — ` +
              `\`syrabit-media/logpush/\` is at ${logpushGb.toFixed(2)} GB ` +
              `vs cap of ${logpushCapGb} GB ` +
              `(\`docs/cloudflare-cost-map.md\`, Logpush row). The 14-day ` +
              `delete rule \`media-logpush-delete-14d\` is the only thing ` +
              `keeping this bounded; if it has been dropped, the prefix ` +
              `will keep growing. ` +
              `Diagnose: \`./infra/r2-lifecycle/apply.sh --verify\` and ` +
              `confirm the rule is present + enabled. If absent, re-apply ` +
              `with \`./infra/r2-lifecycle/apply.sh\`. If present and the ` +
              `prefix is still over cap, Logpush volume has likely ` +
              `outgrown what 14d of retention can absorb — either lower ` +
              `the Logpush sample rate or shorten the retention window. ` +
              `Dashboard: ${DASHBOARD_URL}.`,
            severity: "warning",
            alert_type: "r2_logpush_storage_high",
            bucket: "syrabit-media",
            prefix: LOGPUSH_PREFIX,
            logpush_gb: logpushGb,
            logpush_cap_gb: logpushCapGb,
            dashboard_url: DASHBOARD_URL,
            runbook: "docs/cloudflare-monthly-cost-review.md#step-5",
          };
          logpushAlertFired = await fireWebhook(env, payload);
          if (logpushAlertFired) state.logpush_last_fired_at = now.toISOString();
        }
      }
    }
  }

  await writeState(env.RATE_LIMIT, state);

  if (!storage) {
    console.log(
      `[r2-storage-class-alert] query_failed (logpush_gb=${logpushBytes !== null ? bytesToGb(logpushBytes).toFixed(3) : "n/a"} ` +
      `logpush_alert_fired=${logpushAlertFired} ` +
      `consecutive_query_failures=${state.consecutive_query_failures} ` +
      `query_fail_alert_fired=${queryFailAlertFired})`,
    );
    return {
      ok: false,
      skipped: true,
      reason: "query_failed",
      ia_share: null,
      total_gb: null,
      standard_gb: null,
      infrequent_access_gb: null,
      logpush_gb: logpushBytes !== null ? bytesToGb(logpushBytes) : null,
      logpush_cap_gb: logpushCapGb,
      ia_alert_fired: false,
      logpush_alert_fired: logpushAlertFired,
      rules_age_days: ageDays,
      consecutive_query_failures: state.consecutive_query_failures,
      query_fail_alert_fired: queryFailAlertFired,
    };
  }

  console.log(
    `[r2-storage-class-alert] ia_share=${(iaShare ?? 0).toFixed(3)} ` +
    `total_gb=${bytesToGb(totalBytes).toFixed(3)} ` +
    `standard_gb=${bytesToGb(standardBytes).toFixed(3)} ` +
    `ia_gb=${bytesToGb(iaBytes).toFixed(3)} ` +
    `logpush_gb=${logpushBytes !== null ? bytesToGb(logpushBytes).toFixed(3) : "n/a"} ` +
    `rules_age_days=${ageDays ?? "unset"} ` +
    `ia_alert_fired=${iaAlertFired} logpush_alert_fired=${logpushAlertFired}`,
  );

  return {
    ok: true,
    skipped: false,
    ia_share: iaShare,
    total_gb: bytesToGb(totalBytes),
    standard_gb: bytesToGb(standardBytes),
    infrequent_access_gb: bytesToGb(iaBytes),
    logpush_gb: logpushBytes !== null ? bytesToGb(logpushBytes) : null,
    logpush_cap_gb: logpushCapGb,
    ia_alert_fired: iaAlertFired,
    logpush_alert_fired: logpushAlertFired,
    rules_age_days: ageDays,
    consecutive_query_failures: 0,
    query_fail_alert_fired: false,
  };
}

/**
 * Cron-gating helper: returns true on the first day of each calendar
 * month at 00:00 UTC. The dispatcher in src/index.ts uses this to
 * invoke `runR2StorageClassAlert` exactly once per month. Exported so
 * tests can verify the gate explicitly without rebuilding the cron
 * dispatcher.
 */
export function shouldRunMonthlyR2Check(now: Date): boolean {
  return (
    now.getUTCDate() === 1 &&
    now.getUTCHours() === 0 &&
    now.getUTCMinutes() === 0
  );
}

/**
 * Read the persisted alert state for the admin dashboard tile (Task
 * #315). Returns the empty-state shape (all `null`) when nothing has
 * been written yet, so the UI can render a "never evaluated" placeholder
 * instead of crashing on a missing key.
 */
export async function readR2StorageClassAlertState(
  kv: KVNamespace,
): Promise<R2StorageClassAlertState> {
  return readState(kv);
}

/** Test-only: read the persisted alert state. */
export async function _readR2StorageClassAlertStateForTests(
  kv: KVNamespace,
): Promise<R2StorageClassAlertState> {
  return readState(kv);
}

/** Test-only: KV key the alert state is stored under. */
export const _R2_STORAGE_CLASS_ALERT_STATE_KEY = ALERT_STATE_KEY;

/** Test-only: defaults exposed so tests can assert the configured
 *  thresholds match the documentation in this file's header. */
export const _R2_STORAGE_CLASS_ALERT_DEFAULTS = {
  BUCKETS: DEFAULT_BUCKETS,
  LOGPUSH_CAP_GB: DEFAULT_LOGPUSH_CAP_GB,
  LOGPUSH_PREFIX,
  LIFECYCLE_AGE_GRACE_DAYS,
  COOLDOWN_MS,
  DASHBOARD_URL,
  BYTES_PER_GB,
  QUERY_FAIL_THRESHOLD: DEFAULT_QUERY_FAIL_THRESHOLD,
  QUERY_FAIL_COOLDOWN_MS,
} as const;
