import { Hono, type Context } from 'hono';
import type { JWTPayload } from 'jose';

import { REFERRAL_POLICY } from '../contracts/referral-policy';
import {
  extractBearer,
  isSessionValid,
  verifyAdminToken,
  verifyToken,
} from '../middleware/auth';
import {
  activateApprovedAdvancedPositions,
  advanceReferralLifecycle,
  matureReferralClaim,
  pauseReferralProgram,
  reconcileReferralAccount,
  recordReferralVisit,
  recordReferralGateEvidence,
  resumeReferralProgram,
  reviewAdvancedPosition,
  weeklyReferralMetrics,
} from '../services/referral-attribution';
import {
  activateReferralApplication,
  expireReferralApplication,
  listReferralApplications,
  referralApplicationAudits,
  referralExperience,
  reviewReferralApplication,
  submitReferralApplication,
} from '../services/referral-onboarding';
import {
  approveSettlementStatement,
  listSettlementStatements,
  listBeneficiaries,
  listStatementsForUser,
  openWeeklySettlement,
  recordBeneficiary,
  recordSettlementPayout,
  reviewBeneficiary,
  settleReferralWeek,
  settlementAudits,
  transitionSettlementPayout,
  validatePrivateReceipt,
  MAX_RECEIPT_BYTES,
} from '../services/referral-settlement';
import {
  calculateWeeklyRoi,
  INVALID_ROI_AUDIT_CURSOR_DETAIL,
  ingestAdRevenueReport,
  isInvalidRoiAuditCursorError,
  listAdNetworkInventory,
  listRoiEvidenceDownloadAuditPage,
  normalizeRoiAuditLimit,
  normalizeRoiReportLimit,
  recordRoiControls,
  recordRoiEvidenceDownloadAudit,
  roiDashboard,
} from '../services/referral-roi';
import { getReferralRewardStatus } from '../services/referral-rewards';
import type { Env, JwtPayload } from '../types';

export const referralRouter = new Hono<{ Bindings: Env }>();
export const adminReferralRouter = new Hono<{ Bindings: Env }>();
export const internalReferralRouter = new Hono<{ Bindings: Env }>();

const requireReferralRuntime = async (
  c: Context<{ Bindings: Env }>,
  next: () => Promise<void>,
) => {
  if (c.env.REFERRAL_PROGRAM_RUNTIME_ENABLED !== 'true') {
    return c.json({ detail: 'Not found' }, 404);
  }
  return next();
};
referralRouter.use('*', requireReferralRuntime);
adminReferralRouter.use('*', requireReferralRuntime);
internalReferralRouter.use('*', requireReferralRuntime);

type AccessPayload = JWTPayload & JwtPayload & { jti?: string };

function cookieValue(cookieHeader: string, name: string): string | null {
  const prefix = `${name}=`;
  return cookieHeader.split(';')
    .map(part => part.trim())
    .find(part => part.startsWith(prefix))
    ?.slice(prefix.length) ?? null;
}

async function requestBody(c: Context<{ Bindings: Env }>): Promise<Record<string, unknown> | null> {
  try {
    const body = await c.req.json<unknown>();
    return body && typeof body === 'object' && !Array.isArray(body)
      ? body as Record<string, unknown>
      : null;
  } catch {
    return null;
  }
}

async function requireAccessUser(
  c: Context<{ Bindings: Env }>,
): Promise<AccessPayload | Response> {
  const bearer = extractBearer(c.req.header('Authorization') ?? null);
  if (!bearer) return c.json({ detail: 'Authentication required' }, 401);
  const payload = await verifyToken(bearer, c.env.JWT_SECRET);
  if (!payload || payload.type !== 'access' || !payload.sub) {
    return c.json({ detail: 'Access token required' }, 401);
  }
  if (!await isSessionValid(c.env.DB, payload.sub, payload.iat)) {
    return c.json({ detail: 'Session expired after password change. Sign in again.' }, 401);
  }
  return payload;
}

function allowedMutationOrigin(c: Context<{ Bindings: Env }>): boolean {
  const trustedEdge = c.req.header('X-Edge-Secret') === c.env.EDGE_SHARED_SECRET;
  const origin = c.req.header('Origin')
    ?? (trustedEdge ? c.req.header('X-Original-Origin') : undefined);
  if (!origin) return false;
  const configured = (c.env.ALLOWED_ORIGINS ?? '')
    .split(',')
    .map(value => value.trim())
    .filter(value => value && value !== '*');
  const allowed = new Set([
    'https://syrabit.ai',
    'https://www.syrabit.ai',
    ...configured,
  ]);
  try {
    return allowed.has(new URL(origin).origin);
  } catch {
    return false;
  }
}

