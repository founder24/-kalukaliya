import { describe, it, expect } from 'vitest';
import { formatAuthError } from './authErrors';

const dictDetailError = (code, extras = {}) => ({
  response: {
    data: {
      detail: { code, message: 'backend message', error_codes: ['x'], ...extras },
    },
  },
});

describe('formatAuthError — dict-shaped detail', () => {
  it('falls back to detail.message when code is unknown', () => {
    const err = dictDetailError('something_new', { message: 'Custom backend copy' });
    expect(formatAuthError(err)).toBe('Custom backend copy');
  });

  it('returns fallback when dict detail has no known code and no message', () => {
    const err = {
      response: { data: { detail: { code: 'mystery', error_codes: [] } } },
    };
    expect(formatAuthError(err, 'fallback copy')).toBe('fallback copy');
  });
});

describe('formatAuthError — string detail branch', () => {
  it('returns fallback for unknown snake_case codes', () => {
    const err = { response: { data: { detail: 'unknown_code' } } };
    expect(formatAuthError(err, 'fallback copy')).toBe('fallback copy');
  });

  it('does not treat inherited object properties as friendly error codes', () => {
    const err = { response: { data: { detail: 'toString' } } };
    expect(formatAuthError(err, 'fallback copy')).toBe('toString');
  });

  it('maps the API login failure message to safe user-facing copy', () => {
    const err = { response: { data: { detail: 'Invalid credentials' } } };
    expect(formatAuthError(err)).toBe('Email or password is incorrect.');
  });

  it('returns the string itself when detail is a human sentence', () => {
    const err = { response: { data: { detail: 'Something broke for you.' } } };
    expect(formatAuthError(err)).toBe('Something broke for you.');
  });
});

describe('formatAuthError — array detail branch', () => {
  it('returns the raw string when array entry is unknown', () => {
    const err = { response: { data: { detail: ['nope'] } } };
    expect(formatAuthError(err)).toBe('nope');
  });

  it('returns first.msg when array entry is a Pydantic-style object', () => {
    const err = { response: { data: { detail: [{ msg: 'field required' }] } } };
    expect(formatAuthError(err)).toBe('field required');
  });
});

describe('formatAuthError — defaults', () => {
  it('returns fallback when there is no detail at all', () => {
    expect(formatAuthError({}, 'fallback copy')).toBe('fallback copy');
  });

  it('explains a blocked response and includes a safe request reference', () => {
    const err = {
      response: {
        status: 403,
        headers: { 'x-request-id': 'req-12345678' },
        data: '<html>edge challenge</html>',
      },
    };
    expect(formatAuthError(err)).toBe(
      'This request was blocked. Check your account access or contact support. Reference: req-12345678.',
    );
  });

  it('replaces a generic server detail with retry guidance and its request reference', () => {
    const err = {
      response: {
        status: 500,
        headers: { 'x-request-id': 'req-87654321' },
        data: { detail: 'Internal server error', request_id: 'req-87654321' },
      },
    };
    expect(formatAuthError(err)).toBe(
      'The authentication service is temporarily unavailable. Please try again shortly. Reference: req-87654321.',
    );
  });

  it('explains a network failure without exposing transport internals', () => {
    const err = { code: 'ERR_NETWORK', message: 'Network Error' };
    expect(formatAuthError(err)).toBe(
      'Could not reach the authentication service. Check your connection and try again.',
    );
  });

  it('identifies profile loading failures separately from login failures', () => {
    const err = {
      authStage: 'profile',
      response: { status: 403, headers: { 'x-request-id': 'req-12345678' }, data: '<html></html>' },
    };
    expect(formatAuthError(err)).toBe(
      'Authentication succeeded, but your profile could not be loaded. No session was saved. Please try again. Reference: req-12345678.',
    );
  });

  it('uses a known top-level backend error code', () => {
    const err = { response: { data: { error_code: 'password_reset_required' } } };
    expect(formatAuthError(err)).toBe('Please reset your password before signing in.');
  });
});
