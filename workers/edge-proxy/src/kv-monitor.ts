/**
 * Task #476 — Workers KV usage monitor + graceful fallback.
 *
 * Wraps a `KVNamespace` so every read/write/list/delete is:
 *   1. Counted into per-UTC-day, per-binding, per-operation counters.
 *   2. Made fault-tolerant: if KV throws (the typical signal that we're
 *      throttled or have hit the daily quota), reads fall back to the
 *      Cloudflare Cache API and writes are queued in-memory + retried
 *      via `ctx.waitUntil` so the caller never sees an error.
 *   3. Compared against a warning threshold (default 80% of the quota);
 *      crossing it fires a one-shot-per-day alert to the backend, which
 *      surfaces it in the admin notifications panel and emails admins
 *      via the existing notification system.
 *
 * The wrapped namespace keeps the same `.get/.put/.delete/.list` shape
 * so call sites (rate limiter, bot HTML cache, …) need only swap to the
 * wrapped instance — no other changes.
 *
 * Counters live in the worker isolate's memory and are best-effort
 * persisted under a special `__kv_usage:YYYY-MM-DD` key so a fresh
 * isolate can resume the same daily total. The persistence write is
 * routed through the underlying KV directly (NOT through the wrapper)
 * so it never inflates the counters it is reporting on.
 */

export type KvOp = "read" | "write" | "list" | "delete";

export interface KvUsageQuota {
  read: number;
  write: number;
  list: number;
  delete: number;
}

/** Cloudflare Workers Free Plan defaults — overridable via env. */
export const DEFAULT_QUOTA: KvUsageQuota = {
  read: 100_000,
  write: 1_000,
  list: 1_000,
  delete: 1_000,
};

export const DEFAULT_WARNING_PCT = 80;

export interface KvUsageCounters {
  [op: string]: number; // op in KvOp
}

export interface KvBindingUsage {
  binding: string;
  utcDay: string;
  counters: KvUsageCounters;
  quota: KvUsageQuota;
  percentages: Record<KvOp, number>;
  status: "healthy" | "warning" | "exhausted";
  fallbackActive: boolean;
  /** Most recent alert fired for this binding today (or null). Surfaced
   * in the admin KV health panel so operators can confirm an alert was
   * dispatched and at what severity. */
  lastAlertFired: { op: KvOp; severity: "warning" | "exhausted"; at: string } | null;
}

export interface KvUsageSnapshot {
  utcDay: string;
  warningPct: number;
  bindings: KvBindingUsage[];
}

export interface MonitorAlertContext {
  backendUrl?: string;
  alertSecret?: string;
  warningPct?: number;
  quota?: Partial<KvUsageQuota>;
}

/* ─────────────── module-scoped state (per isolate) ─────────────── */

interface BindingState {
  counters: KvUsageCounters;
  fallbackWriteQueue: Map<string, { value: string; opts?: KVNamespacePutOptions }>;
  /** Per-op set of severities already alerted-on today. Stored as
   *  "<op>:<severity>" so we fire warning AND exhausted as separate
   *  alerts (escalation), but never duplicate the same severity for
   *  the same op on the same day. */
  alertedToday: Set<string>;
  fallbackActive: boolean;
  lastAlertFired: { op: KvOp; severity: "warning" | "exhausted"; at: string } | null;
}

const _state: Map<string, BindingState> = new Map();
let _currentDay: string = utcDayKey();

function utcDayKey(d: Date = new Date()): string {
  return d.toISOString().slice(0, 10); // YYYY-MM-DD
}

function rollDayIfNeeded(): void {
  const today = utcDayKey();
  if (today !== _currentDay) {
    _currentDay = today;
    for (const s of _state.values()) {
      s.counters = { read: 0, write: 0, list: 0, delete: 0 };
      s.alertedToday = new Set();
      s.fallbackActive = false;
      // Deferred writes intentionally survive the day rollover: the
      // queue exists precisely because the previous day's quota was
      // exhausted, and the new day's fresh quota is exactly when we
      // want to drain it. The wrapper's replay loop will flush them
      // out on the next put() / on the next ctx.waitUntil tick.
    }
  }
}

