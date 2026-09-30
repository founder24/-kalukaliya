/**
 * Focused unit tests for apps/api/src/services/ai.ts
 *
 * Tests cover:
 *   1. parseSseLine  — SSE line parser
 *   2. drainStream   — ReadableStream adapter that emits deltas
 *   3. streamGenerate sentinel extraction — model name propagation
 *
 * The Workers AI binding (env.AI) is not available in Node test workers, so
 * runModel / runModelStream are tested indirectly via drainStream + parseSseLine.
 * generate() and streamGenerate() integration with the real binding is covered
 * by the wrangler dev smoke tests.
 */

import { describe, it, expect } from 'vitest';
import {
  parseSseLine,
  drainStream,
  generate,
  generateAssamese,
  streamGenerate,
  AI_MODEL_PRIMARY,
  AI_MODEL_FALLBACK,
  AI_MODEL_ASSAMESE,
  AI_STREAM_TIMEOUT_MS,
} from './ai';

// ─────────────────────────────────────────────────────────────────────────────
// parseSseLine
// ─────────────────────────────────────────────────────────────────────────────

describe('parseSseLine', () => {
  it('returns null for lines without data: prefix', () => {
    expect(parseSseLine('')).toBeNull();
    expect(parseSseLine('event: ping')).toBeNull();
    expect(parseSseLine(':comment')).toBeNull();
  });

  it('returns null for the [DONE] sentinel', () => {
    expect(parseSseLine('data: [DONE]')).toBeNull();
  });

  it('returns null for malformed JSON', () => {
    expect(parseSseLine('data: {broken json')).toBeNull();
  });

  it('extracts delta.content from OpenAI-compatible choice delta shape', () => {
    const line = 'data: ' + JSON.stringify({
      choices: [{ delta: { content: 'Hello' } }],
    });
    expect(parseSseLine(line)).toBe('Hello');
  });

  it('extracts top-level response field (Workers AI non-delta shape)', () => {
    const line = 'data: ' + JSON.stringify({ response: 'World' });
    expect(parseSseLine(line)).toBe('World');
  });

  it('returns null when delta content is empty string', () => {
    const line = 'data: ' + JSON.stringify({
      choices: [{ delta: { content: '' } }],
    });
    expect(parseSseLine(line)).toBeNull();
  });

  it('returns null when response field is empty string', () => {
    const line = 'data: ' + JSON.stringify({ response: '' });
    expect(parseSseLine(line)).toBeNull();
  });

  it('returns null when choices array is empty', () => {
    const line = 'data: ' + JSON.stringify({ choices: [] });
    expect(parseSseLine(line)).toBeNull();
  });

  it('returns null when delta.content is a non-string (number)', () => {
    const line = 'data: ' + JSON.stringify({
      choices: [{ delta: { content: 42 } }],
    });
    expect(parseSseLine(line)).toBeNull();
  });

  it('handles whitespace around the data value', () => {
    const line = 'data:  ' + JSON.stringify({ response: 'Trimmed' });
    expect(parseSseLine(line)).toBe('Trimmed');
  });

  it('accepts a JSON streaming chunk without an SSE data prefix', () => {
    expect(parseSseLine(JSON.stringify({ response: 'Direct JSON chunk' }))).toBe('Direct JSON chunk');
  });

  it('extracts message content and content-part arrays', () => {
    expect(parseSseLine('data: ' + JSON.stringify({
      choices: [{ message: { content: [{ text: 'Array ' }, { text: 'content' }] } }],
    }))).toBe('Array content');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Helpers: build a ReadableStream from an array of Uint8Array chunks
// ─────────────────────────────────────────────────────────────────────────────

function makeStream(chunks: Uint8Array[]): ReadableStream<Uint8Array> {
  let i = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (i >= chunks.length) {
        controller.close();
        return;
      }
      controller.enqueue(chunks[i++]!);
    },
  });
}

function encode(s: string): Uint8Array {
  return new TextEncoder().encode(s);
}

/** Collect all yielded values from an async generator. */
async function collect(gen: AsyncGenerator<string>): Promise<string[]> {
  const results: string[] = [];
  for await (const v of gen) {
    results.push(v);
  }
  return results;
}

// ─────────────────────────────────────────────────────────────────────────────
// drainStream
// ─────────────────────────────────────────────────────────────────────────────

describe('drainStream', () => {
  it('yields deltas from a well-formed SSE stream (delta shape)', async () => {
    const lines = [
      'data: ' + JSON.stringify({ choices: [{ delta: { content: 'Hello' } }] }),
      'data: ' + JSON.stringify({ choices: [{ delta: { content: ' world' } }] }),
      'data: [DONE]',
    ].join('\n') + '\n';

    const stream = makeStream([encode(lines)]);
    const chunks = await collect(drainStream(stream));
    expect(chunks).toEqual(['Hello', ' world']);
  });

  it('handles streams split across multiple Uint8Array chunks', async () => {
    const full = [
      'data: ' + JSON.stringify({ choices: [{ delta: { content: 'A' } }] }) + '\n',
      'data: ' + JSON.stringify({ choices: [{ delta: { content: 'B' } }] }) + '\n',
      'data: [DONE]\n',
    ];
    // Split each line into two halves to simulate partial reads
    const halves: Uint8Array[] = full.flatMap(line => {
      const mid = Math.floor(line.length / 2);
      return [encode(line.slice(0, mid)), encode(line.slice(mid))];
    });

    const stream = makeStream(halves);
    const chunks = await collect(drainStream(stream));
    expect(chunks).toEqual(['A', 'B']);
  });

  it('completes on Workers AI [DONE] without waiting for the body to close', async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encode('data: {"response":"Complete answer"}\n'));
        controller.enqueue(encode('data: [DO'));
        controller.enqueue(encode('NE]'));
      },
      cancel() {
        cancelled = true;
      },
    });
    const ai = { run: async () => stream } as unknown as Ai;

    await expect(collect(streamGenerate(ai, {
      systemPrompt: 'system',
      userMessage: 'question',
      streamTimeoutMs: 40,
    }))).resolves.toEqual([
      'Complete answer',
      `\x00model:${AI_MODEL_PRIMARY}`,
    ]);
    expect(cancelled).toBe(true);
  });

  it('yields nothing for an empty stream', async () => {
    const stream = makeStream([]);
    const chunks = await collect(drainStream(stream));
    expect(chunks).toEqual([]);
  });

  it('skips non-data lines (event:, id:, comments)', async () => {
    const lines = [
      'event: ping',
      ':keep-alive',
      'data: ' + JSON.stringify({ response: 'OK' }),
      'data: [DONE]',
    ].join('\n') + '\n';

    const stream = makeStream([encode(lines)]);
    const chunks = await collect(drainStream(stream));
    expect(chunks).toEqual(['OK']);
  });

  it('handles top-level response shape', async () => {
    const lines = [
      'data: ' + JSON.stringify({ response: 'Workers AI response' }),
      'data: [DONE]',
    ].join('\n') + '\n';

    const stream = makeStream([encode(lines)]);
    const chunks = await collect(drainStream(stream));
    expect(chunks).toEqual(['Workers AI response']);
  });

  it('handles a stream with no [DONE] sentinel', async () => {
    const lines = [
      'data: ' + JSON.stringify({ response: 'No sentinel' }),
    ].join('\n') + '\n';

    const stream = makeStream([encode(lines)]);
    const chunks = await collect(drainStream(stream));
    expect(chunks).toEqual(['No sentinel']);
  });

  it('skips malformed JSON lines without throwing', async () => {
    const lines = [
      'data: {broken',
      'data: ' + JSON.stringify({ response: 'Good' }),
      'data: [DONE]',
    ].join('\n') + '\n';

    const stream = makeStream([encode(lines)]);
    const chunks = await collect(drainStream(stream));
    expect(chunks).toEqual(['Good']);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Sentinel model extraction helper (mirrors what chat.ts does)
// ─────────────────────────────────────────────────────────────────────────────

describe('streamGenerate sentinel convention', () => {
  it('sentinel format identifies the model name correctly', () => {
    const sentinel = `\x00model:${AI_MODEL_PRIMARY}`;
    expect(sentinel.startsWith('\x00model:')).toBe(true);
    expect(sentinel.slice(7)).toBe(AI_MODEL_PRIMARY);
  });

  it('sentinel for fallback model is also extractable', () => {
    const sentinel = `\x00model:${AI_MODEL_FALLBACK}`;
    expect(sentinel.slice(7)).toBe(AI_MODEL_FALLBACK);
  });

  it('non-sentinel chunks do not start with \\x00model:', () => {
    const normalChunk = 'Hello, this is normal content';
    expect(normalChunk.startsWith('\x00model:')).toBe(false);
  });
});

describe('streamGenerate fallback behavior', () => {
  it('keeps each English streaming attempt capped at 3 seconds', () => {
    expect(AI_STREAM_TIMEOUT_MS).toBe(3000);
  });

  it('retries Workers AI fallback when the primary stream is empty', async () => {
    const calls: string[] = [];
    const ai = {
      run: async (model: string) => {
        calls.push(model);
        if (model === AI_MODEL_PRIMARY) return makeStream([]);
        return makeStream([encode('data: {"response":"Fallback answer"}\n')]);
      },
    } as unknown as Ai;

    await expect(collect(streamGenerate(ai, {
      systemPrompt: 'system',
      userMessage: 'hello',
    }))).resolves.toEqual([
      'Fallback answer',
      `\x00model:${AI_MODEL_FALLBACK}`,
    ]);
    expect(calls).toEqual([AI_MODEL_PRIMARY, AI_MODEL_FALLBACK]);
  });

  it('uses buffered generation when both stream models fail before content', async () => {
    const calls: Array<{ model: string; stream: boolean }> = [];
    const ai = {
      run: async (model: string, input: { stream?: boolean }) => {
        calls.push({ model, stream: input.stream === true });
        if (input.stream) throw new Error('stream transport unavailable');
        if (model === AI_MODEL_PRIMARY) return { response: 'Buffered recovery answer' };
        throw new Error('unexpected buffered fallback call');
      },
    } as unknown as Ai;

    await expect(collect(streamGenerate(ai, {
      systemPrompt: 'system',
      userMessage: 'hello',
    }))).resolves.toEqual([
      'Buffered recovery answer',
      `\x00model:${AI_MODEL_PRIMARY}`,
    ]);
    expect(calls).toEqual([
      { model: AI_MODEL_PRIMARY, stream: true },
      { model: AI_MODEL_FALLBACK, stream: true },
      { model: AI_MODEL_PRIMARY, stream: false },
    ]);
  });

  it('adapts a complete Workers AI response object to a stream', async () => {
    const ai = {
      run: async () => ({ response: 'Buffered but valid answer' }),
    } as unknown as Ai;

    await expect(collect(streamGenerate(ai, {
      systemPrompt: 'system',
      userMessage: 'hello',
    }))).resolves.toEqual([
      'Buffered but valid answer',
      `\x00model:${AI_MODEL_PRIMARY}`,
    ]);
  });
});

describe('Workers AI cancellation', () => {
  it('tries the fallback after the primary stream attempt times out', async () => {
    const calls: string[] = [];
    const bindingSignals: AbortSignal[] = [];
    const hanging = () => new ReadableStream<Uint8Array>({
      pull() {
        return new Promise<void>(() => {});
      },
    });
    const ai = {
      run: async (
        model: string,
        _input: unknown,
        options?: { signal?: AbortSignal },
      ) => {
        calls.push(model);
        if (options?.signal) bindingSignals.push(options.signal);
        if (model === AI_MODEL_PRIMARY) return hanging();
        return makeStream([encode('data: {"response":"Recovered answer"}\n')]);
      },
    } as unknown as Ai;

    await expect(collect(streamGenerate(ai, {
      systemPrompt: 'system',
      userMessage: 'hello',
      streamTimeoutMs: 20,
    }))).resolves.toEqual([
      'Recovered answer',
      `\x00model:${AI_MODEL_FALLBACK}`,
    ]);
    expect(calls).toEqual([AI_MODEL_PRIMARY, AI_MODEL_FALLBACK]);
    expect(bindingSignals).toHaveLength(2);
    expect(bindingSignals[0]?.aborted).toBe(true);
    expect(bindingSignals[1]?.aborted).toBe(false);
  });

  it('bounds an English stream that hangs after opening without tokens', async () => {
    const bindingSignals: AbortSignal[] = [];
    const hanging = () => new ReadableStream<Uint8Array>({
      pull() {
        return new Promise<void>(() => {});
      },
    });
    const ai = {
      run: async (
        _model: string,
        _input: unknown,
        options?: { signal?: AbortSignal },
      ) => {
        if (options?.signal) bindingSignals.push(options.signal);
        return hanging();
      },
    } as unknown as Ai;
    await expect(collect(streamGenerate(ai, {
      systemPrompt: 'system',
      userMessage: 'hello',
      streamTimeoutMs: 20,
    }))).rejects.toThrow('streaming attempt timed out');
    expect(bindingSignals).toHaveLength(2);
    expect(bindingSignals.every((signal) => signal.aborted)).toBe(true);
    expect(bindingSignals.every((signal) => (signal.reason as Error).name === 'TimeoutError')).toBe(true);
  });

  it('passes the caller signal to buffered inference and does not retry after abort', async () => {
    const controller = new AbortController();
    const calls: string[] = [];
    let bindingSignal: AbortSignal | undefined;
    const ai = {
      run: async (
        model: string,
        _input: unknown,
        options?: { signal?: AbortSignal },
      ) => {
        calls.push(model);
        bindingSignal = options?.signal;
        return new Promise<never>((_resolve, reject) => {
          const signal = options?.signal;
          if (!signal) {
            reject(new Error('Workers AI signal was not forwarded'));
            return;
          }
          signal.addEventListener('abort', () => reject(signal.reason), { once: true });
        });
      },
    } as unknown as Ai;

    const pending = generate(ai, {
      systemPrompt: 'system',
      userMessage: 'hello',
      signal: controller.signal,
    });
    controller.abort(new Error('client disconnected'));

    await expect(pending).rejects.toThrow('client disconnected');
    expect(bindingSignal).toBe(controller.signal);
    expect(calls).toEqual([AI_MODEL_PRIMARY]);
  });

  it('aborts a non-streaming binding call when its timeout expires', async () => {
    const calls: string[] = [];
    let bindingSignal: AbortSignal | undefined;
    const ai = {
      run: async (
        model: string,
        _input: unknown,
        options?: { signal?: AbortSignal },
      ) => {
        calls.push(model);
        bindingSignal = options?.signal;
        return new Promise<never>((_resolve, reject) => {
          const signal = options?.signal;
          if (!signal) {
            reject(new Error('Workers AI signal was not forwarded'));
            return;
          }
          signal.addEventListener('abort', () => reject(signal.reason), { once: true });
        });
      },
    } as unknown as Ai;

    await expect(generateAssamese(ai, {
      systemPrompt: 'system',
      userMessage: 'hello',
    }, 500)).rejects.toThrow('Assamese model quality repair timed out');

    expect(calls).toEqual([AI_MODEL_ASSAMESE]);
    expect(bindingSignal?.aborted).toBe(true);
    expect((bindingSignal?.reason as Error).name).toBe('TimeoutError');
  });

  it('forwards cancellation to streaming inference and cancels its response reader', async () => {
    const controller = new AbortController();
    let bindingSignal: AbortSignal | undefined;
    let streamCancelled = false;
    const ai = {
      run: async (
        _model: string,
        _input: unknown,
        options?: { signal?: AbortSignal },
      ) => {
        bindingSignal = options?.signal;
        return new ReadableStream<Uint8Array>({
          start(streamController) {
            streamController.enqueue(encode('data: {"response":"Partial"}\n'));
          },
          cancel() {
            streamCancelled = true;
          },
        });
      },
    } as unknown as Ai;

    const generation = streamGenerate(ai, {
      systemPrompt: 'system',
      userMessage: 'hello',
      signal: controller.signal,
    });
    await expect(generation.next()).resolves.toEqual({ done: false, value: 'Partial' });

    const pendingNext = generation.next();
    controller.abort(new Error('client disconnected'));

    await expect(pendingNext).rejects.toThrow('client disconnected');
    expect(bindingSignal).not.toBe(controller.signal);
    expect(streamCancelled).toBe(true);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Model name constants
// ─────────────────────────────────────────────────────────────────────────────

describe('model name constants', () => {
  it('primary model is the low-latency Workers AI Llama model', () => {
    expect(AI_MODEL_PRIMARY).toBe('@cf/meta/llama-3.1-8b-instruct-fast');
  });

  it('fallback model is @cf/qwen/qwen3-30b-a3b-fp8', () => {
    expect(AI_MODEL_FALLBACK).toBe('@cf/qwen/qwen3-30b-a3b-fp8');
    expect(AI_MODEL_ASSAMESE).toBe('@cf/aisingapore/gemma-sea-lion-v4-27b-it');
  });

  it('primary and fallback are distinct', () => {
    expect(AI_MODEL_PRIMARY).not.toBe(AI_MODEL_FALLBACK);
  });
});
