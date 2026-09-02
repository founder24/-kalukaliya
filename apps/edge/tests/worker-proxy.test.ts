import { describe, expect, it, vi } from 'vitest';
import { proxyToApiWorker } from '../src/routes/worker-proxy';

function createEnv(apiFetch: (request: Request) => Promise<Response>): Env {
  return {
    EDGE_SHARED_SECRET: 'edge-test-secret',
    API_WORKER: { fetch: apiFetch },
  } as unknown as Env;
}

describe('Worker-to-Worker proxy', () => {
  it('preserves user credentials and signs admin requests', async () => {
    const apiFetch = vi.fn(async (request: Request) => {
      expect(request.headers.get('Authorization')).toBe('Bearer user-token');
      expect(request.headers.get('X-User-JWT')).toBe('Bearer user-token');
      expect(request.headers.get('Cookie')).toBe('syrabit_admin_session=disposable-session');
      expect(request.headers.get('X-Edge-Signature')).toBeTruthy();
      return new Response(JSON.stringify({ users: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });

    const response = await proxyToApiWorker(
      new Request('https://syrabit.ai/api/v1/admin/users?limit=1', {
        headers: {
          Cookie: 'syrabit_admin_session=disposable-session',
          Authorization: 'Bearer user-token',
        },
      }),
      createEnv(apiFetch),
    );

    expect(response.status).toBe(200);
    expect(apiFetch).toHaveBeenCalledOnce();
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

  it('returns 504 when the service binding does not respond before its deadline', async () => {
    let serviceRequest: Request | undefined;
    const env = {
      ...createEnv((request) => {
        serviceRequest = request;
        return new Promise(() => undefined);
      }),
      SERVICE_BINDING_TIMEOUT_MS: '1',
    } as Env;

    const responsePromise = proxyToApiWorker(
      new Request('https://syrabit.ai/api/v1/users/me'),
      env,
    );
    const response = await responsePromise;

    expect(response.status).toBe(504);
    expect(serviceRequest?.signal.aborted).toBe(true);
    expect(response.headers.get('X-Request-ID')).toBeTruthy();
    expect(response.headers.get('X-Failure-Stage')).toBe('service_binding');
    await expect(response.json()).resolves.toMatchObject({
      error: 'Backend service timed out',
      error_code: 'service_binding_timeout',
      failure_stage: 'service_binding',
      request_id: expect.any(String),
    });
  });
});