/**
 * Task #575 — in-isolate cache of the backend's `/api/health/season`
 * response. The Cloudflare worker stretches per-route cache TTLs
 * during AHSEC + SEBA exam / results windows, but only the FastAPI
 * backend owns the calendar (so the season is consistent across
 * worker, API, and Lambda batch jobs). Polling the backend on every
 * request would defeat the cache it's enabling, so we keep a single
 * snapshot per isolate, refresh it lazily once per `TTL_MS`, and
 * fall back to "normal" on any error.
 *
 * Two-tier cache:
 *   1. **POP-level**: the upstream `fetch()` is sent with
 *      `cf: { cacheTtl: 60, cacheEverything: true }`, so Cloudflare's
 *      per-POP edge cache absorbs the fan-out — every isolate in the
 *      POP shares a single 60 s origin call. The backend response
 *      already carries `Cache-Control: public, max-age=60` so the
 *      contract is consistent on both sides.
 *   2. **Isolate-level**: `cached` shaves the per-isolate `fetch()`
 *      call (even a CF-cache HIT still costs ~ms) so `getCacheTtl()`
 *      stays a synchronous lookup at every callsite.
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
const TIMEOUT_MS = 3_000;

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

async function fetchSeason(backendUrl: string): Promise<SeasonSnapshot> {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${backendUrl}/api/health/season`, {
      method: "GET",
      headers: { "Accept": "application/json" },
      signal: ac.signal,
      // Use Cloudflare's per-POP edge cache as the SHARED 60 s cache
      // tier — every isolate in this POP collapses onto a single
      // origin request per minute. The backend already serves
      // `Cache-Control: public, max-age=60`, so the contract matches
      // on both sides. The isolate-level `cached` global below shaves
      // the remaining per-isolate fetch overhead.
      cf: { cacheTtl: 60, cacheEverything: true } as RequestInitCfProperties,
    });
    if (!res.ok) {
      throw new Error(`season fetch ${res.status}`);
    }
    const body = (await res.json()) as { season?: SeasonName; ttl_multiplier?: number };
    const season: SeasonName =
      body.season === "exam" || body.season === "results" || body.season === "normal"
        ? body.season
        : "normal";
    const ttl_multiplier =
      typeof body.ttl_multiplier === "number" && Number.isFinite(body.ttl_multiplier)
        ? body.ttl_multiplier
        : 1.0;
    return { season, ttl_multiplier, fetched_at_ms: Date.now() };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Returns the current season snapshot, refreshing in the background
 * when older than `TTL_MS`. Never throws — fetch failures preserve
 * the previous snapshot or fall back to `"normal"`.
 */
export async function getSeasonSnapshot(
  backendUrl: string,
  ctx: { waitUntil(promise: Promise<unknown>): void },
): Promise<SeasonSnapshot> {
  const now = Date.now();
  if (cached && now - cached.fetched_at_ms < TTL_MS) {
    return cached;
  }
  if (inflight) {
    return cached ?? FALLBACK;
  }
  inflight = fetchSeason(backendUrl)
    .then((snap) => {
      cached = snap;
      return snap;
    })
    .catch(() => cached ?? FALLBACK)
    .finally(() => {
      inflight = null;
    });
  // Cold-start contract: never block the request path on the
  // upstream fetch (would add up to TIMEOUT_MS of user-visible
  // latency). Serve the FALLBACK ("normal") snapshot immediately and
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
