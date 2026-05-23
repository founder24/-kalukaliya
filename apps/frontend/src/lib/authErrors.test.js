import { describe, it, expect } from 'vitest';
import { formatAuthError } from './authErrors';

// Task #450 — Lock in the friendly Turnstile error mapping added in
// Task #421. The backend (`require_turnstile` in
// `artifacts/syrabit-backend/turnstile.py`) raises HTTPException with
// a dict-shaped detail like:
//   { code: 'turnstile_required', message: '...', error_codes: [...] }
// Axios surfaces this at err.response.data.detail. If a future
// refactor drops the dict branch in formatAuthError, users would
// silently revert to the misleading "check your credentials" message
// these tests guard against.

const dictDetailError = (code, extras = {}) => ({
  response: {
    data: {
      detail: { code, message: 'backend message', error_codes: ['x'], ...extras },
    },
  },
});

describe('formatAuthError — Turnstile dict-shaped detail', () => {
  it('maps turnstile_required to its friendly message', () => {
    expect(formatAuthError(dictDetailError('turnstile_required'))).toBe(
      'Please complete the verification challenge and try again.',
    );
  });

  it('maps turnstile_failed to its friendly message', () => {
    expect(formatAuthError(dictDetailError('turnstile_failed'))).toBe(
      'Verification challenge failed. Please complete the new challenge and try again.',
    );
  });

  it('maps turnstile_misconfigured to its friendly message', () => {
    expect(formatAuthError(dictDetailError('turnstile_misconfigured'))).toBe(
      'Verification is temporarily unavailable. Please try again in a moment.',
    );
  });

  it('maps turnstile_unreachable to its friendly message', () => {
    expect(formatAuthError(dictDetailError('turnstile_unreachable'))).toBe(
      'Could not reach the verification service. Please check your connection and try again.',
    );
  });

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
  it('maps a known code string to its friendly message', () => {
    const err = { response: { data: { detail: 'turnstile_required' } } };
    expect(formatAuthError(err)).toBe(
      'Please complete the verification challenge and try again.',
    );
  });

  it('returns fallback for unknown snake_case codes', () => {
    const err = { response: { data: { detail: 'unknown_code' } } };
    expect(formatAuthError(err, 'fallback copy')).toBe('fallback copy');
  });

  it('returns the string itself when detail is a human sentence', () => {
    const err = { response: { data: { detail: 'Something broke for you.' } } };
    expect(formatAuthError(err)).toBe('Something broke for you.');
  });
});

describe('formatAuthError — array detail branch', () => {
  it('maps a known code from a string array to its friendly message', () => {
    const err = { response: { data: { detail: ['turnstile_failed'] } } };
    expect(formatAuthError(err)).toBe(
      'Verification challenge failed. Please complete the new challenge and try again.',
    );
  });

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
});
