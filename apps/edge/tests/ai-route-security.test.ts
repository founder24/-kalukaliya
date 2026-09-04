import { describe, expect, it, vi } from 'vitest';

import worker from '../src/index';
import { createMockRateLimitNamespace } from './helpers/rate-limit-store';

const JWT_SECRET = 'test-secret-for-unit-tests-at-least-32-characters';

function allowRateLimitNamespace(): DurableObjectNamespace {
  return {
    idFromName: (name: string) => name,
    get: () => ({
      fetch: vi.fn(async () => Response.json({
        allowed: true,
        remaining: 19,
        resetAt: Date.now() + 60_000,
      })),
    }),
  } as unknown as DurableObjectNamespace;
}

function env(overrides: Partial<Env> = {}): Env {
  return {
    JWT_SECRET,
    EDGE_SHARED_SECRET: 'edge-test-secret',
    ALLOWED_ORIGIN: 'https://syrabit.ai',
    R2_BUCKET: { get: vi.fn(async () => null) } as unknown as R2Bucket,
    RATE_LIMIT_KV: {} as KVNamespace,
    ISR_CACHE_KV: {} as KVNamespace,
    RATE_LIMIT_DO: allowRateLimitNamespace(),
    API_WORKER: { fetch: vi.fn(async () => new Response('not found', { status: 404 })) },
    AI: { run: vi.fn(async () => new Response(new ArrayBuffer(1))) } as unknown as Ai,
    ...overrides,
  };
}

function ctx(): ExecutionContext {
  return { waitUntil: vi.fn(), passThroughOnException: vi.fn() };
}

async function accessToken(userId = 'student-1'): Promise<string> {
  const encode = (value: Record<string, unknown>) => btoa(JSON.stringify(value))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  const header = encode({ alg: 'HS256', typ: 'JWT' });
  const payload = encode({ sub: userId, type: 'access', exp: Math.floor(Date.now() / 1000) + 3600 });
  const key = await crypto.subtle.importKey(
    'raw', new TextEncoder().encode(JWT_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  );
  const signature = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(`${header}.${payload}`));
  const encodedSignature = btoa(String.fromCharCode(...new Uint8Array(signature)))
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${header}.${payload}.${encodedSignature}`;
}

describe('edge AI route validation', () => {
  it('rejects non-JSON TTS input without AI execution', async () => {
    const environment = env();
    const response = await worker.fetch(new Request('https://edge.test/api/v1/chat/tts', {
      method: 'POST',
      headers: { Authorization: `Bearer ${await accessToken()}`, 'Content-Type': 'text/plain' },
      body: 'hello',
    }), environment, ctx());

    expect(response.status).toBe(415);
    expect(environment.AI!.run).not.toHaveBeenCalled();
  });

  it('rejects OCR content whose bytes do not match the declared image MIME type', async () => {
    const environment = env();
    const form = new FormData();
    form.set('file', new Blob(['not a png'], { type: 'image/png' }), 'image.png');
    const response = await worker.fetch(new Request('https://edge.test/api/v1/chat/image', {
      method: 'POST',
      headers: { Authorization: `Bearer ${await accessToken()}` },
      body: form,
    }), environment, ctx());

    expect(response.status).toBe(400);
    expect(environment.AI!.run).not.toHaveBeenCalled();
  });

  it('uses a dedicated TTS rate-limit bucket before calling Workers AI', async () => {
    const { namespace, fetch } = await createMockRateLimitNamespace();
    const environment = env({ RATE_LIMIT_DO: namespace });
    const response = await worker.fetch(new Request('https://edge.test/api/v1/chat/tts', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${await accessToken('student-2')}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ text: 'Hello', lang: 'en' }),
    }), environment, ctx());

    expect(response.status).toBe(200);
    expect(fetch.mock.calls.some(([bucket]) => String(bucket).includes('rl:student-2:tts:'))).toBe(true);
    expect(environment.AI!.run).toHaveBeenCalledWith(
      '@cf/myshell/melotts', { prompt: 'Hello', language: 'EN' },
    );
  });
});