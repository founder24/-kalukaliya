import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createHash } from "node:crypto";
import workerHandler, { handleBotContentRequest } from "../src/index";
import { _resetMonitorStateForTests } from "../src/kv-monitor";

const CONTENT_PATH = "/ahsec/class-12/physics/electric-field";
const BACKEND_LM = "Mon, 13 Apr 2026 09:00:00 GMT";
const RENDERED_HTML = "<html><body>" + "x".repeat(800) + "</body></html>";

interface KvEntry {
  value: string;
  expiresAt: number | null;
}

function makeExpiringKv() {
  const store = new Map<string, KvEntry>();
  const putSpy = vi.fn();
  const api = {
    store,
    putSpy,
    async get(k: string): Promise<string | null> {
      const e = store.get(k);
      if (!e) return null;
      if (e.expiresAt !== null && Date.now() >= e.expiresAt) {
        store.delete(k);
        return null;
      }
      return e.value;
    },
    async put(
      k: string,
      v: string,
      opts?: { expirationTtl?: number },
    ): Promise<void> {
      putSpy(k, v, opts);
      const ttlSec = opts?.expirationTtl;
      const expiresAt =
        typeof ttlSec === "number" && ttlSec > 0
          ? Date.now() + ttlSec * 1000
          : null;
      store.set(k, { value: v, expiresAt });
    },
    async delete(k: string): Promise<void> {
      store.delete(k);
    },
    async list() {
      return {
        keys: Array.from(store.keys()).map((name) => ({ name })),
        list_complete: true,
      };
    },
  };
  return api;
}

type Kv = ReturnType<typeof makeExpiringKv>;

function makeEnv(opts: { botCache?: Kv | null; rateLimit?: Kv } = {}) {
  const botCache =
    opts.botCache === null ? undefined : (opts.botCache ?? makeExpiringKv());
  const rateLimit = opts.rateLimit ?? makeExpiringKv();
  return {
    BACKEND_URL: "https://backend.test",
    PAGES_ORIGIN: "https://pages.test",
    RATE_LIMIT: rateLimit as unknown,
    BOT_HTML_CACHE: botCache as unknown,
    CONTENT_DB: undefined as unknown,
    D1_SYNC_SECRET: "secret",
  } as unknown as Parameters<typeof workerHandler.fetch>[1];
}

const ctxNoop = {
  waitUntil: (p: Promise<unknown>) => {
    void p;
  },
  passThroughOnException: () => {},
} as unknown as ExecutionContext;

/**
 * Build a Request that mocks past `verifySearchBot` by attaching
 * `cf.verifiedBot=true`. This is the in-isolate moral equivalent of
 * what the Cloudflare edge sets for a real, address-confirmed
 * Googlebot — without it, the bot branch would be skipped.
 */
function botRequest(
  path: string,
  extraHeaders: Record<string, string> = {},
  cfOverrides: Record<string, unknown> = {},
): Request {
  const req = new Request(`https://syrabit.ai${path}`, {
    headers: {
      "user-agent": "Googlebot/2.1",
      "CF-Connecting-IP": "66.249.66.1",
      ...extraHeaders,
    },
  });
  Object.defineProperty(req, "cf", {
    value: { verifiedBot: true, colo: "test", ...cfOverrides },
    configurable: true,
    writable: true,
  });
  return req;
}

function installRenderedFetch(opts: { headLastModified?: string | null } = {}) {
  // The HEAD probe added in Task #907 fires from the legacy-upgrade
  // background job. By default we mirror the GET response's
  // Last-Modified so the cheap probe behaves like a real backend that
  // honours conditional metadata. Pass `headLastModified: null` to
  // simulate a backend that doesn't expose Last-Modified on HEAD —
  // this exercises the synthesized fallback path.
  const headLm =
    opts.headLastModified === undefined ? BACKEND_LM : opts.headLastModified;
  return vi
    .spyOn(globalThis, "fetch")
    .mockImplementation((async (input: RequestInfo | URL, init?: RequestInit) => {
      const u = typeof input === "string" ? input : input.toString();
      const method = (init?.method ?? "GET").toUpperCase();
      if (u.startsWith("https://pages.test")) {
        return new Response(
          "<html><body>pages-spa-shell</body></html>",
          {
            status: 200,
            headers: { "Content-Type": "text/html; charset=utf-8" },
          },
        );
      }
      if (method === "HEAD") {
        const headers: Record<string, string> = {
          "Content-Type": "text/html; charset=utf-8",
        };
        if (headLm) headers["Last-Modified"] = headLm;
        return new Response(null, { status: 200, headers });
      }
      // Default: backend bot-render endpoint.
      return new Response(RENDERED_HTML, {
        status: 200,
        headers: {
          "Content-Type": "text/html; charset=utf-8",
          "Last-Modified": BACKEND_LM,
        },
      });
    }) as typeof fetch);
}

