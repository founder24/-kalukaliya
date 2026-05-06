/**
 * Cloudflare Workers runtime type stubs.
 *
 * These declarations cover the runtime globals used by the edge-proxy shim.
 * Install @cloudflare/workers-types (devDependency) for full type coverage
 * once wrangler publish is wired into CI.
 *
 * Ref: https://developers.cloudflare.com/workers/runtime-apis/handlers/fetch/
 */

interface ExecutionContext {
  /**
   * Keeps the worker alive until `promise` resolves, even after the response
   * has been sent.  Used for fire-and-forget tasks (e.g. logging, cache warm).
   */
  waitUntil(promise: Promise<unknown>): void;

  /**
   * When called, a network error triggers the request to pass through to the
   * origin server instead of returning an error response.
   */
  passThroughOnException(): void;
}

/**
 * Minimal Cloudflare Workers KV stub — covers the surface used by the
 * /api/edge/kv-cache/* routes (Task #405). Replace with the full
 * @cloudflare/workers-types definition once that devDependency is added.
 *
 * Ref: https://developers.cloudflare.com/kv/api/
 */
interface KVNamespacePutOptions {
  expirationTtl?: number;
  expiration?: number;
  metadata?: Record<string, unknown>;
}

interface KVNamespaceGetWithMetadataResult<V, M> {
  value: V | null;
  metadata: M | null;
}

interface KVNamespaceListOptions {
  prefix?: string;
  limit?: number;
  cursor?: string;
}

interface KVNamespaceListResult {
  keys: { name: string; expiration?: number; metadata?: unknown }[];
  list_complete: boolean;
  cursor?: string;
}

interface KVNamespace {
  get(key: string): Promise<string | null>;
  get(key: string, options: { type: 'json' }): Promise<unknown | null>;
  get(key: string, options: { type: 'text' }): Promise<string | null>;
  getWithMetadata<V = unknown, M = unknown>(
    key: string,
    options: { type: 'json' },
  ): Promise<KVNamespaceGetWithMetadataResult<V, M>>;
  put(
    key: string,
    value: string | ArrayBuffer | ReadableStream,
    options?: KVNamespacePutOptions,
  ): Promise<void>;
  delete(key: string): Promise<void>;
  // Task #454 — `list` is needed by `_aggregateKvCountersAcrossIsolates`
  // to enumerate every isolate's `__kv_usage:*` shared key.
  list(options?: KVNamespaceListOptions): Promise<KVNamespaceListResult>;
}
