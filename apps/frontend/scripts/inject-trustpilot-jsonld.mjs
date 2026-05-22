/**
 * Task #729 — bake the Trustpilot Organization-level aggregate-rating
 * JSON-LD into the static HTML at build time so Googlebot picks it up
 * WITHOUT having to execute the lazy + deferred + async-fetched
 * client-side injector (`GlobalTrustpilotJsonLd.jsx`).
 *
 * Why this exists:
 *   The client-side injector loads from a lazy chunk, mounts only
 *   after `showDeferred = mounted && !ssr` flips, then awaits an HTTP
 *   call to /api/config/trustpilot/aggregate before injecting the
 *   <script type="application/ld+json"> tag. Google's Web Rendering
 *   Service does execute JS but with a tight budget — for lower-
 *   priority pages (FAQ/About/Pricing/Learn/Technology/ExamRoutine)
 *   the rich snippet is unreliable. Static HTML eliminates the entire
 *   JS dependency.
 *
 * What it does:
 *   1. Tries multiple sources for fresh aggregate values:
 *        a. Backend  GET /api/config/trustpilot/aggregate
 *        b. Trustpilot Business API direct (when both
 *           TRUSTPILOT_API_KEY + TRUSTPILOT_BUSINESS_UNIT_ID are set)
 *        c. The committed cache file
 *           scripts/.trustpilot-aggregate-cache.json
 *   2. On a fresh fetch, persists values back to the cache file so the
 *      next build can survive a live-source outage.
 *   3. Inserts (or updates) a <script type="application/ld+json"
 *      id="trustpilot-aggregaterating-static"> in the <head> of
 *      EVERY index.html under dist/ — so the SPA fallback shell AND
 *      every prerendered route's HTML carries the schema.
 *   4. If no source returns positive review counts (live fetch fails
 *      AND the committed cache is empty/invalid), the script logs
 *      loudly, strips any stale injected tag, and EXITS NON-ZERO so
 *      the build refuses to ship — honest-failure mode. Better to
 *      block deploy than to silently ship a homepage without the
 *      aggregateRating Search Console expects, or with stale numbers
 *      from an unrelated previous build.
 *
 * The script is idempotent: re-running on a dist/ that already has
 * the script tag replaces it (so multiple builds in the same dir
 * don't pile up duplicate JSON-LD blocks).
 *
 * Run as part of `pnpm --filter @workspace/syrabit build` after the
 * prerender step (so prerendered HTMLs also receive the inject), or
 * standalone:
 *
 *   node artifacts/syrabit/scripts/inject-trustpilot-jsonld.mjs
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(__dirname, "..", "dist");
const cacheFile = path.resolve(__dirname, ".trustpilot-aggregate-cache.json");

const SCRIPT_ID = "trustpilot-aggregaterating-static";
const ORG_NAME = "Syrabit.ai";
const ORG_URL = "https://syrabit.ai";

const BACKEND_BASE = (
  process.env.TRUSTPILOT_AGGREGATE_BACKEND ||
  process.env.VITE_BACKEND_URL ||
  "https://api.syrabit.ai"
).replace(/\/+$/, "");

const FETCH_TIMEOUT_MS = parseInt(
  process.env.TRUSTPILOT_FETCH_TIMEOUT_MS || "8000",
  10,
);

function log(msg) {
  console.log(`[trustpilot-jsonld] ${msg}`);
}

async function fetchWithTimeout(url, opts = {}, timeoutMs = FETCH_TIMEOUT_MS) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...opts, signal: ctrl.signal });
  } finally {
    clearTimeout(t);
  }
}

function isValidAggregate(obj) {
  return (
    obj &&
    typeof obj === "object" &&
    typeof obj.ratingValue === "number" &&
    Number.isFinite(obj.ratingValue) &&
    obj.ratingValue > 0 &&
    typeof obj.ratingCount === "number" &&
    Number.isInteger(obj.ratingCount) &&
    obj.ratingCount > 0
  );
}

function fetchFromOverride() {
  // Operator escape hatch: when neither the backend nor the
  // Trustpilot Business API can be reached from the build machine
  // (e.g. CloudFront/WAF blocks the build container — Task #747),
  // an operator can set TRUSTPILOT_AGGREGATE_OVERRIDE_JSON to a
  // JSON blob with at least ratingValue + ratingCount, and the
  // build will treat it as a successful live fetch (and persist
  // it back to the cache file so subsequent unattended builds
  // ship the same numbers).
  const raw = (process.env.TRUSTPILOT_AGGREGATE_OVERRIDE_JSON || "").trim();
  if (!raw) return null;
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (e) {
    log(`override: TRUSTPILOT_AGGREGATE_OVERRIDE_JSON is not valid JSON (${e.message || e}) — skipping`);
    return null;
  }
  const candidate = {
    ratingValue: Number(parsed.ratingValue),
    ratingCount: Number(parsed.ratingCount),
    bestRating: Number(parsed.bestRating ?? 5),
    worstRating: Number(parsed.worstRating ?? 1),
    source: "env_override",
  };
  if (!isValidAggregate(candidate)) {
    log(`override: blob missing ratingValue/ratingCount (got ${JSON.stringify(parsed)}) — skipping`);
    return null;
  }
  return candidate;
}

async function fetchFromBackend() {
  const url = `${BACKEND_BASE}/api/config/trustpilot/aggregate`;
  try {
    const r = await fetchWithTimeout(url);
    if (!r.ok) {
      log(`backend ${url} → ${r.status} (skipping)`);
      return null;
    }
    const json = await r.json();
    if (!isValidAggregate(json)) {
      log(`backend returned no usable aggregate (ratingValue=${json?.ratingValue}, ratingCount=${json?.ratingCount}) — skipping`);
      return null;
    }
    return {
      ratingValue: Number(json.ratingValue),
      ratingCount: Number(json.ratingCount),
      bestRating: Number(json.bestRating ?? 5),
      worstRating: Number(json.worstRating ?? 1),
      source: "backend",
    };
  } catch (e) {
    log(`backend fetch failed: ${e.message || e} — falling back`);
    return null;
  }
}

async function fetchFromTrustpilotDirect() {
  const apiKey = (process.env.TRUSTPILOT_API_KEY || "").trim();
  const buId = (process.env.TRUSTPILOT_BUSINESS_UNIT_ID || "").trim();
  if (!apiKey || !buId) {
    log("trustpilot-direct: TRUSTPILOT_API_KEY or TRUSTPILOT_BUSINESS_UNIT_ID not set — skipping");
    return null;
  }
  const url = `https://api.trustpilot.com/v1/business-units/${encodeURIComponent(buId)}?apikey=${encodeURIComponent(apiKey)}`;
  try {
    const r = await fetchWithTimeout(url, {
      headers: {
        apikey: apiKey,
        Accept: "application/json",
        "User-Agent": "Syrabit.ai-Build/1.0 (+https://syrabit.ai)",
      },
    });
    if (!r.ok) {
      log(`trustpilot-direct: HTTP ${r.status} — skipping`);
      return null;
    }
    const data = await r.json();
    const score = (data && typeof data.score === "object") ? data.score : {};
    const ratingValue =
      (typeof data.trustScore === "number" && data.trustScore) ||
      (typeof score.trustScore === "number" && score.trustScore) ||
      (typeof data.stars === "number" && data.stars) ||
      (typeof score.stars === "number" && score.stars) ||
      null;
    const nr = data.numberOfReviews;
    const ratingCount = (typeof nr === "object" && nr !== null) ? nr.total : nr;
    const candidate = {
      ratingValue: Number(ratingValue),
      ratingCount: Number(ratingCount),
      bestRating: 5,
      worstRating: 1,
      source: "trustpilot_direct",
    };
    if (!isValidAggregate(candidate)) {
      log(`trustpilot-direct: unparseable response (rating=${ratingValue} count=${ratingCount}) — skipping`);
      return null;
    }
    return candidate;
  } catch (e) {
    log(`trustpilot-direct fetch failed: ${e.message || e} — falling back`);
    return null;
  }
}

function loadCache() {
  try {
    const raw = fs.readFileSync(cacheFile, "utf8");
    const parsed = JSON.parse(raw);
    if (isValidAggregate(parsed)) {
      return {
        ratingValue: Number(parsed.ratingValue),
        ratingCount: Number(parsed.ratingCount),
        bestRating: Number(parsed.bestRating ?? 5),
        worstRating: Number(parsed.worstRating ?? 1),
        source: `cache(${parsed.source || "unknown"})`,
        fetched_at: parsed.fetched_at || null,
      };
    }
    return null;
  } catch {
    return null;
  }
}

function persistCache(agg) {
  const body = {
    _comment:
      "Build-time cache for Trustpilot aggregate-rating JSON-LD (Task #729). " +
      "Updated automatically on every successful fetch.",
    ratingValue: agg.ratingValue,
    ratingCount: agg.ratingCount,
    bestRating: agg.bestRating,
    worstRating: agg.worstRating,
    fetched_at: new Date().toISOString(),
    source: agg.source.replace(/^cache\(.+\)$/, "$1"),
  };
  fs.writeFileSync(cacheFile, JSON.stringify(body, null, 2) + "\n");
}

function buildJsonLdScript(agg) {
  const node = {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: ORG_NAME,
    url: ORG_URL,
    aggregateRating: {
      "@type": "AggregateRating",
      ratingValue: agg.ratingValue,
      reviewCount: agg.ratingCount,
      bestRating: agg.bestRating,
      worstRating: agg.worstRating,
    },
  };
  return `<script type="application/ld+json" id="${SCRIPT_ID}" data-source="${agg.source}">${JSON.stringify(node)}</script>`;
}

function listHtmlFiles(rootDir) {
  /** @type {string[]} */
  const out = [];
  function walk(dir) {
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (entry.isFile() && entry.name.endsWith(".html")) {
        out.push(full);
      }
    }
  }
  walk(rootDir);
  return out;
}

