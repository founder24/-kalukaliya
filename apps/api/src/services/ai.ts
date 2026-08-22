/**
 * Shared Workers AI text-generation service
 *
 * Uses Cloudflare's env.AI binding exclusively — no external HTTP calls.
 *
 * Models:
 *   Primary  : @cf/zai-org/glm-4.7-flash
 *   Fallback : @cf/qwen/qwen3-30b-a3b-fp8
 *
 * Exports:
 *   generate()        — non-streaming, returns { text, model }
 *   streamGenerate()  — async generator yielding string chunks, tagged with model
 */

// Workers AI model identifiers.
// Typed as string because @cloudflare/workers-types narrows the `model`
// parameter to a union that may not include newer model IDs before types update.
export const AI_MODEL_PRIMARY  = '@cf/zai-org/glm-4.7-flash';
export const AI_MODEL_FALLBACK = '@cf/qwen/qwen3-30b-a3b-fp8';

export interface GenerateOptions {
  systemPrompt: string;
  userMessage:  string;
  maxTokens?:   number;
}

export interface GenerateResult {
  text:  string;
  model: string;
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal: invoke AI binding (non-streaming)
// ─────────────────────────────────────────────────────────────────────────────

async function runModel(
  ai:          Ai,
  model:       string,
  opts:        GenerateOptions,
): Promise<string> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const result = await (ai as any).run(model, {
    messages: [
      { role: 'system', content: opts.systemPrompt },
      { role: 'user',   content: opts.userMessage  },
    ],
    ...(opts.maxTokens !== undefined && { max_tokens: opts.maxTokens }),
  });

  // Workers AI text-generation returns { response: string } or { result: { response: string } }
  // depending on the model family. Normalise both shapes.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const r = result as any;
  const text: string =
    typeof r?.response === 'string'        ? r.response :
    typeof r?.result?.response === 'string' ? r.result.response :
    '';

  return text;
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal: invoke AI binding (streaming)
// Returns a ReadableStream<Uint8Array> as provided by the Workers AI binding.
// ─────────────────────────────────────────────────────────────────────────────

async function runModelStream(
  ai:    Ai,
  model: string,
  opts:  GenerateOptions,
): Promise<ReadableStream<Uint8Array>> {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const result = await (ai as any).run(model, {
    messages: [
      { role: 'system', content: opts.systemPrompt },
      { role: 'user',   content: opts.userMessage  },
    ],
    stream: true,
    ...(opts.maxTokens !== undefined && { max_tokens: opts.maxTokens }),
  });

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const r = result as any;

  // The binding returns a ReadableStream directly for streaming calls.
  if (r instanceof ReadableStream) return r as ReadableStream<Uint8Array>;

  // Some runtime versions wrap it in a { readable } or { body } shape.
  if (r?.readable instanceof ReadableStream) return r.readable as ReadableStream<Uint8Array>;
  if (r?.body    instanceof ReadableStream) return r.body    as ReadableStream<Uint8Array>;

  throw new Error(`[ai] Unexpected streaming response shape from model ${model}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal: parse SSE lines emitted by the Workers AI streaming binding.
// The binding emits OpenAI-compatible SSE: `data: {...}\n\n` with a trailing
// `data: [DONE]` sentinel.
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Parse a single SSE `data:` line into a text delta.
 * Returns the extracted delta string, or null if the line should be skipped.
 *
 * Exported for unit testing.
 */
export function parseSseLine(line: string): string | null {
  if (!line.startsWith('data: ')) return null;
  const raw = line.slice(6).trim();
  if (raw === '[DONE]') return null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const json = JSON.parse(raw) as any;
    // OpenAI-compatible delta shape
    const delta: unknown = json?.choices?.[0]?.delta?.content;
    if (typeof delta === 'string' && delta) return delta;
    // Some Workers AI models use response at the top level (non-delta shape)
    const resp: unknown = json?.response;
    if (typeof resp === 'string' && resp) return resp;
  } catch { /* malformed — skip */ }
  return null;
}

/**
 * Drain a Workers AI streaming ReadableStream and yield text deltas.
 * Exported for unit testing.
 */
export async function* drainStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<string> {
  const reader  = stream.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      const lines = buf.split('\n');
      buf = lines.pop() ?? '';

      for (const line of lines) {
        const delta = parseSseLine(line);
        if (delta !== null) yield delta;
      }
    }

    // Flush any remaining bytes
    if (buf) {
      const delta = parseSseLine(buf.trim());
      if (delta !== null) yield delta;
    }
  } finally {
    reader.cancel().catch(() => {});
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Public API
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Non-streaming text generation with primary → fallback model retry.
 *
 * On primary model error, retries once with the fallback model.
 * Throws only if both models fail.
 */
export async function generate(
  ai:   Ai,
  opts: GenerateOptions,
): Promise<GenerateResult> {
  try {
    const text = await runModel(ai, AI_MODEL_PRIMARY, opts);
    if (text) return { text, model: AI_MODEL_PRIMARY };
    throw new Error('Primary model returned empty response');
  } catch (primaryErr) {
    console.warn('[ai] Primary model failed, trying fallback:', primaryErr);
  }

  const text = await runModel(ai, AI_MODEL_FALLBACK, opts);
  if (!text) throw new Error('[ai] Both primary and fallback models returned empty responses');
  return { text, model: AI_MODEL_FALLBACK };
}

/**
 * Streaming text generation with primary → fallback model retry.
 *
 * The fallback is only attempted if the primary fails BEFORE yielding any
 * tokens (to avoid mixing two responses on the client).
 *
 * Yields: string chunks
 * Sets: result.model on the yielded metadata (accessible via the return value)
 *
 * Because AsyncGenerators cannot easily return extra metadata after the last
 * yield, the resolved model name is available as `streamGenerate.model` on
 * the generator object — callers that need it should collect it after
 * iteration, or use the `generate()` non-streaming API for internal use.
 *
 * For the streaming path we tag the used model by embedding a special
 * `\x00model:<name>` sentinel as the very last yield so callers can extract it.
 * Callers that do not need the model name can filter out lines starting with \x00.
 */
export async function* streamGenerate(
  ai:   Ai,
  opts: GenerateOptions,
): AsyncGenerator<string> {
  let stream: ReadableStream<Uint8Array>;
  let usedModel = AI_MODEL_PRIMARY;

  // Attempt primary model
  try {
    stream = await runModelStream(ai, AI_MODEL_PRIMARY, opts);
  } catch (primaryErr) {
    console.warn('[ai] Primary stream model failed, trying fallback:', primaryErr);
    usedModel = AI_MODEL_FALLBACK;
    stream    = await runModelStream(ai, AI_MODEL_FALLBACK, opts);
  }

  let tokensEmitted = 0;

  try {
    for await (const chunk of drainStream(stream)) {
      tokensEmitted++;
      yield chunk;
    }
  } catch (streamErr) {
    if (tokensEmitted === 0 && usedModel === AI_MODEL_PRIMARY) {
      // Primary failed mid-drain before any token reached the caller — try fallback
      console.warn('[ai] Primary stream drain failed before tokens, trying fallback:', streamErr);
      usedModel = AI_MODEL_FALLBACK;
      const fallbackStream = await runModelStream(ai, AI_MODEL_FALLBACK, opts);
      for await (const chunk of drainStream(fallbackStream)) {
        tokensEmitted++;
        yield chunk;
      }
    } else {
      throw streamErr;
    }
  }

  // Sentinel — callers that need the model name extract this
  yield `\x00model:${usedModel}`;
}
