/**
 * edge-proxy/src/index.ts
 *
 * Lightweight Cloudflare Worker that fronts the API. Originally a
 * Workers-for-Platforms tenant dispatcher, simplified into a routing
 * shim when dispatch moved to GCP Cloud Run. As of the V4 cutover the
 * `ORIGIN_TARGET` flag selects the Azure Container Apps origin.
 *
 * Origin selection
 * ────────────────
 *   ORIGIN_TARGET=azure     → AZURE_BACKEND_URL  (Azure Container Apps syrabit-backend)
 *
 * Performance boost wiring
 * ────────────────────────
 * • Cache-Control headers set by the downstream service are respected
 *   verbatim; cache layer (Cloudflare or origin LB) caches static + API
 *   responses.
 * • Early Hints (103) are emitted for key assets so browsers start
 *   fetching sub-resources before the full response arrives.
 *
 * Required wrangler secrets / vars
 * ─────────────────────────────────
 *   ORIGIN_TARGET            — "azure" (default)
 *   AZURE_BACKEND_URL        — https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io
 *   DISPATCH_SHARED_SECRET   — random 256-bit hex, matched server-side
 */

export interface Env {
  ORIGIN_TARGET?: string;
  AZURE_BACKEND_URL?: string;
  DISPATCH_SHARED_SECRET: string;
  // Durable Object bindings (see wrangler.toml).
  CHAT_SESSION?: DurableObjectNamespace;
  RATE_LIMITER?: DurableObjectNamespace;
  DO_CHAT_ON?: string;
  EDGE_CHAT_RATE_LIMIT?: string;
  EDGE_CHAT_USER_RATE_LIMIT?: string;
  // HS256 secret mirrored from the backend (`JWT_SECRET`) so the edge
  // can cryptographically verify the Bearer token before deriving a
  // per-user limiter scope. When unset the limiter degrades to per-IP
  // — never to client-supplied identity.
  EDGE_JWT_HS256_SECRET?: string;
  // Task #405 — KV namespace backing /api/edge/kv-cache/<key>. Bound
  // in wrangler.toml under both env.production and env.staging.
  CF_EDGE_CACHE?: KVNamespace;
  // Shared secret matched against the X-Edge-Admin-Secret header on
  // /api/edge/kv-cache/* (and the existing /api/edge/kv-usage probe).
  // Mirrors the backend env var of the same name.
  D1_SYNC_SECRET?: string;
  // Task #424 — shared secret used when this worker POSTs a KV-quota
  // alert to the backend's /admin/kv-alerts endpoint. Mirrors the
  // backend env var of the same name. When unset the worker still
  // counts ops but skips the alert dispatch.
  KV_ALERT_SECRET?: string;
  // Task #424 — optional override of the per-op daily quota used when
  // computing CF_EDGE_CACHE percentages. Same JSON shape the
  // workers/edge-proxy kv-monitor accepts: {"read":100000,"write":1000,
  // "list":1000,"delete":1000}. Unset → DEFAULT_KV_QUOTA below.
  KV_QUOTA?: string;
  // Task #424 — warning threshold (percent of quota) at which the
  // worker fires the first alert for a binding/op pair. Defaults to 80.
  KV_WARNING_PCT?: string;
}

// Re-export DO classes so wrangler can find them at the worker entrypoint.
export { ChatSession } from './chat_session';
export { RateLimiter } from './rate_limiter';
export { deriveLimiterScope, verifyHS256Jwt } from './edge_identity';
import { deriveLimiterScope } from './edge_identity';

/**
 * Task #386 — dispatch `/do/...` paths to the corresponding Durable
 * Object namespace. Returns null when the path is not a DO route so
 * the caller can fall through to the regular origin proxy.
 *
 * Auth: same shared bearer as the regular dispatch — the backend
 * `do_chat.py` shim sets `Authorization: Bearer <DISPATCH_SHARED_SECRET>`
 * on every call.
 */