/* ───── per-isolate identity for cross-isolate counter aggregation ─────
 * Lazy-initialized: Cloudflare's Workers validator now rejects async I/O,
 * setTimeout, AND random-value generation in module global scope (error
 * code 10021). `crypto.randomUUID()`, `Math.random()`, and `Date.now()`
 * all qualify, so we defer the call to first request inside `_getIsolateId()`.
 */
let _isolateIdCache: string | null = null;
function _getIsolateId(): string {
  if (_isolateIdCache !== null) return _isolateIdCache;
  _isolateIdCache =
    (typeof crypto !== "undefined" && (crypto as Crypto).randomUUID)
      ? (crypto as Crypto).randomUUID()
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
  return _isolateIdCache;
}
const SHARED_COUNTER_PREFIX = "__kv_usage:";
const FLUSH_EVERY_OPS = 10;
const _opsSinceFlush: Map<string, number> = new Map();

function getBindingState(binding: string): BindingState {
  rollDayIfNeeded();
  let s = _state.get(binding);
  if (!s) {
    s = {
      counters: { read: 0, write: 0, list: 0, delete: 0 },
      fallbackWriteQueue: new Map(),
      alertedToday: new Set(),
      fallbackActive: false,
      lastAlertFired: null,
    };
    _state.set(binding, s);
  }
  return s;
}

function bump(binding: string, op: KvOp): void {
  const s = getBindingState(binding);
  s.counters[op] = (s.counters[op] ?? 0) + 1;
}

/** Schedule a shared-counter flush every Nth op via ctx.waitUntil so
 *  cross-isolate aggregation stays roughly current without blocking
 *  the request. Skipped when no underlying KV / ExecutionContext is
 *  available (e.g. in unit tests). */
function maybeFlush(binding: string, kv: KVNamespace | undefined, ctx?: ExecutionContext): void {
  if (!kv) return;
  const n = (_opsSinceFlush.get(binding) ?? 0) + 1;
  if (n < FLUSH_EVERY_OPS) {
    _opsSinceFlush.set(binding, n);
    return;
  }
  _opsSinceFlush.set(binding, 0);
  const p = flushSharedCounter(binding, kv);
  if (ctx) ctx.waitUntil(p); else void p;
}

/** Reset all in-memory state. Test-only. */
export function _resetMonitorStateForTests(): void {
  _state.clear();
  _currentDay = utcDayKey();
  // _opsSinceFlush is a separate module-level Map that tracks the rolling
  // operation count between shared-counter flushes (every FLUSH_EVERY_OPS
  // ops). It must be cleared alongside _state so accumulated ops from one
  // test cannot cause an unexpected flushSharedCounter write → kv.put() in
  // a later test, making put-call-count assertions flaky.
  _opsSinceFlush.clear();
}

/* ───────────────────── snapshot / status ───────────────────── */

function statusFor(counters: KvUsageCounters, quota: KvUsageQuota, warningPct: number):
  { percentages: Record<KvOp, number>; status: "healthy" | "warning" | "exhausted" } {
  const ops: KvOp[] = ["read", "write", "list", "delete"];
  const percentages = {} as Record<KvOp, number>;
  let status: "healthy" | "warning" | "exhausted" = "healthy";
  for (const op of ops) {
    const used = counters[op] ?? 0;
    const cap = quota[op] || 1;
    const pct = Math.round((used / cap) * 1000) / 10; // 1-decimal
    percentages[op] = pct;
    if (pct >= 100 && status !== "exhausted") status = "exhausted";
    else if (pct >= warningPct && status === "healthy") status = "warning";
  }
  return { percentages, status };
}

/** Backwards-compat sync snapshot: uses isolate-local counters only.
 *  Prefer `getUsageSnapshotAggregated` when you have the underlying KV
 *  namespaces, since it sums counters across all worker isolates that
 *  have flushed to the shared `__kv_usage:*` keys. */
