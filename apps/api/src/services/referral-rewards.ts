import {
  REFERRAL_POLICY,
  REFERRAL_POLICY_VERSION,
} from '../contracts/referral-policy';

const KOLKATA_OFFSET_SECONDS = 5 * 60 * 60 + 30 * 60;
const DAY_SECONDS = 24 * 60 * 60;

export type MonthlyReservationKind = 'base' | 'bonus' | null;

export interface MonthlyReservation {
  allowed: boolean;
  period: string;
  kind: MonthlyReservationKind;
  baseUsed: number;
  bonusUsed: number;
  baseLimit: number;
  bonusLimit: number;
}

export interface ReferralRewardStatus {
  adFree: {
    active: boolean;
    expiresAt: number | null;
  };
  monthly: {
    period: string;
    baseLimit: number;
    baseUsed: number;
    baseRemaining: number;
    bonusGranted: number;
    bonusUsed: number;
    bonusRemaining: number;
  };
}

function monthlyPeriodBounds(epochSeconds: number): {
  period: string;
  endsAt: number;
} {
  const shifted = new Date((epochSeconds + KOLKATA_OFFSET_SECONDS) * 1000);
  const period = shifted.toISOString().slice(0, 7);
  shifted.setUTCDate(1);
  shifted.setUTCMonth(shifted.getUTCMonth() + 1);
  return {
    period,
    endsAt: Math.floor(shifted.getTime() / 1000) - KOLKATA_OFFSET_SECONDS,
  };
}

function rewardPolicyConfigured(): boolean {
  const policy = REFERRAL_POLICY.accessRewards;
  return Number.isSafeInteger(policy.promoterMatureVerifiedThreshold)
    && policy.promoterMatureVerifiedThreshold > 0
    && Number.isSafeInteger(policy.adFreeDurationSeconds)
    && policy.adFreeDurationSeconds > 0
    && Number.isSafeInteger(policy.monthlyBaseCredits)
    && policy.monthlyBaseCredits > 0
    && Number.isSafeInteger(policy.signupBonusCredits)
    && policy.signupBonusCredits > 0
    && Number.isSafeInteger(policy.globalMonthlyCreditCap)
    && policy.globalMonthlyCreditCap >= policy.signupBonusCredits
    && Number.isSafeInteger(policy.perPromoterMonthlyCreditCap)
    && policy.perPromoterMonthlyCreditCap >= policy.signupBonusCredits;
}

async function activeGate(
  db: D1Database,
  now: number,
): Promise<{ weekId: string; evidenceId: string } | null> {
  if (!rewardPolicyConfigured()) return null;
  const row = await db.prepare(`
    SELECT w.id AS week_id, e.id AS evidence_id
    FROM referral_program_state p
    JOIN referral_weeks w
      ON w.state = 'open' AND w.starts_at <= ? AND w.ends_at > ?
    JOIN referral_gate_evidence e
      ON e.recorded_at <= ? AND e.expires_at > ?
     AND e.policy_version = ?
     AND e.gross_reserve_inr - e.outstanding_obligations_inr >= ?
     AND e.finalized_through_at >= ?
     AND e.quality_measured_at >= ?
     AND e.attribution_healthy = 1
     AND e.deduplication_healthy = 1
     AND e.fraud_review_healthy = 1
     AND e.settlement_healthy = 1
    WHERE p.id = 'singleton'
      AND p.state = 'active'
      AND w.policy_version = ?
      AND (w.pause_effective_at IS NULL OR ? < w.pause_effective_at
           OR (w.resumed_at > w.pause_effective_at AND ? >= w.resumed_at))
      AND (p.pause_effective_at IS NULL OR ? < p.pause_effective_at
           OR (p.resumed_at > p.pause_effective_at AND ? >= p.resumed_at))
    ORDER BY e.recorded_at DESC
    LIMIT 1
  `).bind(
    now,
    now,
    now,
    now,
    REFERRAL_POLICY_VERSION,
    REFERRAL_POLICY.maximumWeeklyRewardExposureInr,
    now - REFERRAL_POLICY.funding.revenueEvidenceMaxAgeSeconds,
    now - REFERRAL_POLICY.funding.qualityEvidenceMaxAgeSeconds,
    REFERRAL_POLICY_VERSION,
    now,
    now,
    now,
    now,
  ).first<{ week_id: string; evidence_id: string }>();
  return row
    ? { weekId: row.week_id, evidenceId: row.evidence_id }
    : null;
}

