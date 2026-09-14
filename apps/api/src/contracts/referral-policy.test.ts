import { describe, expect, it } from 'vitest';

import {
  REFERRAL_POLICY,
  admissionDisposition,
  advancedQualificationDisposition,
  advancedVacancyDisposition,
  evaluateWeekOpening,
  maturedBeforePause,
  maximumWeeklyExposureInr,
  weeklyRewardInr,
} from './referral-policy';

describe('recurring referral policy contract', () => {
  it('admits exactly 100 approved influencers and waitlists the 101st', () => {
    expect(admissionDisposition({
      currentApprovedSlots: 99,
      eligibilityApproved: true,
      hasAuthoritativeAdmissionPriority: true,
    })).toBe('approved');
    expect(admissionDisposition({
      currentApprovedSlots: 100,
      eligibilityApproved: true,
      hasAuthoritativeAdmissionPriority: true,
    })).toBe('waitlist');
    expect(admissionDisposition({
      currentApprovedSlots: 99,
      eligibilityApproved: false,
      hasAuthoritativeAdmissionPriority: true,
    })).toBe('ineligible');
    expect(admissionDisposition({
      currentApprovedSlots: 99,
      eligibilityApproved: true,
      hasAuthoritativeAdmissionPriority: false,
    })).toBe('waitlist');
  });

  it('keeps the basic curve flat from visitor 101 through qualification', () => {
    expect(weeklyRewardInr('basic', 0)).toBe(0);
    expect(weeklyRewardInr('basic', 1)).toBe(1);
    expect(weeklyRewardInr('basic', 100)).toBe(100);
    expect(weeklyRewardInr('basic', 101)).toBe(100);
    expect(weeklyRewardInr('basic', 499)).toBe(100);
    expect(weeklyRewardInr('basic', 500)).toBe(100);
  });

  it('uses the basic cap in the qualifying week and advanced cap later', () => {
    expect(weeklyRewardInr('advanced', 500, { qualifyingWeek: true })).toBe(100);
    expect(weeklyRewardInr('advanced', 500)).toBe(500);
    expect(weeklyRewardInr('advanced', 1_000)).toBe(1_000);
    expect(weeklyRewardInr('advanced', 1_001)).toBe(1_000);
    expect(REFERRAL_POLICY.advanced.activation).toBe('following-week-after-fraud-review');
  });

  it('orders the first 30 mature 500-visitor claims for provisional review', () => {
    expect(advancedQualificationDisposition(499, 0)).toBe('not-qualified');
    expect(advancedQualificationDisposition(500, 29)).toBe('provisional-for-review');
    expect(advancedQualificationDisposition(500, 30)).toBe('qualified-waitlist');
  });

  it('moves the first qualified waitlist candidate into review when a vacancy opens', () => {
    expect(advancedVacancyDisposition({
      activeAdvancedPositions: 30,
      provisionalPositionsReserved: 0,
      qualifiedInfluencersAhead: 0,
    })).toBe('no-vacancy');
    expect(advancedVacancyDisposition({
      activeAdvancedPositions: 29,
      provisionalPositionsReserved: 0,
      qualifiedInfluencersAhead: 0,
    })).toBe('provisional-for-review');
    expect(advancedVacancyDisposition({
      activeAdvancedPositions: 29,
      provisionalPositionsReserved: 0,
      qualifiedInfluencersAhead: 1,
    })).toBe('qualified-waitlist');
    expect(advancedVacancyDisposition({
      activeAdvancedPositions: 28,
      provisionalPositionsReserved: 0,
      qualifiedInfluencersAhead: 1,
    })).toBe('provisional-for-review');
    expect(advancedVacancyDisposition({
      activeAdvancedPositions: 28,
      provisionalPositionsReserved: 1,
      qualifiedInfluencersAhead: 1,
    })).toBe('qualified-waitlist');
    expect(advancedVacancyDisposition({
      activeAdvancedPositions: 28,
      provisionalPositionsReserved: 2,
      qualifiedInfluencersAhead: 0,
    })).toBe('no-vacancy');
  });

  it('caps fully occupied weekly exposure at ₹37,000', () => {
    expect(maximumWeeklyExposureInr(0)).toBe(10_000);
    expect(maximumWeeklyExposureInr(30)).toBe(37_000);
    expect(maximumWeeklyExposureInr(31)).toBe(37_000);
    expect(REFERRAL_POLICY.maximumWeeklyRewardExposureInr).toBe(37_000);
  });

  it('fails a new week closed on reserve, lifecycle, or stale evidence', () => {
    const now = 2_000_000;
    const healthy = {
      programState: 'active' as const,
      unencumberedReserveInr: 37_000,
      nowEpochSeconds: now,
      revenue: {
        network: 'adsense',
        finalized: true,
        finalizedThroughEpochSeconds: now - 60,
      },
      quality: {
        finalized: true,
        measuredAtEpochSeconds: now - 60,
      },
      controls: {
        attributionHealthy: true,
        deduplicationHealthy: true,
        fraudReviewHealthy: true,
        settlementHealthy: true,
      },
    };

    expect(evaluateWeekOpening(healthy)).toEqual({ allowed: true, reasons: [] });
    expect(evaluateWeekOpening({ ...healthy, unencumberedReserveInr: 36_999 })).toEqual({
      allowed: false,
      reasons: ['reserve-shortfall'],
    });
    expect(evaluateWeekOpening({ ...healthy, unencumberedReserveInr: Number.NaN })).toEqual({
      allowed: false,
      reasons: ['reserve-shortfall'],
    });
    expect(evaluateWeekOpening({
      ...healthy,
      programState: 'paused',
      revenue: {
        ...healthy.revenue,
        finalizedThroughEpochSeconds:
          now - REFERRAL_POLICY.funding.revenueEvidenceMaxAgeSeconds - 1,
      },
    })).toEqual({
      allowed: false,
      reasons: ['program-paused', 'revenue-evidence-stale'],
    });
    expect(evaluateWeekOpening({ ...healthy, programState: 'closed' }).allowed).toBe(false);
    expect(evaluateWeekOpening({
      ...healthy,
      controls: { ...healthy.controls, deduplicationHealthy: false },
    })).toEqual({
      allowed: false,
      reasons: ['deduplication-control-unhealthy'],
    });
  });

  it('requires finalized AdSense and quality evidence, never client estimates', () => {
    const result = evaluateWeekOpening({
      programState: 'active',
      unencumberedReserveInr: 37_000,
      nowEpochSeconds: 100,
      revenue: { network: 'adsterra', finalized: false, finalizedThroughEpochSeconds: 99 },
      quality: null,
      controls: {
        attributionHealthy: true,
        deduplicationHealthy: true,
        fraudReviewHealthy: true,
        settlementHealthy: true,
      },
    });
    expect(result.reasons).toEqual([
      'revenue-network-not-approved',
      'revenue-not-finalized',
      'quality-evidence-missing',
    ]);
    expect(REFERRAL_POLICY.funding.clientBeaconMayAuthorizeRewards).toBe(false);
    expect(REFERRAL_POLICY.funding.projectedOpportunityMayAuthorizeRewards).toBe(false);
  });

  it('uses Asia/Kolkata weeks and a single cross-influencer weekly identity claim', () => {
    expect(REFERRAL_POLICY.timezone).toBe('Asia/Kolkata');
    expect(REFERRAL_POLICY.weekStartsOn).toBe('monday-00:00');
    expect(REFERRAL_POLICY.identity.weeklyCampaignCount).toBe(1);
    expect(REFERRAL_POLICY.identity.repeatWeekClassification)
      .toBe('retention-or-activity-not-new-acquisition');
  });

  it('stops accrual at the authoritative pause timestamp', () => {
    expect(maturedBeforePause(999, 1_000)).toBe(true);
    expect(maturedBeforePause(1_000, 1_000)).toBe(false);
    expect(REFERRAL_POLICY.access.retiredPythonBackendAllowed).toBe(false);
    expect(REFERRAL_POLICY.access.cookieMutationRequiresCsrf).toBe(true);
    expect(REFERRAL_POLICY.access.genericStaffAccessIsInsufficient).toBe(true);
  });
});