export function getUsageSnapshot(
  bindings: string[],
  ctx: MonitorAlertContext = {},
): KvUsageSnapshot {
  rollDayIfNeeded();
  const quota = { ...DEFAULT_QUOTA, ...(ctx.quota || {}) };
  const warningPct = ctx.warningPct ?? DEFAULT_WARNING_PCT;
  const out: KvBindingUsage[] = [];
  for (const b of bindings) {
    const s = getBindingState(b);
    const { percentages, status } = statusFor(s.counters, quota, warningPct);
    out.push({
      binding: b,
      utcDay: _currentDay,
      counters: { ...s.counters },
      quota,
      percentages,
      status,
      fallbackActive: s.fallbackActive,
      lastAlertFired: s.lastAlertFired,
    });
  }
  return { utcDay: _currentDay, warningPct, bindings: out };
}

/** Async cross-isolate snapshot. For each binding, flushes the local
 *  isolate's counters to a shared KV key, lists every isolate's key for
 *  today, and sums them. This is what the dashboard endpoint uses so
 *  the numbers reflect global Worker traffic, not just one isolate. */
export async function getUsageSnapshotAggregated(
  bindings: Array<{ binding: string; kv: KVNamespace }>,
  ctx: MonitorAlertContext = {},
): Promise<KvUsageSnapshot> {
  rollDayIfNeeded();
  const quota = { ...DEFAULT_QUOTA, ...(ctx.quota || {}) };
  const warningPct = ctx.warningPct ?? DEFAULT_WARNING_PCT;
  const out: KvBindingUsage[] = [];
  for (const { binding, kv } of bindings) {
    const local = getBindingState(binding);
    // Flush our local counter so the listing below includes us.
    await flushSharedCounter(binding, kv);
    let aggregated: KvUsageCounters = { read: 0, write: 0, list: 0, delete: 0 };
    try {
      const listResult = await kv.list({
        prefix: `${SHARED_COUNTER_PREFIX}${binding}:${_currentDay}:`,
      });
      const keys = (listResult as { keys: { name: string }[] }).keys || [];
      for (const k of keys) {
        try {
          const raw = await kv.get(k.name);
          if (!raw) continue;
          const parsed = JSON.parse(raw) as KvUsageCounters;
          for (const op of ["read", "write", "list", "delete"] as KvOp[]) {
            aggregated[op] += parsed[op] ?? 0;
          }
        } catch { /* skip malformed isolate entry */ }
      }
    } catch {
      // Listing failed (KV outage) — fall back to local-only counters
      // so the panel still has data to show.
      aggregated = { ...local.counters };
    }
    // If the shared store had nothing yet (cold isolate), use local.
    const sum = aggregated.read + aggregated.write + aggregated.list + aggregated.delete;
    if (sum === 0) aggregated = { ...local.counters };
    const { percentages, status } = statusFor(aggregated, quota, warningPct);
    out.push({
      binding,
      utcDay: _currentDay,
      counters: aggregated,
      quota,
      percentages,
      status,
      fallbackActive: local.fallbackActive,
      lastAlertFired: local.lastAlertFired,
    });
  }
  return { utcDay: _currentDay, warningPct, bindings: out };
}

/** Persist this isolate's per-binding counters under a unique shared
 *  key so other isolates can sum them. Uses the underlying (unwrapped)
 *  KV directly so it doesn't recurse through the monitor wrapper. */
async function flushSharedCounter(binding: string, kv: KVNamespace): Promise<void> {
  rollDayIfNeeded();
  const s = getBindingState(binding);
  const key = `${SHARED_COUNTER_PREFIX}${binding}:${_currentDay}:${_getIsolateId()}`;
  try {
    await kv.put(key, JSON.stringify(s.counters), { expirationTtl: 60 * 60 * 48 });
  } catch { /* shared-store write best-effort */ }
}

/* ─────────────────────── alerting ─────────────────────── */