export async function issuePromoterAdFreeReward(
  db: D1Database,
  promoterUserId: string,
  now = Math.floor(Date.now() / 1000),
): Promise<'issued' | 'already-issued' | 'not-eligible' | 'gate-closed'> {
  const gate = await activeGate(db, now);
  if (!gate) return 'gate-closed';

  const eligible = await db.prepare(`
    SELECT p.week_id, p.influencer_slot, i.user_id
    FROM referral_weekly_progress p
    JOIN referral_influencer_slots i ON i.slot_no = p.influencer_slot
    JOIN users u ON u.id = i.user_id AND u.deleted_at IS NULL
    WHERE p.week_id = ?
      AND i.user_id = ?
      AND i.status = 'active'
      AND p.policy_version = ?
      AND p.mature_verified_count >= ?
      AND p.reward_eligible_count >= ?
    LIMIT 1
  `).bind(
    gate.weekId,
    promoterUserId,
    REFERRAL_POLICY_VERSION,
    REFERRAL_POLICY.accessRewards.promoterMatureVerifiedThreshold,
    REFERRAL_POLICY.accessRewards.promoterMatureVerifiedThreshold,
  ).first<{ week_id: string; influencer_slot: number; user_id: string }>();
  if (!eligible) return 'not-eligible';

  const activeEntitlement = await db.prepare(`
    SELECT 1 AS active
    FROM referral_access_entitlements
    WHERE user_id = ? AND status = 'active' AND expires_at > ?
    LIMIT 1
  `).bind(promoterUserId, now).first<{ active: number }>();
  if (activeEntitlement) return 'already-issued';

  const claimId = crypto.randomUUID();
  const startsAt = now;
  const expiresAt = now + REFERRAL_POLICY.accessRewards.adFreeDurationSeconds;
  const idempotencyKey = `ad-free:${promoterUserId}:${eligible.week_id}`;
  const claim = await db.prepare(`
    INSERT OR IGNORE INTO referral_reward_claims
      (id, reward_type, promoter_user_id, promoter_slot, week_id, status,
       units, starts_at, expires_at, idempotency_key, policy_version,
       evidence_json, reason, created_at, updated_at)
    SELECT ?, 'ad_free', ?, ?, ?, 'active', 0, ?, ?, ?, ?, ?, ?, ?, ?
    WHERE NOT EXISTS (
      SELECT 1 FROM referral_access_entitlements
      WHERE user_id = ? AND status = 'active' AND expires_at > ?
    )
  `).bind(
    claimId,
    promoterUserId,
    eligible.influencer_slot,
    eligible.week_id,
    startsAt,
    expiresAt,
    idempotencyKey,
    REFERRAL_POLICY_VERSION,
    JSON.stringify({
      gate_evidence_id: gate.evidenceId,
      mature_verified_threshold: REFERRAL_POLICY.accessRewards.promoterMatureVerifiedThreshold,
    }),
    'mature-fraud-reviewed-weekly-threshold',
    now,
    now,
    promoterUserId,
    now,
  ).run();
  if ((claim.meta.changes ?? 0) === 0) return 'already-issued';

  const entitlement = await db.prepare(`
    INSERT OR IGNORE INTO referral_access_entitlements
      (id, user_id, reward_claim_id, status, starts_at, expires_at,
       policy_version, created_at, updated_at)
    VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?)
  `).bind(
    crypto.randomUUID(),
    promoterUserId,
    claimId,
    startsAt,
    expiresAt,
    REFERRAL_POLICY_VERSION,
    now,
    now,
  ).run();
  if ((entitlement.meta.changes ?? 0) === 0) {
    await db.prepare(`
      UPDATE referral_reward_claims
      SET status = 'rejected', reason = 'active-entitlement-race', updated_at = ?
      WHERE id = ? AND status = 'active'
    `).bind(now, claimId).run();
    return 'already-issued';
  }
  return 'issued';
}