async function dispatchDurableObject(
  request: Request,
  env: Env,
  url: URL,
): Promise<Response | null> {
  if (!url.pathname.startsWith('/do/')) {
    return null;
  }

  // Bearer auth is mandatory for DO dispatch — these endpoints carry
  // chat-session payloads and decrement rate-limit counters, so an
  // unauthenticated caller must never reach them.
  const auth = request.headers.get('authorization') || '';
  const expected = `Bearer ${env.DISPATCH_SHARED_SECRET ?? ''}`;
  if (!env.DISPATCH_SHARED_SECRET || auth !== expected) {
    return new Response('unauthorised', { status: 401 });
  }

  // /do/chat-session/<id>[/<sub>]  — sub-paths (e.g. /typing) are
  // forwarded to the DO so it can dispatch on the path internally.
  const chatMatch = url.pathname.match(/^\/do\/chat-session\/([^/]+)(?:\/[^/]+)?$/);
  if (chatMatch) {
    if (!env.CHAT_SESSION) {
      return new Response('CHAT_SESSION binding missing', { status: 503 });
    }
    const id = env.CHAT_SESSION.idFromName(chatMatch[1]);
    const stub = env.CHAT_SESSION.get(id);
    return stub.fetch(request);
  }

  // /do/rate-limiter/check  — singleton DO id "global" so the same
  // bucket is consulted from every region.
  if (url.pathname === '/do/rate-limiter/check') {
    if (!env.RATE_LIMITER) {
      return new Response('RATE_LIMITER binding missing', { status: 503 });
    }
    const id = env.RATE_LIMITER.idFromName('global');
    const stub = env.RATE_LIMITER.get(id);
    return stub.fetch(request);
  }

  return new Response('unknown DO route', { status: 404 });
}

/**
 * Task #424 — KV usage counters for the CF_EDGE_CACHE binding.
 *
 * `dispatchKvCache` (Task #405) was wired in isolation: every read /
 * write / delete that lands on `CF_EDGE_CACHE` succeeded but never
 * incremented the per-binding tally that the admin "KV usage" panel
 * (`/admin/kv-health` → `/api/edge/kv-usage`) relies on. Once the
 * backend's `CF_EDGE_CACHE_ON` flag flips, the new namespace will absorb
 * a meaningful share of the daily KV op quota and operators need to see
 * that burn alongside RATE_LIMIT / BOT_HTML_CACHE — both for the
 * dashboard percentages and so the existing kv-alerts pipeline can page
 * before the quota is exhausted.
 *
 * The counters live in module-scoped memory (per-isolate, per-UTC-day)
 * — same shape and quota defaults the workers/edge-proxy `kv-monitor`
 * uses so the admin route can render the new row identically.
 *
 * Task #454 — cross-isolate aggregation. Per-isolate counters were
 * silently under-reporting global burn by 10-20× on a hot day because
 * the snapshot endpoint only saw whichever isolate happened to handle
 * the probe. We now mirror the workers/edge-proxy `kv-monitor` trick:
 * each isolate periodically flushes its counters under
 * `__kv_usage:<binding>:<utc-day>:<isolate-id>` (stored inside the
 * binding's own KV namespace), and `dispatchKvUsage` lists+sums every
 * isolate's key before returning the snapshot. Alert dispatch is still
 * per-isolate idempotent via `alertedToday` — operators see the global
 * tally, but every isolate independently fires the warning/exhausted
 * webhook once per day so a quiet isolate can still surface a problem.
 */
type KvOpName = 'read' | 'write' | 'delete' | 'list';

interface KvUsageQuota {
  read: number;
  write: number;
  list: number;
  delete: number;
}

const DEFAULT_KV_QUOTA: KvUsageQuota = {
  read: 100_000,
  write: 1_000,
  list: 1_000,
  delete: 1_000,
};

const DEFAULT_WARNING_PCT = 80;

interface KvBindingState {
  counters: Record<KvOpName, number>;
  // "<op>:<severity>" — fired-once-per-day-per-pair so warning AND
  // exhausted both surface (escalation) but neither floods the inbox.
  alertedToday: Set<string>;
}

const _kvState: Map<string, KvBindingState> = new Map();
let _kvCurrentDay = utcDayKey();

