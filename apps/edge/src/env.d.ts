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
  head(key: string): Promise<R2Object | null>;
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
  EDGE_SHARED_SECRET: string;

  // ── JWT Configuration ──
  // Optional: algorithm auto-detected from token header ('HS256' | 'RS256')
  JWT_ALGORITHM?: string;
  // Optional: PEM-encoded RSA public key for RS256 verification
  JWT_PUBLIC_KEY?: string;

  // ── Variables (defined in wrangler.toml [vars]) ──
  BACKEND_URL: string;
  ALLOWED_ORIGIN: string;

  // Optional: override default 30s proxy timeout (milliseconds)
  PROXY_TIMEOUT_MS?: string;
  // Optional: bound service-to-service response-header timeout (milliseconds).
  SERVICE_BINDING_TIMEOUT_MS?: string;

  // Optional: Google Service Account JSON key for Cloud Run authentication
  GOOGLE_SA_KEY?: string;

  // ── Bindings ──
  R2_BUCKET: R2Bucket;
  RATE_LIMIT_KV: KVNamespace;
  ISR_CACHE_KV: KVNamespace;
  // Pre-seeded content HTML store (written by backend content pipeline).
  // Keys: {board}/{class_level}/{subject}/{chapter}/{page_type}
  CONTENT_KV: KVNamespace;
  // Workers AI binding — used for TTS and OCR directly at the edge.
  AI?: Ai;
  // Service Binding: syrabit-api-prod (production only).
  // Declared in wrangler.toml [[env.production.services]] but only ACTIVATED
  // when API_WORKER_LIVE === 'true'. This two-step approach lets the binding be
  // wired up before the API Worker has full route parity — routing stays on
  // BACKEND_URL (Cloud Run) until the operator explicitly flips the flag:
  //   wrangler secret put API_WORKER_LIVE --env production   # enter: true
  API_WORKER?: { fetch(request: Request): Promise<Response> };
  // Guards activation of the API_WORKER service binding. Must be explicitly set
  // to the string "true" to route traffic through the D1-backed API Worker.
  // Any other value (including absent) keeps routing on BACKEND_URL.
  API_WORKER_LIVE?: string;
}

// Minimal Cloudflare Workers AI binding type.
// Full types are provided by @cloudflare/workers-types when installed.
declare interface Ai {
  run(
    model: string,
    inputs: Record<string, unknown>
  ): Promise<Response | Record<string, unknown>>;
}
