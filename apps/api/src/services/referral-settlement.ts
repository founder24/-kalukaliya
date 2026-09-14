import {
  evaluateWeekOpening,
  REFERRAL_POLICY,
  REFERRAL_POLICY_VERSION,
  weeklyRewardInr,
  type ReferralEvidenceGate,
  type ReferralTier,
} from '../contracts/referral-policy';

const MAX_REASON_LENGTH = 1_000;
const MAX_BENEFICIARY_BYTES = 8_192;
const MAX_RECEIPT_BYTES = 5 * 1024 * 1024;
const ALLOWED_RECEIPT_TYPES = new Set(['application/pdf', 'image/png', 'image/jpeg']);

type SettlementStatus =
  | 'calculated' | 'held' | 'approved' | 'paid' | 'failed'
  | 'paused' | 'reversed' | 'clawed_back' | 'corrected';

type PayoutStatus = 'pending' | 'paid' | 'failed' | 'corrected' | 'reversed' | 'clawed_back';

interface GateRow {
  id: string;
  provider_evidence_id: string;
  quality_evidence_id: string;
  finalized_through_at: number;
  quality_measured_at: number;
  gross_reserve_inr: number;
  outstanding_obligations_inr: number;
  attribution_healthy: number;
  deduplication_healthy: number;
  fraud_review_healthy: number;
  settlement_healthy: number;
  recorded_at: number;
  expires_at: number;
}

interface WeekRow {
  id: string;
  week_key: string;
  starts_at: number;
  ends_at: number;
  state: string;
  policy_version: string;
  pause_effective_at: number | null;
  accrual_generation: number;
}

interface StatementRow {
  id: string;
  week_id: string;
  influencer_slot: number;
  user_id: string;
  tier: ReferralTier;
  qualifying_week: number;
  mature_verified_count: number;
  payable_claim_count: number;
  gross_amount_inr: number;
  funded_cap_inr: number;
  status: SettlementStatus;
  quality_hold_released_at: number;
  approved_at: number | null;
  paid_at: number | null;
  failure_reason: string | null;
}

function boundedReason(reason: string, label = 'Reason'): string {
  const value = reason.trim();
  if (value.length < 8 || value.length > MAX_REASON_LENGTH) {
    throw new Error(`${label} must be between 8 and ${MAX_REASON_LENGTH} characters`);
  }
  return value;
}

function jsonBytes(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).byteLength;
}

function gateFromRow(row: GateRow, now: number): ReferralEvidenceGate {
  return {
    programState: 'active',
    unencumberedReserveInr: row.gross_reserve_inr - row.outstanding_obligations_inr,
    nowEpochSeconds: now,
    revenue: {
      network: 'adsense',
      finalized: true,
      finalizedThroughEpochSeconds: row.finalized_through_at,
    },
    quality: {
      finalized: true,
      measuredAtEpochSeconds: row.quality_measured_at,
    },
    controls: {
      attributionHealthy: row.attribution_healthy === 1,
      deduplicationHealthy: row.deduplication_healthy === 1,
      fraudReviewHealthy: row.fraud_review_healthy === 1,
      settlementHealthy: row.settlement_healthy === 1,
    },
  };
}

async function loadGate(
  db: D1Database,
  evidenceId: string,
  now: number,
  fundedCapInr: number,
): Promise<{ row: GateRow; serialized: string }> {
  const row = await db.prepare(`
    SELECT * FROM referral_gate_evidence
    WHERE id = ? OR provider_evidence_id = ?
    ORDER BY recorded_at DESC LIMIT 1
  `).bind(evidenceId, evidenceId).first<GateRow>();
  if (!row) throw new Error('Authoritative settlement evidence not found');
  const gate = gateFromRow(row, now);
  const result = evaluateWeekOpening(gate, { requiredReserveInr: fundedCapInr });
  if (!result.allowed) throw new Error(`Settlement gate rejected: ${result.reasons.join(', ')}`);
  if (row.expires_at <= now) throw new Error('Settlement evidence is expired');
  const serialized = JSON.stringify({
    evidence_id: row.id,
    provider_evidence_id: row.provider_evidence_id,
    quality_evidence_id: row.quality_evidence_id,
    finalized_through_at: row.finalized_through_at,
    quality_measured_at: row.quality_measured_at,
    unencumbered_reserve_inr: gate.unencumberedReserveInr,
    funded_cap_inr: fundedCapInr,
    controls: gate.controls,
  });
  if (serialized.length > 8_192) throw new Error('Settlement evidence is too large');
  return { row, serialized };
}

