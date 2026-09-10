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
      const values = new Map<string, unknown>();
      const storage = {
        get: vi.fn(async (key: string) => values.get(key)),
        put: vi.fn(async (key: string, value: unknown) => {
          values.set(key, value);
        }),
        setAlarm: vi.fn(async (time: number | Date) => {
          scheduledAlarm = typeof time === 'number' ? time : time.getTime();
        }),
        deleteAll: vi.fn(async () => {
          deleteAttempts += 1;
          if (deleteAttempts === 1) throw new Error('simulated storage failure');
          values.clear();
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

  it('deduplicates repeated cleanup alerts and resolves them after recovery', async () => {
    vi.useFakeTimers();
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const infoSpy = vi.spyOn(console, 'info').mockImplementation(() => {});
    try {
      vi.setSystemTime(new Date('2026-09-09T12:00:00.000Z'));
      const values = new Map<string, unknown>();
      const healthValues = new Map<string, string>();
      const healthKv = {
        get: vi.fn(async (key: string) => {
          return healthValues.get(key) ?? null;
        }),
        put: vi.fn(async (key: string, value: string) => {
          healthValues.set(key, value);
        }),
      };
      const aggregateStorage = new Map<string, unknown>();
      const aggregateState = {
        blockConcurrencyWhile: async (callback: () => Promise<unknown>) => callback(),
        storage: {
          transaction: async (callback: (txn: unknown) => Promise<unknown>) => callback({
            get: async (key: string) => aggregateStorage.get(key),
            put: async (key: string, value: unknown) => aggregateStorage.set(key, value),
            delete: async (key: string) => aggregateStorage.delete(key),
          }),
        },
      } as unknown as DurableObjectState;
      let aggregate: RateLimitDurableObject;
      const namespace = {
        idFromName: vi.fn(() => ({ toString: () => 'aggregate' })),
        get: vi.fn(() => ({
          fetch: (input: RequestInfo | URL, init?: RequestInit) =>
            aggregate.fetch(new Request(input, init)),
        })),
      } as unknown as DurableObjectNamespace;
      aggregate = new RateLimitDurableObject(aggregateState, {
        RATE_LIMIT_KV: healthKv as unknown as KVNamespace,
        RATE_LIMIT_DO: namespace,
      });
      let cleanupShouldFail = true;
      const storage = {
        get: vi.fn(async (key: string) => values.get(key)),
        put: vi.fn(async (key: string, value: unknown) => {
          values.set(key, value);
        }),
        setAlarm: vi.fn(async () => {}),
        deleteAll: vi.fn(async () => {
          if (cleanupShouldFail) throw new Error('simulated storage failure');
          values.clear();
        }),
        deleteAlarm: vi.fn(async () => {}),
      };
      const durableObject = new RateLimitDurableObject({
        storage,
      } as unknown as DurableObjectState, {
        RATE_LIMIT_KV: healthKv as unknown as KVNamespace,
        RATE_LIMIT_DO: namespace,
      });

      for (let attempt = 0; attempt < 4; attempt += 1) {
        await expect(durableObject.alarm()).rejects.toThrow('simulated storage failure');
        vi.advanceTimersByTime(5 * 60_000);
      }

      expect(errorSpy).toHaveBeenCalledTimes(1);
      expect(JSON.parse(String(errorSpy.mock.calls[0]?.[0]))).toEqual({
        event: 'rate_limit_cleanup_repeated_failure',
        failures: 3,
        retryInSeconds: 300,
      });
      expect(String(errorSpy.mock.calls[0]?.[0])).not.toContain('student');
      expect(JSON.parse(healthValues.get('health:rate-limit-cleanup') ?? '{}')).toEqual({
        degraded: true,
        active_incidents: 1,
        latest_failure_at: '2026-09-09T12:10:00.000Z',
        latest_recovery_at: null,
        rolling_incident_count: 1,
        history_window_hours: 24,
        recent_transitions: [
          { event: 'failed', occurred_at: '2026-09-09T12:10:00.000Z' },
        ],
        incident_count_buckets: [
          { started_at: '2026-09-09T12:10:00.000Z', count: 1 },
        ],
        alert: {
          enabled: false,
          threshold: 3,
          window_minutes: 60,
          state: 'disabled',
          last_fired_at: null,
          window_expires_at: null,
        },
      });

      vi.advanceTimersByTime(50 * 60_000);
      await expect(durableObject.alarm()).rejects.toThrow('simulated storage failure');
      expect(errorSpy).toHaveBeenCalledTimes(2);
      expect(JSON.parse(String(errorSpy.mock.calls[1]?.[0]))).toEqual({
        event: 'rate_limit_cleanup_repeated_failure',
        failures: 5,
        retryInSeconds: 300,
      });

      cleanupShouldFail = false;
      await durableObject.alarm();

      expect(infoSpy).toHaveBeenCalledTimes(1);
      expect(JSON.parse(String(infoSpy.mock.calls[0]?.[0]))).toEqual({
        event: 'rate_limit_cleanup_recovered',
        previousFailures: 5,
      });
      expect(values.size).toBe(0);
      expect(storage.deleteAlarm).toHaveBeenCalledTimes(1);
      expect(JSON.parse(healthValues.get('health:rate-limit-cleanup') ?? '{}')).toEqual({
        degraded: false,
        active_incidents: 0,
        latest_failure_at: '2026-09-09T13:10:00.000Z',
        latest_recovery_at: '2026-09-09T13:10:00.000Z',
        rolling_incident_count: 2,
        history_window_hours: 24,
        recent_transitions: [
          { event: 'failed', occurred_at: '2026-09-09T12:10:00.000Z' },
          { event: 'failed', occurred_at: '2026-09-09T13:10:00.000Z' },
          { event: 'recovered', occurred_at: '2026-09-09T13:10:00.000Z' },
        ],
        incident_count_buckets: [
          { started_at: '2026-09-09T12:10:00.000Z', count: 1 },
          { started_at: '2026-09-09T13:10:00.000Z', count: 1 },
        ],
        alert: {
          enabled: false,
          threshold: 3,
          window_minutes: 60,
          state: 'disabled',
          last_fired_at: null,
          window_expires_at: null,
        },
      });
    } finally {
      errorSpy.mockRestore();
      infoSpy.mockRestore();
      vi.useRealTimers();
    }
  });

  it('alerts once per aggregate window and resets after recovery or expiry', async () => {
    vi.useFakeTimers();
    const webhook = vi.fn(async () => new Response(null, { status: 204 }));
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockImplementation(webhook);
    try {
      vi.setSystemTime(new Date('2026-09-10T08:00:00.000Z'));
      const values = new Map<string, unknown>();
      const healthValues = new Map<string, string>();
      const state = {
        blockConcurrencyWhile: async (callback: () => Promise<unknown>) => callback(),
        storage: {
          get: async (key: string) => values.get(key),
          put: async (key: string, value: unknown) => values.set(key, value),
          delete: async (key: string) => values.delete(key),
          transaction: async (callback: (txn: unknown) => Promise<unknown>) => callback({
            get: async (key: string) => values.get(key),
            put: async (key: string, value: unknown) => values.set(key, value),
            delete: async (key: string) => values.delete(key),
          }),
        },
      } as unknown as DurableObjectState;
      const aggregate = new RateLimitDurableObject(state, {
        RATE_LIMIT_KV: {
          get: async (key: string) => healthValues.get(key) ?? null,
          put: async (key: string, value: string) => {
            healthValues.set(key, value);
          },
        } as unknown as KVNamespace,
        RATE_LIMIT_CLEANUP_ALERT_WEBHOOK_URL: 'https://alerts.example.test/cleanup',
        RATE_LIMIT_CLEANUP_ALERT_THRESHOLD: '2',
        RATE_LIMIT_CLEANUP_ALERT_WINDOW_MINUTES: '60',
      });
      const report = (action: 'failed' | 'recovered', incidentToken: string) =>
        aggregate.fetch(new Request('https://rate-limit.internal/cleanup-health', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            action,
            incidentToken,
            occurredAt: new Date().toISOString(),
          }),
        }));
      const first = '00000000-0000-4000-8000-000000000001';
      const second = '00000000-0000-4000-8000-000000000002';
      const third = '00000000-0000-4000-8000-000000000003';

      await report('failed', first);
      await report('failed', second);
      await report('failed', third);
      expect(webhook).toHaveBeenCalledTimes(1);

      const payload = JSON.parse(String(webhook.mock.calls[0]?.[1]?.body));
      expect(payload).toEqual({
        text: 'Chat-limit cleanup crossed the alert threshold: 2 incidents (threshold 2) from 2026-09-10T08:00:00.000Z to 2026-09-10T09:00:00.000Z; fired at 2026-09-10T08:00:00.000Z.',
        event: 'rate_limit_cleanup_incident_threshold_crossed',
        incident_count: 2,
        threshold: 2,
        window_started_at: '2026-09-10T08:00:00.000Z',
        window_expires_at: '2026-09-10T09:00:00.000Z',
        fired_at: '2026-09-10T08:00:00.000Z',
      });
      expect(JSON.stringify(payload)).not.toMatch(/student|bucket|incidentToken|identifier/i);

      await report('recovered', first);
      await report('recovered', second);
      await report('recovered', third);
      await report('failed', first);
      expect(webhook).toHaveBeenCalledTimes(2);

      vi.advanceTimersByTime(61 * 60_000);
      await report('failed', second);
      expect(webhook).toHaveBeenCalledTimes(3);
      expect(JSON.parse(healthValues.get('health:rate-limit-cleanup') ?? '{}').alert).toMatchObject({
        enabled: true,
        threshold: 2,
        window_minutes: 60,
        state: 'active',
      });
    } finally {
      fetchSpy.mockRestore();
      vi.useRealTimers();
    }
  });

  it.each([
    ['first then second', [0, 1]],
    ['second then first', [1, 0]],
  ])('keeps overlapping cleanup incidents degraded when recovering %s', async (_label, recoveryOrder) => {
    const healthValues = new Map<string, string>();
    const healthKv = {
      get: async (key: string) => healthValues.get(key) ?? null,
      put: async (key: string, value: string) => { healthValues.set(key, value); },
    } as unknown as KVNamespace;
    const aggregateValues = new Map<string, unknown>();
    const aggregate = new RateLimitDurableObject({
      blockConcurrencyWhile: async (callback: () => Promise<unknown>) => callback(),
      storage: {
        transaction: async (callback: (txn: unknown) => Promise<unknown>) => callback({
          get: async (key: string) => aggregateValues.get(key),
          put: async (key: string, value: unknown) => aggregateValues.set(key, value),
          delete: async (key: string) => aggregateValues.delete(key),
        }),
      },
    } as unknown as DurableObjectState, { RATE_LIMIT_KV: healthKv });
    const incidentTokens = [
      '11111111-1111-4111-8111-111111111111',
      '22222222-2222-4222-8222-222222222222',
    ];
    const transition = (action: 'failed' | 'recovered', incidentToken: string) =>
      aggregate.fetch(new Request('https://rate-limit.internal/cleanup-health', {
        method: 'POST',
        body: JSON.stringify({
          action,
          incidentToken,
          occurredAt: new Date().toISOString(),
        }),
      }));

    await transition('failed', incidentTokens[0]);
    await transition('failed', incidentTokens[1]);
    expect(JSON.parse(healthValues.get('health:rate-limit-cleanup') ?? '{}')).toMatchObject({
      degraded: true,
      active_incidents: 2,
    });

    await transition('recovered', incidentTokens[recoveryOrder[0]]);
    expect(JSON.parse(healthValues.get('health:rate-limit-cleanup') ?? '{}')).toMatchObject({
      degraded: true,
      active_incidents: 1,
    });

    await transition('recovered', incidentTokens[recoveryOrder[1]]);
    const persisted = healthValues.get('health:rate-limit-cleanup') ?? '{}';
    expect(JSON.parse(persisted)).toMatchObject({
      degraded: false,
      active_incidents: 0,
    });
    expect(persisted).not.toContain('11111111-1111-4111-8111-111111111111');
    expect(persisted).not.toContain('22222222-2222-4222-8222-222222222222');
    expect(persisted).not.toContain('student');
    expect(persisted).not.toContain('rl:');
  });

  it('caps cleanup transition history, prunes expired entries, and counts beyond the display cap', async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-09-09T12:00:00.000Z'));
      const healthValues = new Map<string, string>();
      healthValues.set('health:rate-limit-cleanup', JSON.stringify({
        degraded: false,
        active_incidents: 0,
        latest_failure_at: '2026-09-08T11:59:59.000Z',
        latest_recovery_at: null,
        rolling_incident_count: 1,
        history_window_hours: 24,
        recent_transitions: [
          { event: 'failed', occurred_at: '2026-09-08T11:59:59.000Z' },
        ],
        incident_count_buckets: [
          { started_at: '2026-09-08T11:59:00.000Z', count: 9 },
        ],
      }));
      const put = vi.fn(async (key: string, value: string) => {
        healthValues.set(key, value);
      });
      const aggregateValues = new Map<string, unknown>();
      const aggregate = new RateLimitDurableObject({
        blockConcurrencyWhile: async (callback: () => Promise<unknown>) => callback(),
        storage: {
          transaction: async (callback: (txn: unknown) => Promise<unknown>) => callback({
            get: async (key: string) => aggregateValues.get(key),
            put: async (key: string, value: unknown) => aggregateValues.set(key, value),
            delete: async (key: string) => aggregateValues.delete(key),
          }),
        },
      } as unknown as DurableObjectState, {
        RATE_LIMIT_KV: {
          get: async (key: string) => healthValues.get(key) ?? null,
          put,
        } as unknown as KVNamespace,
      });

      for (let index = 0; index < 22; index += 1) {
        const action = index % 2 === 0 ? 'failed' : 'recovered';
        await aggregate.fetch(new Request('https://rate-limit.internal/cleanup-health', {
          method: 'POST',
          body: JSON.stringify({
            action,
            incidentToken: '11111111-1111-4111-8111-111111111111',
            occurredAt: new Date(Date.now() + index * 1000).toISOString(),
          }),
        }));
      }

      const persisted = healthValues.get('health:rate-limit-cleanup') ?? '{}';
      const snapshot = JSON.parse(persisted);
      expect(snapshot.recent_transitions).toHaveLength(20);
      expect(snapshot.recent_transitions[0].occurred_at).toBe('2026-09-09T12:00:02.000Z');
      expect(snapshot.rolling_incident_count).toBe(11);
      expect(snapshot.incident_count_buckets).toEqual([
        { started_at: '2026-09-09T12:00:00.000Z', count: 11 },
      ]);
      expect(persisted).not.toContain('incidentToken');
      expect(persisted).not.toContain('student');
      expect(persisted).not.toContain('rl:');
      expect(put).toHaveBeenLastCalledWith(
        'health:rate-limit-cleanup',
        expect.any(String),
      );
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('authenticated per-language limits in the Workers runtime', () => {
  it('restores both English and Assamese allowances after the minute boundary', async () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-09-09T12:00:59.500Z'));
      const userId = 'student-both-language-reset';
      const token = await authenticatedToken(userId);
      const forwardedLanguages: string[] = [];
      const apiFetch = vi.fn(async (request: Request) => {
        const body = await request.json() as { lang: string };
        forwardedLanguages.push(body.lang);
        return Response.json({ ok: true });
      });
      const environment = runtimeEnv(apiFetch);
      const request = (lang: 'en' | 'as') =>
        chatRequest(lang, '203.0.113.210', undefined, token);

      for (const lang of ['en', 'as'] as const) {
        const admitted = [];
        for (let index = 0; index < 6; index += 1) {
          admitted.push(await worker.fetch(request(lang), environment, context()));
        }
        expect(admitted.every(response => response.status === 200)).toBe(true);
        expect((await worker.fetch(request(lang), environment, context())).status).toBe(429);
      }
      expect(forwardedLanguages.filter(lang => lang === 'en')).toHaveLength(6);
      expect(forwardedLanguages.filter(lang => lang === 'as')).toHaveLength(6);

      const windowKey = Math.floor(Date.now() / 60_000);
      const alarmsRan = await Promise.all((['en', 'as'] as const).map(lang =>
        runDurableObjectAlarm(
          env.RATE_LIMIT_DO.get(
            env.RATE_LIMIT_DO.idFromName(`rl:${userId}:${lang}:${windowKey}`),
          ),
        )
      ));
      expect(alarmsRan).toEqual([true, true]);

      vi.advanceTimersByTime(1_000);
      const restored = await Promise.all((['en', 'as'] as const).map(lang =>
        worker.fetch(request(lang), environment, context())
      ));

      expect(restored.every(response => response.status === 200)).toBe(true);
      expect(forwardedLanguages).toEqual([
        ...Array.from({ length: 6 }, () => 'en'),
        ...Array.from({ length: 6 }, () => 'as'),
        'en',
        'as',
      ]);
      expect(apiFetch).toHaveBeenCalledTimes(14);
    } finally {
      vi.useRealTimers();
    }
  });

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

  it('does not let excess Assamese traffic consume the English allowance', async () => {
    const token = await authenticatedToken('student-reverse-language-isolation');
    const forwardedLanguages: string[] = [];
    const apiFetch = vi.fn(async (request: Request) => {
      const body = await request.json() as { lang: string };
      forwardedLanguages.push(body.lang);
      return Response.json({ ok: true });
    });
    const environment = runtimeEnv(apiFetch);
    const languages = [
      ...Array.from({ length: 10 }, () => 'as' as const),
      ...Array.from({ length: 6 }, () => 'en' as const),
    ];

    const responses = await Promise.all(languages.map((lang, index) =>
      worker.fetch(
        chatRequest(lang, `198.51.100.${index + 20}`, undefined, token),
        environment,
        context(),
      )
    ));

    const assameseResponses = responses.slice(0, 10);
    const englishResponses = responses.slice(10);
    expect(assameseResponses.filter(response => response.status === 200)).toHaveLength(6);
    expect(assameseResponses.filter(response => response.status === 429)).toHaveLength(4);
    expect(englishResponses.every(response => response.status === 200)).toBe(true);
    expect(forwardedLanguages.filter(lang => lang === 'as')).toHaveLength(6);
    expect(forwardedLanguages.filter(lang => lang === 'en')).toHaveLength(6);
    expect(apiFetch).toHaveBeenCalledTimes(12);
  });
});
