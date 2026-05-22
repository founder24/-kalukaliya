// Task #856 — Critical-CSS extraction for Syrabit web.
//
// Runs after the prerender stage and walks every emitted HTML file in
// dist/. For each one, Beasties (the actively-maintained Google fork of
// critters) parses the document, scans for the class names actually
// referenced in the markup, inlines the matching CSS rules into a new
// <style> block in <head>, and rewrites the original render-blocking
// <link rel="stylesheet"> into a non-blocking preload+swap pair.
//
// Why this matters
// ----------------
// The audit at task time showed the initial paint of syrabit.ai was
// gated on a single 141 KB stylesheet (assets/index-*.css). The browser
// could not start rendering until that file finished downloading,
// adding 200–500 ms to FCP on mobile (3G/4G CGNAT — our typical
// AHSEC/SEBA student's network). After this step, only the ~10–14 KB
// of critical above-the-fold rules ship inline; the full stylesheet
// downloads asynchronously and applies once it's ready.
//
// Notes
// -----
//   * pruneSource: false — the full stylesheet stays on disk so other
//     routes (admin, library, chat) can still reuse it from cache.
//   * mergeStylesheets: false — preserves the existing inline <style>
//     blocks (pre-hydration shell, Emergent badge hider, critical CSS
//     stub from index.html) verbatim.
//   * preload: 'swap-high' — emits the high-priority preload + swap
//     pattern (Filament Group's loadCSS recipe), with a <noscript>
//     fallback for clients with JS disabled.
//   * Errors are downgraded to warnings: a parser failure on one
//     prerendered HTML must not fail the Pages build, since the only
//     consequence is that one page goes back to its pre-fix CSS load
//     pattern. The build's own verifier covers correctness.
//
// Idempotent — safe to run twice on the same dist/.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import Beasties from "beasties";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const DIST = path.join(repoRoot, "dist");

if (!fs.existsSync(DIST)) {
  console.error(`[critical-css] dist/ not found at ${DIST}; skipping.`);
  process.exit(0);
}

function walkHtml(dir, files = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      walkHtml(p, files);
    } else if (entry.isFile() && entry.name.endsWith(".html")) {
      files.push(p);
    }
  }
  return files;
}

// Skip files that are not really part of the SPA shell — keeping them
// in the candidate list would just spend time parsing markup that has
// no <link rel="stylesheet"> for Beasties to act on.
const SKIP_NAMES = new Set([
  "googlefbe0a804ad7e5fdd.html", // Google Search Console verification stub
  "stats.html",                   // rollup-plugin-visualizer report
  "offline.html",                 // SW offline shell — already self-contained
]);

// Strip <noscript>…</noscript> bodies before scanning so the noscript
// fallback link Beasties writes on its first pass cannot fool a re-run
// into thinking there is still a render-blocking link to rewrite. Makes
// the script truly idempotent.
function htmlWithoutNoscript(html) {
  return html.replace(/<noscript\b[^>]*>[\s\S]*?<\/noscript>/gi, "");
}

function hasActiveBlockingLink(html) {
  const stripped = htmlWithoutNoscript(html);
  return /\<link[^>]+rel=["']stylesheet["'][^>]+\/assets\/index-[A-Za-z0-9_-]+\.css/.test(
    stripped,
  );
}

// The earlier prerender stages (scripts/prerender-library.mjs +
// scripts/prerender-routes.mjs) full-inline the entire 141 KB
// stylesheet into prerendered HTMLs as a `<style data-inline-css="X">`
// block, citing tasks #391 / #496. That removed render-blocking CSS at
// the time but it predates this critical-CSS step and is now a 140 KB
// HTML payload tax on every cold hit to /library, /browser, /subject/*
// and /chapter/* — the highest-value SEO routes.
//
// Before handing the HTML to Beasties we restore the original
// `<link rel="stylesheet" href="/assets/X">` so Beasties can do its
// proper job: extract just the ~14 KB above-the-fold subset (which now
// includes the SSR-rendered subject/chapter card rules because those
// elements are present in the markup at scan time), inline that, and
// rewrite the link into the non-blocking preload+swap pattern. End
// result for these routes: ~140 KB HTML → ~30 KB HTML, no render
// blocking, no FOUC.
const INLINE_CSS_RE =
  /<style\s+data-inline-css="([^"]+)">[\s\S]*?<\/style>/g;
function unwrapInlinedStylesheet(html) {
  return html.replace(INLINE_CSS_RE, (_match, file) => {
    return `<link rel="stylesheet" crossorigin href="/assets/${file}">`;
  });
}

