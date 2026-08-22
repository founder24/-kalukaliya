import { describe, expect, it } from 'vitest';

import { internalRouter } from './internal';
import type { Env } from '../types';

function request(token = ''): Request {
  return new Request('https://api.example/generate', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ system_prompt: 'system', user_message: 'hello' }),
  });
}

describe('internal generation authentication', () => {
  it('fails closed when EDGE_SHARED_SECRET is missing', async () => {
    const response = await internalRouter.fetch(
      request(),
      { EDGE_SHARED_SECRET: '', AI: { run: async () => ({ response: 'nope' }) } } as unknown as Env,
    );

    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({
      detail: 'Generation service is not configured',
    });
  });

  it('rejects a wrong shared secret before invoking Workers AI', async () => {
    const response = await internalRouter.fetch(
      request('wrong-secret'),
      {
        EDGE_SHARED_SECRET: 'expected-secret',
        AI: { run: async () => ({ response: 'must not run' }) },
      } as unknown as Env,
    );

    expect(response.status).toBe(401);
  });
});