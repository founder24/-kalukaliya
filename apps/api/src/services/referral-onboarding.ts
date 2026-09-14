import { REFERRAL_POLICY, REFERRAL_POLICY_VERSION, weeklyRewardInr } from '../contracts/referral-policy';
import { admitReferralInfluencer, referralWeekBounds } from './referral-attribution';

const ACTIVATION_WINDOW_SECONDS = 7 * 24 * 60 * 60;
const APPLICATION_STATES = new Set([
  'submitted', 'incomplete', 'under_review', 'waitlisted', 'approved',
  'activation_required', 'active', 'expired', 'suspended', 'rejected',
  'paused', 'closed',
]);

export interface ReferralApplicationInput {
  institution: string;
  className: string;
  streamName: string;
  ageEligible: boolean;
  guardianConsentRequired: boolean;
  guardianConsentConfirmed: boolean;
  eligibilityAcknowledged: boolean;
  conductAcknowledged: boolean;
  privacyConsent: boolean;
  termsVersion: string;
  privacyVersion: string;
  idempotencyKey: string;
}

interface ApplicationRow {
  id: string;
  user_id: string;
  status: string;
  institution: string;
  class_name: string;
  stream_name: string;
  academic_snapshot: string;
  contact_snapshot: string;
  identity_status: string;
  kyc_status: string;
  waitlist_priority_at: number | null;
  activation_deadline_at: number | null;
  influencer_slot: number | null;
  decision_reason: string | null;
  submitted_at: number;
  activated_at: number | null;
  payload_fingerprint: string;
  idempotency_key: string;
}

function text(value: string, name: string, min: number, max: number): string {
  const normalized = value.trim().replace(/\s+/g, ' ');
  if (normalized.length < min || normalized.length > max) {
    throw new Error(`${name} must be between ${min} and ${max} characters`);
  }
  return normalized;
}

async function fingerprint(input: ReferralApplicationInput): Promise<string> {
  const canonical = JSON.stringify({
    institution: input.institution,
    className: input.className,
    streamName: input.streamName,
    ageEligible: input.ageEligible,
    guardianConsentRequired: input.guardianConsentRequired,
    guardianConsentConfirmed: input.guardianConsentConfirmed,
    eligibilityAcknowledged: input.eligibilityAcknowledged,
    conductAcknowledged: input.conductAcknowledged,
    privacyConsent: input.privacyConsent,
    termsVersion: input.termsVersion,
    privacyVersion: input.privacyVersion,
  });
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(canonical));
  return Array.from(new Uint8Array(digest))
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('');
}

function parseJson<T>(raw: string | null, fallback: T): T {
  try { return raw ? JSON.parse(raw) as T : fallback; } catch { return fallback; }
}

async function audit(
  db: D1Database,
  input: {
    applicationId: string;
    userId: string;
    actorId: string;
    action: string;
    fromStatus: string | null;
    toStatus: string;
    reason: string;
    occurredAt: number;
    metadata?: Record<string, unknown>;
  },
): Promise<void> {
  await db.prepare(`
    INSERT INTO referral_application_audits
      (id, application_id, user_id, actor_id, action, from_status, to_status,
       reason, policy_version, occurred_at, metadata)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
  `).bind(
    crypto.randomUUID(),
    input.applicationId,
    input.userId,
    input.actorId,
    input.action,
    input.fromStatus,
    input.toStatus,
    text(input.reason, 'Audit reason', 3, 1_000),
    REFERRAL_POLICY_VERSION,
    input.occurredAt,
    JSON.stringify(input.metadata ?? {}),
  ).run();
}

function guardedAuditStatement(
  db: D1Database,
  input: {
    applicationId: string;
    userId: string;
    actorId: string;
    action: string;
    fromStatus: string | null;
    toStatus: string;
    reason: string;
    occurredAt: number;
    metadata?: Record<string, unknown>;
  },
): D1PreparedStatement {
  return db.prepare(`
    INSERT INTO referral_application_audits
      (id, application_id, user_id, actor_id, action, from_status, to_status,
       reason, policy_version, occurred_at, metadata)
    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    WHERE EXISTS (
      SELECT 1 FROM referral_applications
      WHERE id = ? AND status = ? AND updated_at = ?
    )
  `).bind(
    crypto.randomUUID(),
    input.applicationId,
    input.userId,
    input.actorId,
    input.action,
    input.fromStatus,
    input.toStatus,
    text(input.reason, 'Audit reason', 3, 1_000),
    REFERRAL_POLICY_VERSION,
    input.occurredAt,
    JSON.stringify(input.metadata ?? {}),
    input.applicationId,
    input.toStatus,
    input.occurredAt,
  );
}

