import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Env } from '../types';

const authMocks = vi.hoisted(() => {
  const select = vi.fn();
  const insert = vi.fn();
  return {
    createDb: vi.fn(() => ({ select, insert })),
    enforceAuthRateLimit: vi.fn(async () => null),
    select,
    insert,
  };
});

vi.mock('../db/client', () => ({ createDb: authMocks.createDb }));
vi.mock('../services/auth-rate-limit', () => ({
  enforceAuthRateLimit: authMocks.enforceAuthRateLimit,
}));

import { authRouter } from './auth';

describe('signup email validation', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authMocks.enforceAuthRateLimit.mockResolvedValue(null);
  });

  it('rejects malformed email before looking up or inserting a user', async () => {
    const response = await authRouter.fetch(
      new Request('https://worker.test/signup', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          email: 'not-an-email',
          password: 'correct-horse-battery',
        }),
      }),
      { DB: {}, JWT_SECRET: '' } as unknown as Env,
    );

    expect(response.status).toBe(422);
    expect(await response.json()).toEqual({
      detail: 'email must be a valid email address',
    });
    expect(authMocks.select).not.toHaveBeenCalled();
    expect(authMocks.insert).not.toHaveBeenCalled();
  });

  it('rejects a non-string email instead of throwing', async () => {
    const response = await authRouter.fetch(
      new Request('https://worker.test/signup', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          email: 42,
          password: 'correct-horse-battery',
        }),
      }),
      { DB: {}, JWT_SECRET: '' } as unknown as Env,
    );

    expect(response.status).toBe(422);
    expect(authMocks.select).not.toHaveBeenCalled();
    expect(authMocks.insert).not.toHaveBeenCalled();
  });
});