export async function issueReferralSignupReward(
  db: D1Database,
  referredAccountId: string,
  now = Math.floor(Date.now() / 1000),
): Promise<'issued' | 'already-issued' | 'not-eligible' | 'gate-closed'> {
  const gate = await activeGate(db, now);
  if (!gate) return 'gate-closed';
  const { period, endsAt } = monthlyPeriodBounds(now);

  const candidate = await db.prepare(`
    SELECT c.id AS claim_id, c.week_id, c.credited_influencer_slot,
           c.account_id, c.state, c.identity_confidence,
           i.user_id AS promoter_user_id,
           u.email, u.onboarding_done, u.deleted_at
    FROM referral_weekly_claims c
    JOIN referral_influencer_slots i ON i.slot_no = c.credited_influencer_slot
    JOIN users u ON u.id = c.account_id
    WHERE c.account_id = ?
      AND c.state = 'mature'
      AND c.identity_confidence = 'account'
      AND c.maturity_token IS NOT NULL
      AND c.quality_evidence_id IS NOT NULL
      AND c.account_id != i.user_id
      AND i.status = 'active'
      AND c.week_id = ?
      AND u.deleted_at IS NULL
      AND u.email IS NOT NULL AND length(trim(u.email)) > 3
      AND u.onboarding_done = 1
    ORDER BY c.matured_at DESC
    LIMIT 1
  `).bind(referredAccountId, gate.weekId).first<{
    claim_id: string;
    week_id: string;
    credited_influencer_slot: number;
    account_id: string;
    promoter_user_id: string;
    email: string;
    onboarding_done: number;
    deleted_at: number | null;
  }>();
  if (!candidate) {
    const existing = await db.prepare(`
      SELECT 1 AS present FROM referral_reward_claims
      WHERE reward_type = 'signup_bonus' AND referred_account_id = ?
      LIMIT 1
    `).bind(referredAccountId).first<{ present: number }>();
    return existing ? 'already-issued' : 'not-eligible';
  }

  const global = await db.prepare(`
    SELECT COALESCE(SUM(units), 0) AS units
    FROM referral_monthly_credit_grants
    WHERE period = ? AND status = 'active'
  `).bind(period).first<{ units: number }>();
  const promoter = await db.prepare(`
    SELECT COALESCE(SUM(g.units), 0) AS units
    FROM referral_monthly_credit_grants g
    JOIN referral_reward_claims c ON c.id = g.reward_claim_id
    WHERE g.period = ? AND g.status = 'active' AND c.promoter_user_id = ?
  `).bind(period, candidate.promoter_user_id).first<{ units: number }>();
  if (
    (global?.units ?? 0) + REFERRAL_POLICY.accessRewards.signupBonusCredits
      > REFERRAL_POLICY.accessRewards.globalMonthlyCreditCap
    || (promoter?.units ?? 0) + REFERRAL_POLICY.accessRewards.signupBonusCredits
      > REFERRAL_POLICY.accessRewards.perPromoterMonthlyCreditCap
  ) {
    return 'not-eligible';
  }

  const claimId = crypto.randomUUID();
  const nowClaim = await db.prepare(`
    INSERT OR IGNORE INTO referral_reward_claims
      (id, reward_type, promoter_user_id, promoter_slot, week_id,
       referred_account_id, period, status, units, starts_at, expires_at,
       idempotency_key, policy_version, evidence_json, reason, created_at, updated_at)
    SELECT ?, 'signup_bonus', ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?
    WHERE NOT EXISTS (
      SELECT 1 FROM referral_reward_claims
      WHERE reward_type = 'signup_bonus' AND referred_account_id = ?
    )
  `).bind(
    claimId,
    candidate.promoter_user_id,
    candidate.credited_influencer_slot,
    candidate.week_id,
    referredAccountId,
    period,
    REFERRAL_POLICY.accessRewards.signupBonusCredits,
    now,
    endsAt,
    `signup-bonus:${referredAccountId}`,
    REFERRAL_POLICY_VERSION,
    JSON.stringify({
      gate_evidence_id: gate.evidenceId,
      mature_claim_id: candidate.claim_id,
      onboarding_completed: true,
      email_present: true,
    }),
    'mature-fraud-reviewed-referred-account',
    now,
    now,
    referredAccountId,
  ).run();
  if ((nowClaim.meta.changes ?? 0) === 0) return 'already-issued';

  await db.batch([
    db.prepare(`
      INSERT OR IGNORE INTO referral_monthly_credit_grants
        (id, user_id, reward_claim_id, period, units, status,
         policy_version, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)
    `).bind(
      crypto.randomUUID(),
      referredAccountId,
      claimId,
      period,
      REFERRAL_POLICY.accessRewards.signupBonusCredits,
      REFERRAL_POLICY_VERSION,
      now,
      now,
    ),
    db.prepare(`
      INSERT INTO referral_monthly_credit_usage
        (user_id, period, base_limit, bonus_granted, updated_at)
      VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(user_id, period) DO UPDATE SET
        bonus_granted = (
          SELECT COALESCE(SUM(units), 0)
          FROM referral_monthly_credit_grants
          WHERE user_id = ? AND period = ? AND status = 'active'
        ),
        updated_at = excluded.updated_at
    `).bind(
      referredAccountId,
      period,
      REFERRAL_POLICY.accessRewards.monthlyBaseCredits,
      REFERRAL_POLICY.accessRewards.signupBonusCredits,
      now,
      referredAccountId,
      period,
    ),
  ]);
  return 'issued';
}

