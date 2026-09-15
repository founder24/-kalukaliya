import {
  evaluateWeekOpening,
  REFERRAL_POLICY,
  REFERRAL_POLICY_VERSION,
  type ReferralEvidenceGate,
} from '../contracts/referral-policy';
import {
  issuePromoterAdFreeReward,
  issueReferralSignupReward,
} from './referral-rewards';

export const REFERRAL_IDENTITY_COOKIE = 'syrabit_referral_identity';
export const REFERRAL_EVENT_RETENTION_SECONDS = 180 * 24 * 60 * 60;
export const REFERRAL_CLAIM_RETENTION_SECONDS = 400 * 24 * 60 * 60;

const IDENTITY_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const SIGNATURE_PATTERN = /^[a-f0-9]{64}$/;
const KOLKATA_OFFSET_SECONDS = 5 * 60 * 60 + 30 * 60;
const WEEK_SECONDS = 7 * 24 * 60 * 60;
const MAX_EVENT_COUNT_PER_CLAIM = 100;

type IdentityConfidence = 'low' | 'browser' | 'account';

export interface ReferralQualityEvidence {
  evidence_id: string;
  claim_id: string;
  source: 'referral-quality-worker';
  finalized: true;
  fraud_decision: 'pass';
  measured_at_epoch_seconds: number;
  maturity_at_epoch_seconds: number;
  quality_score: number;
}

export interface ReferralWeekBounds {
  id: string;
  key: string;
  startsAt: number;
  endsAt: number;
}

export interface ReferralIdentity {
  identityHash: string;
  confidence: 'low' | 'browser';
  setCookie: string | null;
}

export interface ReferralVisitResult {
  destination: string;
  setCookie: string | null;
  attribution:
    | 'credited'
    | 'repeat'
    | 'competing-influencer'
    | 'accrual-disabled'
    | 'invalid-code';
  claimId: string | null;
}

export interface ReferralLifecycleResult {
  opened: boolean;
  alreadyOpen: boolean;
  week: ReferralWeekBounds;
  activatedAdvancedPositions: number;
  reasons: string[];
}

export interface ReferralGateEvidenceInput {
  providerEvidenceId: string;
  finalizedThroughAt: number;
  grossReserveInr: number;
  outstandingObligationsInr: number;
  qualityEvidenceId: string;
  qualityMeasuredAt: number;
  attributionHealthy: boolean;
  deduplicationHealthy: boolean;
  fraudReviewHealthy: boolean;
  settlementHealthy: boolean;
  recordedAt: number;
  expiresAt: number;
  fundedCapInr?: number;
}

interface InfluencerRow {
  slot_no: number;
  status: string;
}

interface OpenWeekRow {
  id: string;
  week_key: string;
  starts_at: number;
  ends_at: number;
  accrual_generation: number;
}

interface ClaimRow {
  id: string;
  credited_influencer_slot: number;
  identity_confidence: IdentityConfidence;
  state: 'pending' | 'mature' | 'rejected';
  event_count: number;
}