// Task #454 — cross-isolate aggregation primitives. Each worker isolate
// gets a stable UUID (lazy-init: the Workers validator rejects async I/O
// AND random-value generation in module global scope, so we defer the
// call to first use) and periodically writes its counters under
// `__kv_usage:<binding>:<day>:<isolate-id>` inside the binding's KV
// namespace. The aggregated snapshot lists+sums every isolate's key.
const SHARED_KV_USAGE_PREFIX = '__kv_usage:';
const KV_FLUSH_EVERY_OPS = 10;
const _kvOpsSinceFlush: Map<string, number> = new Map();
let _kvIsolateIdCache: string | null = null;
function _kvIsolateId(): string {
  if (_kvIsolateIdCache !== null) return _kvIsolateIdCache;
  _kvIsolateIdCache =
    typeof crypto !== 'undefined' && (crypto as Crypto).randomUUID
      ? (crypto as Crypto).randomUUID()
      : Math.random().toString(36).slice(2) + Date.now().toString(36);
  return _kvIsolateIdCache;
}

function _sharedCounterKey(binding: string): string {
  return `${SHARED_KV_USAGE_PREFIX}${binding}:${_kvCurrentDay}:${_kvIsolateId()}`;
}

/** Persist this isolate's counters under its shared key. Best-effort —
 *  a failed write just means the next snapshot poll under-counts this
 *  isolate by a few ops until the next flush succeeds. */
async function _flushSharedKvCounter(
  binding: string,
  kv: KVNamespace,
): Promise<void> {
  _rollKvDayIfNeeded();
  const s = _kvBindingState(binding);
  try {
    await kv.put(_sharedCounterKey(binding), JSON.stringify(s.counters), {
      // 48h TTL: long enough that yesterday's keys naturally expire after
      // today's snapshot has accumulated, short enough that abandoned
      // isolate IDs don't pile up in the namespace.
      expirationTtl: 60 * 60 * 48,
    });
  } catch {
    /* shared-store write best-effort */
  }
}

/** Schedule a shared-counter flush every Nth op via ctx.waitUntil so
 *  cross-isolate aggregation stays roughly current without blocking the
 *  request. The `_sharedCounterKey` write itself bumps the binding's KV
 *  write counter — that's intentional accounting (these PUTs really do
 *  consume quota), and FLUSH_EVERY_OPS keeps the overhead at ~10% of
 *  total writes which is well below the warning threshold for a busy
 *  binding. */
function _maybeFlushSharedKvCounter(
  binding: string,
  kv: KVNamespace | undefined,
  ctx: ExecutionContext,
): void {
  if (!kv) return;
  const n = (_kvOpsSinceFlush.get(binding) ?? 0) + 1;
  if (n < KV_FLUSH_EVERY_OPS) {
    _kvOpsSinceFlush.set(binding, n);
    return;
  }
  _kvOpsSinceFlush.set(binding, 0);
  ctx.waitUntil(_flushSharedKvCounter(binding, kv));
}

function utcDayKey(d: Date = new Date()): string {
  return d.toISOString().slice(0, 10);
}

function _rollKvDayIfNeeded(): void {
  const today = utcDayKey();
  if (today !== _kvCurrentDay) {
    _kvCurrentDay = today;
    for (const s of _kvState.values()) {
      s.counters = { read: 0, write: 0, list: 0, delete: 0 };
      s.alertedToday = new Set();
    }
  }
}

function _kvBindingState(binding: string): KvBindingState {
  _rollKvDayIfNeeded();
  let s = _kvState.get(binding);
  if (!s) {
    s = {
      counters: { read: 0, write: 0, list: 0, delete: 0 },
      alertedToday: new Set(),
    };
    _kvState.set(binding, s);
  }
  return s;
}

function _resolveKvQuota(env: Env): KvUsageQuota {
  if (!env.KV_QUOTA) return DEFAULT_KV_QUOTA;
  try {
    const parsed = JSON.parse(env.KV_QUOTA) as Partial<KvUsageQuota>;
    return { ...DEFAULT_KV_QUOTA, ...parsed };
  } catch {
    return DEFAULT_KV_QUOTA;
  }
}

function _resolveKvWarningPct(env: Env): number {
  const raw = Number(env.KV_WARNING_PCT ?? '');
  if (Number.isFinite(raw) && raw > 0 && raw <= 100) return raw;
  return DEFAULT_WARNING_PCT;
}

interface KvIsolateBreakdownEntry {
  // Anonymised short id (8 hex chars) — distinct enough for an
  // operator to tell isolates apart without leaking the full UUID.
  id: string;
  counters: Record<KvOpName, number>;
}