function injectIntoHtml(html, scriptTag) {
  // Strip any pre-existing tag with the same id (idempotency).
  const existingPattern = new RegExp(
    `<script[^>]*id=["']${SCRIPT_ID}["'][^>]*>[\\s\\S]*?<\\/script>\\s*`,
    "g",
  );
  let out = html.replace(existingPattern, "");
  // Insert immediately before </head>. If for some reason there is no
  // </head> (unlikely), append to end of file as a defensive fallback.
  if (out.includes("</head>")) {
    out = out.replace("</head>", `  ${scriptTag}\n  </head>`);
  } else {
    out = out + "\n" + scriptTag + "\n";
  }
  return out;
}

async function main() {
  if (!fs.existsSync(distDir)) {
    console.error(`[trustpilot-jsonld] dist/ not found at ${distDir} — run vite build first.`);
    process.exit(1);
  }

  let agg = fetchFromOverride();
  if (!agg) agg = await fetchFromBackend();
  if (!agg) agg = await fetchFromTrustpilotDirect();

  if (agg) {
    log(`fetched aggregate from ${agg.source}: ${agg.ratingValue}★ (${agg.ratingCount} reviews) — persisting to cache`);
    try {
      persistCache(agg);
    } catch (e) {
      log(`warning: failed to write cache file: ${e.message || e}`);
    }
  } else {
    agg = loadCache();
    if (agg) {
      log(`live sources unavailable — using committed cache (${agg.source}, fetched_at=${agg.fetched_at}): ${agg.ratingValue}★ (${agg.ratingCount} reviews)`);
    }
  }

  const htmlFiles = listHtmlFiles(distDir);

  if (!agg) {
    log("no aggregate available from ANY source (backend, trustpilot-direct, cache).");
    log("=> HONEST-FAILURE MODE: refusing to ship a build without aggregateRating.");
    log("=> Fix one of:");
    log("   - make the backend at " + BACKEND_BASE + "/api/config/trustpilot/aggregate return positive ratingValue+ratingCount");
    log("   - export TRUSTPILOT_API_KEY + TRUSTPILOT_BUSINESS_UNIT_ID so the build can hit Trustpilot directly");
    log("   - export TRUSTPILOT_AGGREGATE_OVERRIDE_JSON='{\"ratingValue\":4.1,\"ratingCount\":7}' (operator escape hatch when the build host is WAF-blocked)");
    log("   - commit a non-null " + path.relative(process.cwd(), cacheFile) + " (a previous successful build will write this for you)");
    // Idempotency: STRIP any previously-injected script tag from dist/
    // so the failing build cannot leave stale review numbers behind
    // for a downstream packager to pick up.
    const stripPattern = new RegExp(
      `\\s*<script[^>]*id=["']${SCRIPT_ID}["'][^>]*>[\\s\\S]*?<\\/script>`,
      "g",
    );
    let stripped = 0;
    for (const file of htmlFiles) {
      let html;
      try { html = fs.readFileSync(file, "utf8"); } catch { continue; }
      const next = html.replace(stripPattern, "");
      if (next !== html) {
        fs.writeFileSync(file, next);
        stripped++;
      }
    }
    if (stripped > 0) {
      log(`stripped stale aggregate-rating tag from ${stripped} HTML file(s)`);
    }
    process.exit(1);
  }

  const tag = buildJsonLdScript(agg);
  log(`scanning ${htmlFiles.length} HTML file(s) under dist/`);

  let written = 0;
  let skipped = 0;
  for (const file of htmlFiles) {
    let html;
    try {
      html = fs.readFileSync(file, "utf8");
    } catch {
      skipped++;
      continue;
    }
    const next = injectIntoHtml(html, tag);
    if (next !== html) {
      fs.writeFileSync(file, next);
      written++;
    } else {
      skipped++;
    }
  }
  log(`injection complete — wrote ${written} file(s), skipped ${skipped} (no change needed)`);
  log(`shipping aggregateRating: ${agg.ratingValue}/${agg.bestRating} from ${agg.ratingCount} reviews (source=${agg.source})`);
}

main().catch((e) => {
  console.error("[trustpilot-jsonld] FATAL", e);
  process.exit(1);
});
