/**
 * Task #13 — alert when a newly-live SPA route accumulates significant
 * bot traffic without a matching title-injection pattern.
 *
 * Why this exists
 * ---------------
 * Task #9 records every bot request that falls through _resolveSpaRouteMeta
 * as a "spa_title_miss" datapoint in the Analytics Engine. The coverage
 * signal is therefore already captured — but it only surfaces when an admin
 * manually checks the dashboard tile. A route that goes live at 23:00 IST
 * can rack up hundreds of bot hits overnight with the generic SPA <title>
 * before anyone notices.
 *
 * This module runs once per day (cron "0 1 * * *") and queries the
 * Analytics Engine for the rolling 24-hour window. Any path whose miss
 * count equals or exceeds the configured threshold AND that still has
 * no matching _resolveSpaRouteMeta pattern fires a single webhook alert
 * listing all gaps together — one consolidated payload so the channel
 * doesn't get a separate message per path.
 *
 * Each alert payload includes the path, hit count, and a suggested title
 * string produced by _slugToTitle so the engineer on call can copy-paste
 * a starting point into _resolveSpaRouteMeta without context-switching
 * to the dashboard.
 *
 * Cooldown
 * --------
 * State is stored in RATE_LIMIT KV under "spa_title_miss_alert:state".
 * After firing, the alert enters a 23-hour cooldown so the same set of
 * gaps doesn't page every day until someone fixes them. The cooldown
 * resets when a new path crosses the threshold for the first time.
 *
 * Configuration (set via `wrangler secret put` / dashboard vars):
 *   - SPA_TITLE_MISS_ALERT_DISABLED  (var, "true" to pause entirely)
 *   - SPA_TITLE_MISS_ALERT_THRESHOLD (var, integer, default "50")
 *   - CF_ANALYTICS_TOKEN             (secret, Analytics: Read scope)
 *   - SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL (secret, shared with probes)
 *   - RATE_LIMIT                     (KV binding, shared with probes)
 *
 * Dependencies injected by the caller (to avoid circular imports from
 * index.ts where these functions live):
 *   - resolveMeta  — _resolveSpaRouteMeta from index.ts
 *   - slugToTitle  — _slugToTitle from index.ts
 */

import { querySpaTitleMisses, type SpaTitleMiss } from "./analytics-engine";

// ─── constants ────────────────────────────────────────────────────────────────

const ALERT_STATE_KEY  = "spa_title_miss_alert:state";
const WEBHOOK_TIMEOUT_MS = 10_000;
const DEFAULT_THRESHOLD  = 50;
/** 23 hours — slightly less than 24 so the daily 01:00 UTC cron
 *  doesn't accidentally skip a day due to scheduling jitter. */
const COOLDOWN_MS = 23 * 60 * 60 * 1_000;

// ─── types ────────────────────────────────────────────────────────────────────

export interface SpaTitleMissAlertEnv {
  /** RATE_LIMIT KV for state persistence (shared with bot-cache-alert). */
  RATE_LIMIT?: KVNamespace;
  /** Cloudflare Analytics Engine read token. */
  CF_ANALYTICS_TOKEN?: string;
  /** Set to "true" to disable the alert entirely. */
  SPA_TITLE_MISS_ALERT_DISABLED?: string;
  /** Integer string — paths with count >= this value trigger an alert.
   *  Default: 50 */
  SPA_TITLE_MISS_ALERT_THRESHOLD?: string;
  /** Slack-compatible / PagerDuty Events v2 webhook (shared with probes). */
  SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL?: string;
}

/** Caller-supplied helpers to avoid a circular import on index.ts. */
export interface SpaTitleMissAlertDeps {
  resolveMeta: (pathname: string) => { title: string } | null;
  slugToTitle: (slug: string) => string;
}

interface AlertState {
  /** ISO timestamp of the last alert (for cooldown). */
  last_fired_at: string | null;
  /** Paths that were reported in the last alert (for change detection). */
  last_paths: string[];
}

/** Shape returned by runSpaTitleMissAlert for tests and logging. */
export interface SpaTitleMissAlertResult {
  ok: boolean;
  skipped: boolean;
  reason?: string;
  /** Number of uncovered paths found in the 24h window (any count). */
  gaps_found: number;
  /** Number of paths whose count was >= threshold. */
  gaps_above_threshold: number;
  alert_fired: boolean;
}