interface KvBindingSnapshot {
  binding: string;
  utcDay: string;
  counters: Record<KvOpName, number>;
  quota: KvUsageQuota;
  percentages: Record<KvOpName, number>;
  status: 'healthy' | 'warning' | 'exhausted';
  fallbackActive: boolean;
  // Task #510 — populated only when the snapshot caller asks for the
  // per-isolate breakdown (`?breakdown=isolates`). Omitted by default
  // so the existing default response shape is unchanged.
  isolates?: KvIsolateBreakdownEntry[];
}

/** Anonymise a raw isolate id (UUID or fallback) to an 8-char tag.
 *  We only need distinctness for the admin UI — never identifiability. */
function _shortIsolateId(raw: string): string {
  const cleaned = raw.replace(/-/g, '');
  return cleaned.slice(0, 8) || raw.slice(0, 8);
}

function _kvSnapshotFromCounters(
  binding: string,
  counters: Record<KvOpName, number>,
  env: Env,
): KvBindingSnapshot {
  const quota = _resolveKvQuota(env);
  const warningPct = _resolveKvWarningPct(env);
  const ops: KvOpName[] = ['read', 'write', 'list', 'delete'];
  const percentages = {} as Record<KvOpName, number>;
  let status: 'healthy' | 'warning' | 'exhausted' = 'healthy';
  for (const op of ops) {
    const used = counters[op] ?? 0;
    const cap = quota[op] || 1;
    const pct = Math.round((used / cap) * 1000) / 10;
    percentages[op] = pct;
    if (pct >= 100 && status !== 'exhausted') status = 'exhausted';
    else if (pct >= warningPct && status === 'healthy') status = 'warning';
  }
  return {
    binding,
    utcDay: _kvCurrentDay,
    counters: { ...counters },
    quota,
    percentages,
    status,
    fallbackActive: false,
  };
}

function _kvSnapshotFor(binding: string, env: Env): KvBindingSnapshot {
  const s = _kvBindingState(binding);
  return _kvSnapshotFromCounters(binding, s.counters, env);
}

/**
 * Task #454 — sum the local isolate's counters with every other
 * isolate's persisted counters under `__kv_usage:<binding>:<day>:*`.
 * Used by `dispatchKvUsage` so the dashboard reflects global burn
 * instead of whichever isolate happened to serve the probe.
 *
 * Local counters are flushed first so this isolate's contribution is
 * always part of the listed set (rather than added on top, which would
 * double-count the ops we just flushed). If the listing fails (KV
 * outage) we fall back to local-only counters so the panel still
 * renders something.
 */
export async function _aggregateKvCountersAcrossIsolates(
  binding: string,
  kv: KVNamespace,
): Promise<Record<KvOpName, number>> {
  const { total } = await _collectKvIsolatesBreakdown(binding, kv);
  return total;
}

/**
 * Task #510 — same listing pass as `_aggregateKvCountersAcrossIsolates`
 * but also returns the per-isolate counters so the admin
 * `/admin/kv-health` panel can show which isolate is hottest. The
 * isolate IDs are anonymised (`_shortIsolateId`) before being returned;
 * the worker only needs distinctness for the UI, not identifiability.
 *
 * On listing failure we fall back to a single-entry breakdown reflecting
 * THIS isolate's local counters so the panel still renders something.
 */
