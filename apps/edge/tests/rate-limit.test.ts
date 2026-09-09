import { afterEach, describe, it, expect, vi } from 'vitest';
import worker from '../src/index';
import {
  anonymousRateLimitIdentity,
  checkRateLimit,
  resolveAnonymousIdentity,
} from '../src/middleware/rate-limit';
import { createMockRateLimitNamespace } from './helpers/rate-limit-store';

const EDGE_SECRET = 'edge-rate-limit-test-secret-at-least-32-characters';
const JWT_SECRET = 'edge-jwt-test-secret-at-least-32-characters';
const FIXED_NOW = Date.UTC(2026, 8, 9, 12, 0, 30);

afterEach(() => {
  vi.restoreAllMocks();
});

function context(): ExecutionContext {
  return {
    waitUntil: vi.fn(),
    passThroughOnException: vi.fn(),
  } as unknown as ExecutionContext;
}

function edgeEnv(
  namespace: DurableObjectNamespace,
  apiFetch: (request: Request) => Promise<Response>,
): Env {
  return {
    JWT_SECRET,
    EDGE_SHARED_SECRET: EDGE_SECRET,
    ALLOWED_ORIGIN: 'https://syrabit.ai',
    RATE_LIMIT_DO: namespace,
    API_WORKER: { fetch: apiFetch },
  } as unknown as Env;
}

function chatRequest(lang: 'en' | 'as', headers: HeadersInit = {}): Request {
  return new Request('https://syrabit.ai/api/v1/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'User-Agent': 'rate-limit-integration-test',
      ...headers,
    },
    body: JSON.stringify({ lang, message: 'Explain this chapter' }),
  });
}

function cookiePair(setCookie: string): string {
  return setCookie.split(';', 1)[0]!;
}

