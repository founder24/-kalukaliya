import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { turnstileVerify } from '../src/middleware/bot';
import worker from '../src/index';

describe('Turnstile Bot Verification', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('returns true when Cloudflare returns success', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: async () => ({ success: true }),
    });
    const result = await turnstileVerify('valid-token', 'test-secret');
    expect(result).toBe(true);
    expect(fetch).toHaveBeenCalledWith(
      'https://challenges.cloudflare.com/turnstile/v0/siteverify',
      expect.objectContaining({ method: 'POST' })
    );
  });

  it('returns false when Cloudflare returns failure', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      json: async () => ({ success: false }),
    });
    const result = await turnstileVerify('invalid-token', 'test-secret');
    expect(result).toBe(false);
  });

  it('returns false when fetch throws', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('Network error'));
    const result = await turnstileVerify('any-token', 'test-secret');
    expect(result).toBe(false);
  });
});

describe('Turnstile verification skipped when CF_TURNSTILE_SECRET is not configured', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  function createMockEnv(overrides: Partial<Env> = {}): Env {
    return {
      JWT_SECRET: 'test-secret-for-unit-tests-at-least-32-characters',
      CF_TURNSTILE_SECRET: '',
      AZURE_BACKEND_URL: 'http://localhost:8000',
      ALLOWED_ORIGIN: 'https://syrabit.ai',
      R2_BUCKET: { get: vi.fn(async () => null) } as unknown as R2Bucket,
      RATE_LIMIT_KV: {
        get: vi.fn(async () => null),
        put: vi.fn(async () => {}),
        delete: vi.fn(async () => {}),
      } as unknown as KVNamespace,
      ISR_CACHE_KV: {
        get: vi.fn(async () => null),
        put: vi.fn(async () => {}),
        delete: vi.fn(async () => {}),
      } as unknown as KVNamespace,
      ...overrides,
    };
  }

  function createMockCtx(): ExecutionContext {
    return {
      waitUntil: vi.fn(),
      passThroughOnException: vi.fn(),
    };
  }

  it('allows request through when CF_TURNSTILE_SECRET is empty string', async () => {
    const env = createMockEnv({ CF_TURNSTILE_SECRET: '' });
    const ctx = createMockCtx();

    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ reply: 'ok' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));

    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    const request = new Request('https://syrabit.ai/api/v1/chat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-turnstile-token': 'some-token',
        'User-Agent': 'Mozilla/5.0',
      },
      body: JSON.stringify({ message: 'hello', lang: 'en' }),
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).not.toBe(403);
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('CF_TURNSTILE_SECRET is not configured')
    );
  });

  it('allows request through when CF_TURNSTILE_SECRET is undefined', async () => {
    const env = createMockEnv({ CF_TURNSTILE_SECRET: undefined as unknown as string });
    const ctx = createMockCtx();

    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ reply: 'ok' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })));

    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    const request = new Request('https://syrabit.ai/api/v1/auth/login', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-turnstile-token': 'some-token',
        'User-Agent': 'Mozilla/5.0',
      },
      body: JSON.stringify({ email: 'test@test.com', password: 'pass' }),
    });

    const response = await worker.fetch(request, env, ctx);

    expect(response.status).not.toBe(403);
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining('CF_TURNSTILE_SECRET is not configured')
    );
  });
});
