/**
 * Task #887 — typed view of `monitored-urls.json`, the canonical registry
 * of every backend URL/path the worker (or its companion deploy infra)
 * hard-codes outside the FastAPI app.
 *
 * Why this exists
 * ---------------
 * Task #877 was a 56-hour outage caused by `synthetic-probe.ts` hard-coding
 * `/admin/diagnostics` (no `/api` prefix) while the FastAPI router was
 * mounted under `prefix="/api"`. Every probe 404'd, the watchdog stayed
 * dark, and nobody noticed until someone scrolled production logs.
 *
 * The fix surface (Task #887) is the JSON manifest at
 * `workers/edge-proxy/monitored-urls.json` plus the pytest drift check
 * at `artifacts/syrabit-backend/tests/test_monitoring_url_drift.py` that
 * iterates the manifest and asserts every backend path resolves to a
 * real route in the live FastAPI OpenAPI schema. This module is the
 * runtime side: probes import the canonical strings from here so that
 * the value the test validates is *literally the same value* the worker
 * sends — no transcription drift between the manifest and the code.
 *
 * Task #900 extended the manifest to also cover the edge-cache config.
 * The `CACHEABLE_PREFIXES` / `CACHE_TTL` / `BYPASS_PREFIXES` /
 * `USER_SPECIFIC_PREFIXES` constants in `index.ts` are derived from this
 * module via the `getCacheable*` / `getBypass*` helpers below — so a
 * developer who renames a FastAPI route (e.g. `/api/content/boards` →
 * `/api/v2/content/boards`) gets a CI failure pointing at
 * `monitored-urls.json` instead of a silent cache miss in production.
 *
 * If you add a new probe / healthcheck / hard-coded backend URL anywhere
 * in this worker, register it in `monitored-urls.json` and reference it
 * from this module (or via `getBackendPath()` below). The drift test
 * gates merges; you cannot merge a hard-coded URL that is not in the
 * manifest if it ships through this module.
 */

import manifest from "../monitored-urls.json";

export interface MonitoredEdgeCache {
  /** "cacheable" → store in CF cache. "bypass" → must never be cached. */
  behavior: "cacheable" | "bypass";
  /** TTL in seconds when behavior === "cacheable". Required for cacheable entries. */
  ttl_seconds?: number;
  /** When true, the cache key includes a per-user identity header so each user gets their own entry. */
  user_keyed?: boolean;
  /**
   * Task #575 — optional stretched TTL applied while the backend's
   * `cache_calendar.current_season()` is `"exam"` or `"results"`. The
   * worker polls `/api/health/season` (60 s in-DO cache) and uses
   * this value in place of `ttl_seconds` for the duration of the
   * window. Routes that don't declare it keep `ttl_seconds`
   * unchanged regardless of season — so a route opt-out is just
   * "leave the field off".
   */
  exam_ttl_seconds?: number;
  /** Advisory hit-ratio target surfaced by `/api/health/cache`. */
  cache_hit_ratio_target?: number;
}

/**
 * HTTP verbs the worker is allowed to declare on a manifest entry.
 * Task #916 — the drift test asserts the live OpenAPI path item exposes
 * this verb, catching the Task #877 sibling failure mode where a probe
 * GETs a POST-only endpoint and 405s forever.
 */
export type MonitoredHttpMethod = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface MonitoredBackendPath {
  path: string;
  match: "exact" | "prefix";
  rationale: string;
  registered_in: string[];
  /**
   * Optional name of the exported identifier (constant or helper function)
   * that the `registered_in` files use to reach this path. Set this when
   * the file consumes the path through an import rather than inlining the
   * literal string — the Task #901 drift test will then accept the
   * identifier as a stand-in for the literal path.
   */
  runtime_constant?: string;
  /**
   * HTTP verb the worker actually sends when hitting this path. Defaults
   * to "GET" when omitted. Task #916 — the drift test verifies the live
   * OpenAPI path item actually exposes this method:
   *   * `match: "exact"` → the exact path must support this method.
   *   * `match: "prefix"` → at least one path under the prefix must
   *     support this method (a prefix is a route family, not a single
   *     endpoint, so a strict per-path check would be meaningless).
   * Annotate any entry whose worker-side caller is NOT a GET — e.g.
   * `/api/webhooks` (POST), `/api/ai/chat` (POST).
   */
  method?: MonitoredHttpMethod;
  /**
   * Task #917 — when present, the in-process probe-smoke test (which
   * boots the FastAPI app under TestClient and asserts every
   * `backend_paths` entry returns < 500) skips this entry, surfacing
   * the reason in pytest -v output. Use ONLY when the 5xx is a known
   * test-stub artifact (e.g. the handler awaits a motor cursor pattern
   * `tests/_deps_stub.py` does not yet model) — never to paper over
   * a real production 5xx.
   */
  smoke_skip_reason?: string;
  edge_cache?: MonitoredEdgeCache;
}