export async function _collectKvIsolatesBreakdown(
  binding: string,
  kv: KVNamespace,
): Promise<{
  total: Record<KvOpName, number>;
  isolates: KvIsolateBreakdownEntry[];
}> {
  _rollKvDayIfNeeded();
  const local = _kvBindingState(binding);
  await _flushSharedKvCounter(binding, kv);
  const total: Record<KvOpName, number> = {
    read: 0,
    write: 0,
    list: 0,
    delete: 0,
  };
  const isolates: KvIsolateBreakdownEntry[] = [];
  const ops: KvOpName[] = ['read', 'write', 'list', 'delete'];
  const prefix = `${SHARED_KV_USAGE_PREFIX}${binding}:${_kvCurrentDay}:`;
  try {
    const listResult = await kv.list({ prefix });
    const keys = (listResult as { keys: { name: string }[] }).keys || [];
    for (const k of keys) {
      try {
        const raw = await kv.get(k.name);
        if (!raw) continue;
        const parsed = JSON.parse(raw) as Partial<Record<KvOpName, number>>;
        const counters: Record<KvOpName, number> = {
          read: 0,
          write: 0,
          list: 0,
          delete: 0,
        };
        for (const op of ops) {
          const v = parsed[op] ?? 0;
          counters[op] = v;
          total[op] += v;
        }
        const rawId = k.name.slice(prefix.length);
        isolates.push({ id: _shortIsolateId(rawId), counters });
      } catch {
        /* skip malformed isolate entry */
      }
    }
  } catch {
    return {
      total: { ...local.counters },
      isolates: [
        { id: _shortIsolateId(_kvIsolateId()), counters: { ...local.counters } },
      ],
    };
  }
  const sum = total.read + total.write + total.list + total.delete;
  if (sum === 0) {
    return {
      total: { ...local.counters },
      isolates: [
        { id: _shortIsolateId(_kvIsolateId()), counters: { ...local.counters } },
      ],
    };
  }
  // Sort hottest-first by total ops so the UI naturally shows the
  // isolate(s) most worth looking at at the top.
  isolates.sort((a, b) => {
    const sumA = a.counters.read + a.counters.write + a.counters.list + a.counters.delete;
    const sumB = b.counters.read + b.counters.write + b.counters.list + b.counters.delete;
    return sumB - sumA;
  });
  return { total, isolates };
}

/** Test-only reset hook so unit tests can start from a clean slate. */
export function _resetKvCountersForTests(): void {
  _kvState.clear();
  _kvOpsSinceFlush.clear();
  _kvIsolateIdCache = null;
  _kvCurrentDay = utcDayKey();
}

/** Test-only override of the per-isolate identity. Unit tests use this
 *  to simulate a second isolate writing under a different shared key. */
export function _setKvIsolateIdForTests(id: string | null): void {
  _kvIsolateIdCache = id;
}

/** Test-only seed for the in-memory per-isolate counters. Lets a unit
 *  test pretend this isolate has already handled some ops without
 *  going through the full _bumpKvCounter path (which would also fire
 *  alerts and a shared-counter flush). */
export function _seedKvCountersForTests(
  binding: string,
  counters: Partial<Record<KvOpName, number>>,
): void {
  const s = _kvBindingState(binding);
  for (const op of ['read', 'write', 'list', 'delete'] as KvOpName[]) {
    if (counters[op] !== undefined) s.counters[op] = counters[op] as number;
  }
}

function _backendUrlForAlert(env: Env): string | null {
  const raw = (env.AZURE_BACKEND_URL ?? '').trim().replace(/\/$/, '');
  return raw || null;
}

async function _maybeFireKvAlert(
  binding: string,
  op: KvOpName,
  env: Env,
  ctx: ExecutionContext,
): Promise<void> {
  const s = _kvBindingState(binding);
  const quota = _resolveKvQuota(env);
  const warningPct = _resolveKvWarningPct(env);
  const used = s.counters[op] ?? 0;
  const cap = quota[op] || 1;
  const pct = (used / cap) * 100;
  if (pct < warningPct) return;
  const severity: 'warning' | 'exhausted' = pct >= 100 ? 'exhausted' : 'warning';
  const dedupeKey = `${op}:${severity}`;
  if (s.alertedToday.has(dedupeKey)) return;
  // Validate config BEFORE marking the dedupe key so a temporary
  // misconfig (e.g. KV_ALERT_SECRET unset at the start of the UTC day)
  // doesn't permanently suppress the alert for the rest of the day —
  // once the secret is set the next op past the threshold will fire.
  const backendUrl = _backendUrlForAlert(env);
  const alertSecret = (env.KV_ALERT_SECRET ?? '').trim();
  if (!backendUrl || !alertSecret) return;
  s.alertedToday.add(dedupeKey);
  const body = JSON.stringify({
    binding,
    op,
    used,
    quota: cap,
    percentage: Math.round(pct * 10) / 10,
    utc_day: _kvCurrentDay,
    severity,
  });
  const fire = fetch(`${backendUrl}/admin/kv-alerts`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-KV-Alert-Secret': alertSecret,
    },
    body,
  })
    .then(() => undefined)
    .catch((err) => {
      console.warn('[edge-proxy] kv-alert dispatch failed', { binding, op, err });
    });
  ctx.waitUntil(fire);
}

