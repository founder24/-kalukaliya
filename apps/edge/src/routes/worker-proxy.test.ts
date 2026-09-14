import { describe, expect, it } from 'vitest';

import { proxyToApiWorker } from './worker-proxy';

describe('API Worker trusted origin handoff', () => {
  it('moves the browser Origin into a trusted internal header', async () => {
    let forwarded: Request | null = null;
    const env = {
      EDGE_SHARED_SECRET: 'edge-test-secret-at-least-32-characters',
      API_WORKER: {
        fetch: async (request: Request) => {
          forwarded = request;
          return new Response('{}', {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          });
        },
      },
    } as unknown as Env;
    const response = await proxyToApiWorker(new Request(
      'https://www.syrabit.ai/api/v1/admin/referrals/program/pause',
      {
        method: 'POST',
        headers: {
          Origin: 'https://www.syrabit.ai',
          'X-User-ID': 'staff-user',
        },
        body: '{}',
      },
    ), env);

    expect(response.status).toBe(200);
    expect(forwarded).not.toBeNull();
    expect((forwarded as unknown as Request).headers.get('Origin')).toBeNull();
    expect(
      (forwarded as unknown as Request).headers.get('X-Original-Origin'),
    ).toBe('https://www.syrabit.ai');
    expect(
      (forwarded as unknown as Request).headers.get('X-Edge-Signature'),
    ).toMatch(/^[a-f0-9]{64}$/);
    expect(
      (forwarded as unknown as Request).headers.get('X-Edge-Secret'),
    ).toBe('edge-test-secret-at-least-32-characters');
  });
});