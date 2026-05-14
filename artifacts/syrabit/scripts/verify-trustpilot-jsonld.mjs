/**
 * Task #729 — verify the Trustpilot Organization aggregateRating
 * JSON-LD is present + well-formed in the STATIC HTML of every URL
 * Google might crawl.
 *
 * Why static HTML and not a JS-rendered crawl:
 *   The whole point of Task #729 is whether Googlebot picks up the
 *   stars on lower-priority pages (FAQ/About/Pricing/Learn/Technology/
 *   ExamRoutine) where its render budget is tight. The honest test
 *   is "is the JSON-LD present BEFORE any JS executes?". If yes,
 *   Google's RRT will pass on the first crawl. If no, RRT may pass
 *   intermittently (when WRS happens to render the page) but real-
 *   world rich snippets will be unreliable.
 *
 * Modes:
 *   * --target=remote (default): fetches from the production origin
 *     (https://syrabit.ai by default; override with TARGET_ORIGIN).
 *   * --target=dist: reads files directly from artifacts/syrabit/dist
 *     so the script can run in the build pipeline before deploy.
 *
 * Validation:
 *   For each URL we collect every <script type="application/ld+json">
 *   block, parse them, and report PASS if any one of them contains an
 *   AggregateRating with ratingValue (number > 0) and reviewCount /
 *   ratingCount (integer > 0). Also enforces the bestRating/worstRating
 *   pair is present. We deliberately do NOT call the schema.org HTTP
 *   validator because (a) it adds a flaky network dep to CI and
 *   (b) the local validator below covers the exact constraints Google
 *   actually rejects on for AggregateRating.
 *
 * Exit code:
 *   0 — every URL passed
 *   1 — one or more URLs failed (printed as a table)
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(__dirname, "..", "dist");

const TARGET_ORIGIN = (process.env.TARGET_ORIGIN || "https://syrabit.ai").replace(/\/+$/, "");
const FETCH_TIMEOUT_MS = parseInt(process.env.VERIFY_FETCH_TIMEOUT_MS || "12000", 10);
const GOOGLEBOT_UA =
  "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)";

const args = process.argv.slice(2);
const targetMode =
  args.find((a) => a.startsWith("--target="))?.split("=")[1] || "remote";
// Task #750 — write a machine-readable JSON report next to the
// human-readable table so the daily scheduled workflow can POST it to
// the admin dashboard. Optional; absent path = no file written.
const jsonOutPath =
  args.find((a) => a.startsWith("--json-out="))?.split("=")[1] || "";

// The exact list of "previously-uncovered public URLs" referenced by
// Task #729 + a couple of high-priority controls (Landing/Library)
// to anchor the report.
const TARGET_PATHS = [
  "/",                  // Landing (control — should always pass)
  "/faq",               // Task #729 target
  "/about",             // Task #729 target
  "/pricing",           // Task #729 target
  "/learn",             // Task #729 target
  "/technology",        // Task #729 target
  "/exam-routine",      // Task #729 target
  "/library",           // Library landing (control)
];

function extractJsonLdBlocks(html) {
  const blocks = [];
  const re =
    /<script[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html)) !== null) {
    const raw = m[1].trim();
    try {
      blocks.push(JSON.parse(raw));
    } catch {
      blocks.push({ __parseError: true, raw: raw.slice(0, 120) });
    }
  }
  return blocks;
}

function findAggregateRating(blocks) {
  // AggregateRating may appear:
  //   * directly as a node with "@type": "AggregateRating"
  //   * as the `aggregateRating` field on an Organization /
  //     EducationalOrganization / Product / etc.
  //   * inside an @graph
  const visited = [];
  function visit(node) {
    if (!node || typeof node !== "object") return;
    if (Array.isArray(node)) {
      node.forEach(visit);
      return;
    }
    if (node["@type"] === "AggregateRating") {
      visited.push(node);
    }
    if (node.aggregateRating) visit(node.aggregateRating);
    if (Array.isArray(node["@graph"])) node["@graph"].forEach(visit);
  }
  blocks.forEach(visit);
  return visited;
}

function validateAggregateRating(node) {
  /** @type {string[]} */
  const errors = [];
  const rv = node.ratingValue;
  const rc = node.reviewCount ?? node.ratingCount;
  const best = node.bestRating;
  const worst = node.worstRating;
  if (typeof rv !== "number" || !Number.isFinite(rv) || rv <= 0) {
    errors.push(`ratingValue invalid (${JSON.stringify(rv)})`);
  }
  if (rv > 5) errors.push(`ratingValue > 5 (${rv}); needs explicit bestRating`);
  if (typeof rc !== "number" || !Number.isInteger(rc) || rc <= 0) {
    errors.push(`reviewCount/ratingCount invalid (${JSON.stringify(rc)})`);
  }
  if (best !== undefined && (typeof best !== "number" || best <= 0)) {
    errors.push(`bestRating invalid (${JSON.stringify(best)})`);
  }
  if (worst !== undefined && (typeof worst !== "number" || worst < 0)) {
    errors.push(`worstRating invalid (${JSON.stringify(worst)})`);
  }
  return errors;
}

