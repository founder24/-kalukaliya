import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  getIdentityToken,
  createSignedJwt,
  base64UrlEncode,
  base64UrlEncodeString,
  resetTokenCache,
} from '../src/utils/google-auth';

function createMockEnv(overrides: Partial<Env> = {}): Env {
  return {
    JWT_SECRET: 'test-secret-for-unit-tests-at-least-32-characters',
    EDGE_SHARED_SECRET: 'test-edge-secret',
    BACKEND_URL: 'https://syrabit-backend-851687450401.asia-south1.run.app',
    ALLOWED_ORIGIN: 'https://syrabit.ai',
    R2_BUCKET: { get: vi.fn(async () => null) } as unknown as R2Bucket,
    RATE_LIMIT_KV: {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace,
    ISR_CACHE_KV: {
      get: vi.fn(async () => null),
      put: vi.fn(async () => {}),
      delete: vi.fn(async () => {}),
    } as unknown as KVNamespace,
    ...overrides,
  };
}

describe('Google Auth - getIdentityToken', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
    resetTokenCache();
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it('returns null when GOOGLE_SA_KEY is not set', async () => {
    const env = createMockEnv({ GOOGLE_SA_KEY: undefined });
    const token = await getIdentityToken(env);
    expect(token).toBeNull();
  });

  it('returns null when GOOGLE_SA_KEY is empty string', async () => {
    const env = createMockEnv({ GOOGLE_SA_KEY: '' });
    const token = await getIdentityToken(env);
    expect(token).toBeNull();
  });

  it('fetches and returns an identity token when SA key is configured', async () => {
    const mockIdToken = 'mock-id-token-value';
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ id_token: mockIdToken }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      )
    );

    // Mock crypto.subtle for key import and signing
    const mockSign = vi.fn(async () => new ArrayBuffer(256));
    const mockImportKey = vi.fn(async () => ({ type: 'private' } as CryptoKey));
    vi.stubGlobal('crypto', {
      subtle: {
        importKey: mockImportKey,
        sign: mockSign,
      },
    });

    const saKey = JSON.stringify({
      client_email: 'test@project.iam.gserviceaccount.com',
      private_key: '-----BEGIN PRIVATE KEY-----\nMIIBVgIBADANBg==\n-----END PRIVATE KEY-----\n',
    });

    const env = createMockEnv({ GOOGLE_SA_KEY: saKey });
    const token = await getIdentityToken(env);

    expect(token).toBe(mockIdToken);
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);

    // Verify fetch was called with correct token endpoint
    const fetchCall = vi.mocked(globalThis.fetch).mock.calls[0];
    expect(fetchCall[0]).toBe('https://oauth2.googleapis.com/token');
  });

  it('caches the token and does not re-fetch on second call', async () => {
    const mockIdToken = 'cached-token-value';
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        new Response(JSON.stringify({ id_token: mockIdToken }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      )
    );

    const mockSign = vi.fn(async () => new ArrayBuffer(256));
    const mockImportKey = vi.fn(async () => ({ type: 'private' } as CryptoKey));
    vi.stubGlobal('crypto', {
      subtle: {
        importKey: mockImportKey,
        sign: mockSign,
      },
    });

    const saKey = JSON.stringify({
      client_email: 'test@project.iam.gserviceaccount.com',
      private_key: '-----BEGIN PRIVATE KEY-----\nMIIBVgIBADANBg==\n-----END PRIVATE KEY-----\n',
    });

    const env = createMockEnv({ GOOGLE_SA_KEY: saKey });

    // First call - should fetch
    const token1 = await getIdentityToken(env);
    expect(token1).toBe(mockIdToken);
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);

    // Second call - should use cache
    const token2 = await getIdentityToken(env);
    expect(token2).toBe(mockIdToken);
    expect(globalThis.fetch).toHaveBeenCalledTimes(1); // Still 1, not 2
  });
});

describe('Google Auth - JWT creation', () => {
  beforeEach(() => {
    const mockSign = vi.fn(async () => new ArrayBuffer(64));
    const mockImportKey = vi.fn(async () => ({ type: 'private' } as CryptoKey));
    vi.stubGlobal('crypto', {
      subtle: {
        importKey: mockImportKey,
        sign: mockSign,
      },
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('creates a JWT with correct header and payload structure', async () => {
    const jwt = await createSignedJwt(
      'test@project.iam.gserviceaccount.com',
      '-----BEGIN PRIVATE KEY-----\nMIIBVgIBADANBg==\n-----END PRIVATE KEY-----\n',
      'https://backend.run.app'
    );

    // JWT should have 3 parts
    const parts = jwt.split('.');
    expect(parts).toHaveLength(3);

    // Decode header
    const header = JSON.parse(atob(parts[0].replace(/-/g, '+').replace(/_/g, '/')));
    expect(header.alg).toBe('RS256');
    expect(header.typ).toBe('JWT');

    // Decode payload
    const payload = JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
    expect(payload.iss).toBe('test@project.iam.gserviceaccount.com');
    expect(payload.sub).toBe('test@project.iam.gserviceaccount.com');
    expect(payload.aud).toBe('https://oauth2.googleapis.com/token');
    expect(payload.target_audience).toBe('https://backend.run.app');
    expect(payload.exp - payload.iat).toBe(3600);
  });
});

describe('Google Auth - base64url encoding', () => {
  it('encodes bytes without padding', () => {
    const data = new Uint8Array([72, 101, 108, 108, 111]);
    const encoded = base64UrlEncode(data);
    expect(encoded).not.toContain('=');
    expect(encoded).not.toContain('+');
    expect(encoded).not.toContain('/');
  });

  it('encodes strings correctly', () => {
    const encoded = base64UrlEncodeString('{"alg":"RS256","typ":"JWT"}');
    expect(encoded).not.toContain('=');
    expect(encoded).not.toContain('+');
    expect(encoded).not.toContain('/');
    // Verify it can be decoded
    const decoded = atob(encoded.replace(/-/g, '+').replace(/_/g, '/'));
    expect(decoded).toBe('{"alg":"RS256","typ":"JWT"}');
  });
});
