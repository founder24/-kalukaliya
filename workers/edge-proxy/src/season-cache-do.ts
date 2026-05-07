/**
 * season-cache-do.ts — Task #575 Durable Object that owns the
 * authoritative `/api/health/season` snapshot for the entire region.
 *
 * Why a DO instead of just isolate-local globals + CF cache:
 *   * Per-isolate caching alone gives us O(N_isolates × 1/min)
 *     fan-out to the FastAPI origin, which can spike under load
 *     (Cloudflare runs hundreds of isolates per POP).
 *   * The companion `season-cache.ts` keeps an isolate-level shadow
 *     so `getCacheTtl()` stays a synchronous lookup at every callsite,
 *     but the SHARED 60 s contract — exactly one refresh per 60 s
 *     across every isolate — is enforced here.
 *
 * Single, well-known DO ID (`idFromName("global")`): every isolate
 * resolves the same DO instance and shares the cached payload. The
 * DO sleeps when idle and wakes in ~1 ms, so under steady traffic
 * it stays warm.
 *
 * API (internal worker → DO):
 *   GET https://season-cache/snapshot
 *   Response: SeasonSnapshot JSON (always 200; FALLBACK on any error)
 *
 * Failure mode:
 *   * Origin unreachable → return last-known snapshot (even if older
 *     than `TTL_MS`). A multi-minute API blip during exam mode must
 *     NOT yank every route's TTL back down mid-window.
 *   * No prior successful fetch → return FALLBACK ("normal"). One
 *     request sees the previous season, then the refresh completes.
 *
 * Founder locks: this DO never touches `/api/me/quota` (5 s edge
 * TTL — Task #513) or `/api/ai/chat` (edge bypass — Task #549). It
 * only stretches the existing per-route TTLs declared in
 * `monitored-urls.json`.
 */

import { SEASON_HEALTH_PATH } from "./monitored-urls";

export type SeasonName = "exam" | "results" | "normal";

export interface SeasonSnapshot {
  season: SeasonName;
  ttl_multiplier: number;
  fetched_at_ms: number;
}

const TTL_MS = 60_000;
const TIMEOUT_MS = 3_000;

const FALLBACK: SeasonSnapshot = {
  season: "normal",
  ttl_multiplier: 1.0,
  fetched_at_ms: 0,
};

interface DOEnv {
  BACKEND_URL: string;
}

export class SeasonCacheDO implements DurableObject {
  private readonly state: DurableObjectState;
  private readonly env: DOEnv;
  // In-memory mirror of the persisted snapshot — populated lazily on
  // the first request after a DO wake. Keeps the hot path off
  // storage.get for every snapshot request.
  private inMemory: SeasonSnapshot | null = null;
  private inflight: Promise<SeasonSnapshot> | null = null;

  constructor(state: DurableObjectState, env: DOEnv) {
    this.state = state;
    this.env = env;
  }

  async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname !== "/snapshot") {
      return new Response(JSON.stringify({ error: "unknown path" }), { status: 404 });
    }
    const snap = await this.getSnapshot();
    return new Response(JSON.stringify(snap), {
      status: 200,
      headers: { "Content-Type": "application/json", "X-Source": "season-cache-do" },
    });
  }

  private async getSnapshot(): Promise<SeasonSnapshot> {
    if (this.inMemory === null) {
      const persisted = await this.state.storage.get<SeasonSnapshot>("snapshot");
      if (persisted) this.inMemory = persisted;
    }
    const now = Date.now();
    if (this.inMemory && now - this.inMemory.fetched_at_ms < TTL_MS) {
      return this.inMemory;
    }
    if (this.inflight) {
      return this.inMemory ?? FALLBACK;
    }
    this.inflight = this.refresh()
      .then((snap) => {
        this.inMemory = snap;
        return snap;
      })
      .catch(() => this.inMemory ?? FALLBACK)
      .finally(() => {
        this.inflight = null;
      });
    if (!this.inMemory) {
      // Cold start: block briefly so the first caller after a fresh
      // wake sees a real classification — the calling worker still
      // has its own non-blocking shadow path so user-visible latency
      // is unaffected.
      try {
        return await this.inflight;
      } catch {
        return FALLBACK;
      }
    }
    // Warm path: kick refresh in the background and serve the
    // (slightly stale) in-memory snapshot.
    this.state.waitUntil(this.inflight.then(() => undefined).catch(() => undefined));
    return this.inMemory;
  }

  private async refresh(): Promise<SeasonSnapshot> {
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), TIMEOUT_MS);
    try {
      const res = await fetch(`${this.env.BACKEND_URL}${SEASON_HEALTH_PATH}`, {
        method: "GET",
        headers: { "Accept": "application/json" },
        signal: ac.signal,
        // Belt-and-braces: even though the DO is the single source of
        // truth, we let CF's per-POP cache absorb the very rare
        // post-restart cold call when multiple POPs wake the DO at
        // the same minute boundary.
        cf: { cacheTtl: 60, cacheEverything: true } as RequestInitCfProperties,
      });
      if (!res.ok) throw new Error(`season fetch ${res.status}`);
      const body = (await res.json()) as { season?: SeasonName; ttl_multiplier?: number };
      const season: SeasonName =
        body.season === "exam" || body.season === "results" || body.season === "normal"
          ? body.season
          : "normal";
      const ttl_multiplier =
        typeof body.ttl_multiplier === "number" && Number.isFinite(body.ttl_multiplier)
          ? body.ttl_multiplier
          : 1.0;
      const snap: SeasonSnapshot = { season, ttl_multiplier, fetched_at_ms: Date.now() };
      await this.state.storage.put("snapshot", snap);
      return snap;
    } finally {
      clearTimeout(timer);
    }
  }
}
