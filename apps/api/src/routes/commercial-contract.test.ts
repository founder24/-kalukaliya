import { describe, expect, it } from 'vitest';

import { api } from './index';
import type { Env } from '../types';

describe('retired commercial API boundary', () => {
  it.each([
    '/api/v1/subscription/plans',
    '/api/v1/subscription/status',
    '/api/v1/payments/create-order',
    '/api/v1/payments/verify',
    '/api/v1/payments/credit-topup',
    '/api/v1/payments/credit-topup/verify',
    '/api/v1/payments/recover',
    '/api/v1/payments/refund-request',
    '/api/v1/payments/history',
    '/api/webhooks/razorpay',
  ])('returns Gone without invoking a legacy commercial handler: %s', async (path) => {
    const response = await api.fetch(new Request(`https://api.example${path}`, {
      method: 'POST',
      body: '{}',
    }), {} as Env);
    expect(response.status).toBe(410);
    await expect(response.json()).resolves.toEqual({ detail: 'Not found' });
  });
});