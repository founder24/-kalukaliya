import { describe, expect, it, vi } from 'vitest';

import { isStagingE2eAccountEmail, usersRouter } from './users';
import type { Env } from '../types';

describe('staging E2E account cleanup', () => {
  it('recognizes only the staging E2E UUID email namespace', () => {
    expect(isStagingE2eAccountEmail(
      'staging-e2e-123e4567-e89b-12d3-a456-426614174000@example.invalid',
    )).toBe(true);
    expect(isStagingE2eAccountEmail(
      'staging-123e4567-e89b-12d3-a456-426614174000@example.invalid',
    )).toBe(false);
    expect(isStagingE2eAccountEmail('student@example.com')).toBe(false);
    expect(isStagingE2eAccountEmail(null)).toBe(false);
  });

  it('is unavailable outside the staging environment before touching D1', async () => {
    const DB = { prepare: vi.fn() } as unknown as D1Database;
    const response = await usersRouter.fetch(
      new Request('https://api.example/staging-e2e-account', { method: 'DELETE' }),
      { APP_ENV: 'production', DB } as unknown as Env,
    );

    expect(response.status).toBe(404);
    expect(DB.prepare).not.toHaveBeenCalled();
  });

  it('requires an authenticated staging session', async () => {
    const DB = { prepare: vi.fn() } as unknown as D1Database;
    const response = await usersRouter.fetch(
      new Request('https://api.example/staging-e2e-account', { method: 'DELETE' }),
      { APP_ENV: 'staging', JWT_SECRET: 'test-only', DB } as unknown as Env,
    );

    expect(response.status).toBe(401);
    expect(DB.prepare).not.toHaveBeenCalled();
  });
});