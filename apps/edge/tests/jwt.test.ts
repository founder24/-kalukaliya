import { describe, it, expect } from 'vitest';
import { verifyJWT } from '../src/middleware/jwt';

const TEST_SECRET = 'test-secret-for-unit-tests-at-least-32-characters';

// Helper to create a valid HS256 JWT using Web Crypto
async function createTestJWT(
  payload: Record<string, unknown>,
  secret: string
): Promise<string> {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
  const body = btoa(JSON.stringify(payload))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');

  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const sig = await crypto.subtle.sign(
    'HMAC',
    key,
    encoder.encode(`${header}.${body}`)
  );
  const sigB64 = btoa(String.fromCharCode(...new Uint8Array(sig)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');

  return `${header}.${body}.${sigB64}`;
}

describe('JWT Middleware', () => {
  it('allows public paths without auth', async () => {
    const request = new Request('https://edge.syrabit.ai/health');
    const result = await verifyJWT(request, TEST_SECRET);
    expect(result.valid).toBe(true);
    expect(result.userId).toBe('anonymous');
  });

  it('allows auth signup without auth', async () => {
    const request = new Request('https://edge.syrabit.ai/api/v1/auth/signup');
    const result = await verifyJWT(request, TEST_SECRET);
    expect(result.valid).toBe(true);
  });

  it('rejects missing Authorization header on protected paths', async () => {
    const request = new Request('https://edge.syrabit.ai/api/v1/users/me');
    const result = await verifyJWT(request, TEST_SECRET);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('Missing');
  });

  it('allows anonymous access to optional-auth chat path', async () => {
    const request = new Request('https://edge.syrabit.ai/api/v1/chat/', {
      method: 'POST',
    });
    const result = await verifyJWT(request, TEST_SECRET);
    expect(result.valid).toBe(true);
    expect(result.userId).toBe('anonymous');
  });

  it('rejects expired tokens', async () => {
    const token = await createTestJWT(
      { sub: 'user-123', type: 'access', exp: Math.floor(Date.now() / 1000) - 3600 },
      TEST_SECRET
    );
    const request = new Request('https://edge.syrabit.ai/api/v1/users/me', {
      headers: { Authorization: `Bearer ${token}` },
    });
    const result = await verifyJWT(request, TEST_SECRET);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('expired');
  });

  it('rejects refresh tokens used as access tokens', async () => {
    const token = await createTestJWT(
      { sub: 'user-123', type: 'refresh', exp: Math.floor(Date.now() / 1000) + 3600 },
      TEST_SECRET
    );
    const request = new Request('https://edge.syrabit.ai/api/v1/users/me', {
      headers: { Authorization: `Bearer ${token}` },
    });
    const result = await verifyJWT(request, TEST_SECRET);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('Invalid token type');
  });

  it('accepts valid access tokens', async () => {
    const token = await createTestJWT(
      { sub: 'user-123', type: 'access', exp: Math.floor(Date.now() / 1000) + 3600 },
      TEST_SECRET
    );
    const request = new Request('https://edge.syrabit.ai/api/v1/users/me', {
      headers: { Authorization: `Bearer ${token}` },
    });
    const result = await verifyJWT(request, TEST_SECRET);
    expect(result.valid).toBe(true);
    expect(result.userId).toBe('user-123');
  });

  it('rejects tokens signed with wrong secret', async () => {
    const token = await createTestJWT(
      { sub: 'user-123', type: 'access', exp: Math.floor(Date.now() / 1000) + 3600 },
      'wrong-secret-that-differs-from-expected-one'
    );
    const request = new Request('https://edge.syrabit.ai/api/v1/users/me', {
      headers: { Authorization: `Bearer ${token}` },
    });
    const result = await verifyJWT(request, TEST_SECRET);
    expect(result.valid).toBe(false);
    expect(result.error).toContain('Invalid signature');
  });
});
