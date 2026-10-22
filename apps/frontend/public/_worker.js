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
// Static assets are normally served by the ASSETS binding. `/assets/*` stays
// inside this worker deliberately: a missing hashed bundle must be a real,
// non-HTML 404 rather than an accidental SPA navigation fallback.

// ─── SECURITY HEADERS ────────────────────────────────────────────────────────
// Applied to every response the worker generates (bot-render, sitemap proxy,
// SPA shell). Prevents clickjacking, MIME sniffing, and referrer leakage.
// CSP is intentionally omitted here — it lives in _headers (applied by Pages
// to static assets) and would require keeping two copies in sync.
function addSecurityHeaders(headers) {
  headers.set("X-Frame-Options", "SAMEORIGIN");
  headers.set("X-Content-Type-Options", "nosniff");
  headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
  headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=()");
  return headers;
}

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
// Task #9 — canonical bot registry lives at `infra/bot-rules.yaml`.
// CI guard `scripts/check_bot_rules_drift.py` enforces that every token
// from the verified_search + citation_ai + training_ai buckets appears
// in the regex below.
const SEARCH_BOT_UA = /googlebot|google-extended|googleother|google-inspectiontool|bingbot|duckduckbot|applebot|yandexbot|baiduspider|petalbot|yeti|mojeekbot|seznambot|youbot|slurp|msnbot|perplexitybot|perplexity-user|oai-searchbot|chatgpt-user|gptbot|claudebot|claude-web|anthropic-ai|applebot-extended|ccbot|cohere-ai|bytespider|amazonbot|diffbot|meta-externalagent|facebookexternalhit|facebookbot|twitterbot|linkedinbot|telegrambot|whatsapp|discordbot|slackbot|redditbot/i;

// Backend that serves bot-rendered HTML. Configured at build time via
// the BACKEND_BOT_URL env var on the Pages project; falls back to the
// public api.syrabit.ai hostname if unset.
const DEFAULT_BACKEND = "https://api.syrabit.ai";
// The native API Worker owns crawler artifacts under /api/v1/seo. Keep this
// separate from the bot-render origin: that origin still serves /html/*, while
// the API Worker is the reliable source for feeds, sitemaps, and LLM indexes.
const DEFAULT_SEO_BACKEND = "https://syrabit-api-prod.axomxplain.workers.dev";

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
// extended this proxy to cover them too. The AI plugin manifest is deliberately
// excluded in _routes.json so Pages serves its static JSON file directly.
//
// Fix: when a request lands on any of these paths, proxy directly to
// the matching backend route and force the correct Content-Type. Cached
// at the edge for an hour (matches _headers s-maxage).
const SEO_PASSTHROUGH_RE =
  /^\/(?:sitemap-topics\.xml|sitemap[a-z0-9_-]*\.xml|sitemap-index\.xml|feed\.xml|feed\.json|rss\.xml|feed\/[a-z0-9_-]+\.xml|llms\.txt|llms-full\.txt|robots\.txt|api\/v1\/seo\/sitemap[a-z0-9_-]*\.xml|api\/v1\/seo\/feed\.json)$/i;
// IndexNow keyfiles (32-hex .txt or *indexnow*.txt) are intentionally
// excluded — they're shipped as static assets in dist/ so the Pages
// ASSETS pipeline serves them directly. Routing them through the
// backend hit "Direct origin access denied" because the Pages worker
// fetches BACKEND_URL without the X-Origin-Auth header that
// the edge worker injects.
//
// `/api/v1/seo/sitemap*.xml` paths are also intercepted so that
// Googlebot can follow sitemap-index child <loc> entries. The Worker
// returns the correct Content-Type: application/xml (not the SPA HTML
// shell) for both root aliases (/sitemap-subjects.xml) and
// /api/v1/seo/ sub-paths.

function isNativeSeoArtifact(pathname) {
  return (
    /^\/api\/v1\/seo\//i.test(pathname) ||
    /^\/(?:sitemap[a-z0-9_-]*\.xml|feed(?:\/[a-z0-9_-]+)?\.xml|feed\.json|rss\.xml|llms(?:-full)?\.txt|robots\.txt)$/i.test(pathname)
  );
}

