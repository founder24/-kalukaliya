import { describe, expect, it } from 'vitest';
import { stagingGateFailure } from './staging-gate';

const stagingEnv = {
  APP_ENV: 'staging',
  STAGING_ACCESS_TOKEN: 'stage-only-test-token-0123456789',
};

describe('staging access gate', () => {
  it('does not gate other environments', () => {
    expect(stagingGateFailure(new Request('https://worker.example/health'), {
      APP_ENV: 'production',
    })).toBeNull();
  });

  it('allows CORS preflight without forwarding it to the API', () => {
    expect(stagingGateFailure(new Request('https://worker.example/api/v1/users/me', {
      method: 'OPTIONS',
    }), stagingEnv)).toBeNull();
  });

  it('fails closed when the staging secret is not configured', async () => {
    const response = stagingGateFailure(
      new Request('https://worker.example/health'),
      { APP_ENV: 'staging' },
    );
    expect(response?.status).toBe(503);
    await expect(response?.json()).resolves.toMatchObject({
      error_code: 'staging_access_unconfigured',
    });
  });

  it('rejects missing and incorrect access tokens', async () => {
    const headersList: Array<Record<string, string>> = [
      {},
      { 'X-Syrabit-Staging-Token': 'wrong-token' },
    ];
    for (const headers of headersList) {
      const response = stagingGateFailure(
        new Request('https://worker.example/health', { headers }),
        stagingEnv,
      );
      expect(response?.status).toBe(401);
      await expect(response?.json()).resolves.toMatchObject({
        error_code: 'staging_access_required',
      });
    }
  });

  it('accepts only the configured staging token', () => {
    const response = stagingGateFailure(
      new Request('https://worker.example/health', {
        headers: { 'X-Syrabit-Staging-Token': stagingEnv.STAGING_ACCESS_TOKEN },
      }),
      stagingEnv,
    );
    expect(response).toBeNull();
  });
});