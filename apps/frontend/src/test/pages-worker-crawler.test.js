import { afterEach, describe, expect, it, vi } from "vitest";

import worker from "../../public/_worker.js";

const BOT_HEADERS = {
  "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
  Accept: "text/html",
};

function assetResponse(canonicalHref) {
  const canonical = canonicalHref
    ? `<link rel="canonical" href="${canonicalHref}" />`
    : '<link rel="canonical">';
  return new Response(
    `<!doctype html><html><head>${canonical}</head><body><div id="root"></div></body></html>`,
    { status: 200, headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Pages worker crawler snapshots", () => {
  it("accepts an asset whose canonical path matches the crawler request", async () => {
    const backendFetch = vi.fn();
    vi.stubGlobal("fetch", backendFetch);
    const env = {
      ASSETS: {
        fetch: vi.fn().mockResolvedValue(
          assetResponse("https://syrabit.ai/library"),
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

  it("rejects the SPA fallback and preserves the backend crawler 404", async () => {
    const backendFetch = vi.fn();
    vi.stubGlobal("fetch", backendFetch);
    const env = {
      ASSETS: {
        fetch: vi.fn().mockResolvedValue(assetResponse(null)),
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
});