export async function submitReferralApplication(
  db: D1Database,
  userId: string,
  raw: ReferralApplicationInput,
  now = Math.floor(Date.now() / 1000),
): Promise<{ application: ApplicationRow; idempotent: boolean }> {
  const input = {
    ...raw,
    institution: text(raw.institution, 'Institution', 2, 160),
    className: text(raw.className, 'Class', 1, 80),
    streamName: text(raw.streamName, 'Stream', 1, 80),
    termsVersion: text(raw.termsVersion, 'Terms version', 3, 64),
    privacyVersion: text(raw.privacyVersion, 'Privacy version', 3, 64),
    idempotencyKey: text(raw.idempotencyKey, 'Idempotency key', 8, 128),
  };
  if (
    !input.ageEligible
    || !input.eligibilityAcknowledged
    || !input.conductAcknowledged
    || !input.privacyConsent
    || (input.guardianConsentRequired && !input.guardianConsentConfirmed)
  ) throw new Error('All required eligibility, conduct, privacy, and guardian acknowledgements are required');
  if (
    input.termsVersion !== REFERRAL_POLICY_VERSION
    || input.privacyVersion !== REFERRAL_POLICY_VERSION
  ) throw new Error('The current campaign terms and privacy notice must be accepted');

  const user = await db.prepare(`
    SELECT email, phone, board_name, class_name, stream_name
    FROM users WHERE id = ? AND deleted_at IS NULL
  `).bind(userId).first<Record<string, string | null>>();
  if (!user) throw new Error('User not found');
  if (!user.email) throw new Error('A verified account email is required');

  const normalized: ReferralApplicationInput = input;
  const payloadFingerprint = await fingerprint(normalized);
  const existing = await db.prepare(`
    SELECT * FROM referral_applications WHERE user_id = ?
  `).bind(userId).first<ApplicationRow>();
  if (
    existing
    && existing.idempotency_key === input.idempotencyKey
    && existing.payload_fingerprint === payloadFingerprint
  ) return { application: existing, idempotent: true };
  if (existing && !['incomplete', 'rejected', 'expired'].includes(existing.status)) {
    return { application: existing, idempotent: true };
  }

  const academicSnapshot = JSON.stringify({
    institution: input.institution,
    class_name: input.className,
    stream_name: input.streamName,
    account_board_name: user.board_name,
    account_class_name: user.class_name,
    account_stream_name: user.stream_name,
    captured_at: now,
  });
  const contactSnapshot = JSON.stringify({
    email: user.email,
    phone: user.phone,
    captured_at: now,
  });
  const applicationId = existing?.id ?? crypto.randomUUID();
  const action = existing ? 'resubmitted' : 'submitted';
  await db.prepare(`
    INSERT INTO referral_applications
      (id, user_id, status, institution, class_name, stream_name,
       academic_snapshot, contact_snapshot, identity_status, kyc_status,
       age_eligible, guardian_consent_required, guardian_consent_confirmed,
       eligibility_acknowledged, conduct_acknowledged, privacy_consent,
       terms_version, privacy_version, idempotency_key, payload_fingerprint,
       submitted_at, updated_at)
    VALUES (?, ?, 'submitted', ?, ?, ?, ?, ?, 'pending', 'not_requested',
            1, ?, ?, 1, 1, 1, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(user_id) DO UPDATE SET
      status = 'submitted', institution = excluded.institution,
      class_name = excluded.class_name, stream_name = excluded.stream_name,
      academic_snapshot = excluded.academic_snapshot,
      contact_snapshot = excluded.contact_snapshot,
      identity_status = 'pending', kyc_status = 'not_requested',
      guardian_consent_required = excluded.guardian_consent_required,
      guardian_consent_confirmed = excluded.guardian_consent_confirmed,
      terms_version = excluded.terms_version, privacy_version = excluded.privacy_version,
      idempotency_key = excluded.idempotency_key,
      payload_fingerprint = excluded.payload_fingerprint,
      submitted_at = excluded.submitted_at, decision_reason = NULL,
      waitlist_priority_at = NULL, activation_deadline_at = NULL,
      influencer_slot = NULL,
      reviewed_by = NULL, reviewed_at = NULL, updated_at = excluded.updated_at
  `).bind(
    applicationId,
    userId,
    input.institution,
    input.className,
    input.streamName,
    academicSnapshot,
    contactSnapshot,
    input.guardianConsentRequired ? 1 : 0,
    input.guardianConsentConfirmed ? 1 : 0,
    input.termsVersion,
    input.privacyVersion,
    input.idempotencyKey,
    payloadFingerprint,
    now,
    now,
  ).run();
  await audit(db, {
    applicationId,
    userId,
    actorId: userId,
    action,
    fromStatus: existing?.status ?? null,
    toStatus: 'submitted',
    reason: existing ? 'Applicant resubmitted current evidence' : 'Applicant submitted current evidence',
    occurredAt: now,
  });
  const application = await db.prepare(
    'SELECT * FROM referral_applications WHERE id = ?',
  ).bind(applicationId).first<ApplicationRow>();
  if (!application) throw new Error('Application write failed');
  return { application, idempotent: false };
}