async function requireReferralCapability(
  c: Context<{ Bindings: Env }>,
  capability: string,
): Promise<{ actorId: string } | Response> {
  const bearer = extractBearer(c.req.header('Authorization') ?? null);
  const adminCookie = cookieValue(
    c.req.header('Cookie') ?? '',
    'syrabit_admin_session',
  );
  let actorId: string | null = null;
  let usedCookie = false;

  if (adminCookie) {
    const admin = await verifyAdminToken(adminCookie, c.env.ADMIN_JWT_SECRET);
    if (admin?.sub && await isSessionValid(c.env.DB, admin.sub, admin.iat)) {
      actorId = admin.sub;
      usedCookie = true;
    }
  }
  if (!actorId && bearer) {
    const admin = await verifyAdminToken(bearer, c.env.ADMIN_JWT_SECRET);
    if (admin?.sub && await isSessionValid(c.env.DB, admin.sub, admin.iat)) {
      actorId = admin.sub;
    } else {
      const access = await verifyToken(bearer, c.env.JWT_SECRET);
      if (
        access?.sub
        && access.type === 'access'
        && ['staff', 'admin'].includes(access.role ?? '')
        && await isSessionValid(c.env.DB, access.sub, access.iat)
      ) {
        actorId = access.sub;
      }
    }
  }
  if (!actorId) return c.json({ detail: 'Authentication required' }, 401);
  if (
    usedCookie
    && !['GET', 'HEAD', 'OPTIONS'].includes(c.req.method)
    && !allowedMutationOrigin(c)
  ) {
    return c.json({ detail: 'Trusted Origin required for cookie-authenticated mutation' }, 403);
  }

  const row = await c.env.DB.prepare(`
    SELECT role, capabilities
    FROM users
    WHERE id = ? AND deleted_at IS NULL
  `).bind(actorId).first<{ role: string; capabilities: string | null }>();
  if (!row || !['staff', 'admin'].includes(row.role)) {
    return c.json({ detail: 'Staff access required' }, 403);
  }
  let capabilities: unknown = null;
  try {
    capabilities = row.capabilities ? JSON.parse(row.capabilities) : null;
  } catch {
    capabilities = null;
  }
  if (
    !Array.isArray(capabilities)
    || !capabilities.every(item => typeof item === 'string')
    || (!capabilities.includes(capability) && !capabilities.includes('referral:*'))
  ) {
    return c.json({
      detail: `Explicit ${capability} capability required`,
      capability,
    }, 403);
  }
  return { actorId };
}

async function internalAuthorized(c: Context<{ Bindings: Env }>): Promise<boolean> {
  const secret = c.env.EDGE_SHARED_SECRET;
  const supplied = extractBearer(c.req.header('Authorization') ?? null);
  if (!secret || !supplied) return false;
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const [expected, actual] = await Promise.all([
    crypto.subtle.sign('HMAC', key, encoder.encode(secret)),
    crypto.subtle.sign('HMAC', key, encoder.encode(supplied)),
  ]);
  const left = new Uint8Array(expected);
  const right = new Uint8Array(actual);
  let difference = left.length ^ right.length;
  for (let index = 0; index < Math.min(left.length, right.length); index += 1) {
    difference |= (left[index] ?? 0) ^ (right[index] ?? 0);
  }
  return supplied.length === secret.length && difference === 0;
}

referralRouter.get('/visit/:code', async (c) => {
  let result;
  try {
    result = await recordReferralVisit(
      c.env.DB,
      c.req.raw,
      c.req.param('code'),
      c.env.EDGE_SHARED_SECRET,
    );
  } catch {
    return c.json({ detail: 'Referral attribution unavailable' }, 503);
  }
  const response = c.redirect(`https://syrabit.ai${result.destination}`, 302);
  response.headers.set('Cache-Control', 'private, no-store, max-age=0');
  response.headers.set('Pragma', 'no-cache');
  response.headers.set('Referrer-Policy', 'origin');
  response.headers.set('X-Robots-Tag', 'noindex, nofollow');
  response.headers.set('Cross-Origin-Resource-Policy', 'same-site');
  if (result.setCookie) response.headers.append('Set-Cookie', result.setCookie);
  return response;
});

