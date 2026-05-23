// Task #535: shared backend bundle + traffic fetcher.
//
// All four prerender scripts (prerender-library, prerender-chat,
// prerender-routes, prerender-static-routes) need the same library
// bundle and (in the case of prerender-routes) the traffic ranking.
// Before this module they each issued their own fetch, which on a
// slow / cold Railway backend doubled or tripled the build time.
//
// This module fetches once and caches the JSON on disk under
// node_modules/.cache/prerender/ so a second invocation in the same
// build is a synchronous file read. Cache is process-global too — if
// the orchestrator (scripts/build.mjs) calls warmCache() before
// spawning the per-page scripts, the on-disk file is the only network
// hop the build pipeline pays.
//
// Soft-fails: if the backend is unreachable, the helper returns null
// and callers should fall back to whatever they did before (SPA shell,
// bundle-order ranking, etc). The build never hard-fails on a
// transient network blip.

import crypto from "crypto";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoCacheDir = path.resolve(
  __dirname,
  "..",
  "node_modules",
  ".cache",
  "prerender",
);

export const BACKEND = (
  process.env.PRERENDER_BACKEND_URL ||
  process.env.VITE_BACKEND_URL ||
  "https://syrabit.ai"
).replace(/\/$/, "");

export const FETCH_TIMEOUT_MS = (() => {
  const raw = process.env.PRERENDER_FETCH_TIMEOUT_MS;
  const n = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(n) && n >= 500 && n <= 60_000 ? n : 3000;
})();

// Cache TTL — anything younger than this is reused without re-fetching.
// Default 10 minutes covers a single build comfortably; old entries
// are ignored automatically so cross-build staleness is bounded.
const CACHE_TTL_MS = 10 * 60 * 1000;

// Task #537: Bumpable schema version. Increment whenever the prerender
// scripts start depending on a new field in the backend response (or
// stop depending on an old one). Bumping this string invalidates every
// on-disk cache entry on the next build, even if Cloudflare's build
// cache restored a stale file from a previous build within the TTL.
const CACHE_SCHEMA_VERSION = "1";

// Source-hash fingerprint: hash this module + every prerender-*.mjs
// script alongside it. When a developer edits a prerender script to
// consume a newly-added bundle field, the source hash flips and the
// cache invalidates automatically — no manual SCHEMA_VERSION bump
// required for the common case. Computed lazily and memoised.
let cachedSourceHash = null;
function computeSourceHash() {
  if (cachedSourceHash !== null) return cachedSourceHash;
  try {
    const entries = fs
      .readdirSync(__dirname)
      .filter(
        (f) =>
          (f.startsWith("prerender-") || f === "_prerender-data.mjs") &&
          f.endsWith(".mjs"),
      )
      .sort();
    const hash = crypto.createHash("sha256");
    for (const f of entries) {
      hash.update(f);
      hash.update("\0");
      hash.update(fs.readFileSync(path.join(__dirname, f)));
      hash.update("\0");
    }
    cachedSourceHash = hash.digest("hex").slice(0, 16);
  } catch {
    cachedSourceHash = "nohash";
  }
  return cachedSourceHash;
}

// The full cache fingerprint mixes schema version, backend host (so
// pointing PRERENDER_BACKEND_URL at staging never reuses prod data)
// and the source hash above. Recorded in every cache file and also
// baked into the filename so stale files never collide with current
// ones in the on-disk cache directory.
let cachedFingerprint = null;
function cacheFingerprint() {
  if (cachedFingerprint) return cachedFingerprint;
  cachedFingerprint = crypto
    .createHash("sha256")
    .update(`${CACHE_SCHEMA_VERSION}::${BACKEND}::${computeSourceHash()}`)
    .digest("hex")
    .slice(0, 12);
  return cachedFingerprint;
}

function ensureCacheDir() {
  try {
    fs.mkdirSync(repoCacheDir, { recursive: true });
  } catch {
    // ignore — we'll fall back to network only.
  }
}

function cachePath(name) {
  // Filename includes the fingerprint so a fingerprint change leaves
  // the old file orphaned (ignored on read, swept on next clearCache).
  return path.join(repoCacheDir, `${name}.${cacheFingerprint()}.json`);
}

