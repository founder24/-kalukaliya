export const CONSUMER_REFERRAL_POLICY = {
  version: '2026-09-22',
  pointsPerVerifiedVisitor: 1,
  upgrade: {
    cost: 500,
    durationSeconds: 30 * 24 * 60 * 60,
    monthlyChatLimit: 100,
  },
  adsFree: {
    cost: 500,
    durationSeconds: 30 * 24 * 60 * 60,
  },
} as const;

export type ConsumerReferralBenefit = 'upgrade' | 'ads_free';