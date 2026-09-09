/// <reference types="@cloudflare/vitest-pool-workers/types" />

import { env } from 'cloudflare:test';
import { describe, expect, it, vi } from 'vitest';
import worker from '../src/index';

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
): Request {
  return new Request('https://syrabit.ai/api/v1/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'User-Agent': 'workers-runtime-rate-limit-test',
      'CF-Connecting-IP': ip,
      ...(cookie ? { Cookie: cookie } : {}),
    },
    body: JSON.stringify({ lang, message: 'Explain this chapter' }),
  });
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
});