function _bumpKvCounter(
  binding: string,
  op: KvOpName,
  env: Env,
  ctx: ExecutionContext,
): void {
  const s = _kvBindingState(binding);
  s.counters[op] = (s.counters[op] ?? 0) + 1;
  // Fire-and-forget: alert dispatch is dedupe-guarded so calling it on
  // every op is safe; the actual POST happens at most twice per
  // (binding,op) per UTC day (warning + exhausted).
  void _maybeFireKvAlert(binding, op, env, ctx);
  // Task #454 — periodically mirror our local counters to the shared
  // `__kv_usage:*` key so the snapshot endpoint can sum across every
  // isolate that has handled CF_EDGE_CACHE traffic.
  if (binding === 'CF_EDGE_CACHE') {
    _maybeFlushSharedKvCounter(binding, env.CF_EDGE_CACHE, ctx);
  }
}

/**
 * Task #424 — `/api/edge/kv-usage` exposed by the artifacts edge worker.
 * Returns the same per-binding snapshot shape the workers/edge-proxy
 * `/api/edge/kv-usage` endpoint emits, restricted to the bindings this
 * worker actually owns (CF_EDGE_CACHE today). The admin /kv-health
 * route merges this snapshot into its primary one so the dashboard sees
 * a CF_EDGE_CACHE row alongside RATE_LIMIT / BOT_HTML_CACHE.
 *
 * Auth: same X-Edge-Admin-Secret = D1_SYNC_SECRET handshake the
 * dispatchKvCache routes use.
 */
async function dispatchKvUsage(
  request: Request,
  env: Env,
  url: URL,
): Promise<Response | null> {
  if (url.pathname !== '/api/edge/kv-usage' || request.method !== 'GET') {
    return null;
  }
  const expected = env.D1_SYNC_SECRET ?? '';
  const provided = request.headers.get('x-edge-admin-secret') ?? '';
  if (!expected || !constantTimeEqual(provided, expected)) {
    return new Response('unauthorised', { status: 401 });
  }
  _rollKvDayIfNeeded();
  // Task #510 — opt-in per-isolate breakdown. Default response shape
  // (no `isolates` key) is preserved so existing callers keep working.
  const wantBreakdown = url.searchParams.get('breakdown') === 'isolates';
  const bindings: KvBindingSnapshot[] = [];
  if (env.CF_EDGE_CACHE) {
    // Task #454 — sum every isolate's persisted counters before
    // returning so the dashboard sees global burn, not just whichever
    // isolate happened to serve this probe.
    const { total, isolates } = await _collectKvIsolatesBreakdown(
      'CF_EDGE_CACHE',
      env.CF_EDGE_CACHE,
    );
    const snapshot = _kvSnapshotFromCounters('CF_EDGE_CACHE', total, env);
    if (wantBreakdown) {
      snapshot.isolates = isolates;
    }
    bindings.push(snapshot);
  }
  return jsonResponse({
    utcDay: _kvCurrentDay,
    warningPct: _resolveKvWarningPct(env),
    bindings,
  });
}

/**
 * Task #405 — KV write-through cache routes.
 *
 *   GET    /api/edge/kv-cache/<key>     → 200 { value, ttl_s } | 404
 *   PUT    /api/edge/kv-cache/<key>     body { value, ttl_s }  → 200 { ok, ttl_s }
 *   DELETE /api/edge/kv-cache/<key>                            → 200 { ok }
 *
 * Auth: every request must carry X-Edge-Admin-Secret matching the
 * worker's `D1_SYNC_SECRET` (the same handshake the existing
 * /api/edge/kv-usage probe uses, so no new credential needs
 * provisioning). The backend's `kv_cache.KvCache` calls these from the
 * write-through path when `CF_EDGE_CACHE_ON=1`. Returns null if the
 * path is not a kv-cache route so the caller can fall through.
 */
