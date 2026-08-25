import { describe, expect, it } from 'vitest';

import { anonUserId } from '../services/anonymous';

describe('anonymous Worker quota identity', () => {
  it('uses the browser ID instead of a changeable connection IP', () => {
    const request = new Request('https://api.syrabit.ai/api/v1/chat/stream', {
      headers: {
        'x-anon-id': 'anon_0123456789abcdef0123456789abcdef',
        'CF-Connecting-IP': '203.0.113.9',
      },
    });

    expect(anonUserId(request)).toBe('anon_0123456789abcdef0123456789abcdef');
  });

  it('falls back to the IP-derived key when the header is malformed', () => {
    const request = new Request('https://api.syrabit.ai/api/v1/chat/stream', {
      headers: { 'x-anon-id': 'not-valid', 'CF-Connecting-IP': '203.0.113.9' },
    });

    expect(anonUserId(request)).toBe('ip_203_0_113_9');
  });
});