function validateFundedCap(value: number | undefined): number {
  const cap = value ?? REFERRAL_POLICY.maximumWeeklyRewardExposureInr;
  if (!Number.isSafeInteger(cap) || cap < 0 || cap > REFERRAL_POLICY.maximumWeeklyRewardExposureInr) {
    throw new Error(`Funded weekly cap must be between 0 and ${REFERRAL_POLICY.maximumWeeklyRewardExposureInr}`);
  }
  return cap;
}

export async function ensureWeeklySettlementEnvelope(
  db: D1Database,
  input: {
    weekId: string;
    evidenceId: string;
    fundedCapInr?: number;
    actorId: string;
    openedAt: number;
  },
): Promise<{ weekId: string; fundedCapInr: number; status: string }> {
  const fundedCapInr = validateFundedCap(input.fundedCapInr);
  const week = await db.prepare(`
    SELECT id, starts_at, ends_at, state, policy_version, pause_effective_at, accrual_generation
    FROM referral_weeks WHERE id = ?
  `).bind(input.weekId).first<WeekRow>();
  if (!week) throw new Error('Referral week not found');
  const gate = await loadGate(db, input.evidenceId, input.openedAt, fundedCapInr);
  const status = fundedCapInr > 0 ? 'funded' : 'paused';

  const snapshots = await db.prepare(`
    SELECT slot_no, user_id, tier, advanced_effective_at
    FROM referral_influencer_slots
    WHERE status = 'active' AND user_id IS NOT NULL
    ORDER BY slot_no
  `).all<{ slot_no: number; user_id: string; tier: string; advanced_effective_at: number | null }>();

  const statements: D1PreparedStatement[] = [
    db.prepare(`
      INSERT INTO referral_weekly_envelopes
        (week_id, worst_case_exposure_inr, funded_cap_inr, reserved_inr,
         status, provider_evidence_id, quality_evidence_id, gate_evidence_id,
         finalized_through_at, quality_measured_at, opened_at, updated_at)
      VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(week_id) DO UPDATE SET
        funded_cap_inr = CASE
          WHEN referral_weekly_envelopes.status IN ('closed', 'held') THEN referral_weekly_envelopes.funded_cap_inr
          ELSE excluded.funded_cap_inr
        END,
        status = CASE
          WHEN referral_weekly_envelopes.status IN ('closed', 'held') THEN referral_weekly_envelopes.status
          ELSE excluded.status
        END,
        provider_evidence_id = excluded.provider_evidence_id,
        quality_evidence_id = excluded.quality_evidence_id,
        gate_evidence_id = excluded.gate_evidence_id,
        finalized_through_at = excluded.finalized_through_at,
        quality_measured_at = excluded.quality_measured_at,
        updated_at = excluded.updated_at
    `).bind(
      input.weekId,
      REFERRAL_POLICY.maximumWeeklyRewardExposureInr,
      fundedCapInr,
      status,
      gate.row.provider_evidence_id,
      gate.row.quality_evidence_id,
      gate.row.id,
      gate.row.finalized_through_at,
      gate.row.quality_measured_at,
      input.openedAt,
      input.openedAt,
    ),
    db.prepare(`
      INSERT OR IGNORE INTO referral_accrual_intervals
        (id, week_id, generation, starts_at, state, actor_id, created_at)
      VALUES (?, ?, 0, ?, 'open', ?, ?)
    `).bind(crypto.randomUUID(), input.weekId, week.starts_at, input.actorId, input.openedAt),
  ];

  for (const snapshot of snapshots.results) {
    const advanced = snapshot.tier === 'advanced'
      && snapshot.advanced_effective_at !== null
      && snapshot.advanced_effective_at <= week.starts_at;
    statements.push(db.prepare(`
      INSERT OR IGNORE INTO referral_weekly_tier_snapshots
        (id, week_id, influencer_slot, user_id, tier, qualifying_week, effective_at, policy_version)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `).bind(
      crypto.randomUUID(),
      input.weekId,
      snapshot.slot_no,
      snapshot.user_id,
      advanced ? 'advanced' : 'basic',
      advanced ? 0 : 1,
      week.starts_at,
      REFERRAL_POLICY_VERSION,
    ));
  }
  await db.batch(statements);
  return { weekId: input.weekId, fundedCapInr, status };
}