async function fetchHtmlRemote(urlPath) {
  const url = `${TARGET_ORIGIN}${urlPath}`;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      signal: ctrl.signal,
      headers: { "User-Agent": GOOGLEBOT_UA, Accept: "text/html" },
      redirect: "follow",
    });
    const body = await res.text();
    return { ok: res.ok, status: res.status, body };
  } finally {
    clearTimeout(t);
  }
}

function readHtmlDist(urlPath) {
  // Serve "/foo" by reading dist/foo/index.html (matches Cloudflare
  // Pages route resolution). For "/" use dist/index.html. Falls back
  // to the SPA shell when no prerendered HTML exists.
  const trimmed = urlPath.replace(/^\/+/, "").replace(/\/+$/, "");
  const candidates = [
    trimmed === "" ? "index.html" : path.join(trimmed, "index.html"),
    trimmed === "" ? null : `${trimmed}.html`,
    "index.html", // SPA fallback
  ].filter(Boolean);
  for (const rel of candidates) {
    const full = path.join(distDir, rel);
    if (fs.existsSync(full)) {
      return { ok: true, status: 200, body: fs.readFileSync(full, "utf8"), source: rel };
    }
  }
  return { ok: false, status: 404, body: "" };
}

async function checkUrl(urlPath) {
  const fetched =
    targetMode === "dist"
      ? readHtmlDist(urlPath)
      : await fetchHtmlRemote(urlPath);
  if (!fetched.ok || !fetched.body) {
    return {
      url: urlPath,
      pass: false,
      status: fetched.status,
      reason: `fetch failed (status=${fetched.status})`,
    };
  }
  const blocks = extractJsonLdBlocks(fetched.body);
  if (blocks.length === 0) {
    return {
      url: urlPath,
      pass: false,
      status: fetched.status,
      reason: "no <script type=application/ld+json> blocks found",
    };
  }
  const ratings = findAggregateRating(blocks);
  if (ratings.length === 0) {
    return {
      url: urlPath,
      pass: false,
      status: fetched.status,
      reason: `JSON-LD present (${blocks.length} blocks) but NO AggregateRating in any of them`,
      ldTypes: blocks
        .flatMap((b) => (b["@graph"] ? b["@graph"].map((n) => n["@type"]) : [b["@type"]]))
        .filter(Boolean),
    };
  }
  // Take the first valid one.
  for (const r of ratings) {
    const errs = validateAggregateRating(r);
    if (errs.length === 0) {
      return {
        url: urlPath,
        pass: true,
        status: fetched.status,
        ratingValue: r.ratingValue,
        reviewCount: r.reviewCount ?? r.ratingCount,
        source: targetMode === "dist" ? `dist/${fetched.source}` : `${TARGET_ORIGIN}${urlPath}`,
      };
    }
  }
  return {
    url: urlPath,
    pass: false,
    status: fetched.status,
    reason: `AggregateRating present but invalid: ${validateAggregateRating(ratings[0]).join("; ")}`,
  };
}

function printTable(results) {
  const colW = { url: 18, status: 7, ok: 5, info: 60 };
  const sep =
    "+" +
    "-".repeat(colW.url + 2) +
    "+" +
    "-".repeat(colW.status + 2) +
    "+" +
    "-".repeat(colW.ok + 2) +
    "+" +
    "-".repeat(colW.info + 2) +
    "+";
  console.log(sep);
  console.log(
    `| ${"URL".padEnd(colW.url)} | ${"HTTP".padEnd(colW.status)} | ${"PASS".padEnd(colW.ok)} | ${"DETAIL".padEnd(colW.info)} |`,
  );
  console.log(sep);
  for (const r of results) {
    const detail = r.pass
      ? `${r.ratingValue}★ from ${r.reviewCount} reviews`
      : (r.reason || "fail");
    console.log(
      `| ${String(r.url).padEnd(colW.url).slice(0, colW.url)} | ${String(r.status).padEnd(colW.status)} | ${(r.pass ? "yes" : "NO").padEnd(colW.ok)} | ${String(detail).padEnd(colW.info).slice(0, colW.info)} |`,
    );
  }
  console.log(sep);
}

