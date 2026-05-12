// Statically prerender the /library page (Task #382).
//
// Build the slim library bundle into a real React SSR snapshot of the
// /library route, then write `dist/library/index.html` so Cloudflare
// Pages serves it directly. The shipped HTML contains the full React
// tree inside `#root` (with `data-hydrate="library"`), and the
// browser bundle calls `hydrateRoot` on it instead of `createRoot` —
// React adopts the existing DOM, no remount, no flash.
//
// Steps:
//   1. Read the post-build `dist/index.html` (already has CSS/JS asset
//      refs and modulepreload hints injected).
//   2. Fetch the slim library bundle from the backend so the SSR
//      render produces real subject cards, not a loading skeleton.
//      Falls back to the legacy data-less shell on failure so the
//      build never breaks.
//   3. Import the SSR build output (`dist-ssr/entry-server.js`),
//      render the /library route to a string, and inject it into
//      `#root` (replacing the empty placeholder div that the SPA
//      bootstrap uses).
//   4. Inject `<script>window.__LIBRARY_BUNDLE__=…</script>` BEFORE
//      the main module script so React Query is seeded with the same
//      data the SSR used — guarantees first client render matches the
//      prerendered DOM and hydrateRoot succeeds without a mismatch.
//   5. Update <title>, <meta description>, OG/Twitter tags, and
//      <link rel=canonical> for /library.

import fs from "fs";
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";
import { loadLibraryBundle } from "./_prerender-data.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(__dirname, "..", "dist");
const distSsrDir = path.resolve(__dirname, "..", "dist-ssr");
const srcHtml = path.join(distDir, "index.html");
const ssrEntry = path.join(distSsrDir, "entry-server.js");

// Routes to prerender. /browser is an alias of /library — see App.jsx
// route definitions and Task #386 (the marketing/PageSpeed URL).
const ROUTES = [
  { route: "/library", outDir: path.join(distDir, "library") },
  { route: "/browser", outDir: path.join(distDir, "browser") },
];

const MAX_PRERENDER_CARDS = 12;

const TITLE =
  "Assamboard Subject Library — Notes, MCQs, Definitions & Exam Prep";
const CANONICAL = "https://syrabit.ai/library";
const DESCRIPTION =
  "Explore Assam Board Class 11-12 and Degree subjects. AI-powered notes, MCQs, definitions, and exam preparation for Assam students.";

function escapeHtml(s = "") {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function fetchBundle() {
  // Task #535: shared cache — first call this build pays the network
  // hop, subsequent calls (from prerender-routes etc.) hit disk.
  const data = await loadLibraryBundle();
  if (!data) {
    console.warn(
      "[prerender-library] backend bundle unavailable; falling back to data-less shell",
    );
  }
  return data;
}

const OG_IMAGE_ALT =
  "Assamboard Subject Library — Notes, MCQs, Definitions & Exam Prep";

function rewriteHead(html) {
  html = html.replace(
    /<title>[^<]*<\/title>/,
    `<title>${escapeHtml(TITLE)}</title>`,
  );
  html = html.replace(
    /<meta name="description" content="[^"]*"\s*\/?>(\n)?/,
    `<meta name="description" content="${escapeHtml(DESCRIPTION)}" />\n    `,
  );
  // Task #494: static template no longer carries a placeholder canonical;
  // swap if present (legacy builds) else inject before </head>.
  if (/<link rel="canonical" href="[^"]*"\s*\/?>(\n)?/.test(html)) {
    html = html.replace(
      /<link rel="canonical" href="[^"]*"\s*\/?>(\n)?/,
      `<link rel="canonical" href="${CANONICAL}" />\n    `,
    );
  } else {
    html = html.replace(
      /<\/head>/,
      `    <link rel="canonical" href="${CANONICAL}" />\n` +
      `    <link rel="alternate" hreflang="en-IN" href="${CANONICAL}" />\n  </head>`,
    );
  }
  html = html.replace(
    /<meta property="og:url" content="[^"]*"\s*\/?>/,
    `<meta property="og:url" content="${CANONICAL}" />`,
  );
  html = html.replace(
    /<meta property="og:title" content="[^"]*"\s*\/?>/,
    `<meta property="og:title" content="${escapeHtml(TITLE)}" />`,
  );
  html = html.replace(
    /<meta property="og:description" content="[^"]*"\s*\/?>/,
    `<meta property="og:description" content="${escapeHtml(DESCRIPTION)}" />`,
  );
  html = html.replace(
    /<meta property="og:image:alt" content="[^"]*"\s*\/?>/,
    `<meta property="og:image:alt" content="${escapeHtml(OG_IMAGE_ALT)}" />`,
  );
  html = html.replace(
    /<meta name="twitter:title" content="[^"]*"\s*\/?>/,
    `<meta name="twitter:title" content="${escapeHtml(TITLE)}" />`,
  );
  html = html.replace(
    /<meta name="twitter:description" content="[^"]*"\s*\/?>/,
    `<meta name="twitter:description" content="${escapeHtml(DESCRIPTION)}" />`,
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
  if (/<meta name="twitter:image:alt" content="[^"]*"\s*\/?>/.test(html)) {
    html = html.replace(
      /<meta name="twitter:image:alt" content="[^"]*"\s*\/?>/,
      `<meta name="twitter:image:alt" content="${escapeHtml(OG_IMAGE_ALT)}" />`,
    );
  } else {
    html = html.replace(
      /<\/head>/,
      `    <meta name="twitter:image:alt" content="${escapeHtml(OG_IMAGE_ALT)}" />\n  </head>`,
    );
  }
  return html;
}