referralRouter.post('/reconcile', async (c) => {
  const auth = await requireAccessUser(c);
  if (auth instanceof Response) return auth;
  try {
    const status = await reconcileReferralAccount(
      c.env.DB,
      c.req.raw,
      auth.sub,
      c.env.EDGE_SHARED_SECRET,
    );
    return c.json({ status });
  } catch {
    return c.json({ detail: 'Referral reconciliation unavailable' }, 503);
  }
});

referralRouter.get('/me', async (c) => {
  const auth = await requireAccessUser(c);
  if (auth instanceof Response) return auth;
  try {
    const [experience, rewards] = await Promise.all([
      referralExperience(c.env.DB, auth.sub),
      getReferralRewardStatus(c.env.DB, auth.sub),
    ]);
    return c.json({
      ...experience,
      rewards: {
        ...rewards,
        policy: {
          monthly_base_credits: REFERRAL_POLICY.accessRewards.monthlyBaseCredits,
          signup_bonus_credits: REFERRAL_POLICY.accessRewards.signupBonusCredits,
          ad_free_duration_days: 30,
        },
      },
    });
  } catch {
    return c.json({ detail: 'Referral experience unavailable' }, 503);
  }
});

referralRouter.get('/me/statements', async (c) => {
  const auth = await requireAccessUser(c);
  if (auth instanceof Response) return auth;
  try {
    return c.json({ statements: await listStatementsForUser(c.env.DB, auth.sub) });
  } catch {
    return c.json({ detail: 'Referral statements unavailable' }, 503);
  }
});

referralRouter.post('/applications', async (c) => {
  const auth = await requireAccessUser(c);
  if (auth instanceof Response) return auth;
  if (!allowedMutationOrigin(c)) {
    return c.json({ detail: 'Trusted Origin required' }, 403);
  }
  const body = await requestBody(c);
  if (!body) return c.json({ detail: 'Application body required' }, 422);
  try {
    const result = await submitReferralApplication(c.env.DB, auth.sub, {
      institution: String(body.institution ?? ''),
      className: String(body.class_name ?? ''),
      streamName: String(body.stream_name ?? ''),
      ageEligible: body.age_eligible === true,
      guardianConsentRequired: body.guardian_consent_required === true,
      guardianConsentConfirmed: body.guardian_consent_confirmed === true,
      eligibilityAcknowledged: body.eligibility_acknowledged === true,
      conductAcknowledged: body.conduct_acknowledged === true,
      privacyConsent: body.privacy_consent === true,
      termsVersion: String(body.terms_version ?? ''),
      privacyVersion: String(body.privacy_version ?? ''),
      idempotencyKey: String(body.idempotency_key ?? ''),
    });
    return c.json({
      status: result.application.status,
      application_id: result.application.id,
      idempotent: result.idempotent,
    }, result.idempotent ? 200 : 201);
  } catch (error) {
    return c.json({
      detail: error instanceof Error ? error.message : 'Invalid referral application',
    }, 422);
  }
});

referralRouter.post('/activate', async (c) => {
  const auth = await requireAccessUser(c);
  if (auth instanceof Response) return auth;
  if (!allowedMutationOrigin(c)) {
    return c.json({ detail: 'Trusted Origin required' }, 403);
  }
  try {
    const result = await activateReferralApplication(c.env.DB, auth.sub);
    if (!result) return c.json({ detail: 'Activation is not available' }, 409);
    return c.json(result, result.status === 'active' ? 200 : 409);
  } catch {
    return c.json({ detail: 'Referral activation unavailable' }, 503);
  }
});

adminReferralRouter.get('/applications', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.reviewCapability);
  if (auth instanceof Response) return auth;
  try {
    return c.json({
      applications: await listReferralApplications(c.env.DB, c.req.query('status')),
    });
  } catch {
    return c.json({ detail: 'Referral review queue unavailable' }, 503);
  }
});

adminReferralRouter.get('/applications/:applicationId/audits', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.reviewCapability);
  if (auth instanceof Response) return auth;
  try {
    return c.json({
      audits: await referralApplicationAudits(c.env.DB, c.req.param('applicationId')),
    });
  } catch {
    return c.json({ detail: 'Referral audit history unavailable' }, 503);
  }
});

