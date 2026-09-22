import {
  CONSUMER_REFERRAL_POLICY,
  type ConsumerReferralBenefit,
} from '../contracts/consumer-referral-policy';

function nowSeconds(): number {
  return Math.floor(Date.now() / 1000);
}

function referralCode(): string {
  return Array.from(crypto.getRandomValues(new Uint8Array(16)))
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('');
}

export async function ensureConsumerReferralCode(
  db: D1Database,
  userId: string,
): Promise<string | null> {
  const existing = await db.prepare(
    'SELECT consumer_referral_code FROM users WHERE id = ? AND deleted_at IS NULL',
  ).bind(userId).first<{ consumer_referral_code: string | null }>();
  if (!existing) return null;
  if (existing.consumer_referral_code) return existing.consumer_referral_code;

  for (let attempt = 0; attempt < 3; attempt += 1) {
    const code = referralCode();
    const result = await db.prepare(`
      UPDATE users SET consumer_referral_code = ?, updated_at = ?
      WHERE id = ? AND deleted_at IS NULL AND consumer_referral_code IS NULL
    `).bind(code, nowSeconds(), userId).run();
    if ((result.meta.changes ?? 0) === 1) return code;
    const retry = await db.prepare(
      'SELECT consumer_referral_code FROM users WHERE id = ?',
    ).bind(userId).first<{ consumer_referral_code: string | null }>();
    if (retry?.consumer_referral_code) return retry.consumer_referral_code;
  }
  return null;
}

export function currentQuotaMonthPeriod(epochSeconds = nowSeconds()): string {
  const date = new Date(epochSeconds * 1000);
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, '0')}`;
}

export function monthResetAt(epochSeconds = nowSeconds()): number {
  const date = new Date(epochSeconds * 1000);
  return Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + 1, 1) / 1000;
}

export function monthlyChatLimit(
  tier: string | null | undefined,
  referralUpgradeUntil: number | null | undefined,
  epochSeconds = nowSeconds(),
): number {
  if (Number(referralUpgradeUntil ?? 0) > epochSeconds) {
    return CONSUMER_REFERRAL_POLICY.upgrade.monthlyChatLimit;
  }
  switch (tier ?? 'free') {
    case 'premium': return 600;
    case 'pro': return 300;
    case 'starter': return 100;
    default: return 30;
  }
}

export async function getConsumerReferralStatus(
  db: D1Database,
  userId: string,
  epochSeconds = nowSeconds(),
): Promise<Record<string, unknown>> {
  const row = await db.prepare(`
    SELECT referral_points, referral_visitors_verified,
           referral_upgrade_until, referral_ads_free_until,
           consumer_referral_code
    FROM users WHERE id = ? AND deleted_at IS NULL
  `).bind(userId).first<{
    referral_points: number | null;
    referral_visitors_verified: number | null;
    referral_upgrade_until: number | null;
    referral_ads_free_until: number | null;
    consumer_referral_code: string | null;
  }>();
  const code = await ensureConsumerReferralCode(db, userId);
  const points = Number(row?.referral_points ?? 0);
  const upgradeUntil = Number(row?.referral_upgrade_until ?? 0) || null;
  const adsFreeUntil = Number(row?.referral_ads_free_until ?? 0) || null;
  return {
    points,
    verified_visitors: Number(row?.referral_visitors_verified ?? 0),
    referral_code: code,
    referral_link: code ? `https://syrabit.ai/r/${code}` : null,
    policy: {
      version: CONSUMER_REFERRAL_POLICY.version,
      points_per_verified_visitor: CONSUMER_REFERRAL_POLICY.pointsPerVerifiedVisitor,
      upgrade_points: CONSUMER_REFERRAL_POLICY.upgrade.cost,
      upgrade_monthly_chat_limit: CONSUMER_REFERRAL_POLICY.upgrade.monthlyChatLimit,
      upgrade_duration_days: 30,
      ad_free_points: CONSUMER_REFERRAL_POLICY.adsFree.cost,
      ad_free_duration_days: 30,
    },
    upgrade: {
      active: Boolean(upgradeUntil && upgradeUntil > epochSeconds),
      until: upgradeUntil,
    },
    ads_free: {
      active: Boolean(adsFreeUntil && adsFreeUntil > epochSeconds),
      until: adsFreeUntil,
    },
  };
}

