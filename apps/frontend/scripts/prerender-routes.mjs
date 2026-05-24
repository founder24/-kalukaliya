// Statically prerender top-N subject and chapter routes (Task #385).
//
// Builds on Task #382's /library prerender plumbing. For each
// prerendered route this script:
//   1. Fetches the data the page needs (resolve-subject + chapters
//      for subject pages; chapter-by-slug for chapter pages).
//   2. Imports the SSR build (`dist-ssr/entry-server.js`), seeds
//      React Query (subject pages) or globalThis (chapter pages, which
//      use local state), and renders the route to a string.
//   3. Injects the SSR markup into a copy of `dist/index.html`,
//      replaces #root with the rendered tree (tagged with
//      data-hydrate="subject" or "chapter"), inlines the seed payload
//      so the client can hydrate without flicker, rewrites SEO meta,
//      and writes `dist/<route-path>/index.html`.
//
// Cloudflare Pages serves the deepest static file match first, so the
// new HTML is returned instantly (no SPA boot) for crawlers and
// browsers alike. Falls back to the SPA shell when the backend is
// unreachable so the build never hard-fails on a transient network.
//
// Selection order (Task #388): the script asks the backend for the
// most-visited subject + chapter routes over the last
// PRERENDER_TRAFFIC_DAYS days (default 30) and uses that ranking to
// pick which routes to prerender. Routes not present in the analytics
// rollup fall back to bundle order, so a brand-new deployment with no
// traffic data still ships a sensible set of pages.
//
// Limits are env-tunable so we can scale up gradually:
//   PRERENDER_SUBJECTS_LIMIT          (default 999 - all subjects)
//   PRERENDER_CHAPTERS_PER_SUBJECT    (default 999 - all chapters)
//   PRERENDER_TRAFFIC_DAYS            (default 30)
//   PRERENDER_BACKEND_URL / VITE_BACKEND_URL  (default https://syrabit.ai)
//
// Task #544: defaults were lowered to 20 subjects / 3 chapters to keep
// the worklist under ~80 routes within the 12-min wall budget.
// Task #2 (SEO Quick Wins): raised back to 50 subjects / 6 chapters
// (50 + 50x6 = 350 routes).
// SEO/GEO/AEO: raised to 999/999 to cover all 1000+ routes. The
// 30-min PRERENDER_BUDGET_MS wall-clock cap soft-fails gracefully on
// budget overrun, serving the SPA shell for un-prerendered routes.
// Non-prerendered routes are served via ISR in the edge worker for bots.
// Override via env:
//   PRERENDER_BUDGET_MS=<ms>  (max 30 min)
// if the full route set exceeds 30 min on a cold Cloudflare build.

import fs from "fs";
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";
import {
  loadLibraryBundle,
  loadTopRoutes,
  BACKEND as SHARED_BACKEND,
  FETCH_TIMEOUT_MS as SHARED_TIMEOUT_MS,
} from "./_prerender-data.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(__dirname, "..", "dist");
const distSsrDir = path.resolve(__dirname, "..", "dist-ssr");
const srcHtml = path.join(distDir, "index.html");
const ssrEntry = path.join(distSsrDir, "entry-server.js");

const BACKEND = SHARED_BACKEND;
const SUBJECTS_LIMIT = parseInt(
  process.env.PRERENDER_SUBJECTS_LIMIT || "999",
  10,
);
const CHAPTERS_PER_SUBJECT = parseInt(
  process.env.PRERENDER_CHAPTERS_PER_SUBJECT || "999",
  10,
);
const TRAFFIC_DAYS = parseInt(
  process.env.PRERENDER_TRAFFIC_DAYS || "30",
  10,
);
// Defensive env-knob parser — clamps to [min, max] and falls back to
// `fallback` for non-numeric / out-of-range input so a typo in the
// Pages dashboard can't silently degrade the build.
function envInt(name, fallback, { min = 1, max = Number.MAX_SAFE_INTEGER } = {}) {
  const raw = process.env[name];
  if (raw === undefined || raw === "") return fallback;
  const n = Number.parseInt(raw, 10);
  if (!Number.isFinite(n) || n < min || n > max) {
    console.warn(
      `[prerender-routes] ignoring invalid ${name}=${raw} (using ${fallback})`,
    );
    return fallback;
  }
  return n;
}

// Task #535: default lowered to 3000 ms (shared with _prerender-data
// cache loader). Per-request abort — failed fetches do NOT retry.
const FETCH_TIMEOUT_MS = envInt(
  "PRERENDER_FETCH_TIMEOUT_MS",
  SHARED_TIMEOUT_MS,
  { min: 500, max: 60_000 },
);
// Task #522: bounded concurrency for backend fan-out. The previous
// fully-serial loop (50 subjects × up to 7 fetches each = 350 serial
// network round-trips, each capped at 8s) could blow past Cloudflare's
// 35-min build wall whenever Railway was cold or rate-limiting.
const FETCH_CONCURRENCY = envInt("PRERENDER_FETCH_CONCURRENCY", 8, {
  min: 1, max: 64,
});
// Global wall-clock budget for the entire prerender pass. If we exceed
// it (e.g. backend hard-down), we soft-fail with whatever we managed to
// produce so far — the SPA shell still serves the rest.
const PRERENDER_BUDGET_MS = envInt("PRERENDER_BUDGET_MS", 30 * 60 * 1000, {
  min: 60_000, max: 30 * 60 * 1000,
});

async function pMap(items, mapper, concurrency = FETCH_CONCURRENCY) {
  const out = new Array(items.length);
  let i = 0;
  const workers = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (true) {
      const idx = i++;
      if (idx >= items.length) return;
      out[idx] = await mapper(items[idx], idx);
    }
  });
  await Promise.all(workers);
  return out;
}

