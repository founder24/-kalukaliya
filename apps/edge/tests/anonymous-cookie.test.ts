import { describe, expect, it, vi } from 'vitest';
import worker from '../src/index';

function context(): ExecutionContext {
  return {
    waitUntil: vi.fn(),
    passThroughOnException: vi.fn(),
  } as unknown as ExecutionContext;
}

function env(apiFetch: (request: Request) => Promise<Response>): Env {
  return {
    JWT_SECRET: 'edge-jwt-test-secret-at-least-32-characters',
    EDGE_SHARED_SECRET: 'edge-cookie-test-secret-at-least-32-characters',
    ALLOWED_ORIGIN: 'https://syrabit.ai',
    API_WORKER: { fetch: apiFetch },
  } as unknown as Env;
}

function cookiePair(setCookie: string): string {
  return setCookie.split(';', 1)[0]!;
}

describe('anonymous signed cookie lifecycle', () => {
  it('mints a cookie for storage-disabled credit requests and reuses it for history', async () => {
    const forwardedIds: string[] = [];
    const apiFetch = vi.fn(async (request: Request) => {
      forwardedIds.push(request.headers.get('x-anon-id') ?? '');
      return Response.json({ ok: true });
    });
    const testEnv = env(apiFetch);

    const credits = await worker.fetch(new Request(
      'https://syrabit.ai/api/v1/user/credits',
      { headers: { 'CF-Connecting-IP': '203.0.113.25' } },
    ), testEnv, context());
    const setCookie = credits.headers.get('Set-Cookie');
    expect(setCookie).toContain('syrabit_anon_id=anon_');
    expect(setCookie).toContain('HttpOnly; Secure; SameSite=Lax');
    expect(forwardedIds[0]).toMatch(/^anon_[a-f0-9]{32}$/);

    const history = await worker.fetch(new Request(
      'https://syrabit.ai/api/v1/conversations/anon',
      {
        headers: {
          Cookie: cookiePair(setCookie!),
          'CF-Connecting-IP': '198.51.100.77',
        },
      },
    ), testEnv, context());
    expect(history.status).toBe(200);
    expect(history.headers.get('Set-Cookie')).toBeNull();
    expect(forwardedIds[1]).toBe(forwardedIds[0]);
  });

  it('gives separate browsers on the same connection IP separate identities', async () => {
    const forwardedIds: string[] = [];
    const testEnv = env(async (request) => {
      forwardedIds.push(request.headers.get('x-anon-id') ?? '');
      return Response.json({ ok: true });
    });

    await worker.fetch(new Request(
      'https://syrabit.ai/api/v1/user/credits',
      { headers: { 'CF-Connecting-IP': '203.0.113.25' } },
    ), testEnv, context());
    await worker.fetch(new Request(
      'https://syrabit.ai/api/v1/user/credits',
      { headers: { 'CF-Connecting-IP': '203.0.113.25' } },
    ), testEnv, context());

    expect(forwardedIds).toHaveLength(2);
    expect(forwardedIds[1]).not.toBe(forwardedIds[0]);
  });

  it('preserves an upstream session cookie when minting the anonymous cookie', async () => {
    const testEnv = env(async () => new Response(
      JSON.stringify({ ok: true }),
      {
        headers: {
          'Content-Type': 'application/json',
          'Set-Cookie': 'syrabit_admin_session=session-token; Path=/api/; HttpOnly; Secure; SameSite=Lax',
        },
      },
    ));

    const response = await worker.fetch(new Request(
      'https://syrabit.ai/api/v1/admin/login',
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'CF-Connecting-IP': '203.0.113.25',
        },
        body: JSON.stringify({ email: 'admin@example.com', password: 'not-used' }),
      },
    ), testEnv, context());

    const cookies = response.headers.getSetCookie();
    expect(cookies).toHaveLength(2);
    expect(cookies.some(cookie => cookie.startsWith('syrabit_admin_session='))).toBe(true);
    expect(cookies.some(cookie => cookie.startsWith('syrabit_anon_id='))).toBe(true);
  });
});