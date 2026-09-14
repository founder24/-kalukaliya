export const REFERRAL_POLICY_VERSION = '2026-09-14';

export const REFERRAL_POLICY = {
  version: REFERRAL_POLICY_VERSION,
  controlPlane: 'cloudflare-worker-d1',
  timezone: 'Asia/Kolkata',
  weekStartsOn: 'monday-00:00',
  approvedInfluencerSlots: 100,
  advancedInfluencerSlots: 30,
  basic: {
    rupeesPerMatureVerifiedVisitor: 1,
    weeklyPaidVisitorCap: 100,
    weeklyRewardCapInr: 100,
  },
  advanced: {
    qualificationVisitorsInOneWeek: 500,
    weeklyPaidVisitorCap: 1_000,
    weeklyRewardCapInr: 1_000,
    qualifyingWeekUsesBasicCap: true,
    activation: 'following-week-after-fraud-review',
  },
  maximumWeeklyRewardExposureInr: 37_000,
  identity: {
    weeklyCampaignCount: 1,
    duplicateAttribution: 'first-valid-d1-claim-wins',
    repeatWeekClassification: 'retention-or-activity-not-new-acquisition',
  },
  funding: {
    approvedNetwork: 'adsense',
    revenueEvidenceMaxAgeSeconds: 7 * 24 * 60 * 60,
    qualityEvidenceMaxAgeSeconds: 24 * 60 * 60,
    requireFinalizedProviderRevenue: true,
    clientBeaconMayAuthorizeRewards: false,
    projectedOpportunityMayAuthorizeRewards: false,
  },
  access: {
    policyCapability: 'referral:policy',
    reviewCapability: 'referral:review',
    settlementCapability: 'referral:settle',
    cookieMutationRequiresCsrf: true,
    genericStaffAccessIsInsufficient: true,
    retiredPythonBackendAllowed: false,
  },
} as const;

export type ReferralTier = 'basic' | 'advanced';
export type ReferralProgramState = 'active' | 'paused' | 'closed';
export type AdmissionDisposition = 'approved' | 'waitlist' | 'ineligible';
export type AdvancedQualificationDisposition =
  | 'not-qualified'
  | 'provisional-for-review'
  | 'qualified-waitlist';
export type AdvancedVacancyDisposition =
  | 'no-vacancy'
  | 'provisional-for-review'
  | 'qualified-waitlist';

export interface ReferralEvidenceGate {
  programState: ReferralProgramState;
  unencumberedReserveInr: number;
  nowEpochSeconds: number;
  revenue: {
    network: string;
    finalized: boolean;
    finalizedThroughEpochSeconds: number;
  } | null;
  quality: {
    finalized: boolean;
    measuredAtEpochSeconds: number;
  } | null;
  controls: {
    attributionHealthy: boolean;
    deduplicationHealthy: boolean;
    fraudReviewHealthy: boolean;
    settlementHealthy: boolean;
  };
}

export interface ReferralGateResult {
  allowed: boolean;
  reasons: string[];
}

function nonNegativeInteger(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 0;
  return Math.floor(value);
}

export function admissionDisposition(input: {
  currentApprovedSlots: number;
  eligibilityApproved: boolean;
  hasAuthoritativeAdmissionPriority: boolean;
}): AdmissionDisposition {
  if (!input.eligibilityApproved) return 'ineligible';
  if (
    !input.hasAuthoritativeAdmissionPriority
    || nonNegativeInteger(input.currentApprovedSlots)
      >= REFERRAL_POLICY.approvedInfluencerSlots
  ) return 'waitlist';
  return 'approved';
}

export function weeklyRewardInr(
  tier: ReferralTier,
  matureVerifiedVisitors: number,
  options: { qualifyingWeek?: boolean } = {},
): number {
  const visitors = nonNegativeInteger(matureVerifiedVisitors);
  const useBasicCurve = tier === 'basic' || options.qualifyingWeek === true;
  const cap = useBasicCurve
    ? REFERRAL_POLICY.basic.weeklyPaidVisitorCap
    : REFERRAL_POLICY.advanced.weeklyPaidVisitorCap;
  return Math.min(visitors, cap);
}

