// Cloudflare Pages worker.
//
// Two responsibilities:
//
// 1) **Bot rendering.** Search engines (Googlebot, Bingbot, etc.) and AI
//    crawlers MUST receive the rich, server-rendered HTML for chapter /
//    subject / static pages — NOT the SPA shell. The shell is 27 KB of
//    `<div id="root"></div>` with title "Syrabit.ai" and almost zero
//    chapter-specific text, which Google labels "soft 404" and refuses
//    to index. Before this rewrite, 134 of 137 sitemap URLs were
//    "Discovered – currently not indexed" for exactly that reason.
//    For verified-bot UAs we proxy GET requests to the backend's
//    /html/<rest-of-path> endpoint and return its response (which is
//    syllabus-aligned content + JSON-LD). For HEAD we still return
//    200 cheaply without going to the backend.
//
// 2) **SPA fallback.** Any non-asset path that is not a bot request
//    falls through to /index.html so React Router can take over.
//    HEAD parity is preserved (Task #365).
//
// Static assets (/assets/*, /icons/*, sitemaps, feeds, etc.) are
// excluded in `_routes.json` and never reach this worker.

// ─── CANONICAL BOT REGEX — DO NOT DRIFT ─────────────────────────────────────
// MUST stay aligned with three other locations:
//   * artifacts/syrabit-backend/utils.py        → _SEARCH_BOT_UA_RE (Python source of truth)
//   * artifacts/syrabit/vite.config.js          → BOT_UA (build-time / dev SSR)
//   * workers/edge-proxy/src/index.ts           → SEARCH_BOT_UA (api.syrabit.ai)
// Any UA we want to (a) prerender HTML for, (b) serve sitemaps to, or
// (c) count as a verified crawler in analytics MUST appear in ALL FOUR.
// AI-search crawlers are included because they ground their answers in
// the same HTML they fetch, and AI training crawlers (GPTBot, CCBot,
// Bytespider, …) are included so we can serve them llms.txt via this
// same prerender path instead of the SPA shell.
// ────────────────────────────────────────────────────────────────────────────
const SEARCH_BOT_UA = /googlebot|google-extended|googleother|google-inspectiontool|bingbot|yandexbot|duckduckbot|slurp|baiduspider|applebot|applebot-extended|chatgpt-user|oai-searchbot|gptbot|perplexitybot|perplexity-user|claudebot|claude-web|anthropic-ai|meta-externalagent|bytespider|ccbot|amazonbot|facebookexternalhit|facebookbot|twitterbot|linkedinbot|whatsapp|telegrambot|discordbot/i;

// Backend that serves bot-rendered HTML. Configured at build time via
// the BACKEND_BOT_URL env var on the Pages project; falls back to the
// public api.syrabit.ai hostname if unset.
const DEFAULT_BACKEND = "https://api.syrabit.ai";

// Task #640 (extended): sitemap + feed + llms XML/TXT proxy.
//
// Root cause of the 2026-04-18 → 2026-04-21 indexing collapse: every
// /sitemap*.xml URL on syrabit.ai was returning the SPA shell with
// `Content-Type: text/html` instead of XML. The Pages Worker had no
// special handling, ASSETS.fetch had no static file, so the SPA
// fallback served the React shell — which Googlebot / Bingbot
// validate as a malformed sitemap and silently drop.
//
// Followup audit also found /feed.xml, /feed/<name>.xml, /llms.txt and
// /llms-full.txt were excluded by `_routes.json` → bypassed the worker
// → 404'd against the asset pipeline. Removed those exclusions and
// extended this proxy to cover them too.
//
// Fix: when a request lands on any of these paths, proxy directly to
// the matching backend route and force the correct Content-Type. Cached
// at the edge for an hour (matches _headers s-maxage).
const SEO_PASSTHROUGH_RE =
  /^\/(sitemap[a-z0-9_-]*\.xml|sitemap-index\.xml|feed\.xml|rss\.xml|feed\/[a-z0-9_-]+\.xml|llms\.txt|llms-full\.txt|robots\.txt|\.well-known\/ai-plugin\.json|api\/seo\/sitemap[a-z0-9_-]*\.xml)$/i;
