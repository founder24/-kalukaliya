import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { getPlatformProxy } from 'wrangler';

import { REFERRAL_POLICY_VERSION } from '../contracts/referral-policy';
import { signAccessToken, signAdminToken } from '../middleware/auth';
import { adminReferralRouter, internalReferralRouter } from '../routes/referrals';
import type { Env } from '../types';
import {
  advanceReferralLifecycle,
  enforceReferralGateFreshness,
  activateApprovedAdvancedPositions,
  admitReferralInfluencer,
  matureReferralClaim,
  pauseReferralProgram,
  reconcileReferralAccount,
  recordReferralVisit,
  recordReferralGateEvidence,
  referralWeekBounds,
  resumeReferralProgram,
  reviewAdvancedPosition,
  safeReferralDestination,
  weeklyReferralMetrics,
} from './referral-attribution';

const API_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../');
const JWT_SECRET = 'referral-test-jwt-secret-at-least-32-characters';
const ADMIN_SECRET = 'referral-test-admin-secret-at-least-32-characters';
const EDGE_SECRET = 'referral-test-edge-secret-at-least-32-characters';
const TEST_TIME = Math.floor(Date.parse('2026-09-15T06:00:00.000Z') / 1000);

let env: Env;
let disposeProxy: () => Promise<void>;

function migrationStatements(): string[] {
  const directory = path.join(API_ROOT, 'drizzle/migrations');
  return fs.readdirSync(directory)
    .filter(file => file.endsWith('.sql'))
    .sort()
    .flatMap(file => fs.readFileSync(path.join(directory, file), 'utf8').split(';'))
    .map(fragment => fragment
      .split('\n')
      .filter(line => line.trim() && !line.trim().startsWith('--'))
      .join('\n')
      .trim())
    .filter(Boolean);
}

async function resetReferralState(): Promise<void> {
  await env.DB.batch([
    env.DB.prepare('DELETE FROM referral_claim_events'),
    env.DB.prepare('DELETE FROM referral_visit_rate_limits'),
    env.DB.prepare('DELETE FROM referral_gate_evidence'),
    env.DB.prepare('DELETE FROM referral_admission_audits'),
    env.DB.prepare('DELETE FROM referral_advanced_reviews'),
    env.DB.prepare('DELETE FROM referral_program_transitions'),
    env.DB.prepare('DELETE FROM referral_weekly_progress'),
    env.DB.prepare('DELETE FROM referral_weekly_claims'),
    env.DB.prepare(`
      UPDATE referral_advanced_positions
      SET influencer_slot = NULL,
          status = 'available',
          qualified_week_id = NULL,
          qualified_at = NULL,
          reviewed_by = NULL,
          reviewed_at = NULL,
          review_reason = NULL,
          activates_at = NULL,
          updated_at = unixepoch()
    `),
    env.DB.prepare(`
      UPDATE referral_influencer_slots
      SET user_id = NULL,
          referral_code = NULL,
          status = 'available',
          tier = 'basic',
          identity_verified = 0,
          kyc_verified = 0,
          academic_snapshot = NULL,
          eligibility_reviewed_at = NULL,
          admitted_at = NULL,
          inactive_at = NULL,
          suspended_at = NULL,
          removed_at = NULL,
          advanced_effective_at = NULL,
          updated_at = unixepoch()
    `),
    env.DB.prepare('DELETE FROM referral_weeks'),
    env.DB.prepare(`
      UPDATE referral_program_state
      SET state = 'paused',
          pause_effective_at = NULL,
          resumed_at = NULL,
          accrual_generation = 0,
          updated_by = NULL,
          updated_at = unixepoch()
      WHERE id = 'singleton'
    `),
    env.DB.prepare('DELETE FROM users'),
  ]);
  await createUser('referral-audit-operator', 'staff', ['referral:review']);
}

async function createUser(
  id = crypto.randomUUID(),
  role = 'student',
  capabilities: string[] | null = null,
): Promise<string> {
  await env.DB.prepare(`
    INSERT INTO users
      (id, email, role, capabilities, session_valid_after, created_at, updated_at)
    VALUES (?, ?, ?, ?, 0, ?, ?)
  `).bind(
    id,
    `${id}@example.test`,
    role,
    capabilities ? JSON.stringify(capabilities) : null,
    TEST_TIME,
    TEST_TIME,
  ).run();
  return id;
}

async function openTestWeek(at = TEST_TIME): Promise<ReturnType<typeof referralWeekBounds>> {
  const week = referralWeekBounds(at);
  await gateEvidence(at, `open-test-week-${week.key}`);
  await env.DB.batch([
    env.DB.prepare(`
      INSERT INTO referral_weeks
        (id, week_key, starts_at, ends_at, state, policy_version, opened_at, created_at, updated_at)
      VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        state = 'open',
        pause_effective_at = NULL,
        resumed_at = NULL,
        updated_at = excluded.updated_at
    `).bind(
      week.id,
      week.key,
      week.startsAt,
      week.endsAt,
      REFERRAL_POLICY_VERSION,
      at,
      at,
      at,
    ),
    env.DB.prepare(`
      UPDATE referral_program_state
      SET state = 'active',
          pause_effective_at = NULL,
          resumed_at = NULL,
          updated_at = ?
      WHERE id = 'singleton'
    `).bind(at),
  ]);
  return week;
}

async function admitOne(userId = crypto.randomUUID()): Promise<{
  userId: string;
  slotNo: number;
  code: string;
}> {
  await createUser(userId);
  const result = await admitReferralInfluencer(env.DB, {
    userId,
    identityVerified: true,
    kycVerified: true,
    academicSnapshot: { board: 'AHSEC', class: '12', verified_at: TEST_TIME },
    reviewedBy: 'referral-audit-operator',
    reviewReason: 'Verified identity, eligibility, KYC, and academic record.',
    reviewedAt: TEST_TIME,
  });
  if (!result.admitted) throw new Error('Expected test influencer admission');
  return { userId, slotNo: result.slotNo, code: result.referralCode };
}

function visitRequest(code: string, cookie?: string, next = '/library'): Request {
  return new Request(
    `https://api.syrabit.ai/api/v1/referrals/visit/${code}?next=${encodeURIComponent(next)}`,
    cookie ? { headers: { Cookie: cookie } } : {},
  );
}

function cookieHeader(setCookie: string | null): string {
  if (!setCookie) throw new Error('Expected Set-Cookie');
  return setCookie.split(';')[0] ?? '';
}

function qualityEvidence(claimId: string, maturityAt: number, suffix = 'default') {
  return {
    evidence_id: `quality-evidence-${maturityAt}-${suffix}`,
    claim_id: claimId,
    source: 'referral-quality-worker',
    finalized: true,
    fraud_decision: 'pass',
    measured_at_epoch_seconds: maturityAt,
    maturity_at_epoch_seconds: maturityAt,
    quality_score: 0.95,
  };
}

