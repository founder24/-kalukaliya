import { describe, it, expect } from 'vitest';
import { getCorsHeaders, applyCorsHeaders } from '../src/middleware/cors';

describe('CORS Middleware', () => {
  it('returns correct headers for allowed origin', () => {
    const headers = getCorsHeaders('https://syrabit.ai');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://syrabit.ai');
    expect(headers['Access-Control-Allow-Methods']).toContain('GET');
    expect(headers['Access-Control-Allow-Methods']).toContain('POST');
  });

  it('returns correct headers for secondary allowed origin', () => {
    const headers = getCorsHeaders('https://app.syrabit.ai');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://app.syrabit.ai');
  });

  it('falls back to default origin for unknown origin', () => {
    const headers = getCorsHeaders('https://evil.com');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://syrabit.ai');
  });

  it('includes required CORS headers', () => {
    const headers = getCorsHeaders('https://syrabit.ai');
    expect(headers['Access-Control-Allow-Headers']).toContain('Authorization');
    expect(headers['Access-Control-Max-Age']).toBe('86400');
  });

  it('applyCorsHeaders sets all headers on a Headers object', () => {
    const h = new Headers();
    applyCorsHeaders(h, 'https://syrabit.ai');
    expect(h.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
    expect(h.get('Access-Control-Allow-Methods')).toContain('GET');
  });

  it('applyCorsHeaders uses default when no origin provided', () => {
    const h = new Headers();
    applyCorsHeaders(h);
    expect(h.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
  });
});