export async function openWeeklySettlement(
  db: D1Database,
  input: {
    weekId: string;
    evidenceId: string;
    fundedCapInr?: number;
    actorId: string;
    openedAt: number;
  },
): Promise<{ fundedCapInr: number; status: string }> {
  const result = await ensureWeeklySettlementEnvelope(db, input);
  return { fundedCapInr: result.fundedCapInr, status: result.status };
}

export async function closeAccrualInterval(
  db: D1Database,
  input: { weekId: string; effectiveAt: number; actorId: string; reason: string },
): Promise<boolean> {
  const reason = boundedReason(input.reason, 'Pause reason');
  const result = await db.prepare(`
    UPDATE referral_accrual_intervals
    SET state = 'paused', ends_at = ?, pause_reason = ?, actor_id = ?
    WHERE week_id = ? AND state = 'open' AND starts_at <= ? AND (ends_at IS NULL OR ends_at > ?)
  `).bind(input.effectiveAt, reason, input.actorId, input.weekId, input.effectiveAt, input.effectiveAt).run();
  return (result.meta.changes ?? 0) === 1;
}

export async function openResumedAccrualInterval(
  db: D1Database,
  input: { weekId: string; generation: number; startsAt: number; actorId: string },
): Promise<boolean> {
  const result = await db.prepare(`
    INSERT OR IGNORE INTO referral_accrual_intervals
      (id, week_id, generation, starts_at, state, actor_id, created_at)
    VALUES (?, ?, ?, ?, 'open', ?, ?)
  `).bind(crypto.randomUUID(), input.weekId, input.generation, input.startsAt, input.actorId, input.startsAt).run();
  return (result.meta.changes ?? 0) === 1;
}

async function loadWeekAndEnvelope(
  db: D1Database,
  weekId: string,
): Promise<{ week: WeekRow; fundedCap: number; envelopeStatus: string; qualityMeasuredAt: number }> {
  const row = await db.prepare(`
    SELECT w.id, w.week_key, w.starts_at, w.ends_at, w.state, w.policy_version,
           w.pause_effective_at, w.accrual_generation,
           e.funded_cap_inr, e.status AS envelope_status, e.quality_measured_at
    FROM referral_weeks w
    JOIN referral_weekly_envelopes e ON e.week_id = w.id
    WHERE w.id = ?
  `).bind(weekId).first<WeekRow & {
    funded_cap_inr: number;
    envelope_status: string;
    quality_measured_at: number;
  }>();
  if (!row) throw new Error('Funded settlement envelope not found');
  return {
    week: row,
    fundedCap: row.funded_cap_inr,
    envelopeStatus: row.envelope_status,
    qualityMeasuredAt: row.quality_measured_at,
  };
}