async function dispatchKvCache(
  request: Request,
  env: Env,
  url: URL,
  ctx: ExecutionContext,
): Promise<Response | null> {
  const prefix = '/api/edge/kv-cache/';
  if (!url.pathname.startsWith(prefix)) {
    return null;
  }
  const expected = env.D1_SYNC_SECRET ?? '';
  const provided = request.headers.get('x-edge-admin-secret') ?? '';
  if (!expected || !constantTimeEqual(provided, expected)) {
    return new Response('unauthorised', { status: 401 });
  }
  if (!env.CF_EDGE_CACHE) {
    return new Response('CF_EDGE_CACHE binding missing', { status: 503 });
  }
  let key: string;
  try {
    key = decodeURIComponent(url.pathname.slice(prefix.length));
  } catch {
    return new Response('invalid key', { status: 400 });
  }
  // Cloudflare KV rejects keys >512 bytes. Reject early with a clear
  // status rather than letting the KV API surface a generic 400.
  if (!key || key.length > 512) {
    return new Response('invalid key', { status: 400 });
  }

  if (request.method === 'GET') {
    const stored = await env.CF_EDGE_CACHE.getWithMetadata<unknown, { ttl_s?: number }>(
      key,
      { type: 'json' },
    );
    // Task #424 — every CF_EDGE_CACHE op (hit OR miss) consumes one
    // KV read against the daily quota, so the counter is bumped before
    // we branch on the lookup result.
    _bumpKvCounter('CF_EDGE_CACHE', 'read', env, ctx);
    if (stored.value === null) {
      return new Response('not found', { status: 404 });
    }
    const ttl = stored.metadata?.ttl_s ?? 300;
    return jsonResponse({ value: stored.value, ttl_s: ttl });
  }

  if (request.method === 'PUT') {
    const body = await request.json().catch(() => null) as
      | { value?: unknown; ttl_s?: number }
      | null;
    if (!body || !('value' in body)) {
      return new Response('missing value', { status: 400 });
    }
    // Cloudflare KV requires expirationTtl >= 60s; clamp to a sensible
    // upper bound (24h) so a runaway caller cannot pin a key forever.
    const ttl = Math.max(60, Math.min(86_400, Number(body.ttl_s ?? 300)));
    try {
      await env.CF_EDGE_CACHE.put(key, JSON.stringify(body.value), {
        expirationTtl: ttl,
        metadata: { ttl_s: ttl },
      });
    } catch (err) {
      console.warn('[edge-proxy] kv-cache put failed', { key, err });
      return new Response('kv put failed', { status: 502 });
    }
    // Task #424 — only count a write once we know KV accepted it; a
    // failed put is reported back to the caller and shouldn't inflate
    // the quota tally that drives the alert pipeline.
    _bumpKvCounter('CF_EDGE_CACHE', 'write', env, ctx);
    return jsonResponse({ ok: true, ttl_s: ttl });
  }

  if (request.method === 'DELETE') {
    try {
      await env.CF_EDGE_CACHE.delete(key);
    } catch (err) {
      console.warn('[edge-proxy] kv-cache delete failed', { key, err });
      return new Response('kv delete failed', { status: 502 });
    }
    _bumpKvCounter('CF_EDGE_CACHE', 'delete', env, ctx);
    return jsonResponse({ ok: true });
  }

  return new Response('method not allowed', { status: 405 });
}

