import { describe, expect, it } from 'vitest';

import { webhookRouter } from './webhook';
import type { Env } from '../types';

async function hmacSha256(secret: string, body: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(body));
  return Array.from(new Uint8Array(signature)).map(byte => byte.toString(16).padStart(2, '0')).join('');
}

function eventLedgerDb() {
  const seenEvents = new Set<string>();

  return {
    prepare(query: string) {
      return {
        bind(...values: unknown[]) {
          return {
            async run() {
              if (query.includes('INSERT OR IGNORE INTO webhook_events')) {
                const eventId = values[0] as string;
                const isNew = !seenEvents.has(eventId);
                seenEvents.add(eventId);
                return { meta: { changes: isNew ? 1 : 0 } };
              }
              throw new Error(`Unexpected D1 write: ${query}`);
            },
          };
        },
      };
    },
  };
}

function chargedEventDb() {
  const seenEvents = new Set<string>();
  const state = {
    batches: 0,
    entitlementWrites: 0,
    paymentWrites: 0,
    processedWrites: 0,
    failNextBatch: false,
  };

  const db = {
    prepare(query: string) {
      return {
        bind(...values: unknown[]) {
          return {
            query,
            values,
            async first() {
              if (query.includes('from "users"')) {
                return { id: 'cutover-payment-user', subscription_tier: 'pro' };
              }
              return undefined;
            },
            async raw() {
              if (query.includes('from "users"')) {
                return [['cutover-payment-user', 'pro']];
              }
              return [];
            },
            async run() {
              throw new Error(`Unexpected standalone D1 write: ${query}`);
            },
          };
        },
      };
    },
    async batch(statements: Array<{ query: string; values: unknown[] }>) {
      state.batches += 1;
      if (state.failNextBatch) {
        state.failNextBatch = false;
        throw new Error('Simulated D1 transaction failure');
      }
      const eventId = statements[0]?.values[0] as string;
      const isNew = !seenEvents.has(eventId);
      seenEvents.add(eventId);

      if (isNew) {
        state.entitlementWrites += 1;
        if (statements.some(statement => statement.query.includes('INSERT OR IGNORE INTO payments'))) {
          state.paymentWrites += 1;
        }
        state.processedWrites += 1;
      }

      return statements.map((_, index) => ({
        meta: { changes: index === 0 ? (isNew ? 1 : 0) : (isNew ? 1 : 0) },
      }));
    },
  };

  return { db, state };
}

async function signedWebhook(secret: string, body: string) {
  const signature = await hmacSha256(secret, body);
  return () => new Request('https://api.example/razorpay', {
    method: 'POST',
    headers: { 'X-Razorpay-Signature': signature },
    body,
  });
}

describe('Razorpay webhook event ledger', () => {
  it('accepts a signed delivery once and marks its redelivery as duplicate', async () => {
    const secret = 'webhook-test-secret';
    const body = JSON.stringify({
      event: 'payment.failed',
      id: 'evt_test_payment_failure',
      payload: { payment: { id: 'pay_test_payment_failure' } },
    });
    const request = await signedWebhook(secret, body);
    const env = {
      DB: eventLedgerDb(),
      RAZORPAY_WEBHOOK_SECRET: secret,
    } as unknown as Env;

    const first = await webhookRouter.fetch(request(), env);
    const second = await webhookRouter.fetch(request(), env);

    expect(first.status).toBe(200);
    await expect(first.json()).resolves.toEqual({ status: 'ok' });
    expect(second.status).toBe(200);
    await expect(second.json()).resolves.toEqual({ status: 'ok', duplicate: true });
  });

  it('applies a charged-subscription entitlement only for the first signed delivery', async () => {
    const secret = 'webhook-test-secret';
    const body = JSON.stringify({
      event: 'subscription.charged',
      id: 'evt_test_subscription_charge',
      payload: {
        // Razorpay production deliveries wrap each resource in `entity`.
        subscription: { entity: { id: 'order_test_subscription_charge' } },
        payment: { entity: {
          id: 'pay_test_subscription_charge',
          order_id: 'order_test_subscription_charge',
          amount: 99900,
        } },
      },
    });
    const { db, state } = chargedEventDb();
    const env = {
      DB: db,
      RAZORPAY_WEBHOOK_SECRET: secret,
    } as unknown as Env;
    const request = await signedWebhook(secret, body);

    const first = await webhookRouter.fetch(request(), env);
    const second = await webhookRouter.fetch(request(), env);

    await expect(first.json()).resolves.toEqual({ status: 'ok' });
    await expect(second.json()).resolves.toEqual({ status: 'ok', duplicate: true });
    expect(state).toEqual({
      batches: 2,
      entitlementWrites: 1,
      paymentWrites: 1,
      processedWrites: 1,
      failNextBatch: false,
    });
  });

  const nestedSubscriptionEvents: Array<[string, boolean]> = [
    ['subscription.cancelled', true],
    ['subscription.completed', false],
    ['subscription.expired', true],
  ];

  it.each(nestedSubscriptionEvents)('applies nested %s only once', async (event, nested) => {
    const secret = 'webhook-test-secret';
    const body = JSON.stringify({
      event,
      id: `evt_test_${event.replace('.', '_')}`,
      payload: {
        subscription: nested
          ? { entity: { id: 'order_test_subscription_state' } }
          : { id: 'order_test_subscription_state' },
      },
    });
    const { db, state } = chargedEventDb();
    const env = { DB: db, RAZORPAY_WEBHOOK_SECRET: secret } as unknown as Env;
    const request = await signedWebhook(secret, body);

    const first = await webhookRouter.fetch(request(), env);
    const second = await webhookRouter.fetch(request(), env);

    await expect(first.json()).resolves.toEqual({ status: 'ok' });
    await expect(second.json()).resolves.toEqual({ status: 'ok', duplicate: true });
    expect(state).toMatchObject({
      batches: 2,
      entitlementWrites: 1,
      paymentWrites: 0,
      processedWrites: 1,
    });
  });

  it('allows a charged subscription retry after its D1 batch fails', async () => {
    const secret = 'webhook-test-secret';
    const body = JSON.stringify({
      event: 'subscription.charged',
      id: 'evt_test_subscription_retry',
      payload: {
        subscription: { id: 'order_test_subscription_retry' },
        payment: {
          id: 'pay_test_subscription_retry',
          order_id: 'order_test_subscription_retry',
          amount: 9900,
        },
      },
    });
    const { db, state } = chargedEventDb();
    state.failNextBatch = true;
    const env = { DB: db, RAZORPAY_WEBHOOK_SECRET: secret } as unknown as Env;
    const request = await signedWebhook(secret, body);

    const failed = await webhookRouter.fetch(request(), env);
    const retried = await webhookRouter.fetch(request(), env);

    expect(failed.status).toBe(500);
    await expect(retried.json()).resolves.toEqual({ status: 'ok' });
    expect(state).toMatchObject({
      batches: 2,
      entitlementWrites: 1,
      paymentWrites: 1,
      processedWrites: 1,
      failNextBatch: false,
    });
  });
});