async function gateEvidence(effectiveAt: number, suffix: string): Promise<string> {
  return recordReferralGateEvidence(env.DB, {
    providerEvidenceId: `adsense:${effectiveAt}-${suffix}`,
    finalizedThroughAt: effectiveAt,
    grossReserveInr: 50_000,
    outstandingObligationsInr: 13_000,
    qualityEvidenceId: `quality-gate-${effectiveAt}-${suffix}`,
    qualityMeasuredAt: effectiveAt,
    attributionHealthy: true,
    deduplicationHealthy: true,
    fraudReviewHealthy: true,
    settlementHealthy: true,
    recordedAt: effectiveAt,
    expiresAt: effectiveAt + 3_600,
  });
}

async function createBrowserClaim(
  code: string,
  at: number,
): Promise<{ claimId: string; cookie: string }> {
  const first = await recordReferralVisit(
    env.DB,
    visitRequest(code),
    code,
    EDGE_SECRET,
    at,
  );
  const cookie = cookieHeader(first.setCookie);
  const repeat = await recordReferralVisit(
    env.DB,
    visitRequest(code, cookie),
    code,
    EDGE_SECRET,
    at + 1,
  );
  if (!repeat.claimId) throw new Error('Expected browser claim');
  return { claimId: repeat.claimId, cookie };
}

beforeAll(async () => {
  const proxy = await getPlatformProxy<Env>({
    configPath: path.join(API_ROOT, 'wrangler.toml'),
    remoteBindings: false,
    persist: false,
  });
  disposeProxy = proxy.dispose;
  env = {
    ...proxy.env,
    JWT_SECRET,
    ADMIN_JWT_SECRET: ADMIN_SECRET,
    EDGE_SHARED_SECRET: EDGE_SECRET,
    ALLOWED_ORIGINS: 'https://syrabit.ai',
    APP_ENV: 'test',
    REFERRAL_PROGRAM_RUNTIME_ENABLED: 'true',
  };
  for (const statement of migrationStatements()) {
    await env.DB.prepare(statement).run();
  }
}, 60_000);

beforeEach(async () => {
  await resetReferralState();
});

afterAll(async () => {
  await disposeProxy?.();
});