adminReferralRouter.post('/applications/:applicationId/decision', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.reviewCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  const decision = body?.decision;
  if (
    !['review', 'approve', 'waitlist', 'reject', 'suspend', 'close'].includes(String(decision))
    || typeof body?.reason !== 'string'
  ) return c.json({ detail: 'Valid decision and reason are required' }, 422);
  try {
    const result = await reviewReferralApplication(
      c.env.DB,
      c.req.param('applicationId'),
      {
        decision: decision as 'review' | 'approve' | 'waitlist' | 'reject' | 'suspend' | 'close',
        actorId: auth.actorId,
        reason: body.reason,
        identityVerified: body.identity_verified === true,
        kycVerified: body.kyc_verified === true,
      },
    );
    return result
      ? c.json(result)
      : c.json({ detail: 'Application not found' }, 404);
  } catch (error) {
    return c.json({
      detail: error instanceof Error ? error.message : 'Referral decision failed',
    }, 422);
  }
});

adminReferralRouter.post('/applications/:applicationId/expire', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.reviewCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  if (typeof body?.reason !== 'string') {
    return c.json({ detail: 'Expiry reason is required' }, 422);
  }
  try {
    const result = await expireReferralApplication(
      c.env.DB,
      c.req.param('applicationId'),
      auth.actorId,
      body.reason,
    );
    return result
      ? c.json(result)
      : c.json({ detail: 'Application is not awaiting activation' }, 409);
  } catch {
    return c.json({ detail: 'Activation expiry unavailable' }, 503);
  }
});

adminReferralRouter.post('/admissions', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.reviewCapability);
  if (auth instanceof Response) return auth;
  return c.json({
    detail: 'Direct admissions are retired. Review an applicant through /applications/:applicationId/decision.',
  }, 410);
});

adminReferralRouter.post('/program/pause', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.policyCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  const reason = typeof body?.reason === 'string' ? body.reason.trim() : '';
  if (reason.length < 8 || reason.length > 1_000) {
    return c.json({ detail: 'A pause reason between 8 and 1000 characters is required' }, 422);
  }
  try {
    const paused = await pauseReferralProgram(c.env.DB, auth.actorId, reason);
    return c.json({ status: paused ? 'paused' : 'closed' }, paused ? 200 : 409);
  } catch {
    return c.json({ detail: 'Referral pause unavailable' }, 503);
  }
});

adminReferralRouter.post('/program/resume', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.policyCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  const evidenceId = body?.evidence_id;
  if (typeof evidenceId !== 'string') {
    return c.json({ detail: 'Authoritative evidence ID is required' }, 422);
  }
  const effectiveAt = Math.floor(Date.now() / 1000);
  try {
    const result = await resumeReferralProgram(c.env.DB, {
      actorId: auth.actorId,
      effectiveAt,
      evidenceId,
      ...(body?.funded_cap_inr === undefined
        ? {}
        : { fundedCapInr: Number(body.funded_cap_inr) }),
    });
    return result.resumed
      ? c.json({ status: 'active', accrual_resumed_at: effectiveAt })
      : c.json({ detail: 'Resume gate rejected', reasons: result.reasons }, 409);
  } catch {
    return c.json({ detail: 'Referral resume unavailable' }, 503);
  }
});

adminReferralRouter.post('/program/open-week', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.policyCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  const evidenceId = body?.evidence_id;
  if (typeof evidenceId !== 'string') {
    return c.json({ detail: 'Authoritative evidence ID is required' }, 422);
  }
  const effectiveAt = Math.floor(Date.now() / 1000);
  try {
    const result = await advanceReferralLifecycle(c.env.DB, {
      actorId: auth.actorId,
      effectiveAt,
      evidenceId,
      ...(body?.funded_cap_inr === undefined
        ? {}
        : { fundedCapInr: Number(body.funded_cap_inr) }),
    });
    if (!(result.opened || result.alreadyOpen)) {
      return c.json({ detail: 'Week-opening gate rejected', reasons: result.reasons }, 409);
    }
    const envelope = await openWeeklySettlement(c.env.DB, {
      weekId: result.week.id,
      evidenceId,
      actorId: auth.actorId,
      openedAt: effectiveAt,
      ...(body?.funded_cap_inr === undefined
        ? {}
        : { fundedCapInr: Number(body.funded_cap_inr) }),
    });
    return c.json({
          status: 'active',
          week: result.week,
          opened: result.opened,
          activated_advanced_positions: result.activatedAdvancedPositions,
          funded_cap_inr: envelope.fundedCapInr,
          settlement_status: envelope.status,
        });
  } catch {
    return c.json({ detail: 'Referral week lifecycle unavailable' }, 503);
  }
});

