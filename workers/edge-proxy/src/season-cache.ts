/**
 * Task #575 — isolate-level shadow of the season snapshot. The
 * authoritative cache lives in the `SeasonCacheDO` Durable Object
 * (`season-cache-do.ts`), which serves exactly one origin refresh
 * per 60 s per region. This module shaves the per-request DO RPC
 * by mirroring the snapshot in isolate-local globals, so
 * `getCacheTtl()` stays a synchronous lookup at every callsite.
 *
 * Three-tier cache:
 *   1. **Region-level (DO)**: `SeasonCacheDO` (single ID
 *      `idFromName("global")`) owns the authoritative snapshot and
 *      enforces the 60 s shared refresh contract. Refresh fan-out
 *      to the FastAPI origin is exactly one call per minute per
 *      region regardless of isolate count.
 *   2. **POP-level**: the DO's upstream `fetch()` carries
 *      `cf: { cacheTtl: 60, cacheEverything: true }` as belt-and-
 *      braces against the rare cross-POP cold-wake stampede. The
 *      backend response already carries
 *      `Cache-Control: public, max-age=60` so the contract is
 *      consistent on both sides.
 *   3. **Isolate-level**: `cached` shaves the per-isolate DO RPC so
 *      `getCacheTtl()` stays a synchronous lookup at every callsite.
 *
 * Failure mode: when the backend is unreachable we return the last
 * snapshot we successfully fetched (even if it's older than `TTL_MS`),
 * and only fall back to "normal" when we've never received a payload.
 * This way a multi-minute API blip during exam mode doesn't yank
 * every route's cache TTL back down mid-window.
 *
 * Cold-start contract: the very first request through a fresh
 * isolate MUST NOT block on the upstream fetch — that would add up to
 * `TIMEOUT_MS` (3 s) of user-visible latency on every cold-start
 * request. Instead we return the `FALLBACK` ("normal") snapshot
 * immediately and kick the refresh into `ctx.waitUntil`. The next
 * request through the same isolate (typically within a few ms) gets
 * the real snapshot. The worst case during a season transition is
 * therefore one request that sees the previous season's TTL — which
 * is identical to the worst case under the steady-state TTL window
 * boundary, so this isn't a new failure mode.
 *
 * Founder locks: this module never touches `/api/me/quota` (5 s edge
 * TTL — Task #513) or `/api/ai/chat` (edge bypass — Task #549). It
 * only stretches the existing per-route TTLs declared in
 * `monitored-urls.json`.
 */

export type SeasonName = "exam" | "results" | "normal";

export interface SeasonSnapshot {
  season: SeasonName;
  ttl_multiplier: number;
  fetched_at_ms: number;
}

const TTL_MS = 60_000;
const DO_RPC_TIMEOUT_MS = 1_000;

let cached: SeasonSnapshot | null = null;
let inflight: Promise<SeasonSnapshot> | null = null;

const FALLBACK: SeasonSnapshot = {
  season: "normal",
  ttl_multiplier: 1.0,
  fetched_at_ms: 0,
};

function isStretched(season: SeasonName): boolean {
  return season === "exam" || season === "results";
}

interface SeasonCacheEnv {
  /**
   * Optional so the worker still boots in local dev without the DO
   * migration applied. When unbound, every request gets the FALLBACK
   * snapshot ("normal"); season-aware TTL stretching is effectively a
   * no-op until the binding lands.
   */
  SEASON_CACHE_DO?: DurableObjectNamespace;
}

async function fetchSeasonFromDO(env: SeasonCacheEnv): Promise<SeasonSnapshot> {
  if (!env.SEASON_CACHE_DO) return FALLBACK;
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), DO_RPC_TIMEOUT_MS);
  try {
    const id = env.SEASON_CACHE_DO.idFromName("global");
    const stub = env.SEASON_CACHE_DO.get(id);
    const res = await stub.fetch("https://season-cache/snapshot", {
      method: "GET",
      signal: ac.signal,
    });
    if (!res.ok) throw new Error(`season DO ${res.status}`);
    const body = (await res.json()) as Partial<SeasonSnapshot>;
    const season: SeasonName =
      body.season === "exam" || body.season === "results" || body.season === "normal"
        ? body.season
        : "normal";
    const ttl_multiplier =
      typeof body.ttl_multiplier === "number" && Number.isFinite(body.ttl_multiplier)
        ? body.ttl_multiplier
        : 1.0;
    return {
      season,
      ttl_multiplier,
      fetched_at_ms:
        typeof body.fetched_at_ms === "number" ? body.fetched_at_ms : Date.now(),
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Returns the current season snapshot. Reads through the
 * `SeasonCacheDO` Durable Object (which owns the 60 s shared
 * refresh contract); the per-isolate `cached` global shaves the DO
 * RPC on every subsequent request inside `TTL_MS`. Never throws —
 * fetch failures preserve the previous snapshot, and a fresh
 * isolate without a snapshot yet returns `FALLBACK` ("normal")
 * immediately while refreshing in the background.
 */
export async function getSeasonSnapshot(
  env: SeasonCacheEnv,
  ctx: { waitUntil(promise: Promise<unknown>): void },
): Promise<SeasonSnapshot> {
  const now = Date.now();
  if (cached && now - cached.fetched_at_ms < TTL_MS) {
    return cached;
  }
  if (inflight) {
    return cached ?? FALLBACK;
  }
  inflight = fetchSeasonFromDO(env)
    .then((snap) => {
      cached = snap;
      return snap;
    })
    .catch(() => cached ?? FALLBACK)
    .finally(() => {
      inflight = null;
    });
  // Cold-start contract: never block the request path on the DO
  // RPC. Serve the FALLBACK ("normal") snapshot immediately and
  // refresh in the background — the next request through this
  // isolate picks up the real value. Warm path is identical: serve
  // the (slightly stale) cached snapshot, refresh via waitUntil.
  ctx.waitUntil(inflight.then(() => undefined).catch(() => undefined));
  return cached ?? FALLBACK;
}


/**
 * Pick the effective TTL for a path given the season snapshot.
 * `examTtlEntries` and `normalTtlEntries` are both sorted by
 * descending prefix length so the most specific entry wins.
 */
export function pickEffectiveTtl(
  pathname: string,
  snapshot: SeasonSnapshot,
  normalTtlEntries: ReadonlyArray<readonly [string, number]>,
  examTtlEntries: ReadonlyArray<readonly [string, number]>,
  defaultTtl: number,
): number {
  if (isStretched(snapshot.season)) {
    for (const [prefix, ttl] of examTtlEntries) {
      if (pathname.startsWith(prefix)) return ttl;
    }
  }
  for (const [prefix, ttl] of normalTtlEntries) {
    if (pathname.startsWith(prefix)) return ttl;
  }
  return defaultTtl;
}

/** Test-only reset — module-private cache is process-local. */
export function _resetForTests(): void {
  cached = null;
  inflight = null;
}
