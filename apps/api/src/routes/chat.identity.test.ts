import { describe, expect, it } from 'vitest';

import {
  anonUserId,
  isEdgeRateLimitedRequest,
  trustedEdgeRateLimitUsage,
} from '../services/anonymous';

const COOKIE_SECRET = 'cookie-test-secret-at-least-32-characters';
const COOKIE_ID = 'anon_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';

async function edgeHeaders(id: string, pathname: string): Promise<Record<string, string>> {
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(COOKIE_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = Array.from(new Uint8Array(
    await crypto.subtle.sign('HMAC', key, encoder.encode(`${timestamp}:anonymous:${pathname}`)),
  )).map(byte => byte.toString(16).padStart(2, '0')).join('');
  return {
    'x-anon-id': id,
    'X-User-ID': 'anonymous',
    'X-Edge-Timestamp': timestamp,
    'X-Edge-Signature': signature,
  };
}

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
  it('honors the edge quota marker only with valid edge trust', async () => {
    const pathname = '/api/v1/chat/stream';
    const trusted = new Request(`https://api.syrabit.ai${pathname}`, {
      headers: {
        ...(await edgeHeaders(COOKIE_ID, pathname)),
        'X-Rate-Limited-By': 'edge',
        'X-RateLimit-Limit': '6',
        'X-RateLimit-Remaining': '4',
      },
    });
    const forged = new Request(`https://api.syrabit.ai${pathname}`, {
      headers: { 'X-Rate-Limited-By': 'edge' },
    });

    await expect(isEdgeRateLimitedRequest(trusted, COOKIE_SECRET)).resolves.toBe(true);
    await expect(isEdgeRateLimitedRequest(forged, COOKIE_SECRET)).resolves.toBe(false);
    await expect(trustedEdgeRateLimitUsage(trusted, COOKIE_SECRET)).resolves.toEqual({
      count: 1,
      limit: 6,
    });
  });

  it('rejects a direct unsigned browser ID', async () => {
    const request = new Request('https://api.syrabit.ai/api/v1/chat/stream', {
      headers: {
        'x-anon-id': 'anon_0123456789abcdef0123456789abcdef',
        'CF-Connecting-IP': '203.0.113.9',
      },
    });

    await expect(anonUserId(request, COOKIE_SECRET)).resolves.toBe('ip_203_0_113_9');
  });

  it('accepts a browser ID forwarded by the HMAC-authenticated edge', async () => {
    const pathname = '/api/v1/chat/stream';
    const id = 'anon_0123456789abcdef0123456789abcdef';
    const request = new Request(`https://api.syrabit.ai${pathname}`, {
      headers: {
        ...(await edgeHeaders(id, pathname)),
        'CF-Connecting-IP': '203.0.113.9',
      },
    });

    await expect(anonUserId(request, COOKIE_SECRET)).resolves.toBe(id);
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

  it('prefers the signed cookie over a different forwarded browser ID', async () => {
    const pathname = '/api/v1/chat/stream';
    const forwardedId = 'anon_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
    const request = new Request(`https://api.syrabit.ai${pathname}`, {
      headers: {
        ...(await edgeHeaders(forwardedId, pathname)),
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