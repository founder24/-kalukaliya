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
  streamGenerate,
  AI_MODEL_PRIMARY,
  AI_MODEL_FALLBACK,
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

// ─────────────────────────────────────────────────────────────────────────────
// Model name constants
// ─────────────────────────────────────────────────────────────────────────────

describe('model name constants', () => {
  it('primary model is @cf/zai-org/glm-4.7-flash', () => {
    expect(AI_MODEL_PRIMARY).toBe('@cf/zai-org/glm-4.7-flash');
  });

  it('fallback model is @cf/qwen/qwen3-30b-a3b-fp8', () => {
    expect(AI_MODEL_FALLBACK).toBe('@cf/qwen/qwen3-30b-a3b-fp8');
  });

  it('primary and fallback are distinct', () => {
    expect(AI_MODEL_PRIMARY).not.toBe(AI_MODEL_FALLBACK);
  });
});