function readCacheEnvelope(name) {
  try {
    const file = cachePath(name);
    const stat = fs.statSync(file);
    if (Date.now() - stat.mtimeMs > CACHE_TTL_MS) return null;
    const parsed = JSON.parse(fs.readFileSync(file, "utf-8"));
    // Defence-in-depth: also validate the fingerprint embedded in the
    // file body. If anything (build cache restore, manual copy, etc.)
    // delivered a file whose stored fingerprint doesn't match the
    // current build, treat it as a miss so we re-fetch.
    if (!parsed || typeof parsed !== "object") return null;
    if (parsed.fingerprint !== cacheFingerprint()) return null;
    if (parsed.schemaVersion !== CACHE_SCHEMA_VERSION) return null;
    if (parsed.backend !== BACKEND) return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeCache(name, value, extras = {}) {
  ensureCacheDir();
  try {
    const payload = {
      schemaVersion: CACHE_SCHEMA_VERSION,
      backend: BACKEND,
      fingerprint: cacheFingerprint(),
      backendSignal: extras.backendSignal ?? null,
      payloadSchemaVersion:
        value && typeof value === "object" && "schemaVersion" in value
          ? value.schemaVersion
          : null,
      contentFingerprint: contentFingerprint(value),
      fetchedAt: new Date().toISOString(),
      data: value,
    };
    fs.writeFileSync(cachePath(name), JSON.stringify(payload));
  } catch {
    // best-effort
  }
}

// Recursive structural shape of a payload: just the sorted key set
// plus type tags. Two payloads with the same shape produce the same
// fingerprint regardless of values; a backend that adds, removes, or
// retypes a field flips it. Stored alongside the cache so a stale
// payload restored by the build cache can be detected post-fetch and
// reported even when the backend doesn't expose an ETag.
function contentShape(value, depth = 0) {
  if (value === null || value === undefined) return "n";
  if (depth > 4) return "*"; // bound recursion on deep payloads
  if (Array.isArray(value)) {
    if (value.length === 0) return "[]";
    return "[" + contentShape(value[0], depth + 1) + "]";
  }
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    return (
      "{" +
      keys.map((k) => k + ":" + contentShape(value[k], depth + 1)).join(",") +
      "}"
    );
  }
  return typeof value;
}

function contentFingerprint(value) {
  return crypto
    .createHash("sha256")
    .update(contentShape(value))
    .digest("hex")
    .slice(0, 16);
}

// Backend-driven schema signal. A cheap HEAD on the data URL gives us
// whatever the backend is willing to expose: an ETag, a Last-Modified
// stamp, or a custom X-Schema-Version header. The cache stores the
// signal observed at write time; on read we re-probe and refuse to
// reuse the cache if the backend now reports a different signal —
// which is exactly the cross-build schema-change scenario the build
// cache TTL alone cannot catch. If the backend doesn't expose any of
// these headers (or the probe fails), the result is null and we fall
// back to the embedded fingerprint + content-shape defence-in-depth.
const SIGNAL_TTL_MS = 60_000;
const signalCache = new Map();
async function backendSchemaSignal(url) {
  const now = Date.now();
  const cached = signalCache.get(url);
  if (cached && now - cached.ts < SIGNAL_TTL_MS) return cached.signal;
  const ctrl = new AbortController();
  const timer = setTimeout(
    () => ctrl.abort(),
    Math.min(2000, FETCH_TIMEOUT_MS),
  );
  try {
    const res = await fetch(url, { method: "HEAD", signal: ctrl.signal, headers: buildFetchHeaders() });
    if (!res.ok) {
      signalCache.set(url, { ts: now, signal: null });
      return null;
    }
    const sig =
      res.headers.get("x-schema-version") ||
      res.headers.get("etag") ||
      res.headers.get("last-modified") ||
      null;
    signalCache.set(url, { ts: now, signal: sig });
    return sig;
  } catch {
    signalCache.set(url, { ts: now, signal: null });
    return null;
  } finally {
    clearTimeout(timer);
  }
}

// Task #543: retry-with-backoff for transient HTTP failures, with
// honour for the Retry-After header on 429s. Configurable via env so
// CI can tune for known-slow backends without code changes.
const RETRY_MAX_ATTEMPTS = (() => {
  const n = Number.parseInt(process.env.PRERENDER_FETCH_RETRIES || "", 10);
  return Number.isFinite(n) && n >= 1 && n <= 10 ? n : 4;
})();
const RETRY_BASE_DELAY_MS = (() => {
  const n = Number.parseInt(process.env.PRERENDER_FETCH_RETRY_BASE_MS || "", 10);
  return Number.isFinite(n) && n >= 100 && n <= 30_000 ? n : 750;
})();
const RETRY_MAX_DELAY_MS = 30_000;

function parseRetryAfter(value) {
  if (!value) return null;
  const n = Number(value);
  if (Number.isFinite(n) && n >= 0) return Math.min(n * 1000, RETRY_MAX_DELAY_MS);
  const date = Date.parse(value);
  if (Number.isFinite(date)) {
    return Math.min(Math.max(0, date - Date.now()), RETRY_MAX_DELAY_MS);
  }
  return null;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Build-time origin auth: allows the prerender to reach endpoints that require
// the edge-injected X-Origin-Auth header. Set PRERENDER_ORIGIN_SECRET to the
// same value as the backend ORIGIN_SHARED_SECRET env var in your build config.
// Safe to leave unset — the header is simply omitted and the fetch still works
// for open endpoints.
const PRERENDER_ORIGIN_SECRET = (
  process.env.PRERENDER_ORIGIN_SECRET ||
  process.env.ORIGIN_SHARED_SECRET ||
  ""
).trim();
const PRERENDER_ORIGIN_HEADER = (
  process.env.ORIGIN_SHARED_SECRET_HEADER || "X-Origin-Auth"
).trim();

function buildFetchHeaders() {
  const h = { Accept: "application/json" };
  if (PRERENDER_ORIGIN_SECRET) h[PRERENDER_ORIGIN_HEADER] = PRERENDER_ORIGIN_SECRET;
  return h;
}

async function fetchJson(url, timeoutMs = FETCH_TIMEOUT_MS) {
  let lastErr;
  for (let attempt = 1; attempt <= RETRY_MAX_ATTEMPTS; attempt++) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      const res = await fetch(url, {
        signal: ctrl.signal,
        headers: buildFetchHeaders(),
      });
      if (res.ok) return await res.json();
      // Retry only on transient classes: 429 (rate-limited) and 5xx.
      const transient = res.status === 429 || res.status >= 500;
      if (!transient || attempt === RETRY_MAX_ATTEMPTS) {
        throw new Error(`HTTP ${res.status} ${url}`);
      }
      const retryAfterMs = parseRetryAfter(res.headers.get("retry-after"));
      const backoff = Math.min(
        RETRY_BASE_DELAY_MS * 2 ** (attempt - 1),
        RETRY_MAX_DELAY_MS,
      );
      const jitter = Math.floor(Math.random() * 250);
      const wait = (retryAfterMs ?? backoff) + jitter;
      console.warn(
        `[prerender-data] ${url} HTTP ${res.status} (attempt ${attempt}/${RETRY_MAX_ATTEMPTS}); retrying in ${wait}ms`,
      );
      await sleep(wait);
      lastErr = new Error(`HTTP ${res.status} ${url}`);
      continue;
    } catch (err) {
      lastErr = err;
      // AbortError / network errors are also retryable.
      if (attempt === RETRY_MAX_ATTEMPTS) break;
      const backoff = Math.min(
        RETRY_BASE_DELAY_MS * 2 ** (attempt - 1),
        RETRY_MAX_DELAY_MS,
      );
      console.warn(
        `[prerender-data] ${url} ${err?.name || "Error"}: ${err?.message || err} (attempt ${attempt}/${RETRY_MAX_ATTEMPTS}); retrying in ${backoff}ms`,
      );
      await sleep(backoff);
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastErr ?? new Error(`fetchJson failed for ${url}`);
}

// In-memory promise cache so concurrent callers in the same process
// share a single in-flight fetch (the disk cache covers cross-process).
const inflight = new Map();

async function fetchOnce(name, url) {
  const envelope = readCacheEnvelope(name);
  if (envelope) {
    // Cross-build invalidation hook: re-probe the backend's schema
    // signal (cheap HEAD, capped at 2s, memoised for 60s in-process).
    // If the live signal is non-null and disagrees with the one we
    // recorded when this cache was written, the backend schema (or at
    // least its content) changed since the cache was produced — drop
    // it and re-fetch. If the backend doesn't expose a usable signal
    // we trust the embedded fingerprint + TTL like before.
    const liveSignal = await backendSchemaSignal(url);
    const cachedSignal = envelope.backendSignal ?? null;
    if (
      liveSignal !== null &&
      cachedSignal !== null &&
      liveSignal !== cachedSignal
    ) {
      console.warn(
        `[prerender-data] ${name} backend signal changed (${cachedSignal} -> ${liveSignal}); cache invalidated`,
      );
    } else {
      if (liveSignal === null || cachedSignal === null) {
        // Surface the gap so CI build logs make it obvious when
        // backend-driven invalidation is degraded to fingerprint+TTL
        // only. Operators can fix this by exposing X-Schema-Version
        // or a stable ETag from the data endpoint.
        console.warn(
          `[prerender-data] ${name} backend schema signal unavailable (live=${liveSignal ?? "null"} cached=${cachedSignal ?? "null"}); reusing cache via fingerprint+TTL only`,
        );
      }
      return envelope.data;
    }
  }
  if (inflight.has(name)) return inflight.get(name);
  const p = (async () => {
    try {
      const data = await fetchJson(url);
      const backendSignal = await backendSchemaSignal(url);
      writeCache(name, data, { backendSignal });
      return data;
    } catch (err) {
      console.warn(
        `[prerender-data] ${name} fetch failed (${err.message}); returning null`,
      );
      return null;
    } finally {
      inflight.delete(name);
    }
  })();
  inflight.set(name, p);
  return p;
}

export async function loadLibraryBundle() {
  return fetchOnce(
    "library-bundle",
    `${BACKEND}/api/content/library-bundle?slim=1`,
  );
}

export async function loadTopRoutes(days = 30, limit = 1000) {
  return fetchOnce(
    `top-routes-${days}-${limit}`,
    `${BACKEND}/api/analytics/top-routes?days=${days}&limit=${limit}`,
  );
}

// Convenience for the orchestrator: pre-warm both caches in parallel.
// Returns an object with whichever fetches succeeded.
export async function warmCache({ days = 30 } = {}) {
  const [bundle, traffic] = await Promise.all([
    loadLibraryBundle(),
    loadTopRoutes(days),
  ]);
  return { bundle, traffic };
}

function clearCache() {
  try {
    fs.rmSync(repoCacheDir, { recursive: true, force: true });
  } catch {
    // ignore
  }
}
