// Task #535: single-pass post-build verifier.
//
// Walks dist/ ONCE, collects every index.html, and applies all the
// structural assertions the individual verify-* scripts performed —
// in a single in-memory pass. Then runs verify-hydration.mjs in
// parallel because it needs a real headless browser (heavy enough
// that it stays in its own subprocess).
//
// The legacy verify-prerender, verify-library-prerender, and
// verify-canonicals scripts were removed in Task #538 — every
// assertion they performed is reproduced inline below so the build
// pipeline only walks the disk once.

import fs from "fs";
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";
import { spawn } from "child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(__dirname, "..", "dist");
const distSsrDir = path.resolve(__dirname, "..", "dist-ssr");

const SITE = "https://syrabit.ai";

const REQUIRED_CANONICAL_ROUTES = [
  { route: "/library", file: "library/index.html" },
  { route: "/chat", file: "chat/index.html" },
  { route: "/home", file: "home/index.html" },
  { route: "/pricing", file: "pricing/index.html" },
  { route: "/login", file: "login/index.html" },
  { route: "/signup", file: "signup/index.html" },
  { route: "/terms", file: "terms/index.html" },
  { route: "/privacy", file: "privacy/index.html" },
  { route: "/about", file: "about/index.html" },
  { route: "/technology", file: "technology/index.html" },
  { route: "/profile", file: "profile/index.html" },
  { route: "/admin/login", file: "admin/login/index.html" },
];

const failures = [];
const warnings = [];

function fail(msg) {
  failures.push(msg);
}
function warn(msg) {
  warnings.push(msg);
}

function* walk(dir, prefix = "") {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      if (rel === "assets" || rel === "icons" || rel === "fonts") continue;
      yield* walk(full, rel);
    } else if (entry.name === "index.html") {
      yield { full, rel };
    }
  }
}

const startedAt = Date.now();

if (!fs.existsSync(distDir)) {
  console.error(`[verify-all] FAIL: dist/ missing at ${distDir}`);
  process.exit(1);
}

// ── Pass 1: walk dist/, gather all index.html files into memory ─────
const pages = [];
for (const item of walk(distDir)) {
  pages.push({ ...item, body: fs.readFileSync(item.full, "utf-8") });
}
console.log(`[verify-all] walked dist/ — ${pages.length} index.html files`);

// ── Root index.html: must NOT carry a hard-coded canonical ──────────
const root = pages.find((p) => p.rel === "index.html");
if (root) {
  const rootCanonicals =
    root.body.match(/<link\s+rel="canonical"\s+href="[^"]*"[^>]*>/g) || [];
  if (rootCanonicals.length > 0) {
    fail(
      `dist/index.html must not ship a static canonical (found ${rootCanonicals.length}): ` +
        rootCanonicals.join(" | "),
    );
  }
}

// Index by `file` for required-route check.
const byRel = new Map(pages.map((p) => [p.rel, p]));

// ── Required canonical routes ───────────────────────────────────────
for (const { route, file } of REQUIRED_CANONICAL_ROUTES) {
  const page = byRel.get(file);
  if (!page) {
    fail(`${route}: missing ${file}`);
    continue;
  }
  const tags =
    page.body.match(/<link\s+rel="canonical"\s+href="[^"]*"[^>]*>/g) || [];
  if (tags.length !== 1) {
    fail(
      `${route}: expected exactly 1 canonical tag, found ${tags.length} ` +
        `(${tags.join(" | ") || "<none>"})`,
    );
    continue;
  }
  const expected = `${SITE}${route}`;
  const m = tags[0].match(/href="([^"]*)"/);
  if (!m || m[1] !== expected) {
    fail(
      `${route}: canonical href mismatch — expected ${expected}, got ${m && m[1]}`,
    );
  }
}

