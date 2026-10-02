import { readFileSync } from "node:fs";
import path from "node:path";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";

const serviceWorkerSource = readFileSync(
  path.resolve(process.cwd(), "public/sw.js"),
  "utf8",
);
const serviceWorkerContext = { self: { addEventListener() {} } };
runInNewContext(
  `${serviceWorkerSource}\nglobalThis.__isCacheableApi = isCacheableApi;`,
  serviceWorkerContext,
);
const isCacheableApi = serviceWorkerContext.__isCacheableApi;

describe("service worker library API cache routes", () => {
  it.each([
    ["versioned", "/api/v1/content/library-bundle?slim=1"],
    ["legacy", "/api/content/library-bundle?slim=1"],
  ])("caches the %s library bundle route", (_variant, url) => {
    expect(isCacheableApi(new URL(url, "https://syrabit.ai").pathname)).toBe(true);
  });
});