export async function settleReferralWeek(
  db: D1Database,
  input: { weekId: string; actorId: string; settledAt: number; reason?: string },
): Promise<{ weekId: string; status: string; statements: number; totalInr: number; idempotent: boolean }> {
  const reason = boundedReason(input.reason ?? 'Weekly settlement calculated after quality review.');
  const { week, fundedCap, envelopeStatus, qualityMeasuredAt } = await loadWeekAndEnvelope(db, input.weekId);
  if (envelopeStatus === 'closed' || envelopeStatus === 'held') {
    const existing = await db.prepare(`
      SELECT COUNT(*) AS statements, COALESCE(SUM(gross_amount_inr), 0) AS total
      FROM referral_weekly_statements WHERE week_id = ?
    `).bind(input.weekId).first<{ statements: number; total: number }>();
    return {
      weekId: input.weekId,
      status: envelopeStatus,
      statements: existing?.statements ?? 0,
      totalInr: existing?.total ?? 0,
      idempotent: true,
    };
  }
  if (week.ends_at > input.settledAt) throw new Error('A week cannot settle before it ends');
  if (!['open', 'paused', 'review', 'finalized', 'closed'].includes(week.state)) {
    throw new Error('Week must be open, paused, or under review before settlement');
  }
  if (qualityMeasuredAt > input.settledAt) throw new Error('Quality/fraud hold is not released');

  const snapshots = await db.prepare(`
    SELECT s.influencer_slot, s.user_id, s.tier, s.qualifying_week,
           COUNT(c.id) AS mature_count
    FROM referral_weekly_tier_snapshots s
    LEFT JOIN referral_weekly_claims c
      ON c.week_id = s.week_id
     AND c.credited_influencer_slot = s.influencer_slot
     AND c.state = 'mature'
     AND c.progress_counted = 1
     AND c.identity_confidence IN ('browser', 'account')
     AND c.first_seen_at < COALESCE((SELECT pause_effective_at FROM referral_weeks WHERE id = s.week_id), 9223372036854775807)
    WHERE s.week_id = ?
    GROUP BY s.influencer_slot, s.user_id, s.tier, s.qualifying_week
    ORDER BY s.influencer_slot
  `).bind(input.weekId).all<{
    influencer_slot: number;
    user_id: string;
    tier: ReferralTier;
    qualifying_week: number;
    mature_count: number;
  }>();

  let remaining = fundedCap;
  const statementRows: Array<{
    statementId: string;
    snapshot: typeof snapshots.results[number];
    claimIds: string[];
    gross: number;
  }> = [];
  for (const snapshot of snapshots.results) {
    const claims = await db.prepare(`
      SELECT id FROM referral_weekly_claims
      WHERE week_id = ? AND credited_influencer_slot = ?
        AND state = 'mature' AND progress_counted = 1
        AND identity_confidence IN ('browser', 'account')
        AND first_seen_at < COALESCE(
          (SELECT pause_effective_at FROM referral_weeks WHERE id = ?),
          9223372036854775807
        )
      ORDER BY matured_at, id
    `).bind(input.weekId, snapshot.influencer_slot, input.weekId).all<{ id: string }>();
    const perInfluencerCap = weeklyRewardInr(
      snapshot.tier,
      Number(snapshot.mature_count),
      { qualifyingWeek: snapshot.qualifying_week === 1 },
    );
    const gross = Math.min(perInfluencerCap, remaining);
    const claimIds = claims.results.slice(0, gross).map(claim => claim.id);
    statementRows.push({
      statementId: crypto.randomUUID(),
      snapshot,
      claimIds,
      gross,
    });
    remaining -= gross;
  }

  const evidenceSnapshot = JSON.stringify({
    week_id: input.weekId,
    generation: week.accrual_generation,
    quality_hold_released_at: qualityMeasuredAt,
    funded_cap_inr: fundedCap,
    settled_at: input.settledAt,
  });
  const statements: D1PreparedStatement[] = [];
  for (const row of statementRows) {
    statements.push(db.prepare(`
      INSERT OR IGNORE INTO referral_weekly_statements
        (id, week_id, influencer_slot, user_id, tier, qualifying_week,
         mature_verified_count, payable_claim_count, rate_inr, gross_amount_inr,
         funded_cap_inr, status, quality_hold_released_at, accrual_generation,
         evidence_snapshot, created_at, updated_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, 'held', ?, ?, ?, ?, ?)
    `).bind(
      row.statementId,
      input.weekId,
      row.snapshot.influencer_slot,
      row.snapshot.user_id,
      row.snapshot.tier,
      row.snapshot.qualifying_week,
      row.snapshot.mature_count,
      row.claimIds.length,
      row.gross,
      fundedCap,
      qualityMeasuredAt,
      week.accrual_generation,
      evidenceSnapshot,
      input.settledAt,
      input.settledAt,
    ));
    for (const claimId of row.claimIds) {
      statements.push(db.prepare(`
        INSERT OR IGNORE INTO referral_statement_claims
          (statement_id, claim_id, amount_inr, created_at)
        VALUES (?, ?, 1, ?)
      `).bind(row.statementId, claimId, input.settledAt));
    }
    statements.push(db.prepare(`
      INSERT INTO referral_settlement_audits
        (id, week_id, statement_id, influencer_slot, action, to_status,
         actor_id, reason, metadata, policy_version, evidence_snapshot, occurred_at)
      VALUES (?, ?, ?, ?, 'statement_calculated', 'held', ?, ?, ?, ?, ?, ?)
    `).bind(
      crypto.randomUUID(),
      input.weekId,
      row.statementId,
      row.snapshot.influencer_slot,
      input.actorId,
      reason,
      JSON.stringify({
        mature_verified_count: row.snapshot.mature_count,
        payable_claim_count: row.claimIds.length,
        gross_amount_inr: row.gross,
      }),
      REFERRAL_POLICY_VERSION,
      evidenceSnapshot,
      input.settledAt,
    ));
  }
  statements.push(db.prepare(`
    UPDATE referral_weekly_envelopes
    SET reserved_inr = COALESCE((SELECT SUM(gross_amount_inr) FROM referral_weekly_statements WHERE week_id = ?), 0),
        status = 'held', closed_at = ?, updated_at = ?
    WHERE week_id = ? AND status NOT IN ('closed', 'held')
  `).bind(input.weekId, input.settledAt, input.settledAt, input.weekId));
  statements.push(db.prepare(`
    UPDATE referral_weeks SET state = 'finalized', updated_at = ?
    WHERE id = ? AND state IN ('paused', 'review', 'closed')
  `).bind(input.settledAt, input.weekId));
  if (statements.length) await db.batch(statements);
  return {
    weekId: input.weekId,
    status: 'held',
    statements: statementRows.length,
    totalInr: fundedCap - remaining,
    idempotent: false,
  };
}