// Map public aliases to the native API Worker's mounted /api/v1/seo routes.
// This includes feeds and LLM documents, not just sitemaps. Leaving feed.xml
// unchanged works only through the edge Worker and fails when Pages talks to
// the native Worker directly.
function backendPathForSeo(pathname) {
  if (/^\/api\/v1\/seo\//i.test(pathname)) {
    return pathname;
  }
  if (isNativeSeoArtifact(pathname)) {
    return "/api/v1/seo" + pathname;
  }
  return pathname;
}

function contentTypeForSeo(pathname) {
  if (/feed\.json$/i.test(pathname)) return "application/feed+json; charset=utf-8";
  if (/feed(?:\/[a-z0-9_-]+)?\.xml$/i.test(pathname) || /rss\.xml$/i.test(pathname)) {
    return "application/rss+xml; charset=utf-8";
  }
  if (/\.json$/i.test(pathname)) return "application/json; charset=utf-8";
  if (/\.txt$/i.test(pathname)) return "text/plain; charset=utf-8";
  return "application/xml; charset=utf-8";
}

async function sitemapProxy(request, env, url) {
  const backend = isNativeSeoArtifact(url.pathname)
    ? ((env && env.SEO_BACKEND_URL) || DEFAULT_SEO_BACKEND)
    : ((env && env.BACKEND_BOT_URL) || DEFAULT_BACKEND);
  const backendPath = backendPathForSeo(url.pathname);
  const backendUrl = backend + backendPath + url.search;
  const isXml = !/\.txt$/i.test(url.pathname);
  try {
    const resp = await fetch(backendUrl, {
      method: request.method,
      headers: {
        "User-Agent": request.headers.get("User-Agent") || "",
        "Accept": "application/rss+xml,application/xml,text/xml,text/plain,*/*",
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
      // robots.txt: no-transform prevents CF's "AI Scrapers & Crawlers"
      // managed robots.txt feature from prepending its Content-Signal block,
      // which causes Lighthouse to flag the file as malformed (unknown directive).
      const isTxt = /\.txt$/i.test(url.pathname);
      headers.set(
        "Cache-Control",
        isTxt
          ? "public, max-age=3600, s-maxage=86400, stale-while-revalidate=3600, no-transform"
          : "public, max-age=3600, s-maxage=86400, stale-while-revalidate=3600",
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
      addSecurityHeaders(headers);
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

// Browser API traffic normally uses the same-origin path so staff requests do
// not need a CORS preflight. The Pages Worker forwards the original
// Authorization, application cookies, and Cloudflare Access cookies/assertion
// to the protected API origin. The API Worker remains the authority for both
// application authentication and route authorization.
async function apiProxy(request, env, url) {
  const backend = (env && env.API_BACKEND_URL) || DEFAULT_BACKEND;
  const backendUrl = backend + url.pathname + url.search;
  const headers = new Headers(request.headers);
  headers.delete("Host");
  headers.delete("Content-Length");
  try {
    const upstream = await fetch(new Request(backendUrl, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "manual",
    }));
    const responseHeaders = new Headers(upstream.headers);
    responseHeaders.set("Cache-Control", "no-store");
    responseHeaders.set("X-Source", "pages-api-proxy");
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  } catch {
    return new Response(
      JSON.stringify({ detail: "API service unavailable" }),
      {
        status: 503,
        headers: {
          "Content-Type": "application/json; charset=utf-8",
          "Cache-Control": "no-store",
          "X-Source": "pages-api-proxy",
        },
      },
    );
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
  "/staff",
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

function normalizedPathname(pathname) {
  if (!pathname || pathname === "/") return "/";
  return pathname.replace(/\/+$/, "");
}

const EXACT_FRONTEND_ROUTES = new Set([
  "/", "/home", "/terms", "/privacy", "/about", "/technology",
  "/status", "/exam-routine", "/payment/success", "/payment/cancel", "/login",
  "/signup", "/reset-password", "/onboarding", "/library", "/browser",
  "/browse", "/browser-tabs", "/curriculum", "/subscribe", "/chat", "/read",
  "/history", "/profile", "/profile/memories", "/notebook", "/flashcards",
  "/guardian", "/admin", "/admin/login", "/staff",
]);

function isDeclaredFrontendRoute(pathname) {
  const normalized = normalizedPathname(pathname);
  if (EXACT_FRONTEND_ROUTES.has(normalized)) return true;

  const parts = normalized.split("/").filter(Boolean);
  if (parts.length === 2 && ["subject", "learn", "pyq"].includes(parts[0])) {
    return true;
  }
  if (parts.length === 3 && parts[0] === "cms") return true;
  if (parts[0] === "as" && parts.length >= 5 && parts.length <= 8) return true;

  // The public SEO routes start with a board slug and use 3–7 segments.
  // Their existence is resolved by the backend when no prerender exists.
  return parts.length >= 3 && parts.length <= 7;
}

function parseStudyRoute(pathname) {
  const parts = normalizedPathname(pathname).split("/").filter(Boolean);
  const isAssamese = parts[0] === "as";
  if (isAssamese) parts.shift();

  let board, classSlug, streamSlug = null, subjectSlug, chapterSlug, topicSlug = null;
  if (parts.length === 3) {
    [board, classSlug, subjectSlug] = parts;
    return { board, classSlug, subjectSlug, type: "subject", isAssamese };
  } else if (parts.length === 4) {
    [board, classSlug, subjectSlug, chapterSlug] = parts;
  } else if (parts.length === 5 && parts[4] !== "topic") {
    [board, classSlug, streamSlug, subjectSlug, chapterSlug] = parts;
  } else if (parts.length === 6 && parts[4] === "topic") {
    [board, classSlug, subjectSlug, chapterSlug] = parts;
    topicSlug = parts[5];
  } else if (parts.length === 7 && parts[5] === "topic") {
    [board, classSlug, streamSlug, subjectSlug, chapterSlug] = parts;
    topicSlug = parts[6];
  } else {
    return null;
  }
  return { board, classSlug, streamSlug, subjectSlug, chapterSlug, topicSlug, isAssamese };
}

async function resolveStudyRoute(request, env, parsed) {
  const backend = (env && env.API_BACKEND_URL) || DEFAULT_BACKEND;
  if (parsed.type === "subject") {
    const segments = [parsed.board, parsed.classSlug, parsed.subjectSlug]
      .map(encodeURIComponent);
    const language = parsed.isAssamese ? "?lang=as" : "";
    try {
      const response = await fetch(
        `${backend}/api/v1/content/resolve-subject/${segments.join("/")}${language}`,
        { method: "GET", headers: { Accept: "application/json" } },
      );
      if (response.status === 404) return { kind: "not-found" };
      if (!response.ok) return { kind: "unavailable" };
      const payload = await response.json();
      return payload?.id ? { kind: "valid" } : { kind: "not-found" };
    } catch {
      return { kind: "unavailable" };
    }
  }
  const resolver = parsed.isAssamese ? "chapter-by-slug-as" : "chapter-by-slug";
  const segments = [
    parsed.board, parsed.classSlug,
    ...(parsed.streamSlug ? [parsed.streamSlug] : []),
    parsed.subjectSlug, parsed.chapterSlug,
  ].map(encodeURIComponent);
  try {
    const response = await fetch(
      `${backend}/api/v1/content/${resolver}/${segments.join("/")}`,
      { method: "GET", headers: { Accept: "application/json" } },
    );
    if (response.status === 404) return { kind: "not-found" };
    if (!response.ok) return { kind: "unavailable" };
    const payload = await response.json();
    if (!payload || !payload.chapter_id) return { kind: "not-found" };

    if (parsed.topicSlug) {
      const topics = Array.isArray(payload.published_topics)
        ? payload.published_topics
        : [];
      const topicExists = topics.some((topic) =>
        topic && (
          topic.topic_slug === parsed.topicSlug ||
          topic.slug === parsed.topicSlug ||
          topic.topic_slug_as === parsed.topicSlug
        ) &&
        (!topic.definition_status || topic.definition_status === "ok" || topic.status === "published")
      );
      if (!topicExists) return { kind: "not-found" };
    }

    const canonicalSlug = payload.canonical_slug;
    if (
      payload.slug_redirect &&
      canonicalSlug &&
      canonicalSlug !== parsed.chapterSlug
    ) {
      const topicSuffix = parsed.topicSlug ? `/topic/${parsed.topicSlug}` : "";
      const chapterPath = normalizedPathname(new URL(request.url).pathname)
        .slice(0, topicSuffix ? -topicSuffix.length : undefined);
      const slash = chapterPath.lastIndexOf("/");
      const targetPath = `${chapterPath.slice(0, slash + 1)}${encodeURIComponent(canonicalSlug)}${topicSuffix}`;
      return { kind: "redirect", location: `${targetPath}${new URL(request.url).search}` };
    }
    return { kind: "valid" };
  } catch {
    return { kind: "unavailable" };
  }
}

function studyRouteResponse(request, result) {
  if (result.kind === "not-found") return botNotFoundResponse(request);
  if (result.kind === "redirect") {
    return new Response(null, {
      status: 301,
      headers: {
        Location: result.location,
        "Cache-Control": "public, max-age=3600, s-maxage=86400",
        "X-Source": "verified-chapter-alias",
      },
    });
  }
  if (result.kind === "unavailable") {
    return new Response(request.method === "HEAD" ? null : "Study page validation temporarily unavailable\n", {
      status: 503,
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        "Cache-Control": "no-store",
        "Retry-After": "120",
        "X-Source": "study-route-validation-unavailable",
      },
    });
  }
  return null;
}

async function assetMatchesRequestedPrerender(response, pathname) {
  try {
    const html = await response.clone().text();
    const metas = html.match(/<meta\b[^>]*>/gi) || [];
    for (const tag of metas) {
      if (!/\bname\s*=\s*["']syrabit-prerender-path["']/i.test(tag)) continue;
      const outputPath = tag.match(/\bcontent\s*=\s*["']([^"']+)["']/i)?.[1];
      return normalizedPathname(outputPath) === normalizedPathname(pathname);
    }
  } catch {
    // Treat unreadable HTML as an asset miss and use backend bot rendering.
  }
  return false;
}

async function canonicalRedirectFromPrerender(response, request, pathname) {
  try {
    const html = await response.clone().text();
    const href = html.match(/<link\b(?=[^>]*\brel=["']canonical["'])[^>]*\bhref=["']([^"']+)["']/i)?.[1];
    if (!href) return null;
    const canonical = new URL(href, request.url);
    const current = new URL(request.url);
    if (
      canonical.origin !== current.origin ||
      normalizedPathname(canonical.pathname) === normalizedPathname(pathname)
    ) return null;
    return new Response(null, {
      status: 301,
      headers: {
        Location: `${canonical.pathname}${canonical.search}`,
        "Cache-Control": "public, max-age=3600, s-maxage=86400",
        "X-Source": "verified-prerender-canonical",
      },
    });
  } catch {
    return null;
  }
}

function botNotFoundResponse(request) {
  const headers = new Headers({
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "X-Source": "bot-render-not-found",
  });
  addSecurityHeaders(headers);
  return new Response(
    request.method === "HEAD" ? null : "<!doctype html><title>Not Found</title><h1>Not Found</h1>",
    { status: 404, headers },
  );
}

function assetNotFoundResponse(request) {
  const headers = new Headers({
    "Content-Type": "text/plain; charset=utf-8",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Source": "asset-not-found",
  });
  addSecurityHeaders(headers);
  return new Response(request.method === "HEAD" ? null : "Not Found\n", {
    status: 404,
    headers,
  });
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
    // Preserve a real backend miss as a real 404. This prevents nonexistent
    // crawler URLs from becoming soft-200 SPA pages or misleading 503s.
    if (resp.status === 404) {
      return botNotFoundResponse(request);
    }
    // Backend returns 200 with HTML on hit, non-200 on outage/error.
    if (resp.status === 200) {
      const ct = resp.headers.get("content-type") || "";
      if (ct.includes("text/html") || ct.includes("application/xhtml")) {
        const html = await resp.text();
        // Never turn an upstream SPA fallback into a successful crawler
        // response. That shell looks like a soft-404 and masks missing
        // chapter rendering behind a cacheable 200.
        const genericShell =
          /<title\b[^>]*>[^<]*Syrabit\.ai\s*(?:—|&mdash;)\s*Educational Browser For Assam Board Students[^<]*<\/title>/i.test(html) ||
          /<h1\b[^>]*>\s*Syrabit\.ai\s*(?:—|&mdash;)\s*Educational Browser For Assam Board Students\s*<\/h1>/i.test(html);
        if (genericShell) {
          const headers = new Headers({
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
            "Retry-After": "120",
            "X-Source": "bot-render-generic-rejected",
          });
          addSecurityHeaders(headers);
          return new Response(
            request.method === "HEAD" ? null : "<!doctype html><title>Temporarily unavailable</title>",
            { status: 503, headers },
          );
        }
        const headers = new Headers(resp.headers);
        headers.set("X-Source", "bot-render");
        headers.set("Cache-Control", "public, max-age=3600, s-maxage=86400, stale-while-revalidate=3600");
        headers.set("X-Robots-Tag", "all, max-snippet:-1, max-image-preview:large");
        addSecurityHeaders(headers);
        return new Response(html, { status: 200, headers });
      }
    }
  } catch {
    // Intentional fall-through — handled below.
  }
  // Backend miss or non-HTML 200: return a 503 so crawlers retry rather
  // than indexing an empty SPA shell (M-5 fix — avoids soft 404).
  return new Response(
    request.method === "HEAD"
      ? null
      : "<!-- bot-render temporarily unavailable, retry later -->",
    {
      status: 503,
      headers: {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "public, max-age=60",
        "Retry-After": "120",
      },
    },
  );
}

async function spaShellResponse(request, env, url, originalStatus) {
  try {
    const indexResponse = await env.ASSETS.fetch(new URL("/", url.origin));
    if (!indexResponse || indexResponse.status !== 200) {
      return minimalRedirectResponse();
    }
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
  } catch {
    return minimalRedirectResponse();
  }
}

function minimalRedirectResponse() {
  const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><script>window.location.replace('/library')</script></head><body></body></html>`;
  return new Response(html, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const ua = request.headers.get("User-Agent") || "";
    const isBot = SEARCH_BOT_UA.test(ua);
    const studyRoute = parseStudyRoute(url.pathname);

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

    // Keep API calls on the Pages origin. This is especially important for
    // /api/v1/admin/*: Access cannot authenticate a browser CORS preflight,
    // while a same-origin request carries the normal application credentials
    // without preflight.
    if (url.pathname.startsWith("/api/") || url.pathname === "/health" || url.pathname.startsWith("/health/")) {
      return apiProxy(request, env, url);
    }

    // Root redirect: send bare / and /home to /library for real browsers.
    // _redirects would do this but the worker intercepts every request
    // before Cloudflare Pages consults _redirects, so we must handle it
    // here. Bots are excluded — they fall through to the bot-render path
    // below so Googlebot still receives the prerendered homepage HTML.
    if (
      !isBot &&
      (request.method === "GET" || request.method === "HEAD") &&
      (url.pathname === "/" || url.pathname === "/home" || url.pathname === "/home/")
    ) {
      return new Response(null, {
        status: 301,
        headers: {
          "Location": "/library",
          "Cache-Control": "public, max-age=0, must-revalidate",
          "X-Frame-Options": "DENY",
          "X-Content-Type-Options": "nosniff",
        },
      });
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

    // Hashed Vite bundles are requested with a script/style destination but
    // can also carry Accept: text/html through proxies. Never apply the SPA
    // fallback to these requests: a stale deployment must surface as a real
    // non-HTML 404 so browsers fail the asset, rather than parse index.html as
    // JavaScript or CSS. This runs before bot rendering too.
    if (/^\/assets\/.+/.test(url.pathname)) {
      try {
        const response = await env.ASSETS.fetch(request);
        const contentType = response.headers.get("Content-Type") || "";
        // Guard against a Pages asset fallback that reports 200 but sends
        // index.html. A JavaScript/CSS asset is never an HTML document.
        return response.status === 404 || /(?:x?html)/i.test(contentType)
          ? assetNotFoundResponse(request)
          : response;
      } catch {
        return assetNotFoundResponse(request);
      }
    }

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
      //
      //    CF Pages stores prerendered pages as /path/index.html and
      //    serves them at /path/ (with trailing slash). A bot hitting
      //    /path (no slash, e.g. after following the / → /library 301)
      //    gets a 308 from ASSETS — the worker then saw non-200 and
      //    fell through to botRender, which 503'd for data-driven pages
      //    like /library. Normalize the URL to include a trailing slash
      //    for extension-less paths so the prerendered snapshot is found.
      try {
        const extensionless = !url.pathname.match(/\.[a-z0-9]{1,10}$/i);
        // Pages' directory redirect behaviour is not consistent for Worker
        // ASSETS.fetch. Ask for the emitted file directly first; only then
        // try the directory URL for compatibility with older deployments.
        const assetUrls = extensionless
          ? [
              new URL(`${url.pathname.replace(/\/+$/, "")}/index.html`, url.origin),
              new URL(`${url.pathname.replace(/\/+$/, "")}/`, url.origin),
            ]
          : [url];
        for (const assetFetchUrl of assetUrls) {
          const assetResp = await env.ASSETS.fetch(new Request(assetFetchUrl, request));
          if (assetResp.status !== 200) continue;
          const ct = assetResp.headers.get("content-type") || "";
          if (
            (ct.includes("text/html") || ct.includes("application/xhtml")) &&
            await assetMatchesRequestedPrerender(assetResp, url.pathname)
          ) {
            if (studyRoute) {
              const canonicalRedirect = await canonicalRedirectFromPrerender(
                assetResp, request, url.pathname,
              );
              if (canonicalRedirect) return canonicalRedirect;
            }
            const headers = new Headers(assetResp.headers);
            headers.set("X-Source", "prerender");
            headers.set("Cache-Control", "public, max-age=3600, s-maxage=86400, stale-while-revalidate=3600");
            headers.set("X-Robots-Tag", "all, max-snippet:-1, max-image-preview:large");
            return new Response(assetResp.body, {
              status: 200,
              headers,
            });
          }
        }
      } catch {
        // Fall through to bot-render on any asset-pipeline error.
      }
      // Only synthesize a 404 for paths React Router does not declare. Known
      // routes without a snapshot still fall through to backend bot rendering.
      if (!isDeclaredFrontendRoute(url.pathname)) {
        return botNotFoundResponse(request);
      }
      if (studyRoute) {
        const validation = await resolveStudyRoute(request, env, studyRoute);
        const response = studyRouteResponse(request, validation);
        if (response) return response;
      }
      // 2) No prerendered snapshot — try the backend bot-render proxy.
      //    botRender now returns a 503 on failure (M-5 fix) so bots
      //    never receive an empty SPA shell as the canonical document.
      const rendered = await botRender(request, env, url);
      return rendered;
    }

    try {
      // Normalize extension-less paths to include a trailing slash before
      // hitting the ASSETS pipeline. CF Pages stores prerendered pages as
      // /path/index.html and serves them at /path/ — requesting /path
      // (no slash) gets a 308 redirect to /path/, adding a full round-trip
      // (~935ms on mobile). Same fix as the bot path above (line ~388).
      const assetUrl = (
        !url.pathname.endsWith("/") &&
        !url.pathname.match(/\.[a-z0-9]{1,10}$/i)
      ) ? new URL(url.pathname + "/", url.origin) : url;
      const assetRequest = assetUrl === url ? request : new Request(assetUrl, request);
      const response = await env.ASSETS.fetch(assetRequest);
      if (
        studyRoute &&
        (request.method === "GET" || request.method === "HEAD") &&
        (
          (request.headers.get("Accept") || "").includes("text/html") ||
          (request.method === "HEAD" && ["", "*/*"].includes(request.headers.get("Accept") || ""))
        )
      ) {
        const contentType = response.headers.get("content-type") || "";
        if (
          response.status === 200 &&
          /(?:x?html)/i.test(contentType) &&
          await assetMatchesRequestedPrerender(response, url.pathname)
        ) {
          if (studyRoute) {
            const canonicalRedirect = await canonicalRedirectFromPrerender(
              response, request, url.pathname,
            );
            if (canonicalRedirect) return canonicalRedirect;
          }
          return response;
        }
        const validation = await resolveStudyRoute(request, env, studyRoute);
        const validationResponse = studyRouteResponse(request, validation);
        if (validationResponse) return validationResponse;
        return spaShellResponse(request, env, url, response.status);
      }
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
        if (
          (isGetNavigation || isHeadProbe) &&
          !isDeclaredFrontendRoute(url.pathname)
        ) {
          return botNotFoundResponse(request);
        }
        if (isGetNavigation || isHeadProbe || isBotGet) {
          return spaShellResponse(request, env, url, 404);
        }
        return response;
      }
      return response;
    } catch {
      if (studyRoute && (request.method === "GET" || request.method === "HEAD")) {
        const validation = await resolveStudyRoute(request, env, studyRoute);
        const validationResponse = studyRouteResponse(request, validation);
        if (validationResponse) return validationResponse;
        return spaShellResponse(request, env, url, 200);
      }
      return spaShellResponse(request, env, url, 200);
    }
  },
};
