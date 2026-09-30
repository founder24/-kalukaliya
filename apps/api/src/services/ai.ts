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
export const AI_MODEL_ASSAMESE = '@cf/aisingapore/gemma-sea-lion-v4-27b-it';
/** Hard cap on local wall-clock time for one English attempt, including a stalled provider response. */
export const AI_STREAM_TIMEOUT_MS = 3_000;

export interface GenerateOptions {
  systemPrompt: string;
  userMessage:  string;
  maxTokens?:   number;
  signal?:      AbortSignal;
}

export interface GenerateResult {
  text:  string;
  model: string;
}

function abortReason(signal: AbortSignal): unknown {
  return signal.reason ?? new DOMException('The operation was aborted', 'AbortError');
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw abortReason(signal);
}

function isTimeoutError(error: unknown): boolean {
  return error instanceof Error && error.name === 'TimeoutError';
}

async function runWithTimeout<T>(
  parentSignal: AbortSignal | undefined,
  timeoutMs: number,
  timeoutMessage: string,
  run: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  let rejectAbort!: (reason: unknown) => void;
  const abortPromise = new Promise<never>((_resolve, reject) => {
    rejectAbort = reject;
  });
  const onAbort = () => rejectAbort(abortReason(controller.signal));
  controller.signal.addEventListener('abort', onAbort, { once: true });

  const abortFromParent = () => {
    controller.abort(
      parentSignal?.reason ?? new DOMException('The operation was aborted', 'AbortError'),
    );
  };
  if (parentSignal?.aborted) {
    abortFromParent();
  } else {
    parentSignal?.addEventListener('abort', abortFromParent, { once: true });
  }

  const timeoutError = new Error(timeoutMessage);
  timeoutError.name = 'TimeoutError';
  let timer: ReturnType<typeof setTimeout> | undefined;

  try {
    if (!controller.signal.aborted) {
      timer = setTimeout(
        () => controller.abort(timeoutError),
        Math.max(500, timeoutMs),
      );
    }
    const operation = controller.signal.aborted
      ? Promise.reject(abortReason(controller.signal))
      : run(controller.signal);
    const result = await Promise.race([operation, abortPromise]);
    throwIfAborted(controller.signal);
    return result;
  } finally {
    if (timer !== undefined) clearTimeout(timer);
    parentSignal?.removeEventListener('abort', abortFromParent);
    controller.signal.removeEventListener('abort', onAbort);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Internal: invoke AI binding (non-streaming)
// ─────────────────────────────────────────────────────────────────────────────

async function runModel(
  ai:          Ai,
  model:       string,
  opts:        GenerateOptions,
): Promise<string> {
  throwIfAborted(opts.signal);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const result = await (ai as any).run(model, {
    messages: [
      { role: 'system', content: opts.systemPrompt },
      { role: 'user',   content: opts.userMessage  },
    ],
    ...(opts.maxTokens !== undefined && { max_tokens: opts.maxTokens }),
  }, opts.signal ? { signal: opts.signal } : undefined);

  // Workers AI text-generation returns { response: string } or { result: { response: string } }
  // depending on the model family. Normalise both shapes.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const r = result as any;
  return extractResponseText(r) ?? '';
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
  throwIfAborted(opts.signal);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const result = await (ai as any).run(model, {
    messages: [
      { role: 'system', content: opts.systemPrompt },
      { role: 'user',   content: opts.userMessage  },
    ],
    stream: true,
    ...(opts.maxTokens !== undefined && { max_tokens: opts.maxTokens }),
  }, opts.signal ? { signal: opts.signal } : undefined);

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

function isSseDoneLine(line: string): boolean {
  const trimmed = line.trim();
  return trimmed === '[DONE]'
    || (trimmed.startsWith('data:') && trimmed.slice(5).trim() === '[DONE]');
}

/**
 * Drain a Workers AI streaming ReadableStream and yield text deltas.
 * Exported for unit testing.
 */
export async function* drainStream(
  stream: ReadableStream<Uint8Array>,
  signal?: AbortSignal,
  deadlineAt?: number,
  onDeadline?: (error: Error) => void,
): AsyncGenerator<string> {
  const reader  = stream.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let cancelPromise: Promise<void> | undefined;
  const cancelReader = (reason?: unknown): Promise<void> => {
    cancelPromise ??= reader.cancel(reason).catch(() => {});
    return cancelPromise;
  };
  let rejectAbort!: (reason: unknown) => void;
  const abortPromise = signal === undefined
    ? undefined
    : new Promise<never>((_resolve, reject) => {
        rejectAbort = reject;
      });
  const cancelOnAbort = () => {
    if (!signal) return;
    const reason = abortReason(signal);
    void cancelReader(reason);
    rejectAbort(reason);
  };
  signal?.addEventListener('abort', cancelOnAbort, { once: true });
  if (signal?.aborted) cancelOnAbort();
  void abortPromise?.catch(() => {});
  const remaining = deadlineAt === undefined ? undefined : Math.max(1, deadlineAt - Date.now());
  let deadlineTimer: ReturnType<typeof setTimeout> | undefined;
  let timedOut = false;
  let timeoutError: Error | undefined;
  const deadlinePromise = remaining === undefined
    ? undefined
    : new Promise<never>((_, reject) => {
        deadlineTimer = setTimeout(() => {
          const error = new Error('AI streaming attempt timed out');
          error.name = 'TimeoutError';
          timedOut = true;
          timeoutError = error;
          onDeadline?.(error);
          void cancelReader(error);
          reject(error);
        }, remaining);
      });

  try {
    throwIfAborted(signal);
    while (true) {
      const read = reader.read();
      const readResult = Promise.race([
        read,
        ...(deadlinePromise === undefined ? [] : [deadlinePromise]),
        ...(abortPromise === undefined ? [] : [abortPromise]),
      ]);
      const { done, value } = await readResult;
      if (timedOut) throw timeoutError;
      throwIfAborted(signal);
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      const lines = buf.split('\n');
      buf = lines.pop() ?? '';

      for (const line of lines) {
        if (isSseDoneLine(line)) {
          void cancelReader();
          return;
        }
        const delta = parseSseLine(line);
        if (delta !== null) yield delta;
      }
      if (isSseDoneLine(buf)) {
        void cancelReader();
        return;
      }
    }

    // Flush any remaining decoder bytes and final unterminated line.
    buf += decoder.decode();
    if (buf) {
      if (isSseDoneLine(buf)) {
        void cancelReader();
        return;
      }
      const delta = parseSseLine(buf.trim());
      if (delta !== null) yield delta;
    }
  } finally {
    if (deadlineTimer !== undefined) clearTimeout(deadlineTimer);
    signal?.removeEventListener('abort', cancelOnAbort);
    void cancelReader(signal?.aborted ? abortReason(signal) : undefined);
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
    throwIfAborted(opts.signal);
    console.warn('[ai] Primary model failed, trying fallback:', primaryErr);
  }

  throwIfAborted(opts.signal);
  const text = await runModel(ai, AI_MODEL_FALLBACK, opts);
  if (!text) throw new Error('[ai] Both primary and fallback models returned empty responses');
  return { text, model: AI_MODEL_FALLBACK };
}

/** Generate directly with the stronger fallback model for a quality repair. */
export async function generateFallback(
  ai: Ai,
  opts: GenerateOptions,
  timeoutMs = 6_000,
): Promise<GenerateResult> {
  const text = await runWithTimeout(
    opts.signal,
    timeoutMs,
    '[ai] Fallback model quality repair timed out',
    (signal) => runModel(ai, AI_MODEL_FALLBACK, { ...opts, signal }),
  );
  if (!text) throw new Error('[ai] Fallback model returned an empty response');
  return { text, model: AI_MODEL_FALLBACK };
}

/** Assamese-specialized quality repair using the strongest tested model. */
export async function generateAssamese(
  ai: Ai,
  opts: GenerateOptions,
  timeoutMs = 6_000,
): Promise<GenerateResult> {
  const text = await runWithTimeout(
    opts.signal,
    timeoutMs,
    '[ai] Assamese model quality repair timed out',
    (signal) => runModel(ai, AI_MODEL_ASSAMESE, { ...opts, signal }),
  );
  if (!text) throw new Error('[ai] Assamese model returned an empty response');
  return { text, model: AI_MODEL_ASSAMESE };
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
  opts: GenerateOptions & {
    primaryModel?: string;
    fallbackModel?: string;
    streamTimeoutMs?: number;
  },
): AsyncGenerator<string> {
  const primaryModel = opts.primaryModel ?? AI_MODEL_PRIMARY;
  const fallbackModel = opts.fallbackModel ?? AI_MODEL_FALLBACK;
  const streamTimeoutMs = opts.streamTimeoutMs ?? AI_STREAM_TIMEOUT_MS;
  let usedModel = primaryModel;
  let tokensEmitted = 0;

  // Do not mix responses: the fallback is available only when the primary
  // fails before yielding any visible content. An empty primary stream counts
  // as a failure, which prevents callers from receiving a successful-looking
  // completion with an empty answer.
  try {
    for await (const chunk of streamModel(ai, primaryModel, opts, streamTimeoutMs)) {
      tokensEmitted++;
      yield chunk;
    }
  } catch (primaryErr) {
    throwIfAborted(opts.signal);
    if (tokensEmitted > 0) throw primaryErr;
    console.warn('[ai] Primary stream model failed, trying fallback:', primaryErr);
    usedModel = fallbackModel;
    try {
      for await (const chunk of streamModel(ai, fallbackModel, opts, streamTimeoutMs)) {
        tokensEmitted++;
        yield chunk;
      }
    } catch (fallbackErr) {
      throwIfAborted(opts.signal);
      // Do not start an unbounded buffered recovery after a bounded stream
      // attempt timed out. A transport/model failure still retains the
      // historical buffered recovery path.
      if (isTimeoutError(fallbackErr)) throw fallbackErr;
      if (tokensEmitted > 0) throw fallbackErr;
      console.warn('[ai] Both stream models failed, trying buffered generation:', fallbackErr);
      const buffered = await generate(ai, opts);
      usedModel = buffered.model;
      tokensEmitted++;
      yield buffered.text;
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
  timeoutMs: number,
): AsyncGenerator<string> {
  const deadlineAt = Date.now() + Math.max(1, timeoutMs);
  const controller = new AbortController();
  let rejectAbort!: (reason: unknown) => void;
  const abortPromise = new Promise<never>((_resolve, reject) => {
    rejectAbort = reject;
  });
  const onAbort = () => rejectAbort(abortReason(controller.signal));
  controller.signal.addEventListener('abort', onAbort, { once: true });
  if (controller.signal.aborted) onAbort();
  void abortPromise.catch(() => {});

  const abortFromParent = () => {
    controller.abort(
      opts.signal?.reason ?? new DOMException('The operation was aborted', 'AbortError'),
    );
  };
  if (opts.signal?.aborted) {
    abortFromParent();
  } else {
    opts.signal?.addEventListener('abort', abortFromParent, { once: true });
  }

  const timeoutError = new Error(`[ai] ${model} streaming attempt timed out`);
  timeoutError.name = 'TimeoutError';
  const timeoutTimer = controller.signal.aborted
    ? undefined
    : setTimeout(
        () => controller.abort(timeoutError),
        Math.max(1, deadlineAt - Date.now()),
      );
  let emitted = 0;
  let completed = false;
  try {
    throwIfAborted(controller.signal);
    const stream = await Promise.race([
      runModelStream(ai, model, { ...opts, signal: controller.signal }),
      abortPromise,
    ]);
    for await (const chunk of drainStream(
      stream,
      controller.signal,
      deadlineAt,
      (error) => {
        if (!controller.signal.aborted) controller.abort(error);
      },
    )) {
      emitted++;
      yield chunk;
    }
    if (emitted === 0) {
      throw new Error(`[ai] ${model} returned an empty streaming response`);
    }
    completed = true;
  } catch (error) {
    if (controller.signal.aborted) throw abortReason(controller.signal);
    throw error;
  } finally {
    if (timeoutTimer !== undefined) clearTimeout(timeoutTimer);
    opts.signal?.removeEventListener('abort', abortFromParent);
    controller.signal.removeEventListener('abort', onAbort);
    if (!completed && !controller.signal.aborted) {
      controller.abort(new DOMException('Streaming attempt ended before completion', 'AbortError'));
    }
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