function applicationView(row: ApplicationRow | null, includePrivate = false) {
  if (!row) return null;
  return {
    id: row.id,
    status: row.status,
    review_decision: ['activation_required', 'active'].includes(row.status) ? 'approved' : null,
    institution: row.institution,
    class_name: row.class_name,
    stream_name: row.stream_name,
    identity_status: row.identity_status,
    kyc_status: row.kyc_status,
    waitlist_priority_at: row.waitlist_priority_at,
    activation_deadline_at: row.activation_deadline_at,
    decision_reason: row.decision_reason,
    submitted_at: row.submitted_at,
    activated_at: row.activated_at,
    ...(includePrivate ? {
      user_id: row.user_id,
      academic_snapshot: parseJson(row.academic_snapshot, {}),
      contact_snapshot: parseJson(row.contact_snapshot, {}),
      influencer_slot: row.influencer_slot,
    } : {}),
  };
}

export async function referralExperience(db: D1Database, userId: string) {
  const now = Math.floor(Date.now() / 1000);
  const application = await db.prepare(
    'SELECT * FROM referral_applications WHERE user_id = ?',
  ).bind(userId).first<ApplicationRow>();
  const program = await db.prepare(`
    SELECT state, pause_effective_at, resumed_at FROM referral_program_state WHERE id = 'singleton'
  `).first<{ state: string; pause_effective_at: number | null; resumed_at: number | null }>();
  const week = referralWeekBounds(now);
  let dashboard = null;
  if (application?.influencer_slot) {
    const row = await db.prepare(`
      SELECT i.status AS slot_status, i.tier, i.referral_code,
             COALESCE(p.mature_verified_count, 0) AS mature_verified,
             COALESCE(p.reward_eligible_count, 0) AS reward_eligible,
             COALESCE(SUM(CASE WHEN c.state = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
             COALESCE(SUM(CASE WHEN c.state = 'rejected' THEN 1 ELSE 0 END), 0) AS rejected_count,
             a.status AS advanced_status, a.position_no, a.activates_at
      FROM referral_influencer_slots i
      LEFT JOIN referral_weekly_progress p
        ON p.influencer_slot = i.slot_no AND p.week_id = ?
      LEFT JOIN referral_weekly_claims c
        ON c.credited_influencer_slot = i.slot_no AND c.week_id = ?
      LEFT JOIN referral_advanced_positions a ON a.influencer_slot = i.slot_no
      WHERE i.slot_no = ?
      GROUP BY i.slot_no
    `).bind(week.id, week.id, application.influencer_slot)
      .first<Record<string, number | string | null>>();
    const remaining = await db.prepare(`
      SELECT COUNT(*) AS count FROM referral_advanced_positions WHERE status IN ('available', 'released')
    `).first<{ count: number }>();
    if (row) {
      const tier = row.tier === 'advanced' ? 'advanced' : 'basic';
      const mature = Number(row.mature_verified ?? 0);
      const activated = application.status === 'active' && row.slot_status === 'active';
      dashboard = {
        tier,
        slot_status: row.slot_status,
        current_week: {
          id: week.id,
          starts_at: week.startsAt,
          ends_at: week.endsAt,
          mature_verified: mature,
          pending: Number(row.pending_count ?? 0),
          rejected: Number(row.rejected_count ?? 0),
          authoritative_reward_inr: weeklyRewardInr(tier, mature),
          reward_cap_inr: tier === 'advanced'
            ? REFERRAL_POLICY.advanced.weeklyRewardCapInr
            : REFERRAL_POLICY.basic.weeklyRewardCapInr,
        },
        qualification: {
          target: REFERRAL_POLICY.advanced.qualificationVisitorsInOneWeek,
          mature_verified: mature,
          status: row.advanced_status ?? 'not-qualified',
          position_no: row.position_no,
          activates_at: row.activates_at,
          remaining_advanced_positions: remaining?.count ?? 0,
        },
        referral: activated ? {
          code: row.referral_code,
          link: `https://syrabit.ai/r/${row.referral_code}`,
        } : null,
      };
    }
  }
  return {
    policy: {
      version: REFERRAL_POLICY_VERSION,
      approved_slots: REFERRAL_POLICY.approvedInfluencerSlots,
      advanced_slots: REFERRAL_POLICY.advancedInfluencerSlots,
      basic_weekly_cap_inr: REFERRAL_POLICY.basic.weeklyRewardCapInr,
      advanced_weekly_cap_inr: REFERRAL_POLICY.advanced.weeklyRewardCapInr,
      advanced_target: REFERRAL_POLICY.advanced.qualificationVisitorsInOneWeek,
      delayed_settlement: true,
      public_identity: false,
    },
    program: {
      state: program?.state ?? 'paused',
      pause_effective_at: program?.pause_effective_at ?? null,
      resumed_at: program?.resumed_at ?? null,
    },
    application: applicationView(application ?? null),
    dashboard,
  };
}