// ─── helpers ──────────────────────────────────────────────────────────────────

const EMPTY_STATE: AlertState = {
  last_fired_at: null,
  last_paths:    [],
};

async function readState(kv: KVNamespace): Promise<AlertState> {
  try {
    const raw = await kv.get(ALERT_STATE_KEY);
    if (!raw) return { ...EMPTY_STATE };
    const parsed = JSON.parse(raw) as Partial<AlertState>;
    return {
      last_fired_at: parsed.last_fired_at ?? null,
      last_paths:    Array.isArray(parsed.last_paths) ? parsed.last_paths : [],
    };
  } catch {
    return { ...EMPTY_STATE };
  }
}

async function writeState(kv: KVNamespace, state: AlertState): Promise<void> {
  try {
    // 48-hour TTL — state is only needed for cooldown decisions.
    await kv.put(ALERT_STATE_KEY, JSON.stringify(state), {
      expirationTtl: 48 * 3600,
    });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "unknown";
    console.warn(`[spa-title-miss-alert] state write failed: ${msg.slice(0, 200)}`);
  }
}

function threshold(env: SpaTitleMissAlertEnv): number {
  const raw = env.SPA_TITLE_MISS_ALERT_THRESHOLD;
  if (!raw) return DEFAULT_THRESHOLD;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < 1) return DEFAULT_THRESHOLD;
  return Math.floor(n);
}

async function fireWebhook(
  env: SpaTitleMissAlertEnv,
  gaps: Array<SpaTitleMiss & { suggestedTitle: string }>,
): Promise<boolean> {
  const webhook = env.SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL;
  if (!webhook) {
    console.error(
      "[spa-title-miss-alert] PAGING-DARK: title-miss gap threshold reached " +
      `(${gaps.length} path(s)) but SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL is ` +
      "not configured — no page will be sent. Fix: " +
      "`wrangler secret put SYNTHETIC_PROBE_WATCHDOG_WEBHOOK_URL`",
    );
    return false;
  }

  const lines = gaps.map(
    (g) =>
      `• \`${g.pathname}\` — ${g.count} bot hits/24h ` +
      `(suggested title: "${g.suggestedTitle}")`,
  );

  const payload = {
    text:
      `:eyes: *Syrabit SPA title-injection gap alert* — ` +
      `${gaps.length} uncovered path${gaps.length === 1 ? "" : "s"} ` +
      `exceeded the bot-hit threshold in the last 24 h:\n` +
      lines.join("\n") +
      `\n\nAdd these patterns to \`_resolveSpaRouteMeta\` in ` +
      `\`workers/edge-proxy/src/index.ts\` to stop serving the generic ` +
      `SPA \`<title>\` to crawlers.`,
    severity: "warning",
    alert_type: "spa_title_miss_gap",
    gap_count: gaps.length,
    gaps: gaps.map((g) => ({
      pathname:       g.pathname,
      count:          g.count,
      suggested_title: g.suggestedTitle,
    })),
  };

  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), WEBHOOK_TIMEOUT_MS);
    const resp = await fetch(webhook, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload),
      signal:  ctrl.signal,
    });
    clearTimeout(timer);
    if (!resp.ok) {
      console.warn(
        `[spa-title-miss-alert] webhook returned ${resp.status} — ` +
        "alert may not have been delivered",
      );
      return false;
    }
    return true;
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "unknown";
    console.warn(`[spa-title-miss-alert] webhook fetch failed: ${msg.slice(0, 200)}`);
    return false;
  }
}

// ─── public API ───────────────────────────────────────────────────────────────

/**
 * Run the daily SPA title-miss gap alert.
 *
 * Safe to call from a cron handler or an ad-hoc fetch handler (e.g. tests).
 * All side effects (KV reads/writes, webhook POST) are idempotent within
 * the 23-hour cooldown window.
 *
 * @param env   Worker env (RATE_LIMIT, CF_ANALYTICS_TOKEN, webhook URL, …)
 * @param deps  Injected helpers from index.ts (_resolveSpaRouteMeta,
 *              _slugToTitle) — passed to avoid circular imports.
 * @param now   Optional override for "current time" (test seam).
 */