function escapeHtml(s = "") {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function fetchJson(url) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      signal: ctrl.signal,
      headers: { Accept: "application/json" },
    });
    if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

// React Query's setQueryData rejects undefined values, so strip them
// from inlined payloads. Keeps the wire format identical between SSR
// and client hydration.
function clean(obj) {
  if (Array.isArray(obj)) return obj.map(clean);
  if (obj && typeof obj === "object") {
    const out = {};
    for (const [k, v] of Object.entries(obj)) {
      if (v !== undefined) out[k] = clean(v);
    }
    return out;
  }
  return obj;
}

function rewriteHead(html, { title, description, canonical, ogImageAlt }) {
  html = html.replace(
    /<title>[^<]*<\/title>/,
    `<title>${escapeHtml(title)}</title>`,
  );
  html = html.replace(
    /<meta name="description" content="[^"]*"\s*\/?>(\n)?/,
    `<meta name="description" content="${escapeHtml(description)}" />\n    `,
  );
  // Task #494 / Task #538: strip ALL existing canonical tags globally, then
  // inject the single authoritative canonical + hreflang before </head>.
  html = html.replace(/<link rel="canonical" href="[^"]*"\s*\/?>(\n)?/g, "");
  html = html.replace(
    /<\/head>/,
    `    <link rel="canonical" href="${canonical}" />\n` +
    `    <link rel="alternate" hreflang="en-IN" href="${canonical}" />\n  </head>`,
  );
  html = html.replace(
    /<meta property="og:url" content="[^"]*"\s*\/?>/,
    `<meta property="og:url" content="${canonical}" />`,
  );
  html = html.replace(
    /<meta property="og:title" content="[^"]*"\s*\/?>/,
    `<meta property="og:title" content="${escapeHtml(title)}" />`,
  );
  html = html.replace(
    /<meta property="og:description" content="[^"]*"\s*\/?>/,
    `<meta property="og:description" content="${escapeHtml(description)}" />`,
  );
  if (ogImageAlt) {
    html = html.replace(
      /<meta property="og:image:alt" content="[^"]*"\s*\/?>/,
      `<meta property="og:image:alt" content="${escapeHtml(ogImageAlt)}" />`,
    );
  }
  html = html.replace(
    /<meta name="twitter:title" content="[^"]*"\s*\/?>/,
    `<meta name="twitter:title" content="${escapeHtml(title)}" />`,
  );
  html = html.replace(
    /<meta name="twitter:description" content="[^"]*"\s*\/?>/,
    `<meta name="twitter:description" content="${escapeHtml(description)}" />`,
  );
  // Task #38: inject twitter:image and twitter:image:alt so the edge-proxy
  // HTMLRewriter has a tag to rewrite and X/Twitter link previews use the
  // route-specific banner rather than falling back to nothing.
  if (/<meta name="twitter:image" content="[^"]*"\s*\/?>/.test(html)) {
    html = html.replace(
      /<meta name="twitter:image" content="[^"]*"\s*\/?>/,
      `<meta name="twitter:image" content="https://syrabit.ai/opengraph.jpg" />`,
    );
  } else {
    html = html.replace(
      /<\/head>/,
      `    <meta name="twitter:image" content="https://syrabit.ai/opengraph.jpg" />\n  </head>`,
    );
  }
  if (ogImageAlt) {
    if (/<meta name="twitter:image:alt" content="[^"]*"\s*\/?>/.test(html)) {
      html = html.replace(
        /<meta name="twitter:image:alt" content="[^"]*"\s*\/?>/,
        `<meta name="twitter:image:alt" content="${escapeHtml(ogImageAlt)}" />`,
      );
    } else {
      html = html.replace(
        /<\/head>/,
        `    <meta name="twitter:image:alt" content="${escapeHtml(ogImageAlt)}" />\n  </head>`,
      );
    }
  }
  return html;
}

// P0 #1 of the AI-visibility plan — inject schema.org FAQPage JSON-LD
// directly into the prerendered <head> as a static <script> tag so AI
// crawlers (Googlebot, Perplexity, ChatGPT, Claude) see it on first
// byte without executing JavaScript.
//
// PageMeta also emits the same JSON-LD client-side via syncJsonLd, but
// uses a `data-pm` marker to tag its own scripts. The script we inject
// here CARRIES the same marker so PageMeta's client-side cleanup
// (`querySelectorAll("script[type='application/ld+json'][data-pm]")
// .forEach(remove)`) replaces this static script with the React-built
// equivalent on hydration — no duplicate FAQPage scripts, no SEO
// penalty for "double markup".
function injectBreadcrumbJsonLd(html, items) {
  if (!Array.isArray(items) || items.length < 2) return html;
  const ld = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": items.map((item, i) => ({
      "@type": "ListItem",
      "position": i + 1,
      "name": item.name,
      "item": item.url,
    })),
  };
  const json = JSON.stringify(ld).replace(/<\//g, "<\\/");
  const tag = `    <script type="application/ld+json" data-pm="1">${json}</script>\n  `;
  if (html.includes("</head>")) {
    return html.replace("</head>", `${tag}</head>`);
  }
  return html;
}