function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) {
    return false;
  }
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) {
    diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return diff === 0;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const EARLY_HINTS_ASSETS = [
  '</fonts/inter.woff2>; rel=preload; as=font; crossorigin',
  '</icons/icon-192x192.png>; rel=preload; as=image',
];

function resolveOrigin(env: Env): { url: string; target: string } | null {
  const target = (env.ORIGIN_TARGET ?? 'azure').toLowerCase();
  if (target !== 'azure') {
    console.warn('[edge-proxy] unsupported ORIGIN_TARGET — only "azure" is wired post-V4 cutover', { target });
  }
  return env.AZURE_BACKEND_URL ? { url: env.AZURE_BACKEND_URL, target: 'azure' } : null;
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);

    // Task #386 — Durable Object routes are intercepted BEFORE the
    // origin proxy so chat-session reads / rate-limit checks never hit
    // the Azure backend.
    const doResponse = await dispatchDurableObject(request, env, url);
    if (doResponse) {
      return doResponse;
    }

    // Task #424 — admin KV-usage probe. Returns the per-binding
    // counters this worker maintains (CF_EDGE_CACHE today) so the
    // /admin/kv-health panel can render a CF_EDGE_CACHE row alongside
    // the workers/edge-proxy RATE_LIMIT / BOT_HTML_CACHE rows. Must
    // come BEFORE dispatchKvCache so the snapshot path is never
    // counted as a CF_EDGE_CACHE op.
    const kvUsageResponse = await dispatchKvUsage(request, env, url);
    if (kvUsageResponse) {
      return kvUsageResponse;
    }

    // Task #405 — KV write-through cache routes. Handled at the edge
    // so the in-process LRU on a sibling pod can hydrate without an
    // origin round-trip. Task #424 — `ctx` is passed through so
    // counter-bump alerts can `waitUntil` the POST to /admin/kv-alerts.
    const kvResponse = await dispatchKvCache(request, env, url, ctx);
    if (kvResponse) {
      return kvResponse;
    }

    // Edge rate-limit for chat ingress, gated by DO_CHAT_ON. Scope is
    // user-keyed when the Bearer token verifies against
    // EDGE_JWT_HS256_SECRET (the same HS256 secret the backend uses
    // for JWT_SECRET); otherwise it falls back to per-IP. Fails open
    // on DO errors. Covers every chat ingress shape the backend
    // exposes: /chat, /api/chat, /ai_chat, /api/ai_chat, /ai/chat,
    // /api/ai/chat.
    const chatPathRe = /^\/(?:api\/)?(?:ai\/|ai_)?chat(?:\b|\/)/i;
    if (
      String(env.DO_CHAT_ON ?? '').toLowerCase() === 'true' &&
      env.RATE_LIMITER &&
      chatPathRe.test(url.pathname)
    ) {
      try {
        const { scope, kind } = await deriveLimiterScope(request, env);
        const limit = Math.max(
          1,
          Number(
            kind === 'do-chat-user'
              ? (env.EDGE_CHAT_USER_RATE_LIMIT ?? env.EDGE_CHAT_RATE_LIMIT ?? '60')
              : (env.EDGE_CHAT_RATE_LIMIT ?? '30'),
          ),
        );
        const id = env.RATE_LIMITER.idFromName('global');
        const stub = env.RATE_LIMITER.get(id);
        const probe = await stub.fetch('https://internal/do/rate-limiter/check', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ key: `edge-chat:${scope}`, limit, window_s: 60 }),
        });
        if (probe.ok) {
          const verdict = await probe.json() as { allowed?: boolean; remaining?: number };
          if (verdict && verdict.allowed === false) {
            return new Response(
              JSON.stringify({ error: 'rate_limited', scope: 'edge', remaining: 0 }),
              {
                status: 429,
                headers: {
                  'Content-Type': 'application/json',
                  'Retry-After': '60',
                  'X-Edge-Rate-Scope': kind,
                },
              },
            );
          }
        }
      } catch (err) {
        console.warn('[edge-proxy] edge rate-limit probe failed', { err });
      }
    }

    const origin = resolveOrigin(env);
    if (!origin) {
      return new Response('origin not configured for ORIGIN_TARGET', { status: 503 });
    }

    const targetUrl = `${origin.url}${url.pathname}${url.search}`;

    const upstreamRequest = new Request(targetUrl, {
      method: request.method,
      headers: (() => {
        const h = new Headers(request.headers);
        h.set('x-dispatch-secret', env.DISPATCH_SHARED_SECRET ?? '');
        h.set('x-forwarded-host', url.hostname);
        h.set('x-origin-target', origin.target);
        // Propagate Cloudflare geo headers so the origin can apply
        // regional logic without re-doing IP geolocation.
        h.set('x-cf-ipcountry', request.headers.get('cf-ipcountry') ?? '');
        h.set('x-real-ip', request.headers.get('cf-connecting-ip') ?? '');
        return h;
      })(),
      body: ['GET', 'HEAD'].includes(request.method) ? null : request.body,
      redirect: 'follow',
    });

    try {
      const upstream = await fetch(upstreamRequest);

      const contentType = upstream.headers.get('content-type') ?? '';
      if (contentType.includes('text/html')) {
        ctx.waitUntil(Promise.resolve());
      }

      const response = new Response(upstream.body, upstream);

      if (contentType.includes('text/html')) {
        const r = new Response(response.body, response);
        r.headers.set('Link', EARLY_HINTS_ASSETS.join(', '));
        r.headers.set('x-syrabit-origin', origin.target);
        return r;
      }

      response.headers.set('x-syrabit-origin', origin.target);
      return response;
    } catch (err) {
      console.error('[edge-proxy] upstream fetch failed', { target: origin.target, err });
      return new Response('upstream unavailable', { status: 502 });
    }
  },
};