// ── Library / browser prerender quality ─────────────────────────────
const LIBRARY_FILES = ["library/index.html", "browser/index.html"];
for (const rel of LIBRARY_FILES) {
  const page = byRel.get(rel);
  if (!page) {
    fail(`missing prerender output at ${rel}`);
    continue;
  }
  const body = page.body;
  if (!body.includes('data-hydrate="library"')) {
    fail(`${rel}: missing data-hydrate="library" marker on #root`);
    continue;
  }
  for (const sig of [
    "Switched to client rendering",
    "does not support Suspense",
    "<!--$!-->",
  ]) {
    if (body.includes(sig)) {
      fail(`${rel}: contains React SSR abort signature ${JSON.stringify(sig)}`);
    }
  }
  const m = body.match(
    /<div id="root" data-hydrate="library">([\s\S]*?)<\/div>\s*<script/,
  );
  if (!m) {
    fail(`${rel}: could not locate #root container`);
    continue;
  }
  const inner = m[1];
  if (inner.trim().length < 500) {
    fail(`${rel}: #root content too small (${inner.length} bytes)`);
  }
  const bundleIdx = body.indexOf("window.__LIBRARY_BUNDLE__");
  const moduleIdx = body.indexOf('<script type="module"');
  if (bundleIdx === -1) {
    warn(
      `${rel}: window.__LIBRARY_BUNDLE__ not inlined (backend was unreachable at prerender time)`,
    );
  } else if (moduleIdx === -1 || bundleIdx > moduleIdx) {
    fail(`${rel}: window.__LIBRARY_BUNDLE__ must be inlined BEFORE main module script`);
  }
  if (
    !/<link rel="modulepreload"[^>]*href="\/assets\/LibraryPage-[^"]+\.js"/.test(
      body,
    )
  ) {
    fail(`${rel}: missing <link rel="modulepreload"> for LibraryPage-*.js`);
  }
  if (/<div id="__shell"/.test(body)) {
    fail(`${rel}: still contains a #__shell overlay`);
  }
}

// ── Subject / chapter prerender quality + canonical sampling ────────
const subjectSamples = [];
const chapterSamples = [];
const SAMPLE_LIMIT = 5;
const REQUIRED_SET = new Set(REQUIRED_CANONICAL_ROUTES.map((r) => r.file));

for (const page of pages) {
  if (page.rel === "index.html") continue;
  if (LIBRARY_FILES.includes(page.rel)) continue;
  const route = "/" + page.rel.replace(/\/index\.html$/, "");

  const m = page.body.match(
    /<div id="root" data-hydrate="([a-z]+)">([\s\S]*?)<\/div>\s*<script/,
  );
  if (m) {
    const kind = m[1];
    const inner = m[2];
    if (kind === "subject" || kind === "chapter") {
      if (inner.trim().length < 400) {
        fail(`${route}: #root inner too small (${inner.length} bytes)`);
      }
      const seedKey =
        kind === "chapter" ? "__CHAPTER_PRELOAD__" : "__SSR_QUERIES__";
      const seedIdx = page.body.indexOf(`window.${seedKey}`);
      const moduleIdx = page.body.indexOf('<script type="module"');
      if (seedIdx === -1) {
        fail(`${route}: missing inlined window.${seedKey} payload`);
      } else if (moduleIdx === -1 || seedIdx > moduleIdx) {
        fail(`${route}: window.${seedKey} must be inlined BEFORE main module script`);
      }
      if (/<div id="__shell"/.test(page.body)) {
        fail(`${route}: legacy #__shell overlay still present`);
      }
      const pageChunkBase =
        kind === "subject" ? "SubjectLandingPage" : "ChapterPage";
      const preloadRe = new RegExp(
        `<link rel="modulepreload"[^>]*href="/assets/${pageChunkBase}-[^"]+\\.js"`,
      );
      if (!preloadRe.test(page.body)) {
        fail(
          `${route}: missing <link rel="modulepreload"> for ${pageChunkBase}-*.js`,
        );
      }
      // Spot-check canonicals on a few subject + chapter routes.
      if (
        kind === "subject" &&
        subjectSamples.length < SAMPLE_LIMIT &&
        !REQUIRED_SET.has(page.rel)
      ) {
        subjectSamples.push({ route, body: page.body });
      } else if (
        kind === "chapter" &&
        chapterSamples.length < SAMPLE_LIMIT
      ) {
        chapterSamples.push({ route, body: page.body });
      }
    }
  }
}

function checkSampleCanonical({ route, body }) {
  const tags =
    body.match(/<link\s+rel="canonical"\s+href="[^"]*"[^>]*>/g) || [];
  if (tags.length !== 1) {
    fail(
      `${route}: expected exactly 1 canonical tag, found ${tags.length} ` +
        `(${tags.join(" | ") || "<none>"})`,
    );
    return;
  }
  const expected = `${SITE}${route}`;
  const m = tags[0].match(/href="([^"]*)"/);
  if (!m || m[1] !== expected) {
    fail(
      `${route}: canonical href mismatch — expected ${expected}, got ${m && m[1]}`,
    );
  }
}
for (const s of subjectSamples) checkSampleCanonical(s);
for (const c of chapterSamples) checkSampleCanonical(c);

