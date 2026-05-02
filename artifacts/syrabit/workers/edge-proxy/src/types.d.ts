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
