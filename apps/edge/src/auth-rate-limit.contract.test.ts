import { afterEach, describe, expect, it, vi } from 'vitest';

import edgeWorker from './index';

function executionContext(): ExecutionContext {
  return {
    waitUntil() {},
    passThroughOnException() {},
  } as unknown as ExecutionContext;
}

function authRequest(ip?: string): Request {
  return new Request('https://syrabit.ai/api/v1/auth/login', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Forwarded-For': '203.0.113.250',
      ...(ip ? { 'CF-Connecting-IP': ip } : {}),
    },
    body: JSON.stringify({ email: 'student@example.test', password: 'incorrect' }),
  });
}

function mockNamespace(fetch: (id: string) => Promise<Response>) {
  const names: string[] = [];
  const namespace = {
    idFromName(name: string) {
      names.push(name);
      return name;
    },
    get(id: string) {
      return { fetch: () => fetch(id) };
    },
  } as unknown as DurableObjectNamespace;
  return { namespace, names };
}

function environment(
  apiFetch: (request: Request) => Promise<Response>,
  RATE_LIMIT_DO?: DurableObjectNamespace,
): Env {
  return {
    API_WORKER: { fetch: apiFetch },
    RATE_LIMIT_DO,
    JWT_SECRET: 'auth-rate-limit-test-secret-at-least-32-characters',
    EDGE_SHARED_SECRET: 'auth-rate-limit-edge-secret-at-least-32-characters',
    ALLOWED_ORIGIN: 'https://syrabit.ai',
  } as unknown as Env;
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('edge authentication rate-limit contract', () => {
  it('rejects an exhausted IP bucket before forwarding and returns retry headers', async () => {
    const ip = '198.51.100.8';
    const { namespace, names } = mockNamespace(async () => Response.json({
      allowed: false,
      remaining: 0,
      resetAt: Date.now() + 60_000,
    }));
    const apiFetch = vi.fn(async () => Response.json({ ok: true }));

    const response = await edgeWorker.fetch(
      authRequest(ip),
      environment(apiFetch, namespace),
      executionContext(),
    );

    expect(response.status).toBe(429);
    expect(response.headers.get('Retry-After')).toBeTruthy();
    const limitedBody = await response.json() as { error_code?: string };
    expect(limitedBody.error_code).toBe('auth_rate_limited');
    expect(apiFetch).not.toHaveBeenCalled();
    expect(names[0]).toContain('auth:login');
    expect(names[0]).not.toContain(ip);
    expect(names[0]).not.toContain('203.0.113.250');
  });

  it('fails closed when the Durable Object binding is missing', async () => {
    const apiFetch = vi.fn(async () => Response.json({ ok: true }));

    const response = await edgeWorker.fetch(
      authRequest('198.51.100.9'),
      environment(apiFetch),
      executionContext(),
    );

    expect(response.status).toBe(503);
    const unavailableBody = await response.json() as { error_code?: string };
    expect(unavailableBody.error_code).toBe('rate_limit_storage_unavailable');
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('fails closed when the trusted Cloudflare client-IP header is absent', async () => {
    const storageFetch = vi.fn(async () => Response.json({ allowed: true, remaining: 9, resetAt: Date.now() + 60_000 }));
    const { namespace, names } = mockNamespace(storageFetch);
    const apiFetch = vi.fn(async () => Response.json({ ok: true }));

    const response = await edgeWorker.fetch(
      authRequest(),
      environment(apiFetch, namespace),
      executionContext(),
    );

    expect(response.status).toBe(503);
    expect(names).toHaveLength(0);
    expect(storageFetch).not.toHaveBeenCalled();
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('fails closed when the Durable Object request fails', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const { namespace } = mockNamespace(async () => {
      throw new Error('simulated storage failure');
    });
    const apiFetch = vi.fn(async () => Response.json({ ok: true }));

    const response = await edgeWorker.fetch(
      authRequest('198.51.100.10'),
      environment(apiFetch, namespace),
      executionContext(),
    );

    expect(response.status).toBe(503);
    const unavailableBody = await response.json() as { error_code?: string };
    expect(unavailableBody.error_code).toBe('rate_limit_storage_unavailable');
    expect(apiFetch).not.toHaveBeenCalled();
  });
});