function injectCourseJsonLd(html, { subjectName, boardName, className, canonical, chaptersCount }) {
  const ld = {
    "@context": "https://schema.org",
    "@type": "Course",
    "name": `${subjectName} - ${boardName} ${className}`,
    "description": `Complete ${subjectName} study material for ${boardName} ${className} students. ${chaptersCount} chapters with notes, MCQs, and AI-powered learning.`,
    "provider": {
      "@type": "Organization",
      "name": "Syrabit.ai",
      "url": "https://syrabit.ai",
    },
    "url": canonical,
    "educationalLevel": `${className} ${boardName}`.trim(),
    "courseWorkload": `${chaptersCount} chapters of study material`,
    "hasCourseInstance": {
      "@type": "CourseInstance",
      "courseMode": "online",
      "courseWorkload": `${chaptersCount} chapters`,
    },
    "inLanguage": "en-IN",
    "isAccessibleForFree": true,
  };
  const json = JSON.stringify(ld).replace(/<\//g, "<\\/");
  const tag = `    <script type="application/ld+json" data-pm="1">${json}</script>\n  `;
  if (html.includes("</head>")) {
    return html.replace("</head>", `${tag}</head>`);
  }
  return html;
}

function injectFaqJsonLdIntoHead(html, faqEntries) {
  if (!Array.isArray(faqEntries) || faqEntries.length < 2) return html;
  const mainEntity = faqEntries.slice(0, 10).map((e) => ({
    "@type": "Question",
    name: String(e.question || "").trim(),
    acceptedAnswer: {
      "@type": "Answer",
      text: String(e.answer || "").trim(),
    },
  })).filter((q) => q.name && q.acceptedAnswer.text);
  if (mainEntity.length < 2) return html;
  const ld = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity,
  };
  // Escape `</` so a stray sequence in user content can't close our
  // script tag; mirrors the SSR queries inline-script escape pattern.
  const json = JSON.stringify(ld).replace(/<\//g, "<\\/");
  const tag =
    `    <script type="application/ld+json" data-pm="1">${json}</script>\n  `;
  if (html.includes("</head>")) {
    return html.replace("</head>", `${tag}</head>`);
  }
  return html;
}

// Task #395: page-chunk preload helper, lazily resolved on first use
// so the import cost doesn't hit unrelated routes.
let _pageChunkHelper = null;
async function pageChunkHelper() {
  if (!_pageChunkHelper) {
    _pageChunkHelper = await import(
      pathToFileURL(path.join(__dirname, "_page-chunk-preload.mjs")).href
    );
  }
  return _pageChunkHelper;
}

const HYDRATE_KIND_TO_CHUNK_BASE = {
  subject: "SubjectLandingPage",
  chapter: "ChapterPage",
  library: "LibraryPage",
  chat: "ChatPage",
};

// Task #496: per-hydrate-kind modulepreload allow-list. Vite emits a
// `<link rel="modulepreload">` for every static dep of the entry chunk
// in dist/index.html. For prerendered subject + chapter snapshots,
// that pulls in 80-150 KB of JS the route never executes on the
// critical path (chat code playground, alternate page chunks, framer,
// etc.), inflating mobile TBT and the "Reduce unused JavaScript"
// Lighthouse opportunity (audited 2026-04-18: ~617 KB on chapter,
// ~646 KB on subject). The patterns below match
// prerender-library.mjs's NON_LIBRARY_PRELOAD_PATTERNS but tuned per
// route — chapter pages legitimately need markdown / syntax /
// StickyToc, subject landings do not.
const PRELOAD_STRIP_BY_KIND = {
  subject: [
    /sandpack/i,
    /^markdown-/i,
    /MarkdownC/i,
    /framer/i,
    /^syntax-/i,
    /ChatPa/,
    /ChapterPa/,
    /StickyToc/,
    /^badge-/,
    /^skeleton-/,
    /LibraryPa/,
  ],
  chapter: [
    /sandpack/i,
    /framer/i,
    /ChatPa/,
    /LibraryPa/,
    /^badge-/,
    /CmsDocsSection/,
    /CmsPostsGrid/,
  ],
};

function stripUnusedModulepreloads(html, hydrateKind) {
  const patterns = PRELOAD_STRIP_BY_KIND[hydrateKind];
  if (!patterns || !patterns.length) return html;
  return html.replace(
    /\s*<link rel="modulepreload"[^>]*href="\/assets\/([^"]+)"[^>]*>/g,
    (match, file) =>
      patterns.some((re) => re.test(file)) ? "" : match,
  );
}

// Task #496: inline the main app CSS the same way prerender-library.mjs
// does. Subject and chapter snapshots are fully prerendered (SSR
// markup inside #root), so the external CSS link is a hard
// render-blocking dependency on the critical path. Inlining cuts a
// ~300 ms round-trip on slow 3G mobile and removes the Lighthouse
// "Render blocking requests" finding for these routes. The cost is one
// extra HTML payload (~70 KB raw → ~14 KB gzipped) but it's cached by
// the page's edge SWR policy after the first hit.
function inlineMainCssOnce(html, distDir) {
  const cssLinkRe = /<link rel="stylesheet"[^>]*href="\/assets\/([^"]+\.css)"[^>]*>/;
  const m = html.match(cssLinkRe);
  if (!m) return html;
  const cssPath = path.join(distDir, "assets", m[1]);
  if (!fs.existsSync(cssPath)) return html;
  const cssContent = fs.readFileSync(cssPath, "utf-8");
  const out = html.replace(
    cssLinkRe,
    `<style data-inline-css="${m[1]}">${cssContent}</style>`,
  );
  console.log(
    `[prerender-routes] inlined ${m[1]} (${cssContent.length} bytes) — removed render-blocking CSS for subject + chapter snapshots`,
  );
  return out;
}

