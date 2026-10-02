import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Env } from '../types';

const authMocks = vi.hoisted(() => {
  const select = vi.fn();
  const insert = vi.fn();
  const insertedValues = vi.fn();
  const hashPassword = vi.fn();
  const signAccessToken = vi.fn();
  const signRefreshToken = vi.fn();
  return {
    createDb: vi.fn(() => ({ select, insert })),
    enforceAuthRateLimit: vi.fn(async () => null),
    select,
    insert,
    insertedValues,
    hashPassword,
    signAccessToken,
    signRefreshToken,
  };
});

vi.mock('../db/client', () => ({ createDb: authMocks.createDb }));
vi.mock('../services/auth-rate-limit', () => ({
  enforceAuthRateLimit: authMocks.enforceAuthRateLimit,
}));
vi.mock('../middleware/auth', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../middleware/auth')>();
  return {
    ...actual,
    hashPassword: authMocks.hashPassword,
    signAccessToken: authMocks.signAccessToken,
    signRefreshToken: authMocks.signRefreshToken,
  };
});

import { authRouter } from './auth';

describe('signup request validation and consent persistence', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    authMocks.enforceAuthRateLimit.mockResolvedValue(null);
    authMocks.select.mockImplementation(() => ({
      from: () => ({
        where: () => ({ get: vi.fn(async () => null) }),
      }),
    }));
    authMocks.insert.mockImplementation(() => ({
      values: authMocks.insertedValues,
    }));
    authMocks.insertedValues.mockResolvedValue(undefined);
    authMocks.hashPassword.mockResolvedValue('hashed-password');
    authMocks.signAccessToken.mockResolvedValue('access-token');
    authMocks.signRefreshToken.mockResolvedValue({ token: 'refresh-token' });
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

  it.each([
    ['omitted', undefined],
    ['false', false],
  ])('rejects signup when data-processing consent is %s', async (_label, consent) => {
    const body: Record<string, unknown> = {
      email: 'student@example.com',
      password: 'correct-horse-battery',
    };
    if (consent !== undefined) body.consent_dpdp = consent;

    const response = await authRouter.fetch(
      new Request('https://worker.test/signup', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      }),
      { DB: {}, JWT_SECRET: 'unit-test-secret' } as unknown as Env,
    );

    expect(response.status).toBe(422);
    expect(await response.json()).toEqual({
      detail: 'Data processing consent is required',
    });
    expect(authMocks.select).not.toHaveBeenCalled();
    expect(authMocks.insert).not.toHaveBeenCalled();
  });

  it('persists affirmative data-processing consent on account creation', async () => {
    const response = await authRouter.fetch(
      new Request('https://worker.test/signup', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          email: 'student@example.com',
          password: 'correct-horse-battery',
          consent_dpdp: true,
        }),
      }),
      { DB: {}, JWT_SECRET: 'unit-test-secret' } as unknown as Env,
    );

    expect(response.status).toBe(201);
    expect(authMocks.insertedValues).toHaveBeenCalledWith(
      expect.objectContaining({ consentDpdp: 1 }),
    );
  });
});