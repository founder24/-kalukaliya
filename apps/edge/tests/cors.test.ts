import { describe, it, expect } from 'vitest';
import { getCorsHeaders, applyCorsHeaders } from '../src/middleware/cors';

describe('getCorsHeaders', () => {
  it('returns correct headers for allowed origin', () => {
    const headers = getCorsHeaders('https://syrabit.ai');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://syrabit.ai');
    expect(headers['Access-Control-Allow-Methods']).toContain('GET');
    expect(headers['Access-Control-Allow-Methods']).toContain('POST');
    expect(headers['Access-Control-Allow-Headers']).toContain('Authorization');
    expect(headers['Access-Control-Max-Age']).toBe('86400');
  });

  it('returns correct headers for second allowed origin', () => {
    const headers = getCorsHeaders('https://app.syrabit.ai');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://app.syrabit.ai');
  });

  it('falls back to default for unknown origin', () => {
    const headers = getCorsHeaders('https://malicious.com');
    expect(headers['Access-Control-Allow-Origin']).toBe('https://syrabit.ai');
  });
});

describe('applyCorsHeaders', () => {
  it('modifies Headers object with CORS headers for valid origin', () => {
    const headers = new Headers();
    applyCorsHeaders(headers, 'https://syrabit.ai');
    expect(headers.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
    expect(headers.get('Access-Control-Allow-Methods')).toContain('GET');
    expect(headers.get('Access-Control-Allow-Headers')).toContain('Authorization');
  });

  it('uses fallback origin when no origin provided', () => {
    const headers = new Headers();
    applyCorsHeaders(headers);
    expect(headers.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
  });

  it('uses fallback origin for invalid origin', () => {
    const headers = new Headers();
    applyCorsHeaders(headers, 'https://evil.com');
    expect(headers.get('Access-Control-Allow-Origin')).toBe('https://syrabit.ai');
  });
});
