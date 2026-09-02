import { describe, expect, it } from 'vitest';

import { paymentsRouter } from './payments';
import { signAccessToken } from '../middleware/auth';
import type { Env } from '../types';

const JWT_SECRET = 'refund-route-test-secret-at-least-32-characters';

function refundDb(ownerId = 'user-a') {
  let requested = false;
  return {
    prepare(query: string) {
      return {
        bind(...values: unknown[]) {
          return {
            async first() {
              if (query.includes('session_valid_after')) return { session_valid_after: 0 };
              if (query.includes('FROM payments')) {
                return values[1] === ownerId ? { id: String(values[0]) } : null;
              }
              throw new Error(`Unexpected D1 lookup: ${query}`);
            },
            async run() {
              if (!query.includes('INSERT OR IGNORE INTO refund_requests')) {
                throw new Error(`Unexpected D1 write: ${query}`);
              }
              if (requested) return { meta: { changes: 0 } };
              requested = true;
              return { meta: { changes: 1 } };
            },
          };
        },
      };
    },
  } as unknown as D1Database;
}

async function requestFor(userId: string, payload: unknown, db: D1Database) {
  const token = await signAccessToken(userId, 'student', JWT_SECRET);
  return paymentsRouter.fetch(new Request('https://api.example/refund-request', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }), { DB: db, JWT_SECRET } as unknown as Env);
}

describe('POST /payments/refund-request', () => {
  it('rejects malformed requests before looking up a payment', async () => {
    const response = await requestFor('user-a', { payment_id: '', reason: 123 }, refundDb());
    expect(response.status).toBe(422);
    await expect(response.json()).resolves.toEqual({ detail: 'A valid payment_id is required' });
  });

  it('does not let a user request a refund for another user’s payment', async () => {
    const response = await requestFor('user-b', { payment_id: 'payment-a' }, refundDb('user-a'));
    expect(response.status).toBe(404);
    await expect(response.json()).resolves.toEqual({ detail: 'Eligible payment not found' });
  });

  it('creates one staff-reviewed request and safely absorbs a duplicate', async () => {
    const db = refundDb();
    const first = await requestFor('user-a', { payment_id: 'payment-a', reason: 'Accidental purchase' }, db);
    const duplicate = await requestFor('user-a', { payment_id: 'payment-a' }, db);

    expect(first.status).toBe(201);
    await expect(first.json()).resolves.toEqual({ status: 'submitted', message: 'Refund request submitted' });
    await expect(duplicate.json()).resolves.toEqual({
      status: 'already_requested',
      message: 'A refund request already exists for this payment',
    });
  });
});