describe('weekly referral attribution ledger', () => {
  it('uses Monday-to-Monday Asia/Kolkata week boundaries', () => {
    const week = referralWeekBounds(
      Math.floor(Date.parse('2026-09-14T18:29:59.000Z') / 1000),
    );
    expect(week.key).toBe('2026-09-14');
    expect(new Date(week.startsAt * 1000).toISOString()).toBe('2026-09-13T18:30:00.000Z');
    expect(new Date(week.endsAt * 1000).toISOString()).toBe('2026-09-20T18:30:00.000Z');
    expect(referralWeekBounds(week.endsAt).key).toBe('2026-09-21');
  });

  it('opens from migration defaults and rolls over without test-only week seeding', async () => {
    const firstEvidence = await gateEvidence(TEST_TIME, 'first-open');
    const first = await advanceReferralLifecycle(env.DB, {
      actorId: 'policy-operator',
      effectiveAt: TEST_TIME,
      evidenceId: firstEvidence,
    });
    expect(first).toMatchObject({
      opened: true,
      alreadyOpen: false,
      activatedAdvancedPositions: 0,
      reasons: [],
    });
    const firstState = await env.DB.prepare(`
      SELECT state, accrual_generation FROM referral_weeks WHERE id = ?
    `).bind(first.week.id).first<{ state: string; accrual_generation: number }>();
    expect(firstState).toEqual({ state: 'open', accrual_generation: 1 });

    const nextAt = first.week.endsAt + 60;
    const secondEvidence = await gateEvidence(nextAt, 'rollover');
    const second = await advanceReferralLifecycle(env.DB, {
      actorId: 'policy-operator',
      effectiveAt: nextAt,
      evidenceId: secondEvidence,
    });
    expect(second).toMatchObject({ opened: true, reasons: [] });
    const weeks = await env.DB.prepare(`
      SELECT id, state, accrual_generation
      FROM referral_weeks ORDER BY starts_at
    `).all<{
      id: string;
      state: string;
      accrual_generation: number;
    }>();
    expect(weeks.results).toEqual([
      { id: first.week.id, state: 'finalized', accrual_generation: 1 },
      { id: second.week.id, state: 'open', accrual_generation: 2 },
    ]);
    const program = await env.DB.prepare(`
      SELECT state, accrual_generation FROM referral_program_state
      WHERE id = 'singleton'
    `).first<{ state: string; accrual_generation: number }>();
    expect(program).toEqual({ state: 'active', accrual_generation: 2 });
  });

  it('fails closed when week opening cites fabricated authoritative evidence', async () => {
    const result = await advanceReferralLifecycle(env.DB, {
      actorId: 'policy-operator',
      effectiveAt: TEST_TIME,
      evidenceId: 'fabricated-client-assertion',
    });
    expect(result).toMatchObject({
      opened: false,
      reasons: ['authoritative-evidence-not-found'],
    });
  });

  it('atomically pauses on failing midweek evidence and retains its audit record', async () => {
    await openTestWeek();
    const influencer = await admitOne();
    const initial = await createBrowserClaim(influencer.code, TEST_TIME);
    const evidenceId = await recordReferralGateEvidence(env.DB, {
      providerEvidenceId: 'adsense:midweek-failure-001',
      finalizedThroughAt: TEST_TIME + 10,
      grossReserveInr: 36_999,
      outstandingObligationsInr: 0,
      qualityEvidenceId: 'quality-midweek-failure-001',
      qualityMeasuredAt: TEST_TIME + 10,
      attributionHealthy: true,
      deduplicationHealthy: true,
      fraudReviewHealthy: true,
      settlementHealthy: true,
      recordedAt: TEST_TIME + 10,
      expiresAt: TEST_TIME + 70,
    });
    const laterVisit = await recordReferralVisit(
      env.DB,
      visitRequest(influencer.code, initial.cookie),
      influencer.code,
      EDGE_SECRET,
      TEST_TIME + 11,
    );
    expect(laterVisit.attribution).toBe('accrual-disabled');
    const program = await env.DB.prepare(`
      SELECT state, pause_effective_at FROM referral_program_state WHERE id = 'singleton'
    `).first<{ state: string; pause_effective_at: number }>();
    expect(program).toEqual({ state: 'paused', pause_effective_at: TEST_TIME + 10 });
    const retained = await env.DB.prepare(`
      SELECT gross_reserve_inr, outstanding_obligations_inr, expires_at
      FROM referral_gate_evidence WHERE id = ?
    `).bind(evidenceId).first<Record<string, number>>();
    expect(retained).toEqual({
      gross_reserve_inr: 36_999,
      outstanding_obligations_inr: 0,
      expires_at: TEST_TIME + 70,
    });
    const transition = await env.DB.prepare(`
      SELECT gate_evidence FROM referral_program_transitions
      WHERE transition = 'pause' AND effective_at = ?
    `).bind(TEST_TIME + 10).first<{ gate_evidence: string }>();
    expect(transition?.gate_evidence).toContain('"grossReserveInr":36999');
  });

  it('rejects external, protocol-relative, backslash, control, and malformed redirects', () => {
    expect(safeReferralDestination('/library?subject=physics')).toBe('/library?subject=physics');
    for (const unsafe of [
      'https://evil.example/phish',
      '//evil.example/phish',
      '/\\evil.example',
      '/library%0d%0aSet-Cookie:bad=1',
      '%',
    ]) {
      expect(safeReferralDestination(unsafe)).toBe('/library');
    }
  });

  it('atomically admits exactly 100 eligible influencers and gives each one code', async () => {
    const userIds = await Promise.all(
      Array.from({ length: 101 }, (_, index) => createUser(`admission-${index}`)),
    );
    const admissions = await Promise.all(userIds.map(userId => admitReferralInfluencer(env.DB, {
      userId,
      identityVerified: true,
      kycVerified: true,
      academicSnapshot: { board: 'AHSEC', user_id: userId },
      reviewedBy: 'referral-audit-operator',
      reviewReason: 'Verified identity, eligibility, KYC, and academic record.',
      reviewedAt: TEST_TIME,
    })));
    expect(admissions.filter(result => result.admitted)).toHaveLength(100);
    expect(admissions.filter(result => !result.admitted)).toHaveLength(1);
    const audits = await env.DB.prepare(`
      SELECT COUNT(*) AS count, MIN(actor_id) AS actor_id
      FROM referral_admission_audits
    `).first<{ count: number; actor_id: string }>();
    expect(audits).toEqual({
      count: 100,
      actor_id: 'referral-audit-operator',
    });

    const rows = await env.DB.prepare(`
      SELECT COUNT(*) AS total,
             COUNT(DISTINCT user_id) AS users,
             COUNT(DISTINCT referral_code) AS codes,
             MIN(length(referral_code)) AS min_code_length
      FROM referral_influencer_slots
      WHERE status = 'active'
    `).first<Record<string, number>>();
    expect(rows).toMatchObject({
      total: 100,
      users: 100,
      codes: 100,
      min_code_length: 32,
    });

    await env.DB.prepare(`
      UPDATE referral_influencer_slots
      SET user_id = NULL, referral_code = NULL, status = 'available', updated_at = ?
      WHERE slot_no = 1
    `).bind(TEST_TIME + 1).run();
    const rejectedIndex = admissions.findIndex(result => !result.admitted);
    const replacement = await admitReferralInfluencer(env.DB, {
      userId: userIds[rejectedIndex]!,
      identityVerified: true,
      kycVerified: true,
      academicSnapshot: { board: 'AHSEC', replacement: true },
      reviewedBy: 'referral-audit-operator',
      reviewReason: 'Verified replacement identity, KYC, and academic eligibility.',
      reviewedAt: TEST_TIME + 2,
    });
    expect(replacement).toMatchObject({ admitted: true, slotNo: 1 });
    const slotAudits = await env.DB.prepare(`
      SELECT COUNT(*) AS count FROM referral_admission_audits
      WHERE influencer_slot = 1
    `).first<{ count: number }>();
    expect(slotAudits?.count).toBe(2);
  });

  it('globally credits one weekly browser identity to the first influencer only', async () => {
    const week = await openTestWeek();
    const firstInfluencer = await admitOne();
    const competitor = await admitOne();

    const first = await recordReferralVisit(
      env.DB,
      visitRequest(firstInfluencer.code),
      firstInfluencer.code,
      EDGE_SECRET,
      TEST_TIME,
    );
    expect(first.attribution).toBe('credited');
    const cookie = cookieHeader(first.setCookie);

    const repeat = await recordReferralVisit(
      env.DB,
      visitRequest(firstInfluencer.code, cookie),
      firstInfluencer.code,
      EDGE_SECRET,
      TEST_TIME + 1,
    );
    const competing = await recordReferralVisit(
      env.DB,
      visitRequest(competitor.code, cookie),
      competitor.code,
      EDGE_SECRET,
      TEST_TIME + 2,
    );
    expect(repeat.attribution).toBe('repeat');
    expect(competing.attribution).toBe('competing-influencer');
    expect(new Set([first.claimId, repeat.claimId, competing.claimId])).toEqual(new Set([first.claimId]));

    const claim = await env.DB.prepare(`
      SELECT credited_influencer_slot, identity_confidence, event_count
      FROM referral_weekly_claims WHERE week_id = ?
    `).bind(week.id).first<Record<string, number | string>>();
    expect(claim).toMatchObject({
      credited_influencer_slot: firstInfluencer.slotNo,
      identity_confidence: 'browser',
      event_count: 3,
    });
  });

  it('keeps missing and tampered cookies low-confidence and non-payable', async () => {
    await openTestWeek();
    const influencer = await admitOne();
    const first = await recordReferralVisit(
      env.DB,
      visitRequest(influencer.code),
      influencer.code,
      EDGE_SECRET,
      TEST_TIME,
    );
    const originalCookie = cookieHeader(first.setCookie);
    const tamperedCookie = `${originalCookie.slice(0, -1)}x`;
    const tampered = await recordReferralVisit(
      env.DB,
      visitRequest(influencer.code, tamperedCookie),
      influencer.code,
      EDGE_SECRET,
      TEST_TIME + 1,
    );

    expect(tampered.setCookie).toContain('syrabit_referral_identity=');
    const rows = await env.DB.prepare(`
      SELECT id, identity_confidence FROM referral_weekly_claims ORDER BY first_seen_at, id
    `).all<{ id: string; identity_confidence: string }>();
    expect(rows.results).toHaveLength(2);
    expect(rows.results.every(row => row.identity_confidence === 'low')).toBe(true);
    for (const row of rows.results) {
      await expect(matureReferralClaim(
        env.DB,
        row.id,
        qualityEvidence(row.id, TEST_TIME + 2, row.id),
        TEST_TIME + 2,
      ))
        .resolves.toMatchObject({ counted: false });
    }
  });

  it('bounds missing-cookie claim writes per network and influencer', async () => {
    await openTestWeek();
    const influencer = await admitOne();
    const results = [];
    for (let index = 0; index < 31; index += 1) {
      const request = new Request(
        `https://www.syrabit.com/api/v1/referrals/visit/${influencer.code}`,
        { headers: { 'CF-Connecting-IP': '198.51.100.25' } },
      );
      results.push(await recordReferralVisit(
        env.DB,
        request,
        influencer.code,
        EDGE_SECRET,
        TEST_TIME + index,
      ));
    }
    expect(results.slice(0, 30).every(result => result.attribution === 'credited')).toBe(true);
    expect(results[30]).toMatchObject({
      attribution: 'accrual-disabled',
      claimId: null,
    });
    const claims = await env.DB.prepare(`
      SELECT COUNT(*) AS count FROM referral_weekly_claims
      WHERE credited_influencer_slot = ?
    `).bind(influencer.slotNo).first<{ count: number }>();
    expect(claims?.count).toBe(30);
  });

  it('reconciles a later account once and rejects its duplicate browser claim', async () => {
    await openTestWeek();
    const influencer = await admitOne();
    const accountId = await createUser();
    const first = await createBrowserClaim(influencer.code, TEST_TIME);
    const second = await createBrowserClaim(influencer.code, TEST_TIME + 10);
    await matureReferralClaim(
      env.DB,
      first.claimId,
      qualityEvidence(first.claimId, TEST_TIME + 12, 'first-account'),
      TEST_TIME + 12,
    );
    await matureReferralClaim(
      env.DB,
      second.claimId,
      qualityEvidence(second.claimId, TEST_TIME + 13, 'second-account'),
      TEST_TIME + 13,
    );

    await expect(reconcileReferralAccount(
      env.DB,
      visitRequest(influencer.code, first.cookie),
      accountId,
      EDGE_SECRET,
      TEST_TIME + 20,
    )).resolves.toBe('reconciled');
    await expect(reconcileReferralAccount(
      env.DB,
      visitRequest(influencer.code, second.cookie),
      accountId,
      EDGE_SECRET,
      TEST_TIME + 21,
    )).resolves.toBe('duplicate-account');

    const claims = await env.DB.prepare(`
      SELECT state, account_id, rejection_reason
      FROM referral_weekly_claims ORDER BY first_seen_at
    `).all<Record<string, string | null>>();
    expect(claims.results).toEqual([
      expect.objectContaining({ state: 'mature', account_id: accountId }),
      expect.objectContaining({ state: 'rejected', rejection_reason: 'duplicate-account' }),
    ]);
    const progress = await env.DB.prepare(`
      SELECT mature_verified_count FROM referral_weekly_progress
      WHERE influencer_slot = ?
    `).bind(influencer.slotNo).first<{ mature_verified_count: number }>();
    expect(progress?.mature_verified_count).toBe(1);
  });

  it('counts a mature claim exactly once across retries', async () => {
    const week = await openTestWeek();
    const influencer = await admitOne();
    const { claimId } = await createBrowserClaim(influencer.code, TEST_TIME);
    const results = await Promise.all(Array.from(
      { length: 12 },
      () => matureReferralClaim(
        env.DB,
        claimId,
        qualityEvidence(claimId, TEST_TIME + 120, 'retry'),
        TEST_TIME + 120,
      ),
    ));
    expect(results.filter(result => result.counted)).toHaveLength(1);

    const progress = await env.DB.prepare(`
      SELECT mature_verified_count, unique_browser_count
      FROM referral_weekly_progress
      WHERE week_id = ? AND influencer_slot = ?
    `).bind(week.id, influencer.slotNo).first<Record<string, number>>();
    expect(progress).toMatchObject({
      mature_verified_count: 1,
      unique_browser_count: 1,
    });
  });

  it('rejects unfinalized or untimestamped quality evidence', async () => {
    await openTestWeek();
    const influencer = await admitOne();
    const { claimId } = await createBrowserClaim(influencer.code, TEST_TIME);
    await expect(matureReferralClaim(
      env.DB,
      claimId,
      { fraud_score: 0 },
      TEST_TIME + 20,
    )).rejects.toThrow('Finalized, fresh, passing referral quality evidence is required');
    const claim = await env.DB.prepare(`
      SELECT state, progress_counted FROM referral_weekly_claims WHERE id = ?
    `).bind(claimId).first<{ state: string; progress_counted: number }>();
    expect(claim).toEqual({ state: 'pending', progress_counted: 0 });
  });

  it('caps authoritative basic reward-eligible progress at 100', async () => {
    const week = await openTestWeek();
    const influencer = await admitOne();
    await env.DB.prepare(`
      INSERT INTO referral_weekly_progress
        (id, week_id, influencer_slot, mature_verified_count,
         reward_eligible_count, policy_version, updated_at)
      VALUES (?, ?, ?, 99, 99, ?, ?)
    `).bind(
      `${week.id}:${influencer.slotNo}`,
      week.id,
      influencer.slotNo,
      REFERRAL_POLICY_VERSION,
      TEST_TIME,
    ).run();
    const first = await createBrowserClaim(influencer.code, TEST_TIME);
    const second = await createBrowserClaim(influencer.code, TEST_TIME + 10);
    await matureReferralClaim(
      env.DB,
      first.claimId,
      qualityEvidence(first.claimId, TEST_TIME + 30, 'cap-one'),
      TEST_TIME + 30,
    );
    await matureReferralClaim(
      env.DB,
      second.claimId,
      qualityEvidence(second.claimId, TEST_TIME + 31, 'cap-two'),
      TEST_TIME + 31,
    );
    const progress = await env.DB.prepare(`
      SELECT mature_verified_count, reward_eligible_count
      FROM referral_weekly_progress WHERE week_id = ? AND influencer_slot = ?
    `).bind(week.id, influencer.slotNo).first<Record<string, number>>();
    expect(progress).toMatchObject({
      mature_verified_count: 101,
      reward_eligible_count: 100,
    });
  });

  it('enforces the pause cutoff while links keep redirecting and resume starts a new window', async () => {
    const week = await openTestWeek();
    const influencer = await admitOne();
    const beforePause = await createBrowserClaim(influencer.code, TEST_TIME);
    await expect(matureReferralClaim(
      env.DB,
      beforePause.claimId,
      qualityEvidence(beforePause.claimId, TEST_TIME + 5, 'mature-before-pause'),
      TEST_TIME + 5,
    )).resolves.toMatchObject({ counted: true });
    await pauseReferralProgram(
      env.DB,
      'policy-operator',
      'Attribution controls require an immediate pause.',
      TEST_TIME + 30,
    );
    const pauseAudit = await env.DB.prepare(`
      SELECT actor_id, reason, effective_at, policy_version
      FROM referral_program_transitions WHERE transition = 'pause'
    `).first<Record<string, string | number>>();
    expect(pauseAudit).toEqual({
      actor_id: 'policy-operator',
      reason: 'Attribution controls require an immediate pause.',
      effective_at: TEST_TIME + 30,
      policy_version: REFERRAL_POLICY_VERSION,
    });

    const pausedVisit = await recordReferralVisit(
      env.DB,
      visitRequest(influencer.code, beforePause.cookie, '/library'),
      influencer.code,
      EDGE_SECRET,
      TEST_TIME + 31,
    );
    expect(pausedVisit).toMatchObject({
      destination: '/library',
      attribution: 'accrual-disabled',
      claimId: null,
    });
    await expect(matureReferralClaim(
      env.DB,
      beforePause.claimId,
      qualityEvidence(beforePause.claimId, TEST_TIME + 31, 'paused'),
      TEST_TIME + 31,
    )).resolves.toMatchObject({ counted: false });

    const resumeEvidence = await gateEvidence(TEST_TIME + 60, 'resume');
    await expect(resumeReferralProgram(env.DB, {
      actorId: 'policy-operator',
      effectiveAt: TEST_TIME + 60,
      evidenceId: resumeEvidence,
    })).resolves.toEqual({ resumed: true, reasons: [] });
    await expect(matureReferralClaim(
      env.DB,
      beforePause.claimId,
      qualityEvidence(beforePause.claimId, TEST_TIME + 61, 'pre-pause-after-resume'),
      TEST_TIME + 61,
    )).resolves.toMatchObject({ counted: false });
    const resumed = await recordReferralVisit(
      env.DB,
      visitRequest(influencer.code, beforePause.cookie),
      influencer.code,
      EDGE_SECRET,
      TEST_TIME + 61,
    );
    expect(resumed.attribution).toBe('repeat');
    expect(resumed.claimId).toBe(beforePause.claimId);
    const generations = await env.DB.prepare(`
      SELECT accrual_generation
      FROM referral_weekly_claims
      WHERE week_id = ?
      ORDER BY accrual_generation
    `).bind(week.id).all<{ accrual_generation: number }>();
    expect(generations.results).toEqual([
      { accrual_generation: 0 },
    ]);
    const progress = await env.DB.prepare(`
      SELECT mature_verified_count FROM referral_weekly_progress
      WHERE week_id = ? AND influencer_slot = ?
    `).bind(week.id, influencer.slotNo).first<{ mature_verified_count: number }>();
    expect(progress?.mature_verified_count).toBe(1);
  });

  it('finalizes authoritative pre-cutoff maturity evidence after the pause', async () => {
    const week = await openTestWeek();
    const influencer = await admitOne();
    const claim = await createBrowserClaim(influencer.code, TEST_TIME);
    await pauseReferralProgram(
      env.DB,
      'policy-operator',
      'Attribution controls require a deterministic cutoff.',
      TEST_TIME + 30,
    );
    await expect(matureReferralClaim(
      env.DB,
      claim.claimId,
      qualityEvidence(claim.claimId, TEST_TIME + 20, 'delayed-pre-cutoff'),
      TEST_TIME + 40,
    )).resolves.toMatchObject({ counted: true });
    const progress = await env.DB.prepare(`
      SELECT mature_verified_count FROM referral_weekly_progress
      WHERE week_id = ? AND influencer_slot = ?
    `).bind(week.id, influencer.slotNo).first<{ mature_verified_count: number }>();
    expect(progress?.mature_verified_count).toBe(1);
  });

  it('moves a pending weekly claim into the resumed generation on a real repeat visit', async () => {
    const week = await openTestWeek();
    const influencer = await admitOne();
    const claim = await createBrowserClaim(influencer.code, TEST_TIME);
    await pauseReferralProgram(
      env.DB,
      'policy-operator',
      'Temporary control pause before a verified resume.',
      TEST_TIME + 30,
    );
    const evidenceId = await gateEvidence(TEST_TIME + 60, 'pending-resume');
    await expect(resumeReferralProgram(env.DB, {
      actorId: 'policy-operator',
      effectiveAt: TEST_TIME + 60,
      evidenceId,
    })).resolves.toEqual({ resumed: true, reasons: [] });
    const repeat = await recordReferralVisit(
      env.DB,
      visitRequest(influencer.code, claim.cookie),
      influencer.code,
      EDGE_SECRET,
      TEST_TIME + 61,
    );
    expect(repeat).toMatchObject({ attribution: 'repeat', claimId: claim.claimId });
    const moved = await env.DB.prepare(`
      SELECT accrual_generation, first_seen_at FROM referral_weekly_claims WHERE id = ?
    `).bind(claim.claimId).first<{ accrual_generation: number; first_seen_at: number }>();
    expect(moved).toEqual({ accrual_generation: 1, first_seen_at: TEST_TIME + 61 });
    await expect(matureReferralClaim(
      env.DB,
      claim.claimId,
      qualityEvidence(claim.claimId, TEST_TIME + 62, 'post-resume'),
      TEST_TIME + 62,
    )).resolves.toMatchObject({ counted: true });
    const rows = await env.DB.prepare(`
      SELECT COUNT(*) AS count FROM referral_weekly_claims WHERE week_id = ?
    `).bind(week.id).first<{ count: number }>();
    expect(rows?.count).toBe(1);
  });

  it('blocks maturation after gate expiry and records the automatic stale-evidence pause', async () => {
    await openTestWeek();
    const influencer = await admitOne();
    const claim = await createBrowserClaim(influencer.code, TEST_TIME);
    const afterExpiry = TEST_TIME + 3_601;
    await expect(matureReferralClaim(
      env.DB,
      claim.claimId,
      qualityEvidence(claim.claimId, afterExpiry, 'expired-gate'),
      afterExpiry,
    )).resolves.toMatchObject({ counted: false });
    await expect(enforceReferralGateFreshness(env.DB, afterExpiry)).resolves.toBe(true);
    const state = await env.DB.prepare(`
      SELECT state, pause_effective_at FROM referral_program_state WHERE id = 'singleton'
    `).first<{ state: string; pause_effective_at: number }>();
    expect(state).toEqual({ state: 'paused', pause_effective_at: afterExpiry });
    const progress = await env.DB.prepare(`
      SELECT COUNT(*) AS count FROM referral_weekly_progress
      WHERE week_id = ? AND influencer_slot = ?
    `).bind(referralWeekBounds(TEST_TIME).id, influencer.slotNo)
      .first<{ count: number }>();
    expect(progress?.count).toBe(0);
  });

  it('records the first accepted 500th claim once under a 499/500 race', async () => {
    const week = await openTestWeek();
    const influencer = await admitOne();
    await env.DB.prepare(`
      INSERT INTO referral_weekly_progress
        (id, week_id, influencer_slot, mature_verified_count, policy_version, updated_at)
      VALUES (?, ?, ?, 499, ?, ?)
    `).bind(
      `${week.id}:${influencer.slotNo}`,
      week.id,
      influencer.slotNo,
      REFERRAL_POLICY_VERSION,
      TEST_TIME,
    ).run();
    const first = await createBrowserClaim(influencer.code, TEST_TIME);
    const second = await createBrowserClaim(influencer.code, TEST_TIME + 10);

    const results = await Promise.all([
      matureReferralClaim(
        env.DB,
        first.claimId,
        qualityEvidence(first.claimId, TEST_TIME + 100, 'race-one'),
        TEST_TIME + 100,
      ),
      matureReferralClaim(
        env.DB,
        second.claimId,
        qualityEvidence(second.claimId, TEST_TIME + 101, 'race-two'),
        TEST_TIME + 101,
      ),
    ]);
    const claimStates = await env.DB.prepare(`
      SELECT id, state, maturity_token, progress_counted, rejection_reason
      FROM referral_weekly_claims
      WHERE id IN (?, ?)
      ORDER BY id
    `).bind(first.claimId, second.claimId).all<Record<string, unknown>>();
    expect(
      results.every(result => result.counted),
      JSON.stringify({ results, claimStates: claimStates.results }),
    ).toBe(true);

    const progress = await env.DB.prepare(`
      SELECT mature_verified_count, provisional_qualified_at
      FROM referral_weekly_progress
      WHERE week_id = ? AND influencer_slot = ?
    `).bind(week.id, influencer.slotNo).first<Record<string, number>>();
    expect(progress?.mature_verified_count).toBe(501);
    expect([TEST_TIME + 100, TEST_TIME + 101]).toContain(progress?.provisional_qualified_at);
    const positions = await env.DB.prepare(`
      SELECT
        COUNT(*) AS count,
        SUM(CASE WHEN influencer_slot IS NULL THEN 1 ELSE 0 END) AS empty_count
      FROM referral_advanced_positions
      WHERE influencer_slot = ? AND status = 'provisional'
    `).bind(influencer.slotNo).first<{ count: number; empty_count: number }>();
    expect(positions?.count).toBe(1);
    expect(positions?.empty_count).toBe(0);
  });

  it('reverses a matured duplicate account and withdraws invalid qualification', async () => {
    const week = await openTestWeek();
    const influencer = await admitOne();
    await env.DB.prepare(`
      INSERT INTO referral_weekly_progress
        (id, week_id, influencer_slot, mature_verified_count,
         reward_eligible_count, policy_version, updated_at)
      VALUES (?, ?, ?, 498, 100, ?, ?)
    `).bind(
      `${week.id}:${influencer.slotNo}`,
      week.id,
      influencer.slotNo,
      REFERRAL_POLICY_VERSION,
      TEST_TIME,
    ).run();
    const first = await createBrowserClaim(influencer.code, TEST_TIME);
    const second = await createBrowserClaim(influencer.code, TEST_TIME + 10);
    await matureReferralClaim(
      env.DB,
      first.claimId,
      qualityEvidence(first.claimId, TEST_TIME + 30, 'reversal-one'),
      TEST_TIME + 30,
    );
    await matureReferralClaim(
      env.DB,
      second.claimId,
      qualityEvidence(second.claimId, TEST_TIME + 31, 'reversal-two'),
      TEST_TIME + 31,
    );
    const accountId = await createUser();
    await reconcileReferralAccount(
      env.DB,
      visitRequest(influencer.code, first.cookie),
      accountId,
      EDGE_SECRET,
      TEST_TIME + 40,
    );
    await reconcileReferralAccount(
      env.DB,
      visitRequest(influencer.code, second.cookie),
      accountId,
      EDGE_SECRET,
      TEST_TIME + 41,
    );
    const progress = await env.DB.prepare(`
      SELECT mature_verified_count, provisional_qualified_at
      FROM referral_weekly_progress WHERE week_id = ? AND influencer_slot = ?
    `).bind(week.id, influencer.slotNo).first<{
      mature_verified_count: number;
      provisional_qualified_at: number | null;
    }>();
    expect(progress).toEqual({
      mature_verified_count: 499,
      provisional_qualified_at: null,
    });
    const positions = await env.DB.prepare(`
      SELECT COUNT(*) AS count FROM referral_advanced_positions
      WHERE influencer_slot = ?
    `).bind(influencer.slotNo).first<{ count: number }>();
    expect(positions?.count).toBe(0);
  });

  it('does not withdraw an advanced position earned in an earlier week', async () => {
    const week = await openTestWeek();
    const influencer = await admitOne();
    const priorWeek = referralWeekBounds(week.startsAt - 1);
    await env.DB.batch([
      env.DB.prepare(`
        INSERT INTO referral_weeks
          (id, week_key, starts_at, ends_at, state, policy_version, opened_at)
        VALUES (?, ?, ?, ?, 'finalized', ?, ?)
      `).bind(
        priorWeek.id,
        priorWeek.key,
        priorWeek.startsAt,
        priorWeek.endsAt,
        REFERRAL_POLICY_VERSION,
        priorWeek.startsAt,
      ),
      env.DB.prepare(`
        UPDATE referral_influencer_slots
        SET tier = 'advanced', advanced_effective_at = ?, updated_at = ?
        WHERE slot_no = ?
      `).bind(week.startsAt, TEST_TIME, influencer.slotNo),
      env.DB.prepare(`
        UPDATE referral_advanced_positions
        SET influencer_slot = ?, status = 'active',
            qualified_week_id = ?, qualified_at = ?, activates_at = ?,
            updated_at = ?
        WHERE position_no = 1
      `).bind(
        influencer.slotNo,
        priorWeek.id,
        priorWeek.endsAt - 60,
        week.startsAt,
        TEST_TIME,
      ),
      env.DB.prepare(`
        INSERT INTO referral_weekly_progress
          (id, week_id, influencer_slot, mature_verified_count,
           reward_eligible_count, policy_version, updated_at)
        VALUES (?, ?, ?, 498, 498, ?, ?)
      `).bind(
        `${week.id}:${influencer.slotNo}`,
        week.id,
        influencer.slotNo,
        REFERRAL_POLICY_VERSION,
        TEST_TIME,
      ),
    ]);
    const first = await createBrowserClaim(influencer.code, TEST_TIME);
    const second = await createBrowserClaim(influencer.code, TEST_TIME + 10);
    await matureReferralClaim(
      env.DB,
      first.claimId,
      qualityEvidence(first.claimId, TEST_TIME + 30, 'prior-position-one'),
      TEST_TIME + 30,
    );
    await matureReferralClaim(
      env.DB,
      second.claimId,
      qualityEvidence(second.claimId, TEST_TIME + 31, 'prior-position-two'),
      TEST_TIME + 31,
    );
    const accountId = await createUser();
    await reconcileReferralAccount(
      env.DB,
      visitRequest(influencer.code, first.cookie),
      accountId,
      EDGE_SECRET,
      TEST_TIME + 40,
    );
    await reconcileReferralAccount(
      env.DB,
      visitRequest(influencer.code, second.cookie),
      accountId,
      EDGE_SECRET,
      TEST_TIME + 41,
    );

    const position = await env.DB.prepare(`
      SELECT qualified_week_id, status
      FROM referral_advanced_positions WHERE position_no = 1
    `).first<{ qualified_week_id: string; status: string }>();
    const slot = await env.DB.prepare(`
      SELECT tier FROM referral_influencer_slots WHERE slot_no = ?
    `).bind(influencer.slotNo).first<{ tier: string }>();
    expect(position).toEqual({
      qualified_week_id: priorWeek.id,
      status: 'active',
    });
    expect(slot?.tier).toBe('advanced');
  });

  it('reserves no more than 30 advanced positions in a 30/31 race', async () => {
    const week = await openTestWeek();
    const candidates: Array<{ slotNo: number; claimId: string }> = [];
    for (let index = 0; index < 31; index += 1) {
      const influencer = await admitOne(`qualification-${index}`);
      await env.DB.prepare(`
        INSERT INTO referral_weekly_progress
          (id, week_id, influencer_slot, mature_verified_count, policy_version, updated_at)
        VALUES (?, ?, ?, 499, ?, ?)
      `).bind(
        `${week.id}:${influencer.slotNo}`,
        week.id,
        influencer.slotNo,
        REFERRAL_POLICY_VERSION,
        TEST_TIME,
      ).run();
      const claim = await createBrowserClaim(influencer.code, TEST_TIME + index * 3);
      candidates.push({ slotNo: influencer.slotNo, claimId: claim.claimId });
    }

    const results = await Promise.all(candidates.map((candidate, index) => matureReferralClaim(
      env.DB,
      candidate.claimId,
      qualityEvidence(
        candidate.claimId,
        TEST_TIME + 1_000 + (30 - index),
        `position-${index}`,
      ),
      TEST_TIME + 1_000 + (30 - index),
    )));
    expect(results.filter(result => result.counted)).toHaveLength(31);
    const positionCount = await env.DB.prepare(`
      SELECT COUNT(*) AS count
      FROM referral_advanced_positions
      WHERE status = 'provisional'
    `).first<{ count: number }>();
    const qualifiedCount = await env.DB.prepare(`
      SELECT COUNT(*) AS count
      FROM referral_weekly_progress
      WHERE provisional_qualified_at IS NOT NULL
    `).first<{ count: number }>();
    expect(positionCount?.count).toBe(30);
    expect(qualifiedCount?.count).toBe(31);
    const emptyProvisional = await env.DB.prepare(`
      SELECT COUNT(*) AS count FROM referral_advanced_positions
      WHERE status = 'provisional' AND influencer_slot IS NULL
    `).first<{ count: number }>();
    expect(emptyProvisional?.count).toBe(0);
    const winners = await env.DB.prepare(`
      SELECT influencer_slot
      FROM referral_advanced_positions
      WHERE status = 'provisional'
      ORDER BY qualified_at, influencer_slot
    `).all<{ influencer_slot: number }>();
    expect(winners.results.map(row => row.influencer_slot)).toEqual(
      candidates.slice(1).reverse().map(candidate => candidate.slotNo),
    );
  });

  it('promotes the earliest qualified waitlist candidate after rejection', async () => {
    const week = await openTestWeek();
    for (let slot = 1; slot <= 31; slot += 1) {
      await createUser(`promotion-${slot}`);
      const admitted = await admitReferralInfluencer(env.DB, {
        userId: `promotion-${slot}`,
        identityVerified: true,
        kycVerified: true,
        academicSnapshot: { slot },
        reviewedBy: 'referral-audit-operator',
        reviewReason: 'Verified identity, eligibility, KYC, and academic record.',
        reviewedAt: TEST_TIME,
      });
      if (!admitted.admitted) throw new Error('Expected candidate admission');
      await env.DB.prepare(`
        INSERT INTO referral_weekly_progress
          (id, week_id, influencer_slot, mature_verified_count,
           provisional_qualified_at, policy_version, updated_at)
        VALUES (?, ?, ?, 500, ?, ?, ?)
      `).bind(
        `${week.id}:${admitted.slotNo}`,
        week.id,
        admitted.slotNo,
        TEST_TIME + slot,
        REFERRAL_POLICY_VERSION,
        TEST_TIME + slot,
      ).run();
      if (slot <= 30) {
        await env.DB.prepare(`
          UPDATE referral_advanced_positions
          SET influencer_slot = ?, status = 'provisional',
              qualified_week_id = ?, qualified_at = ?, updated_at = ?
          WHERE position_no = ?
        `).bind(admitted.slotNo, week.id, TEST_TIME + slot, TEST_TIME + slot, slot).run();
      }
    }

    await expect(reviewAdvancedPosition(env.DB, {
      positionNo: 1,
      approved: false,
      reviewerId: 'reviewer',
      reason: 'Failed fraud review',
      reviewedAt: TEST_TIME + 100,
    })).resolves.toBe(true);
    const replacement = await env.DB.prepare(`
      SELECT influencer_slot, status FROM referral_advanced_positions WHERE position_no = 1
    `).first<{ influencer_slot: number; status: string }>();
    expect(replacement).toEqual({
      influencer_slot: 31,
      status: 'provisional',
    });

    await createUser('promotion-32');
    const later = await admitReferralInfluencer(env.DB, {
      userId: 'promotion-32',
      identityVerified: true,
      kycVerified: true,
      academicSnapshot: { slot: 32 },
      reviewedBy: 'referral-audit-operator',
      reviewReason: 'Verified identity, eligibility, KYC, and academic record.',
      reviewedAt: TEST_TIME,
    });
    if (!later.admitted) throw new Error('Expected later candidate admission');
    await env.DB.prepare(`
      INSERT INTO referral_weekly_progress
        (id, week_id, influencer_slot, mature_verified_count,
         policy_version, updated_at)
      VALUES (?, ?, ?, 499, ?, ?)
    `).bind(
      `${week.id}:${later.slotNo}`,
      week.id,
      later.slotNo,
      REFERRAL_POLICY_VERSION,
      TEST_TIME,
    ).run();
    const laterClaim = await createBrowserClaim(later.referralCode, TEST_TIME + 400);
    await matureReferralClaim(
      env.DB,
      laterClaim.claimId,
      qualityEvidence(laterClaim.claimId, TEST_TIME + 500, 'post-rejection'),
      TEST_TIME + 500,
    );
    const winners = await env.DB.prepare(`
      SELECT influencer_slot FROM referral_advanced_positions
      WHERE status = 'provisional' ORDER BY influencer_slot
    `).all<{ influencer_slot: number }>();
    expect(winners.results.map(row => row.influencer_slot)).toEqual(
      Array.from({ length: 30 }, (_, index) => index + 2),
    );
  });

  it('activates approved advanced status only in the following open week', async () => {
    const currentWeek = await openTestWeek();
    const influencer = await admitOne();
    await env.DB.prepare(`
      UPDATE referral_advanced_positions
      SET influencer_slot = ?, status = 'provisional',
          qualified_week_id = ?, qualified_at = ?, updated_at = ?
      WHERE position_no = 1
    `).bind(influencer.slotNo, currentWeek.id, TEST_TIME, TEST_TIME).run();
    await reviewAdvancedPosition(env.DB, {
      positionNo: 1,
      approved: true,
      reviewerId: 'reviewer',
      reason: 'Fraud review passed',
      reviewedAt: TEST_TIME + 100,
    });
    await expect(activateApprovedAdvancedPositions(env.DB, TEST_TIME + 200)).resolves.toBe(0);

    await env.DB.prepare(`
      UPDATE referral_weeks SET state = 'finalized', updated_at = ? WHERE id = ?
    `).bind(currentWeek.endsAt, currentWeek.id).run();
    const nextWeek = referralWeekBounds(currentWeek.endsAt);
    await env.DB.prepare(`
      INSERT INTO referral_weeks
        (id, week_key, starts_at, ends_at, state, policy_version, opened_at)
      VALUES (?, ?, ?, ?, 'open', ?, ?)
    `).bind(
      nextWeek.id,
      nextWeek.key,
      nextWeek.startsAt,
      nextWeek.endsAt,
      REFERRAL_POLICY_VERSION,
      nextWeek.startsAt,
    ).run();
    await env.DB.prepare(`
      UPDATE referral_influencer_slots SET status = 'suspended'
      WHERE slot_no = ?
    `).bind(influencer.slotNo).run();
    await expect(activateApprovedAdvancedPositions(env.DB, nextWeek.startsAt)).resolves.toBe(0);
    await env.DB.prepare(`
      UPDATE referral_influencer_slots SET status = 'active'
      WHERE slot_no = ?
    `).bind(influencer.slotNo).run();
    await expect(activateApprovedAdvancedPositions(env.DB, nextWeek.startsAt + 300)).resolves.toBe(1);
    const row = await env.DB.prepare(`
      SELECT tier, advanced_effective_at
      FROM referral_influencer_slots WHERE slot_no = ?
    `).bind(influencer.slotNo).first<{ tier: string; advanced_effective_at: number }>();
    expect(row).toEqual({
      tier: 'advanced',
      advanced_effective_at: nextWeek.startsAt,
    });
  });

  it('reports browser, account, low-confidence, repeat, and mature metrics separately', async () => {
    const week = await openTestWeek();
    const influencer = await admitOne();
    const browser = await createBrowserClaim(influencer.code, TEST_TIME);
    await matureReferralClaim(
      env.DB,
      browser.claimId,
      qualityEvidence(browser.claimId, TEST_TIME + 20, 'metrics-browser'),
      TEST_TIME + 20,
    );
    await recordReferralVisit(
      env.DB,
      visitRequest(influencer.code),
      influencer.code,
      EDGE_SECRET,
      TEST_TIME + 30,
    );
    const accountClaim = await createBrowserClaim(influencer.code, TEST_TIME + 40);
    const accountId = await createUser();
    await reconcileReferralAccount(
      env.DB,
      visitRequest(influencer.code, accountClaim.cookie),
      accountId,
      EDGE_SECRET,
      TEST_TIME + 50,
    );
    await matureReferralClaim(
      env.DB,
      accountClaim.claimId,
      qualityEvidence(accountClaim.claimId, TEST_TIME + 60, 'metrics-account'),
      TEST_TIME + 60,
    );

    await expect(weeklyReferralMetrics(env.DB, week.id, influencer.slotNo)).resolves.toEqual({
      uniqueBrowsers: 1,
      authenticatedAccounts: 1,
      lowConfidenceIdentities: 1,
      repeatedWeeklyIdentities: 2,
      matureVerified: 2,
    });
  });
});