export interface MonitoredExternalUrl {
  url: string;
  rationale: string;
  registered_in: string[];
  /** See `MonitoredBackendPath.runtime_constant`. */
  runtime_constant?: string;
  /** See `MonitoredBackendPath.method`. Defaults to "GET" when omitted. */
  method?: MonitoredHttpMethod;
}

export interface MonitoredUrlsManifest {
  $schema_version: number;
  backend_paths: MonitoredBackendPath[];
  intentionally_external: MonitoredExternalUrl[];
}

const MANIFEST = manifest as unknown as MonitoredUrlsManifest;

export const MONITORED_URLS: MonitoredUrlsManifest = MANIFEST;

/**
 * Look up a backend path by its canonical string. Throws if the path is
 * not registered in the manifest — this turns "I forgot to update the
 * manifest" into a startup error instead of a silent 404 in production.
 *
 * Use this from probe modules so the value they fetch and the value the
 * drift test validates are the same Object reference.
 */
export function getBackendPath(path: string): MonitoredBackendPath {
  const found = MANIFEST.backend_paths.find((p) => p.path === path);
  if (!found) {
    throw new Error(
      `[monitored-urls] backend path ${JSON.stringify(path)} is not registered in ` +
      `workers/edge-proxy/monitored-urls.json. Add it (with a one-line rationale) ` +
      `before hard-coding it in the worker — the Task #887 drift check requires it.`,
    );
  }
  return found;
}

/**
 * Look up an intentionally-external URL by its canonical string. Throws
 * if the URL is not registered. Same rationale as `getBackendPath`.
 */
export function getExternalUrl(url: string): MonitoredExternalUrl {
  const found = MANIFEST.intentionally_external.find((u) => u.url === url);
  if (!found) {
    throw new Error(
      `[monitored-urls] external URL ${JSON.stringify(url)} is not registered in ` +
      `workers/edge-proxy/monitored-urls.json. Add it (with a one-line rationale ` +
      `explaining why it is NOT a FastAPI route) before hard-coding it in the ` +
      `worker — the Task #887 drift check requires it.`,
    );
  }
  return found;
}

// ─── Convenience re-exports for the well-known monitored paths ─────────
//
// Probe modules import these constants directly so the worker and the
// drift test cannot disagree about the canonical string. Add a constant
// here whenever you register a new entry in `monitored-urls.json`.

/** Synthetic-probe target — see `synthetic-probe.ts`. */
export const SYNTHETIC_PROBE_PATH = getBackendPath("/api/admin/diagnostics").path;

/** cf-block-probe target — see `cf-block-probe.ts`. */
export const CF_BLOCK_PROBE_DEFAULT_URL = getExternalUrl("https://syrabit.ai/").url;


// ─── Edge-cache config (Task #900) ─────────────────────────────────────
//
// `index.ts` used to inline three sibling constants — `CACHEABLE_PREFIXES`,
// `CACHE_TTL`, `BYPASS_PREFIXES` — that decided whether the worker
// stored a response in the CF cache and for how long. Each constant was
// a hand-maintained list of `/api/...` strings, and a renamed FastAPI
// route silently stopped matching with no probe to catch it (the same
// failure mode as Task #877). Task #900 moves the source of truth into
// `monitored-urls.json`'s `edge_cache` block; the helpers below project
// the manifest into the shape `index.ts` consumes at runtime.
//
// Eagerly memoised at module load — the manifest is a literal JSON
// import, so this work happens exactly once per Worker instance.

const CACHEABLE_ENTRIES: readonly MonitoredBackendPath[] = MANIFEST.backend_paths.filter(
  (entry) => entry.edge_cache?.behavior === "cacheable",
);

const BYPASS_ENTRIES: readonly MonitoredBackendPath[] = MANIFEST.backend_paths.filter(
  (entry) => entry.edge_cache?.behavior === "bypass",
);