async function createTestJWT(userId: string): Promise<string> {
  const encode = (value: object) => btoa(JSON.stringify(value))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  const header = encode({ alg: 'HS256', typ: 'JWT' });
  const payload = encode({
    sub: userId,
    type: 'access',
    exp: Math.floor(Date.now() / 1000) + 3600,
  });
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(JWT_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign(
    'HMAC',
    key,
    encoder.encode(`${header}.${payload}`),
  );
  const encodedSignature = btoa(String.fromCharCode(...new Uint8Array(signature)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  return `${header}.${payload}.${encodedSignature}`;
}

describe('Rate Limiting', () => {
  it('uses one edge-minted ID for the cookie, API header, and mixed-language limiter bucket', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(FIXED_NOW);
    const { namespace, fetch } = await createMockRateLimitNamespace();
    const forwardedIds: string[] = [];
    const apiFetch = vi.fn(async (request: Request) => {
      forwardedIds.push(request.headers.get('x-anon-id') ?? '');
      return Response.json({ ok: true });
    });
    const environment = edgeEnv(namespace, apiFetch);

    const first = await worker.fetch(chatRequest('en', {
      'CF-Connecting-IP': '203.0.113.1',
    }), environment, context());
    const setCookie = first.headers.get('Set-Cookie');
    const mintedId = forwardedIds[0];
    const identityBucket = fetch.mock.calls
      .map(call => call[0] as string)
      .find(bucket => bucket.startsWith('rl:anon_'));

    expect(first.status).toBe(200);
    expect(mintedId).toMatch(/^anon_[a-f0-9]{32}$/);
    expect(setCookie).toContain(`syrabit_anon_id=${mintedId}.`);
    expect(identityBucket).toMatch(new RegExp(`^rl:${mintedId}:anonymous-chat:`));

    const cookie = cookiePair(setCookie!);
    const responses = [first];
    for (let index = 1; index < 7; index += 1) {
      responses.push(await worker.fetch(chatRequest(index % 2 === 0 ? 'en' : 'as', {
        Cookie: cookie,
        'CF-Connecting-IP': `203.0.113.${index + 1}`,
      }), environment, context()));
    }

    expect(responses.slice(0, 6).map(response => response.status)).toEqual(
      Array(6).fill(200),
    );
    expect(responses[6]?.status).toBe(429);
    expect(forwardedIds).toEqual(Array(6).fill(mintedId));
    expect(fetch.mock.calls.filter(call =>
      (call[0] as string).startsWith(`rl:${mintedId}:anonymous-chat:`)
    )).toHaveLength(7);
  });

  it('shares one six-request mixed-language bucket across fresh identities on a trusted network', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(FIXED_NOW);
    const { namespace, fetch } = await createMockRateLimitNamespace();
    const apiFetch = vi.fn(async () => Response.json({ ok: true }));
    const environment = edgeEnv(namespace, apiFetch);
    const responses: Response[] = [];

    for (let index = 0; index < 7; index += 1) {
      responses.push(await worker.fetch(chatRequest(index % 2 === 0 ? 'en' : 'as', {
        'CF-Connecting-IP': '198.51.100.44',
      }), environment, context()));
    }

    expect(responses.slice(0, 6).map(response => response.status)).toEqual(
      Array(6).fill(200),
    );
    expect(responses[6]?.status).toBe(429);
    expect(apiFetch).toHaveBeenCalledTimes(6);
    expect(fetch.mock.calls.filter(call =>
      (call[0] as string).startsWith('rl:network:ip_198_51_100_44:anonymous-chat:')
    )).toHaveLength(7);
  });

  it('admits exactly six concurrent mixed-language requests for one signed browser identity', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(FIXED_NOW);
    const { namespace } = await createMockRateLimitNamespace();
    const apiFetch = vi.fn(async () => Response.json({ ok: true }));
    const environment = edgeEnv(namespace, apiFetch);
    const identity = await resolveAnonymousIdentity(new Request(
      'https://syrabit.ai/api/v1/user/credits',
      { headers: { 'CF-Connecting-IP': '203.0.113.20' } },
    ), EDGE_SECRET);
    const cookie = cookiePair(identity.setCookie!);

    const responses = await Promise.all(
      Array.from({ length: 10 }, (_, index) => worker.fetch(
        chatRequest(index % 2 === 0 ? 'en' : 'as', {
          Cookie: cookie,
          'CF-Connecting-IP': `203.0.113.${index + 20}`,
        }),
        environment,
        context(),
      )),
    );

    expect(responses.filter(response => response.status === 200)).toHaveLength(6);
    expect(responses.filter(response => response.status === 429)).toHaveLength(4);
    expect(apiFetch).toHaveBeenCalledTimes(6);
  });

  it('admits exactly six concurrent mixed-language requests from fresh identities on one trusted network', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(FIXED_NOW);
    const { namespace } = await createMockRateLimitNamespace();
    const apiFetch = vi.fn(async () => Response.json({ ok: true }));
    const environment = edgeEnv(namespace, apiFetch);

    const responses = await Promise.all(
      Array.from({ length: 10 }, (_, index) => worker.fetch(
        chatRequest(index % 2 === 0 ? 'en' : 'as', {
          'CF-Connecting-IP': '198.51.100.45',
        }),
        environment,
        context(),
      )),
    );

    expect(responses.filter(response => response.status === 200)).toHaveLength(6);
    expect(responses.filter(response => response.status === 429)).toHaveLength(4);
    expect(apiFetch).toHaveBeenCalledTimes(6);
  });

  it('retains the separate authenticated per-language edge policy', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(FIXED_NOW);
    const { namespace, fetch } = await createMockRateLimitNamespace();
    const apiFetch = vi.fn(async () => Response.json({ ok: true }));
    const environment = edgeEnv(namespace, apiFetch);
    const token = await createTestJWT('student-123');
    const responses: Response[] = [];

    for (let index = 0; index < 7; index += 1) {
      responses.push(await worker.fetch(chatRequest(index % 2 === 0 ? 'en' : 'as', {
        Authorization: `Bearer ${token}`,
        'CF-Connecting-IP': '192.0.2.10',
      }), environment, context()));
    }

    expect(responses.map(response => response.status)).toEqual(Array(7).fill(200));
    expect(apiFetch).toHaveBeenCalledTimes(7);
    expect(fetch.mock.calls.every(call =>
      /^rl:student-123:(en|as):/.test(call[0] as string)
    )).toBe(true);
    expect(fetch.mock.calls.some(call => (call[0] as string).includes(':en:'))).toBe(true);
    expect(fetch.mock.calls.some(call => (call[0] as string).includes(':as:'))).toBe(true);
    expect(fetch.mock.calls.some(call => (call[0] as string).includes(':anonymous-chat:')))
      .toBe(false);
    expect(fetch.mock.calls.some(call => (call[0] as string).includes('rl:network:')))
      .toBe(false);
  });

  it('does not trust a caller-selected anonymous ID without a signed cookie', async () => {
    const request = new Request('https://syrabit.ai/api/v1/chat/stream', {
      headers: {
        'x-anon-id': 'anon_0123456789abcdef0123456789abcdef',
        'CF-Connecting-IP': '203.0.113.9',
      },
    });

    await expect(anonymousRateLimitIdentity(request)).resolves
      .toBe('ip_203_0_113_9');
  });

  it('uses the connection IP when the browser ID is missing or malformed', async () => {
    const request = new Request('https://syrabit.ai/api/v1/chat/stream', {
      headers: {
        'x-anon-id': 'anon_wrong',
        'CF-Connecting-IP': '203.0.113.9',
      },
    });

    await expect(anonymousRateLimitIdentity(request)).resolves.toBe('ip_203_0_113_9');
  });

  it('does not trust caller-controlled forwarding headers for fallback identity', async () => {
    const request = new Request('https://syrabit.ai/api/v1/chat/stream', {
      headers: {
        'x-anon-id': 'anon_wrong',
        'X-Real-IP': '203.0.113.9',
        'X-Forwarded-For': '198.51.100.44',
      },
    });

    await expect(anonymousRateLimitIdentity(request)).resolves.toBe('ip_unknown');
  });

  it('mints and reuses separate signed cookies for browsers on one shared IP', async () => {
    const secret = 'edge-cookie-secret-at-least-32-characters';
    const first = await resolveAnonymousIdentity(new Request(
      'https://syrabit.ai/api/v1/user/credits',
      { headers: { 'CF-Connecting-IP': '203.0.113.9' } },
    ), secret);
    const second = await resolveAnonymousIdentity(new Request(
      'https://syrabit.ai/api/v1/user/credits',
      { headers: { 'CF-Connecting-IP': '203.0.113.9' } },
    ), secret);

    expect(first.id).toMatch(/^anon_[a-f0-9]{32}$/);
    expect(second.id).toMatch(/^anon_[a-f0-9]{32}$/);
    expect(second.id).not.toBe(first.id);
    expect(first.setCookie).toContain('HttpOnly; Secure; SameSite=Lax');
    expect(first.setCookie).toContain('Max-Age=31536000');

    const cookie = first.setCookie!.split(';', 1)[0]!;
    const reload = await resolveAnonymousIdentity(new Request(
      'https://syrabit.ai/api/v1/conversations/anon',
      {
        headers: {
          Cookie: cookie,
          'CF-Connecting-IP': '198.51.100.44',
        },
      },
    ), secret);
    expect(reload).toEqual({ id: first.id, setCookie: null });
  });

  it('allows request under limit', async () => {
    const { namespace } = await createMockRateLimitNamespace();
    const result = await checkRateLimit(namespace, 'user-1', 'en', 30);
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(29);
  });

  it('blocks request at limit', async () => {
    const { namespace } = await createMockRateLimitNamespace(30);
    const result = await checkRateLimit(namespace, 'user-1', 'en', 30);
    expect(result.allowed).toBe(false);
    expect(result.remaining).toBe(0);
  });

  it('routes a bucket through one Durable Object stub', async () => {
    const { namespace, fetch } = await createMockRateLimitNamespace();
    await checkRateLimit(namespace, 'user-2', 'as', 10);
    await checkRateLimit(namespace, 'user-2', 'as', 10);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls[0]?.[0]).toContain('rl:user-2:as:');
  });

  it('returns correct remaining count', async () => {
    const { namespace } = await createMockRateLimitNamespace(5);
    const result = await checkRateLimit(namespace, 'user-3', 'en', 10);
    expect(result.allowed).toBe(true);
    expect(result.remaining).toBe(4);
  });

  it('includes resetAt timestamp', async () => {
    const { namespace } = await createMockRateLimitNamespace();
    const result = await checkRateLimit(namespace, 'user-4', 'en', 30);
    expect(result.resetAt).toBeGreaterThan(Date.now());
  });

  it('allows exactly the limit under parallel requests', async () => {
    const { namespace } = await createMockRateLimitNamespace();
    const results = await Promise.all(
      Array.from({ length: 40 }, () => checkRateLimit(namespace, 'parallel-user', 'en', 30)),
    );
    expect(results.filter(result => result.allowed)).toHaveLength(30);
    expect(results.filter(result => !result.allowed)).toHaveLength(10);
  });
});
