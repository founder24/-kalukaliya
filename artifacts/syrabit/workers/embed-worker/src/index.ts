/**
 * embed-worker/src/index.ts
 *
 * Custom Cloudflare Worker that produces fixed-dimension embeddings by
 * mean-pooling hidden state representations from two Workers-AI
 * models (Gemma-300M + Qwen3-0.6B per Task #382) and folding them into
 * a single EMBED_DIMS-wide vector that matches the existing Pinecone
 * serverless index (1024-dim, cosine).
 *
 * Endpoints
 * ─────────
 *   POST /embed       — body: { texts: string[], task_type?: string }
 *                        headers: X-Embed-Secret: <EMBED_SHARED_SECRET>
 *                        returns: { vectors: number[][], dims, model_version }
 *   GET  /health      — returns: { ok: true, dims, batch, models[], version }
 *   GET  /version     — returns: { version, dims, models[], built_at }
 *
 * Pooling formula
 * ───────────────
 *   1. For each configured model, run inference and capture the model's
 *      embedding/representation vector.
 *   2. Mean-pool token-level vectors per model into a single per-model
 *      vector (server-side mean pool — Workers AI text-embedding
 *      endpoints already return pooled vectors; for chat-only models we
 *      fall back to the response-vector embedding API).
 *   3. Element-wise sum the per-model vectors to form a fused
 *      representation.
 *   4. Resize the fused vector to EMBED_DIMS via deterministic
 *      truncation/zero-pad so the output width is constant regardless
 *      of which underlying models the worker is configured against.
 *
 * Auth + rate limiting
 * ────────────────────
 * Backend callers must send `X-Embed-Secret: <EMBED_SHARED_SECRET>`.
 * Requests without the matching secret return 401. Per-IP rate
 * limiting uses an in-memory sliding window (resets on isolate
 * recycle); a real boundary is the upstream Cloudflare WAF rule on
 * embed.syrabit.ai, this is just a cheap defence-in-depth cap.
 */

export interface Env {
  AI: {
    run: (model: string, input: any) => Promise<any>;
  };
  EMBED_SHARED_SECRET: string;
  EMBED_DIMS?: string;
  EMBED_MAX_BATCH?: string;
  EMBED_MAX_CHARS?: string;
  EMBED_MODELS?: string;
  EMBED_RATE_RPM?: string;
  EMBED_WORKER_VERSION?: string;
}

interface EmbedBody {
  texts?: unknown;
  task_type?: unknown;
}

interface EmbedResponse {
  vectors: number[][];
  dims: number;
  count: number;
  model_version: string;
  models: string[];
}

const DEFAULT_DIMS = 1024;
const DEFAULT_BATCH = 32;
const DEFAULT_CHARS = 4096;
// Task #382 — Workers-AI dedicated embedding endpoints. These are the
// embedding models from the requested model families (Gemma-300M and
// Qwen3-0.6B) — NOT the chat/instruct variants, which do not return
// embeddings via env.AI.run({ text: [...] }). They each return a pooled
// vector per text:
//   @cf/google/embeddinggemma-300m   →  768-dim (resized to 1024)
//   @cf/qwen/qwen3-embedding-0.6b    → 1024-dim
// We element-wise sum the two vectors after L2-normalising them and
// then resize/normalise once more so the final width is exactly
// EMBED_DIMS regardless of the underlying model widths.
const DEFAULT_MODELS =
  "@cf/google/embeddinggemma-300m,@cf/qwen/qwen3-embedding-0.6b";
const DEFAULT_RPM = 600;
const DEFAULT_VERSION = "1.1.0";

function envInt(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) && n > 0 ? n : fallback;
}

function configFromEnv(env: Env): {
  dims: number;
  batch: number;
  chars: number;
  models: string[];
  rpm: number;
  version: string;
} {
  return {
    dims: envInt(env.EMBED_DIMS, DEFAULT_DIMS),
    batch: envInt(env.EMBED_MAX_BATCH, DEFAULT_BATCH),
    chars: envInt(env.EMBED_MAX_CHARS, DEFAULT_CHARS),
    models: (env.EMBED_MODELS ?? DEFAULT_MODELS)
      .split(",")
      .map((m) => m.trim())
      .filter(Boolean),
    rpm: envInt(env.EMBED_RATE_RPM, DEFAULT_RPM),
    version: env.EMBED_WORKER_VERSION ?? DEFAULT_VERSION,
  };
}