// IndexNow keyfiles (32-hex .txt or *indexnow*.txt) are intentionally
// excluded — they're shipped as static assets in dist/ so the Pages
// ASSETS pipeline serves them directly. Routing them through the
// backend hit "Direct origin access denied" because the Pages worker
// fetches BACKEND_URL without the X-Origin-Auth header that
// the edge worker injects.
//
// Task #259: `/api/seo/sitemap*.xml` paths are also intercepted so
// that Googlebot can follow sitemap-index child <loc> entries even if
// they still reference the /api/seo/ prefix. The Worker returns the
// correct Content-Type: application/xml (not the SPA HTML shell) for
// both root aliases (/sitemap-subjects.xml) and /api/seo/ sub-paths.

// Map a public path to the corresponding backend path. Root sitemaps
// (/sitemap*.xml) are rewritten to /api/seo/<name>. /api/seo/sitemap*.xml
// sub-paths are already correct and need no rewrite. Feeds, llms, robots
// and .well-known are served at the backend root unchanged.
function backendPathForSeo(pathname) {
  if (/^\/sitemap[a-z0-9_-]*\.xml$/i.test(pathname)) {
    return "/api/seo" + pathname;
  }
  // /api/seo/sitemap*.xml — already the correct backend path, pass through.
  if (/^\/api\/seo\/sitemap[a-z0-9_-]*\.xml$/i.test(pathname)) {
    return pathname;
  }
  // /feed.xml, /feed/<name>.xml, /rss.xml, /llms.txt, /llms-full.txt,
  // /robots.txt, /.well-known/ai-plugin.json, /<key>-indexnow-<…>.txt
  // — backend serves all of these at the root path, no rewrite needed.
  return pathname;
}

function contentTypeForSeo(pathname) {
  if (/\.json$/i.test(pathname)) return "application/json; charset=utf-8";
  if (/\.txt$/i.test(pathname)) return "text/plain; charset=utf-8";
  return "application/xml; charset=utf-8";
}

async function sitemapProxy(request, env, url) {
  const backend = (env && env.BACKEND_BOT_URL) || DEFAULT_BACKEND;
  const backendPath = backendPathForSeo(url.pathname);
  const backendUrl = backend + backendPath + url.search;
  const isXml = !/\.txt$/i.test(url.pathname);
  try {
    const resp = await fetch(backendUrl, {
      method: request.method,
      headers: {
        "User-Agent": request.headers.get("User-Agent") || "",
        "Accept": "application/xml,text/xml,*/*",
        "X-Forwarded-For": request.headers.get("CF-Connecting-IP") || "",
        "X-Sitemap-Proxy": "1",
      },
      cf: { cacheTtl: 3600, cacheEverything: true },
    });
    if (resp.status === 200) {
      const headers = new Headers(resp.headers);
      // Force the right content-type even if the backend mislabels it
      // (e.g. .txt llms passthrough must NOT be served as application/xml).
      headers.set("Content-Type", contentTypeForSeo(url.pathname));
      headers.set(
        "Cache-Control",
        "public, max-age=3600, s-maxage=86400, stale-while-revalidate=3600",
      );
      headers.set("X-Source", "sitemap-proxy");
      // Cloudflare Workers' fetch() auto-decompresses response bodies
      // before exposing them on resp.body, so we MUST strip the
      // content-encoding / transfer-encoding headers — otherwise the
      // outer runtime re-applies them on top of an already-decoded
      // stream and the client sees garbled XML.
      headers.delete("transfer-encoding");
      headers.delete("content-encoding");
      // HEAD parity: never carry a body on a HEAD response (Fetch spec
      // forbids it; some validators reject sitemaps if HEAD lies about
      // the body).
      const body = request.method === "HEAD" ? null : resp.body;
      return new Response(body, { status: 200, headers });
    }
    // Non-200 from backend: do NOT serve the SPA shell — return a
    // small 503 with a meaningful message so search engines retry
    // later instead of indexing garbage.
    return seoUpstreamError(
      request,
      url,
      isXml,
      `upstream returned ${resp.status}`,
    );
  } catch (err) {
    return seoUpstreamError(request, url, isXml, "upstream unreachable");
  }
}

