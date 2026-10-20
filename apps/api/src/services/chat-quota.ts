import { currentQuotaDayPeriod } from './anonymous';

/**
 * Completed turns plus live reservations count against the same daily allowance.
 * Keeping both in this read lets the API expose the authoritative remaining count
 * without allowing concurrent requests to overstate it.
 */
export async function getDailyChatQuotaUsage(
  d1: D1Database,
  identityId: string,
  isAnonymous: boolean,
  period = currentQuotaDayPeriod(),
): Promise<number> {
  const now = Math.floor(Date.now() / 1000);
  const row = await d1.prepare(`
    SELECT COALESCE((
      SELECT count FROM daily_chat_quota_usage
      WHERE identity_id = ? AND is_anonymous = ? AND period = ?
    ), 0) + (
      SELECT COUNT(*) FROM chat_request_claims
      WHERE daily_identity_id = ? AND is_anon = ? AND daily_period = ?
        AND daily_quota_reserved = 1 AND status = 'reserved'
        AND expires_at > ?
    ) AS count
  `).bind(
    identityId,
    isAnonymous ? 1 : 0,
    period,
    identityId,
    isAnonymous ? 1 : 0,
    period,
    now,
  ).first<{ count: number }>();
  return row?.count ?? 0;
}

/** Completed free-account turns plus live monthly reservations. */
export async function getMonthlyChatQuotaUsage(
  d1: D1Database,
  userId: string,
  period: string,
): Promise<number> {
  const now = Math.floor(Date.now() / 1000);
  const row = await d1.prepare(`
    SELECT COALESCE((
      SELECT count FROM monthly_quota_usage WHERE user_id = ? AND period = ?
    ), 0) + (
      SELECT COUNT(*) FROM chat_request_claims
      WHERE user_id = ? AND monthly_period = ?
        AND monthly_quota_reserved = 1 AND status = 'reserved'
        AND expires_at > ?
    ) AS count
  `).bind(userId, period, userId, period, now).first<{ count: number }>();
  return row?.count ?? 0;
}