export async function listReferralApplications(db: D1Database, status?: string) {
  const filter = status && APPLICATION_STATES.has(status) ? status : null;
  const result = filter
    ? await db.prepare(`
        SELECT * FROM referral_applications WHERE status = ?
        ORDER BY COALESCE(waitlist_priority_at, submitted_at), submitted_at, id LIMIT 200
      `).bind(filter).all<ApplicationRow>()
    : await db.prepare(`
        SELECT * FROM referral_applications
        ORDER BY CASE status WHEN 'submitted' THEN 0 WHEN 'under_review' THEN 1
          WHEN 'waitlisted' THEN 2 ELSE 3 END,
          COALESCE(waitlist_priority_at, submitted_at), submitted_at, id LIMIT 200
      `).all<ApplicationRow>();
  return result.results.map(row => applicationView(row, true));
}

export async function reviewReferralApplication(
  db: D1Database,
  applicationId: string,
  input: {
    decision: 'review' | 'approve' | 'waitlist' | 'reject' | 'suspend' | 'close';
    actorId: string;
    reason: string;
    identityVerified?: boolean;
    kycVerified?: boolean;
    now?: number;
  },
) {
  const now = input.now ?? Math.floor(Date.now() / 1000);
  const reason = text(input.reason, 'Decision reason', 8, 1_000);
  const row = await db.prepare(
    'SELECT * FROM referral_applications WHERE id = ?',
  ).bind(applicationId).first<ApplicationRow>();
  if (!row) return null;
  const allowedFrom: Record<typeof input.decision, string[]> = {
    review: ['submitted', 'incomplete'],
    approve: ['submitted', 'under_review', 'waitlisted'],
    waitlist: ['submitted', 'under_review'],
    reject: ['submitted', 'incomplete', 'under_review', 'waitlisted'],
    suspend: ['active'],
    close: ['submitted', 'incomplete', 'under_review', 'waitlisted', 'activation_required', 'active', 'expired', 'suspended', 'rejected', 'paused'],
  };
  if (!allowedFrom[input.decision].includes(row.status)) {
    throw new Error(`Cannot ${input.decision} an application with status ${row.status}`);
  }
  if (input.decision === 'suspend') {
    if (!row.influencer_slot) throw new Error('Active application has no assigned slot');
    const auditInput = {
      applicationId: row.id,
      userId: row.user_id,
      actorId: input.actorId,
      action: 'suspended',
      fromStatus: row.status,
      toStatus: 'suspended',
      reason,
      occurredAt: now,
      metadata: { influencer_slot: row.influencer_slot },
    };
    const results = await db.batch([
      db.prepare(`
        UPDATE referral_applications
        SET status = 'suspended', decision_reason = ?, reviewed_by = ?,
            reviewed_at = ?, suspended_at = ?, updated_at = ?
        WHERE id = ? AND status = 'active'
      `).bind(reason, input.actorId, now, now, now, row.id),
      db.prepare(`
        UPDATE referral_influencer_slots
        SET status = 'suspended', suspended_at = ?, updated_at = ?
        WHERE slot_no = ? AND user_id = ? AND status = 'active'
          AND EXISTS (
            SELECT 1 FROM referral_applications
            WHERE id = ? AND status = 'suspended'
          )
      `).bind(now, now, row.influencer_slot, row.user_id, row.id),
      guardedAuditStatement(db, auditInput),
    ]);
    if (results.some(result => result.meta.changes !== 1)) {
      throw new Error('Suspension conflict');
    }
    return { status: 'suspended', activation_deadline_at: null, influencer_slot: row.influencer_slot };
  }
  if (input.decision === 'close' && row.influencer_slot) {
    const auditInput = {
      applicationId: row.id,
      userId: row.user_id,
      actorId: input.actorId,
      action: 'closed',
      fromStatus: row.status,
      toStatus: 'closed',
      reason,
      occurredAt: now,
      metadata: { released_influencer_slot: row.influencer_slot },
    };
    const results = await db.batch([
      db.prepare(`
        UPDATE referral_applications
        SET status = 'closed', influencer_slot = NULL, activation_deadline_at = NULL,
            decision_reason = ?, reviewed_by = ?, reviewed_at = ?,
            closed_at = ?, updated_at = ?
        WHERE id = ? AND status = ?
      `).bind(reason, input.actorId, now, now, now, row.id, row.status),
      db.prepare(`
        UPDATE referral_advanced_positions SET
          influencer_slot = NULL, status = 'released',
          qualified_week_id = NULL, qualified_at = NULL,
          reviewed_by = NULL, reviewed_at = NULL, review_reason = NULL,
          activates_at = NULL, updated_at = ?
        WHERE influencer_slot = ? AND status IN ('provisional', 'active')
      `).bind(now, row.influencer_slot),
      db.prepare(`
        UPDATE referral_influencer_slots SET
          user_id = NULL, referral_code = NULL, status = 'available', tier = 'basic',
          identity_verified = 0, kyc_verified = 0, academic_snapshot = NULL,
          eligibility_reviewed_at = NULL, admitted_at = NULL, inactive_at = ?,
          suspended_at = NULL, removed_at = NULL, advanced_effective_at = NULL,
          policy_version = ?, updated_at = ?
        WHERE slot_no = ? AND user_id = ? AND status IN ('active', 'inactive', 'suspended')
          AND EXISTS (
            SELECT 1 FROM referral_applications
            WHERE id = ? AND status = 'closed' AND influencer_slot IS NULL
          )
      `).bind(now, REFERRAL_POLICY_VERSION, now, row.influencer_slot, row.user_id, row.id),
      guardedAuditStatement(db, auditInput),
    ]);
    if (
      results[0]?.meta.changes !== 1
      || results[2]?.meta.changes !== 1
      || results[3]?.meta.changes !== 1
    ) {
      throw new Error('Closure conflict');
    }
    return { status: 'closed', activation_deadline_at: null, influencer_slot: null };
  }
  let status = row.status;
  let action: string = input.decision;
  let slot = row.influencer_slot;
  if (input.decision === 'review') {
    status = 'under_review';
    action = 'under_review';
  }
  if (input.decision === 'waitlist') {
    status = 'waitlisted';
    action = 'waitlisted';
  }
  if (input.decision === 'reject') {
    status = 'rejected';
    action = 'rejected';
  }
  if (input.decision === 'close') {
    status = 'closed';
    action = 'closed';
  }
  if (input.decision === 'approve') {
    if (!input.identityVerified || !input.kycVerified) {
      throw new Error('Verified identity and KYC evidence are required for approval');
    }
    const admission = await admitReferralInfluencer(db, {
      userId: row.user_id,
      academicSnapshot: parseJson(row.academic_snapshot, {}),
      identityVerified: true,
      kycVerified: true,
      reviewedBy: input.actorId,
      reviewReason: reason,
      reviewedAt: now,
    });
    if (!admission.admitted) {
      status = 'waitlisted';
      action = 'waitlisted';
    } else {
      status = 'activation_required';
      action = 'approved';
      slot = admission.slotNo;
      const reserved = await db.prepare(`
        UPDATE referral_influencer_slots
        SET status = 'inactive', inactive_at = ?, updated_at = ?
        WHERE slot_no = ? AND user_id = ? AND status = 'active'
      `).bind(now, now, admission.slotNo, row.user_id).run();
      if (reserved.meta.changes !== 1) {
        throw new Error('Referral place reservation conflict');
      }
    }
  }
  const deadline = status === 'activation_required' ? now + ACTIVATION_WINDOW_SECONDS : null;
  const auditInput = {
    applicationId: row.id,
    userId: row.user_id,
    actorId: input.actorId,
    action,
    fromStatus: row.status,
    toStatus: status,
    reason,
    occurredAt: now,
    metadata: slot ? { influencer_slot: slot, activation_deadline_at: deadline } : {},
  };
  const results = await db.batch([
    db.prepare(`
    UPDATE referral_applications SET
      status = ?, identity_status = CASE WHEN ? THEN 'verified' ELSE identity_status END,
      kyc_status = CASE WHEN ? THEN 'verified' ELSE kyc_status END,
      waitlist_priority_at = CASE WHEN ? = 'waitlisted' THEN COALESCE(waitlist_priority_at, ?) ELSE waitlist_priority_at END,
      activation_deadline_at = ?, influencer_slot = ?, decision_reason = ?,
      reviewed_by = ?, reviewed_at = ?, suspended_at = CASE WHEN ? = 'suspended' THEN ? ELSE suspended_at END,
      closed_at = CASE WHEN ? = 'closed' THEN ? ELSE closed_at END, updated_at = ?
    WHERE id = ? AND status = ?
    `).bind(
    status,
    input.identityVerified ? 1 : 0,
    input.kycVerified ? 1 : 0,
    status,
    now,
    deadline,
    slot,
    reason,
    input.actorId,
    now,
    status,
    now,
    status,
    now,
    now,
    row.id,
    row.status,
    ),
    guardedAuditStatement(db, auditInput),
  ]);
  if (results[0]?.meta.changes !== 1 || results[1]?.meta.changes !== 1) {
    if (input.decision === 'approve' && slot) {
      await db.batch([
        db.prepare(`
          UPDATE referral_influencer_slots SET
            user_id = NULL, referral_code = NULL, status = 'available', tier = 'basic',
            identity_verified = 0, kyc_verified = 0, academic_snapshot = NULL,
            eligibility_reviewed_at = NULL, admitted_at = NULL, inactive_at = ?,
            policy_version = ?, updated_at = ?
          WHERE slot_no = ? AND user_id = ? AND status = 'inactive'
        `).bind(now, REFERRAL_POLICY_VERSION, now, slot, row.user_id),
        db.prepare(`
          DELETE FROM referral_admission_audits
          WHERE influencer_slot = ? AND user_id = ? AND admitted_at = ?
        `).bind(slot, row.user_id, now),
      ]);
    }
    throw new Error('Application decision conflict');
  }
  return { status, activation_deadline_at: deadline, influencer_slot: slot };
}