// ── In-memory sliding window rate limiter ────────────────────────────────────
const RATE_WINDOW_MS = 60_000;
const ipBuckets = new Map<string, number[]>();

function rateLimit(ip: string, limit: number): boolean {
  const now = Date.now();
  const bucket = (ipBuckets.get(ip) ?? []).filter(
    (ts) => now - ts < RATE_WINDOW_MS,
  );
  if (bucket.length >= limit) {
    ipBuckets.set(ip, bucket);
    return false;
  }
  bucket.push(now);
  ipBuckets.set(ip, bucket);
  return true;
}

// ── Vector helpers ───────────────────────────────────────────────────────────
function meanPool(rows: number[][]): number[] {
  if (!rows.length) return [];
  const width = rows[0].length;
  const acc = new Array<number>(width).fill(0);
  for (const row of rows) {
    for (let i = 0; i < width && i < row.length; i++) {
      acc[i] += row[i];
    }
  }
  for (let i = 0; i < width; i++) acc[i] /= rows.length;
  return acc;
}

function elementwiseAdd(a: number[], b: number[]): number[] {
  const width = Math.max(a.length, b.length);
  const out = new Array<number>(width).fill(0);
  for (let i = 0; i < width; i++) {
    out[i] = (a[i] ?? 0) + (b[i] ?? 0);
  }
  return out;
}

function resizeToDims(vec: number[], dims: number): number[] {
  if (vec.length === dims) return vec;
  if (vec.length > dims) return vec.slice(0, dims);
  const out = vec.slice();
  while (out.length < dims) out.push(0);
  return out;
}

function l2Normalise(vec: number[]): number[] {
  let sum = 0;
  for (const v of vec) sum += v * v;
  const norm = Math.sqrt(sum);
  if (!Number.isFinite(norm) || norm === 0) return vec;
  return vec.map((v) => v / norm);
}

// ── Workers-AI inference adapter ─────────────────────────────────────────────
//
// All configured models are dedicated text-embedding endpoints. The
// canonical Workers-AI response shapes for embedding endpoints are:
//   1. { shape: [N, D], data: [[...], [...]] }   — bge / embeddinggemma
//   2. { data: [{ embedding: [...] }] }          — OpenAI-compatible shim
//   3. { embedding: [...] }                       — single-vector shorthand
// We accept all three. Hidden-state / logits paths from the previous
// version were chat-model fallbacks and are no longer reached because
// DEFAULT_MODELS now points exclusively at embedding endpoints; we
// keep them as a defensive last resort so a future operator who points
// EMBED_MODELS at a chat model still gets *some* representation back
// instead of an empty vector.
async function runModelEmbedding(
  ai: Env["AI"],
  model: string,
  text: string,
): Promise<number[]> {
  const result = await ai.run(model, { text: [text] });

  // Shape 1: { shape: [N, D], data: [[...]] } — bge-m3, embeddinggemma,
  // qwen3-embedding. Each row is a per-text vector; we batch one text
  // per call so we always read row[0].
  if (Array.isArray(result?.data) && Array.isArray(result.data[0])) {
    return (result.data as number[][])[0];
  }
  // Shape 2: { data: [{ embedding: [...] }] } — OpenAI-compatible.
  if (Array.isArray(result?.data) && result.data[0]?.embedding) {
    return result.data[0].embedding as number[];
  }
  // Shape 3: { embedding: [...] } — single-embedding shorthand.
  if (Array.isArray(result?.embedding)) {
    return result.embedding as number[];
  }
  // Defensive fallbacks for chat-model misconfiguration. Not used by
  // the default model set.
  if (Array.isArray(result?.hidden_states)) {
    return meanPool(result.hidden_states as number[][]);
  }
  if (Array.isArray(result?.logits)) {
    return result.logits as number[];
  }
  return [];
}