adminReferralRouter.post('/advanced/:position/review', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.reviewCapability);
  if (auth instanceof Response) return auth;
  const positionNo = Number(c.req.param('position'));
  const body = await requestBody(c);
  if (
    !Number.isInteger(positionNo)
    || positionNo < 1
    || positionNo > REFERRAL_POLICY.advancedInfluencerSlots
    || !body
    || typeof body.approved !== 'boolean'
    || typeof body.reason !== 'string'
    || !body.reason.trim()
  ) {
    return c.json({ detail: 'Valid position, approved boolean, and review reason are required' }, 422);
  }
  try {
    const reviewed = await reviewAdvancedPosition(c.env.DB, {
      positionNo,
      approved: body.approved,
      reason: body.reason,
      reviewerId: auth.actorId,
      reviewedAt: Math.floor(Date.now() / 1000),
    });
    return reviewed
      ? c.json({ status: body.approved ? 'approved' : 'rejected' })
      : c.json({ detail: 'Provisional position not found' }, 409);
  } catch {
    return c.json({ detail: 'Advanced review unavailable' }, 503);
  }
});

adminReferralRouter.get('/weeks/:weekId/influencers/:slot/metrics', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.reviewCapability);
  if (auth instanceof Response) return auth;
  const slot = Number(c.req.param('slot'));
  if (!Number.isInteger(slot) || slot < 1 || slot > REFERRAL_POLICY.approvedInfluencerSlots) {
    return c.json({ detail: 'Invalid influencer slot' }, 422);
  }
  try {
    const metrics = await weeklyReferralMetrics(c.env.DB, c.req.param('weekId'), slot);
    return c.json({
      week_id: c.req.param('weekId'),
      influencer_slot: slot,
      labels: {
        unique_browsers: 'Unique browsers',
        authenticated_accounts: 'Authenticated accounts',
        low_confidence_identities: 'Low-confidence identities',
        repeated_weekly_identities: 'Repeated weekly identities',
        mature_verified: 'Mature verified claims',
      },
      metrics: {
        unique_browsers: metrics.uniqueBrowsers,
        authenticated_accounts: metrics.authenticatedAccounts,
        low_confidence_identities: metrics.lowConfidenceIdentities,
        repeated_weekly_identities: metrics.repeatedWeeklyIdentities,
        mature_verified: metrics.matureVerified,
      },
    });
  } catch {
    return c.json({ detail: 'Referral metrics unavailable' }, 503);
  }
});

adminReferralRouter.get('/settlements', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  try {
    return c.json({
      statements: await listSettlementStatements(c.env.DB, c.req.query('week_id')),
    });
  } catch {
    return c.json({ detail: 'Settlement statements unavailable' }, 503);
  }
});

adminReferralRouter.get('/roi/dashboard', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  try {
    return c.json(await roiDashboard(c.env.DB, Number(c.req.query('limit') ?? 12)));
  } catch {
    return c.json({ detail: 'Referral ROI dashboard unavailable' }, 503);
  }
});

adminReferralRouter.get('/roi/dashboard/export', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  try {
    const reportLimit = normalizeRoiReportLimit(Number(c.req.query('limit') ?? 12));
    const dashboard = await roiDashboard(c.env.DB, reportLimit);
    c.executionCtx.waitUntil(
      recordRoiEvidenceDownloadAudit(c.env.DB, {
        actorId: auth.actorId,
        reportLimit,
        occurredAt: Math.floor(Date.now() / 1000),
      }).catch(() => undefined),
    );
    return new Response(JSON.stringify(dashboard, null, 2), {
      headers: {
        'Cache-Control': 'private, no-store, max-age=0',
        'Content-Disposition': 'attachment; filename="referral-roi-evidence.json"',
        'Content-Type': 'application/json; charset=utf-8',
        'X-Content-Type-Options': 'nosniff',
      },
    });
  } catch {
    return c.json({ detail: 'Referral ROI evidence export unavailable' }, 503);
  }
});

adminReferralRouter.get('/roi/dashboard/export-audits', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  try {
    const cursor = c.req.query('cursor')?.trim();
    const page = await listRoiEvidenceDownloadAuditPage(c.env.DB, {
      limit: normalizeRoiAuditLimit(Number(c.req.query('limit') ?? 25)),
      ...(cursor ? { cursor } : {}),
    });
    return c.json({ audits: page.audits, next_cursor: page.nextCursor });
  } catch (error) {
    if (isInvalidRoiAuditCursorError(error)) {
      return c.json({ detail: INVALID_ROI_AUDIT_CURSOR_DETAIL }, 422);
    }
    return c.json({ detail: 'Referral ROI evidence audit history unavailable' }, 503);
  }
});

