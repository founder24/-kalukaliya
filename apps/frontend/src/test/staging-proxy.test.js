import { describe, expect, it } from 'vitest';
import {
  stagingProxyHeaders,
  validateStagingViteEnvironment,
} from '../../vite-staging-proxy.js';

const stagingHost = 'syrabitworker-staging.axomxplain.workers.dev';
const testToken = 'local-test-token-that-is-never-used-outside-unit-tests';

describe('staging Vite proxy safeguards', () => {
  it('adds the gate header only for the exact HTTPS staging Worker origin', () => {
    const env = { STAGING_ACCESS_HOST: stagingHost, STAGING_ACCESS_TOKEN: testToken };

    expect(stagingProxyHeaders(`https://${stagingHost}`, env)).toEqual({
      'X-Syrabit-Staging-Token': testToken,
    });
    expect(stagingProxyHeaders(`https://${stagingHost}/path`, env)).toEqual({});
    expect(stagingProxyHeaders(`http://${stagingHost}`, env)).toEqual({});
    expect(stagingProxyHeaders('https://api.syrabit.ai', env)).toEqual({});
    expect(stagingProxyHeaders('https://another-account.workers.dev', env)).toEqual({});
    expect(stagingProxyHeaders('https://syrabitworker-prod.example-account.workers.dev', env)).toEqual({});
  });

  it('requires a staging-only target and same-origin browser routing in staging E2E mode', () => {
    const env = {
      STAGING_E2E: '1',
      STAGING_ACCESS_HOST: stagingHost,
      STAGING_ACCESS_TOKEN: testToken,
      BACKEND_PROXY_URL: `https://${stagingHost}`,
      VITE_BACKEND_URL: '',
      VITE_CHAT_API_ORIGIN: '',
    };

    expect(() => validateStagingViteEnvironment(env)).not.toThrow();
    expect(() => validateStagingViteEnvironment({
      ...env,
      BACKEND_PROXY_URL: 'https://api.syrabit.ai',
    })).toThrow(/exactly match STAGING_ACCESS_HOST/);
    expect(() => validateStagingViteEnvironment({
      ...env,
      BACKEND_PROXY_URL: `http://${stagingHost}`,
    })).toThrow(/exactly match STAGING_ACCESS_HOST/);
    expect(() => validateStagingViteEnvironment({
      ...env,
      VITE_BACKEND_URL: 'https://api.syrabit.ai',
    })).toThrow(/same-origin browser requests/);
    expect(() => validateStagingViteEnvironment({
      ...env,
      STAGING_ACCESS_TOKEN: '',
    })).toThrow(/STAGING_ACCESS_TOKEN is required/);
  });

  it('does not change normal local proxy behavior when staging E2E is disabled', () => {
    expect(() => validateStagingViteEnvironment({ STAGING_E2E: '0' })).not.toThrow();
    expect(stagingProxyHeaders('https://api.syrabit.ai', {
      STAGING_ACCESS_HOST: stagingHost,
      STAGING_ACCESS_TOKEN: testToken,
    })).toEqual({});
  });
});