import { describe, expect, it } from 'vitest';

import {
  anonymousNetworkRateLimitIdentity,
  resolveAnonymousIdentity,
} from './rate-limit';

const SECRET = 'edge-anonymous-test-secret-at-least-32-characters';
const BROWSER_ID = 'anon_0123456789abcdef0123456789abcdef';

describe('edge anonymous identity', () => {
  it('ignores a caller-selected ID and mints an HttpOnly identity cookie', async () => {
    const identity = await resolveAnonymousIdentity(new Request('https://syrabit.ai/api/v1/chat/stream', {
      headers: {
        'x-anon-id': BROWSER_ID,
        'CF-Connecting-IP': '203.0.113.9',
      },
    }), SECRET);

    expect(identity.id).toMatch(/^anon_[a-f0-9]{32}$/);
    expect(identity.id).not.toBe(BROWSER_ID);
    expect(identity.setCookie).toContain(`syrabit_anon_id=${identity.id}.`);
    expect(identity.setCookie).toContain('HttpOnly');
    expect(identity.setCookie).toContain('Secure');
  });

  it('prefers the signed cookie when local storage rotates its ID', async () => {
    const first = await resolveAnonymousIdentity(new Request('https://syrabit.ai/api/v1/chat/stream', {
      headers: { 'x-anon-id': BROWSER_ID },
    }), SECRET);
    const cookie = first.setCookie?.split(';')[0] ?? '';
    const second = await resolveAnonymousIdentity(new Request('https://syrabit.ai/api/v1/chat/stream', {
      headers: {
        Cookie: cookie,
        'x-anon-id': 'anon_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
      },
    }), SECRET);

    expect(second.id).toBe(first.id);
    expect(second.setCookie).toBeNull();
  });

  it('derives the abuse bucket only from Cloudflare connection identity', () => {
    const request = new Request('https://syrabit.ai/api/v1/chat/stream', {
      headers: {
        'x-anon-id': BROWSER_ID,
        'CF-Connecting-IP': '203.0.113.9',
      },
    });
    expect(anonymousNetworkRateLimitIdentity(request)).toBe('ip_203_0_113_9');
  });
});