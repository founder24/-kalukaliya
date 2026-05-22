// Edge-proxy DO routing — pin the regex that dispatches /do/chat-session
// requests so the /typing sub-path reaches the ChatSession DO instead
// of returning 404. The regex MUST stay in lock-step with the one in
// `workers/edge-proxy/src/index.ts`.
import { describe, it, expect } from 'vitest';

const CHAT_SESSION_RE = /^\/do\/chat-session\/([^/]+)(?:\/[^/]+)?$/;
// Mirror of `chatPathRe` in workers/edge-proxy/src/index.ts. The
// rate-limit gate MUST match every path the backend serves chat on.
const CHAT_INGRESS_RE = /^\/(?:api\/)?(?:ai\/|ai_)?chat(?:\b|\/)/i;

describe('edge-proxy DO routing', () => {
  it('matches the bare session path', () => {
    const m = '/do/chat-session/abc123'.match(CHAT_SESSION_RE);
    expect(m).not.toBeNull();
    expect(m[1]).toBe('abc123');
  });

  it('matches the /typing sub-path so DO receives the request', () => {
    const m = '/do/chat-session/abc123/typing'.match(CHAT_SESSION_RE);
    expect(m).not.toBeNull();
    expect(m[1]).toBe('abc123');
  });

  it('does not match deeper sub-paths (only one extra segment)', () => {
    expect('/do/chat-session/abc/typing/extra'.match(CHAT_SESSION_RE)).toBeNull();
  });

  it('does not match unrelated DO routes', () => {
    expect('/do/rate-limiter/check'.match(CHAT_SESSION_RE)).toBeNull();
  });
});

describe('edge-proxy chat-ingress rate-limit gate', () => {
  for (const path of [
    '/chat',
    '/chat/new',
    '/api/chat',
    '/api/chat/stream',
    '/ai_chat',
    '/api/ai_chat',
    '/ai/chat',
    '/api/ai/chat',
    '/api/ai/chat/stream',
  ]) {
    it(`matches ${path}`, () => {
      expect(CHAT_INGRESS_RE.test(path)).toBe(true);
    });
  }

  for (const path of ['/api/health', '/auth/login', '/api/seo/html/homepage']) {
    it(`does not match ${path}`, () => {
      expect(CHAT_INGRESS_RE.test(path)).toBe(false);
    });
  }
});

// Edge limiter identity must be derived only from a cryptographically
// verified Bearer JWT — a forged or unsigned token must NOT be able to
// partition or evade the bucket. These tests exercise deriveLimiterScope
// end-to-end with a real HMAC signature.
import { deriveLimiterScope, verifyHS256Jwt } from '../../workers/edge-proxy/src/edge_identity.ts';

const SECRET = 'test-edge-secret-32bytes-min--xxxx';

function b64u(buf) {
  return Buffer.from(buf).toString('base64')
    .replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_');
}

async function signHS256(payload, secret = SECRET) {
  const header = { alg: 'HS256', typ: 'JWT' };
  const enc = new TextEncoder();
  const headerB64 = b64u(JSON.stringify(header));
  const payloadB64 = b64u(JSON.stringify(payload));
  const data = `${headerB64}.${payloadB64}`;
  const key = await crypto.subtle.importKey(
    'raw', enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
  );
  const sig = await crypto.subtle.sign('HMAC', key, enc.encode(data));
  return `${data}.${b64u(new Uint8Array(sig))}`;
}

function fakeRequest(headers) {
  return { headers: { get: (n) => headers[n.toLowerCase()] ?? null } };
}

describe('edge-proxy rate-limit identity (verified-JWT only)', () => {
  it('falls back to IP scope when no Bearer token is present', async () => {
    const r = await deriveLimiterScope(
      fakeRequest({ 'cf-connecting-ip': '203.0.113.7' }),
      { EDGE_JWT_HS256_SECRET: SECRET },
    );
    expect(r).toEqual({ scope: 'ip:203.0.113.7', kind: 'do-chat-ip' });
  });

  it('falls back to IP scope when EDGE_JWT_HS256_SECRET is unset', async () => {
    const tok = await signHS256({ sub: 'user-42' });
    const r = await deriveLimiterScope(
      fakeRequest({
        'cf-connecting-ip': '203.0.113.7',
        authorization: `Bearer ${tok}`,
      }),
      {},
    );
    expect(r.kind).toBe('do-chat-ip');
    expect(r.scope).toBe('ip:203.0.113.7');
  });

  it('falls back to IP scope when the JWT signature is forged', async () => {
    const tok = await signHS256({ sub: 'user-42' }, 'a-different-secret');
    const r = await deriveLimiterScope(
      fakeRequest({
        'cf-connecting-ip': '203.0.113.7',
        authorization: `Bearer ${tok}`,
      }),
      { EDGE_JWT_HS256_SECRET: SECRET },
    );
    expect(r.kind).toBe('do-chat-ip');
    expect(r.scope).toBe('ip:203.0.113.7');
  });

  it('falls back to IP scope when the token is structurally malformed', async () => {
    const r = await deriveLimiterScope(
      fakeRequest({
        'cf-connecting-ip': '203.0.113.7',
        authorization: 'Bearer not.a.token',
      }),
      { EDGE_JWT_HS256_SECRET: SECRET },
    );
    expect(r.kind).toBe('do-chat-ip');
  });

  it('falls back to IP scope when the JWT is expired', async () => {
    const tok = await signHS256({ sub: 'user-42', exp: 1 });
    const r = await deriveLimiterScope(
      fakeRequest({
        'cf-connecting-ip': '203.0.113.7',
        authorization: `Bearer ${tok}`,
      }),
      { EDGE_JWT_HS256_SECRET: SECRET },
    );
    expect(r.kind).toBe('do-chat-ip');
  });

  it('returns a user-scoped key only for a properly-signed unexpired JWT', async () => {
    const tok = await signHS256({
      sub: 'user-42',
      exp: Math.floor(Date.now() / 1000) + 3600,
    });
    const r = await deriveLimiterScope(
      fakeRequest({
        'cf-connecting-ip': '203.0.113.7',
        authorization: `Bearer ${tok}`,
      }),
      { EDGE_JWT_HS256_SECRET: SECRET },
    );
    expect(r).toEqual({ scope: 'user:user-42', kind: 'do-chat-user' });
  });

  it('does not honour an X-User-Id header', async () => {
    const r = await deriveLimiterScope(
      fakeRequest({
        'cf-connecting-ip': '203.0.113.7',
        'x-user-id': 'attacker',
      }),
      { EDGE_JWT_HS256_SECRET: SECRET },
    );
    expect(r.scope).toBe('ip:203.0.113.7');
  });

  it('verifyHS256Jwt rejects RS256-claimed tokens', async () => {
    const enc = new TextEncoder();
    const header = b64u(JSON.stringify({ alg: 'RS256', typ: 'JWT' }));
    const payload = b64u(JSON.stringify({ sub: 'x' }));
    const key = await crypto.subtle.importKey(
      'raw', enc.encode(SECRET),
      { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'],
    );
    const sig = await crypto.subtle.sign(
      'HMAC', key, enc.encode(`${header}.${payload}`),
    );
    const tok = `${header}.${payload}.${b64u(new Uint8Array(sig))}`;
    const out = await verifyHS256Jwt(tok, SECRET);
    expect(out).toBeNull();
  });
});