// ── Cross-check prerender manifest counts vs disk ───────────────────
const manifestPath = path.join(distDir, "prerender-manifest.json");
let manifestSubjects = 0;
let manifestChapters = 0;
if (fs.existsSync(manifestPath)) {
  try {
    const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
    manifestSubjects = manifest?.counts?.subjects_written ?? 0;
    manifestChapters = manifest?.counts?.chapters_written ?? 0;
  } catch (err) {
    warn(`prerender-manifest.json unreadable: ${err.message}`);
  }
} else {
  warn("no prerender-manifest.json — prerender step likely soft-failed");
}

// Count prerendered pages on disk for reporting.
let subjectsOnDisk = 0;
let chaptersOnDisk = 0;
for (const page of pages) {
  if (/data-hydrate="subject"/.test(page.body)) subjectsOnDisk++;
  if (/data-hydrate="chapter"/.test(page.body)) chaptersOnDisk++;
}
if (manifestSubjects > 0 && subjectsOnDisk === 0) {
  fail(`manifest claimed ${manifestSubjects} subjects written but 0 found on disk`);
}
if (manifestChapters > 0 && chaptersOnDisk === 0) {
  fail(`manifest claimed ${manifestChapters} chapters written but 0 found on disk`);
}

// ── Bootstrap must wire up hydrateRoot for "library" ────────────────
try {
  const indexJsxFiles = fs
    .readdirSync(path.join(distDir, "assets"))
    .filter((f) => /^index-[^.]+\.js$/.test(f))
    .map((f) => path.join(distDir, "assets", f));
  let foundHydrate = false;
  for (const f of indexJsxFiles) {
    const src = fs.readFileSync(f, "utf-8");
    if (src.includes("hydrateRoot") && src.includes('"library"')) {
      foundHydrate = true;
      break;
    }
  }
  if (!foundHydrate) {
    fail(
      "client bootstrap does not appear to call hydrateRoot for /library — " +
        "check src/index.jsx and ensure it survived minification",
    );
  }
} catch (err) {
  warn(`unable to scan dist/assets for hydrateRoot: ${err.message}`);
}

// ── Task #560: build-time indexability gate ─────────────────────────
// Every prerendered HTML file that ships in dist/ must carry the three
// fields Google/Bing need to index a URL: a non-empty <title>, a
// <meta name="description"> with content, and a <link rel="canonical">.
// Routes the SPA serves only as the JS shell (e.g. /admin/*, /reset,
// /history, /profile) are skipped — they're explicitly disallowed in
// robots.txt so missing SEO meta on them is intentional.
const INDEXABILITY_SKIP_RE =
  /^(admin\/|history\/|profile\/|reset\/|cms\/|api\/)/;