async function maybeFireAlert(
  binding: string,
  op: KvOp,
  ctx: MonitorAlertContext,
  execCtx?: ExecutionContext,
): Promise<void> {
  const s = getBindingState(binding);
  const quota = { ...DEFAULT_QUOTA, ...(ctx.quota || {}) };
  const warningPct = ctx.warningPct ?? DEFAULT_WARNING_PCT;
  const used = s.counters[op] ?? 0;
  const cap = quota[op] || 1;
  const pct = (used / cap) * 100;
  if (pct < warningPct) return;
  const severity: "warning" | "exhausted" = pct >= 100 ? "exhausted" : "warning";
  // Allow escalation: warning fires once/op/day, exhausted fires once
  // more once that op crosses 100%. Same severity for the same op on
  // the same day is suppressed.
  const dedupeKey = `${op}:${severity}`;
  if (s.alertedToday.has(dedupeKey)) return;
  s.alertedToday.add(dedupeKey);
  s.lastAlertFired = { op, severity, at: new Date().toISOString() };
  if (!ctx.backendUrl || !ctx.alertSecret) return;
  const body = JSON.stringify({
    binding,
    op,
    used,
    quota: cap,
    percentage: Math.round(pct * 10) / 10,
    utc_day: _currentDay,
    severity,
  });
  const fire = fetch(`${ctx.backendUrl.replace(/\/$/, "")}/admin/kv-alerts`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-KV-Alert-Secret": ctx.alertSecret,
    },
    body,
  }).then(() => {}).catch(() => {});
  if (execCtx) execCtx.waitUntil(fire); else await fire;
}

/* ─────────────────── monitored KV wrapper ─────────────────── */

const FALLBACK_QUEUE_MAX = 100;
const FALLBACK_CACHE_HOST = "https://kv-fallback.invalid";

function fallbackCacheKey(binding: string, key: string): Request {
  // The Cache API keys on a Request URL; use a synthetic URL that's
  // namespaced so different bindings don't collide.
  return new Request(
    `${FALLBACK_CACHE_HOST}/${encodeURIComponent(binding)}/${encodeURIComponent(key)}`,
    { method: "GET" },
  );
}

export interface WrapKvOptions extends MonitorAlertContext {
  /** Cloudflare ExecutionContext (for waitUntil-based fallbacks). */
  ctx?: ExecutionContext;
  /** Inject Cache API for tests. */
  cache?: Cache;
}

/** Returns true when the per-op counter has hit/passed the configured quota
 * for this binding — in that case the wrapper proactively short-circuits
 * to the fallback path instead of issuing the KV call (which would just
 * throw with a 429/quota error). This is the "near-quota" branch of the
 * fallback policy, complementing the failure-triggered branch. */
function isOverQuota(binding: string, op: KvOp, ctx: MonitorAlertContext): boolean {
  const quota = { ...DEFAULT_QUOTA, ...(ctx.quota || {}) };
  // Counter has already been bumped for this op when we reach here, so
  // `used` represents what the counter would be AFTER this call. The
  // op that lands exactly on the cap is still allowed to hit KV; only
  // ops past the cap are short-circuited to the fallback path.
  const used = (getBindingState(binding).counters[op] ?? 0);
  const cap = quota[op] || 1;
  return used > cap;
}

