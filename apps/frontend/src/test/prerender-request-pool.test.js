import { describe, expect, it, vi } from "vitest";

import { createJsonRequestPool } from "../../scripts/_prerender-request-pool.mjs";

describe("prerender backend request pool", () => {
  it("enforces one global in-flight cap across concurrent callers", async () => {
    let active = 0;
    let maximumActive = 0;
    const fetchImpl = vi.fn(async (url) => {
      active++;
      maximumActive = Math.max(maximumActive, active);
      await new Promise((resolve) => setTimeout(resolve, 5));
      active--;
      return {
        ok: true,
        status: 200,
        json: async () => ({ url }),
      };
    });
    const fetchJson = createJsonRequestPool({
      concurrency: 4,
      timeoutMs: 1_000,
      fetchImpl,
    });

    const results = await Promise.all(
      Array.from({ length: 32 }, (_, index) => fetchJson(`/chapter/${index}`)),
    );

    expect(results).toHaveLength(32);
    expect(fetchImpl).toHaveBeenCalledTimes(32);
    expect(maximumActive).toBe(4);
  });

  it("releases a slot after a failed request", async () => {
    const fetchImpl = vi
      .fn()
      .mockRejectedValueOnce(new Error("backend aborted"))
      .mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
      });
    const fetchJson = createJsonRequestPool({
      concurrency: 1,
      timeoutMs: 1_000,
      fetchImpl,
      maxAttempts: 1,
    });

    const first = fetchJson("/first");
    const second = fetchJson("/second");

    await expect(first).rejects.toThrow("backend aborted");
    await expect(second).resolves.toEqual({ ok: true });
  });

  it("retries transient failures without exceeding the global cap", async () => {
    let active = 0;
    let maximumActive = 0;
    const fetchImpl = vi.fn(async () => {
      active++;
      maximumActive = Math.max(maximumActive, active);
      await new Promise((resolve) => setTimeout(resolve, 5));
      active--;
      if (fetchImpl.mock.calls.length < 3) {
        throw new Error("backend aborted");
      }
      return {
        ok: true,
        status: 200,
        json: async () => ({ ok: true }),
      };
    });
    const fetchJson = createJsonRequestPool({
      concurrency: 1,
      timeoutMs: 1_000,
      fetchImpl,
      retryBaseDelayMs: 0,
      retryJitterMs: 0,
      sleepImpl: async () => {},
    });

    await expect(fetchJson("/retry")).resolves.toEqual({ ok: true });

    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(maximumActive).toBe(1);
  });

  it("does not retry permanent client errors", async () => {
    const fetchImpl = vi.fn(async () => ({
      ok: false,
      status: 404,
      headers: { get: () => null },
    }));
    const fetchJson = createJsonRequestPool({
      concurrency: 1,
      timeoutMs: 1_000,
      fetchImpl,
      retryBaseDelayMs: 0,
      retryJitterMs: 0,
      sleepImpl: async () => {},
    });

    await expect(fetchJson("/missing")).rejects.toThrow("HTTP 404 /missing");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