// Trim the bundle to only fields LibraryPage actually reads, so the
// inlined <script> stays small (every byte hurts mobile LCP).
function slimBundleForClient(bundle) {
  if (!bundle) return null;
  const pick = (obj, keys) => {
    const out = {};
    for (const k of keys) if (obj[k] !== undefined) out[k] = obj[k];
    return out;
  };
  return {
    boards: (bundle.boards || []).map((b) => pick(b, ["id", "name", "slug"])),
    classes: (bundle.classes || []).map((c) =>
      pick(c, ["id", "name", "slug", "board_id"]),
    ),
    streams: (bundle.streams || []).map((s) =>
      pick(s, ["id", "name", "slug", "class_id"]),
    ),
    subjects: (bundle.subjects || []).map((s) =>
      pick(s, [
        "id",
        "name",
        "slug",
        "description",
        "stream_id",
        "icon",
        "color",
        "chapter_count",
      ]),
    ),
    chapter_count: bundle.chapter_count ?? bundle.chapters?.length ?? 0,
  };
}

async function main() {
  if (!fs.existsSync(srcHtml)) {
    console.warn(
      `[prerender-library] dist/index.html not found at ${srcHtml}; skipping`,
    );
    return;
  }

  const bundle = await fetchBundle();
  const slim = slimBundleForClient(bundle);

  let html = fs.readFileSync(srcHtml, "utf-8");

  // Boundary markers between the pre-hydration shell block and #root.
  // We re-resolve their positions AFTER the baseHtml mutations
  // (modulepreload strip + CSS inlining + page-chunk preload inject)
  // because those mutations change byte offsets, and stale indices
  // would leave the legacy `<div id="__shell">` overlay in the
  // prerendered HTML.
  const startMarker =
    `<noscript><style>#__shell{display:none!important}</style></noscript>`;
  const rootRe = /<div id="root"[^>]*><\/div>/;
  if (html.indexOf(startMarker) === -1 || !rootRe.test(html)) {
    throw new Error(
      "[prerender-library] could not locate shell markers in dist/index.html — structure changed?",
    );
  }

  // Real SSR is mandatory. A bundle fetch failure is recoverable —
  // we still SSR with empty data and ship the React skeleton (which
  // hydrates correctly). But a missing/broken SSR build is a HARD
  // failure: the task contract is that /library ships as prebuilt
  // hydrated HTML, not a JS-only shell. (Task #382 — architect review)
  if (!fs.existsSync(ssrEntry)) {
    throw new Error(
      `[prerender-library] required SSR build missing at ${ssrEntry}; ` +
        `build pipeline must run "vite build --ssr src/entry-server.jsx --outDir dist-ssr" first`,
    );
  }

  // Minimal browser-API polyfills so the bundled SSR module can boot
  // in Node — the app guards most window/document access behind
  // useEffect, but a handful of helpers (anon-id init in utils/api,
  // language context init, etc.) call localStorage at module load.
  if (typeof globalThis.localStorage === "undefined") {
    const noop = () => null;
    globalThis.localStorage = {
      getItem: noop, setItem: () => {}, removeItem: () => {},
      clear: () => {}, key: noop, length: 0,
    };
    globalThis.sessionStorage = globalThis.localStorage;
  }

  const mod = await import(pathToFileURL(ssrEntry).href);
  const renderRoute = mod.renderRoute || mod.default;
  if (typeof renderRoute !== "function") {
    throw new Error(
      "[prerender-library] entry-server.js did not export renderRoute()",
    );
  }
  let baseHtml = html;

  // Task #391: strip modulepreload links for chunks that /library does not
  // need on the critical path. Vite auto-emits modulepreload hints for
  // every static dep of the entry chunk, including chunks only used by
  // /chat (sandpack-client) and /chapter (markdown). Removing them from
  // the prerendered library snapshot cuts ~80-150KB of speculative
  // downloads on mobile first paint without affecting code-split fetches
  // on later navigations (the imports still resolve, just on demand).
  const NON_LIBRARY_PRELOAD_PATTERNS = [
    /sandpack/i,         // chat code playground
    /^markdown-/i,       // chapter MD renderer chunk
    /MarkdownC/i,
    /framer/i,           // landing-only motion
    /^syntax-/i,         // chat syntax highlighter
    /ChatPa/,            // chat page chunk (if present)
    /ChapterPa/,         // chapter page chunk (if present)
    /StickyToc/,         // chapter TOC chunk (if present)
    /^badge-/,           // chat badge chunk (if present)
    /^skeleton-/,        // shared skeleton loader
    // Task #639: chunks that LibraryPage does not statically import.
    // Without these, Vite emits modulepreload hints inherited from
    // the entry chunk for code paths used by other routes (admin
    // tables, dialog/popover ancestry, charts, etc.) and the browser
    // burns a full RTT downloading them on /library mobile first paint.
    /^radix-/i,          // dialog/popover/sheet primitives — chat/login/forms only
    /^vendor-/i,         // residual vendor (web-vitals, tslib) — non-critical
    /^charts-/i,         // recharts/d3 — admin analytics only
    /^Admin/,            // any admin-only chunk
    /^Login/,            // login page chunk
    /^Signup/,           // signup chunk
    /^Pricing/,          // pricing chunk
    /Editor/,            // CMS editor chunks
    /^dep-axios/,        // axios is loaded lazily via the api wrapper
  ];
  baseHtml = baseHtml.replace(
    /\s*<link rel="modulepreload"[^>]*href="\/assets\/([^"]+)"[^>]*>/g,
    (match, file) => {
      return NON_LIBRARY_PRELOAD_PATTERNS.some((re) => re.test(file)) ? "" : match;
    },
  );

  // Task #391: inline the main app CSS and remove the external
  // <link rel="stylesheet">. The library page is fully prerendered
  // (SSR snapshot inside #root), so the CSS file is a hard
  // render-blocking dependency on the critical path. Inlining it cuts a
  // ~300ms round-trip on slow 3G mobile and removes the Lighthouse
  // "render-blocking resources" finding. The cost is one extra HTML
  // payload per first hit (~70 KB CSS), but Cloudflare gzips this to
  // ~14 KB and the result is cached by the page's edge SWR policy.
  const cssLinkRe = /<link rel="stylesheet"[^>]*href="\/assets\/([^"]+\.css)"[^>]*>/;
  const cssLinkMatch = baseHtml.match(cssLinkRe);
  if (cssLinkMatch) {
    const cssPath = path.join(distDir, "assets", cssLinkMatch[1]);
    if (fs.existsSync(cssPath)) {
      const cssContent = fs.readFileSync(cssPath, "utf-8");
      baseHtml = baseHtml.replace(
        cssLinkRe,
        `<style data-inline-css="${cssLinkMatch[1]}">${cssContent}</style>`,
      );
      console.log(
        `[prerender-library] inlined ${cssLinkMatch[1]} (${cssContent.length} bytes) — removed render-blocking CSS`,
      );
    }
  }

  // Task #395: LibraryPage is now its own dynamic chunk, so Vite's
  // automatic modulepreload set in dist/index.html no longer covers it.
  // Inject a manual preload hint so the LibraryPage chunk fetches in
  // parallel with the entry chunk and hydration doesn't pay an extra
  // RTT after entry parse.
  const { findPageChunk, injectPageChunkPreload } = await import(
    pathToFileURL(path.join(__dirname, "_page-chunk-preload.mjs")).href
  );
  const libraryChunk = findPageChunk(distDir, "LibraryPage");
  if (!libraryChunk) {
    throw new Error(
      "[prerender-library] no LibraryPage-*.js chunk found in dist/assets — " +
        "Task #395 contract requires a per-page chunk; check Vite chunk naming",
    );
  }
  baseHtml = injectPageChunkPreload(baseHtml, libraryChunk);
  console.log(
    `[prerender-library] injected modulepreload for ${libraryChunk}`,
  );

  // Resolve shell-strip indices AGAINST the mutated baseHtml so byte
  // shifts from CSS inlining + modulepreload edits don't leave the
  // legacy shell overlay in the prerendered output.
  const startIdx = baseHtml.indexOf(startMarker);
  const rootMatch = baseHtml.match(rootRe);
  if (startIdx === -1 || !rootMatch) {
    throw new Error(
      "[prerender-library] shell markers missing from baseHtml after mutations",
    );
  }

  for (const { route, outDir } of ROUTES) {
    const out = await renderRoute({ url: route, bundleSlim: slim });
    if (Array.isArray(out?.errors) && out.errors.length) {
      for (const e of out.errors) {
        console.warn(
          `[prerender-library] SSR onError (${route}):`,
          e?.stack || e?.message || e,
        );
      }
    }
    const ssrHtml = out?.html;
    if (typeof ssrHtml !== "string" || ssrHtml.length === 0) {
      throw new Error(
        `[prerender-library] renderRoute(${route}) returned empty html`,
      );
    }

    let routeHtml =
      baseHtml.slice(0, startIdx) +
      baseHtml.slice(rootMatch.index).replace(
        rootRe,
        `<div id="root" data-hydrate="library">${ssrHtml}</div>`,
      );

    if (slim) {
      const json = JSON.stringify(slim).replace(/</g, "\\u003c");
      const inlineScript = `<script>window.__LIBRARY_BUNDLE__=${json};</script>`;
      routeHtml = routeHtml.replace(
        /<script type="module"/,
        `${inlineScript}\n    <script type="module"`,
      );
    }

    routeHtml = rewriteHead(routeHtml);

    const outHtml = path.join(outDir, "index.html");
    fs.mkdirSync(outDir, { recursive: true });
    fs.writeFileSync(outHtml, routeHtml);

    const written = fs.readFileSync(outHtml, "utf-8");
    if (
      !written.includes('data-hydrate="library"') ||
      /<div id="root" data-hydrate="library"><\/div>/.test(written)
    ) {
      throw new Error(
        `[prerender-library] hydration assertion failed for ${route}: #root is empty or missing data-hydrate marker`,
      );
    }
    // Hard assertion: og:image:alt must be present and non-empty so social
    // crawlers see a descriptive alt string on the prerendered /library page.
    if (!/<meta property="og:image:alt" content="[^"]+"/.test(written)) {
      throw new Error(
        `[prerender-library] og:image:alt assertion failed for ${route}: tag missing or empty`,
      );
    }
    // Task #38: twitter:image and twitter:image:alt must be present so the
    // edge-proxy HTMLRewriter can rewrite them and X/Twitter link previews
    // show the route-specific banner rather than falling back to nothing.
    if (!/<meta name="twitter:image" content="[^"]+"/.test(written)) {
      throw new Error(
        `[prerender-library] twitter:image assertion failed for ${route}: tag missing or empty`,
      );
    }
    if (!/<meta name="twitter:image:alt" content="[^"]+"/.test(written)) {
      throw new Error(
        `[prerender-library] twitter:image:alt assertion failed for ${route}: tag missing or empty`,
      );
    }

    console.log(
      `[prerender-library] wrote ${path.relative(distDir, outHtml)} ` +
        `(${(bundle?.subjects || []).length} subjects, ${routeHtml.length} bytes, SSR+hydrate)`,
    );
  }
}

main()
  .then(() => {
    // Force-exit so the orchestrator does not SIGTERM us after the
    // 5-min budget. Without this, keep-alive HTTP sockets to the
    // backend held the event loop open and the parent killed us
    // anyway (see Cloudflare Pages build log 2026-04-19:
    // "prerender-library.mjs exceeded 300000ms — sending SIGTERM").
    process.exit(0);
  })
  .catch((err) => {
    console.error(err?.stack || err);
    // Hard fail — the deployed artifact must contain a hydrated
    // /library snapshot. (Task #382 — architect review)
    process.exit(1);
  });