export function wrapKvNamespace(
  kv: KVNamespace,
  binding: string,
  opts: WrapKvOptions = {},
): KVNamespace {
  const cache = opts.cache ?? (typeof caches !== "undefined" ? (caches as CacheStorage).default : undefined);

  async function readFromCacheFallback(key: string): Promise<string | null> {
    if (!cache) return null;
    try {
      const r = await cache.match(fallbackCacheKey(binding, key));
      if (!r) return null;
      return await r.text();
    } catch {
      return null;
    }
  }

  async function writeToCacheFallback(key: string, value: string, ttl?: number): Promise<void> {
    if (!cache) return;
    try {
      const headers: Record<string, string> = {
        "Content-Type": "text/plain; charset=utf-8",
      };
      if (ttl && ttl > 0) headers["Cache-Control"] = `max-age=${Math.min(ttl, 86400)}`;
      const resp = new Response(value, { status: 200, headers });
      await cache.put(fallbackCacheKey(binding, key), resp);
    } catch { /* ignore */ }
  }

  function enqueueDeferredWrite(key: string, value: string, putOpts?: KVNamespacePutOptions): void {
    const s = getBindingState(binding);
    if (s.fallbackWriteQueue.size >= FALLBACK_QUEUE_MAX) {
      // Drop the oldest to keep memory bounded.
      const firstKey = s.fallbackWriteQueue.keys().next().value as string | undefined;
      if (firstKey !== undefined) s.fallbackWriteQueue.delete(firstKey);
    }
    s.fallbackWriteQueue.set(key, { value, opts: putOpts });
    s.fallbackActive = true;

    const replay = (async () => {
      // Brief backoff, then a single retry. If KV is back, we drain.
      await new Promise((r) => setTimeout(r, 1000));
      const queue = s.fallbackWriteQueue;
      for (const [k, payload] of Array.from(queue.entries())) {
        try {
          await kv.put(k, payload.value, payload.opts);
          queue.delete(k);
        } catch { /* leave in queue for next time */ }
      }
      if (queue.size === 0) s.fallbackActive = false;
    })();
    if (opts.ctx) opts.ctx.waitUntil(replay); else void replay;
  }

  return {
    async get(key: string, getOpts?: KVNamespaceGetOptions<undefined> | "text"): Promise<string | null> {
      bump(binding, "read");
      maybeFlush(binding, kv, opts.ctx);
      void maybeFireAlert(binding, "read", opts, opts.ctx);
      if (isOverQuota(binding, "read", opts)) {
        // Proactive near-quota fallback: don't even attempt KV.
        getBindingState(binding).fallbackActive = true;
        return await readFromCacheFallback(key);
      }
      try {
        const v = await (kv.get as (k: string, o?: unknown) => Promise<string | null>)(key, getOpts);
        if (v !== null && v !== undefined) {
          // Best-effort: keep a copy in the Cache API so the fallback
          // path can serve last-known-good values during a KV outage.
          if (opts.ctx) opts.ctx.waitUntil(writeToCacheFallback(key, v));
          else void writeToCacheFallback(key, v);
        }
        return v;
      } catch {
        getBindingState(binding).fallbackActive = true;
        return await readFromCacheFallback(key);
      }
    },
    async put(key: string, value: string, putOpts?: KVNamespacePutOptions): Promise<void> {
      bump(binding, "write");
      maybeFlush(binding, kv, opts.ctx);
      void maybeFireAlert(binding, "write", opts, opts.ctx);
      if (isOverQuota(binding, "write", opts)) {
        // Proactive near-quota fallback: queue the write for later.
        enqueueDeferredWrite(key, value, putOpts);
        return;
      }
      try {
        await kv.put(key, value, putOpts);
        // Mirror to cache so reads during a later outage still return the
        // value the caller just wrote.
        const ttl = (putOpts && (putOpts as { expirationTtl?: number }).expirationTtl) || undefined;
        if (opts.ctx) opts.ctx.waitUntil(writeToCacheFallback(key, value, ttl));
        else void writeToCacheFallback(key, value, ttl);
      } catch {
        enqueueDeferredWrite(key, value, putOpts);
      }
    },
    async delete(key: string): Promise<void> {
      bump(binding, "delete");
      maybeFlush(binding, kv, opts.ctx);
      void maybeFireAlert(binding, "delete", opts, opts.ctx);
      try {
        await kv.delete(key);
      } catch { /* swallow — a stale delete just means the value lingers briefly */ }
    },
    async list(listOpts?: KVNamespaceListOptions): Promise<KVNamespaceListResult<unknown>> {
      bump(binding, "list");
      maybeFlush(binding, kv, opts.ctx);
      void maybeFireAlert(binding, "list", opts, opts.ctx);
      try {
        return await kv.list(listOpts);
      } catch {
        getBindingState(binding).fallbackActive = true;
        return { keys: [], list_complete: true, cacheStatus: null } as unknown as KVNamespaceListResult<unknown>;
      }
    },
    // The wrapped namespace keeps the same getWithMetadata signature for
    // call sites that need it; route reads through the same counters and
    // fallback path.
    async getWithMetadata(key: string): Promise<KVNamespaceGetWithMetadataResult<string, unknown>> {
      bump(binding, "read");
      void maybeFireAlert(binding, "read", opts, opts.ctx);
      try {
        const r = await kv.getWithMetadata(key);
        return r as KVNamespaceGetWithMetadataResult<string, unknown>;
      } catch {
        getBindingState(binding).fallbackActive = true;
        const v = await readFromCacheFallback(key);
        return { value: v, metadata: null, cacheStatus: null } as KVNamespaceGetWithMetadataResult<string, unknown>;
      }
    },
  } as unknown as KVNamespace;
}