function seoUpstreamError(request, url, isXml, reason) {
  let body = null;
  if (request.method !== "HEAD") {
    body = isXml
      ? `<?xml version="1.0" encoding="UTF-8"?>\n<!-- ${reason} -->\n`
      : `# ${reason}\n`;
  }
  return new Response(body, {
    status: 503,
    headers: {
      "Content-Type": contentTypeForSeo(url.pathname),
      "Cache-Control": "public, max-age=60",
      "Retry-After": "300",
    },
  });
}

// Paths that never need bot rendering — let them go straight to the
// SPA shell (auth flows, the /chat surface, internal admin, etc.).
// Anything not listed here gets the bot-render path for bots.
// Prefixes are stored WITHOUT trailing slashes so the matcher below
// can do an exact-match OR `path.startsWith(prefix + "/")` test
// uniformly. Adding a trailing slash here breaks the matcher:
// e.g. "/api/" + "/" → "/api//" which never matches "/api/users".
const BOT_RENDER_SKIP_PREFIXES = [
  "/login",
  "/signup",
  "/profile",
  "/admin",
  "/auth",
  "/api",
  "/cms",
  "/chat",
];

function shouldBotRender(pathname) {
  if (pathname === "/" || pathname === "") return true;
  for (const p of BOT_RENDER_SKIP_PREFIXES) {
    if (pathname === p || pathname.startsWith(p + "/")) return false;
  }
  return true;
}

async function botRender(request, env, url) {
  // Map / → /html/homepage, /home → /html/home, etc. The backend
  // exposes /html/<rest-of-path> for chapters and /html/homepage
  // for the root. We pass the path through unchanged for everything
  // except /, which the backend serves under /html/homepage.
  const backend = (env && env.BACKEND_BOT_URL) || DEFAULT_BACKEND;
  let backendPath;
  if (url.pathname === "/" || url.pathname === "") {
    backendPath = "/html/homepage";
  } else {
    backendPath = "/html" + url.pathname;
  }
  const backendUrl = backend + backendPath + url.search;
  try {
    const resp = await fetch(backendUrl, {
      method: "GET",
      headers: {
        "User-Agent": request.headers.get("User-Agent") || "",
        "Accept": "text/html",
        "X-Forwarded-For": request.headers.get("CF-Connecting-IP") || "",
        "X-Bot-Render": "1",
      },
      cf: { cacheTtl: 300, cacheEverything: true },
    });
    // Backend returns 200 with HTML on hit, non-200 on miss/error.
    if (resp.status === 200) {
      const ct = resp.headers.get("content-type") || "";
      if (ct.includes("text/html") || ct.includes("application/xhtml")) {
        const headers = new Headers(resp.headers);
        headers.set("X-Source", "bot-render");
        headers.set("Cache-Control", "public, max-age=300, s-maxage=300");
        return new Response(resp.body, { status: 200, headers });
      }
    }
  } catch {
    // Fall through to SPA shell on any backend error — better to
    // serve the shell than to 5xx Googlebot.
  }
  return null;
}

