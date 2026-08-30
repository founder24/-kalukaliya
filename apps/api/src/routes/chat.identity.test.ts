import { describe, expect, it } from 'vitest';

import { anonUserId } from '../services/anonymous';

const COOKIE_SECRET = 'cookie-test-secret-at-least-32-characters';
const COOKIE_ID = 'anon_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

async function signedCookie(id: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(COOKIE_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = Array.from(new Uint8Array(
    await crypto.subtle.sign('HMAC', key, encoder.encode(id)),
  )).map(byte => byte.toString(16).padStart(2, '0')).join('');
  return `syrabit_anon_id=${id}.${signature}`;
}

describe('anonymous Worker quota identity', () => {
  it('uses the browser ID instead of a changeable connection IP', async () => {
    const request = new Request('https://api.syrabit.ai/api/v1/chat/stream', {
      headers: {
        'x-anon-id': 'anon_0123456789abcdef0123456789abcdef',
        'CF-Connecting-IP': '203.0.113.9',
      },
    });

    await expect(anonUserId(request)).resolves
      .toBe('anon_0123456789abcdef0123456789abcdef');
  });

  it('falls back to the IP-derived key when the header is malformed', async () => {
    const request = new Request('https://api.syrabit.ai/api/v1/chat/stream', {
      headers: { 'x-anon-id': 'not-valid', 'CF-Connecting-IP': '203.0.113.9' },
    });

    await expect(anonUserId(request)).resolves.toBe('ip_203_0_113_9');
  });

  it('uses a valid signed cookie before the shared connection IP', async () => {
    const request = new Request('https://api.syrabit.ai/api/v1/chat/stream', {
      headers: {
        Cookie: await signedCookie(COOKIE_ID),
        'CF-Connecting-IP': '203.0.113.9',
      },
    });

    await expect(anonUserId(request, COOKIE_SECRET)).resolves.toBe(COOKIE_ID);
  });

  it('rejects a tampered anonymous cookie', async () => {
    const cookie = await signedCookie(COOKIE_ID);
    const request = new Request('https://api.syrabit.ai/api/v1/chat/stream', {
      headers: {
        Cookie: cookie.replace(COOKIE_ID, 'anon_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'),
        'CF-Connecting-IP': '203.0.113.9',
      },
    });

    await expect(anonUserId(request, COOKIE_SECRET)).resolves.toBe('ip_203_0_113_9');
  });
});