async function fuseEmbedding(
  ai: Env["AI"],
  models: string[],
  text: string,
  dims: number,
): Promise<number[]> {
  let fused: number[] = [];
  for (const model of models) {
    let perModel: number[] = [];
    try {
      perModel = await runModelEmbedding(ai, model, text);
    } catch (err) {
      console.warn(
        `[embed-worker] model ${model} failed: ${(err as Error).message ?? err}`,
      );
      continue;
    }
    if (!perModel.length) continue;
    // L2-normalise per-model first so a model with a larger native
    // norm doesn't dominate the fused vector. Then resize each to the
    // common output width before element-wise summation so two models
    // with different native dims (e.g. 768 + 1024) can still be fused
    // without one being silently truncated by the other's width.
    const sized = resizeToDims(l2Normalise(perModel), dims);
    fused = fused.length ? elementwiseAdd(fused, sized) : sized;
  }
  if (!fused.length) {
    throw new Error("all configured models returned empty embeddings");
  }
  return l2Normalise(resizeToDims(fused, dims));
}

// ── Request handlers ─────────────────────────────────────────────────────────
function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function clientIp(request: Request): string {
  return (
    request.headers.get("cf-connecting-ip") ??
    request.headers.get("x-real-ip") ??
    "unknown"
  );
}

async function handleEmbed(request: Request, env: Env): Promise<Response> {
  const cfg = configFromEnv(env);

  if (!env.EMBED_SHARED_SECRET) {
    return jsonResponse(
      { error: "EMBED_SHARED_SECRET not configured" },
      503,
    );
  }
  if (request.headers.get("x-embed-secret") !== env.EMBED_SHARED_SECRET) {
    return jsonResponse({ error: "unauthorized" }, 401);
  }

  if (!rateLimit(clientIp(request), cfg.rpm)) {
    return jsonResponse({ error: "rate_limited" }, 429);
  }

  let body: EmbedBody;
  try {
    body = (await request.json()) as EmbedBody;
  } catch {
    return jsonResponse({ error: "invalid_json" }, 400);
  }

  if (!Array.isArray(body.texts)) {
    return jsonResponse({ error: "texts must be an array of strings" }, 400);
  }
  if (body.texts.length === 0) {
    return jsonResponse({ error: "texts must be non-empty" }, 400);
  }
  if (body.texts.length > cfg.batch) {
    return jsonResponse(
      { error: `batch too large (max ${cfg.batch})` },
      413,
    );
  }
  for (const t of body.texts) {
    if (typeof t !== "string") {
      return jsonResponse({ error: "every text must be a string" }, 400);
    }
  }

  const trimmed = (body.texts as string[]).map((t) => t.slice(0, cfg.chars));
  const vectors: number[][] = [];
  for (const text of trimmed) {
    const vec = await fuseEmbedding(env.AI, cfg.models, text, cfg.dims);
    vectors.push(vec);
  }

  const payload: EmbedResponse = {
    vectors,
    dims: cfg.dims,
    count: vectors.length,
    model_version: cfg.version,
    models: cfg.models,
  };
  return jsonResponse(payload);
}

function handleHealth(env: Env): Response {
  const cfg = configFromEnv(env);
  return jsonResponse({
    ok: true,
    dims: cfg.dims,
    batch: cfg.batch,
    models: cfg.models,
    version: cfg.version,
  });
}

function handleVersion(env: Env): Response {
  const cfg = configFromEnv(env);
  return jsonResponse({
    version: cfg.version,
    dims: cfg.dims,
    models: cfg.models,
    built_at: new Date().toISOString(),
  });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/embed") {
      return handleEmbed(request, env);
    }
    if (request.method === "GET" && url.pathname === "/health") {
      return handleHealth(env);
    }
    if (request.method === "GET" && url.pathname === "/version") {
      return handleVersion(env);
    }
    return jsonResponse({ error: "not_found", path: url.pathname }, 404);
  },
};