export async function activateReferralApplication(
  db: D1Database,
  userId: string,
  now = Math.floor(Date.now() / 1000),
) {
  const row = await db.prepare(`
    SELECT * FROM referral_applications WHERE user_id = ?
  `).bind(userId).first<ApplicationRow>();
  if (!row || row.status !== 'activation_required' || !row.influencer_slot) return null;
  if (!row.activation_deadline_at || row.activation_deadline_at <= now) {
    await expireReferralApplication(db, row.id, userId, 'Activation deadline expired', now, true);
    return { status: 'expired' };
  }
  const auditInput = {
    applicationId: row.id,
    userId,
    actorId: userId,
    action: 'activated',
    fromStatus: 'activation_required',
    toStatus: 'active',
    reason: 'Applicant accepted activation',
    occurredAt: now,
  };
  const results = await db.batch([
    db.prepare(`
      UPDATE referral_influencer_slots SET status = 'active', inactive_at = NULL, updated_at = ?
      WHERE slot_no = ? AND user_id = ? AND status = 'inactive'
        AND EXISTS (
          SELECT 1 FROM referral_applications
          WHERE id = ? AND status = 'activation_required'
            AND activation_deadline_at > ?
        )
    `).bind(now, row.influencer_slot, userId, row.id, now),
    db.prepare(`
      UPDATE referral_applications SET status = 'active', activated_at = ?, updated_at = ?
      WHERE id = ? AND status = 'activation_required'
        AND activation_deadline_at > ?
        AND EXISTS (
          SELECT 1 FROM referral_influencer_slots
          WHERE slot_no = ? AND user_id = ? AND status = 'active'
        )
    `).bind(now, now, row.id, now, row.influencer_slot, userId),
    guardedAuditStatement(db, auditInput),
  ]);
  if (results.some(result => result.meta.changes !== 1)) throw new Error('Activation conflict');
  const slot = await db.prepare(`
    SELECT referral_code FROM referral_influencer_slots WHERE slot_no = ?
  `).bind(row.influencer_slot).first<{ referral_code: string }>();
  return {
    status: 'active',
    referral_code: slot?.referral_code,
    referral_link: slot?.referral_code ? `https://syrabit.ai/r/${slot.referral_code}` : null,
  };
}

