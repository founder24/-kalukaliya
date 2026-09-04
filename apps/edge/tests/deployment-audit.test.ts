import { describe, expect, it, vi } from 'vitest';
import worker from '../src/index';
import { createMockRateLimitNamespace } from './helpers/rate-limit-store';

function env(overrides: Partial<Env> = {}): Env {
  return {
    JWT_SECRET: 'test-secret-for-unit-tests-at-least-32-characters',
    EDGE_SHARED_SECRET: 'edge-test-secret',
    ALLOWED_ORIGIN: 'https://syrabit.ai',
    R2_BUCKET: { get: vi.fn(async () => null) } as unknown as R2Bucket,
    RATE_LIMIT_KV: {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace,
    RATE_LIMIT_DO: createMockRateLimitNamespace().namespace,
    ISR_CACHE_KV: {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace,
    ...overrides,
  };
}

function ctx(): ExecutionContext {
  return {
    waitUntil: vi.fn(),
    passThroughOnException: vi.fn(),
  };
}

describe('edge deployment routing audit', () => {
  it('serves API preflight without requiring a service binding', async () => {
    const response = await worker.fetch(
      new Request('https://api.syrabit.ai/api/v1/chat', { method: 'OPTIONS' }),
      env(),
      ctx(),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get('Access-Control-Allow-Methods')).toContain('POST');
  });

  it('returns an explicit 503 when an API service binding is absent', async () => {
    const response = await worker.fetch(
      new Request('https://api.syrabit.ai/api/v1/content/subjects'),
      env(),
      ctx(),
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      error_code: 'api_worker_binding_unavailable',
    });
  });

  it('routes API requests exclusively through the service binding', async () => {
    const apiFetch = vi.fn(async () => new Response('{"subjects":[]}', {
      headers: { 'Content-Type': 'application/json' },
    }));
    const response = await worker.fetch(
      new Request('https://api.syrabit.ai/api/v1/content/subjects'),
      env({ API_WORKER: { fetch: apiFetch } }),
      ctx(),
    );

    expect(response.status).toBe(200);
    expect(apiFetch).toHaveBeenCalledOnce();
  });

  it('returns an explicit 503 for health when its service binding is absent', async () => {
    const response = await worker.fetch(
      new Request('https://api.syrabit.ai/health'),
      env(),
      ctx(),
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      error_code: 'api_worker_binding_unavailable',
    });
  });

  it('reports the API Worker health probe result', async () => {
    const apiFetch = vi.fn(async () => new Response(null, { status: 200 }));
    const response = await worker.fetch(
      new Request('https://api.syrabit.ai/health'),
      env({ API_WORKER: { fetch: apiFetch } }),
      ctx(),
    );

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      backend_reachable: true,
      backend_mode: 'api-worker',
    });
    expect(response.headers.get('X-Syrabit-Health-Backend')).toBe('api-worker');
  });

  it('returns 503 for degraded full health while preserving its response body', async () => {
    const apiFetch = vi.fn(async () => Response.json({
      status: 'degraded',
      checks: { d1: { status: 'error' } },
    }));
    const response = await worker.fetch(
      new Request('https://api.syrabit.ai/health/full'),
      env({ API_WORKER: { fetch: apiFetch } }),
      ctx(),
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      status: 'degraded',
      edge: { status: 'healthy' },
      backend: { status: 'degraded' },
    });
  });

  it('serves R2 assets and keeps unknown non-API routes isolated', async () => {
    const missingAsset = await worker.fetch(
      new Request('https://api.syrabit.ai/assets/missing.js'),
      env(),
      ctx(),
    );
    const unknown = await worker.fetch(
      new Request('https://syrabit.ai/unknown-path', { method: 'POST' }),
      env(),
      ctx(),
    );

    expect(missingAsset.status).toBe(404);
    expect(unknown.status).toBe(404);
  });
});