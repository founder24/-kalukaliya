import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { proxyToApiWorker } from '../src/routes/worker-proxy';
import { resetTokenCache } from '../src/utils/google-auth';

const SERVICE_ACCOUNT_KEY = JSON.stringify({
  client_email: 'edge-test@project.iam.gserviceaccount.com',
  private_key: '-----BEGIN PRIVATE KEY-----\nMIIBVgIBADANBg==\n-----END PRIVATE KEY-----\n',
});

function createEnv(apiFetch: (request: Request) => Promise<Response>): Env {
  return {
    BACKEND_URL: 'https://cloud-run.example.com',
    GOOGLE_SA_KEY: SERVICE_ACCOUNT_KEY,
    EDGE_SHARED_SECRET: 'edge-test-secret',
    API_WORKER: { fetch: apiFetch },
  } as unknown as Env;
}

describe('Worker-to-Worker fallback authentication', () => {
  beforeEach(() => {
    resetTokenCache();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('passes a freshly minted Cloud Run token to retained admin fallbacks', async () => {
    const apiFetch = vi.fn(async (request: Request) => {
      expect(request.headers.get('X-Cloud-Run-Token')).toBe('Bearer cloud-run-id-token');
      expect(request.headers.get('Cookie')).toBe('syrabit_admin_session=disposable-session');
      return new Response(JSON.stringify({ users: [], total: 0, offset: 0, limit: 1, has_more: false }), {
        status: 200,
        headers: { 'Content-Type': 'application/json', 'X-Syrabit-Route': 'cloud-run-fallback' },
      });
    });

    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ id_token: 'cloud-run-id-token' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )));
    const existingCrypto = globalThis.crypto;
    vi.stubGlobal('crypto', {
      ...existingCrypto,
      subtle: {
        importKey: vi.fn(async () => ({ type: 'private' } as CryptoKey)),
        sign: vi.fn(async () => new ArrayBuffer(256)),
      },
    });

    const response = await proxyToApiWorker(
      new Request('https://syrabit.ai/api/v1/admin/users?limit=1', {
        headers: {
          Cookie: 'syrabit_admin_session=disposable-session',
          // A caller must not be able to choose the Cloud Run credential.
          'X-Cloud-Run-Token': 'Bearer attacker-token',
        },
      }),
      createEnv(apiFetch),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get('X-Cloud-Run-Token')).toBeNull();
    expect(apiFetch).toHaveBeenCalledOnce();
  });

  it('passes a freshly minted Cloud Run token to retained seed fallbacks', async () => {
    const apiFetch = vi.fn(async (request: Request) => {
      expect(request.headers.get('X-Cloud-Run-Token')).toBe('Bearer cloud-run-id-token');
      return new Response('{"status":"ok"}', {
        status: 200,
        headers: { 'Content-Type': 'application/json', 'X-Syrabit-Route': 'cloud-run-fallback' },
      });
    });

    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ id_token: 'cloud-run-id-token' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )));
    const existingCrypto = globalThis.crypto;
    vi.stubGlobal('crypto', {
      ...existingCrypto,
      subtle: {
        importKey: vi.fn(async () => ({ type: 'private' } as CryptoKey)),
        sign: vi.fn(async () => new ArrayBuffer(256)),
      },
    });

    const response = await proxyToApiWorker(
      new Request('https://syrabit.ai/api/v1/seed/legacy-run/status'),
      createEnv(apiFetch),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get('X-Syrabit-Route')).toBe('cloud-run-fallback');
    expect(apiFetch).toHaveBeenCalledOnce();
  });

  it('does not attach fallback credentials to unrelated native API routes', async () => {
    const apiFetch = vi.fn(async (request: Request) => {
      expect(request.headers.get('X-Cloud-Run-Token')).toBeNull();
      return new Response('{}', { status: 200 });
    });
    const tokenExchange = vi.fn();
    vi.stubGlobal('fetch', tokenExchange);

    await proxyToApiWorker(
      new Request('https://syrabit.ai/api/v1/users/me', {
        headers: { 'X-Cloud-Run-Token': 'Bearer attacker-token' },
      }),
      createEnv(apiFetch),
    );

    expect(tokenExchange).not.toHaveBeenCalled();
  });

  it('preserves JSON content type for the staff streams catalogue route', async () => {
    const apiFetch = vi.fn(async () => new Response('[]', {
      status: 200,
      headers: { 'Content-Type': 'application/json', 'X-Syrabit-Route': 'worker-native' },
    }));

    const response = await proxyToApiWorker(
      new Request('https://syrabit.ai/api/v1/staff/content/streams'),
      createEnv(apiFetch),
    );

    expect(response.status).toBe(200);
    expect(response.headers.get('Content-Type')).toContain('application/json');
    expect(response.headers.get('Cache-Control')).not.toBe('no-store');
  });

  it('keeps only the chat stream endpoint on the SSE response path', async () => {
    const apiFetch = vi.fn(async () => new Response('data: ready\n\n', {
      status: 200,
      headers: { 'Content-Type': 'text/plain' },
    }));

    const response = await proxyToApiWorker(
      new Request('https://syrabit.ai/api/v1/chat/stream', { method: 'POST' }),
      createEnv(apiFetch),
    );

    expect(response.headers.get('Content-Type')).toBe('text/event-stream');
    expect(response.headers.get('Cache-Control')).toBe('no-store');
  });
});