export async function listSettlementStatements(
  db: D1Database,
  weekId?: string,
): Promise<StatementRow[]> {
  const rows = await db.prepare(`
    SELECT id, week_id, influencer_slot, user_id, tier, qualifying_week,
           mature_verified_count, payable_claim_count, gross_amount_inr,
           funded_cap_inr, status, quality_hold_released_at, approved_at, paid_at,
           failure_reason
    FROM referral_weekly_statements
    ${weekId ? 'WHERE week_id = ?' : ''}
    ORDER BY week_id DESC, influencer_slot
  `).bind(...(weekId ? [weekId] : [])).all<StatementRow>();
  return rows.results;
}

export async function listStatementsForUser(
  db: D1Database,
  userId: string,
): Promise<StatementRow[]> {
  const rows = await db.prepare(`
    SELECT id, week_id, influencer_slot, user_id, tier, qualifying_week,
           mature_verified_count, payable_claim_count, gross_amount_inr,
           funded_cap_inr, status, quality_hold_released_at, approved_at, paid_at,
           failure_reason
    FROM referral_weekly_statements
    WHERE user_id = ?
    ORDER BY week_id DESC
  `).bind(userId).all<StatementRow>();
  return rows.results;
}

export async function approveSettlementStatement(
  db: D1Database,
  input: { statementId: string; actorId: string; reason: string; approvedAt: number },
): Promise<StatementRow> {
  const reason = boundedReason(input.reason, 'Approval reason');
  const existing = await db.prepare(`
    SELECT * FROM referral_weekly_statements WHERE id = ?
  `).bind(input.statementId).first<StatementRow>();
  if (!existing) throw new Error('Settlement statement not found');
  if (existing.status === 'approved' || existing.status === 'paid') return existing;
  if (existing.status !== 'held') throw new Error('Only held statements can be approved');
  const beneficiary = await db.prepare(`
    SELECT id FROM referral_beneficiaries
    WHERE user_id = ? AND status = 'verified'
    ORDER BY verified_at DESC LIMIT 1
  `).bind(existing.user_id).first<{ id: string }>();
  if (!beneficiary) throw new Error('A verified beneficiary is required before approval');
  const result = await db.batch([
    db.prepare(`
      UPDATE referral_weekly_statements
      SET status = 'approved', approved_by = ?, approved_at = ?, updated_at = ?
      WHERE id = ? AND status = 'held'
    `).bind(input.actorId, input.approvedAt, input.approvedAt, input.statementId),
    db.prepare(`
      INSERT INTO referral_settlement_audits
        (id, week_id, statement_id, influencer_slot, action, from_status, to_status,
         actor_id, reason, policy_version, occurred_at)
      VALUES (?, ?, ?, ?, 'statement_approved', 'held', 'approved', ?, ?, ?, ?)
    `).bind(
      crypto.randomUUID(),
      existing.week_id,
      existing.id,
      existing.influencer_slot,
      input.actorId,
      reason,
      REFERRAL_POLICY_VERSION,
      input.approvedAt,
    ),
  ]);
  if (result[0]?.meta.changes !== 1) throw new Error('Statement approval race lost');
  return { ...existing, status: 'approved', approved_at: input.approvedAt };
}