async function spaShellResponse(request, env, url, originalStatus) {
  const indexResponse = await env.ASSETS.fetch(new URL("/", url.origin));
  if (request.method === "HEAD") {
    return new Response(null, {
      headers: indexResponse.headers,
      status: 200,
    });
  }
  return new Response(indexResponse.body, {
    headers: indexResponse.headers,
    status: originalStatus === 404 ? 200 : (originalStatus || 200),
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const ua = request.headers.get("User-Agent") || "";
    const isBot = SEARCH_BOT_UA.test(ua);

    // Task #640: sitemap + feed XML proxy. Run BEFORE bot-render and
    // BEFORE asset lookup so we never accidentally serve the SPA shell
    // (text/html) for a sitemap URL — that's what was killing
    // Googlebot's ability to enumerate any URL on the site.
    if (
      (request.method === "GET" || request.method === "HEAD") &&
      SEO_PASSTHROUGH_RE.test(url.pathname) &&
      request.headers.get("X-Sitemap-Proxy") !== "1"
    ) {
      return sitemapProxy(request, env, url);
    }

    // Loop guard: if this request already carries the X-Bot-Render
    // tag (i.e. it's the backend fetching back through the Pages
    // host because BACKEND_BOT_URL was misconfigured), do NOT
    // recurse — fall through to the asset pipeline. Same for any
    // request whose target host literally equals the configured
    // backend host: that means someone pointed BACKEND_BOT_URL at
    // syrabit.ai itself.
    const backendHost = (() => {
      try { return new URL((env && env.BACKEND_BOT_URL) || DEFAULT_BACKEND).host; }
      catch { return ""; }
    })();
    const wouldLoop =
      request.headers.get("X-Bot-Render") === "1" ||
      (backendHost && backendHost === url.host);

    // Bot rendering: GET requests from search/AI bot UAs that hit a
    // non-skip path get high-quality HTML. Priority order:
    //   1) Prerendered static snapshot (dist/<route>/index.html) —
    //      richest markup, includes JSON-LD, full content, hashed
    //      asset preloads. Wins whenever it exists.
    //   2) Backend bot-render proxy (/html/<path>) — fallback for
    //      dynamically-generated chapter/board URLs that don't have a
    //      static snapshot.
    //   3) SPA shell (handled in the asset-pipeline block below).
    //
    // Task #640: Earlier the order was inverted (bot-render first,
    // ASSETS fallback). The backend's /html/<path> handler returned
    // a generic shell-sized response for prerendered routes like
    // /library/, which MEANT verified bots received ~30 KB shells
    // for routes that had perfectly good 70-100 KB prerendered
    // snapshots sitting in dist/. Flipping the order recovers them.
    if (
      isBot &&
      !wouldLoop &&
      request.method === "GET" &&
      shouldBotRender(url.pathname)
    ) {
      // 1) Try the static prerender snapshot first. ASSETS.fetch
      //    returns 404 when no snapshot exists; we only treat HTML
      //    200s here as a hit — anything else falls through to the
      //    backend bot-render path so we don't accidentally serve
      //    JSON, images, or redirects as the canonical HTML.
      try {
        const assetResp = await env.ASSETS.fetch(request);
        if (assetResp.status === 200) {
          const ct = assetResp.headers.get("content-type") || "";
          if (ct.includes("text/html") || ct.includes("application/xhtml")) {
            const headers = new Headers(assetResp.headers);
            headers.set("X-Source", "prerender");
            return new Response(assetResp.body, {
              status: 200,
              headers,
            });
          }
        }
      } catch {
        // Fall through to bot-render on any asset-pipeline error.
      }
      // 2) No prerendered snapshot — try the backend bot-render proxy.
      const rendered = await botRender(request, env, url);
      if (rendered) return rendered;
      // 3) Backend miss too → fall through to the asset pipeline /
      //    SPA shell below so the bot at least gets a valid 200.
    }

    try {
      const response = await env.ASSETS.fetch(request);
      if (response.status === 404) {
        const accept = request.headers.get("Accept") || "";
        // Same-as-before SPA fallback rules. Note that for bots we
        // already tried the backend above; if we land here it means
        // either the path is in BOT_RENDER_SKIP_PREFIXES (auth, admin,
        // chat — pages bots shouldn't index anyway) or the backend
        // missed and we want a graceful 200.
        const isGetNavigation =
          request.method === "GET" && accept.includes("text/html");
        const isHeadProbe =
          request.method === "HEAD" &&
          (accept === "" || accept.includes("text/html") || accept.includes("*/*"));
        const isBotGet = isBot && request.method === "GET";
        if (isGetNavigation || isHeadProbe || isBotGet) {
          return spaShellResponse(request, env, url, 404);
        }
        return response;
      }
      return response;
    } catch {
      return spaShellResponse(request, env, url, 200);
    }
  },
};