describe("BOT_HTML_CACHE end-to-end via worker.fetch (verifiedBot=true)", () => {
  let fetchSpy: ReturnType<typeof installRenderedFetch>;

  beforeEach(() => {
    // Each test starts with a clean kv-monitor isolate state so the
    // module-level counters from earlier tests can't push us across the
    // proactive-fallback threshold and short-circuit a real KV write.
    _resetMonitorStateForTests();
    fetchSpy = installRenderedFetch();
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    vi.useRealTimers();
  });

  it("cold request: 200 with X-Source bot-prerender(-fallback) and X-Cache BOT-KV-MISS, KV populated", async () => {
    const kv = makeExpiringKv();
    const env = makeEnv({ botCache: kv });

    const resp = await workerHandler.fetch(
      botRequest(CONTENT_PATH),
      env,
      ctxNoop,
    );
    expect(resp.status).toBe(200);
    expect(resp.headers.get("X-Cache")).toBe("BOT-KV-MISS");
    expect(resp.headers.get("X-Source")).toMatch(
      /^bot-prerender(?:-fallback)?$/,
    );
    expect(resp.headers.get("Last-Modified")).toBe(BACKEND_LM);
    expect(resp.headers.get("ETag")).toMatch(/^"[0-9a-f]{12}"$/);
    expect(await resp.text()).toBe(RENDERED_HTML);

    expect(kv.putSpy).toHaveBeenCalledTimes(1);
    const [storedKey, storedValue, storedOpts] = kv.putSpy.mock.calls[0];
    expect(storedKey).toBe(`bot:content:${CONTENT_PATH}`);
    expect(storedOpts?.expirationTtl).toBe(3600);
    const wrapper = JSON.parse(storedValue);
    expect(wrapper.body).toBe(RENDERED_HTML);
    expect(wrapper.lastmod).toBe(BACKEND_LM);
    expect(wrapper.etag).toMatch(/^[0-9a-f]{12}$/);
  });

  it("warm request: identical follow-up returns 200 with X-Source bot-cache and X-Cache BOT-KV-HIT", async () => {
    const kv = makeExpiringKv();
    const env = makeEnv({ botCache: kv });

    const r1 = await workerHandler.fetch(botRequest(CONTENT_PATH), env, ctxNoop);
    expect(r1.headers.get("X-Cache")).toBe("BOT-KV-MISS");
    const coldEtag = r1.headers.get("ETag");
    await r1.text(); // drain body so the spy snapshot below is stable

    const callsBefore = fetchSpy.mock.calls.length;

    const r2 = await workerHandler.fetch(botRequest(CONTENT_PATH), env, ctxNoop);
    expect(r2.status).toBe(200);
    expect(r2.headers.get("X-Source")).toBe("bot-cache");
    expect(r2.headers.get("X-Cache")).toBe("BOT-KV-HIT");
    expect(r2.headers.get("ETag")).toBe(coldEtag);
    expect(r2.headers.get("Last-Modified")).toBe(BACKEND_LM);
    expect(await r2.text()).toBe(RENDERED_HTML);

    // Warm path must not re-fetch from the backend.
    expect(fetchSpy.mock.calls.length).toBe(callsBefore);
    // Warm path must not re-write KV (only the cold request did).
    expect(kv.putSpy).toHaveBeenCalledTimes(1);
  });

  it("conditional GET: matching If-None-Match against the cached etag returns 304 with no body", async () => {
    const kv = makeExpiringKv();
    const env = makeEnv({ botCache: kv });

    const r1 = await workerHandler.fetch(botRequest(CONTENT_PATH), env, ctxNoop);
    const etag = r1.headers.get("ETag")!;
    expect(etag).toMatch(/^"[0-9a-f]{12}"$/);
    await r1.text();

    const r2 = await workerHandler.fetch(
      botRequest(CONTENT_PATH, { "If-None-Match": etag }),
      env,
      ctxNoop,
    );
    expect(r2.status).toBe(304);
    expect(r2.headers.get("ETag")).toBe(etag);
    expect(r2.headers.get("Last-Modified")).toBe(BACKEND_LM);
    expect(r2.headers.get("X-Source")).toBe("bot-cache");
    expect(await r2.text()).toBe("");
  });

  it("legacy plain-string KV entry: served with synthesized etag (sha256 prefix) and supports If-None-Match → 304", async () => {
    const LEGACY_HTML =
      "<html><body>legacy-bot-cache-entry-" + "y".repeat(400) + "</body></html>";
    const expectedEtag = createHash("sha256")
      .update(LEGACY_HTML)
      .digest("hex")
      .slice(0, 12);

    const kv = makeExpiringKv();
    const env = makeEnv({ botCache: kv });

    // Pre-seed BOT_HTML_CACHE the way the previous worker version did:
    // the value is a plain HTML string, not the {body,lastmod,etag} JSON
    // wrapper that parseBotCacheEntry expects.
    kv.store.set(`bot:content:${CONTENT_PATH}`, {
      value: LEGACY_HTML,
      expiresAt: null,
    });

    const cacheKey = `bot:content:${CONTENT_PATH}`;
    const cacheWrites = () =>
      kv.putSpy.mock.calls.filter((c) => c[0] === cacheKey).length;

    // The Task #896 upgrade and the Task #907 HEAD probe both run on the
    // background path through ctx.waitUntil. Collect those promises so
    // we can await them deterministically before asserting on the
    // post-conditions.
    const writes: Promise<unknown>[] = [];
    const ctxAwait = {
      waitUntil: (p: Promise<unknown>) => {
        writes.push(p);
      },
      passThroughOnException: () => {},
    } as unknown as ExecutionContext;

    const callsBefore = fetchSpy.mock.calls.length;
    const r1 = await workerHandler.fetch(
      botRequest(CONTENT_PATH),
      env,
      ctxAwait,
    );
    expect(r1.status).toBe(200);
    expect(r1.headers.get("X-Source")).toBe("bot-cache");
    expect(r1.headers.get("X-Cache")).toBe("BOT-KV-HIT");
    expect(r1.headers.get("ETag")).toBe(`"${expectedEtag}"`);
    expect(r1.headers.get("Last-Modified")).toBeTruthy();
    expect(await r1.text()).toBe(LEGACY_HTML);

    await Promise.all(writes.splice(0));

    // Legacy hit must not trigger a backend re-render. The Task #907
    // upgrade job is allowed to fire a cheap metadata-only HEAD probe,
    // but no full GET re-render is permitted. The KV upgrade itself
    // happens exactly once per legacy entry (Task #896).
    const newCalls = fetchSpy.mock.calls.slice(callsBefore);
    for (const [, init] of newCalls) {
      const method = ((init as RequestInit | undefined)?.method ?? "GET").toUpperCase();
      expect(method).toBe("HEAD");
    }
    expect(cacheWrites()).toBe(1);

    // Conditional GET against the synthesized etag must yield 304.
    const callsBeforeCond = fetchSpy.mock.calls.length;
    const r2 = await workerHandler.fetch(
      botRequest(CONTENT_PATH, { "If-None-Match": `"${expectedEtag}"` }),
      env,
      ctxAwait,
    );
    expect(r2.status).toBe(304);
    expect(r2.headers.get("ETag")).toBe(`"${expectedEtag}"`);
    expect(r2.headers.get("X-Source")).toBe("bot-cache");
    expect(await r2.text()).toBe("");

    await Promise.all(writes.splice(0));

    // Still no backend re-render. The wrapper now parses cleanly so the
    // legacy-upgrade branch is skipped — no HEAD probe, no rewrites.
    expect(fetchSpy.mock.calls.length).toBe(callsBeforeCond);
    expect(cacheWrites()).toBe(1);
  });

  it("legacy plain-string KV entry is upgraded to the JSON wrapper on first read; when the backend HEAD probe omits Last-Modified, the synthesized timestamp is preserved (Task #896 fallback)", async () => {
    const LEGACY_HTML =
      "<html><body>legacy-upgrade-" + "z".repeat(400) + "</body></html>";
    const expectedEtag = createHash("sha256")
      .update(LEGACY_HTML)
      .digest("hex")
      .slice(0, 12);

    // Reinstall the fetch mock to simulate a backend that doesn't expose
    // Last-Modified on HEAD responses — this is the regression-guard for
    // Task #907's fallback contract: the synthesized "now-at-first-read"
    // must still flow through to the JSON wrapper unchanged.
    fetchSpy.mockRestore();
    fetchSpy = installRenderedFetch({ headLastModified: null });

    const kv = makeExpiringKv();
    const env = makeEnv({ botCache: kv });
    const cacheKey = `bot:content:${CONTENT_PATH}`;

    // Pre-seed the old plain-HTML format.
    kv.store.set(cacheKey, { value: LEGACY_HTML, expiresAt: null });

    // The handler defers the upgrade write through ctx.waitUntil — collect
    // and await those promises so the post-conditions are deterministic.
    const writes: Promise<unknown>[] = [];
    const ctxAwait = {
      waitUntil: (p: Promise<unknown>) => {
        writes.push(p);
      },
      passThroughOnException: () => {},
    } as unknown as ExecutionContext;

    // Pin time so the synthesized Last-Modified is the same value we expect
    // to see persisted in KV and replayed on every subsequent read.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-20T12:00:00Z"));
    const synthesizedLm = new Date().toUTCString();

    const r1 = await workerHandler.fetch(
      botRequest(CONTENT_PATH),
      env,
      ctxAwait,
    );
    await Promise.all(writes.splice(0));

    expect(r1.status).toBe(200);
    expect(r1.headers.get("X-Source")).toBe("bot-cache");
    expect(r1.headers.get("ETag")).toBe(`"${expectedEtag}"`);
    expect(r1.headers.get("Last-Modified")).toBe(synthesizedLm);
    expect(await r1.text()).toBe(LEGACY_HTML);

    // The KV value is now the JSON wrapper, with the synthesized lastmod
    // (the backend HEAD probe returned no Last-Modified, so we fall back)
    // and body-derived etag preserved. TTL matches the configured page TTL.
    const writeCalls = kv.putSpy.mock.calls.filter((c) => c[0] === cacheKey);
    expect(writeCalls).toHaveLength(1);
    const [, writtenValue, writtenOpts] = writeCalls[0];
    expect(writtenOpts?.expirationTtl).toBe(3600);
    const wrapper = JSON.parse(writtenValue);
    expect(wrapper.body).toBe(LEGACY_HTML);
    expect(wrapper.etag).toBe(expectedEtag);
    expect(wrapper.lastmod).toBe(synthesizedLm);
    expect(kv.store.get(cacheKey)?.value).toBe(writtenValue);

    // Advance the clock by 30 minutes — well within the 3600s KV TTL so
    // the entry is still present — to prove the second read does NOT
    // synthesize a fresh "now": the upgraded JSON wrapper now drives the
    // Last-Modified header, so it stays pinned to the original value.
    vi.setSystemTime(Date.now() + 30 * 60 * 1000);
    const r2 = await workerHandler.fetch(
      botRequest(CONTENT_PATH),
      env,
      ctxAwait,
    );
    await Promise.all(writes.splice(0));

    expect(r2.status).toBe(200);
    expect(r2.headers.get("X-Source")).toBe("bot-cache");
    expect(r2.headers.get("ETag")).toBe(`"${expectedEtag}"`);
    expect(r2.headers.get("Last-Modified")).toBe(synthesizedLm);
    expect(await r2.text()).toBe(LEGACY_HTML);

    // No further rewrites — upgrade happens exactly once.
    expect(
      kv.putSpy.mock.calls.filter((c) => c[0] === cacheKey),
    ).toHaveLength(1);
  });

  it("legacy plain-string KV entry: background upgrade prefers the upstream Last-Modified from the HEAD probe over the synthesized timestamp (Task #907)", async () => {
    const LEGACY_HTML =
      "<html><body>legacy-probe-" + "q".repeat(400) + "</body></html>";
    const expectedEtag = createHash("sha256")
      .update(LEGACY_HTML)
      .digest("hex")
      .slice(0, 12);

    const kv = makeExpiringKv();
    const env = makeEnv({ botCache: kv });
    const cacheKey = `bot:content:${CONTENT_PATH}`;

    // Pre-seed the old plain-HTML format.
    kv.store.set(cacheKey, { value: LEGACY_HTML, expiresAt: null });

    const writes: Promise<unknown>[] = [];
    const ctxAwait = {
      waitUntil: (p: Promise<unknown>) => {
        writes.push(p);
      },
      passThroughOnException: () => {},
    } as unknown as ExecutionContext;

    // Pin time so we can prove the persisted Last-Modified is the
    // upstream value, NOT the "now-at-first-read" timestamp.
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-22T03:14:15Z"));
    const synthesizedLm = new Date().toUTCString();
    expect(synthesizedLm).not.toBe(BACKEND_LM); // sanity

    // First read — the served response still uses the synthesized
    // timestamp (we built `entry` inline before the background probe
    // could resolve), but the *persisted* wrapper must carry the
    // upstream Last-Modified surfaced by the cheap HEAD probe.
    //
    // The crawler arrives with `If-None-Match` of an unrelated etag
    // (it doesn't match our entry, so the served response is 200) —
    // this asserts the probe path correctly strips the conditional
    // header before talking to the backend, otherwise the backend
    // could 304 the probe with no `Last-Modified`.
    const r1 = await workerHandler.fetch(
      botRequest(CONTENT_PATH, { "If-None-Match": '"deadbeefdead"' }),
      env,
      ctxAwait,
    );
    await Promise.all(writes.splice(0));

    expect(r1.status).toBe(200);
    expect(r1.headers.get("X-Source")).toBe("bot-cache");
    expect(r1.headers.get("ETag")).toBe(`"${expectedEtag}"`);
    expect(r1.headers.get("Last-Modified")).toBe(synthesizedLm);
    expect(await r1.text()).toBe(LEGACY_HTML);

    // The handler must have issued exactly one HEAD probe at the
    // backend's resolved bot-render endpoint — the metadata-only path
    // (no full re-render).
    const headCalls = fetchSpy.mock.calls.filter(([, init]) => {
      const method = ((init as RequestInit | undefined)?.method ?? "GET")
        .toUpperCase();
      return method === "HEAD";
    });
    expect(headCalls.length).toBe(1);
    const [headUrl, headInit] = headCalls[0];
    expect(String(headUrl)).toBe(
      `https://backend.test/api/seo/html/ahsec/class-12/physics/electric-field`,
    );
    // The probe must carry the bot-probe marker and must NOT forward any
    // inbound conditional headers — otherwise a crawler that arrived
    // with `If-None-Match` could induce a 304 from the backend (no
    // `Last-Modified` body), and we'd silently drop back to the
    // synthesized fallback even when the upstream has an authoritative
    // date.
    const probeHeaders = new Headers(
      (headInit as RequestInit | undefined)?.headers ?? {},
    );
    expect(probeHeaders.get("X-Bot-Probe")).toBe("1");
    expect(probeHeaders.get("X-Bot-Request")).toBe("1");
    expect(probeHeaders.get("If-None-Match")).toBeNull();
    expect(probeHeaders.get("If-Modified-Since")).toBeNull();

    // The upgraded JSON wrapper now carries the upstream Last-Modified
    // (Task #907) — this is the assertion the task explicitly calls for.
    const writeCalls = kv.putSpy.mock.calls.filter((c) => c[0] === cacheKey);
    expect(writeCalls).toHaveLength(1);
    const [, writtenValue, writtenOpts] = writeCalls[0];
    expect(writtenOpts?.expirationTtl).toBe(3600);
    const wrapper = JSON.parse(writtenValue);
    expect(wrapper.body).toBe(LEGACY_HTML);
    expect(wrapper.etag).toBe(expectedEtag);
    expect(wrapper.lastmod).toBe(BACKEND_LM);

    // Subsequent reads must return the upstream Last-Modified — Google's
    // index dates can now line up with reality without forcing a full
    // re-render on every legacy hit.
    vi.setSystemTime(Date.now() + 5 * 60 * 1000);
    const r2 = await workerHandler.fetch(
      botRequest(CONTENT_PATH),
      env,
      ctxAwait,
    );
    await Promise.all(writes.splice(0));

    expect(r2.status).toBe(200);
    expect(r2.headers.get("X-Source")).toBe("bot-cache");
    expect(r2.headers.get("Last-Modified")).toBe(BACKEND_LM);
    expect(await r2.text()).toBe(LEGACY_HTML);

    // Wrapper now parses cleanly — no further upgrade writes, no probe.
    expect(
      kv.putSpy.mock.calls.filter((c) => c[0] === cacheKey),
    ).toHaveLength(1);
  });

  it("legacy plain-string KV entry: increments the bot_cache.legacy_upgrade counter exactly once per legacy hit (Task #908)", async () => {
    const LEGACY_HTML =
      "<html><body>legacy-counter-" + "k".repeat(400) + "</body></html>";

    const botCache = makeExpiringKv();
    const rateLimit = makeExpiringKv();
    const env = makeEnv({ botCache, rateLimit });
    const cacheKey = `bot:content:${CONTENT_PATH}`;

    // Pre-seed BOT_HTML_CACHE with the legacy plain-HTML format —
    // the only path that should fire the legacy_upgrade counter.
    botCache.store.set(cacheKey, { value: LEGACY_HTML, expiresAt: null });

    // recordBotCacheEvent schedules its KV writes via ctx.waitUntil, so
    // we need a context that lets us await those promises — otherwise
    // the assertion races the counter write.
    const writes: Promise<unknown>[] = [];
    const ctxAwait = {
      waitUntil: (p: Promise<unknown>) => {
        writes.push(p);
      },
      passThroughOnException: () => {},
    } as unknown as ExecutionContext;

    // Pin time so the bot-cache bucket index is stable across both
    // requests in this test — proves the second request bumps the
    // SAME bucket counter from "1" to "2", not a fresh bucket to "1".
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-25T07:30:00Z"));
    // bot_cache:count:<kind>:<bucket> — bucket = floor(now / (5 * 60 * 1000))
    const bucket = Math.floor(Date.now() / (5 * 60 * 1000));
    const counterKey = `bot_cache:count:legacy_upgrade:${bucket}`;

    // First legacy hit.
    const r1 = await workerHandler.fetch(
      botRequest(CONTENT_PATH),
      env,
      ctxAwait,
    );
    await Promise.all(writes.splice(0));
    expect(r1.status).toBe(200);
    expect(r1.headers.get("X-Source")).toBe("bot-cache");
    await r1.text();

    // Exactly one write to the legacy_upgrade counter, value "1", with
    // the rolling-hour TTL. The counter must be namespaced under the
    // bot_cache:count:legacy_upgrade:* key family so it shows up
    // alongside hit/miss/conditional_304/fallback in the bot-cache
    // dashboard (and is read by getBotCacheStats /
    // bot-cache-alert::readBuckets via BOT_CACHE_EVENTS).
    const counterWrites1 = rateLimit.putSpy.mock.calls.filter(
      (c) => c[0] === counterKey,
    );
    expect(counterWrites1).toHaveLength(1);
    expect(counterWrites1[0][1]).toBe("1");
    expect(counterWrites1[0][2]?.expirationTtl).toBe(3600);

    // Sibling hit counter must also have fired exactly once (same
    // request) — proves legacy_upgrade is a SUB-event of hit, not a
    // replacement for it. Without this assertion a refactor that
    // accidentally swapped the hit counter for legacy_upgrade would
    // pass.
    const hitCounterKey = `bot_cache:count:hit:${bucket}`;
    expect(
      rateLimit.putSpy.mock.calls.filter((c) => c[0] === hitCounterKey),
    ).toHaveLength(1);

    // Crucially: a SECOND legacy hit must increment to "2" (not
    // re-write "1"). This guards against the easy bug of recording
    // the event from a code path that only fires on the upgrade
    // write itself — the upgrade only happens once per entry, but
    // the legacy_upgrade COUNTER must fire once per legacy READ so
    // operators can see how much legacy traffic the rolling hour
    // is absorbing.
    //
    // Re-seed because the background upgrade from the first request
    // rewrote the KV entry to the JSON wrapper.
    botCache.store.set(cacheKey, { value: LEGACY_HTML, expiresAt: null });

    const r2 = await workerHandler.fetch(
      botRequest(CONTENT_PATH),
      env,
      ctxAwait,
    );
    await Promise.all(writes.splice(0));
    expect(r2.status).toBe(200);
    await r2.text();

    const counterWrites2 = rateLimit.putSpy.mock.calls.filter(
      (c) => c[0] === counterKey,
    );
    expect(counterWrites2).toHaveLength(2);
    expect(counterWrites2[1][1]).toBe("2");

    // And — the parsed-wrapper path (a normal hit, NOT legacy) must
    // NOT fire the counter. After the first legacy upgrade the entry
    // is stored as the JSON wrapper; a follow-up read of THAT entry
    // is a regular hit and must be invisible to the legacy counter.
    const beforeWrites = rateLimit.putSpy.mock.calls.filter(
      (c) => c[0] === counterKey,
    ).length;
    const r3 = await workerHandler.fetch(
      botRequest(CONTENT_PATH),
      env,
      ctxAwait,
    );
    await Promise.all(writes.splice(0));
    expect(r3.status).toBe(200);
    expect(r3.headers.get("X-Source")).toBe("bot-cache");
    await r3.text();
    const afterWrites = rateLimit.putSpy.mock.calls.filter(
      (c) => c[0] === counterKey,
    ).length;
    expect(afterWrites).toBe(beforeWrites);
  });

  it("non-verified Googlebot UA falls through to Pages-origin proxy without writing BOT_HTML_CACHE", async () => {
    const kv = makeExpiringKv();
    const env = makeEnv({ botCache: kv });

    // Construct a request WITHOUT cf.verifiedBot. clientIp ("9.9.9.9")
    // is not in any verified-bot CIDR range, so verifySearchBot must
    // mark this as spoofed and the worker must skip the bot-cache path.
    const req = new Request(`https://syrabit.ai/about`, {
      headers: {
        "user-agent": "Googlebot/2.1",
        "CF-Connecting-IP": "9.9.9.9",
      },
    });

    const resp = await workerHandler.fetch(req, env, ctxNoop);

    expect(resp.status).toBe(200);
    expect(await resp.text()).toBe("<html><body>pages-spa-shell</body></html>");
    // Worker's own marker must be present (proves it went through the
    // proxy branch, not the bot-cache branch).
    expect(resp.headers.get("X-Edge-Worker")).toBe("syrabit-edge");
    // Critical: the spoofed bot must not have populated BOT_HTML_CACHE.
    expect(kv.putSpy).not.toHaveBeenCalled();
    expect(kv.store.size).toBe(0);
  });

  it("/notes subject path: cold request misses, backend is fetched and KV is written; warm request hits cache without re-fetching (Task #28)", async () => {
    const NOTES_PATH = "/notes/class-12/biology";
    const kv = makeExpiringKv();
    const env = makeEnv({ botCache: kv });

    const r1 = await workerHandler.fetch(
      botRequest(NOTES_PATH),
      env,
      ctxNoop,
    );
    expect(r1.status).toBe(200);
    expect(r1.headers.get("X-Cache")).toBe("BOT-KV-MISS");
    expect(r1.headers.get("X-Source")).toMatch(/^bot-prerender(?:-fallback)?$/);
    expect(r1.headers.get("Last-Modified")).toBe(BACKEND_LM);
    expect(r1.headers.get("ETag")).toMatch(/^"[0-9a-f]{12}"$/);
    expect(await r1.text()).toBe(RENDERED_HTML);

    // KV must have been written with the correct key.
    expect(kv.putSpy).toHaveBeenCalledTimes(1);
    const [storedKey, storedValue, storedOpts] = kv.putSpy.mock.calls[0];
    expect(storedKey).toBe(`bot:content:${NOTES_PATH}`);
    expect(storedOpts?.expirationTtl).toBe(3600);
    const wrapper = JSON.parse(storedValue);
    expect(wrapper.body).toBe(RENDERED_HTML);
    expect(wrapper.lastmod).toBe(BACKEND_LM);

    // Verify the backend was called with the correct SEO subject URL.
    const backendCalls = fetchSpy.mock.calls.filter(([url, init]) => {
      const method = ((init as RequestInit | undefined)?.method ?? "GET").toUpperCase();
      return method === "GET" && String(url).includes("/api/seo/html/subject/notes/class-12/biology");
    });
    expect(backendCalls.length).toBe(1);

    // Warm request: KV hit, no backend re-fetch.
    const callsBefore = fetchSpy.mock.calls.length;
    const r2 = await workerHandler.fetch(
      botRequest(NOTES_PATH),
      env,
      ctxNoop,
    );
    expect(r2.status).toBe(200);
    expect(r2.headers.get("X-Cache")).toBe("BOT-KV-HIT");
    expect(r2.headers.get("X-Source")).toBe("bot-cache");
    expect(await r2.text()).toBe(RENDERED_HTML);
    expect(fetchSpy.mock.calls.length).toBe(callsBefore);
    expect(kv.putSpy).toHaveBeenCalledTimes(1);
  });

  it("KV entry expires after expirationTtl seconds: a later request misses and re-renders", async () => {
    const kv = makeExpiringKv();
    const env = makeEnv({ botCache: kv });
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-04-13T09:00:00Z"));

    // The fetch handler's bot path doesn't await ctx.waitUntil, so the
    // KV write is deferred. For deterministic TTL math we drive the
    // KV write directly here via handleBotContentRequest with a ctx
    // that awaits waitUntil promises. The handler is the same code
    // exercised by the worker.fetch path above (cases 1–3) — we're
    // just sequencing the writes synchronously to apply fake timers.
    const writes: Promise<unknown>[] = [];
    const ctxAwait = {
      waitUntil: (p: Promise<unknown>) => {
        writes.push(p);
      },
      passThroughOnException: () => {},
    } as unknown as ExecutionContext;

    const r1 = await handleBotContentRequest(
      env as unknown as Parameters<typeof handleBotContentRequest>[0],
      CONTENT_PATH,
      "66.249.66.1",
      botRequest(CONTENT_PATH),
      ctxAwait,
    );
    await Promise.all(writes.splice(0));
    expect(r1!.headers.get("X-Cache")).toBe("BOT-KV-MISS");
    expect(kv.putSpy).toHaveBeenCalledTimes(1);
    const ttlSec = kv.putSpy.mock.calls[0][2]!.expirationTtl as number;
    expect(ttlSec).toBe(3600);

    // Just before the TTL elapses we still get a hit.
    vi.setSystemTime(Date.now() + (ttlSec - 1) * 1000);
    const r2 = await handleBotContentRequest(
      env as unknown as Parameters<typeof handleBotContentRequest>[0],
      CONTENT_PATH,
      "66.249.66.1",
      botRequest(CONTENT_PATH),
      ctxAwait,
    );
    await Promise.all(writes.splice(0));
    expect(r2!.headers.get("X-Cache")).toBe("BOT-KV-HIT");
    expect(kv.putSpy).toHaveBeenCalledTimes(1);

    // Step past the expiry window — KV mock evicts the entry, so the
    // request must miss and re-render through the backend.
    vi.setSystemTime(Date.now() + 2 * 1000);
    const r3 = await handleBotContentRequest(
      env as unknown as Parameters<typeof handleBotContentRequest>[0],
      CONTENT_PATH,
      "66.249.66.1",
      botRequest(CONTENT_PATH),
      ctxAwait,
    );
    await Promise.all(writes.splice(0));
    expect(r3!.headers.get("X-Cache")).toBe("BOT-KV-MISS");
    expect(r3!.headers.get("X-Source")).toMatch(
      /^bot-prerender(?:-fallback)?$/,
    );
    expect(kv.putSpy).toHaveBeenCalledTimes(2);
  });
});
