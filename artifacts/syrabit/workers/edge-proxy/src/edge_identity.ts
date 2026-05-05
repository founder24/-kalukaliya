export interface ScopeResult {
  scope: string;
  kind: 'do-chat-user' | 'do-chat-ip';
}

function base64UrlDecodeBytes(s: string): Uint8Array {
  const pad = (4 - (s.length % 4)) % 4;
  const b64 = s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat(pad);
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function base64UrlDecodeString(s: string): string {
  const bytes = base64UrlDecodeBytes(s);
  return new TextDecoder().decode(bytes);
}

export async function verifyHS256Jwt(
  token: string,
  secret: string,
): Promise<{ sub: string } | null> {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  const [h, p, s] = parts;
  try {
    const enc = new TextEncoder();
    const key = await crypto.subtle.importKey(
      'raw',
      enc.encode(secret),
      { name: 'HMAC', hash: 'SHA-256' },
      false,
      ['verify'],
    );
    const sigBytes = base64UrlDecodeBytes(s);
    const data = enc.encode(`${h}.${p}`);
    const ok = await crypto.subtle.verify('HMAC', key, sigBytes, data);
    if (!ok) return null;
    const header = JSON.parse(base64UrlDecodeString(h)) as { alg?: string };
    if (header.alg !== 'HS256') return null;
    const payload = JSON.parse(base64UrlDecodeString(p)) as {
      sub?: unknown;
      exp?: unknown;
    };
    if (typeof payload.exp === 'number' && Date.now() / 1000 > payload.exp) {
      return null;
    }
    if (typeof payload.sub !== 'string' || payload.sub.length === 0) {
      return null;
    }
    return { sub: payload.sub.slice(0, 64) };
  } catch (_) {
    return null;
  }
}

export async function deriveLimiterScope(
  request: { headers: { get(name: string): string | null } },
  env: { EDGE_JWT_HS256_SECRET?: string },
): Promise<ScopeResult> {
  const ip = request.headers.get('cf-connecting-ip') || 'anon';
  const ipScope: ScopeResult = { scope: `ip:${ip}`, kind: 'do-chat-ip' };
  const secret = env.EDGE_JWT_HS256_SECRET;
  if (!secret) return ipScope;
  const auth = request.headers.get('authorization') || '';
  const m = auth.match(
    /^Bearer\s+([A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)$/,
  );
  if (!m) return ipScope;
  const claims = await verifyHS256Jwt(m[1], secret);
  if (!claims) return ipScope;
  return { scope: `user:${claims.sub}`, kind: 'do-chat-user' };
}