adminReferralRouter.get('/roi/inventory', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  try {
    return c.json({ inventory: await listAdNetworkInventory(c.env.DB) });
  } catch {
    return c.json({ detail: 'Ad network inventory unavailable' }, 503);
  }
});

adminReferralRouter.post('/roi/revenue', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  if (!body) return c.json({ detail: 'Provider report body required' }, 422);
  try {
    return c.json(await ingestAdRevenueReport(c.env.DB, {
      network: String(body.network ?? ''),
      periodStart: Number(body.period_start),
      periodEnd: Number(body.period_end),
      settlementPeriod: String(body.settlement_period ?? ''),
      ...(body.currency === undefined ? {} : { currency: String(body.currency) }),
      grossRevenuePaise: Number(body.gross_revenue_paise),
      ...(body.adjustments_paise === undefined ? {} : { adjustmentsPaise: Number(body.adjustments_paise) }),
      ...(body.provider_fees_paise === undefined ? {} : { providerFeesPaise: Number(body.provider_fees_paise) }),
      monetizedImpressions: Number(body.monetized_impressions),
      finalized: body.finalized === true,
      finalizedThroughAt: Number(body.finalized_through_at),
      fetchedAt: Number(body.fetched_at),
      freshnessExpiresAt: Number(body.freshness_expires_at),
      sourceReference: String(body.source_reference ?? ''),
      evidenceHash: String(body.evidence_hash ?? ''),
      importedBy: auth.actorId,
    }), 201);
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'Provider revenue unavailable' }, 422);
  }
});

adminReferralRouter.post('/roi/controls', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  if (!body) return c.json({ detail: 'ROI control evidence body required' }, 422);
  try {
    return c.json(await recordRoiControls(c.env.DB, {
      reserveHealthy: body.reserve_healthy === true,
      revenueFresh: body.revenue_fresh === true,
      invalidTrafficHealthy: body.invalid_traffic_healthy === true,
      adAccountHealthy: body.ad_account_healthy === true,
      contributionMarginHealthy: body.contribution_margin_healthy === true,
      identityResetsHealthy: body.identity_resets_healthy === true,
      fraudHealthy: body.fraud_healthy === true,
      exposureHealthy: body.exposure_healthy === true,
      evidenceId: String(body.evidence_id ?? ''),
      warnings: Array.isArray(body.warnings)
        ? body.warnings.filter(item => typeof item === 'string') as string[]
        : [],
      updatedBy: auth.actorId,
      updatedAt: Number(body.updated_at),
      expiresAt: Number(body.expires_at),
    }));
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'ROI controls unavailable' }, 422);
  }
});

adminReferralRouter.post('/roi/weeks/:weekId/calculate', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  try {
    return c.json(await calculateWeeklyRoi(c.env.DB, {
      weekId: c.req.param('weekId'),
      actorId: auth.actorId,
      calculatedAt: Math.floor(Date.now() / 1000),
      ...(body ? {
        costs: {
          ...(body.review_cost_inr === undefined ? {} : { reviewCostInr: Number(body.review_cost_inr) }),
          ...(body.fraud_cost_inr === undefined ? {} : { fraudCostInr: Number(body.fraud_cost_inr) }),
          ...(body.reversal_cost_inr === undefined ? {} : { reversalCostInr: Number(body.reversal_cost_inr) }),
          ...(body.support_cost_inr === undefined ? {} : { supportCostInr: Number(body.support_cost_inr) }),
          ...(body.operating_cost_inr === undefined ? {} : { operatingCostInr: Number(body.operating_cost_inr) }),
        },
      } : {}),
    }));
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'Weekly ROI calculation unavailable' }, 409);
  }
});

adminReferralRouter.post('/weeks/:weekId/settle', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  try {
    const result = await settleReferralWeek(c.env.DB, {
      weekId: c.req.param('weekId'),
      actorId: auth.actorId,
      settledAt: Math.floor(Date.now() / 1000),
      ...(typeof body?.reason === 'string' ? { reason: body.reason } : {}),
    });
    return c.json(result);
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'Settlement unavailable' }, 409);
  }
});

adminReferralRouter.post('/statements/:statementId/approve', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  if (typeof body?.reason !== 'string') return c.json({ detail: 'Approval reason is required' }, 422);
  try {
    return c.json(await approveSettlementStatement(c.env.DB, {
      statementId: c.req.param('statementId'),
      actorId: auth.actorId,
      reason: body.reason,
      approvedAt: Math.floor(Date.now() / 1000),
    }));
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'Statement approval unavailable' }, 409);
  }
});

