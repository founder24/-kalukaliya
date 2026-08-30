import { afterEach, describe, expect, it, vi } from "vitest";

import {
  clearPrerenderCache,
  loadLibraryBundle,
} from "../../scripts/_prerender-data.mjs";
import {
  applyPrerenderCachePolicy,
  DEVELOPMENT_CACHE_POLICY_MESSAGE,
  RELEASE_CACHE_POLICY_MESSAGE,
} from "../../scripts/prerender-all.mjs";

const originalFetch = globalThis.fetch;

const oldCurriculum = {
  subjects: [{ id: "old-subject", name: "Old curriculum snapshot" }],
};
const currentCurriculum = {
  subjects: [{ id: "current-subject", name: "Current curriculum snapshot" }],
};

function responseFor(payload, signal) {
  const headers = {
    get: (name) =>
      name.toLowerCase() === "x-schema-version" ? signal ?? null : null,
  };
  return {
    ok: true,
    status: 200,
    headers,
    json: async () => payload,
  };
}

function stubBackend(payload, signal = null) {
  const methods = [];
  const fetchMock = vi.fn(async (_url, init = {}) => {
    methods.push((init.method || "GET").toUpperCase());
    return responseFor(payload, signal);
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    getCount: (method) =>
      methods.filter((requestMethod) => requestMethod === method).length,
  };
}

afterEach(() => {
  clearPrerenderCache();
  vi.unstubAllGlobals();
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("prerender cache policy", () => {
  it("release builds discard a restored curriculum snapshot before fetching", async () => {
    const seed = stubBackend(oldCurriculum);
    await expect(loadLibraryBundle()).resolves.toEqual(oldCurriculum);
    expect(seed.getCount("GET")).toBe(1);

    const releaseLog = vi.spyOn(console, "log").mockImplementation(() => {});
    applyPrerenderCachePolicy({ strict: true });
    expect(releaseLog).toHaveBeenCalledWith(RELEASE_CACHE_POLICY_MESSAGE);

    const current = stubBackend(currentCurriculum);
    await expect(loadLibraryBundle()).resolves.toEqual(currentCurriculum);
    expect(current.getCount("GET")).toBe(1);
  });

  it("development builds reuse a fresh curriculum snapshot without another payload fetch", async () => {
    const seed = stubBackend(oldCurriculum);
    await expect(loadLibraryBundle()).resolves.toEqual(oldCurriculum);
    expect(seed.getCount("GET")).toBe(1);

    const developmentLog = vi.spyOn(console, "log").mockImplementation(() => {});
    applyPrerenderCachePolicy({ strict: false });
    expect(developmentLog).toHaveBeenCalledWith(
      DEVELOPMENT_CACHE_POLICY_MESSAGE,
    );

    const reused = stubBackend(currentCurriculum);
    await expect(loadLibraryBundle()).resolves.toEqual(oldCurriculum);
    // A fresh cache entry may perform the cheap HEAD schema probe, but it must
    // not issue another GET for the curriculum payload.
    expect(reused.getCount("GET")).toBe(0);
  });

  it("discards a cached curriculum snapshot when the backend schema signal changes", async () => {
    const seed = stubBackend(oldCurriculum, "schema-v1");
    await expect(loadLibraryBundle()).resolves.toEqual(oldCurriculum);
    expect(seed.getCount("GET")).toBe(1);
    expect(seed.getCount("HEAD")).toBe(1);

    const changed = stubBackend(currentCurriculum, "schema-v2");
    await expect(loadLibraryBundle()).resolves.toEqual(currentCurriculum);
    expect(changed.getCount("GET")).toBe(1);
    expect(changed.getCount("HEAD")).toBeGreaterThanOrEqual(1);

    const unchanged = stubBackend(
      { subjects: [{ id: "should-not-be-fetched" }] },
      "schema-v2",
    );
    await expect(loadLibraryBundle()).resolves.toEqual(currentCurriculum);
    expect(unchanged.getCount("GET")).toBe(0);
    expect(unchanged.getCount("HEAD")).toBe(1);
  });
});