function injectShell(htmlTemplate, { ssrHtml, hydrateKind, inlineScripts, pageChunkPreload }) {
  const startMarker =
    `<noscript><style>#__shell{display:none!important}</style></noscript>`;
  const startIdx = htmlTemplate.indexOf(startMarker);
  const rootRe = /<div id="root"[^>]*><\/div>/;
  const rootMatch = htmlTemplate.match(rootRe);
  if (startIdx === -1 || !rootMatch) {
    throw new Error(
      "[prerender-routes] could not locate shell markers in dist/index.html — structure changed?",
    );
  }
  let html =
    htmlTemplate.slice(0, startIdx) +
    htmlTemplate.slice(rootMatch.index).replace(
      rootRe,
      `<div id="root" data-hydrate="${hydrateKind}">${ssrHtml}</div>`,
    );

  if (inlineScripts && inlineScripts.length) {
    const blob = inlineScripts.join("\n    ");
    html = html.replace(
      /<script type="module"/,
      `${blob}\n    <script type="module"`,
    );
  }

  if (pageChunkPreload && pageChunkPreload.injectFn && pageChunkPreload.chunkFile) {
    html = pageChunkPreload.injectFn(html, pageChunkPreload.chunkFile);
  }
  return html;
}

function writeRoute(routePath, html) {
  const outDir = path.join(distDir, routePath.replace(/^\//, ""));
  fs.mkdirSync(outDir, { recursive: true });
  const outHtml = path.join(outDir, "index.html");
  fs.writeFileSync(outHtml, html);
  // Task #38: hard assertions — twitter:image and twitter:image:alt must be
  // present in every prerendered snapshot so the edge-proxy HTMLRewriter can
  // find and replace them with the route-specific subject banner.
  const written = fs.readFileSync(outHtml, "utf-8");
  if (!/<meta name="twitter:image" content="[^"]+"/.test(written)) {
    throw new Error(
      `[prerender-routes] twitter:image assertion failed for ${routePath}: tag missing or empty`,
    );
  }
  if (!/<meta name="twitter:image:alt" content="[^"]+"/.test(written)) {
    throw new Error(
      `[prerender-routes] twitter:image:alt assertion failed for ${routePath}: tag missing or empty`,
    );
  }
  return outHtml;
}

function pickSubjectFields(subject) {
  const keys = [
    "id", "_id", "name", "slug", "description", "icon", "color",
    "stream_id", "class_id", "board_id",
    "board_name", "class_name", "stream_name",
    "board_slug", "class_slug", "stream_slug",
    "chapter_count", "tags",
  ];
  const out = {};
  for (const k of keys) if (subject[k] !== undefined) out[k] = subject[k];
  return out;
}

function pickChapterListFields(ch) {
  const keys = [
    "id", "_id", "title", "slug", "description",
    "content_type", "order", "chapter_id",
  ];
  const out = {};
  for (const k of keys) if (ch[k] !== undefined) out[k] = ch[k];
  return out;
}

// Trim chapter payload to the fields ChapterPage actually reads, so
// the inlined script stays small (LCP-critical).
function pickChapterPayload(c) {
  const keys = [
    "chapter_id", "title", "topic_title", "chapter_title",
    "subject_name", "board_name", "class_name", "stream_name",
    "subject_slug", "board_slug", "class_slug", "stream_slug",
    "chapter_slug", "content", "content_as", "content_type",
    "has_assamese", "meta_description", "word_count",
    "generated_at", "updated_at", "bing_keywords",
    // P0 #1 of the AI-visibility plan — FAQPage JSON-LD seed pulled
    // from /api/content/chapters/{id}/faq-jsonld and merged into the
    // chapter preload below. Listed here so the keep-list filter
    // doesn't strip it.
    "faq_entries",
    // Task #914 Step 3 — published topics seed for the per-topic
    // AI answer cards. Baking these into the preload means the
    // first-byte HTML already contains the citable definitions and
    // attribution sentences, so AI crawlers don't need to execute
    // JS to see them.
    "published_topics",
  ];
  const out = {};
  for (const k of keys) if (c[k] !== undefined) out[k] = c[k];
  return out;
}

// Fetch FAQPage entries (built from MCQ Q+A) for a chapter so the
// prerendered HTML ships schema.org FAQPage JSON-LD on first byte —
// this is what crawlers (Googlebot, Perplexity, ChatGPT) see, and
// missing it is the gap P0 #1 of the AI-visibility plan addresses.
//
// Returns `null` (not throws) on any failure so a backend hiccup or a
// chapter without parseable MCQs never blocks the rest of the chapter
// snapshot. The runtime useEffect in ChapterPage will still try to
// fetch on the client for non-prerendered routes.
async function fetchChapterFaqEntries(chapterId) {
  if (!chapterId) return null;
  const url = `${BACKEND.replace(/\/$/, "")}/api/content/chapters/${encodeURIComponent(chapterId)}/faq-jsonld`;
  try {
    const payload = await fetchJson(url);
    const entries = Array.isArray(payload?.entries) ? payload.entries : null;
    if (!entries || entries.length < 2) return null; // chapterSchema() also requires >= 2
    return entries;
  } catch {
    // 404 (no parseable MCQs) and transient network errors are both fine —
    // we just skip baking FAQ into this chapter snapshot.
    return null;
  }
}

// Task #914 Step 3 — fetch the published-topics list (already
// filtered server-side to those with `definition_status=ok`) so the
// prerendered HTML ships every AI answer card on first byte. Same
// failure semantics as fetchChapterFaqEntries: any error returns
// null and the runtime useEffect in ChapterPage takes over for the
// SPA path.
async function fetchChapterPublishedTopics(chapterId) {
  if (!chapterId) return null;
  const url = `${BACKEND.replace(/\/$/, "")}/api/content/chapters/${encodeURIComponent(chapterId)}/topics-published`;
  try {
    const payload = await fetchJson(url);
    const topics = Array.isArray(payload?.topics) ? payload.topics : null;
    if (!topics || topics.length === 0) return null;
    return topics;
  } catch {
    return null;
  }
}