export async function recordBeneficiary(
  db: D1Database,
  input: { userId: string; actorId: string; details: unknown; occurredAt: number },
): Promise<{ id: string; status: string }> {
  if (!input.details || typeof input.details !== 'object' || Array.isArray(input.details)) {
    throw new Error('Beneficiary details must be an object');
  }
  if (jsonBytes(input.details) > MAX_BENEFICIARY_BYTES) throw new Error('Beneficiary details are too large');
  const snapshot = JSON.stringify(input.details);
  const existing = await db.prepare(`
    SELECT id, status, submitted_by FROM referral_beneficiaries
    WHERE user_id = ? AND status IN ('pending', 'verified')
    ORDER BY created_at DESC LIMIT 1
  `).bind(input.userId).first<{ id: string; status: string; submitted_by: string }>();
  if (existing?.status === 'verified') throw new Error('Verified beneficiary changes require revocation review');
  if (existing) {
    await db.prepare(`
      UPDATE referral_beneficiaries
      SET details_snapshot = ?, submitted_by = ?, updated_at = ?
      WHERE id = ? AND status = 'pending'
    `).bind(snapshot, input.actorId, input.occurredAt, existing.id).run();
    return { id: existing.id, status: 'pending' };
  }
  const id = crypto.randomUUID();
  await db.prepare(`
    INSERT INTO referral_beneficiaries
      (id, user_id, status, details_snapshot, submitted_by, created_at, updated_at)
    VALUES (?, ?, 'pending', ?, ?, ?, ?)
  `).bind(id, input.userId, snapshot, input.actorId, input.occurredAt, input.occurredAt).run();
  return { id, status: 'pending' };
}

export async function listBeneficiaries(
  db: D1Database,
): Promise<Array<{
  id: string;
  user_id: string;
  status: string;
  details_snapshot: string;
  submitted_by: string;
  verified_by: string | null;
  verified_at: number | null;
}>> {
  const rows = await db.prepare(`
    SELECT id, user_id, status, details_snapshot, submitted_by, verified_by, verified_at
    FROM referral_beneficiaries
    ORDER BY updated_at DESC, id
  `).all<{
    id: string;
    user_id: string;
    status: string;
    details_snapshot: string;
    submitted_by: string;
    verified_by: string | null;
    verified_at: number | null;
  }>();
  return rows.results;
}

export async function reviewBeneficiary(
  db: D1Database,
  input: { beneficiaryId: string; actorId: string; approved: boolean; reason: string; occurredAt: number },
): Promise<{ id: string; status: string }> {
  const reason = boundedReason(input.reason, 'Beneficiary review reason');
  const existing = await db.prepare(`
    SELECT id, status, submitted_by FROM referral_beneficiaries WHERE id = ?
  `).bind(input.beneficiaryId).first<{ id: string; status: string; submitted_by: string }>();
  if (!existing) throw new Error('Beneficiary not found');
  if (existing.submitted_by === input.actorId) throw new Error('Separation of duties requires another reviewer');
  if (existing.status !== 'pending') return { id: existing.id, status: existing.status };
  const status = input.approved ? 'verified' : 'rejected';
  const result = await db.batch([
    db.prepare(`
      UPDATE referral_beneficiaries
      SET status = ?, verified_by = ?, verified_at = ?, decision_reason = ?, updated_at = ?
      WHERE id = ? AND status = 'pending'
    `).bind(status, input.actorId, input.occurredAt, reason, input.occurredAt, input.beneficiaryId),
    db.prepare(`
      INSERT INTO referral_settlement_audits
        (id, action, actor_id, reason, metadata, policy_version, occurred_at)
      VALUES (?, 'beneficiary_reviewed', ?, ?, ?, ?, ?)
    `).bind(
      crypto.randomUUID(),
      input.actorId,
      reason,
      JSON.stringify({ beneficiary_id: input.beneficiaryId, status }),
      REFERRAL_POLICY_VERSION,
      input.occurredAt,
    ),
  ]);
  if (result[0]?.meta.changes !== 1) throw new Error('Beneficiary review race lost');
  return { id: existing.id, status };
}