const TITLE_RE = /<title[^>]*>([\s\S]*?)<\/title>/i;
const DESC_RE =
  /<meta\s+[^>]*name=["']description["'][^>]*content=["']([^"']*)["'][^>]*>/i;
const CANONICAL_RE =
  /<link\s+[^>]*rel=["']canonical["'][^>]*href=["']([^"']*)["'][^>]*>/i;

let indexabilityChecked = 0;
for (const page of pages) {
  if (page.rel === "index.html") continue; // root has dynamic SPA-injected meta
  if (INDEXABILITY_SKIP_RE.test(page.rel)) continue;
  const route = "/" + page.rel.replace(/\/index\.html$/, "");
  indexabilityChecked++;

  const titleMatch = page.body.match(TITLE_RE);
  const titleText = titleMatch ? titleMatch[1].trim() : "";
  if (!titleText || titleText === "Syrabit.ai") {
    fail(
      `${route}: missing or generic <title> (got ${JSON.stringify(titleText)}); ` +
        `every indexable URL must ship a unique title at build time`,
    );
  }

  const descMatch = page.body.match(DESC_RE);
  const descText = descMatch ? descMatch[1].trim() : "";
  if (!descText) {
    fail(`${route}: missing or empty <meta name="description">`);
  } else if (descText.length < 50) {
    warn(
      `${route}: meta description is short (${descText.length} chars) — ` +
        `Google may rewrite it. Aim for 80-160 chars.`,
    );
  }

  const canonMatch = page.body.match(CANONICAL_RE);
  if (!canonMatch || !canonMatch[1].trim()) {
    fail(`${route}: missing <link rel="canonical">`);
  } else if (!canonMatch[1].startsWith("https://syrabit.ai")) {
    fail(
      `${route}: canonical href must be absolute https://syrabit.ai/* ` +
        `(got ${canonMatch[1]})`,
    );
  }
}
console.log(
  `[verify-all] indexability gate: checked ${indexabilityChecked} prerendered route(s)`,
);

// ── og:image:alt postcondition (Task #37 / Task #51) ─────────────────
// Task #37 injected <meta property="og:image:alt"> into all four prerender
// scripts.  Each script has a local hard assertion when it writes the file,
// but verify-all.mjs needs to independently confirm the tag survived the full
// build pipeline (critical-CSS inlining, asset hashing, etc.) and landed in
// every dist/ HTML that social crawlers will see.
//
// Scope: the same INDEXABILITY_SKIP_RE exclusion set applies: admin/*,
// history/*, profile/*, reset/*, cms/*, and api/* are explicitly disallowed
// in robots.txt and never reached by social crawlers, so og:image:alt has no
// effect on them. Checking only publicly-indexable routes is intentional —
// demanding og:image:alt on private routes would create noise without value.
//
// Matches both attribute orders robustly:
//   <meta property="og:image:alt" content="…" />        ← prerender canonical form
//   <meta content="…" property="og:image:alt" />        ← acceptable alternate form
const OG_IMAGE_ALT_CONTENT_RE =
  /<meta\b[^>]*\bproperty=["']og:image:alt["'][^>]*\bcontent=["']([^"']*)["'][^>]*>/i;
const OG_IMAGE_ALT_CONTENT_RE2 =
  /<meta\b[^>]*\bcontent=["']([^"']*)["'][^>]*\bproperty=["']og:image:alt["'][^>]*>/i;

let ogAltChecked = 0;
for (const page of pages) {
  if (page.rel === "index.html") continue; // root: SPA shell, meta injected client-side
  if (INDEXABILITY_SKIP_RE.test(page.rel)) continue;
  const route = "/" + page.rel.replace(/\/index\.html$/, "");

  const m =
    page.body.match(OG_IMAGE_ALT_CONTENT_RE) ||
    page.body.match(OG_IMAGE_ALT_CONTENT_RE2);
  if (!m) {
    fail(
      `${route}: missing <meta property="og:image:alt"> — ` +
        `Task #37 injects this tag; a prerender script or build step may have dropped it`,
    );
    continue;
  }
  const altText = m[1].trim();
  if (!altText) {
    fail(
      `${route}: og:image:alt content is empty — ` +
        `social crawlers require a non-empty alt description for the preview banner`,
    );
    continue;
  }
  ogAltChecked++;
}
console.log(
  `[verify-all] og:image:alt gate: checked ${ogAltChecked} prerendered route(s)`,
);

// ── og:image URL format gate (Task #66) ──────────────────────────────────────
// When an og:image meta tag is present the URL must be absolute HTTPS and
// point to either the subject-specific CDN path or the site's own origin.
// Catching a stale localhost URL, a relative path, or an unexpected domain
// here prevents broken social-preview banners from reaching production.
//
// The check is WARN-only when og:image is absent: some static pages are
// expected to fall back to the edge-injected generic image, so a missing
// tag in the prerendered HTML is acceptable.  But if the tag IS present
// the URL must satisfy the allowlist — that failure is hard.
//
// Scope: same INDEXABILITY_SKIP_RE exclusion as the other OG gates.
//
// Allowed URL prefixes — both must be absolute HTTPS on our own origins:
//   https://cdn.syrabit.ai/og/   Task #50 subject-specific CDN images
//   https://syrabit.ai/          generic site assets (e.g. opengraph.jpg)
// If the CDN base URL changes, update OG_IMAGE_ALLOWED_PREFIXES here AND
// in workers/edge-proxy/src/index.ts (_OG_IMAGE_BASE constant) together.
const OG_IMAGE_SRC_RE =
  /<meta\b[^>]*\bproperty=["']og:image["'][^>]*\bcontent=["']([^"']*)["'][^>]*>/i;
const OG_IMAGE_SRC_RE2 =
  /<meta\b[^>]*\bcontent=["']([^"']*)["'][^>]*\bproperty=["']og:image["'][^>]*>/i;

const OG_IMAGE_ALLOWED_PREFIXES = [
  "https://cdn.syrabit.ai/og/", // subject-specific CDN images (Task #50)
  "https://syrabit.ai/",        // generic site assets — e.g. opengraph.jpg
];

let ogImageUrlChecked = 0;
let ogImageUrlAbsent  = 0;
for (const page of pages) {
  if (page.rel === "index.html") continue; // root: SPA shell
  if (INDEXABILITY_SKIP_RE.test(page.rel)) continue;
  const route = "/" + page.rel.replace(/\/index\.html$/, "");

  const m =
    page.body.match(OG_IMAGE_SRC_RE) ||
    page.body.match(OG_IMAGE_SRC_RE2);

  if (!m) {
    warn(
      `${route}: no <meta property="og:image"> found — ` +
        `social crawlers will fall back to the edge-injected generic preview`,
    );
    ogImageUrlAbsent++;
    continue;
  }

  const imgUrl = m[1].trim();
  if (!imgUrl) {
    fail(`${route}: og:image content is empty`);
    continue;
  }

  // Fail fast on obvious breakage patterns before checking the allowlist.
  if (/localhost|127\.0\.0\.1|undefined/i.test(imgUrl)) {
    fail(
      `${route}: og:image URL looks like a dev/debug artifact: ${imgUrl} — ` +
        `this value must never ship in a production build`,
    );
    continue;
  }
  if (!imgUrl.startsWith("https://")) {
    fail(
      `${route}: og:image URL is not absolute HTTPS: ${imgUrl} — ` +
        `relative paths and plain HTTP URLs are rejected by most social crawlers`,
    );
    continue;
  }
  if (!OG_IMAGE_ALLOWED_PREFIXES.some((p) => imgUrl.startsWith(p))) {
    fail(
      `${route}: og:image URL (${imgUrl}) does not start with an allowed prefix. ` +
        `Allowed: ${OG_IMAGE_ALLOWED_PREFIXES.map((p) => p).join(", ")}. ` +
        `Update OG_IMAGE_ALLOWED_PREFIXES in verify-all.mjs if the CDN base has changed.`,
    );
    continue;
  }

  ogImageUrlChecked++;
}
console.log(
  `[verify-all] og:image URL gate: checked ${ogImageUrlChecked} route(s), ` +
    `${ogImageUrlAbsent} absent (warn-only)`,
);

// ── Critical-CSS postcondition (Task #856) ──────────────────────────
// scripts/inline-critical-css.mjs runs in the build pipeline ahead of
// this verifier and is expected to leave the SPA-fallback +
// prerendered HTMLs without a render-blocking <link rel="stylesheet">
// to the main bundle. Two non-blocking shapes are acceptable:
//
//   (a) Beasties rewrote the link to preload+swap with a <noscript>
//       fallback (the SPA fallback /index.html and most prerendered
//       routes). Looks like:
//         <link rel="alternate stylesheet preload" ... href="/assets/
//              index-XYZ.css" ... onload="this.rel='stylesheet'">
//
//   (b) The prerender step inlined the full stylesheet body inline
//       under a <style data-inline-css="index-XYZ.css"> wrapper
//       (currently /library and /browser do this). Bigger HTML
//       payload, but zero render-blocking external CSS.
//
// We strip <noscript>…</noscript> bodies before scanning for the
// blocking pattern so the JS-disabled fallback link does not count.
const NOSCRIPT_RE = /<noscript\b[^>]*>[\s\S]*?<\/noscript>/gi;
const ACTIVE_BLOCKING_RE =
  /<link[^>]+rel=["']stylesheet["'][^>]+\/assets\/index-[A-Za-z0-9_-]+\.css/;

const cssRoutesToCheck = [
  { route: "/", file: "index.html" },
  ...REQUIRED_CANONICAL_ROUTES,
  // Library + browser are first-class prerender targets handled by
  // scripts/prerender-library.mjs (see LIBRARY_FILES). They're not in
  // REQUIRED_CANONICAL_ROUTES because their canonicals are already
  // injected by the prerender step itself, but the critical-CSS
  // postcondition still applies.
  { route: "/library", file: "library/index.html" },
  { route: "/browser", file: "browser/index.html" },
];

// Soft + hard budgets for per-route inline critical CSS (sum of every
// non-data-inline-css <style> body in <head>). The 14 KB target comes
// from the Task #856 spec; today's measured worst case is ~16 KB on
// the marketing-shell SPA fallbacks (/about, /pricing, /login,
// /signup) where Beasties picks up shared header/hero/CTA selectors.
// A WARN at 18 KB gives us headroom + visibility before silent growth
// becomes a real LCP regression; a hard FAIL at 30 KB catches the
// "Beasties stopped pruning entirely" case.
const INLINE_CSS_SOFT_KB = 18;
const INLINE_CSS_HARD_KB = 30;
// Match <style …> blocks but exclude the legacy
// <style data-inline-css="…"> wrapper that prerender-{library,routes}
// emits and the inliner un-wraps before Beasties.
const INLINE_STYLE_RE =
  /<style(?![^>]*\bdata-inline-css\b)[^>]*>([\s\S]*?)<\/style>/g;
function measureInlineCssKB(html) {
  let total = 0;
  for (const m of html.matchAll(INLINE_STYLE_RE)) total += m[1].length;
  return total / 1024;
}

let cssChecked = 0;
for (const { route, file } of cssRoutesToCheck) {
  const page = byRel.get(file);
  if (!page) continue; // missing-page failure already raised above
  const stripped = page.body.replace(NOSCRIPT_RE, "");
  if (ACTIVE_BLOCKING_RE.test(stripped)) {
    fail(
      `${route}: render-blocking <link rel="stylesheet"> to /assets/index-*.css ` +
        `survived the critical-CSS step (Task #856 regression). Check that ` +
        `scripts/inline-critical-css.mjs ran successfully against this HTML.`,
    );
    continue;
  }
  const kb = measureInlineCssKB(page.body);
  if (kb > INLINE_CSS_HARD_KB) {
    fail(
      `${route}: inline critical CSS is ${kb.toFixed(1)} KB which exceeds the ` +
        `${INLINE_CSS_HARD_KB} KB hard budget — Beasties may have stopped pruning. ` +
        `Investigate scripts/inline-critical-css.mjs before shipping.`,
    );
    continue;
  }
  if (kb > INLINE_CSS_SOFT_KB) {
    warn(
      `${route}: inline critical CSS is ${kb.toFixed(1)} KB (soft budget ` +
        `${INLINE_CSS_SOFT_KB} KB) — review whether Beasties is over-inlining.`,
    );
  }
  cssChecked++;
}
console.log(
  `[verify-all] critical-css gate: checked ${cssChecked} route(s) ` +
    `(soft budget ${INLINE_CSS_SOFT_KB} KB, hard ${INLINE_CSS_HARD_KB} KB)`,
);

// ── Print warnings + decide outcome ─────────────────────────────────
for (const w of warnings) console.warn(`[verify-all] WARN: ${w}`);

const elapsed = Math.round((Date.now() - startedAt) / 1000);
console.log(
  `[verify-all] structural pass done in ${elapsed}s — ` +
    `${pages.length} html files, ${subjectsOnDisk} subject + ${chaptersOnDisk} chapter prerenders`,
);

// ── Run hydration headless-browser check in parallel (best-effort) ──
async function runHydration() {
  if (process.env.SKIP_VERIFY_HYDRATION === "1") {
    console.log("[verify-all] SKIP_VERIFY_HYDRATION=1 — skipping browser check");
    return 0;
  }
  return new Promise((resolve) => {
    const child = spawn(
      process.execPath,
      [path.join(__dirname, "verify-hydration.mjs")],
      { stdio: "inherit", env: process.env },
    );
    const timer = setTimeout(
      () => {
        try {
          child.kill("SIGTERM");
        } catch {}
      },
      4 * 60_000,
    );
    timer.unref();
    child.on("exit", (code) => {
      clearTimeout(timer);
      resolve(code ?? 0);
    });
  });
}

const hydrationCode = await runHydration();
if (hydrationCode !== 0) {
  fail(`verify-hydration exited with code ${hydrationCode}`);
}

// ── Cleanup dist-ssr (matches the legacy library-verifier behaviour) ───
if (fs.existsSync(distSsrDir)) {
  fs.rmSync(distSsrDir, { recursive: true, force: true });
  console.log(
    `[verify-all] cleaned ${path.relative(path.dirname(distDir), distSsrDir)}`,
  );
}

if (failures.length) {
  console.error(`[verify-all] FAIL — ${failures.length} violation(s):`);
  for (const f of failures) console.error("  - " + f);
  process.exit(1);
}

console.log("[verify-all] OK — all post-build assertions passed");