export function advancedQualificationDisposition(
  matureVerifiedVisitors: number,
  qualifiedInfluencersAhead: number,
): AdvancedQualificationDisposition {
  if (
    nonNegativeInteger(matureVerifiedVisitors)
      < REFERRAL_POLICY.advanced.qualificationVisitorsInOneWeek
  ) {
    return 'not-qualified';
  }
  return nonNegativeInteger(qualifiedInfluencersAhead) < REFERRAL_POLICY.advancedInfluencerSlots
    ? 'provisional-for-review'
    : 'qualified-waitlist';
}

export function advancedVacancyDisposition(input: {
  activeAdvancedPositions: number;
  provisionalPositionsReserved: number;
  qualifiedInfluencersAhead: number;
}): AdvancedVacancyDisposition {
  const availableUnreservedPositions = Math.max(
    0,
    REFERRAL_POLICY.advancedInfluencerSlots
      - nonNegativeInteger(input.activeAdvancedPositions)
      - nonNegativeInteger(input.provisionalPositionsReserved),
  );
  if (availableUnreservedPositions === 0) return 'no-vacancy';
  return nonNegativeInteger(input.qualifiedInfluencersAhead) < availableUnreservedPositions
    ? 'provisional-for-review'
    : 'qualified-waitlist';
}

function freshAt(
  evidenceEpochSeconds: number,
  nowEpochSeconds: number,
  maximumAgeSeconds: number,
): boolean {
  const age = nowEpochSeconds - evidenceEpochSeconds;
  return Number.isFinite(age) && age >= 0 && age <= maximumAgeSeconds;
}

export function evaluateWeekOpening(
  gate: ReferralEvidenceGate,
  options: { requiredReserveInr?: number } = {},
): ReferralGateResult {
  const reasons: string[] = [];
  const requiredReserveInr = Number.isSafeInteger(options.requiredReserveInr)
    ? Math.max(0, options.requiredReserveInr as number)
    : REFERRAL_POLICY.maximumWeeklyRewardExposureInr;

  if (gate.programState !== 'active') reasons.push(`program-${gate.programState}`);
  if (
    !Number.isFinite(gate.unencumberedReserveInr)
    || gate.unencumberedReserveInr < requiredReserveInr
  ) {
    reasons.push('reserve-shortfall');
  }
  if (!gate.revenue) {
    reasons.push('revenue-evidence-missing');
  } else {
    if (gate.revenue.network !== REFERRAL_POLICY.funding.approvedNetwork) {
      reasons.push('revenue-network-not-approved');
    }
    if (!gate.revenue.finalized) reasons.push('revenue-not-finalized');
    if (!freshAt(
      gate.revenue.finalizedThroughEpochSeconds,
      gate.nowEpochSeconds,
      REFERRAL_POLICY.funding.revenueEvidenceMaxAgeSeconds,
    )) reasons.push('revenue-evidence-stale');
  }
  if (!gate.quality) {
    reasons.push('quality-evidence-missing');
  } else {
    if (!gate.quality.finalized) reasons.push('quality-not-finalized');
    if (!freshAt(
      gate.quality.measuredAtEpochSeconds,
      gate.nowEpochSeconds,
      REFERRAL_POLICY.funding.qualityEvidenceMaxAgeSeconds,
    )) reasons.push('quality-evidence-stale');
  }
  if (!gate.controls.attributionHealthy) reasons.push('attribution-control-unhealthy');
  if (!gate.controls.deduplicationHealthy) reasons.push('deduplication-control-unhealthy');
  if (!gate.controls.fraudReviewHealthy) reasons.push('fraud-review-control-unhealthy');
  if (!gate.controls.settlementHealthy) reasons.push('settlement-control-unhealthy');

  return { allowed: reasons.length === 0, reasons };
}

export function maturedBeforePause(
  maturityEpochSeconds: number,
  pauseEffectiveEpochSeconds: number,
): boolean {
  return maturityEpochSeconds < pauseEffectiveEpochSeconds;
}

export function maximumWeeklyExposureInr(
  advancedPositions: number = REFERRAL_POLICY.advancedInfluencerSlots,
): number {
  const advanced = Math.min(
    nonNegativeInteger(advancedPositions),
    REFERRAL_POLICY.advancedInfluencerSlots,
  );
  const basic = REFERRAL_POLICY.approvedInfluencerSlots - advanced;
  return (
    advanced * REFERRAL_POLICY.advanced.weeklyRewardCapInr
    + basic * REFERRAL_POLICY.basic.weeklyRewardCapInr
  );
}