export async function reserveMonthlyChatQuota(
  db: D1Database,
  userId: string,
  tier: string,
  role: string,
  period = monthlyPeriodBounds(Math.floor(Date.now() / 1000)).period,
): Promise<MonthlyReservation> {
  const unavailable: MonthlyReservation = {
    allowed: true,
    period,
    kind: null,
    baseUsed: 0,
    bonusUsed: 0,
    baseLimit: 0,
    bonusLimit: 0,
  };
  if (role === 'admin' || role === 'staff' || tier !== 'free') return unavailable;

  const now = Math.floor(Date.now() / 1000);
  const row = await db.prepare(`
    INSERT INTO referral_monthly_credit_usage
      (user_id, period, base_limit, bonus_granted,
       base_reserved, updated_at)
    SELECT ?, ?, ?, COALESCE((
      SELECT SUM(units) FROM referral_monthly_credit_grants
      WHERE user_id = ? AND period = ? AND status = 'active'
    ), 0), 1, ?
    ON CONFLICT(user_id, period) DO UPDATE SET
      bonus_granted = MAX(
        referral_monthly_credit_usage.bonus_granted,
        excluded.bonus_granted
      ),
      base_reserved = referral_monthly_credit_usage.base_reserved + 1,
      updated_at = excluded.updated_at
      WHERE (
        referral_monthly_credit_usage.base_limit
          - referral_monthly_credit_usage.base_reserved
          - referral_monthly_credit_usage.base_consumed
        + MAX(referral_monthly_credit_usage.bonus_granted, excluded.bonus_granted)
          - referral_monthly_credit_usage.bonus_consumed
      ) > 0
    RETURNING base_limit, base_reserved, base_consumed,
              bonus_granted, bonus_reserved, bonus_consumed
  `).bind(
    userId,
    period,
    REFERRAL_POLICY.accessRewards.monthlyBaseCredits,
    userId,
    period,
    now,
  ).first<{
    base_limit: number;
    base_reserved: number;
    base_consumed: number;
    bonus_granted: number;
    bonus_reserved: number;
    bonus_consumed: number;
  }>();
  if (!row) {
    type UsageRow = {
      base_limit: number;
      base_reserved: number;
      base_consumed: number;
      bonus_granted: number;
      bonus_reserved: number;
      bonus_consumed: number;
    };
    const current = await db.prepare(`
      SELECT base_limit, base_reserved, base_consumed,
             bonus_granted, bonus_reserved, bonus_consumed
      FROM referral_monthly_credit_usage
      WHERE user_id = ? AND period = ?
    `).bind(userId, period).first<UsageRow>();
    return {
      allowed: false,
      period,
      kind: null,
      baseUsed: Math.min(
        (current?.base_reserved ?? 0) + (current?.base_consumed ?? 0),
        current?.base_limit ?? REFERRAL_POLICY.accessRewards.monthlyBaseCredits,
      ),
      bonusUsed: Math.max(
        0,
        (current?.base_reserved ?? 0) + (current?.base_consumed ?? 0)
          - (current?.base_limit ?? REFERRAL_POLICY.accessRewards.monthlyBaseCredits),
      ),
      baseLimit: current?.base_limit ?? REFERRAL_POLICY.accessRewards.monthlyBaseCredits,
      bonusLimit: current?.bonus_granted ?? 0,
    };
  }
  const totalUsed = row.base_reserved + row.base_consumed;
  const baseUsed = Math.min(totalUsed, row.base_limit);
  const bonusUsed = Math.max(0, totalUsed - row.base_limit);
  return {
    allowed: true,
    period,
    // Reserve one combined monthly allowance. Base credits are consumed
    // first; bonus usage is derived after the base allowance is exhausted.
    kind: 'base',
    baseUsed,
    bonusUsed,
    baseLimit: row.base_limit,
    bonusLimit: row.bonus_granted,
  };
}