export async function recordSettlementPayout(
  db: D1Database,
  input: {
    statementId: string;
    actorId: string;
    status: PayoutStatus;
    idempotencyKey: string;
    utrReference?: string;
    providerReference?: string;
    reason: string;
    occurredAt: number;
  },
): Promise<{ payoutId: string; statementId: string; status: PayoutStatus; idempotent: boolean }> {
  const reason = boundedReason(input.reason, 'Payout reason');
  if (!/^[A-Za-z0-9._-]{8,128}$/.test(input.idempotencyKey)) {
    throw new Error('A valid payout idempotency key is required');
  }
  if (input.status === 'paid' && !input.utrReference?.trim()) {
    throw new Error('UTR/reference is required for a paid payout');
  }
  const statement = await db.prepare(`
    SELECT * FROM referral_weekly_statements WHERE id = ?
  `).bind(input.statementId).first<StatementRow>();
  if (!statement) throw new Error('Settlement statement not found');
  const existing = await db.prepare(`
    SELECT id, status FROM referral_payouts WHERE statement_id = ?
  `).bind(input.statementId).first<{ id: string; status: PayoutStatus }>();
  if (existing && existing.status !== 'failed' && existing.status !== 'pending') {
    return { payoutId: existing.id, statementId: input.statementId, status: existing.status, idempotent: true };
  }
  if (!['approved', 'paid', 'failed'].includes(statement.status)) {
    throw new Error('Statement must be approved before payout');
  }
  const beneficiary = await db.prepare(`
    SELECT id FROM referral_beneficiaries WHERE user_id = ? AND status = 'verified'
  `).bind(statement.user_id).first<{ id: string }>();
  if (!beneficiary) throw new Error('Verified beneficiary is required');
  const payoutId = existing?.id ?? crypto.randomUUID();
  const paidAt = input.status === 'paid' ? input.occurredAt : null;
  const failedAt = input.status === 'failed' ? input.occurredAt : null;
  const results = await db.batch([
    existing
      ? db.prepare(`
          UPDATE referral_payouts
          SET status = ?, idempotency_key = ?, beneficiary_id = ?, utr_reference = ?,
              provider_reference = ?, failure_reason = ?, attempts = attempts + 1,
              paid_at = ?, failed_at = ?, updated_at = ?
          WHERE id = ? AND status IN ('failed', 'pending')
        `).bind(
          input.status,
          input.idempotencyKey,
          beneficiary.id,
          input.utrReference?.trim() ?? null,
          input.providerReference?.trim() ?? null,
          input.status === 'failed' ? reason : null,
          paidAt,
          failedAt,
          input.occurredAt,
          payoutId,
        )
      : db.prepare(`
          INSERT INTO referral_payouts
            (id, statement_id, week_id, influencer_slot, user_id, beneficiary_id,
             amount_inr, status, idempotency_key, utr_reference, provider_reference,
             failure_reason, attempts, paid_at, failed_at, created_by, updated_at)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        `).bind(
          payoutId,
          statement.id,
          statement.week_id,
          statement.influencer_slot,
          statement.user_id,
          beneficiary.id,
          statement.gross_amount_inr,
          input.status,
          input.idempotencyKey,
          input.utrReference?.trim() ?? null,
          input.providerReference?.trim() ?? null,
          input.status === 'failed' ? reason : null,
          paidAt,
          failedAt,
          input.actorId,
          input.occurredAt,
        ),
    db.prepare(`
      UPDATE referral_weekly_statements
      SET status = CASE WHEN ? = 'paid' THEN 'paid' WHEN ? = 'failed' THEN 'failed' ELSE status END,
          paid_at = CASE WHEN ? = 'paid' THEN ? ELSE paid_at END,
          failure_reason = CASE WHEN ? = 'failed' THEN ? ELSE failure_reason END,
          updated_at = ?
      WHERE id = ?
    `).bind(
      input.status,
      input.status,
      input.status,
      input.occurredAt,
      input.status,
      input.status === 'failed' ? reason : null,
      input.occurredAt,
      statement.id,
    ),
    db.prepare(`
      INSERT INTO referral_settlement_audits
        (id, week_id, statement_id, payout_id, influencer_slot, action, to_status,
         actor_id, reason, metadata, policy_version, occurred_at)
      VALUES (?, ?, ?, ?, ?, 'payout_recorded', ?, ?, ?, ?, ?, ?)
    `).bind(
      crypto.randomUUID(),
      statement.week_id,
      statement.id,
      payoutId,
      statement.influencer_slot,
      input.status,
      input.actorId,
      reason,
      JSON.stringify({ amount_inr: statement.gross_amount_inr, utr_reference: input.utrReference ?? null }),
      REFERRAL_POLICY_VERSION,
      input.occurredAt,
    ),
  ]);
  if (results[0]?.meta.changes !== 1) throw new Error('Payout write race lost');
  return { payoutId, statementId: statement.id, status: input.status, idempotent: false };
}

