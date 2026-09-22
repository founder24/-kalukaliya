import { describe, expect, it } from 'vitest';
import {
  CONSUMER_REFERRAL_POLICY,
} from '../contracts/consumer-referral-policy';
import {
  currentQuotaMonthPeriod,
  monthResetAt,
  monthlyChatLimit,
} from './consumer-referrals';

describe('consumer referral entitlements', () => {
  it('uses the approved point policy', () => {
    expect(CONSUMER_REFERRAL_POLICY.pointsPerVerifiedVisitor).toBe(1);
    expect(CONSUMER_REFERRAL_POLICY.upgrade.cost).toBe(500);
    expect(CONSUMER_REFERRAL_POLICY.adsFree.cost).toBe(500);
  });

  it('restores 30 monthly chats for free users and raises the limit during upgrade', () => {
    const now = Math.floor(Date.parse('2026-09-22T00:00:00Z') / 1000);
    expect(monthlyChatLimit('free', null, now)).toBe(30);
    expect(monthlyChatLimit('free', now + 60, now)).toBe(100);
    expect(monthlyChatLimit('free', now - 1, now)).toBe(30);
  });

  it('uses UTC month buckets with the next month reset', () => {
    const now = Math.floor(Date.parse('2026-09-22T00:00:00Z') / 1000);
    expect(currentQuotaMonthPeriod(now)).toBe('2026-09');
    expect(new Date(monthResetAt(now) * 1000).toISOString()).toBe('2026-10-01T00:00:00.000Z');
  });
});