describe('referral control route authorization', () => {
  it('keeps all referral runtime routes disabled until the release flag is enabled', async () => {
    const enabled = env.REFERRAL_PROGRAM_RUNTIME_ENABLED;
    delete env.REFERRAL_PROGRAM_RUNTIME_ENABLED;
    try {
      const response = await adminReferralRouter.fetch(
        new Request('https://api.syrabit.ai/program/open-week', { method: 'POST' }),
        env,
      );
      expect(response.status).toBe(404);
    } finally {
      if (enabled === undefined) delete env.REFERRAL_PROGRAM_RUNTIME_ENABLED;
      else env.REFERRAL_PROGRAM_RUNTIME_ENABLED = enabled;
    }
  });

  it('accepts a trusted edge-carried origin for cookie-authenticated mutations', async () => {
    const targetId = await createUser('cookie-admission-target');
    const operatorId = await createUser(
      'cookie-referral-reviewer',
      'staff',
      ['referral:review'],
    );
    const adminToken = await signAdminToken(operatorId, ADMIN_SECRET);
    const response = await adminReferralRouter.fetch(
      new Request('https://api.syrabit.ai/admissions', {
        method: 'POST',
        headers: {
          Cookie: `syrabit_admin_session=${adminToken}`,
          'Content-Type': 'application/json',
          'X-Edge-Secret': EDGE_SECRET,
          'X-Original-Origin': 'https://www.syrabit.ai',
        },
        body: JSON.stringify({
          user_id: targetId,
          identity_verified: true,
          kyc_verified: true,
          eligibility_approved: true,
          has_authoritative_admission_priority: true,
          reason: 'Verified identity, eligibility, KYC, and academic record.',
          academic_snapshot: { board: 'AHSEC' },
        }),
      }),
      env,
    );
    expect(response.status).toBe(201);

    const spoofTargetId = await createUser('spoofed-origin-target');
    const spoofed = await adminReferralRouter.fetch(
      new Request('https://api.syrabit.ai/admissions', {
        method: 'POST',
        headers: {
          Cookie: `syrabit_admin_session=${adminToken}`,
          'Content-Type': 'application/json',
          'X-Original-Origin': 'https://www.syrabit.ai',
        },
        body: JSON.stringify({
          user_id: spoofTargetId,
          identity_verified: true,
          kyc_verified: true,
          eligibility_approved: true,
          has_authoritative_admission_priority: true,
          reason: 'Verified identity, eligibility, KYC, and academic record.',
          academic_snapshot: { board: 'AHSEC' },
        }),
      }),
      env,
    );
    expect(spoofed.status).toBe(403);
  });

  it('opens the first week through the capability-gated lifecycle route', async () => {
    const operatorId = await createUser(
      'referral-policy-operator',
      'staff',
      ['referral:policy'],
    );
    const token = await signAccessToken(operatorId, 'staff', JWT_SECRET);
    const now = Math.floor(Date.now() / 1000);
    const evidenceId = await gateEvidence(now, 'route-open');
    const response = await adminReferralRouter.fetch(
      new Request('https://api.syrabit.ai/program/open-week', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ evidence_id: evidenceId }),
      }),
      env,
    );
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      status: 'active',
      opened: true,
    });
    const week = await env.DB.prepare(`
      SELECT state FROM referral_weeks
      WHERE starts_at <= ? AND ends_at > ?
    `).bind(now, now).first<{ state: string }>();
    expect(week?.state).toBe('open');
  });

  it('requires explicit review capability for admission', async () => {
    const targetId = await createUser('admission-target');
    const staffId = await createUser('ordinary-staff', 'staff', ['content:edit']);
    const token = await signAccessToken(staffId, 'staff', JWT_SECRET, TEST_TIME);
    const response = await adminReferralRouter.fetch(
      new Request('https://api.syrabit.ai/admissions', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          user_id: targetId,
          identity_verified: true,
          kyc_verified: true,
          eligibility_approved: true,
          has_authoritative_admission_priority: true,
          reason: 'Verified identity, eligibility, KYC, and academic record.',
          academic_snapshot: { board: 'AHSEC' },
        }),
      }),
      env,
    );
    expect(response.status).toBe(403);
  });

  it('admits with explicit review capability and rejects a refresh-style anonymous call', async () => {
    const targetId = await createUser('capability-target');
    const reviewerId = await createUser(
      'referral-reviewer',
      'staff',
      ['referral:review'],
    );
    const token = await signAccessToken(reviewerId, 'staff', JWT_SECRET);
    const response = await adminReferralRouter.fetch(
      new Request('https://api.syrabit.ai/admissions', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          user_id: targetId,
          identity_verified: true,
          kyc_verified: true,
          eligibility_approved: true,
          has_authoritative_admission_priority: true,
          reason: 'Verified identity, eligibility, KYC, and academic record.',
          academic_snapshot: { board: 'AHSEC' },
        }),
      }),
      env,
    );
    expect(response.status).toBe(201);

    const anonymous = await adminReferralRouter.fetch(
      new Request('https://api.syrabit.ai/admissions', { method: 'POST' }),
      env,
    );
    expect(anonymous.status).toBe(401);
  });

  it('fails internal maturation closed without the exact shared secret', async () => {
    const response = await internalReferralRouter.fetch(
      new Request('https://api.syrabit.ai/claims/claim-id/mature', {
        method: 'POST',
        headers: {
          Authorization: 'Bearer wrong-secret',
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ quality_evidence: { fraud_score: 0 } }),
      }),
      env,
    );
    expect(response.status).toBe(401);
  });
});