// Topical-mapping (Task: topical mapping + topical authority) —
// bake the related-topic graph (siblings + cross-chapter) into the
// chapter preload so SSR / curl-no-JS already ships the full
// internal-linking graph on first byte. Mirrors the runtime
// useEffect in ChapterPage; same null-on-failure semantics.
async function fetchChapterTopicsRelated(chapterId) {
  if (!chapterId) return null;
  const url = `${BACKEND.replace(/\/$/, "")}/api/content/chapters/${encodeURIComponent(chapterId)}/topics-related?limit=12`;
  try {
    const payload = await fetchJson(url);
    if (!payload || typeof payload !== "object") return null;
    const siblings = Array.isArray(payload.siblings) ? payload.siblings : [];
    const crossChapter = Array.isArray(payload.cross_chapter) ? payload.cross_chapter : [];
    if (siblings.length === 0 && crossChapter.length === 0) return null;
    return { siblings, cross_chapter: crossChapter };
  } catch {
    return null;
  }
}

// Topical-mapping pillar — bake the subject's full topic index into
// the SubjectLandingPage preload via `window.__SUBJECT_PRELOAD__`.
// Same null-on-failure semantics as the chapter helpers.
async function fetchSubjectTopicIndex(subjectId) {
  if (!subjectId) return null;
  const url = `${BACKEND.replace(/\/$/, "")}/api/content/subjects/${encodeURIComponent(subjectId)}/topic-index`;
  try {
    const payload = await fetchJson(url);
    if (!payload || typeof payload !== "object") return null;
    const chapters = Array.isArray(payload.chapters) ? payload.chapters : [];
    if (chapters.length === 0) return null;
    return {
      chapters,
      total_topics: Number(payload.total_topics || 0),
    };
  } catch {
    return null;
  }
}

function enumerateSubjectRoutes(bundle) {
  const boards = new Map((bundle.boards || []).map((b) => [b.id, b]));
  const classes = new Map((bundle.classes || []).map((c) => [c.id, c]));
  const streams = new Map((bundle.streams || []).map((s) => [s.id, s]));
  const routes = [];
  for (const subject of bundle.subjects || []) {
    const stream = streams.get(subject.stream_id);
    if (!stream) continue;
    const cls = classes.get(stream.class_id);
    if (!cls) continue;
    const board = boards.get(cls.board_id);
    if (!board) continue;
    if (!subject.slug || !cls.slug || !board.slug) continue;
    routes.push({
      board: board.slug,
      classSlug: cls.slug,
      streamSlug: stream.slug || null,
      subjectSlug: subject.slug,
      subject,
    });
  }
  return routes;
}

async function resolvePageChunkPreload(hydrateKind) {
  const baseName = HYDRATE_KIND_TO_CHUNK_BASE[hydrateKind];
  if (!baseName) return null;
  const helper = await pageChunkHelper();
  const chunkFile = helper.findPageChunk(distDir, baseName);
  if (!chunkFile) {
    throw new Error(
      `[prerender-routes] no ${baseName}-*.js chunk found in dist/assets — ` +
        `Task #395 contract requires a per-page chunk (hydrateKind=${hydrateKind})`,
    );
  }
  return { injectFn: helper.injectPageChunkPreload, chunkFile };
}

async function renderOne(renderRoute, htmlTemplate, opts) {
  const out = await renderRoute({
    url: opts.url,
    seed: opts.seed,
  });
  if (Array.isArray(out?.errors) && out.errors.length) {
    for (const e of out.errors) {
      console.warn(
        `[prerender-routes] SSR onError (${opts.url}):`,
        e?.stack || e?.message || e,
      );
    }
  }
  const ssrHtml = out?.html;
  if (typeof ssrHtml !== "string" || ssrHtml.length === 0) {
    throw new Error(
      `[prerender-routes] renderRoute(${opts.url}) returned empty html`,
    );
  }
  const pageChunkPreload = await resolvePageChunkPreload(opts.hydrateKind);
  const trimmedTemplate = stripUnusedModulepreloads(
    htmlTemplate,
    opts.hydrateKind,
  );
  const html = injectShell(trimmedTemplate, {
    ssrHtml,
    hydrateKind: opts.hydrateKind,
    inlineScripts: opts.inlineScripts,
    pageChunkPreload,
  });
  return rewriteHead(html, opts.head);
}