export async function transitionSettlementPayout(
  db: D1Database,
  input: {
    payoutId: string;
    actorId: string;
    status: 'corrected' | 'reversed' | 'clawed_back';
    reason: string;
    occurredAt: number;
  },
): Promise<{ payoutId: string; status: string }> {
  const reason = boundedReason(input.reason, 'Payout correction reason');
  const payout = await db.prepare(`
    SELECT id, week_id, statement_id, influencer_slot, status
    FROM referral_payouts WHERE id = ?
  `).bind(input.payoutId).first<{ id: string; week_id: string; statement_id: string; influencer_slot: number; status: string }>();
  if (!payout) throw new Error('Payout not found');
  const result = await db.batch([
    db.prepare(`
      UPDATE referral_payouts
      SET status = ?, reversed_at = CASE WHEN ? IN ('reversed', 'clawed_back') THEN ? ELSE reversed_at END,
          updated_at = ?
      WHERE id = ? AND status NOT IN ('reversed', 'clawed_back')
    `).bind(input.status, input.status, input.occurredAt, input.occurredAt, input.payoutId),
    db.prepare(`
      UPDATE referral_weekly_statements
      SET status = ?, updated_at = ?
      WHERE id = ?
    `).bind(input.status, input.occurredAt, payout.statement_id),
    db.prepare(`
      INSERT INTO referral_settlement_audits
        (id, week_id, statement_id, payout_id, influencer_slot, action, from_status,
         to_status, actor_id, reason, policy_version, occurred_at)
      VALUES (?, ?, ?, ?, ?, 'payout_transition', ?, ?, ?, ?, ?, ?)
    `).bind(
      crypto.randomUUID(),
      payout.week_id,
      payout.statement_id,
      payout.id,
      payout.influencer_slot,
      payout.status,
      input.status,
      input.actorId,
      reason,
      REFERRAL_POLICY_VERSION,
      input.occurredAt,
    ),
  ]);
  if (result[0]?.meta.changes !== 1) throw new Error('Payout transition race lost');
  return { payoutId: payout.id, status: input.status };
}

export function validatePrivateReceipt(
  file: { name: string; type: string; size: number },
): void {
  if (!file.name || file.name.length > 180) throw new Error('Receipt filename is invalid');
  if (!ALLOWED_RECEIPT_TYPES.has(file.type)) throw new Error('Receipt must be a PDF, PNG, or JPEG');
  if (!Number.isInteger(file.size) || file.size <= 0 || file.size > MAX_RECEIPT_BYTES) {
    throw new Error('Receipt must be between 1 byte and 5 MB');
  }
}

export { MAX_RECEIPT_BYTES };

export async function settlementAudits(
  db: D1Database,
  weekId?: string,
): Promise<Array<Record<string, unknown>>> {
  const rows = await db.prepare(`
    SELECT * FROM referral_settlement_audits
    ${weekId ? 'WHERE week_id = ?' : ''}
    ORDER BY occurred_at DESC, created_at DESC
  `).bind(...(weekId ? [weekId] : [])).all<Record<string, unknown>>();
  return rows.results;
}