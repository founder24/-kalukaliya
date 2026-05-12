// Statically prerender the /chat page (Task #387).
//
// Same SSR-into-#root approach as scripts/prerender-library.mjs: render
// the React tree for /chat at build time, write dist/chat/index.html
// with `data-hydrate="chat"` on #root, and let the client call
// hydrateRoot to adopt the existing DOM (no remount, no flash).
//
// /chat has no server-fetched data (the empty state is purely static),
// so we skip the bundle fetch step. /chat is also `Disallow`'d in
// robots.txt and ships `<meta name="robots" content="noindex,follow">`,
// so the rewriteHead step overrides the default index/follow value
// from the shared shell.

import fs from "fs";
import path from "path";
import { fileURLToPath, pathToFileURL } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(__dirname, "..", "dist");
const distSsrDir = path.resolve(__dirname, "..", "dist-ssr");
const srcHtml = path.join(distDir, "index.html");
const outDir = path.join(distDir, "chat");
const outHtml = path.join(outDir, "index.html");
const ssrEntry = path.join(distSsrDir, "entry-server.js");

const TITLE =
  "Syrabit AI Chat — Ask Anything About Your Syllabus";
const CANONICAL = "https://syrabit.ai/chat";
const DESCRIPTION =
  "Ask Syrabit's AI tutor anything about AHSEC, SEBA and Degree subjects. Get instant explanations, MCQs, definitions and exam-ready answers in English or Assamese.";

function escapeHtml(s = "") {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

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
  // swap if present (legacy builds) else inject before </head>. /chat is
  // noindex,follow, but we still emit a canonical so that AI crawlers
  // don't merge ranking signals onto the homepage.
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
    `<meta property="og:image:alt" content="${escapeHtml(TITLE)}" />`,
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
      `<meta name="twitter:image:alt" content="${escapeHtml(TITLE)}" />`,
    );
  } else {
    html = html.replace(
      /<\/head>/,
      `    <meta name="twitter:image:alt" content="${escapeHtml(TITLE)}" />\n  </head>`,
    );
  }
  // /chat is auth-gated and personalized — override the default
  // `index, follow, …` robots meta from the shared shell with
  // noindex,follow. Also matches the same guard in robots.txt.
  if (/<meta name="robots" content="[^"]*"\s*\/?>/.test(html)) {
    html = html.replace(
      /<meta name="robots" content="[^"]*"\s*\/?>/,
      `<meta name="robots" content="noindex, follow" />`,
    );
  } else {
    html = html.replace(
      /<\/head>/,
      `    <meta name="robots" content="noindex, follow" />\n  </head>`,
    );
  }
  return html;
}

