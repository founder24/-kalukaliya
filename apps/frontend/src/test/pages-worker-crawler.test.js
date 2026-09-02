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
});