import { describe, expect, it } from 'vitest';

import edgeWorker from './index';
import { isEdgeRateLimitedRequest } from '../../api/src/services/anonymous';

const SECRET = 'edge-quota-contract-secret-at-least-32-characters';

function executionContext(): ExecutionContext {
  return {
    waitUntil() {},
    passThroughOnException() {},
  } as unknown as ExecutionContext;
}

describe('edge to API chat quota contract', () => {
  it('consumes edge quota once and does not reserve API quota again', async () => {
    let networkReservations = 0;
    let studentReservations = 0;
    let apiReservations = 0;

    const rateLimitNamespace = {
      idFromName(name: string) {
        return name;
      },
      get(id: string) {
        return {
          async fetch() {
            if (id.includes('network:')) networkReservations += 1;
            else studentReservations += 1;
            return Response.json({ allowed: true, remaining: 5, resetAt: Date.now() + 60_000 });
          },
        };
      },
    } as unknown as DurableObjectNamespace;

    const apiWorker = {
      async fetch(request: Request) {
        if (!await isEdgeRateLimitedRequest(request, SECRET)) apiReservations += 1;
        return Response.json({ ok: true });
      },
    } as Fetcher;

    const response = await edgeWorker.fetch(
      new Request('https://syrabit.ai/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'CF-Connecting-IP': '203.0.113.9',
          'X-Rate-Limited-By': 'edge',
          'X-Edge-Secret': 'caller-forged',
          'X-Edge-Timestamp': '1',
          'X-Edge-Signature': '0'.repeat(64),
        },
        body: JSON.stringify({ message: 'Explain photosynthesis', lang: 'en' }),
      }),
      {
        API_WORKER: apiWorker,
        RATE_LIMIT_DO: rateLimitNamespace,
        EDGE_SHARED_SECRET: SECRET,
      } as unknown as Env,
      executionContext(),
    );

    expect(response.status).toBe(200);
    expect(networkReservations).toBe(1);
    expect(studentReservations).toBe(1);
    expect(apiReservations).toBe(0);
  });

  it('strips a caller marker and cannot bypass API enforcement when edge quota rejects', async () => {
    let apiCalls = 0;
    const rateLimitNamespace = {
      idFromName(name: string) {
        return name;
      },
      get() {
        return {
          async fetch() {
            return Response.json({ allowed: false, remaining: 0, resetAt: Date.now() + 60_000 });
          },
        };
      },
    } as unknown as DurableObjectNamespace;

    const response = await edgeWorker.fetch(
      new Request('https://syrabit.ai/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'CF-Connecting-IP': '203.0.113.9',
          'X-Rate-Limited-By': 'edge',
        },
        body: JSON.stringify({ message: 'Explain photosynthesis', lang: 'en' }),
      }),
      {
        API_WORKER: { fetch: async () => {
          apiCalls += 1;
          return Response.json({ ok: true });
        } } as unknown as Fetcher,
        RATE_LIMIT_DO: rateLimitNamespace,
        EDGE_SHARED_SECRET: SECRET,
      } as unknown as Env,
      executionContext(),
    );

    expect(response.status).toBe(429);
    expect(apiCalls).toBe(0);
  });
});