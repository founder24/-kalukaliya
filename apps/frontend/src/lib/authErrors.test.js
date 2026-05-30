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
});
