import { afterEach, describe, expect, it, vi } from "vitest";

import worker from "../../public/_worker.js";
import {
  LIBRARY_SEO_DESCRIPTION,
  LIBRARY_SEO_TITLE,
} from "../lib/librarySeo";

const BOT_HEADERS = {
  "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
  Accept: "text/html",
};

function assetResponse({
  canonicalHref = null,
  prerenderPath = null,
  title = null,
  description = null,
} = {}) {
  const canonical = canonicalHref
    ? `<link rel="canonical" href="${canonicalHref}" />`
    : '<link rel="canonical">';
  const marker = prerenderPath
    ? `<meta name="syrabit-prerender-path" content="${prerenderPath}" />`
    : "";
  const publicMetadata = title && description
    ? `<title>${title}</title>` +
      `<meta name="description" content="${description}" />` +
      `<meta property="og:title" content="${title}" />` +
      `<meta name="twitter:title" content="${title}" />`
    : "";
  return new Response(
    `<!doctype html><html><head>${canonical}${marker}${publicMetadata}</head><body><div id="root"></div></body></html>`,
    { status: 200, headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});


describe("Pages worker crawler snapshots", () => {
  it.each([
    ["/library", "crawler", BOT_HEADERS],
    ["/browser", "crawler", BOT_HEADERS],
    ["/library", "browser", { Accept: "text/html" }],
    ["/browser", "browser", { Accept: "text/html" }],
  ])(
    "serves Degree public metadata on %s to a %s request",
    async (route, _requestKind, headers) => {
      const backendFetch = vi.fn();
      vi.stubGlobal("fetch", backendFetch);
      const env = {
        ASSETS: {
          fetch: vi.fn().mockResolvedValue(
            assetResponse({
              canonicalHref: "https://syrabit.ai/library",
              prerenderPath: route,
              title: LIBRARY_SEO_TITLE,
              description: LIBRARY_SEO_DESCRIPTION,
            }),
          ),
        },
      };

      const response = await worker.fetch(
        new Request(`https://syrabit.ai${route}`, { headers }),
        env,
      );
      const html = await response.text();

      expect(response.status).toBe(200);
      expect(html).toContain(`<title>${LIBRARY_SEO_TITLE}</title>`);
      expect(html).toContain(
        `<meta property="og:title" content="${LIBRARY_SEO_TITLE}" />`,
      );
      expect(html).toContain(
        `<meta name="twitter:title" content="${LIBRARY_SEO_TITLE}" />`,
      );
      expect(html).toContain(
        `<meta name="description" content="${LIBRARY_SEO_DESCRIPTION}" />`,
      );
      expect(html).not.toMatch(/assamboard/i);
      expect(backendFetch).not.toHaveBeenCalled();
    },
  );

  it("accepts an asset whose canonical path matches the crawler request", async () => {
    const backendFetch = vi.fn();
    vi.stubGlobal("fetch", backendFetch);
    const env = {
      ASSETS: {
        fetch: vi.fn().mockResolvedValue(
          assetResponse({
            canonicalHref: "https://syrabit.ai/library",
            prerenderPath: "/library",
          }),
        ),
      },
    };

    const response = await worker.fetch(
      new Request("https://syrabit.ai/library", { headers: BOT_HEADERS }),
      env,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("X-Source")).toBe("prerender");
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it.each([
    ["Googlebot", BOT_HEADERS],
    ["GPTBot", { "User-Agent": "GPTBot/1.0", Accept: "text/html" }],
  ])("serves a static chapter snapshot to %s without backend rendering", async (_bot, headers) => {
    const backendFetch = vi.fn();
    vi.stubGlobal("fetch", backendFetch);
    const chapterPath = "/ahsec/hs-2nd-year/economics/forms-of-market-and-price-determination";
    const env = {
      ASSETS: {
        fetch: vi.fn().mockResolvedValue(
          assetResponse({ prerenderPath: chapterPath }),
        ),
      },
    };

    const response = await worker.fetch(
      new Request(`https://syrabit.ai${chapterPath}`, {
        headers,
      }),
      env,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("X-Source")).toBe("prerender");
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("uses explicit chapter index.html when directory lookup would redirect", async () => {
    const backendFetch = vi.fn();
    vi.stubGlobal("fetch", backendFetch);
    const chapterPath = "/ahsec/hs-2nd-year/economics/forms-of-market-and-price-determination";
    const assetFetch = vi.fn()
      .mockResolvedValueOnce(assetResponse({ prerenderPath: chapterPath }))
      .mockResolvedValueOnce(new Response(null, { status: 308 }));
    const response = await worker.fetch(
      new Request(`https://syrabit.ai${chapterPath}`, { headers: BOT_HEADERS }),
      { ASSETS: { fetch: assetFetch } },
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("X-Source")).toBe("prerender");
    expect(assetFetch.mock.calls[0][0].url).toBe(
      `https://syrabit.ai${chapterPath}/index.html`,
    );
    expect(assetFetch).toHaveBeenCalledOnce();
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("accepts the /browser output marker even though its canonical is /library", async () => {
    const backendFetch = vi.fn();
    vi.stubGlobal("fetch", backendFetch);
    const env = {
      ASSETS: {
        fetch: vi.fn().mockResolvedValue(
          assetResponse({
            canonicalHref: "https://syrabit.ai/library",
            prerenderPath: "/browser",
          }),
        ),
      },
    };

    const response = await worker.fetch(
      new Request("https://syrabit.ai/browser", { headers: BOT_HEADERS }),
      env,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("X-Source")).toBe("prerender");
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("falls through to bot rendering for a declared route without a snapshot", async () => {
    const backendFetch = vi.fn().mockResolvedValue(
      new Response("<!doctype html><title>Status</title>", {
        status: 200,
        headers: { "Content-Type": "text/html" },
      }),
    );
    vi.stubGlobal("fetch", backendFetch);
    const env = {
      ASSETS: { fetch: vi.fn().mockResolvedValue(assetResponse()) },
    };

    const response = await worker.fetch(
      new Request("https://syrabit.ai/status", { headers: BOT_HEADERS }),
      env,
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("X-Source")).toBe("bot-render");
    expect(backendFetch).toHaveBeenCalledOnce();
  });

  it("rejects the SPA fallback for an undeclared route", async () => {
    const backendFetch = vi.fn();
    vi.stubGlobal("fetch", backendFetch);
    const env = {
      ASSETS: {
        fetch: vi.fn().mockResolvedValue(assetResponse()),
      },
    };

    const response = await worker.fetch(
      new Request("https://syrabit.ai/release-health-missing-test", {
        headers: BOT_HEADERS,
      }),
      env,
    );

    expect(response.status).toBe(404);
    expect(response.headers.get("X-Source")).toBe("bot-render-not-found");
    expect(response.headers.get("X-Robots-Tag")).toContain("noindex");
    expect(backendFetch).not.toHaveBeenCalled();
  });

  it("returns a real 404 when a declared chapter is missing upstream", async () => {
    const backendFetch = vi.fn().mockResolvedValue(
      new Response("not found", { status: 404 }),
    );
    vi.stubGlobal("fetch", backendFetch);
    const env = { ASSETS: { fetch: vi.fn().mockResolvedValue(assetResponse()) } };

    const response = await worker.fetch(
      new Request("https://syrabit.ai/ahsec/hs-2nd-year/economics/not-a-chapter", {
        headers: BOT_HEADERS,
      }),
      env,
    );

    expect(response.status).toBe(404);
    expect(response.headers.get("X-Source")).toBe("bot-render-not-found");
    expect(response.headers.get("X-Robots-Tag")).toContain("noindex");
  });

  it("proxies browser API calls same-origin with application and Access credentials", async () => {
    const backendFetch = vi.fn().mockResolvedValue(
      new Response('{"active":false}', {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", backendFetch);

    const request = new Request(
      "https://syrabit.ai/api/v1/admin/break-glass-status",
      {
        headers: {
          Authorization: "Bearer staff-jwt",
          Cookie: "syrabit_admin_session=session; CF_Authorization=access",
          "Cf-Access-Jwt-Assertion": "signed-access-assertion",
        },
      },
    );
    const response = await worker.fetch(request, {
      ASSETS: { fetch: vi.fn() },
      API_BACKEND_URL: "https://api.syrabit.ai",
    });

    expect(response.status).toBe(200);
    expect(response.headers.get("X-Source")).toBe("pages-api-proxy");
    expect(await response.json()).toEqual({ active: false });
    expect(backendFetch).toHaveBeenCalledOnce();
    const forwarded = backendFetch.mock.calls[0][0];
    expect(forwarded.url).toBe(
      "https://api.syrabit.ai/api/v1/admin/break-glass-status",
    );
    expect(forwarded.headers.get("Authorization")).toBe("Bearer staff-jwt");
    expect(forwarded.headers.get("Cookie")).toContain("CF_Authorization=access");
    expect(forwarded.headers.get("Cf-Access-Jwt-Assertion")).toBe(
      "signed-access-assertion",
    );
  });

  it("proxies the dynamic public topic sitemap as XML instead of SPA HTML", async () => {
    const backendFetch = vi.fn().mockResolvedValue(
      new Response('<?xml version="1.0"?><urlset></urlset>', {
        status: 200,
        headers: { "Content-Type": "application/xml" },
      }),
    );
    vi.stubGlobal("fetch", backendFetch);

    const response = await worker.fetch(
      new Request("https://syrabit.ai/sitemap-topics.xml"),
      { ASSETS: { fetch: vi.fn() }, SEO_BACKEND_URL: "https://seo.example.test" },
    );

    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toContain("application/xml");
    expect(response.headers.get("X-Source")).toBe("sitemap-proxy");
    expect(await response.text()).toContain("<urlset>");
    expect(backendFetch).toHaveBeenCalledOnce();
    expect(backendFetch.mock.calls[0][0]).toBe(
      "https://seo.example.test/api/v1/seo/sitemap-topics.xml",
    );
  });

  it.each([
    ["browser navigation", { Accept: "text/html" }, 404],
    ["crawler with a Pages HTML fallback", BOT_HEADERS, 200],
  ])("returns a non-HTML 404 for a missing hashed asset requested by a %s", async (_kind, headers, assetStatus) => {
    const backendFetch = vi.fn();
    vi.stubGlobal("fetch", backendFetch);
    const assetFetch = vi.fn().mockResolvedValue(
      new Response("<!doctype html><title>SPA shell</title>", {
        status: assetStatus,
        headers: { "Content-Type": "text/html; charset=utf-8" },
      }),
    );
    const request = new Request(
      "https://syrabit.ai/assets/index-deadbeef.js",
      { headers },
    );

    const response = await worker.fetch(request, { ASSETS: { fetch: assetFetch } });

    expect(response.status).toBe(404);
    expect(response.headers.get("Content-Type")).toContain("text/plain");
    expect(response.headers.get("X-Source")).toBe("asset-not-found");
    expect(await response.text()).not.toContain("SPA shell");
    expect(assetFetch).toHaveBeenCalledWith(request);
    expect(backendFetch).not.toHaveBeenCalled();
  });
});