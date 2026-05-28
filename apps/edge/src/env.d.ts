/**
 * Cloudflare Worker Environment Bindings
 * Typed interface for all secrets, vars, and bindings used by the edge worker.
 *
 * NOTE: KVNamespace, R2Bucket, ExecutionContext are provided by @cloudflare/workers-types.
 * These ambient declarations are fallbacks for environments where the types package
 * is unavailable (e.g., CI without npm install). Remove if workers-types is installed.
 */

// Ambient Cloudflare types (provided by @cloudflare/workers-types at runtime)
declare interface KVNamespace {
  get(key: string, options?: { type?: string }): Promise<string | null>;
  put(key: string, value: string, options?: { expirationTtl?: number }): Promise<void>;
  delete(key: string): Promise<void>;
}

declare interface R2Bucket {
  get(key: string): Promise<R2Object | null>;
}

declare interface R2Object {
  body: ReadableStream;
  writeHttpMetadata(headers: Headers): void;
}

declare interface ExecutionContext {
  waitUntil(promise: Promise<unknown>): void;
  passThroughOnException(): void;
}

interface Env {
  // ── Secrets (set via `npx wrangler secret put <NAME>`) ──
  JWT_SECRET: string;
  CF_TURNSTILE_SECRET: string;
  EDGE_SHARED_SECRET: string;

  // ── JWT Configuration ──
  // Optional: algorithm auto-detected from token header ('HS256' | 'RS256')
  JWT_ALGORITHM?: string;
  // Optional: PEM-encoded RSA public key for RS256 verification
  JWT_PUBLIC_KEY?: string;

  // ── Variables (defined in wrangler.toml [vars]) ──
  AZURE_BACKEND_URL: string;
  ALLOWED_ORIGIN: string;

  // Optional: override default 30s proxy timeout (milliseconds)
  PROXY_TIMEOUT_MS?: string;

  // ── Bindings ──
  R2_BUCKET: R2Bucket;
  RATE_LIMIT_KV: KVNamespace;
  ISR_CACHE_KV: KVNamespace;
}
