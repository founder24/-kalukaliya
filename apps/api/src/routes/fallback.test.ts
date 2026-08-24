import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Context } from 'hono';
import type { Env } from '../types';
import { proxyToCloudRun } from './fallback';

describe('Cloud Run compatibility fallback', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('converts the internal OIDC header into Cloud Run authorization', async () => {
    let forwardedAuthorization = '';
    let forwardedCookie = '';
    let forwardedInternalToken: string | null = 'unexpected';
    vi.stubGlobal('fetch', vi.fn(async (_url: string, init: RequestInit) => {
      const forwardedHeaders = new Headers(init.headers);
      forwardedAuthorization = forwardedHeaders.get('Authorization') ?? '';
      forwardedCookie = forwardedHeaders.get('Cookie') ?? '';
      forwardedInternalToken = forwardedHeaders.get('X-Cloud-Run-Token');
      return new Response(JSON.stringify({ users: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }));

    const request = new Request('https://api-worker.example.com/api/v1/admin/users?limit=1', {
      headers: {
        Cookie: 'syrabit_admin_session=disposable-session',
        'X-Cloud-Run-Token': 'Bearer cloud-run-id-token',
      },
    });
    const context = {
      env: { BACKEND_URL: 'https://cloud-run.example.com' },
      req: {
        url: request.url,
        raw: request,
        method: request.method,
        header: (name: string) => request.headers.get(name) ?? undefined,
      },
    } as unknown as Context<{ Bindings: Env }>;

    const response = await proxyToCloudRun(context);

    expect(response.status).toBe(200);
    expect(response.headers.get('X-Syrabit-Route')).toBe('cloud-run-fallback');
    expect(forwardedAuthorization).toBe('Bearer cloud-run-id-token');
    expect(forwardedCookie).toBe('syrabit_admin_session=disposable-session');
    expect(forwardedInternalToken).toBeNull();
  });
});