export async function runSpaTitleMissAlert(
  env: SpaTitleMissAlertEnv,
  deps: SpaTitleMissAlertDeps,
  now: Date = new Date(),
): Promise<SpaTitleMissAlertResult> {
  // Kill-switch.
  if (env.SPA_TITLE_MISS_ALERT_DISABLED?.toLowerCase() === "true") {
    return {
      ok: true, skipped: true, reason: "SPA_TITLE_MISS_ALERT_DISABLED=true",
      gaps_found: 0, gaps_above_threshold: 0, alert_fired: false,
    };
  }

  // CF_ANALYTICS_TOKEN is required to query the Analytics Engine.
  if (!env.CF_ANALYTICS_TOKEN) {
    console.warn(
      "[spa-title-miss-alert] CF_ANALYTICS_TOKEN not set — skipping. " +
      "Run: `wrangler secret put CF_ANALYTICS_TOKEN`",
    );
    return {
      ok: true, skipped: true, reason: "CF_ANALYTICS_TOKEN not configured",
      gaps_found: 0, gaps_above_threshold: 0, alert_fired: false,
    };
  }

  // RATE_LIMIT KV is needed for state/cooldown; fail gracefully if missing.
  if (!env.RATE_LIMIT) {
    console.warn(
      "[spa-title-miss-alert] RATE_LIMIT KV not bound — cannot persist " +
      "cooldown state. Skipping.",
    );
    return {
      ok: true, skipped: true, reason: "RATE_LIMIT KV not bound",
      gaps_found: 0, gaps_above_threshold: 0, alert_fired: false,
    };
  }

  const kv    = env.RATE_LIMIT;
  const state = await readState(kv);
  const thr   = threshold(env);

  // ── cooldown check ─────────────────────────────────────────────────────────
  if (state.last_fired_at) {
    const lastMs = new Date(state.last_fired_at).getTime();
    if (now.getTime() - lastMs < COOLDOWN_MS) {
      return {
        ok: true, skipped: true,
        reason: `cooldown active since ${state.last_fired_at}`,
        gaps_found: 0, gaps_above_threshold: 0, alert_fired: false,
      };
    }
  }

  // ── query Analytics Engine ─────────────────────────────────────────────────
  let misses: SpaTitleMiss[];
  try {
    misses = await querySpaTitleMisses(env.CF_ANALYTICS_TOKEN, "24h");
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : "unknown";
    console.error(`[spa-title-miss-alert] Analytics Engine query failed: ${msg.slice(0, 300)}`);
    return {
      ok: false, skipped: false, reason: `query failed: ${msg.slice(0, 200)}`,
      gaps_found: 0, gaps_above_threshold: 0, alert_fired: false,
    };
  }

  // ── classify: uncovered and above threshold ────────────────────────────────
  const uncovered = misses.filter((m) => deps.resolveMeta(m.pathname) === null);
  const gapsAbove = uncovered.filter((m) => m.count >= thr);

  if (gapsAbove.length === 0) {
    // Nothing to page about — write state to record the evaluation.
    await writeState(kv, { ...state, last_paths: [] });
    return {
      ok: true, skipped: false,
      gaps_found: uncovered.length, gaps_above_threshold: 0,
      alert_fired: false,
    };
  }

  // ── build enriched payload ─────────────────────────────────────────────────
  const enriched = gapsAbove.map((m) => {
    // Derive a suggested title from the last meaningful path segment.
    // e.g. "/ahsec/hs-2nd-year/physics" → "Physics"
    const parts = m.pathname.replace(/\/$/, "").split("/").filter(Boolean);
    const lastPart = parts[parts.length - 1] ?? m.pathname;
    return { ...m, suggestedTitle: deps.slugToTitle(lastPart) };
  });

  // ── fire webhook ───────────────────────────────────────────────────────────
  const fired = await fireWebhook(env, enriched);

  if (fired) {
    await writeState(kv, {
      last_fired_at: now.toISOString(),
      last_paths:    gapsAbove.map((m) => m.pathname),
    });
  }

  return {
    ok: true, skipped: false,
    gaps_found: uncovered.length,
    gaps_above_threshold: gapsAbove.length,
    alert_fired: fired,
  };
}