export async function expireReferralApplication(
  db: D1Database,
  applicationId: string,
  actorId: string,
  reason: string,
  now = Math.floor(Date.now() / 1000),
  promote = true,
) {
  const row = await db.prepare(
    'SELECT * FROM referral_applications WHERE id = ?',
  ).bind(applicationId).first<ApplicationRow>();
  if (!row || !row.influencer_slot || !['activation_required', 'approved'].includes(row.status)) return null;
  const auditInput = {
    applicationId: row.id,
    userId: row.user_id,
    actorId,
    action: 'expired',
    fromStatus: row.status,
    toStatus: 'expired',
    reason,
    occurredAt: now,
  };
  const results = await db.batch([
    db.prepare(`
      UPDATE referral_applications SET status = 'expired', influencer_slot = NULL,
        activation_deadline_at = NULL, decision_reason = ?, updated_at = ?
      WHERE id = ? AND status IN ('activation_required', 'approved')
        AND influencer_slot = ?
        AND EXISTS (
          SELECT 1 FROM referral_influencer_slots
          WHERE slot_no = ? AND user_id = ? AND status = 'inactive'
        )
    `).bind(reason, now, row.id, row.influencer_slot, row.influencer_slot, row.user_id),
    db.prepare(`
      UPDATE referral_influencer_slots SET
        user_id = NULL, referral_code = NULL, status = 'available', tier = 'basic',
        identity_verified = 0, kyc_verified = 0, academic_snapshot = NULL,
        eligibility_reviewed_at = NULL, admitted_at = NULL, inactive_at = ?,
        policy_version = ?, updated_at = ?
      WHERE slot_no = ? AND user_id = ? AND status = 'inactive'
        AND EXISTS (
          SELECT 1 FROM referral_applications
          WHERE id = ? AND status = 'expired' AND influencer_slot IS NULL
        )
    `).bind(now, REFERRAL_POLICY_VERSION, now, row.influencer_slot, row.user_id, row.id),
    guardedAuditStatement(db, auditInput),
  ]);
  if (results.some(result => result.meta.changes !== 1)) {
    throw new Error('Activation expiry conflict');
  }
  if (!promote) return { status: 'expired', promoted: null };
  const next = await db.prepare(`
    SELECT * FROM referral_applications WHERE status = 'waitlisted'
      AND identity_status = 'verified' AND kyc_status = 'verified'
    ORDER BY waitlist_priority_at, submitted_at, id LIMIT 1
  `).first<ApplicationRow>();
  if (!next) return { status: 'expired', promoted: null };
  const promoted = await reviewReferralApplication(db, next.id, {
    decision: 'approve',
    actorId,
    reason: `Waitlist promotion after expired activation: ${reason}`.slice(0, 1_000),
    identityVerified: true,
    kycVerified: true,
    now,
  });
  if (promoted) {
    await audit(db, {
      applicationId: next.id,
      userId: next.user_id,
      actorId,
      action: 'waitlist_promoted',
      fromStatus: 'waitlisted',
      toStatus: promoted.status,
      reason: 'Promoted by oldest verified waitlist priority after a slot expired',
      occurredAt: now,
    });
  }
  return { status: 'expired', promoted: promoted ? next.id : null };
}

export async function referralApplicationAudits(db: D1Database, applicationId: string) {
  const result = await db.prepare(`
    SELECT action, from_status, to_status, reason, actor_id, occurred_at, metadata
    FROM referral_application_audits WHERE application_id = ?
    ORDER BY occurred_at DESC, id DESC LIMIT 200
  `).bind(applicationId).all();
  return result.results;
}