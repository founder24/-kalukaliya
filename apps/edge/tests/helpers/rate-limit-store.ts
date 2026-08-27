import { vi } from 'vitest';

export interface MockRateLimitNamespace {
  namespace: DurableObjectNamespace;
  fetch: ReturnType<typeof vi.fn>;
}

export function createMockRateLimitNamespace(
  initialCount = 0,
  fail = false,
): Promise<MockRateLimitNamespace> {
  const counts = new Map<string, number>();
  const queues = new Map<string, Promise<void>>();

  const fetch = vi.fn(async (bucket: string, request: Request): Promise<Response> => {
    const previous = queues.get(bucket) ?? Promise.resolve();
    let release!: () => void;
    const turn = new Promise<void>((resolve) => { release = resolve; });
    queues.set(bucket, previous.then(() => turn));
    await previous;

    try {
      if (fail) throw new Error('durable object unavailable');
      const command = await request.json() as { limit: number; resetAt: number };
      const count = counts.has(bucket) ? counts.get(bucket)! : initialCount;
      if (count >= command.limit) {
        return Response.json({ allowed: false, remaining: 0, resetAt: command.resetAt });
      }
      const next = count + 1;
      counts.set(bucket, next);
      return Response.json({
        allowed: true,
        remaining: command.limit - next,
        resetAt: command.resetAt,
      });
    } finally {
      release();
    }
  });

  const namespace = {
    idFromName: (name: string) => name,
    get: (id: string) => ({
      fetch: (input: RequestInfo | URL, init?: RequestInit) => {
        const request = input instanceof Request ? input : new Request(input, init);
        return fetch(id, request);
      },
    }),
  } as unknown as DurableObjectNamespace;

  return { namespace, fetch };
}