export async function recordReferralGateEvidence(
  db: D1Database,
  input: ReferralGateEvidenceInput,
): Promise<string> {
  if (
    !/^adsense:[A-Za-z0-9_-]{8,128}$/.test(input.providerEvidenceId)
    || !/^[A-Za-z0-9_-]{16,128}$/.test(input.qualityEvidenceId)
    || !Number.isSafeInteger(input.finalizedThroughAt)
    || !Number.isSafeInteger(input.qualityMeasuredAt)
    || !Number.isSafeInteger(input.recordedAt)
    || !Number.isSafeInteger(input.expiresAt)
    || input.finalizedThroughAt > input.recordedAt
    || input.qualityMeasuredAt > input.recordedAt
    || input.expiresAt <= input.recordedAt
    || input.expiresAt - input.recordedAt > REFERRAL_POLICY.funding.qualityEvidenceMaxAgeSeconds
    || !Number.isSafeInteger(input.grossReserveInr)
    || !Number.isSafeInteger(input.outstandingObligationsInr)
    || input.grossReserveInr < input.outstandingObligationsInr
  ) {
    throw new Error('Invalid authoritative referral gate evidence');
  }
  const providerEvidence = await db.prepare(`
    SELECT network, finalized, finalized_through_at, freshness_expires_at
    FROM ad_revenue_reports
    WHERE id = ? OR source_reference = ?
    ORDER BY created_at DESC LIMIT 1
  `).bind(input.providerEvidenceId, input.providerEvidenceId)
    .first<{
      network: string;
      finalized: number;
      finalized_through_at: number;
      freshness_expires_at: number;
    }>();
  if (
    !providerEvidence
    || providerEvidence.network !== REFERRAL_POLICY.funding.approvedNetwork
    || providerEvidence.finalized !== 1
    || providerEvidence.finalized_through_at < input.finalizedThroughAt
    || providerEvidence.freshness_expires_at <= input.recordedAt
  ) {
    throw new Error('Finalized, fresh provider revenue evidence is required');
  }
  const id = crypto.randomUUID();
  const gate: ReferralEvidenceGate = {
    programState: 'active',
    unencumberedReserveInr: input.grossReserveInr - input.outstandingObligationsInr,
    nowEpochSeconds: input.recordedAt,
    revenue: {
      network: 'adsense',
      finalized: true,
      finalizedThroughEpochSeconds: input.finalizedThroughAt,
    },
    quality: {
      finalized: true,
      measuredAtEpochSeconds: input.qualityMeasuredAt,
    },
    controls: {
      attributionHealthy: input.attributionHealthy,
      deduplicationHealthy: input.deduplicationHealthy,
      fraudReviewHealthy: input.fraudReviewHealthy,
      settlementHealthy: input.settlementHealthy,
    },
  };
  const requiredReserveInr = input.fundedCapInr ?? REFERRAL_POLICY.maximumWeeklyRewardExposureInr;
  if (
    !Number.isSafeInteger(requiredReserveInr)
    || requiredReserveInr < 0
    || requiredReserveInr > REFERRAL_POLICY.maximumWeeklyRewardExposureInr
  ) {
    throw new Error('Invalid funded referral cap');
  }
  const gateResult = evaluateWeekOpening(gate, { requiredReserveInr });
  const evidenceInsert = db.prepare(`
    INSERT INTO referral_gate_evidence
      (id, provider, provider_evidence_id, finalized_through_at,
       gross_reserve_inr, outstanding_obligations_inr,
       quality_evidence_id, quality_measured_at,
       attribution_healthy, deduplication_healthy,
       fraud_review_healthy, settlement_healthy,
       recorded_at, expires_at, policy_version)
    VALUES (?, 'adsense', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).bind(
    id,
    input.providerEvidenceId,
    input.finalizedThroughAt,
    input.grossReserveInr,
    input.outstandingObligationsInr,
    input.qualityEvidenceId,
    input.qualityMeasuredAt,
    input.attributionHealthy ? 1 : 0,
    input.deduplicationHealthy ? 1 : 0,
    input.fraudReviewHealthy ? 1 : 0,
    input.settlementHealthy ? 1 : 0,
    input.recordedAt,
    input.expiresAt,
    REFERRAL_POLICY_VERSION,
  );
  if (gateResult.allowed) {
    await evidenceInsert.run();
    return id;
  }
  const reason = `Automatic gate pause: ${gateResult.reasons.join(', ')}`.slice(0, 1_000);
  const serialized = JSON.stringify({ evidence_id: id, ...input, gate_result: gateResult });
  await db.batch([
    evidenceInsert,
    db.prepare(`
      UPDATE referral_program_state
      SET state = 'paused', pause_effective_at = ?, updated_by = 'gate-monitor',
          updated_at = ?
      WHERE id = 'singleton' AND state = 'active'
    `).bind(input.recordedAt, input.recordedAt),
    db.prepare(`
      UPDATE referral_weeks
      SET state = 'paused', pause_effective_at = ?, updated_at = ?
      WHERE state = 'open' AND starts_at <= ? AND ends_at > ?
    `).bind(input.recordedAt, input.recordedAt, input.recordedAt, input.recordedAt),
    db.prepare(`
      INSERT OR IGNORE INTO referral_program_transitions
        (id, transition, actor_id, reason, effective_at, accrual_generation,
         gate_evidence, policy_version)
      SELECT ?, 'pause', 'gate-monitor', ?, ?, accrual_generation, ?, ?
      FROM referral_program_state
      WHERE id = 'singleton' AND state = 'paused' AND updated_at = ?
    `).bind(
      crypto.randomUUID(),
      reason,
      input.recordedAt,
      serialized,
      REFERRAL_POLICY_VERSION,
      input.recordedAt,
    ),
  ]);
  return id;
}

async function loadReferralGate(
  db: D1Database,
  evidenceId: string,
  now: number,
): Promise<{ gate: ReferralEvidenceGate; serialized: string } | null> {
  const row = await db.prepare(`
    SELECT * FROM referral_gate_evidence
    WHERE id = ? AND expires_at > ? AND policy_version = ?
  `).bind(evidenceId, now, REFERRAL_POLICY_VERSION).first<Record<string, number | string>>();
  if (!row) return null;
  const gate: ReferralEvidenceGate = {
    programState: 'active',
    unencumberedReserveInr:
      Number(row.gross_reserve_inr) - Number(row.outstanding_obligations_inr),
    nowEpochSeconds: now,
    revenue: {
      network: 'adsense',
      finalized: true,
      finalizedThroughEpochSeconds: Number(row.finalized_through_at),
    },
    quality: {
      finalized: true,
      measuredAtEpochSeconds: Number(row.quality_measured_at),
    },
    controls: {
      attributionHealthy: row.attribution_healthy === 1,
      deduplicationHealthy: row.deduplication_healthy === 1,
      fraudReviewHealthy: row.fraud_review_healthy === 1,
      settlementHealthy: row.settlement_healthy === 1,
    },
  };
  return {
    gate,
    serialized: JSON.stringify({
      ...row,
      evidence_id: evidenceId,
      calculated_unencumbered_reserve_inr: gate.unencumberedReserveInr,
    }),
  };
}

function cookieValue(cookieHeader: string, name: string): string | null {
  const prefix = `${name}=`;
  return cookieHeader.split(';')
    .map(part => part.trim())
    .find(part => part.startsWith(prefix))
    ?.slice(prefix.length) ?? null;
}

function hex(buffer: ArrayBuffer): string {
  return Array.from(new Uint8Array(buffer))
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('');
}

function base64Url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');
}

function timingSafeEqual(left: string, right: string): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

async function hmacHex(secret: string, message: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  return hex(await crypto.subtle.sign('HMAC', key, encoder.encode(message)));
}

function randomIdentity(): string {
  return base64Url(crypto.getRandomValues(new Uint8Array(32)));
}

export function referralWeekBounds(epochSeconds: number): ReferralWeekBounds {
  const shifted = new Date((epochSeconds + KOLKATA_OFFSET_SECONDS) * 1000);
  const daysSinceMonday = (shifted.getUTCDay() + 6) % 7;
  shifted.setUTCDate(shifted.getUTCDate() - daysSinceMonday);
  shifted.setUTCHours(0, 0, 0, 0);
  const startsAt = Math.floor(shifted.getTime() / 1000) - KOLKATA_OFFSET_SECONDS;
  const key = shifted.toISOString().slice(0, 10);
  return {
    id: `referral-week-${key}`,
    key,
    startsAt,
    endsAt: startsAt + WEEK_SECONDS,
  };
}

export function safeReferralDestination(raw: string | undefined): string {
  if (!raw) return '/library';
  let decoded: string;
  try {
    decoded = decodeURIComponent(raw);
  } catch {
    return '/library';
  }
  if (
    !decoded.startsWith('/')
    || decoded.startsWith('//')
    || decoded.includes('\\')
    || /[\u0000-\u001f\u007f]/.test(decoded)
  ) {
    return '/library';
  }
  try {
    const destination = new URL(decoded, 'https://syrabit.ai');
    if (destination.origin !== 'https://syrabit.ai') return '/library';
    return `${destination.pathname}${destination.search}${destination.hash}`.slice(0, 2_048);
  } catch {
    return '/library';
  }
}

export async function resolveReferralIdentity(
  request: Request,
  secret: string,
): Promise<ReferralIdentity> {
  const encoded = cookieValue(request.headers.get('Cookie') ?? '', REFERRAL_IDENTITY_COOKIE);
  if (encoded) {
    let signed = '';
    try {
      signed = decodeURIComponent(encoded);
    } catch {
      signed = '';
    }
    const separator = signed.lastIndexOf('.');
    if (separator > 0) {
      const identity = signed.slice(0, separator);
      const signature = signed.slice(separator + 1);
      if (IDENTITY_PATTERN.test(identity) && SIGNATURE_PATTERN.test(signature)) {
        const expected = await hmacHex(secret, `cookie:${identity}`);
        if (timingSafeEqual(signature, expected)) {
          return {
            identityHash: await hmacHex(secret, `identity:${identity}`),
            confidence: 'browser',
            setCookie: null,
          };
        }
      }
    }
  }

  const identity = randomIdentity();
  const signature = await hmacHex(secret, `cookie:${identity}`);
  const secure = new URL(request.url).protocol === 'https:';
  return {
    identityHash: await hmacHex(secret, `identity:${identity}`),
    confidence: 'low',
    setCookie: `${REFERRAL_IDENTITY_COOKIE}=${identity}.${signature}; Path=/; Domain=.syrabit.ai; Max-Age=7776000; HttpOnly; SameSite=Lax${secure ? '; Secure' : ''}`,
  };
}

async function weeklyIdentityHash(
  secret: string,
  stableIdentityHash: string,
  weekId: string,
): Promise<string> {
  return hmacHex(secret, `weekly-identity:${weekId}:${stableIdentityHash}`);
}

async function eventKey(
  secret: string,
  slot: number,
  identityHash: string,
  eventType: string,
  occurredAt: number,
): Promise<string> {
  return hmacHex(
    secret,
    `event:${slot}:${identityHash}:${eventType}:${Math.floor(occurredAt / 60)}`,
  );
}

async function reserveLowConfidenceVisit(
  db: D1Database,
  request: Request,
  influencerSlot: number,
  secret: string,
  occurredAt: number,
): Promise<boolean> {
  const network = (request.headers.get('CF-Connecting-IP') ?? 'unknown')
    .trim()
    .toLowerCase()
    .slice(0, 128);
  const hour = Math.floor(occurredAt / 3_600);
  const bucketKey = await hmacHex(
    secret,
    `referral-low-confidence:${influencerSlot}:${network}:${hour}`,
  );
  const row = await db.prepare(`
    INSERT INTO referral_visit_rate_limits
      (bucket_key, request_count, expires_at, updated_at)
    VALUES (?, 1, ?, ?)
    ON CONFLICT(bucket_key) DO UPDATE SET
      request_count = referral_visit_rate_limits.request_count + 1,
      updated_at = excluded.updated_at
    WHERE referral_visit_rate_limits.request_count < 30
    RETURNING request_count
  `).bind(bucketKey, (hour + 2) * 3_600, occurredAt)
    .first<{ request_count: number }>();
  return Boolean(row);
}

async function openWeek(db: D1Database, occurredAt: number): Promise<OpenWeekRow | null> {
  return db.prepare(`
    SELECT w.id, w.week_key, w.starts_at, w.ends_at, w.accrual_generation
    FROM referral_weeks w
    JOIN referral_program_state p ON p.id = 'singleton'
    WHERE w.state = 'open'
      AND p.state = 'active'
      AND w.starts_at <= ?
      AND w.ends_at > ?
      AND (
        w.pause_effective_at IS NULL
        OR ? < w.pause_effective_at
        OR (w.resumed_at > w.pause_effective_at AND ? >= w.resumed_at)
      )
      AND (p.pause_effective_at IS NULL OR ? < p.pause_effective_at OR p.resumed_at > p.pause_effective_at)
      AND EXISTS (
        SELECT 1 FROM referral_gate_evidence e
        WHERE e.recorded_at <= ?
          AND e.expires_at > ?
          AND e.gross_reserve_inr - e.outstanding_obligations_inr >= ?
          AND e.finalized_through_at >= ?
          AND e.quality_measured_at >= ?
          AND e.attribution_healthy = 1
          AND e.deduplication_healthy = 1
          AND e.fraud_review_healthy = 1
          AND e.settlement_healthy = 1
      )
    LIMIT 1
  `).bind(
    occurredAt,
    occurredAt,
    occurredAt,
    occurredAt,
    occurredAt,
    occurredAt,
    occurredAt,
    REFERRAL_POLICY.maximumWeeklyRewardExposureInr,
    occurredAt - REFERRAL_POLICY.funding.revenueEvidenceMaxAgeSeconds,
    occurredAt - REFERRAL_POLICY.funding.qualityEvidenceMaxAgeSeconds,
  ).first<OpenWeekRow>();
}

export async function enforceReferralGateFreshness(
  db: D1Database,
  occurredAt = Math.floor(Date.now() / 1000),
): Promise<boolean> {
  const active = await db.prepare(`
    SELECT 1 AS active
    FROM referral_program_state p
    JOIN referral_weeks w ON w.state = 'open'
    WHERE p.id = 'singleton' AND p.state = 'active'
      AND w.starts_at <= ? AND w.ends_at > ?
    LIMIT 1
  `).bind(occurredAt, occurredAt).first<{ active: number }>();
  if (!active || await openWeek(db, occurredAt)) return false;
  return pauseReferralProgram(
    db,
    'gate-monitor',
    'Automatic pause because authoritative gate evidence is missing, stale, or failing.',
    occurredAt,
  );
}

async function recordBoundedEvent(
  db: D1Database,
  input: {
    claimId: string | null;
    weekId: string | null;
    influencerSlot: number;
    eventKey: string;
    eventType: 'visit' | 'repeat' | 'reconcile' | 'mature' | 'reject' | 'paused_visit';
    identityConfidence: IdentityConfidence;
    occurredAt: number;
  },
): Promise<void> {
  await db.prepare(`
    INSERT INTO referral_claim_events
      (id, claim_id, week_id, influencer_slot, event_key, event_type,
       identity_confidence, occurred_at, expires_at)
    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
    WHERE ? IS NULL OR (
      SELECT COUNT(*) FROM referral_claim_events WHERE claim_id = ?
    ) < ?
    ON CONFLICT(event_key) DO NOTHING
  `).bind(
    crypto.randomUUID(),
    input.claimId,
    input.weekId,
    input.influencerSlot,
    input.eventKey,
    input.eventType,
    input.identityConfidence,
    input.occurredAt,
    input.occurredAt + REFERRAL_EVENT_RETENTION_SECONDS,
    input.claimId,
    input.claimId,
    MAX_EVENT_COUNT_PER_CLAIM,
  ).run();
}

export async function recordReferralVisit(
  db: D1Database,
  request: Request,
  referralCode: string,
  secret: string,
  occurredAt = Math.floor(Date.now() / 1000),
): Promise<ReferralVisitResult> {
  const destination = safeReferralDestination(new URL(request.url).searchParams.get('next') ?? undefined);
  if (!/^[A-Za-z0-9_-]{32}$/.test(referralCode)) {
    return { destination, setCookie: null, attribution: 'invalid-code', claimId: null };
  }

  const influencer = await db.prepare(`
    SELECT slot_no, status
    FROM referral_influencer_slots
    WHERE referral_code = ? AND status = 'active'
    LIMIT 1
  `).bind(referralCode).first<InfluencerRow>();
  if (!influencer) {
    return { destination, setCookie: null, attribution: 'invalid-code', claimId: null };
  }

  const identity = await resolveReferralIdentity(request, secret);
  const week = await openWeek(db, occurredAt);
  if (!week) {
    await recordBoundedEvent(db, {
      claimId: null,
      weekId: null,
      influencerSlot: influencer.slot_no,
      eventKey: await eventKey(secret, influencer.slot_no, identity.identityHash, 'paused_visit', occurredAt),
      eventType: 'paused_visit',
      identityConfidence: identity.confidence,
      occurredAt,
    });
    return {
      destination,
      setCookie: identity.setCookie,
      attribution: 'accrual-disabled',
      claimId: null,
    };
  }
  if (
    identity.confidence === 'low'
    && !await reserveLowConfidenceVisit(
      db,
      request,
      influencer.slot_no,
      secret,
      occurredAt,
    )
  ) {
    return {
      destination,
      setCookie: identity.setCookie,
      attribution: 'accrual-disabled',
      claimId: null,
    };
  }

  const identityHash = await weeklyIdentityHash(
    secret,
    identity.identityHash,
    week.id,
  );
  const proposedClaimId = crypto.randomUUID();
  const claim = await db.prepare(`
    INSERT INTO referral_weekly_claims
      (id, week_id, identity_hash, credited_influencer_slot, identity_confidence,
       state, first_seen_at, last_seen_at, event_count, accrual_generation,
       policy_version, expires_at, updated_at)
    VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, 1, ?, ?, ?, ?)
    ON CONFLICT(week_id, identity_hash) DO UPDATE SET
      first_seen_at = CASE
        WHEN referral_weekly_claims.state = 'pending'
          AND excluded.accrual_generation > referral_weekly_claims.accrual_generation
        THEN excluded.first_seen_at
        ELSE referral_weekly_claims.first_seen_at
      END,
      last_seen_at = MAX(referral_weekly_claims.last_seen_at, excluded.last_seen_at),
      event_count = MIN(100, referral_weekly_claims.event_count + 1),
      identity_confidence = CASE
        WHEN referral_weekly_claims.identity_confidence = 'low'
          AND referral_weekly_claims.credited_influencer_slot = excluded.credited_influencer_slot
          AND excluded.identity_confidence = 'browser'
        THEN 'browser'
        ELSE referral_weekly_claims.identity_confidence
      END,
      accrual_generation = CASE
        WHEN referral_weekly_claims.state = 'pending'
          AND excluded.accrual_generation > referral_weekly_claims.accrual_generation
        THEN excluded.accrual_generation
        ELSE referral_weekly_claims.accrual_generation
      END,
      updated_at = excluded.updated_at
    RETURNING id, credited_influencer_slot, identity_confidence, state, event_count
  `).bind(
    proposedClaimId,
    week.id,
    identityHash,
    influencer.slot_no,
    identity.confidence,
    occurredAt,
    occurredAt,
    week.accrual_generation,
    REFERRAL_POLICY_VERSION,
    week.ends_at + REFERRAL_CLAIM_RETENTION_SECONDS,
    occurredAt,
  ).first<ClaimRow>();
  if (!claim) throw new Error('Referral claim write returned no row');

  const isCompeting = claim.credited_influencer_slot !== influencer.slot_no;
  const isNew = claim.id === proposedClaimId;
  await recordBoundedEvent(db, {
    claimId: claim.id,
    weekId: week.id,
    influencerSlot: influencer.slot_no,
    eventKey: await eventKey(
      secret,
      influencer.slot_no,
      identityHash,
      isNew ? 'visit' : 'repeat',
      occurredAt,
    ),
    eventType: isNew ? 'visit' : 'repeat',
    identityConfidence: identity.confidence,
    occurredAt,
  });

  return {
    destination,
    setCookie: identity.setCookie,
    attribution: isCompeting ? 'competing-influencer' : isNew ? 'credited' : 'repeat',
    claimId: claim.id,
  };
}

export async function admitReferralInfluencer(
  db: D1Database,
  input: {
    userId: string;
    academicSnapshot: Record<string, unknown>;
    identityVerified: true;
    kycVerified: true;
    reviewedBy: string;
    reviewReason: string;
    reviewedAt: number;
  },
): Promise<{ admitted: true; slotNo: number; referralCode: string } | { admitted: false }> {
  const academicSnapshot = JSON.stringify(input.academicSnapshot);
  if (academicSnapshot.length < 2 || academicSnapshot.length > 8_192) {
    throw new Error('Academic snapshot must be between 2 and 8192 bytes');
  }
  const reviewReason = input.reviewReason.trim();
  if (reviewReason.length < 8 || reviewReason.length > 1_000) {
    throw new Error('Admission review reason must be between 8 and 1000 characters');
  }

  for (let attempt = 0; attempt < 3; attempt += 1) {
    const referralCode = base64Url(crypto.getRandomValues(new Uint8Array(24)));
    try {
      const auditId = crypto.randomUUID();
      const results = await db.batch([
        db.prepare(`
          UPDATE referral_influencer_slots
          SET user_id = ?,
              referral_code = ?,
            status = 'active',
              tier = 'basic',
              identity_verified = 1,
              kyc_verified = 1,
              academic_snapshot = ?,
              eligibility_reviewed_at = ?,
              admitted_at = ?,
              policy_version = ?,
              updated_at = ?
          WHERE slot_no = (
            SELECT slot_no
            FROM referral_influencer_slots
            WHERE status = 'available' AND user_id IS NULL
            ORDER BY slot_no
            LIMIT 1
          )
            AND EXISTS (SELECT 1 FROM users WHERE id = ? AND deleted_at IS NULL)
            AND EXISTS (SELECT 1 FROM users WHERE id = ? AND deleted_at IS NULL)
            AND NOT EXISTS (
              SELECT 1 FROM referral_influencer_slots WHERE user_id = ?
            )
        `).bind(
          input.userId,
          referralCode,
          academicSnapshot,
          input.reviewedAt,
          input.reviewedAt,
          REFERRAL_POLICY_VERSION,
          input.reviewedAt,
          input.userId,
          input.reviewedBy,
          input.userId,
        ),
        db.prepare(`
          INSERT INTO referral_admission_audits
            (id, influencer_slot, user_id, actor_id, reason,
             admitted_at, policy_version)
          SELECT ?, slot_no, user_id, ?, ?, admitted_at, policy_version
          FROM referral_influencer_slots
          WHERE user_id = ? AND referral_code = ? AND admitted_at = ?
        `).bind(
          auditId,
          input.reviewedBy,
          reviewReason,
          input.userId,
          referralCode,
          input.reviewedAt,
        ),
      ]);
      if (results[0]?.meta.changes !== 1 || results[1]?.meta.changes !== 1) {
        return { admitted: false };
      }
      const row = await db.prepare(`
        SELECT slot_no FROM referral_influencer_slots
        WHERE user_id = ? AND referral_code = ?
      `).bind(input.userId, referralCode).first<{ slot_no: number }>();
      return row
        ? { admitted: true, slotNo: row.slot_no, referralCode }
        : { admitted: false };
    } catch (error) {
      if (attempt === 2) throw error;
    }
  }
  return { admitted: false };
}

export async function reconcileReferralAccount(
  db: D1Database,
  request: Request,
  accountId: string,
  secret: string,
  occurredAt = Math.floor(Date.now() / 1000),
): Promise<'reconciled' | 'already-reconciled' | 'duplicate-account' | 'no-claim'> {
  const identity = await resolveReferralIdentity(request, secret);
  if (identity.confidence !== 'browser') return 'no-claim';
  const week = await openWeek(db, occurredAt);
  if (!week) return 'no-claim';
  const identityHash = await weeklyIdentityHash(
    secret,
    identity.identityHash,
    week.id,
  );
  const claim = await db.prepare(`
    SELECT id, credited_influencer_slot, identity_confidence, state, event_count
    FROM referral_weekly_claims
    WHERE week_id = ? AND identity_hash = ?
    LIMIT 1
  `).bind(
    week.id,
    identityHash,
  ).first<ClaimRow & { account_id?: string | null }>();
  if (!claim || claim.state === 'rejected') return 'no-claim';

  const canonical = await db.prepare(`
    SELECT id FROM referral_weekly_claims
    WHERE week_id = ? AND account_id = ? AND state != 'rejected'
    LIMIT 1
  `).bind(week.id, accountId).first<{ id: string }>();
  if (canonical && canonical.id !== claim.id) {
    await rejectDuplicateAccountClaim(db, claim.id, occurredAt);
    await recordBoundedEvent(db, {
      claimId: claim.id,
      weekId: week.id,
      influencerSlot: claim.credited_influencer_slot,
      eventKey: await eventKey(secret, claim.credited_influencer_slot, identityHash, 'reject', occurredAt),
      eventType: 'reject',
      identityConfidence: 'account',
      occurredAt,
    });
    return 'duplicate-account';
  }

  const reconciliationToken = crypto.randomUUID();
  let updated = false;
  try {
    const results = await db.batch([
      db.prepare(`
        UPDATE referral_weekly_claims
        SET account_id = ?,
            identity_confidence = 'account',
            reconciliation_token = ?,
            updated_at = ?
        WHERE id = ? AND state != 'rejected'
          AND (account_id IS NULL OR account_id = ?)
          AND identity_confidence != 'account'
      `).bind(
        accountId,
        reconciliationToken,
        occurredAt,
        claim.id,
        accountId,
      ),
      db.prepare(`
        UPDATE referral_weekly_progress
        SET unique_browser_count = MAX(0, unique_browser_count - 1),
            authenticated_account_count = authenticated_account_count + 1,
            updated_at = ?
        WHERE EXISTS (
          SELECT 1 FROM referral_weekly_claims c
          WHERE c.reconciliation_token = ?
            AND c.maturity_token IS NOT NULL
            AND c.week_id = referral_weekly_progress.week_id
            AND c.credited_influencer_slot = referral_weekly_progress.influencer_slot
        )
      `).bind(occurredAt, reconciliationToken),
    ]);
    updated = results[0]?.meta.changes === 1;
  } catch {
    await rejectDuplicateAccountClaim(db, claim.id, occurredAt);
    return 'duplicate-account';
  }
  if (!updated) {
    if (claim.identity_confidence === 'account') {
      await issueReferralSignupReward(db, accountId, occurredAt).catch(() => {});
      return 'already-reconciled';
    }
    return 'duplicate-account';
  }

  await recordBoundedEvent(db, {
    claimId: claim.id,
    weekId: week.id,
    influencerSlot: claim.credited_influencer_slot,
    eventKey: await eventKey(secret, claim.credited_influencer_slot, identityHash, 'reconcile', occurredAt),
    eventType: 'reconcile',
    identityConfidence: 'account',
    occurredAt,
  });
  await issueReferralSignupReward(db, accountId, occurredAt).catch(() => {});
  return 'reconciled';
}

async function rejectDuplicateAccountClaim(
  db: D1Database,
  claimId: string,
  occurredAt: number,
): Promise<void> {
  const reversalToken = crypto.randomUUID();
  await db.batch([
    db.prepare(`
      UPDATE referral_weekly_claims
      SET state = 'rejected',
          rejection_reason = 'duplicate-account',
          reversal_token = ?,
          progress_counted = 0,
          updated_at = ?
      WHERE id = ? AND state != 'rejected'
    `).bind(reversalToken, occurredAt, claimId),
    db.prepare(`
      UPDATE referral_weekly_progress
      SET mature_verified_count = MAX(0, mature_verified_count - 1),
          reward_eligible_count = MIN(
            reward_eligible_count,
            MAX(0, mature_verified_count - 1)
          ),
          unique_browser_count = MAX(
            0,
            unique_browser_count - CASE WHEN (
              SELECT identity_confidence FROM referral_weekly_claims
              WHERE reversal_token = ?
            ) = 'browser' THEN 1 ELSE 0 END
          ),
          authenticated_account_count = MAX(
            0,
            authenticated_account_count - CASE WHEN (
              SELECT identity_confidence FROM referral_weekly_claims
              WHERE reversal_token = ?
            ) = 'account' THEN 1 ELSE 0 END
          ),
          updated_at = ?
      WHERE EXISTS (
        SELECT 1 FROM referral_weekly_claims c
        WHERE c.reversal_token = ?
          AND c.week_id = referral_weekly_progress.week_id
          AND c.credited_influencer_slot = referral_weekly_progress.influencer_slot
          AND c.maturity_token IS NOT NULL
      )
    `).bind(reversalToken, reversalToken, occurredAt, reversalToken),
    db.prepare(`
      UPDATE referral_weekly_progress
      SET provisional_qualified_at = NULL, updated_at = ?
      WHERE mature_verified_count < ?
        AND EXISTS (
          SELECT 1 FROM referral_weekly_claims c
          WHERE c.reversal_token = ?
            AND c.week_id = referral_weekly_progress.week_id
            AND c.credited_influencer_slot = referral_weekly_progress.influencer_slot
        )
    `).bind(
      occurredAt,
      REFERRAL_POLICY.advanced.qualificationVisitorsInOneWeek,
      reversalToken,
    ),
    db.prepare(`
      UPDATE referral_advanced_positions
      SET influencer_slot = NULL,
          status = 'available',
          qualified_week_id = NULL,
          qualified_at = NULL,
          reviewed_by = NULL,
          reviewed_at = NULL,
          review_reason = NULL,
          activates_at = NULL,
          updated_at = ?
      WHERE influencer_slot = (
        SELECT credited_influencer_slot
        FROM referral_weekly_claims
        WHERE reversal_token = ?
      )
        AND qualified_week_id = (
          SELECT week_id
          FROM referral_weekly_claims
          WHERE reversal_token = ?
        )
        AND EXISTS (
          SELECT 1
          FROM referral_weekly_progress p
          JOIN referral_weekly_claims c
            ON c.week_id = p.week_id
           AND c.credited_influencer_slot = p.influencer_slot
          WHERE c.reversal_token = ?
            AND p.mature_verified_count < ?
        )
    `).bind(
      occurredAt,
      reversalToken,
      reversalToken,
      reversalToken,
      REFERRAL_POLICY.advanced.qualificationVisitorsInOneWeek,
    ),
    db.prepare(`
      UPDATE referral_influencer_slots
      SET tier = 'basic', advanced_effective_at = NULL, updated_at = ?
      WHERE slot_no = (
        SELECT credited_influencer_slot
        FROM referral_weekly_claims
        WHERE reversal_token = ?
      )
        AND tier = 'advanced'
        AND NOT EXISTS (
          SELECT 1 FROM referral_advanced_positions a
          WHERE a.influencer_slot = referral_influencer_slots.slot_no
            AND a.status = 'active'
        )
    `).bind(occurredAt, reversalToken),
  ]);
  await fillAdvancedVacancies(db, occurredAt);
}

function validateQualityEvidence(
  input: Record<string, unknown>,
  claimId: string,
  processedAt: number,
): ReferralQualityEvidence {
  const measuredAt = input.measured_at_epoch_seconds;
  const maturityAt = input.maturity_at_epoch_seconds;
  const qualityScore = input.quality_score;
  if (
    typeof input.evidence_id !== 'string'
    || !/^[A-Za-z0-9_-]{16,128}$/.test(input.evidence_id)
    || input.claim_id !== claimId
    || input.source !== 'referral-quality-worker'
    || input.finalized !== true
    || input.fraud_decision !== 'pass'
    || !Number.isSafeInteger(measuredAt)
    || !Number.isSafeInteger(maturityAt)
    || (maturityAt as number) > (measuredAt as number)
    || (measuredAt as number) > processedAt
    || processedAt - (measuredAt as number) > REFERRAL_POLICY.funding.qualityEvidenceMaxAgeSeconds
    || typeof qualityScore !== 'number'
    || !Number.isFinite(qualityScore)
    || qualityScore < 0
    || qualityScore > 1
  ) {
    throw new Error('Finalized, fresh, passing referral quality evidence is required');
  }
  return input as unknown as ReferralQualityEvidence;
}

async function rebalanceProvisionalAdvancedPositions(
  db: D1Database,
  occurredAt: number,
): Promise<void> {
  const statements = [
    db.prepare(`
      UPDATE referral_advanced_positions
      SET influencer_slot = NULL,
          status = 'available',
          qualified_week_id = NULL,
          qualified_at = NULL,
          updated_at = ?
      WHERE status = 'provisional'
    `).bind(occurredAt),
  ];
  for (let positionNo = 1; positionNo <= REFERRAL_POLICY.advancedInfluencerSlots; positionNo += 1) {
    statements.push(db.prepare(`
      UPDATE referral_advanced_positions
      SET influencer_slot = (
            SELECT p.influencer_slot
            FROM referral_weekly_progress p
            JOIN referral_influencer_slots i ON i.slot_no = p.influencer_slot
            WHERE p.provisional_qualified_at IS NOT NULL
              AND p.mature_verified_count >= ?
              AND i.status = 'active'
              AND NOT EXISTS (
                SELECT 1 FROM referral_advanced_positions a
                WHERE a.influencer_slot = p.influencer_slot
                  AND a.status IN ('provisional', 'approved', 'active')
              )
            ORDER BY p.provisional_qualified_at, p.influencer_slot
            LIMIT 1
          ),
          status = 'provisional',
          qualified_week_id = (
            SELECT p.week_id
            FROM referral_weekly_progress p
            JOIN referral_influencer_slots i ON i.slot_no = p.influencer_slot
            WHERE p.provisional_qualified_at IS NOT NULL
              AND p.mature_verified_count >= ?
              AND i.status = 'active'
              AND NOT EXISTS (
                SELECT 1 FROM referral_advanced_positions a
                WHERE a.influencer_slot = p.influencer_slot
                  AND a.status IN ('provisional', 'approved', 'active')
              )
            ORDER BY p.provisional_qualified_at, p.influencer_slot
            LIMIT 1
          ),
          qualified_at = (
            SELECT p.provisional_qualified_at
            FROM referral_weekly_progress p
            JOIN referral_influencer_slots i ON i.slot_no = p.influencer_slot
            WHERE p.provisional_qualified_at IS NOT NULL
              AND p.mature_verified_count >= ?
              AND i.status = 'active'
              AND NOT EXISTS (
                SELECT 1 FROM referral_advanced_positions a
                WHERE a.influencer_slot = p.influencer_slot
                  AND a.status IN ('provisional', 'approved', 'active')
              )
            ORDER BY p.provisional_qualified_at, p.influencer_slot
            LIMIT 1
          ),
          policy_version = ?,
          updated_at = ?
      WHERE position_no = ?
        AND status IN ('available', 'released', 'rejected')
        AND EXISTS (
          SELECT 1
          FROM referral_weekly_progress p
          JOIN referral_influencer_slots i ON i.slot_no = p.influencer_slot
          WHERE p.provisional_qualified_at IS NOT NULL
            AND p.mature_verified_count >= ?
            AND i.status = 'active'
            AND NOT EXISTS (
              SELECT 1 FROM referral_advanced_positions a
              WHERE a.influencer_slot = p.influencer_slot
                AND a.status IN ('provisional', 'approved', 'active')
            )
        )
    `).bind(
      REFERRAL_POLICY.advanced.qualificationVisitorsInOneWeek,
      REFERRAL_POLICY.advanced.qualificationVisitorsInOneWeek,
      REFERRAL_POLICY.advanced.qualificationVisitorsInOneWeek,
      REFERRAL_POLICY_VERSION,
      occurredAt,
      positionNo,
      REFERRAL_POLICY.advanced.qualificationVisitorsInOneWeek,
    ));
  }
  await db.batch(statements);
}

export async function matureReferralClaim(
  db: D1Database,
  claimId: string,
  qualityEvidence: Record<string, unknown>,
  processedAt = Math.floor(Date.now() / 1000),
): Promise<{
  counted: boolean;
  matureVerifiedCount: number | null;
  provisionalQualifiedAt: number | null;
  advancedPosition: number | null;
}> {
  const evidence = validateQualityEvidence(qualityEvidence, claimId, processedAt);
  const maturedAt = evidence.maturity_at_epoch_seconds;
  const serializedEvidence = JSON.stringify(evidence);
  if (serializedEvidence.length > 4_096) throw new Error('Quality evidence exceeds 4096 bytes');
  const maturityToken = crypto.randomUUID();

  await db.batch([
    db.prepare(`
      UPDATE referral_weekly_claims
      SET state = 'mature',
          matured_at = ?,
          maturity_token = ?,
          quality_evidence_id = ?,
          progress_counted = 1,
          quality_evidence = ?,
          updated_at = ?
      WHERE id = ?
        AND state = 'pending'
        AND progress_counted = 0
        AND identity_confidence IN ('browser', 'account')
        AND ? >= first_seen_at
        AND EXISTS (
          SELECT 1
          FROM referral_weeks w
          JOIN referral_program_state p ON p.id = 'singleton'
          WHERE w.id = referral_weekly_claims.week_id
            AND ? >= w.starts_at
            AND ? < w.ends_at
            AND (
              (
                w.state = 'open'
                AND p.state = 'active'
                AND referral_weekly_claims.accrual_generation = w.accrual_generation
                AND referral_weekly_claims.accrual_generation = p.accrual_generation
                AND (
                  w.pause_effective_at IS NULL
                  OR ? < w.pause_effective_at
                  OR (w.resumed_at > w.pause_effective_at AND ? >= w.resumed_at)
                )
                AND (
                  p.pause_effective_at IS NULL
                  OR ? < p.pause_effective_at
                  OR p.resumed_at > p.pause_effective_at
                )
              )
              OR (
                w.pause_effective_at IS NOT NULL
                AND p.pause_effective_at IS NOT NULL
                AND ? < w.pause_effective_at
                AND ? < p.pause_effective_at
              )
            )
            AND EXISTS (
              SELECT 1 FROM referral_gate_evidence e
              WHERE e.recorded_at <= ?
                AND e.expires_at > ?
                AND e.gross_reserve_inr - e.outstanding_obligations_inr >= ?
                AND e.finalized_through_at >= ?
                AND e.quality_measured_at >= ?
                AND e.attribution_healthy = 1
                AND e.deduplication_healthy = 1
                AND e.fraud_review_healthy = 1
                AND e.settlement_healthy = 1
            )
        )
    `).bind(
      maturedAt,
      maturityToken,
      evidence.evidence_id,
      serializedEvidence,
      maturedAt,
      claimId,
      maturedAt,
      maturedAt,
      maturedAt,
      maturedAt,
      maturedAt,
      maturedAt,
      maturedAt,
      maturedAt,
      maturedAt,
      maturedAt,
      REFERRAL_POLICY.maximumWeeklyRewardExposureInr,
      maturedAt - REFERRAL_POLICY.funding.revenueEvidenceMaxAgeSeconds,
      maturedAt - REFERRAL_POLICY.funding.qualityEvidenceMaxAgeSeconds,
    ),
    db.prepare(`
      INSERT INTO referral_weekly_progress
        (id, week_id, influencer_slot, mature_verified_count, reward_eligible_count,
         unique_browser_count, authenticated_account_count, low_confidence_count,
         policy_version, updated_at)
      SELECT
        week_id || ':' || credited_influencer_slot,
        week_id,
        credited_influencer_slot,
        1,
        1,
        CASE WHEN identity_confidence = 'browser' THEN 1 ELSE 0 END,
        CASE WHEN identity_confidence = 'account' THEN 1 ELSE 0 END,
        0,
        policy_version,
        ?
      FROM referral_weekly_claims
      WHERE id = ? AND maturity_token = ?
      ON CONFLICT(week_id, influencer_slot) DO UPDATE SET
        mature_verified_count = referral_weekly_progress.mature_verified_count + 1,
        reward_eligible_count = MIN(
          CASE WHEN EXISTS (
            SELECT 1
            FROM referral_influencer_slots i
            JOIN referral_weeks w ON w.id = referral_weekly_progress.week_id
            WHERE i.slot_no = referral_weekly_progress.influencer_slot
              AND i.tier = 'advanced'
              AND i.advanced_effective_at IS NOT NULL
              AND i.advanced_effective_at <= w.starts_at
          ) THEN ? ELSE ? END,
          referral_weekly_progress.mature_verified_count + 1
        ),
        unique_browser_count = referral_weekly_progress.unique_browser_count
          + CASE WHEN (SELECT identity_confidence FROM referral_weekly_claims WHERE id = ?) = 'browser' THEN 1 ELSE 0 END,
        authenticated_account_count = referral_weekly_progress.authenticated_account_count
          + CASE WHEN (SELECT identity_confidence FROM referral_weekly_claims WHERE id = ?) = 'account' THEN 1 ELSE 0 END,
        updated_at = excluded.updated_at
    `).bind(
      maturedAt,
      claimId,
      maturityToken,
      REFERRAL_POLICY.advanced.weeklyPaidVisitorCap,
      REFERRAL_POLICY.basic.weeklyPaidVisitorCap,
      claimId,
      claimId,
    ),
    db.prepare(`
      UPDATE referral_weekly_progress
      SET provisional_qualified_at = COALESCE(provisional_qualified_at, ?),
          updated_at = ?
      WHERE id = (
        SELECT week_id || ':' || credited_influencer_slot
        FROM referral_weekly_claims
        WHERE id = ? AND maturity_token = ?
      )
        AND mature_verified_count >= ?
    `).bind(
      maturedAt,
      maturedAt,
      claimId,
      maturityToken,
      REFERRAL_POLICY.advanced.qualificationVisitorsInOneWeek,
    ),
    db.prepare(`
      UPDATE referral_advanced_positions
      SET influencer_slot = (
            SELECT credited_influencer_slot
            FROM referral_weekly_claims
            WHERE id = ? AND maturity_token = ?
          ),
          status = 'provisional',
          qualified_week_id = (
            SELECT week_id FROM referral_weekly_claims
            WHERE id = ? AND maturity_token = ?
          ),
          qualified_at = ?,
          policy_version = ?,
          updated_at = ?
      WHERE position_no = (
        SELECT position_no
        FROM referral_advanced_positions
        WHERE status IN ('available', 'released', 'rejected')
          AND influencer_slot IS NULL
        ORDER BY position_no
        LIMIT 1
      )
        AND EXISTS (
          SELECT 1
          FROM referral_weekly_progress p
          JOIN referral_weekly_claims c
            ON c.week_id = p.week_id
           AND c.credited_influencer_slot = p.influencer_slot
          WHERE c.id = ?
            AND c.maturity_token = ?
            AND p.provisional_qualified_at = ?
            AND p.mature_verified_count >= ?
            AND EXISTS (
              SELECT 1 FROM referral_influencer_slots i
              WHERE i.slot_no = p.influencer_slot AND i.status = 'active'
            )
        )
        AND NOT EXISTS (
          SELECT 1 FROM referral_advanced_positions a
          WHERE a.influencer_slot = (
            SELECT credited_influencer_slot FROM referral_weekly_claims
            WHERE id = ? AND maturity_token = ?
          )
          AND a.status IN ('provisional', 'approved', 'active')
        )
    `).bind(
      claimId,
      maturityToken,
      claimId,
      maturityToken,
      maturedAt,
      REFERRAL_POLICY_VERSION,
      maturedAt,
      claimId,
      maturityToken,
      maturedAt,
      REFERRAL_POLICY.advanced.qualificationVisitorsInOneWeek,
      claimId,
      maturityToken,
    ),
  ]);
  await rebalanceProvisionalAdvancedPositions(db, maturedAt);

  const claim = await db.prepare(`
    SELECT week_id, credited_influencer_slot, account_id
    FROM referral_weekly_claims
    WHERE id = ? AND maturity_token = ?
  `).bind(claimId, maturityToken).first<{
    week_id: string;
    credited_influencer_slot: number;
    account_id: string | null;
  }>();
  if (!claim) {
    return {
      counted: false,
      matureVerifiedCount: null,
      provisionalQualifiedAt: null,
      advancedPosition: null,
    };
  }
  const progress = await db.prepare(`
    SELECT mature_verified_count, reward_eligible_count, provisional_qualified_at
    FROM referral_weekly_progress
    WHERE week_id = ? AND influencer_slot = ?
  `).bind(claim.week_id, claim.credited_influencer_slot).first<{
    mature_verified_count: number;
    reward_eligible_count: number;
    provisional_qualified_at: number | null;
  }>();
  const promoter = await db.prepare(`
    SELECT user_id
    FROM referral_influencer_slots
    WHERE slot_no = ?
  `).bind(claim.credited_influencer_slot).first<{ user_id: string }>();
  if (
    promoter?.user_id
    && (progress?.mature_verified_count ?? 0) >= REFERRAL_POLICY.accessRewards.promoterMatureVerifiedThreshold
    && (progress?.reward_eligible_count ?? 0) >= REFERRAL_POLICY.accessRewards.promoterMatureVerifiedThreshold
  ) {
    await issuePromoterAdFreeReward(db, promoter.user_id, maturedAt).catch(() => {});
  }
  if (claim.account_id) {
    await issueReferralSignupReward(db, claim.account_id, maturedAt).catch(() => {});
  }
  const position = await db.prepare(`
    SELECT position_no
    FROM referral_advanced_positions
    WHERE influencer_slot = ? AND status IN ('provisional', 'approved', 'active')
  `).bind(claim.credited_influencer_slot).first<{ position_no: number }>();
  return {
    counted: true,
    matureVerifiedCount: progress?.mature_verified_count ?? null,
    provisionalQualifiedAt: progress?.provisional_qualified_at ?? null,
    advancedPosition: position?.position_no ?? null,
  };
}

export async function fillAdvancedVacancies(
  db: D1Database,
  occurredAt = Math.floor(Date.now() / 1000),
): Promise<number> {
  let filled = 0;
  for (let iteration = 0; iteration < REFERRAL_POLICY.advancedInfluencerSlots; iteration += 1) {
    const row = await db.prepare(`
      UPDATE referral_advanced_positions
      SET influencer_slot = (
            SELECT p.influencer_slot
            FROM referral_weekly_progress p
            LEFT JOIN referral_advanced_positions a
              ON a.influencer_slot = p.influencer_slot
             AND a.status IN ('provisional', 'approved', 'active')
            WHERE p.provisional_qualified_at IS NOT NULL
              AND a.position_no IS NULL
              AND EXISTS (
                SELECT 1 FROM referral_influencer_slots i
                WHERE i.slot_no = p.influencer_slot AND i.status = 'active'
              )
              AND NOT EXISTS (
                SELECT 1 FROM referral_advanced_reviews r
                WHERE r.influencer_slot = p.influencer_slot
                  AND r.qualified_week_id = p.week_id
                  AND r.decision = 'rejected'
              )
            ORDER BY p.provisional_qualified_at, p.influencer_slot
            LIMIT 1
          ),
          qualified_week_id = (
            SELECT p.week_id
            FROM referral_weekly_progress p
            LEFT JOIN referral_advanced_positions a
              ON a.influencer_slot = p.influencer_slot
             AND a.status IN ('provisional', 'approved', 'active')
            WHERE p.provisional_qualified_at IS NOT NULL
              AND a.position_no IS NULL
              AND EXISTS (
                SELECT 1 FROM referral_influencer_slots i
                WHERE i.slot_no = p.influencer_slot AND i.status = 'active'
              )
              AND NOT EXISTS (
                SELECT 1 FROM referral_advanced_reviews r
                WHERE r.influencer_slot = p.influencer_slot
                  AND r.qualified_week_id = p.week_id
                  AND r.decision = 'rejected'
              )
            ORDER BY p.provisional_qualified_at, p.influencer_slot
            LIMIT 1
          ),
          qualified_at = (
            SELECT p.provisional_qualified_at
            FROM referral_weekly_progress p
            LEFT JOIN referral_advanced_positions a
              ON a.influencer_slot = p.influencer_slot
             AND a.status IN ('provisional', 'approved', 'active')
            WHERE p.provisional_qualified_at IS NOT NULL
              AND a.position_no IS NULL
              AND EXISTS (
                SELECT 1 FROM referral_influencer_slots i
                WHERE i.slot_no = p.influencer_slot AND i.status = 'active'
              )
              AND NOT EXISTS (
                SELECT 1 FROM referral_advanced_reviews r
                WHERE r.influencer_slot = p.influencer_slot
                  AND r.qualified_week_id = p.week_id
                  AND r.decision = 'rejected'
              )
            ORDER BY p.provisional_qualified_at, p.influencer_slot
            LIMIT 1
          ),
          status = 'provisional',
          policy_version = ?,
          updated_at = ?
      WHERE position_no = (
        SELECT position_no
        FROM referral_advanced_positions
        WHERE status IN ('available', 'released', 'rejected')
          AND influencer_slot IS NULL
        ORDER BY position_no
        LIMIT 1
      )
        AND EXISTS (
          SELECT 1
          FROM referral_weekly_progress p
          LEFT JOIN referral_advanced_positions a
            ON a.influencer_slot = p.influencer_slot
           AND a.status IN ('provisional', 'approved', 'active')
          WHERE p.provisional_qualified_at IS NOT NULL
            AND a.position_no IS NULL
            AND EXISTS (
              SELECT 1 FROM referral_influencer_slots i
              WHERE i.slot_no = p.influencer_slot AND i.status = 'active'
            )
            AND NOT EXISTS (
              SELECT 1 FROM referral_advanced_reviews r
              WHERE r.influencer_slot = p.influencer_slot
                AND r.qualified_week_id = p.week_id
                AND r.decision = 'rejected'
            )
        )
      RETURNING position_no
    `).bind(REFERRAL_POLICY_VERSION, occurredAt).first<{ position_no: number }>();
    if (!row) break;
    filled += 1;
  }
  return filled;
}

export async function reviewAdvancedPosition(
  db: D1Database,
  input: {
    positionNo: number;
    approved: boolean;
    reviewerId: string;
    reason: string;
    reviewedAt: number;
  },
): Promise<boolean> {
  const reason = input.reason.trim().slice(0, 1_024);
  if (!reason) throw new Error('A review reason is required');
  if (input.approved) {
    const activatesAt = referralWeekBounds(input.reviewedAt).endsAt;
    const results = await db.batch([
      db.prepare(`
        INSERT INTO referral_advanced_reviews
          (id, position_no, influencer_slot, qualified_week_id, qualified_at,
           decision, reviewer_id, reason, reviewed_at, activates_at, policy_version)
        SELECT ?, position_no, influencer_slot, qualified_week_id, qualified_at,
               'approved', ?, ?, ?, ?, ?
        FROM referral_advanced_positions
        WHERE position_no = ? AND status = 'provisional'
      `).bind(
        crypto.randomUUID(),
        input.reviewerId,
        reason,
        input.reviewedAt,
        activatesAt,
        REFERRAL_POLICY_VERSION,
        input.positionNo,
      ),
      db.prepare(`
        UPDATE referral_advanced_positions
        SET status = 'approved',
            reviewed_by = ?,
            reviewed_at = ?,
            review_reason = ?,
            activates_at = ?,
            updated_at = ?
        WHERE position_no = ? AND status = 'provisional'
      `).bind(
        input.reviewerId,
        input.reviewedAt,
        reason,
        activatesAt,
        input.reviewedAt,
        input.positionNo,
      ),
    ]);
    return results[1]?.meta.changes === 1;
  }

  const results = await db.batch([
    db.prepare(`
      INSERT INTO referral_advanced_reviews
        (id, position_no, influencer_slot, qualified_week_id, qualified_at,
         decision, reviewer_id, reason, reviewed_at, policy_version)
      SELECT ?, position_no, influencer_slot, qualified_week_id, qualified_at,
             'rejected', ?, ?, ?, ?
      FROM referral_advanced_positions
      WHERE position_no = ? AND status = 'provisional'
    `).bind(
      crypto.randomUUID(),
      input.reviewerId,
      reason,
      input.reviewedAt,
      REFERRAL_POLICY_VERSION,
      input.positionNo,
    ),
    db.prepare(`
      UPDATE referral_weekly_progress
      SET provisional_qualified_at = NULL, updated_at = ?
      WHERE (week_id, influencer_slot) = (
        SELECT qualified_week_id, influencer_slot
        FROM referral_advanced_positions
        WHERE position_no = ? AND status = 'provisional'
      )
    `).bind(input.reviewedAt, input.positionNo),
    db.prepare(`
      UPDATE referral_advanced_positions
      SET influencer_slot = NULL,
          status = 'available',
          qualified_week_id = NULL,
          qualified_at = NULL,
          reviewed_by = NULL,
          reviewed_at = NULL,
          review_reason = NULL,
          activates_at = NULL,
          updated_at = ?
      WHERE position_no = ? AND status = 'provisional'
    `).bind(input.reviewedAt, input.positionNo),
  ]);
  if (results[2]?.meta.changes === 1) await fillAdvancedVacancies(db, input.reviewedAt);
  return results[2]?.meta.changes === 1;
}

export async function activateApprovedAdvancedPositions(
  db: D1Database,
  occurredAt = Math.floor(Date.now() / 1000),
): Promise<number> {
  const eligible = await db.prepare(`
    SELECT a.position_no, a.influencer_slot, (
      SELECT w.starts_at
      FROM referral_weeks w
      JOIN referral_program_state p ON p.id = 'singleton'
      WHERE w.state = 'open'
        AND p.state = 'active'
        AND w.starts_at <= ?
        AND w.ends_at > ?
        AND w.starts_at >= a.activates_at
      LIMIT 1
    ) AS effective_week_start
    FROM referral_advanced_positions a
    JOIN referral_influencer_slots i ON i.slot_no = a.influencer_slot
    WHERE a.status = 'approved'
      AND i.status = 'active'
      AND a.activates_at IS NOT NULL
      AND a.activates_at <= ?
      AND EXISTS (
        SELECT 1 FROM referral_weeks w
        JOIN referral_program_state p ON p.id = 'singleton'
        WHERE w.state = 'open'
          AND p.state = 'active'
          AND w.starts_at <= ?
          AND w.ends_at > ?
          AND w.starts_at >= a.activates_at
      )
    ORDER BY a.position_no
  `).bind(occurredAt, occurredAt, occurredAt, occurredAt, occurredAt).all<{
    position_no: number;
    influencer_slot: number;
    effective_week_start: number;
  }>();
  let activated = 0;
  for (const row of eligible.results) {
    const results = await db.batch([
      db.prepare(`
        UPDATE referral_influencer_slots
        SET tier = 'advanced', advanced_effective_at = ?, updated_at = ?
        WHERE slot_no = ? AND status = 'active'
      `).bind(row.effective_week_start, occurredAt, row.influencer_slot),
      db.prepare(`
        UPDATE referral_advanced_positions
        SET status = 'active', updated_at = ?
        WHERE position_no = ? AND status = 'approved'
          AND EXISTS (
            SELECT 1 FROM referral_influencer_slots i
            WHERE i.slot_no = referral_advanced_positions.influencer_slot
              AND i.status = 'active'
              AND i.tier = 'advanced'
          )
      `).bind(occurredAt, row.position_no),
    ]);
    if (results[1]?.meta.changes === 1) activated += 1;
  }
  return activated;
}

export async function pauseReferralProgram(
  db: D1Database,
  actorId: string,
  reason: string,
  effectiveAt = Math.floor(Date.now() / 1000),
): Promise<boolean> {
  const boundedReason = reason.trim();
  if (boundedReason.length < 8 || boundedReason.length > 1_000) {
    throw new Error('Pause reason must be between 8 and 1000 characters');
  }
  const results = await db.batch([
    db.prepare(`
      UPDATE referral_program_state
      SET state = 'paused',
          pause_effective_at = CASE
            WHEN state = 'paused' AND pause_effective_at IS NOT NULL
              THEN MIN(pause_effective_at, ?)
            ELSE ?
          END,
          updated_by = ?,
          updated_at = ?
      WHERE id = 'singleton' AND state != 'closed'
    `).bind(effectiveAt, effectiveAt, actorId, effectiveAt),
    db.prepare(`
      UPDATE referral_weeks
      SET state = 'paused',
          pause_effective_at = COALESCE(pause_effective_at, ?),
          updated_at = ?
      WHERE state = 'open' AND starts_at <= ? AND ends_at > ?
    `).bind(effectiveAt, effectiveAt, effectiveAt, effectiveAt),
    db.prepare(`
      INSERT INTO referral_program_transitions
        (id, transition, actor_id, reason, effective_at, accrual_generation,
         gate_evidence, policy_version)
      SELECT ?, 'pause', ?, ?, ?, accrual_generation, NULL, ?
      FROM referral_program_state
      WHERE id = 'singleton' AND state = 'paused'
    `).bind(
      crypto.randomUUID(),
      actorId,
      boundedReason,
      effectiveAt,
      REFERRAL_POLICY_VERSION,
    ),
    db.prepare(`
      UPDATE referral_accrual_intervals
      SET state = 'paused', ends_at = ?, pause_reason = ?, actor_id = ?
      WHERE week_id = (
        SELECT id FROM referral_weeks
        WHERE state = 'paused' AND starts_at <= ? AND ends_at > ?
        ORDER BY starts_at DESC LIMIT 1
      )
        AND state = 'open'
        AND starts_at <= ?
        AND (ends_at IS NULL OR ends_at > ?)
    `).bind(
      effectiveAt,
      boundedReason,
      actorId,
      effectiveAt,
      effectiveAt,
      effectiveAt,
      effectiveAt,
    ),
  ]);
  return results[0]?.meta.changes === 1;
}

export async function advanceReferralLifecycle(
  db: D1Database,
  input: {
    actorId: string;
    effectiveAt: number;
    evidenceId: string;
    fundedCapInr?: number;
  },
): Promise<ReferralLifecycleResult> {
  const week = referralWeekBounds(input.effectiveAt);
  const evidence = await loadReferralGate(db, input.evidenceId, input.effectiveAt);
  if (!evidence) {
    return {
      opened: false, alreadyOpen: false, week,
      activatedAdvancedPositions: 0, reasons: ['authoritative-evidence-not-found'],
    };
  }
  const gate = evaluateWeekOpening(
    evidence.gate,
    input.fundedCapInr === undefined ? {} : { requiredReserveInr: input.fundedCapInr },
  );
  if (!gate.allowed) {
    return {
      opened: false,
      alreadyOpen: false,
      week,
      activatedAdvancedPositions: 0,
      reasons: gate.reasons,
    };
  }
  const serializedGate = evidence.serialized;
  if (serializedGate.length > 8_192) {
    return {
      opened: false,
      alreadyOpen: false,
      week,
      activatedAdvancedPositions: 0,
      reasons: ['gate-evidence-too-large'],
    };
  }
  const results = await db.batch([
    db.prepare(`
      UPDATE referral_weeks
      SET state = 'finalized', updated_at = ?
      WHERE ends_at <= ? AND state IN ('open', 'paused', 'review')
    `).bind(input.effectiveAt, input.effectiveAt),
    db.prepare(`
      INSERT INTO referral_weeks
        (id, week_key, starts_at, ends_at, state, policy_version,
         opened_at, accrual_generation, created_at, updated_at)
      VALUES (?, ?, ?, ?, 'closed', ?, NULL, 0, ?, ?)
      ON CONFLICT(id) DO NOTHING
    `).bind(
      week.id,
      week.key,
      week.startsAt,
      week.endsAt,
      REFERRAL_POLICY_VERSION,
      input.effectiveAt,
      input.effectiveAt,
    ),
    db.prepare(`
      UPDATE referral_program_state
      SET state = 'active',
          resumed_at = ?,
          pause_effective_at = NULL,
          accrual_generation = accrual_generation + 1,
          updated_by = ?,
          updated_at = ?
      WHERE id = 'singleton'
        AND state != 'closed'
        AND NOT EXISTS (
          SELECT 1 FROM referral_weeks
          WHERE id = ? AND state = 'open'
        )
    `).bind(
      input.effectiveAt,
      input.actorId,
      input.effectiveAt,
      week.id,
    ),
    db.prepare(`
      UPDATE referral_weeks
      SET state = 'open',
          opened_at = COALESCE(opened_at, ?),
          resumed_at = ?,
          accrual_generation = (
            SELECT accrual_generation FROM referral_program_state
            WHERE id = 'singleton'
          ),
          updated_at = ?
      WHERE id = ? AND state IN ('closed', 'paused', 'review')
        AND EXISTS (
          SELECT 1 FROM referral_program_state
          WHERE id = 'singleton' AND state = 'active'
        )
    `).bind(
      input.effectiveAt,
      input.effectiveAt,
      input.effectiveAt,
      week.id,
    ),
    db.prepare(`
      UPDATE referral_influencer_slots
      SET tier = 'advanced',
          advanced_effective_at = ?,
          updated_at = ?
      WHERE status = 'active'
        AND EXISTS (
          SELECT 1 FROM referral_advanced_positions a
          WHERE a.influencer_slot = referral_influencer_slots.slot_no
            AND a.status = 'approved'
            AND a.activates_at IS NOT NULL
            AND a.activates_at <= ?
            AND EXISTS (
              SELECT 1 FROM referral_weeks w
              WHERE w.id = ? AND w.state = 'open'
                AND w.starts_at >= a.activates_at
            )
        )
    `).bind(
      week.startsAt,
      input.effectiveAt,
      week.startsAt,
      week.id,
    ),
    db.prepare(`
      UPDATE referral_advanced_positions
      SET status = 'active', updated_at = ?
      WHERE status = 'approved'
        AND activates_at IS NOT NULL
        AND activates_at <= ?
        AND EXISTS (
          SELECT 1 FROM referral_influencer_slots i
          WHERE i.slot_no = referral_advanced_positions.influencer_slot
            AND i.status = 'active'
            AND i.tier = 'advanced'
        )
        AND EXISTS (
          SELECT 1 FROM referral_weeks w
          WHERE w.id = ? AND w.state = 'open'
            AND w.starts_at >= referral_advanced_positions.activates_at
        )
    `).bind(input.effectiveAt, week.startsAt, week.id),
    db.prepare(`
      INSERT OR IGNORE INTO referral_program_transitions
        (id, transition, actor_id, effective_at, accrual_generation,
         gate_evidence, policy_version)
      SELECT ?, 'resume', ?, ?, accrual_generation, ?, ?
      FROM referral_program_state
      WHERE id = 'singleton'
        AND state = 'active'
        AND EXISTS (
          SELECT 1 FROM referral_weeks
          WHERE id = ? AND state = 'open' AND opened_at = ?
        )
    `).bind(
      crypto.randomUUID(),
      input.actorId,
      input.effectiveAt,
      serializedGate,
      REFERRAL_POLICY_VERSION,
      week.id,
      input.effectiveAt,
    ),
  ]);
  const opened = results[3]?.meta.changes === 1;
  const current = await db.prepare(`
    SELECT state FROM referral_weeks WHERE id = ?
  `).bind(week.id).first<{ state: string }>();
  return {
    opened,
    alreadyOpen: !opened && current?.state === 'open',
    week,
    activatedAdvancedPositions: results[5]?.meta.changes ?? 0,
    reasons: opened || current?.state === 'open' ? [] : ['week-open-failed'],
  };
}

export async function resumeReferralProgram(
  db: D1Database,
  input: {
    actorId: string;
    effectiveAt: number;
    evidenceId: string;
    fundedCapInr?: number;
  },
): Promise<{ resumed: boolean; reasons: string[] }> {
  const evidence = await loadReferralGate(db, input.evidenceId, input.effectiveAt);
  if (!evidence) return { resumed: false, reasons: ['authoritative-evidence-not-found'] };
  const envelope = await db.prepare(`
    SELECT funded_cap_inr FROM referral_weekly_envelopes
    WHERE status IN ('paused', 'funded')
    ORDER BY opened_at DESC LIMIT 1
  `).first<{ funded_cap_inr: number }>();
  const requiredReserveInr = input.fundedCapInr ?? envelope?.funded_cap_inr;
  const gate = evaluateWeekOpening(
    evidence.gate,
    requiredReserveInr === undefined ? {} : { requiredReserveInr },
  );
  if (!gate.allowed) return { resumed: false, reasons: gate.reasons };
  const serializedGate = evidence.serialized;
  if (serializedGate.length > 8_192) {
    return { resumed: false, reasons: ['gate-evidence-too-large'] };
  }
  const results = await db.batch([
    db.prepare(`
      UPDATE referral_program_state
      SET state = 'active',
          resumed_at = ?,
          accrual_generation = accrual_generation + 1,
          updated_by = ?,
          updated_at = ?
      WHERE id = 'singleton'
        AND state = 'paused'
        AND EXISTS (
          SELECT 1 FROM referral_weeks
          WHERE state = 'paused' AND starts_at <= ? AND ends_at > ?
        )
    `).bind(
      input.effectiveAt,
      input.actorId,
      input.effectiveAt,
      input.effectiveAt,
      input.effectiveAt,
    ),
    db.prepare(`
      UPDATE referral_weeks
      SET state = 'open',
          resumed_at = ?,
          accrual_generation = accrual_generation + 1,
          updated_at = ?
      WHERE state = 'paused' AND starts_at <= ? AND ends_at > ?
        AND EXISTS (
          SELECT 1 FROM referral_program_state
          WHERE id = 'singleton' AND state = 'active'
        )
    `).bind(
      input.effectiveAt,
      input.effectiveAt,
      input.effectiveAt,
      input.effectiveAt,
    ),
    db.prepare(`
      INSERT INTO referral_program_transitions
        (id, transition, actor_id, effective_at, accrual_generation,
         gate_evidence, policy_version)
      SELECT ?, 'resume', ?, ?, accrual_generation, ?, ?
      FROM referral_program_state
      WHERE id = 'singleton'
        AND state = 'active'
        AND resumed_at = ?
    `).bind(
      crypto.randomUUID(),
      input.actorId,
      input.effectiveAt,
      serializedGate,
      REFERRAL_POLICY_VERSION,
      input.effectiveAt,
    ),
    db.prepare(`
      INSERT OR IGNORE INTO referral_accrual_intervals
        (id, week_id, generation, starts_at, state, actor_id, created_at)
      SELECT ?, id, accrual_generation, ?, 'open', ?, ?
      FROM referral_weeks
      WHERE state = 'open' AND starts_at <= ? AND ends_at > ?
    `).bind(
      crypto.randomUUID(),
      input.effectiveAt,
      input.actorId,
      input.effectiveAt,
      input.effectiveAt,
      input.effectiveAt,
    ),
  ]);
  return {
    resumed: results[0]?.meta.changes === 1 && results[1]?.meta.changes === 1,
    reasons: results[0]?.meta.changes === 1 && results[1]?.meta.changes === 1
      ? []
      : ['no-paused-current-week'],
  };
}

export async function weeklyReferralMetrics(
  db: D1Database,
  weekId: string,
  influencerSlot: number,
): Promise<{
  uniqueBrowsers: number;
  authenticatedAccounts: number;
  lowConfidenceIdentities: number;
  repeatedWeeklyIdentities: number;
  matureVerified: number;
}> {
  const row = await db.prepare(`
    SELECT
      COUNT(DISTINCT CASE WHEN identity_confidence = 'browser' THEN identity_hash END) AS unique_browsers,
      COUNT(DISTINCT CASE WHEN identity_confidence = 'account' THEN account_id END) AS authenticated_accounts,
      COUNT(DISTINCT CASE WHEN identity_confidence = 'low' THEN identity_hash END) AS low_confidence_identities,
      COALESCE(SUM(CASE WHEN event_count > 1 THEN 1 ELSE 0 END), 0) AS repeated_weekly_identities,
      COALESCE(SUM(CASE WHEN state = 'mature' AND progress_counted = 1 THEN 1 ELSE 0 END), 0) AS mature_verified
    FROM referral_weekly_claims
    WHERE week_id = ? AND credited_influencer_slot = ?
  `).bind(weekId, influencerSlot).first<Record<string, number>>();
  return {
    uniqueBrowsers: row?.unique_browsers ?? 0,
    authenticatedAccounts: row?.authenticated_accounts ?? 0,
    lowConfidenceIdentities: row?.low_confidence_identities ?? 0,
    repeatedWeeklyIdentities: row?.repeated_weekly_identities ?? 0,
    matureVerified: row?.mature_verified ?? 0,
  };
}