// Validate cacheable entries carry a TTL. Throwing at module load is the
// loudest place to surface a malformed manifest — a deployed worker that
// silently skipped TTL entries would re-introduce the exact silent-miss
// class of bug this module exists to prevent.
for (const entry of CACHEABLE_ENTRIES) {
  if (typeof entry.edge_cache?.ttl_seconds !== "number") {
    throw new Error(
      `[monitored-urls] backend path ${JSON.stringify(entry.path)} has ` +
      `edge_cache.behavior="cacheable" but no numeric ttl_seconds. ` +
      `Set ttl_seconds in monitored-urls.json — see Task #900 docstring.`,
    );
  }
}

const CACHEABLE_PREFIXES_FROZEN: readonly string[] = Object.freeze(
  CACHEABLE_ENTRIES.map((entry) => entry.path),
);

const BYPASS_PREFIXES_FROZEN: readonly string[] = Object.freeze(
  BYPASS_ENTRIES.map((entry) => entry.path),
);

const USER_SPECIFIC_PREFIXES_FROZEN: readonly string[] = Object.freeze(
  CACHEABLE_ENTRIES.filter((entry) => entry.edge_cache?.user_keyed === true).map(
    (entry) => entry.path,
  ),
);

// TTL list sorted by descending key length so that the most specific
// prefix wins the lookup. e.g. /api/seo/keyword-index (24 chars) must
// resolve before /api/seo/ (9 chars) for a request to
// /api/seo/keyword-index/foo. Matches the order-sensitive behaviour of
// the previous `Object.entries(CACHE_TTL)` iteration in `index.ts`.
const CACHE_TTL_ENTRIES_SORTED: ReadonlyArray<readonly [string, number]> = Object.freeze(
  CACHEABLE_ENTRIES
    .map((entry) => [entry.path, entry.edge_cache!.ttl_seconds!] as const)
    .sort((a, b) => b[0].length - a[0].length),
);

// Task #575 — parallel array sorted the same way that holds
// `(path, exam_ttl_seconds)` for the routes that opted in. The worker
// consults this list while in exam / results mode and falls back to
// the normal CACHE_TTL_ENTRIES_SORTED entry (or DEFAULT_CACHE_TTL_SECONDS)
// for routes without the field.
const EXAM_CACHE_TTL_ENTRIES_SORTED: ReadonlyArray<readonly [string, number]> = Object.freeze(
  CACHEABLE_ENTRIES
    .filter((entry) => typeof entry.edge_cache?.exam_ttl_seconds === "number")
    .map((entry) => [entry.path, entry.edge_cache!.exam_ttl_seconds!] as const)
    .sort((a, b) => b[0].length - a[0].length),
);

/** Default cache TTL for paths that match `isCacheable` but have no explicit TTL entry. */
export const DEFAULT_CACHE_TTL_SECONDS = 300;

/** Edge-cacheable path prefixes (used by `isCacheable` in `index.ts`). */
export function getCacheablePrefixes(): readonly string[] {
  return CACHEABLE_PREFIXES_FROZEN;
}

/** Path prefixes the edge MUST NOT cache (used by `isBypass` in `index.ts`). */
export function getBypassPrefixes(): readonly string[] {
  return BYPASS_PREFIXES_FROZEN;
}

/** Cacheable prefixes whose cache key includes a per-user identity header. */
export function getUserSpecificPrefixes(): readonly string[] {
  return USER_SPECIFIC_PREFIXES_FROZEN;
}

/**
 * Cache TTL entries, sorted by descending key length. Iterate in order
 * and return the first prefix that `pathname.startsWith(prefix)` — the
 * sort guarantees the most specific entry wins.
 */
export function getCacheTtlEntries(): ReadonlyArray<readonly [string, number]> {
  return CACHE_TTL_ENTRIES_SORTED;
}

/**
 * Task #575 — exam-season TTL overrides. Same iteration contract as
 * `getCacheTtlEntries` (descending prefix-length sort) but only
 * contains routes that declare `edge_cache.exam_ttl_seconds`. The
 * worker checks this list first while in exam / results mode and
 * falls through to `getCacheTtlEntries` for unmatched routes.
 */
export function getExamCacheTtlEntries(): ReadonlyArray<readonly [string, number]> {
  return EXAM_CACHE_TTL_ENTRIES_SORTED;
}