async function main() {
  if (!fs.existsSync(srcHtml)) {
    console.warn(
      `[prerender-routes] dist/index.html not found at ${srcHtml}; skipping`,
    );
    return;
  }
  if (!fs.existsSync(ssrEntry)) {
    throw new Error(
      `[prerender-routes] required SSR build missing at ${ssrEntry}; ` +
        `build pipeline must run "vite build --ssr src/entry-server.jsx --outDir dist-ssr" first`,
    );
  }

  // Browser-API polyfills mirroring scripts/prerender-library.mjs.
  if (typeof globalThis.localStorage === "undefined") {
    const noop = () => null;
    globalThis.localStorage = {
      getItem: noop, setItem: () => {}, removeItem: () => {},
      clear: () => {}, key: noop, length: 0,
    };
    globalThis.sessionStorage = globalThis.localStorage;
  }

  // Pull the slim library bundle to enumerate subject routes. If the
  // backend is unreachable we soft-fail (logged) instead of breaking
  // the build — Task #382 already ships the SPA shell as a safety net.
  // Task #535: shared cross-script cache. First script in the build
  // pays the network hop; the rest read from disk.
  const bundle = await loadLibraryBundle();
  if (!bundle) {
    console.warn(
      "[prerender-routes] library bundle unavailable; " +
        "skipping subject + chapter prerender (SPA shell will serve)",
    );
    return;
  }

  const allSubjectRoutes = enumerateSubjectRoutes(bundle);

  // Pull traffic ranking (Task #388). Each entry is `{ path, views }`
  // for the most-visited subject + chapter routes over the last
  // TRAFFIC_DAYS days. We build path → views maps for both subject
  // (3-segment) and chapter (4-segment) routes, then derive a subject
  // priority that combines the subject landing-page views with the
  // sum of all chapter views under that prefix. This means a subject
  // whose chapters are popular gets prerendered even when the bare
  // landing URL is rarely visited directly.
  // Anything missing from analytics keeps its bundle-order position so
  // a cold start still produces sensible output.
  const subjectViews = new Map();
  const chapterViews = new Map();
  try {
    const trafficPayload = await loadTopRoutes(TRAFFIC_DAYS, 1000);
    if (!trafficPayload) throw new Error("traffic ranking unavailable");
    const list = Array.isArray(trafficPayload?.routes) ? trafficPayload.routes : [];
    for (const row of list) {
      if (!row || typeof row.path !== "string") continue;
      const views = Number(row.views) || 0;
      const segs = row.path.replace(/^\//, "").split("/");
      if (segs.length === 3) {
        subjectViews.set(row.path, views);
      } else if (segs.length === 4) {
        chapterViews.set(row.path, views);
        const subjectPath = `/${segs[0]}/${segs[1]}/${segs[2]}`;
        subjectViews.set(
          subjectPath,
          (subjectViews.get(subjectPath) || 0) + views,
        );
      }
    }
    console.log(
      `[prerender-routes] traffic ranking: ${list.length} routes from last ${TRAFFIC_DAYS}d ` +
        `(${subjectViews.size} subject prefixes, ${chapterViews.size} chapters)`,
    );
  } catch (err) {
    console.warn(
      `[prerender-routes] traffic ranking unavailable (${err.message}); ` +
        `falling back to bundle order`,
    );
  }

  const haveTraffic = subjectViews.size > 0 || chapterViews.size > 0;
  const subjectScore = (route) => {
    const url = `/${route.board}/${route.classSlug}/${route.subjectSlug}`;
    return subjectViews.get(url) || 0;
  };
  const rankedSubjectRoutes = haveTraffic
    ? [...allSubjectRoutes].sort((a, b) => {
        const sa = subjectScore(a);
        const sb = subjectScore(b);
        if (sb !== sa) return sb - sa; // higher views first
        return allSubjectRoutes.indexOf(a) - allSubjectRoutes.indexOf(b);
      })
    : allSubjectRoutes;
  const subjectRoutes = rankedSubjectRoutes.slice(0, SUBJECTS_LIMIT);
  console.log(
    `[prerender-routes] ${subjectRoutes.length}/${allSubjectRoutes.length} subjects in scope ` +
      `(limit=${SUBJECTS_LIMIT}, chapters per subject=${CHAPTERS_PER_SUBJECT}, ` +
      `selection=${haveTraffic ? "traffic" : "bundle-order"})`,
  );

  const mod = await import(pathToFileURL(ssrEntry).href);
  const renderRoute = mod.renderRoute || mod.default;
  if (typeof renderRoute !== "function") {
    throw new Error(
      "[prerender-routes] entry-server.js did not export renderRoute()",
    );
  }

  let htmlTemplate = fs.readFileSync(srcHtml, "utf-8");
  // Task #496: inline CSS once on the shared template — both subject
  // and chapter snapshots inherit the non-render-blocking CSS load.
  htmlTemplate = inlineMainCssOnce(htmlTemplate, distDir);

  let subjectsWritten = 0;
  let chaptersWritten = 0;
  let subjectsFailed = 0;
  let chaptersFailed = 0;
  let budgetExceeded = false;

  const startedAt = Date.now();
  const overBudget = () => Date.now() - startedAt > PRERENDER_BUDGET_MS;

  await pMap(subjectRoutes, async (route) => {
    if (overBudget()) {
      budgetExceeded = true;
      return;
    }
    const { board, classSlug, subjectSlug, subject } = route;
    const url = `/${board}/${classSlug}/${subjectSlug}`;
    const canonical = `https://syrabit.ai${url}`;

    let resolved;
    let chapters;
    try {
      resolved = await fetchJson(
        `${BACKEND.replace(/\/$/, "")}/api/content/resolve-subject/${board}/${classSlug}/${subjectSlug}`,
      );
      const subjectId = resolved?.id || resolved?._id || subject.id;
      chapters = await fetchJson(
        `${BACKEND.replace(/\/$/, "")}/api/content/chapters/${subjectId}`,
      );
    } catch (err) {
      console.warn(
        `[prerender-routes] subject data fetch failed for ${url}: ${err.message}`,
      );
      subjectsFailed++;
      return;
    }

    const subjectName = resolved.name || subject.name || subjectSlug;
    const className = resolved.class_name || classSlug;
    const boardName = resolved.board_name || board;
    const subjectDataClean = clean(pickSubjectFields(resolved));
    const chaptersClean = clean(
      (chapters || []).map(pickChapterListFields),
    );

    const queries = [
      { key: ["resolve-subject", board, classSlug, subjectSlug], data: subjectDataClean },
    ];
    const subjectIdForKey = subjectDataClean.id || subjectDataClean._id;
    if (subjectIdForKey) {
      queries.push({ key: ["chapters", subjectIdForKey], data: chaptersClean });
    }

    const inlineScripts = [
      `<script>window.__SSR_QUERIES__=${JSON.stringify(queries).replace(/</g, "\\u003c")};</script>`,
    ];

    // Topical-mapping pillar — bake the full topic index for this
    // subject into the SSR pass via `seed.subjectPreload` (which
    // entry-server.jsx mirrors onto `globalThis.__SSR_SUBJECT_PRELOAD__`)
    // AND mirror the same payload onto `window.__SUBJECT_PRELOAD__`
    // for the client-side hydration / SPA navigation path. Failure is
    // non-fatal; the runtime useEffect on the SPA path takes over.
    let subjectPreload = null;
    if (subjectIdForKey) {
      const topicIndex = await fetchSubjectTopicIndex(subjectIdForKey);
      if (topicIndex) {
        subjectPreload = {
          subject_id: subjectIdForKey,
          topic_index: topicIndex,
        };
        inlineScripts.push(
          `<script>window.__SUBJECT_PRELOAD__=${JSON.stringify(subjectPreload).replace(/</g, "\\u003c")};</script>`,
        );
      }
    }

    try {
      const html = await renderOne(renderRoute, htmlTemplate, {
        url,
        seed: { queries, subjectPreload },
        hydrateKind: "subject",
        inlineScripts,
        head: {
          title:
            `${subjectName} Notes & Study Material | ${boardName} ${className}`,
          description:
            resolved.description ||
            `Complete ${subjectName} study material for ${boardName} ${className}. ` +
              `AI-powered notes, MCQs, important questions, and exam preparation.`,
          canonical,
          ogImageAlt: `${subjectName} — ${boardName} ${className} | Syrabit.ai`,
        },
      });
      let subjectHtml = html;
      subjectHtml = injectBreadcrumbJsonLd(subjectHtml, [
        { name: "Home", url: "https://syrabit.ai/" },
        { name: "Library", url: "https://syrabit.ai/library" },
        { name: subjectName, url: canonical },
      ]);
      subjectHtml = injectCourseJsonLd(subjectHtml, {
        subjectName,
        boardName,
        className,
        canonical,
        chaptersCount: chaptersClean.length,
      });
      const out = writeRoute(url, subjectHtml);
      subjectsWritten++;
      console.log(
        `[prerender-routes] subject ${url} → ${path.relative(distDir, out)} ` +
          `(${chaptersClean.length} chapters)`,
      );
    } catch (err) {
      console.warn(
        `[prerender-routes] subject render failed for ${url}: ${err.message}`,
      );
      subjectsFailed++;
      return;
    }

    // ── Chapter prerender for the same subject ────────────────────
    // Re-rank chapter candidates by real traffic (Task #388). Chapters
    // missing from the analytics rollup keep their bundle-order
    // position so we still prerender something useful for new
    // subjects with no recorded views yet.
    const chapterCandidates = (chapters || []).filter((c) => c.slug);
    const rankedChapters = haveTraffic
      ? [...chapterCandidates].sort((a, b) => {
          const va = chapterViews.get(`${url}/${a.slug}`) || 0;
          const vb = chapterViews.get(`${url}/${b.slug}`) || 0;
          if (vb !== va) return vb - va; // higher views first
          return chapterCandidates.indexOf(a) - chapterCandidates.indexOf(b);
        })
      : chapterCandidates;
    const candidateChapters = rankedChapters.slice(0, CHAPTERS_PER_SUBJECT);

    await pMap(candidateChapters, async (ch) => {
      if (overBudget()) {
        budgetExceeded = true;
        return;
      }
      const chapterSlug = ch.slug;
      const chapterUrl = `${url}/${chapterSlug}`;
      const chapterCanonical = `https://syrabit.ai${chapterUrl}`;

      let chapterPayload;
      try {
        chapterPayload = await fetchJson(
          `${BACKEND.replace(/\/$/, "")}/api/content/chapter-by-slug/${board}/${classSlug}/${subjectSlug}/${chapterSlug}`,
        );
      } catch (err) {
        console.warn(
          `[prerender-routes] chapter data fetch failed for ${chapterUrl}: ${err.message}`,
        );
        chaptersFailed++;
        return;
      }
      if (!chapterPayload || typeof chapterPayload !== "object") {
        chaptersFailed++;
        return;
      }

      const chapterData = clean(pickChapterPayload(chapterPayload));
      // P0 #1 of the AI-visibility plan — fetch FAQPage entries built
      // from the chapter's MCQs and bake them into BOTH:
      //   (a) the chapter preload, so the runtime useEffect in
      //       ChapterPage skips its fetch and the client-side
      //       PageMeta builds the same JSON-LD on first render
      //       (no double-fetch, no flash of missing schema)
      //   (b) a `<script type="application/ld+json">` injected into
      //       the prerendered HTML head below — this is what AI/SEO
      //       crawlers read on first byte (PageMeta currently emits
      //       JSON-LD only via client useEffect, which Googlebot
      //       executes but Perplexity/ChatGPT often do not).
      // Failure here is logged but never fatal: the runtime useEffect
      // in ChapterPage will still try to fetch on the client.
      const faqEntries = await fetchChapterFaqEntries(chapterData.chapter_id);
      if (faqEntries) {
        chapterData.faq_entries = faqEntries;
      }
      // Task #914 Step 3 — bake published topics so the answer
      // cards render server-side (no JS, no flash, single source
      // of truth for bots and humans).
      const publishedTopics = await fetchChapterPublishedTopics(chapterData.chapter_id);
      if (publishedTopics) {
        chapterData.published_topics = publishedTopics;
      }
      // Topical-mapping — bake siblings + cross-chapter related
      // topics into the preload so bots / curl-no-JS see the full
      // internal-linking graph in the SSR HTML.
      const topicsRelated = await fetchChapterTopicsRelated(chapterData.chapter_id);
      if (topicsRelated) {
        chapterData.topics_related = topicsRelated;
      }
      const preload = {
        board, classSlug, subjectSlug, chapterSlug,
        data: chapterData,
      };

      const chapterIdx = candidateChapters.findIndex(c => c.slug === chapterSlug);
      const prevCh = chapterIdx > 0 ? candidateChapters[chapterIdx - 1] : null;
      const nextCh = chapterIdx < candidateChapters.length - 1 ? candidateChapters[chapterIdx + 1] : null;
      preload.siblings_nav = {
        prev: prevCh ? { slug: prevCh.slug, title: prevCh.title } : null,
        next: nextCh ? { slug: nextCh.slug, title: nextCh.title } : null,
        subject: { slug: subjectSlug, name: subjectName, path: url },
      };

      const chapterTitle =
        chapterPayload.topic_title ||
        chapterPayload.chapter_title ||
        ch.title ||
        chapterSlug;
      const subjName = chapterPayload.subject_name || subjectName;
      const bName = chapterPayload.board_name || boardName;
      const cName = chapterPayload.class_name || className;

      const inlineChapterScripts = [
        `<script>window.__CHAPTER_PRELOAD__=${JSON.stringify(preload).replace(/</g, "\\u003c")};</script>`,
      ];

      try {
        let html = await renderOne(renderRoute, htmlTemplate, {
          url: chapterUrl,
          seed: { chapterPreload: preload },
          hydrateKind: "chapter",
          inlineScripts: inlineChapterScripts,
          head: {
            title: chapterPayload.content_type === 'mcq'
              ? `${chapterTitle} MCQs with Answers | ${subjName} ${bName} ${cName}`
              : `${chapterTitle} - ${subjName} Notes | ${bName} ${cName}`,
            description:
              chapterPayload.meta_description ||
              `${chapterTitle} - Complete ${subjName} notes for ${bName} ${cName}. Includes explanations, MCQs, important questions & AI-powered study material.`,
            canonical: chapterCanonical,
            ogImageAlt: `${chapterTitle} — ${subjName} | Syrabit.ai`,
          },
        });
        // P0 #1 — bake FAQPage JSON-LD into byte-zero HTML so AI
        // crawlers see Q+A structure without executing JS.
        if (faqEntries) {
          html = injectFaqJsonLdIntoHead(html, faqEntries);
        }
        html = injectBreadcrumbJsonLd(html, [
          { name: "Home", url: "https://syrabit.ai/" },
          { name: "Library", url: "https://syrabit.ai/library" },
          { name: subjName, url: `https://syrabit.ai${url}` },
          { name: chapterTitle, url: chapterCanonical },
        ]);
        const out = writeRoute(chapterUrl, html);
        chaptersWritten++;
        console.log(
          `[prerender-routes] chapter ${chapterUrl} → ${path.relative(distDir, out)}`,
        );
      } catch (err) {
        console.warn(
          `[prerender-routes] chapter render failed for ${chapterUrl}: ${err.message}`,
        );
        chaptersFailed++;
      }
    });
  });

  const elapsedSec = Math.round((Date.now() - startedAt) / 1000);
  console.log(
    `[prerender-routes] done in ${elapsedSec}s — subjects=${subjectsWritten} ok / ${subjectsFailed} failed; ` +
      `chapters=${chaptersWritten} ok / ${chaptersFailed} failed` +
      (budgetExceeded ? ` (BUDGET EXCEEDED at ${PRERENDER_BUDGET_MS}ms — soft-stopped)` : ""),
  );

  // Persist the manifest so the verify script (and future audits) can
  // walk the prerendered surface area without re-enumerating.
  const manifest = {
    generated_at: new Date().toISOString(),
    backend: BACKEND,
    limits: {
      subjects: SUBJECTS_LIMIT,
      chaptersPerSubject: CHAPTERS_PER_SUBJECT,
    },
    selection: {
      mode: haveTraffic ? "traffic" : "bundle-order",
      traffic_days: TRAFFIC_DAYS,
      ranked_subjects: subjectViews.size,
      ranked_chapters: chapterViews.size,
    },
    counts: {
      subjects_written: subjectsWritten,
      subjects_failed: subjectsFailed,
      chapters_written: chaptersWritten,
      chapters_failed: chaptersFailed,
    },
  };
  fs.writeFileSync(
    path.join(distDir, "prerender-manifest.json"),
    JSON.stringify(manifest, null, 2),
  );
}

main()
  .then(() => {
    // Force-exit so the orchestrator does not SIGTERM us after the
    // 5-min budget. Even after main() resolves, Node keeps the event
    // loop alive due to keep-alive HTTP sockets to the backend (see
    // Cloudflare Pages build log 2026-04-19: "[prerender-routes] done
    // in 37s" followed 4 minutes later by "exceeded 300000ms — sending
    // SIGTERM"). All useful work has already been written to disk by
    // the time we get here, so a clean exit is safe.
    process.exit(0);
  })
  .catch((err) => {
    // Soft-fail: a transient network blip on the build host should not
    // break the whole deployment. Subject + chapter routes will fall
    // back to the SPA shell on Cloudflare Pages, exactly as they did
    // before this script existed. (Task #385 — architect review)
    console.error("[prerender-routes] non-fatal failure:", err?.stack || err);
    process.exit(0);
  });