adminReferralRouter.post('/beneficiaries', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  if (typeof body?.user_id !== 'string' || !body.details) {
    return c.json({ detail: 'user_id and private beneficiary details are required' }, 422);
  }
  try {
    return c.json(await recordBeneficiary(c.env.DB, {
      userId: body.user_id,
      actorId: auth.actorId,
      details: body.details,
      occurredAt: Math.floor(Date.now() / 1000),
    }), 201);
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'Beneficiary unavailable' }, 409);
  }
});

adminReferralRouter.get('/beneficiaries', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  try {
    return c.json({ beneficiaries: await listBeneficiaries(c.env.DB) });
  } catch {
    return c.json({ detail: 'Beneficiary records unavailable' }, 503);
  }
});

adminReferralRouter.post('/beneficiaries/:beneficiaryId/review', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  if (typeof body?.approved !== 'boolean' || typeof body.reason !== 'string') {
    return c.json({ detail: 'approved and reason are required' }, 422);
  }
  try {
    return c.json(await reviewBeneficiary(c.env.DB, {
      beneficiaryId: c.req.param('beneficiaryId'),
      actorId: auth.actorId,
      approved: body.approved,
      reason: body.reason,
      occurredAt: Math.floor(Date.now() / 1000),
    }));
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'Beneficiary review unavailable' }, 409);
  }
});

adminReferralRouter.post('/statements/:statementId/payout', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  const status = body?.status;
  if (status !== 'paid' && status !== 'failed') {
    return c.json({ detail: 'Payout status must be paid or failed' }, 422);
  }
  try {
    return c.json(await recordSettlementPayout(c.env.DB, {
      statementId: c.req.param('statementId'),
      actorId: auth.actorId,
      status,
      idempotencyKey: String(body?.idempotency_key ?? ''),
      reason: String(body?.reason ?? ''),
      occurredAt: Math.floor(Date.now() / 1000),
      ...(typeof body?.utr_reference === 'string' ? { utrReference: body.utr_reference } : {}),
      ...(typeof body?.provider_reference === 'string'
        ? { providerReference: body.provider_reference }
        : {}),
    }));
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'Payout unavailable' }, 409);
  }
});

adminReferralRouter.post('/payouts/:payoutId/transition', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  const body = await requestBody(c);
  if (!['corrected', 'reversed', 'clawed_back'].includes(String(body?.status))
    || typeof body?.reason !== 'string') {
    return c.json({ detail: 'A correction status and reason are required' }, 422);
  }
  try {
    return c.json(await transitionSettlementPayout(c.env.DB, {
      payoutId: c.req.param('payoutId'),
      actorId: auth.actorId,
      status: body.status as 'corrected' | 'reversed' | 'clawed_back',
      reason: body.reason,
      occurredAt: Math.floor(Date.now() / 1000),
    }));
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'Payout transition unavailable' }, 409);
  }
});

adminReferralRouter.post('/payouts/:payoutId/receipt', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  const form = await c.req.formData();
  const file = form.get('receipt');
  if (!file || typeof file === 'string') {
    return c.json({ detail: 'A receipt file is required' }, 422);
  }
  const upload = file as unknown as {
    name: string;
    type: string;
    size: number;
    arrayBuffer: () => Promise<ArrayBuffer>;
  };
  try {
    validatePrivateReceipt(upload);
    if (upload.size > MAX_RECEIPT_BYTES) return c.json({ detail: 'Receipt is too large' }, 422);
    const bytes = new Uint8Array(await upload.arrayBuffer());
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    const hash = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
    const extension = upload.type === 'application/pdf' ? 'pdf' : upload.type === 'image/png' ? 'png' : 'jpg';
    const objectKey = `private/referral-receipts/${c.req.param('payoutId')}/${hash}.${extension}`;
    await c.env.R2_BUCKET.put(objectKey, bytes, {
      httpMetadata: { contentType: upload.type, cacheControl: 'private, no-store' },
      customMetadata: { sha256: hash, uploadedBy: auth.actorId },
    });
    await c.env.DB.prepare(`
      INSERT INTO referral_payment_receipts
        (id, payout_id, object_key, original_name, content_type, byte_size, sha256, uploaded_by)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `).bind(
      crypto.randomUUID(),
      c.req.param('payoutId'),
      objectKey,
      upload.name,
      upload.type,
      upload.size,
      hash,
      auth.actorId,
    ).run();
    return c.json({ status: 'stored', sha256: hash, private: true }, 201);
  } catch (error) {
    return c.json({ detail: error instanceof Error ? error.message : 'Receipt upload unavailable' }, 422);
  }
});

