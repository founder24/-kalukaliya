import { describe, expect, it, vi } from 'vitest';

import type { Env } from '../types';
import { healthRouter } from './health';

function healthyEnv(): Env {
  return {
    DB: { prepare: vi.fn(() => ({ first: vi.fn(async () => ({ 1: 1 })) })) },
    AI: { run: vi.fn(async () => ({ data: [[0.1]] })) },
    VECTORIZE: { describe: vi.fn(async () => ({ name: 'syrabit-rag', vectorsCount: 1 })) },
    R2_BUCKET: { head: vi.fn(async () => null) },
    CONTENT_KV: { get: vi.fn(async () => null) },
    RATE_LIMIT_KV: { get: vi.fn(async () => null) },
    EDGE_SHARED_SECRET: 'deep-health-test-secret',
  } as unknown as Env;
}

describe('API Worker deep health', () => {
  function deepRequest(): Request {
    return new Request('https://api.test/deep', {
      headers: { Authorization: 'Bearer deep-health-test-secret' },
    });
  }

  it('exercises every critical binding without writes', async () => {
    const env = healthyEnv();
    const response = await healthRouter.fetch(deepRequest(), env);
    const payload = await response.json() as Record<string, any>;

    expect(response.status).toBe(200);
    expect(payload.status).toBe('healthy');
    expect(payload.missing_bindings).toEqual([]);
    expect(payload.mutation_free).toBe(true);
    expect(Object.keys(payload.checks).sort()).toEqual(
      ['content_kv', 'd1', 'r2', 'rate_limit_kv', 'vectorize', 'workers_ai'].sort(),
    );
    expect(env.AI.run).toHaveBeenCalledWith('@cf/baai/bge-m3', { text: ['health probe'] });
    expect(env.R2_BUCKET.head).toHaveBeenCalledWith('__health_probe__');
    expect(env.CONTENT_KV.get).toHaveBeenCalledWith('__health_probe__');
    expect(env.RATE_LIMIT_KV.get).toHaveBeenCalledWith('__health_probe__');
  });

  it('returns 503 and names missing bindings', async () => {
    const env = healthyEnv();
    env.VECTORIZE = undefined as unknown as VectorizeIndex;

    const response = await healthRouter.fetch(deepRequest(), env);
    const payload = await response.json() as Record<string, any>;

    expect(response.status).toBe(503);
    expect(payload.status).toBe('degraded');
    expect(payload.missing_bindings).toContain('VECTORIZE');
    expect(payload.checks.vectorize.status).toBe('unbound');
  });

  it('bounds a stalled dependency probe', async () => {
    vi.useFakeTimers();
    const env = healthyEnv();
    env.AI = {
      run: vi.fn(() => new Promise(() => undefined)),
    } as unknown as Ai;

    const responsePromise = healthRouter.fetch(deepRequest(), env);
    await vi.advanceTimersByTimeAsync(4_001);
    const response = await responsePromise;
    const payload = await response.json() as Record<string, any>;
    vi.useRealTimers();

    expect(response.status).toBe(503);
    expect(payload.checks.workers_ai.status).toBe('error');
    expect(payload.checks.workers_ai.detail).toContain('timed out');
  });

  it('does not run paid probes for unauthenticated callers', async () => {
    const env = healthyEnv();
    const response = await healthRouter.fetch(
      new Request('https://api.test/deep'),
      env,
    );

    expect(response.status).toBe(401);
    expect(env.AI.run).not.toHaveBeenCalled();
  });
});