// Stale dist/ files from an earlier (pre-idempotent) run of this
// script can have multiple stacked Beasties critical-CSS extracts +
// duplicated preload+swap links pointing at the same bundle. Detect
// that shape so we can clean them up below. Today's idempotency check
// (htmlWithoutNoscript + INLINE_CSS_RE) prevents *new* duplication,
// but it won't repair files that were already stacked.
const PRELOAD_DUP_RE =
  /<link[^>]*rel=["']alternate stylesheet preload["'][^>]*\/assets\/index-[A-Za-z0-9_-]+\.css/g;
function countPreloadLinks(html) {
  PRELOAD_DUP_RE.lastIndex = 0;
  let n = 0;
  while (PRELOAD_DUP_RE.exec(html)) n++;
  return n;
}

function shouldProcess(htmlPath) {
  const name = path.basename(htmlPath);
  if (SKIP_NAMES.has(name)) return false;
  const html = fs.readFileSync(htmlPath, "utf-8");
  if (hasActiveBlockingLink(html)) return true;
  // Prerendered routes have the full sheet wrapped in
  // <style data-inline-css="…">. Treat them as candidates so the
  // unwrap → Beasties pass below can shrink them.
  if (INLINE_CSS_RE.test(html)) {
    INLINE_CSS_RE.lastIndex = 0;
    return true;
  }
  // Cleanup pass for stale stacked output from earlier runs.
  if (countPreloadLinks(html) > 1) return true;
  return false;
}

// Collapse duplicate stacked output from earlier (pre-idempotent)
// inliner runs. Keeps:
//   * the FIRST <link rel="alternate stylesheet preload" …> only
//   * one <noscript><link rel="stylesheet" …></noscript> fallback
// Drops every other preload+swap copy and returns the cleaned HTML.
// We deliberately do not touch the inlined critical-CSS <style>
// blocks here — Beasties.process() below will re-emit a single
// canonical extract once we restore a single <link> input. This keeps
// the dedup logic small and avoids guessing which of the stacked
// <style> blocks is the "right" one.
function dedupeStaleOutput(html) {
  if (countPreloadLinks(html) <= 1) return html;
  let firstSeen = false;
  let out = html.replace(PRELOAD_DUP_RE, (match) => {
    if (!firstSeen) {
      firstSeen = true;
      // Convert the kept preload link back into a plain
      // <link rel="stylesheet" …> so Beasties below treats it as
      // input to extract from (and re-emits a single fresh
      // preload+swap pair). Strip the onload attribute to be safe.
      return match
        .replace(/rel=["']alternate stylesheet preload["']/, 'rel="stylesheet"')
        .replace(/\s+onload=("[^"]*"|'[^']*')/, "")
        .replace(/\s+as=("style"|'style')/, "")
        .replace(/\s+title=("[^"]*"|'[^']*')/, "");
    }
    return ""; // drop every duplicate
  });
  // Drop duplicate <noscript> fallback links pointing at the same
  // bundle — Beasties will re-emit a fresh one.
  const NOSCRIPT_LINK_RE =
    /<noscript>\s*<link[^>]+rel=["']stylesheet["'][^>]+\/assets\/index-[A-Za-z0-9_-]+\.css[^>]*>\s*<\/noscript>/g;
  out = out.replace(NOSCRIPT_LINK_RE, "");
  // Drop every existing Beasties-emitted critical-CSS <style> block
  // (anything that doesn't carry the data-inline-css attribute and
  // sits in <head>). Beasties below will re-extract a single canonical
  // critical CSS block on the cleaned input.
  const STALE_STYLE_RE =
    /<style(?![^>]*\bdata-inline-css\b)(?![^>]*\bdata-keep\b)[^>]*>[\s\S]*?<\/style>/g;
  // Only operate on <head>. Body-level <style> would be unusual but
  // we still want to leave it alone.
  const headEnd = out.indexOf("</head>");
  if (headEnd > 0) {
    const head = out.slice(0, headEnd);
    const body = out.slice(headEnd);
    // Preserve the small SHELL/Emergent inline blocks (≤ 2 KB each) —
    // those are the hand-authored ones, not Beasties output.
    const cleanedHead = head.replace(STALE_STYLE_RE, (m, ..._rest) => {
      const inner = m.replace(/<\/?style[^>]*>/g, "");
      return inner.length > 4096 ? "" : m;
    });
    out = cleanedHead + body;
  }
  return out;
}

const beasties = new Beasties({
  path: DIST,
  publicPath: "/",
  preload: "swap-high",
  pruneSource: false,
  mergeStylesheets: false,
  inlineFonts: false,
  reduceInlineStyles: false,
  fonts: false,
  logLevel: "silent",
});

const htmls = walkHtml(DIST);
let processed = 0;
let skipped = 0;
const failures = [];
let bytesInlinedTotal = 0;

const start = Date.now();

for (const htmlPath of htmls) {
  if (!shouldProcess(htmlPath)) {
    skipped++;
    continue;
  }
  const original = fs.readFileSync(htmlPath, "utf-8");
  // Step 1: clean up any stale stacked output from earlier
  // (pre-idempotent) inliner runs. After this, the file has at most
  // one preload+swap link and the small hand-authored inline blocks.
  const deduped = dedupeStaleOutput(original);
  // Step 2: restore the canonical <link> shape on prerendered routes
  // so Beasties can extract their critical subset and defer the rest
  // (otherwise they'd ship the full ~141 KB sheet inline forever).
  const beastiesInput = unwrapInlinedStylesheet(deduped);
  try {
    const transformed = await beasties.process(beastiesInput);
    if (typeof transformed === "string" && transformed.length > 0) {
      // Track HTML-size delta vs the on-disk file. For prerendered
      // routes this is a large NEGATIVE number (e.g. -110 KB on
      // /library) because we replaced a 141 KB inline <style> with a
      // ~14 KB critical extract + a small <link>. For SPA-fallback
      // pages it's a small positive number (the inlined critical CSS).
      const delta = transformed.length - original.length;
      bytesInlinedTotal += delta;
      fs.writeFileSync(htmlPath, transformed);
      processed++;
    } else {
      skipped++;
    }
  } catch (err) {
    failures.push({ path: htmlPath, err: String(err && err.message || err) });
  }
}

const elapsed = Date.now() - start;
const rel = (p) => path.relative(repoRoot, p);
console.log(
  `[critical-css] processed ${processed}, skipped ${skipped}, ` +
  `failures ${failures.length}, ~${Math.round(bytesInlinedTotal / 1024)} KB ` +
  `inlined across all HTMLs (${elapsed}ms)`,
);
if (failures.length) {
  for (const f of failures) {
    console.warn(`[critical-css] WARN ${rel(f.path)}: ${f.err}`);
  }
}

// Hard-fail guards (Task #856 review feedback). A green build that
// silently shipped render-blocking CSS again would defeat the entire
// task, so we fail loudly when the entry point breaks or when a high
// fraction of pages broke at once (typical signature of a Beasties
// upgrade gone wrong, or a Vite asset-name change Beasties can't
// resolve). Per-file failures below the threshold remain soft so a
// single oddball prerender can't block deploys.
const ROOT_INDEX = path.join(DIST, "index.html");
const rootFailed = failures.some((f) => path.resolve(f.path) === ROOT_INDEX);
if (rootFailed) {
  console.error(
    `[critical-css] FATAL: dist/index.html (the SPA entry point) failed to ` +
    `inline. Refusing to ship a build that loses critical-CSS coverage on ` +
    `the route every cold visit lands on.`,
  );
  process.exit(1);
}
// Failure ratio guard: if more than 1/3 of the candidate HTMLs broke,
// something systemic is wrong (Beasties version drift, dist/ layout
// change, etc.) and the build should not proceed. processed + failures
// = the candidate set we actually attempted.
const attempted = processed + failures.length;
if (attempted > 0 && failures.length / attempted > 1 / 3) {
  console.error(
    `[critical-css] FATAL: ${failures.length}/${attempted} HTMLs failed to ` +
    `inline (>33% threshold) — investigate Beasties + dist/ before shipping.`,
  );
  process.exit(1);
}

// Postcondition: the root index MUST end up in the non-blocking
// pattern. This catches the silent case where Beasties returns a
// string but does not actually rewrite the link (e.g., it could not
// resolve the stylesheet on disk).
if (fs.existsSync(ROOT_INDEX)) {
  const rootHtml = fs.readFileSync(ROOT_INDEX, "utf-8");
  if (hasActiveBlockingLink(rootHtml)) {
    console.error(
      `[critical-css] FATAL: dist/index.html still has an active ` +
      `<link rel="stylesheet" href="/assets/index-*.css"> after Beasties ran. ` +
      `Critical-CSS extraction did not take effect on the SPA entry point.`,
    );
    process.exit(1);
  }
}
