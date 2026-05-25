import { describe, it, expect } from 'vitest';
import { getCorsHeaders, applyCorsHeaders } from '../src/middleware/cors';

describe('CORS Middleware', () => {
  it('returns correct headers for allowed origin', () => {
    const headers = getCorsHeaders('https://syrabit.ai');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://syrabit.ai');
    expect(headers['Access-Control-Allow-Methods']).toContain('GET');
    expect(headers['Access-Control-Allow-Methods']).toContain('POST');
  });

  it('returns default origin for disallowed origin', () => {
    const headers = getCorsHeaders('https://evil.com');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://syrabit.ai');
  });

  it('allows app.syrabit.ai as valid origin', () => {
    const headers = getCorsHeaders('https://app.syrabit.ai');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://app.syrabit.ai');
  });

  it('applyCorsHeaders sets all headers on Headers object', () => {
    const headers = new Headers();
    applyCorsHeaders(headers, 'https://syrabit.ai');
    expect(headers.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
    expect(headers.get('Access-Control-Allow-Methods')).toContain('GET');
    expect(headers.get('Access-Control-Allow-Headers')).toContain('Authorization');
    expect(headers.get('Access-Control-Max-Age')).toBe('86400');
  });
});