async function main() {
  if (!fs.existsSync(srcHtml)) {
    console.warn(
      `[prerender-chat] dist/index.html not found at ${srcHtml}; skipping`,
    );
    return;
  }

  let html = fs.readFileSync(srcHtml, "utf-8");

  const startMarker =
    `<noscript><style>#__shell{display:none!important}</style></noscript>`;
  const startIdx = html.indexOf(startMarker);
  const rootRe = /<div id="root"[^>]*><\/div>/;
  const rootMatch = html.match(rootRe);
  if (startIdx === -1 || !rootMatch) {
    throw new Error(
      "[prerender-chat] could not locate shell markers in dist/index.html — structure changed?",
    );
  }

  // SSR build is mandatory — the deployed /chat artifact must contain
  // a hydrated React snapshot, not a JS-only shell. (Same contract as
  // prerender-library.mjs.)
  if (!fs.existsSync(ssrEntry)) {
    throw new Error(
      `[prerender-chat] required SSR build missing at ${ssrEntry}; ` +
        `build pipeline must run "vite build --ssr src/entry-server.jsx --outDir dist-ssr" first`,
    );
  }

  // Minimal browser-API polyfills so the bundled SSR module can boot
  // in Node — ChatPage initialises responseLang from localStorage at
  // render time, the auth context reads from storage on init, etc.
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
      "[prerender-chat] entry-server.js did not export renderRoute()",
    );
  }
  const out = await renderRoute({ url: "/chat" });
  if (Array.isArray(out?.errors) && out.errors.length) {
    for (const e of out.errors) {
      console.warn(
        "[prerender-chat] SSR onError:",
        e?.stack || e?.message || e,
      );
    }
  }
  const ssrHtml = out?.html;
  if (typeof ssrHtml !== "string" || ssrHtml.length === 0) {
    throw new Error("[prerender-chat] renderRoute() returned empty html");
  }

  // Replace the legacy pre-hydration shell block AND the empty #root
  // with the SSR output. Mark with data-hydrate so the bootstrap
  // calls hydrateRoot instead of createRoot.
  html =
    html.slice(0, startIdx) +
    html.slice(rootMatch.index).replace(
      rootRe,
      `<div id="root" data-hydrate="chat">${ssrHtml}</div>`,
    );

  // Task #395: ChatPage is now its own dynamic chunk; inject a
  // modulepreload so the browser fetches it in parallel with the entry
  // chunk and `preloadPageForKind("chat")` doesn't add a hydration RTT.
  {
    const { findPageChunk, injectPageChunkPreload } = await import(
      pathToFileURL(path.join(__dirname, "_page-chunk-preload.mjs")).href
    );
    const chatChunk = findPageChunk(distDir, "ChatPage");
    if (!chatChunk) {
      throw new Error(
        "[prerender-chat] no ChatPage-*.js chunk found in dist/assets — " +
          "Task #395 contract requires a per-page chunk",
      );
    }
    html = injectPageChunkPreload(html, chatChunk);
    console.log(`[prerender-chat] injected modulepreload for ${chatChunk}`);
  }

  html = rewriteHead(html);

  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(outHtml, html);

  // Hard assertion: the generated file must contain `data-hydrate=
  // "chat"` and a non-empty #root, AND the noindex marker.
  const written = fs.readFileSync(outHtml, "utf-8");
  if (
    !written.includes('data-hydrate="chat"') ||
    /<div id="root" data-hydrate="chat"><\/div>/.test(written)
  ) {
    throw new Error(
      "[prerender-chat] hydration assertion failed: #root is empty or missing data-hydrate marker",
    );
  }
  if (!/<meta name="robots" content="noindex, follow"\s*\/?>/.test(written)) {
    throw new Error(
      "[prerender-chat] noindex assertion failed: robots meta not set to noindex,follow",
    );
  }
  // Hard assertion: og:image:alt must be present and non-empty so social
  // crawlers see a descriptive alt string on the prerendered /chat page.
  if (!/<meta property="og:image:alt" content="[^"]+"/.test(written)) {
    throw new Error(
      "[prerender-chat] og:image:alt assertion failed: tag missing or empty in prerendered HTML",
    );
  }
  // Task #38: twitter:image and twitter:image:alt must be present so the
  // edge-proxy HTMLRewriter can rewrite them and X/Twitter link previews
  // show the route-specific banner rather than falling back to nothing.
  if (!/<meta name="twitter:image" content="[^"]+"/.test(written)) {
    throw new Error(
      "[prerender-chat] twitter:image assertion failed: tag missing or empty in prerendered HTML",
    );
  }
  if (!/<meta name="twitter:image:alt" content="[^"]+"/.test(written)) {
    throw new Error(
      "[prerender-chat] twitter:image:alt assertion failed: tag missing or empty in prerendered HTML",
    );
  }

  console.log(
    `[prerender-chat] wrote ${path.relative(distDir, outHtml)} ` +
      `(${html.length} bytes, SSR+hydrate, noindex)`,
  );
}

main().catch((err) => {
  console.error(err?.stack || err);
  process.exit(1);
});
