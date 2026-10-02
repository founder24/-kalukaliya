import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Env } from '../types';

const resetMocks = vi.hoisted(() => ({
  createDb: vi.fn(),
  enforceAuthRateLimit: vi.fn(async () => null),
  select: vi.fn(),
  insert: vi.fn(),
  insertedValues: vi.fn(),
  user: { id: 'user-1' } as { id: string } | null,
}));

vi.mock('../db/client', () => ({
  createDb: resetMocks.createDb,
}));
vi.mock('../services/auth-rate-limit', () => ({
  enforceAuthRateLimit: resetMocks.enforceAuthRateLimit,
}));

import { authRouter } from './auth';

function makeRequest() {
  return new Request('https://worker.test/reset-password/request', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email: 'student@example.com' }),
  });
}

async function requestReset(resendApiKey?: string) {
  return authRouter.fetch(
    makeRequest(),
    {
      DB: {},
      JWT_SECRET: 'unit-test-secret',
      ...(resendApiKey ? { RESEND_API_KEY: resendApiKey } : {}),
    } as unknown as Env,
  );
}

describe('password reset email delivery', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('fetch', vi.fn());
    resetMocks.user = { id: 'user-1' };
    resetMocks.createDb.mockReturnValue({
      select: resetMocks.select,
      insert: resetMocks.insert,
    });
    resetMocks.select.mockImplementation(() => ({
      from: () => ({
        where: () => ({ get: vi.fn(async () => resetMocks.user) }),
      }),
    }));
    resetMocks.insert.mockImplementation(() => ({
      values: resetMocks.insertedValues,
    }));
    resetMocks.insertedValues.mockResolvedValue(undefined);
    resetMocks.enforceAuthRateLimit.mockResolvedValue(null);
  });

  it('keeps the public response generic and logs missing provider configuration safely', async () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => {});

    const response = await requestReset();

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      message: 'If an account exists, reset instructions will be sent',
    });
    expect(fetch).not.toHaveBeenCalled();
    expect(log).toHaveBeenCalledWith(
      '[auth] password reset email delivery failed',
      { reason: 'missing_provider_key' },
    );
    log.mockRestore();
  });

  it.each([
    [
      'non-2xx provider response',
      { ok: false, status: 503 },
      { reason: 'provider_rejected', status: 503 },
    ],
    [
      'rejected provider request',
      new Error('provider request details must not be logged'),
      { reason: 'provider_request_failed' },
    ],
  ])('keeps the response generic and records a sanitized failure for a %s', async (
    _label,
    providerResult,
    expectedLog,
  ) => {
    vi.mocked(fetch).mockImplementationOnce(async () => {
      if (providerResult instanceof Error) throw providerResult;
      return providerResult as Response;
    });
    const log = vi.spyOn(console, 'error').mockImplementation(() => {});

    const response = await requestReset('test-provider-key');

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      message: 'If an account exists, reset instructions will be sent',
    });
    expect(log).toHaveBeenCalledWith(
      '[auth] password reset email delivery failed',
      expectedLog,
    );
    expect(log.mock.calls).toEqual([
      ['[auth] password reset email delivery failed', expectedLog],
    ]);
    log.mockRestore();
  });

  it('uses the same public response when no account matches the submitted email', async () => {
    resetMocks.user = null;
    const response = await requestReset('test-provider-key');

    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      message: 'If an account exists, reset instructions will be sent',
    });
    expect(fetch).not.toHaveBeenCalled();
  });
});