export async function releaseMonthlyReservation(
  db: D1Database,
  userId: string,
  period: string,
  kind: MonthlyReservationKind,
): Promise<void> {
  if (!kind) return;
  await db.prepare(`
    UPDATE referral_monthly_credit_usage
    SET base_reserved = MAX(0, base_reserved - 1),
        updated_at = ?
    WHERE user_id = ? AND period = ?
  `).bind(Math.floor(Date.now() / 1000), userId, period).run();
}

export async function releaseClaimMonthlyReservation(
  db: D1Database,
  requestId: string,
  userId: string,
): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  await db.batch([
    db.prepare(`
      UPDATE referral_monthly_credit_usage
      SET base_reserved = MAX(0, base_reserved - (
            SELECT monthly_base_reserved FROM chat_request_claims
            WHERE request_id = ? AND user_id = ? AND status = 'reserved'
          )),
          updated_at = ?
      WHERE user_id = ?
        AND period = (
          SELECT monthly_period FROM chat_request_claims
          WHERE request_id = ? AND user_id = ? AND status = 'reserved'
        )
    `).bind(requestId, userId, now, userId, requestId, userId),
    db.prepare(`
      UPDATE chat_request_claims
      SET monthly_base_reserved = 0, monthly_bonus_reserved = 0
      WHERE request_id = ? AND user_id = ? AND status = 'reserved'
    `).bind(requestId, userId),
  ]);
}

export async function completeClaimMonthlyReservation(
  db: D1Database,
  requestId: string,
  userId: string,
): Promise<void> {
  const now = Math.floor(Date.now() / 1000);
  await db.batch([
    db.prepare(`
      UPDATE referral_monthly_credit_usage
      SET base_reserved = MAX(0, base_reserved - (
            SELECT monthly_base_reserved FROM chat_request_claims
            WHERE request_id = ? AND user_id = ? AND status = 'reserved'
          )),
          base_consumed = base_consumed + (
            SELECT monthly_base_reserved FROM chat_request_claims
            WHERE request_id = ? AND user_id = ? AND status = 'reserved'
          ),
          updated_at = ?
      WHERE user_id = ?
        AND period = (
          SELECT monthly_period FROM chat_request_claims
          WHERE request_id = ? AND user_id = ? AND status = 'reserved'
        )
    `).bind(
      requestId, userId, requestId, userId,
      now, userId, requestId, userId,
    ),
    db.prepare(`
      UPDATE chat_request_claims
      SET monthly_base_reserved = 0, monthly_bonus_reserved = 0
      WHERE request_id = ? AND user_id = ? AND status = 'reserved'
    `).bind(requestId, userId),
  ]);
}

export async function getReferralRewardStatus(
  db: D1Database,
  userId: string,
  now = Math.floor(Date.now() / 1000),
): Promise<ReferralRewardStatus> {
  const { period } = monthlyPeriodBounds(now);
  const access = await db.prepare(`
    SELECT expires_at FROM referral_access_entitlements
    WHERE user_id = ? AND status = 'active' AND expires_at > ?
    ORDER BY expires_at DESC LIMIT 1
  `).bind(userId, now).first<{ expires_at: number }>();
  const usage = await db.prepare(`
    SELECT base_limit, base_reserved, base_consumed,
           bonus_granted, bonus_reserved, bonus_consumed
    FROM referral_monthly_credit_usage
    WHERE user_id = ? AND period = ?
  `).bind(userId, period).first<{
    base_limit: number;
    base_reserved: number;
    base_consumed: number;
    bonus_granted: number;
    bonus_reserved: number;
    bonus_consumed: number;
  }>();
  const baseLimit = usage?.base_limit ?? REFERRAL_POLICY.accessRewards.monthlyBaseCredits;
  const totalUsed = (usage?.base_reserved ?? 0) + (usage?.base_consumed ?? 0);
  const baseUsed = Math.min(totalUsed, baseLimit);
  const bonusGranted = usage?.bonus_granted ?? 0;
  const bonusUsed = Math.max(0, totalUsed - baseLimit);
  return {
    adFree: { active: Boolean(access), expiresAt: access?.expires_at ?? null },
    monthly: {
      period,
      baseLimit,
      baseUsed,
      baseRemaining: Math.max(0, baseLimit - baseUsed),
      bonusGranted,
      bonusUsed,
      bonusRemaining: Math.max(0, bonusGranted - bonusUsed),
    },
  };
}