adminReferralRouter.get('/payouts/:payoutId/receipt', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  const receipt = await c.env.DB.prepare(`
    SELECT object_key, original_name, content_type, sha256
    FROM referral_payment_receipts
    WHERE payout_id = ?
    ORDER BY created_at DESC LIMIT 1
  `).bind(c.req.param('payoutId')).first<{
    object_key: string;
    original_name: string;
    content_type: string;
    sha256: string;
  }>();
  if (!receipt) return c.json({ detail: 'Private receipt not found' }, 404);
  const object = await c.env.R2_BUCKET.get(receipt.object_key);
  if (!object) return c.json({ detail: 'Private receipt object is unavailable' }, 404);
  const headers = new Headers();
  headers.set('Content-Type', receipt.content_type);
  headers.set('Content-Length', String(object.size));
  headers.set('Content-Disposition', `attachment; filename="${receipt.original_name.replace(/[^A-Za-z0-9._-]/g, '_')}"`);
  headers.set('Cache-Control', 'private, no-store');
  headers.set('X-Content-SHA256', receipt.sha256);
  return new Response(object.body, { headers });
});

adminReferralRouter.get('/settlements/audits', async (c) => {
  const auth = await requireReferralCapability(c, REFERRAL_POLICY.access.settlementCapability);
  if (auth instanceof Response) return auth;
  try {
    return c.json({ audits: await settlementAudits(c.env.DB, c.req.query('week_id')) });
  } catch {
    return c.json({ detail: 'Settlement audit history unavailable' }, 503);
  }
});

internalReferralRouter.post('/gate-evidence', async (c) => {
  if (!await internalAuthorized(c)) return c.json({ detail: 'Unauthorized' }, 401);
  const body = await requestBody(c);
  if (!body) return c.json({ detail: 'Evidence body required' }, 422);
  try {
    const evidenceId = await recordReferralGateEvidence(c.env.DB, {
      providerEvidenceId: String(body.provider_evidence_id ?? ''),
      finalizedThroughAt: Number(body.finalized_through_at),
      grossReserveInr: Number(body.gross_reserve_inr),
      outstandingObligationsInr: Number(body.outstanding_obligations_inr),
      qualityEvidenceId: String(body.quality_evidence_id ?? ''),
      qualityMeasuredAt: Number(body.quality_measured_at),
      attributionHealthy: body.attribution_healthy === true,
      deduplicationHealthy: body.deduplication_healthy === true,
      fraudReviewHealthy: body.fraud_review_healthy === true,
      settlementHealthy: body.settlement_healthy === true,
      recordedAt: Number(body.recorded_at),
      expiresAt: Number(body.expires_at),
      ...(body.funded_cap_inr === undefined
        ? {}
        : { fundedCapInr: Number(body.funded_cap_inr) }),
    });
    return c.json({ evidence_id: evidenceId }, 201);
  } catch {
    return c.json({ detail: 'Invalid authoritative referral gate evidence' }, 422);
  }
});

internalReferralRouter.post('/claims/:claimId/mature', async (c) => {
  if (!await internalAuthorized(c)) return c.json({ detail: 'Unauthorized' }, 401);
  const body = await requestBody(c);
  if (!body || !body.quality_evidence || typeof body.quality_evidence !== 'object'
    || Array.isArray(body.quality_evidence)) {
    return c.json({ detail: 'quality_evidence object required' }, 422);
  }
  try {
    const result = await matureReferralClaim(
      c.env.DB,
      c.req.param('claimId'),
      body.quality_evidence as Record<string, unknown>,
    );
    return result.counted
      ? c.json(result)
      : c.json({ ...result, detail: 'Claim is ineligible, already processed, or outside the open accrual window' }, 409);
  } catch (error) {
    if (error instanceof Error && error.message.includes('quality evidence')) {
      return c.json({ detail: error.message }, 422);
    }
    return c.json({ detail: 'Referral maturation unavailable' }, 503);
  }
});

internalReferralRouter.post('/advanced/activate', async (c) => {
  if (!await internalAuthorized(c)) return c.json({ detail: 'Unauthorized' }, 401);
  try {
    const activated = await activateApprovedAdvancedPositions(c.env.DB);
    return c.json({ activated });
  } catch {
    return c.json({ detail: 'Advanced activation unavailable' }, 503);
  }
});