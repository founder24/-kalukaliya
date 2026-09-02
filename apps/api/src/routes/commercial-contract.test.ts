import { afterEach, describe, expect, it, vi } from 'vitest';

import { paymentsRouter } from './payments';
import { subscriptionRouter } from './subscription';
import { signAccessToken } from '../middleware/auth';
import type { Env } from '../types';

const JWT_SECRET = 'commercial-contract-test-secret-at-least-32-characters';

function orderDb(): D1Database {
  return {
    prepare(query: string) {
      return {
        bind() {
          return {
            async first() {
              if (query.includes('session_valid_after')) return { session_valid_after: 0 };
              throw new Error(`Unexpected D1 lookup: ${query}`);
            },
            async run() {
              if (query.includes('payments_pending')) return { meta: { changes: 1 } };
              throw new Error(`Unexpected D1 write: ${query}`);
            },
          };
        },
      };
    },
  } as unknown as D1Database;
}

async function authedRequest(path: string, body: unknown) {
  const token = await signAccessToken('commercial-user', 'student', JWT_SECRET);
  return new Request(`https://api.example${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

const env = (DB: D1Database) => ({
  DB,
  JWT_SECRET,
  RAZORPAY_KEY_ID: 'rzp_test_commercial',
  RAZORPAY_KEY_SECRET: 'commercial-key-secret',
}) as unknown as Env;

function mockRazorpay() {
  const requests: RequestInit[] = [];
  const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    requests.push(init ?? {});
    return new Response(JSON.stringify({ id: 'order_commercial' }), { status: 200 });
  });
  vi.stubGlobal('fetch', fetchMock);
  return { fetchMock, requests };
}

function requestBody(mock: ReturnType<typeof mockRazorpay>) {
  const init = mock.requests[0];
  return JSON.parse(String(init?.body)) as { amount: number; notes: Record<string, string> };
}

afterEach(() => vi.unstubAllGlobals());

describe('customer-facing commercial contract', () => {
  it('publishes the Free, Starter, and Pro daily-credit one-time offers', async () => {
    const response = await subscriptionRouter.fetch(new Request('https://api.example/plans'), {} as Env);
    expect(response.status).toBe(200);
    const body = await response.json() as { plans: Array<Record<string, unknown>> };
    expect(body.plans.map(({ id, price_inr, billing, message_limit }) => ({ id, price_inr, billing, message_limit }))).toEqual([
      { id: 'free', price_inr: 0, billing: 'forever', message_limit: 30 },
      { id: 'starter', price_inr: 99, billing: 'one-time', message_limit: 500 },
      { id: 'pro', price_inr: 999, billing: 'one-time', message_limit: 4000 },
    ]);
  });

  it.each([
    ['starter', 9900],
    ['pro', 99900],
  ])('creates a %s subscription order at %i paise with one-time notes', async (plan, amount) => {
    const razorpay = mockRazorpay();
    const response = await subscriptionRouter.fetch(await authedRequest('/create-order', { plan }), env(orderDb()));
    expect(response.status).toBe(200);
    expect(requestBody(razorpay)).toMatchObject({
      amount,
      notes: { user_id: 'commercial-user', plan, purchase_type: 'one_time_plan' },
    });
  });

  it('requires a selected Starter or Pro plan for subscription orders', async () => {
    const razorpay = mockRazorpay();
    const response = await subscriptionRouter.fetch(await authedRequest('/create-order', { plan: 'free' }), env(orderDb()));
    expect(response.status).toBe(400);
    expect(razorpay.fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ['starter', 9900],
    ['pro', 99900],
  ])('uses the same %s amount through the payments order endpoint', async (plan, amount) => {
    const razorpay = mockRazorpay();
    const response = await paymentsRouter.fetch(await authedRequest('/create-order', { plan }), env(orderDb()));
    expect(response.status).toBe(200);
    expect(requestBody(razorpay)).toMatchObject({
      amount,
      notes: { user_id: 'commercial-user', plan, purchase_type: 'one_time_plan' },
    });
  });

  it.each([
    [100, 4900],
    [500, 19900],
    [1000, 34900],
  ])('accepts the %i-credit top-up at %i paise', async (credits, amount) => {
    const razorpay = mockRazorpay();
    const response = await paymentsRouter.fetch(await authedRequest('/credit-topup', { credits }), env(orderDb()));
    expect(response.status).toBe(200);
    expect(requestBody(razorpay)).toMatchObject({ amount, notes: { credits: String(credits), type: 'credit_topup' } });
  });

  it('rejects the retired 250-credit top-up pack', async () => {
    const razorpay = mockRazorpay();
    const response = await paymentsRouter.fetch(await authedRequest('/credit-topup', { credits: 250 }), env(orderDb()));
    expect(response.status).toBe(400);
    expect(razorpay.fetchMock).not.toHaveBeenCalled();
  });
});