async function main() {
  console.log(
    `[verify-trustpilot-jsonld] target=${targetMode}` +
      (targetMode === "remote" ? ` origin=${TARGET_ORIGIN}` : ` dir=${distDir}`),
  );
  const results = [];
  // Sequential — keeps output deterministic and avoids hammering prod.
  for (const p of TARGET_PATHS) {
    /* eslint-disable no-await-in-loop */
    const r = await checkUrl(p);
    results.push(r);
  }
  printTable(results);
  const passed = results.filter((r) => r.pass).length;
  const failed = results.length - passed;
  console.log(`\n${passed}/${results.length} URLs pass; ${failed} fail.`);

  // If every URL returned 403, the GitHub Actions runner is being blocked by
  // Cloudflare Bot Fight Mode (it cannot pass CF's Googlebot reverse-DNS
  // verification). This is a CDN configuration issue, NOT a JSON-LD regression.
  // Emit a warning and exit 0 so the scheduled job doesn't page on-call for
  // infra noise. Only real regressions (some pages 200 but lack JSON-LD) fail.
  const all403 = results.length > 0 && results.every(
    (r) => !r.pass && r.status === 403
  );
  if (all403) {
    console.warn(
      "\n::warning title=Trustpilot check blocked by CDN::" +
      "All URLs returned HTTP 403 — Cloudflare Bot Fight Mode is blocking the " +
      "GitHub Actions runner (it cannot pass Googlebot reverse-DNS verification). " +
      "This is a CDN infra issue, not a JSON-LD regression. Re-run manually with " +
      "TARGET_ORIGIN pointing to an origin that allows this IP, or disable Bot Fight " +
      "Mode for the verifier's IP range. Exiting 0 to avoid false-positive pages."
    );
    if (jsonOutPath) {
      const report = {
        schemaVersion: 1,
        generatedAt: new Date().toISOString(),
        target: targetMode,
        origin: targetMode === "remote" ? TARGET_ORIGIN : null,
        totalUrls: results.length,
        passed: 0,
        failed: results.length,
        ok: null,
        blockedByCdn: true,
        results: results.map((r) => ({
          url: r.url, pass: false, status: 403,
          ratingValue: null, reviewCount: null,
          reason: "blocked by CDN (403)",
        })),
      };
      try {
        fs.mkdirSync(path.dirname(path.resolve(jsonOutPath)), { recursive: true });
        fs.writeFileSync(jsonOutPath, JSON.stringify(report, null, 2));
      } catch { /* non-fatal */ }
    }
    process.exit(0);
  }

  // Task #750 — emit a machine-readable copy of the same report so the
  // scheduled workflow can POST it to the admin dashboard. We write the
  // file even on failure so ops can see WHICH urls failed in the UI.
  if (jsonOutPath) {
    const report = {
      schemaVersion: 1,
      generatedAt: new Date().toISOString(),
      target: targetMode,
      origin: targetMode === "remote" ? TARGET_ORIGIN : null,
      totalUrls: results.length,
      passed,
      failed,
      ok: failed === 0,
      results: results.map((r) => ({
        url: r.url,
        pass: !!r.pass,
        status: r.status ?? null,
        ratingValue: r.ratingValue ?? null,
        reviewCount: r.reviewCount ?? null,
        reason: r.pass ? null : (r.reason || "fail"),
      })),
    };
    try {
      fs.mkdirSync(path.dirname(path.resolve(jsonOutPath)), { recursive: true });
      fs.writeFileSync(jsonOutPath, JSON.stringify(report, null, 2));
      console.log(`\nWrote JSON report → ${jsonOutPath}`);
    } catch (e) {
      console.error(`\n[verify-trustpilot-jsonld] failed to write --json-out=${jsonOutPath}: ${e?.message || e}`);
      // Don't change the overall pass/fail outcome on a write error —
      // the JSON file is supplementary, the table above is canonical.
    }
  }

  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => {
  console.error("[verify-trustpilot-jsonld] FATAL", e);
  process.exit(2);
});