export async function awardVerifiedVisitorPoints(
  db: D1Database,
  claimId: string,
  maturityToken: string,
  awardedAt = nowSeconds(),
): Promise<boolean> {
  const ledgerId = crypto.randomUUID();
  const inserted = await db.prepare(`
    INSERT OR IGNORE INTO referral_points_ledger
      (id, user_id, source_type, source_key, points, created_at)
    SELECT ?, i.user_id, 'verified_visitor',
           'influencer-visitor:' || c.consumer_identity_key, ?, ?
    FROM referral_weekly_claims c
    JOIN referral_influencer_slots i ON i.slot_no = c.credited_influencer_slot
    JOIN users u ON u.id = i.user_id AND u.deleted_at IS NULL
    WHERE c.id = ? AND c.maturity_token = ?
      AND c.consumer_identity_key IS NOT NULL
      AND i.status = 'active'
  `).bind(
    ledgerId,
    CONSUMER_REFERRAL_POLICY.pointsPerVerifiedVisitor,
    awardedAt,
    claimId,
    maturityToken,
  ).run();
  if ((inserted.meta.changes ?? 0) !== 1) return false;

  await db.prepare(`
    UPDATE users
    SET referral_points = COALESCE(referral_points, 0) + ?,
        referral_visitors_verified = COALESCE(referral_visitors_verified, 0) + ?,
        updated_at = ?
    WHERE id = (
      SELECT user_id FROM referral_points_ledger WHERE id = ?
    )
  `).bind(
    CONSUMER_REFERRAL_POLICY.pointsPerVerifiedVisitor,
    CONSUMER_REFERRAL_POLICY.pointsPerVerifiedVisitor,
    awardedAt,
    ledgerId,
  ).run();
  return true;
}

export async function recordConsumerReferralVisit(
  db: D1Database,
  ownerUserId: string,
  visitorKey: string,
  identityConfidence: 'low' | 'browser' | 'account',
  occurredAt = nowSeconds(),
): Promise<boolean> {
  if (identityConfidence === 'low') return false;
  const visit = await db.prepare(`
    INSERT OR IGNORE INTO consumer_referral_visits
      (id, owner_user_id, visitor_key, identity_confidence, created_at)
    VALUES (?, ?, ?, ?, ?)
  `).bind(
    crypto.randomUUID(),
    ownerUserId,
    visitorKey,
    identityConfidence,
    occurredAt,
  ).run();
  if ((visit.meta.changes ?? 0) !== 1) return false;

  const ledger = await db.prepare(`
    INSERT OR IGNORE INTO referral_points_ledger
      (id, user_id, source_type, source_key, points, created_at)
    VALUES (?, ?, 'verified_visitor', ?, ?, ?)
  `).bind(
    crypto.randomUUID(),
    ownerUserId,
    `consumer-visitor:${ownerUserId}:${visitorKey}`,
    CONSUMER_REFERRAL_POLICY.pointsPerVerifiedVisitor,
    occurredAt,
  ).run();
  if ((ledger.meta.changes ?? 0) !== 1) return false;

  await db.prepare(`
    UPDATE users
    SET referral_points = COALESCE(referral_points, 0) + ?,
        referral_visitors_verified = COALESCE(referral_visitors_verified, 0) + ?,
        updated_at = ?
    WHERE id = ? AND deleted_at IS NULL
  `).bind(
    CONSUMER_REFERRAL_POLICY.pointsPerVerifiedVisitor,
    CONSUMER_REFERRAL_POLICY.pointsPerVerifiedVisitor,
    occurredAt,
    ownerUserId,
  ).run();
  return true;
}

export async function redeemReferralPoints(
  db: D1Database,
  userId: string,
  benefit: ConsumerReferralBenefit,
  redeemedAt = nowSeconds(),
): Promise<Record<string, unknown> | null> {
  const isUpgrade = benefit === 'upgrade';
  const cost = isUpgrade
    ? CONSUMER_REFERRAL_POLICY.upgrade.cost
    : CONSUMER_REFERRAL_POLICY.adsFree.cost;
  const duration = isUpgrade
    ? CONSUMER_REFERRAL_POLICY.upgrade.durationSeconds
    : CONSUMER_REFERRAL_POLICY.adsFree.durationSeconds;
  const column = isUpgrade ? 'referral_upgrade_until' : 'referral_ads_free_until';
  const redemptionId = crypto.randomUUID();

  const updated = await db.prepare(`
    UPDATE users
    SET referral_points = referral_points - ?,
        ${column} = MAX(COALESCE(${column}, 0), ?) + ?,
        updated_at = ?
    WHERE id = ? AND deleted_at IS NULL AND referral_points >= ?
    RETURNING referral_points, ${column} AS entitlement_until
  `).bind(cost, redeemedAt, duration, redeemedAt, userId, cost)
    .first<{ referral_points: number; entitlement_until: number }>();
  if (!updated) return null;

  await db.prepare(`
    INSERT INTO referral_points_ledger
      (id, user_id, source_type, source_key, points, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
  `).bind(
    redemptionId,
    userId,
    isUpgrade ? 'upgrade_redemption' : 'ads_free_redemption',
    redemptionId,
    -cost,
    redeemedAt,
  ).run();

  return {
    benefit,
    points: updated.referral_points,
    entitlement_until: updated.entitlement_until,
  };
}