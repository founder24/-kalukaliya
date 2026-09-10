import assert from 'node:assert/strict';
import test from 'node:test';
import {
  isExpectedPostLogoutAuthResponse,
  isPostLogoutAuthEndpoint,
} from './staff-portal-response-policy.mjs';

test('accepts only an exact post-logout auth endpoint returning 401', () => {
  assert.equal(
    isExpectedPostLogoutAuthResponse('https://api.syrabit.ai/api/v1/users/me', 401, true),
    true,
  );
  assert.equal(
    isExpectedPostLogoutAuthResponse('https://api.syrabit.ai/api/v1/admin/verify', 401, true),
    true,
  );
  assert.equal(
    isExpectedPostLogoutAuthResponse('https://api.syrabit.ai/api/v1/users/me', 503, true),
    false,
  );
  assert.equal(
    isExpectedPostLogoutAuthResponse('https://api.syrabit.ai/api/v1/users/me-extra', 401, true),
    false,
  );
  assert.equal(
    isExpectedPostLogoutAuthResponse('https://api.syrabit.ai/api/v1/users/me', 401, false),
    false,
  );
});

test('identifies a post-logout auth probe independently of its response status', () => {
  assert.equal(
    isPostLogoutAuthEndpoint('https://api.syrabit.ai/api/v1/users/me', true),
    true,
  );
});