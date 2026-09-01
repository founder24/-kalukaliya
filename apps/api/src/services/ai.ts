/**
 * Shared Workers AI text-generation service
 *
 * Uses Cloudflare's env.AI binding exclusively — no external HTTP calls.
 *
 * Models:
 *   Primary  : @cf/meta/llama-3.1-8b-instruct-fast
 *   Fallback : @cf/qwen/qwen3-30b-a3b-fp8
 *
 * Exports:
 *   generate()        — non-streaming, returns { text, model }
 *   streamGenerate()  — async generator yielding string chunks, tagged with model
 */

// Workers AI model identifiers.
// Typed as string because @cloudflare/workers-types narrows the `model`
// parameter to a union that may not include newer model IDs before types update.
export const AI_MODEL_PRIMARY  = '@cf/meta/llama-3.1-8b-instruct-fast';
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

  // The binding returns a ReadableStream directly for streaming calls. Depending
  // on the Workers AI model/runtime version, it can also arrive wrapped in a
  // Response-like object or nested under response/result.
  const stream = findReadableStream(r);
  if (stream) return stream;

  // A few Workers AI model families return a complete response object even when
  // `stream: true` is requested. Adapt that shape to a one-event stream instead
  // of treating a valid answer as a provider outage.
  const text = extractResponseText(r);
  if (text) return responseTextStream(text);

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
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith(':') || trimmed.startsWith('event:')) return null;

  // Streaming bindings normally emit OpenAI-compatible `data:` lines, but some
  // model/runtime combinations supply one JSON object per chunk without the
  // prefix. Support both while rejecting unrelated SSE fields.
  const raw = trimmed.startsWith('data:')
    ? trimmed.slice(5).trimStart()
    : trimmed;
  if (raw === '[DONE]') return null;
  try {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const json = JSON.parse(raw) as any;
    return extractResponseText(json);
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
  let usedModel = AI_MODEL_PRIMARY;
  let tokensEmitted = 0;

  // Do not mix responses: the fallback is available only when the primary
  // fails before yielding any visible content. An empty primary stream counts
  // as a failure, which prevents callers from receiving a successful-looking
  // completion with an empty answer.
  try {
    for await (const chunk of streamModel(ai, AI_MODEL_PRIMARY, opts)) {
      tokensEmitted++;
      yield chunk;
    }
  } catch (primaryErr) {
    if (tokensEmitted > 0) throw primaryErr;
    console.warn('[ai] Primary stream model failed, trying fallback:', primaryErr);
    usedModel = AI_MODEL_FALLBACK;
    for await (const chunk of streamModel(ai, AI_MODEL_FALLBACK, opts)) {
      tokensEmitted++;
      yield chunk;
    }
  }

  if (tokensEmitted === 0) throw new Error('[ai] Both stream models returned an empty response');

  // Sentinel — callers that need the model name extract this
  yield `\x00model:${usedModel}`;
}

async function* streamModel(
  ai: Ai,
  model: string,
  opts: GenerateOptions,
): AsyncGenerator<string> {
  const stream = await runModelStream(ai, model, opts);
  let emitted = 0;
  for await (const chunk of drainStream(stream)) {
    emitted++;
    yield chunk;
  }
  if (emitted === 0) {
    throw new Error(`[ai] ${model} returned an empty streaming response`);
  }
}

function findReadableStream(value: unknown): ReadableStream<Uint8Array> | null {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const r = value as any;
  const candidates = [
    r,
    r?.readable,
    r?.body,
    r?.response,
    r?.response?.body,
    r?.result,
    r?.result?.body,
  ];
  for (const candidate of candidates) {
    if (candidate instanceof ReadableStream) {
      return candidate as ReadableStream<Uint8Array>;
    }
  }
  return null;
}

function extractResponseText(value: unknown): string | null {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const r = value as any;
  const candidates: unknown[] = [
    r?.choices?.[0]?.delta?.content,
    r?.choices?.[0]?.message?.content,
    r?.response,
    r?.result?.response,
    r?.message?.content,
    r?.content,
  ];
  for (const candidate of candidates) {
    const text = contentToText(candidate);
    if (text) return text;
  }
  return null;
}

function contentToText(value: unknown): string | null {
  if (typeof value === 'string') return value || null;
  if (!Array.isArray(value)) return null;
  const text = value
    .map((item) => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object' && 'text' in item && typeof item.text === 'string') {
        return item.text;
      }
      return '';
    })
    .join('');
  return text || null;
}

function responseTextStream(text: string): ReadableStream<Uint8Array> {
  const encoded = new TextEncoder().encode(JSON.stringify({ response: text }));
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(encoded);
      controller.close();
    },
  });
}
