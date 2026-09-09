/// <reference types="@cloudflare/vitest-pool-workers/types" />

import {
  env,
  runDurableObjectAlarm,
} from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import worker from '../src/index';
import { RateLimitDurableObject } from '../src/middleware/rate-limit';

const EDGE_SECRET = 'edge-runtime-test-secret-at-least-32-characters';
const JWT_SECRET = 'edge-runtime-jwt-secret-at-least-32-characters';

function context(): ExecutionContext {
  return {
    waitUntil: vi.fn(),
    passThroughOnException: vi.fn(),
  } as unknown as ExecutionContext;
}

function chatRequest(
  lang: 'en' | 'as',
  ip: string,
  cookie?: string,
  token?: string,
): Request {
  return new Request('https://syrabit.ai/api/v1/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'User-Agent': 'workers-runtime-rate-limit-test',
      'CF-Connecting-IP': ip,
      ...(cookie ? { Cookie: cookie } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ lang, message: 'Explain this chapter' }),
  });
}

async function authenticatedToken(userId: string): Promise<string> {
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

async function signedAnonymousCookie(id: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(EDGE_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign(
    'HMAC',
    key,
    new TextEncoder().encode(id),
  );
  const hex = Array.from(new Uint8Array(signature))
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('');
  return `syrabit_anon_id=${id}.${hex}`;
}

function runtimeEnv(apiFetch: (request: Request) => Promise<Response>): Env {
  return {
    ...env,
    JWT_SECRET,
    EDGE_SHARED_SECRET: EDGE_SECRET,
    ALLOWED_ORIGIN: 'https://syrabit.ai',
    API_WORKER: { fetch: apiFetch },
  } as unknown as Env;
}

async function runConcurrentBurst(
  requestForIndex: (index: number) => Request,
): Promise<{ responses: Response[]; apiFetch: ReturnType<typeof vi.fn> }> {
  const apiFetch = vi.fn(async () => Response.json({ ok: true }));
  const environment = runtimeEnv(apiFetch);
  const responses = await Promise.all(
    Array.from({ length: 10 }, (_, index) => worker.fetch(
      requestForIndex(index),
      environment,
      context(),
    )),
  );
  return { responses, apiFetch };
}

function expectSixAdmissions(
  responses: Response[],
  apiFetch: ReturnType<typeof vi.fn>,
): void {
  expect(responses.filter(response => response.status === 200)).toHaveLength(6);
  expect(responses.filter(response => response.status === 429)).toHaveLength(4);
  expect(apiFetch).toHaveBeenCalledTimes(6);
}

describe('anonymous burst protection in the Workers runtime', () => {
  it('admits exactly six concurrent English and Assamese requests for one signed browser', async () => {
    const cookie = await signedAnonymousCookie(
      'anon_0123456789abcdef0123456789abcdef',
    );
    const { responses, apiFetch } = await runConcurrentBurst(index =>
      chatRequest(
        index % 2 === 0 ? 'en' : 'as',
        `203.0.113.${index + 10}`,
        cookie,
      )
    );

    expectSixAdmissions(responses, apiFetch);
  });

  it('admits exactly six concurrent fresh identities on one trusted network', async () => {
    const { responses, apiFetch } = await runConcurrentBurst(index =>
      chatRequest(
        index % 2 === 0 ? 'en' : 'as',
        '198.51.100.45',
      )
    );

    expectSixAdmissions(responses, apiFetch);
  });

  it('admits a fresh request after the one-minute window alarm clears persisted buckets', async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-09-09T12:00:59.500Z'));
      const cookie = await signedAnonymousCookie(
        'anon_fedcba9876543210fedcba9876543210',
      );
      const apiFetch = vi.fn(async (request: Request) => Response.json(
        { ok: true },
        {
          headers: {
            'X-RateLimit-Limit': request.headers.get('X-RateLimit-Limit') ?? '',
            'X-RateLimit-Remaining': request.headers.get('X-RateLimit-Remaining') ?? '',
            'X-RateLimit-Reset': request.headers.get('X-RateLimit-Reset') ?? '',
          },
        },
      ));
      const environment = runtimeEnv(apiFetch);
      const request = () => chatRequest('en', '203.0.113.200', cookie);

      const admitted = [];
      for (let index = 0; index < 6; index += 1) {
        admitted.push(await worker.fetch(request(), environment, context()));
      }
      const blocked = await worker.fetch(request(), environment, context());

      expect(admitted.every(response => response.status === 200)).toBe(true);
      expect(blocked.status).toBe(429);
      expect(blocked.headers.get('X-RateLimit-Limit')).toBe('6');
      expect(blocked.headers.get('X-RateLimit-Remaining')).toBe('0');
      expect(blocked.headers.get('X-RateLimit-Reset')).toBe('1788955260');
      expect(blocked.headers.get('Retry-After')).toBe('1');
      expect(apiFetch).toHaveBeenCalledTimes(6);

      const windowKey = Math.floor(Date.now() / 60_000);
      const bucketNames = [
        `rl:network:ip_203_0_113_200:anonymous-chat:${windowKey}`,
        `rl:anon_fedcba9876543210fedcba9876543210:anonymous-chat:${windowKey}`,
      ];
      const alarmsRan = await Promise.all(bucketNames.map(name =>
        runDurableObjectAlarm(
          env.RATE_LIMIT_DO.get(env.RATE_LIMIT_DO.idFromName(name)),
        )
      ));
      expect(alarmsRan).toEqual([true, true]);

      vi.advanceTimersByTime(1_000);
      const restored = await worker.fetch(request(), environment, context());

      expect(restored.status).toBe(200);
      expect(restored.headers.get('X-RateLimit-Limit')).toBe('6');
      expect(restored.headers.get('X-RateLimit-Remaining')).toBe('5');
      expect(restored.headers.get('X-RateLimit-Reset')).toBe('1788955320');
      expect(apiFetch).toHaveBeenCalledTimes(7);
    } finally {
      vi.useRealTimers();
    }
  });

  it('retries expired bucket cleanup after an alarm handler failure', async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-09-09T12:00:00.000Z'));
      let deleteAttempts = 0;
      let scheduledAlarm: number | null = null;
      const storage = {
        setAlarm: vi.fn(async (time: number | Date) => {
          scheduledAlarm = typeof time === 'number' ? time : time.getTime();
        }),
        deleteAll: vi.fn(async () => {
          deleteAttempts += 1;
          if (deleteAttempts === 1) throw new Error('simulated storage failure');
        }),
        deleteAlarm: vi.fn(async () => {
          scheduledAlarm = null;
        }),
      };
      const durableObject = new RateLimitDurableObject({
        storage,
      } as unknown as DurableObjectState);

      await expect(durableObject.alarm()).rejects.toThrow('simulated storage failure');
      expect(scheduledAlarm).toBe(Date.now() + 5 * 60_000);
      expect(storage.deleteAlarm).not.toHaveBeenCalled();

      vi.advanceTimersByTime(5 * 60_000);
      await durableObject.alarm();

      expect(storage.deleteAll).toHaveBeenCalledTimes(2);
      expect(storage.deleteAlarm).toHaveBeenCalledTimes(1);
      expect(scheduledAlarm).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('authenticated per-language limits in the Workers runtime', () => {
  it('does not let excess English traffic consume the Assamese allowance', async () => {
    const token = await authenticatedToken('student-language-isolation');
    const forwardedLanguages: string[] = [];
    const apiFetch = vi.fn(async (request: Request) => {
      const body = await request.json() as { lang: string };
      forwardedLanguages.push(body.lang);
      return Response.json({ ok: true });
    });
    const environment = runtimeEnv(apiFetch);
    const languages = [
      ...Array.from({ length: 10 }, () => 'en' as const),
      ...Array.from({ length: 6 }, () => 'as' as const),
    ];

    const responses = await Promise.all(languages.map((lang, index) =>
      worker.fetch(
        chatRequest(lang, `203.0.113.${index + 20}`, undefined, token),
        environment,
        context(),
      )
    ));

    const englishResponses = responses.slice(0, 10);
    const assameseResponses = responses.slice(10);
    expect(englishResponses.filter(response => response.status === 200)).toHaveLength(6);
    expect(englishResponses.filter(response => response.status === 429)).toHaveLength(4);
    expect(assameseResponses.every(response => response.status === 200)).toBe(true);
    expect(forwardedLanguages.filter(lang => lang === 'en')).toHaveLength(6);
    expect(forwardedLanguages.filter(lang => lang === 'as')).toHaveLength(6);
    expect(apiFetch).toHaveBeenCalledTimes(12);
  });
});
