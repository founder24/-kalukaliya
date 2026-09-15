import { REFERRAL_POLICY } from '../contracts/referral-policy';

const APPROVED_NETWORK = REFERRAL_POLICY.funding.approvedNetwork;
const MAX_SOURCE_REFERENCE = 256;
const MAX_EVIDENCE_HASH = /^[a-f0-9]{64}$/i;
const MAX_RECONCILIATION_WEEKS = 12;
const ADSENSE_SOURCE_REFERENCE = /^adsense:[A-Za-z0-9_-]{8,128}$/;

type InventoryStatus = 'enabled' | 'disabled' | 'unconfigured';
type RoiDataQuality = 'healthy' | 'warning' | 'blocked';

interface InventoryRow {
  network: string;
  status: InventoryStatus;
  configured: number;
  placements_json: string;
  policy_notes: string;
  updated_by: string | null;
  updated_at: number;
}

interface RevenueRow {
  id: string;
  network: string;
  period_start: number;
  period_end: number;
  settlement_period: string;
  currency: string;
  gross_revenue_paise: number;
  adjustments_paise: number;
  provider_fees_paise: number;
  net_revenue_paise: number;
  monetized_impressions: number;
  finalized: number;
  finalized_through_at: number;
  fetched_at: number;
  freshness_expires_at: number;
  source_reference: string;
  evidence_hash: string;
  imported_by: string;
  created_at: number;
}

interface ControlRow {
  id: string;
  reserve_healthy: number;
  revenue_fresh: number;
  invalid_traffic_healthy: number;
  ad_account_healthy: number;
  contribution_margin_healthy: number;
  identity_resets_healthy: number;
  fraud_healthy: number;
  exposure_healthy: number;
  evidence_id: string;
  warnings_json: string;
  updated_by: string;
  updated_at: number;
  expires_at: number;
}

interface WeekRow {
  id: string;
  starts_at: number;
  ends_at: number;
}

interface ReconciliationWeekRow extends WeekRow {
  week_key: string;
}

interface ReconciliationStatusRow {
  id: string;
  status: 'running' | 'completed' | 'skipped' | 'failed';
  started_at: number;
  completed_at: number | null;
  weeks: number;
  fetched: number;
  imported: number;
  idempotent: number;
  calculated: number;
  failures_json: string;
}

export interface AdSenseReconciliationConfig {
  ADSENSE_REPORT_URL?: string;
  ADSENSE_REPORT_TOKEN?: string;
}

export interface AdSenseReconciliationResult {
  status: 'completed' | 'skipped' | 'failed';
  weeks: number;
  fetched: number;
  imported: number;
  idempotent: number;
  calculated: number;
  failures: string[];
}

interface RoiCosts {
  reviewCostInr?: number;
  fraudCostInr?: number;
  reversalCostInr?: number;
  supportCostInr?: number;
  operatingCostInr?: number;
}

function nonNegativeInteger(value: number | undefined, label: string): number {
  if (value === undefined) return 0;
  if (!Number.isSafeInteger(value) || value < 0) throw new Error(`${label} must be a non-negative integer`);
  return value;
}

function parseWarnings(value: string | null | undefined): string[] {
  try {
    const parsed = JSON.parse(value ?? '[]');
    return Array.isArray(parsed)
      ? parsed.filter(item => typeof item === 'string').slice(0, 32)
      : [];
  } catch {
    return [];
  }
}

function safeFailureMessage(error: unknown): string {
  const message = error instanceof Error
    ? error.message
    : typeof error === 'string'
      ? error
      : 'unknown provider error';
  return message
    .replace(/Bearer\s+\S+/gi, 'Bearer [redacted]')
    .replace(/https?:\/\/\S+/gi, '[provider]')
    .slice(0, 512);
}

function parseFailureSummaries(value: string | null | undefined): string[] {
  try {
    const parsed = JSON.parse(value ?? '[]');
    return Array.isArray(parsed)
      ? parsed
        .filter(item => typeof item === 'string')
        .map(item => safeFailureMessage(item))
        .slice(0, 16)
      : [];
  } catch {
    return [];
  }
}

async function markReconciliationStarted(db: D1Database, startedAt: number): Promise<void> {
  await db.prepare(`
    INSERT INTO adsense_reconciliation_status
      (id, status, started_at, completed_at, weeks, fetched, imported, idempotent, calculated, failures_json)
    VALUES ('singleton', 'running', ?, NULL, 0, 0, 0, 0, 0, '[]')
    ON CONFLICT(id) DO UPDATE SET
      status = excluded.status,
      started_at = excluded.started_at,
      completed_at = excluded.completed_at,
      weeks = excluded.weeks,
      fetched = excluded.fetched,
      imported = excluded.imported,
      idempotent = excluded.idempotent,
      calculated = excluded.calculated,
      failures_json = excluded.failures_json
  `).bind(startedAt).run();
}

async function markReconciliationFinished(
  db: D1Database,
  completedAt: number,
  result: AdSenseReconciliationResult,
): Promise<void> {
  await db.prepare(`
    UPDATE adsense_reconciliation_status
    SET status = ?, completed_at = ?, weeks = ?, fetched = ?, imported = ?,
        idempotent = ?, calculated = ?, failures_json = ?
    WHERE id = 'singleton'
  `).bind(
    result.status,
    completedAt,
    result.weeks,
    result.fetched,
    result.imported,
    result.idempotent,
    result.calculated,
    JSON.stringify(result.failures.map(item => safeFailureMessage(item)).slice(0, 16)),
  ).run();
}

function boundedText(value: string, label: string): string {
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > MAX_SOURCE_REFERENCE) {
    throw new Error(`${label} is required and must be at most ${MAX_SOURCE_REFERENCE} characters`);
  }
  return trimmed;
}

function providerRecord(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function providerString(
  record: Record<string, unknown>,
  key: string,
  label = key,
): string {
  if (typeof record[key] !== 'string') throw new Error(`${label} is required`);
  return boundedText(record[key] as string, label);
}

function providerInteger(
  record: Record<string, unknown>,
  key: string,
  label = key,
): number {
  const value = record[key];
  if (!Number.isSafeInteger(value) || (value as number) < 0) {
    throw new Error(`${label} must be a non-negative integer`);
  }
  return value as number;
}

function providerBoolean(
  record: Record<string, unknown>,
  key: string,
  label = key,
): boolean {
  if (typeof record[key] !== 'boolean') throw new Error(`${label} is required`);
  return record[key] as boolean;
}

interface ProviderAggregate {
  settlementPeriod: string;
  currency: string;
  grossRevenuePaise: number;
  adjustmentsPaise: number;
  providerFeesPaise: number;
  monetizedImpressions: number;
  finalizedThroughAt: number;
  freshnessExpiresAt: number;
  sourceReference: string;
  evidenceHash: string;
}

function parseProviderAggregate(
  payload: unknown,
  week: ReconciliationWeekRow,
  fetchedAt: number,
): ProviderAggregate {
  const envelope = providerRecord(payload, 'AdSense response');
  if (!Array.isArray(envelope.reports) || envelope.reports.length !== 1) {
    throw new Error('AdSense response must contain exactly one aggregate report');
  }
  const report = providerRecord(envelope.reports[0], 'AdSense aggregate report');
  const network = report.network === undefined ? APPROVED_NETWORK : report.network;
  if (network !== APPROVED_NETWORK) throw new Error('AdSense response network is not approved');
  if (providerInteger(report, 'period_start', 'Period start') !== week.starts_at
    || providerInteger(report, 'period_end', 'Period end') !== week.ends_at) {
    throw new Error('AdSense report period does not match the referral week');
  }
  if (!providerBoolean(report, 'finalized', 'Finalization')) {
    throw new Error('AdSense report is not finalized');
  }
  const currency = providerString(report, 'currency', 'Currency').toUpperCase();
  if (currency !== 'INR') throw new Error('AdSense report currency must be INR');
  const sourceReference = providerString(report, 'source_reference', 'Source reference');
  if (!ADSENSE_SOURCE_REFERENCE.test(sourceReference)) {
    throw new Error('AdSense source reference is invalid');
  }
  const evidenceHash = providerString(report, 'evidence_hash', 'Evidence hash').toLowerCase();
  if (!MAX_EVIDENCE_HASH.test(evidenceHash)) throw new Error('AdSense evidence hash is invalid');
  const finalizedThroughAt = providerInteger(
    report,
    'finalized_through_at',
    'Finalized-through timestamp',
  );
  if (finalizedThroughAt > fetchedAt || finalizedThroughAt < week.ends_at) {
    throw new Error('AdSense report does not finalize the full referral week');
  }
  const freshnessExpiresAt = providerInteger(
    report,
    'freshness_expires_at',
    'Freshness expiry',
  );
  if (
    freshnessExpiresAt <= fetchedAt
    || freshnessExpiresAt - fetchedAt > REFERRAL_POLICY.funding.revenueEvidenceMaxAgeSeconds
  ) {
    throw new Error('AdSense freshness window is invalid');
  }
  return {
    settlementPeriod: providerString(report, 'settlement_period', 'Settlement period'),
    currency,
    grossRevenuePaise: providerInteger(report, 'gross_revenue_paise', 'Gross revenue'),
    adjustmentsPaise: providerInteger(report, 'adjustments_paise', 'Adjustments'),
    providerFeesPaise: providerInteger(report, 'provider_fees_paise', 'Provider fees'),
    monetizedImpressions: providerInteger(
      report,
      'monetized_impressions',
      'Monetized impressions',
    ),
    finalizedThroughAt,
    freshnessExpiresAt,
    sourceReference,
    evidenceHash,
  };
}

async function fetchAdSenseAggregate(
  baseUrl: string,
  token: string | undefined,
  week: ReconciliationWeekRow,
  fetchedAt: number,
): Promise<ProviderAggregate> {
  const url = new URL(baseUrl);
  url.searchParams.set('period_start', String(week.starts_at));
  url.searchParams.set('period_end', String(week.ends_at));
  url.searchParams.set('settlement_period', week.week_key);
  const headers = new Headers({ Accept: 'application/json' });
  if (token?.trim()) headers.set('Authorization', `Bearer ${token.trim()}`);
  const response = await fetch(url, {
    method: 'GET',
    headers,
    redirect: 'error',
  });
  if (!response.ok) {
    throw new Error(`AdSense provider returned HTTP ${response.status}`);
  }
  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error('AdSense provider returned invalid JSON');
  }
  return parseProviderAggregate(payload, week, fetchedAt);
}

export async function listAdNetworkInventory(db: D1Database): Promise<Array<Record<string, unknown>>> {
  const rows = await db.prepare(`
    SELECT network, status, configured, placements_json, policy_notes, updated_by, updated_at
    FROM ad_network_inventory
    ORDER BY CASE network WHEN 'adsense' THEN 0 ELSE 1 END, network
  `).all<InventoryRow>();
  return rows.results.map(row => ({
    network: row.network,
    status: row.status,
    configured: row.configured === 1,
    placements: (() => {
      try {
        const parsed = JSON.parse(row.placements_json);
        return Array.isArray(parsed) ? parsed.filter(item => typeof item === 'string') : [];
      } catch {
        return [];
      }
    })(),
    policy_notes: row.policy_notes,
    updated_by: row.updated_by,
    updated_at: row.updated_at,
    contributes_to_revenue: row.network === APPROVED_NETWORK
      && row.status === 'enabled'
      && row.configured === 1,
  }));
}

export async function ingestAdRevenueReport(
  db: D1Database,
  input: {
    network: string;
    periodStart: number;
    periodEnd: number;
    settlementPeriod: string;
    currency?: string;
    grossRevenuePaise: number;
    adjustmentsPaise?: number;
    providerFeesPaise?: number;
    monetizedImpressions: number;
    finalized: boolean;
    finalizedThroughAt: number;
    fetchedAt: number;
    freshnessExpiresAt: number;
    sourceReference: string;
    evidenceHash: string;
    importedBy: string;
  },
): Promise<{ id: string; idempotent: boolean; netRevenuePaise: number }> {
  const sourceReference = boundedText(input.sourceReference, 'Provider source reference');
  const settlementPeriod = boundedText(input.settlementPeriod, 'Settlement period');
  if (input.network !== APPROVED_NETWORK) {
    throw new Error(`Only ${APPROVED_NETWORK} provider reports can fund referral rewards`);
  }
  if (
    !Number.isSafeInteger(input.periodStart)
    || !Number.isSafeInteger(input.periodEnd)
    || input.periodEnd <= input.periodStart
    || !Number.isSafeInteger(input.grossRevenuePaise)
    || input.grossRevenuePaise < 0
    || !Number.isSafeInteger(input.monetizedImpressions)
    || input.monetizedImpressions < 0
    || !Number.isSafeInteger(input.finalizedThroughAt)
    || !Number.isSafeInteger(input.fetchedAt)
    || !Number.isSafeInteger(input.freshnessExpiresAt)
    || input.finalizedThroughAt > input.fetchedAt
    || input.freshnessExpiresAt <= input.fetchedAt
    || input.freshnessExpiresAt - input.fetchedAt > REFERRAL_POLICY.funding.revenueEvidenceMaxAgeSeconds
    || !MAX_EVIDENCE_HASH.test(input.evidenceHash)
    || !input.finalized
  ) {
    throw new Error('Finalized, bounded provider revenue evidence is required');
  }
  const adjustmentsPaise = nonNegativeInteger(input.adjustmentsPaise, 'Adjustments');
  const providerFeesPaise = nonNegativeInteger(input.providerFeesPaise, 'Provider fees');
  const netRevenuePaise = input.grossRevenuePaise - adjustmentsPaise - providerFeesPaise;
  const inventory = await db.prepare(`
    SELECT status, configured FROM ad_network_inventory WHERE network = ?
  `).bind(input.network).first<{ status: InventoryStatus; configured: number }>();
  if (!inventory || inventory.status !== 'enabled' || inventory.configured !== 1) {
    throw new Error('Provider network is not enabled and configured');
  }
  const id = crypto.randomUUID();
  const inserted = await db.prepare(`
    INSERT INTO ad_revenue_reports
      (id, network, period_start, period_end, settlement_period, currency,
       gross_revenue_paise, adjustments_paise, provider_fees_paise, net_revenue_paise,
       monetized_impressions, finalized, finalized_through_at, fetched_at,
       freshness_expires_at, source_reference, evidence_hash, imported_by)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(source_reference) DO NOTHING
  `).bind(
    id,
    input.network,
    input.periodStart,
    input.periodEnd,
    settlementPeriod,
    input.currency ?? 'INR',
    input.grossRevenuePaise,
    adjustmentsPaise,
    providerFeesPaise,
    netRevenuePaise,
    input.monetizedImpressions,
    input.finalizedThroughAt,
    input.fetchedAt,
    input.freshnessExpiresAt,
    sourceReference,
    input.evidenceHash.toLowerCase(),
    input.importedBy,
  ).run();
  if (inserted.meta.changes > 0) {
    return { id, idempotent: false, netRevenuePaise };
  }
  const existing = await db.prepare(`
    SELECT id, evidence_hash, net_revenue_paise, gross_revenue_paise,
           adjustments_paise, provider_fees_paise, monetized_impressions
    FROM ad_revenue_reports WHERE source_reference = ?
  `).bind(sourceReference).first<{
    id: string;
    evidence_hash: string;
    net_revenue_paise: number;
    gross_revenue_paise: number;
    adjustments_paise: number;
    provider_fees_paise: number;
    monetized_impressions: number;
  }>();
  if (!existing
    || existing.evidence_hash !== input.evidenceHash
    || existing.net_revenue_paise !== netRevenuePaise
    || existing.gross_revenue_paise !== input.grossRevenuePaise
    || existing.adjustments_paise !== adjustmentsPaise
    || existing.provider_fees_paise !== providerFeesPaise
    || existing.monetized_impressions !== input.monetizedImpressions) {
    throw new Error('Provider source reference was previously recorded with different evidence');
  }
  return { id: existing.id, idempotent: true, netRevenuePaise };
}

export async function reconcileFinalizedAdSenseReports(
  db: D1Database,
  config: AdSenseReconciliationConfig,
  now: number,
): Promise<AdSenseReconciliationResult> {
  if (!Number.isSafeInteger(now)) throw new Error('Reconciliation time is invalid');
  await markReconciliationStarted(db, now);
  if (!config.ADSENSE_REPORT_URL?.trim()) {
    const result: AdSenseReconciliationResult = {
      status: 'skipped',
      weeks: 0,
      fetched: 0,
      imported: 0,
      idempotent: 0,
      calculated: 0,
      failures: ['AdSense report endpoint is not configured'],
    };
    await markReconciliationFinished(db, now, result);
    return result;
  }
  const weeks = await db.prepare(`
    SELECT id, week_key, starts_at, ends_at
    FROM referral_weeks
    WHERE ends_at <= ? AND state IN ('closed', 'finalized')
    ORDER BY ends_at ASC
    LIMIT ?
  `).bind(now, MAX_RECONCILIATION_WEEKS).all<ReconciliationWeekRow>();

  let fetched = 0;
  let imported = 0;
  let idempotent = 0;
  let calculated = 0;
  const failures: string[] = [];
  try {
    for (const week of weeks.results) {
      let additionalWarnings: string[] = [];
      try {
        const aggregate = await fetchAdSenseAggregate(
          config.ADSENSE_REPORT_URL,
          config.ADSENSE_REPORT_TOKEN,
          week,
          now,
        );
        fetched += 1;
        const result = await ingestAdRevenueReport(db, {
          network: APPROVED_NETWORK,
          periodStart: week.starts_at,
          periodEnd: week.ends_at,
          settlementPeriod: aggregate.settlementPeriod,
          currency: aggregate.currency,
          grossRevenuePaise: aggregate.grossRevenuePaise,
          adjustmentsPaise: aggregate.adjustmentsPaise,
          providerFeesPaise: aggregate.providerFeesPaise,
          monetizedImpressions: aggregate.monetizedImpressions,
          finalized: true,
          finalizedThroughAt: aggregate.finalizedThroughAt,
          fetchedAt: now,
          freshnessExpiresAt: aggregate.freshnessExpiresAt,
          sourceReference: aggregate.sourceReference,
          evidenceHash: aggregate.evidenceHash,
          importedBy: 'adsense-reconciliation',
        });
        if (result.idempotent) idempotent += 1;
        else imported += 1;
      } catch (error) {
        additionalWarnings = ['adsense-reconciliation-failed'];
        failures.push(`${week.week_key}: ${safeFailureMessage(error)}`.slice(0, 512));
      }
      await calculateWeeklyRoi(db, {
        weekId: week.id,
        actorId: 'adsense-reconciliation',
        additionalWarnings,
        calculatedAt: now,
      });
      calculated += 1;
    }
  } catch (error) {
    const result: AdSenseReconciliationResult = {
      status: 'failed',
      weeks: weeks.results.length,
      fetched,
      imported,
      idempotent,
      calculated,
      failures: [...failures, safeFailureMessage(error)].slice(0, 16),
    };
    await markReconciliationFinished(db, now, result);
    throw error;
  }
  const result: AdSenseReconciliationResult = {
    status: 'completed',
    weeks: weeks.results.length,
    fetched,
    imported,
    idempotent,
    calculated,
    failures,
  };
  await markReconciliationFinished(db, now, result);
  return result;
}

export async function recordRoiControls(
  db: D1Database,
  input: {
    reserveHealthy: boolean;
    revenueFresh: boolean;
    invalidTrafficHealthy: boolean;
    adAccountHealthy: boolean;
    contributionMarginHealthy: boolean;
    identityResetsHealthy: boolean;
    fraudHealthy: boolean;
    exposureHealthy: boolean;
    evidenceId: string;
    warnings?: string[];
    updatedBy: string;
    updatedAt: number;
    expiresAt: number;
  },
): Promise<{ status: 'recorded'; pauseRecommended: boolean }> {
  if (
    !boundedText(input.evidenceId, 'ROI control evidence ID')
    || !Number.isSafeInteger(input.updatedAt)
    || !Number.isSafeInteger(input.expiresAt)
    || input.expiresAt <= input.updatedAt
    || input.expiresAt - input.updatedAt > REFERRAL_POLICY.funding.revenueEvidenceMaxAgeSeconds
  ) {
    throw new Error('Invalid ROI control evidence');
  }
  const warnings = (input.warnings ?? [])
    .filter(item => typeof item === 'string')
    .map(item => item.trim().slice(0, 256))
    .filter(Boolean)
    .slice(0, 32);
  const healthy = [
    input.reserveHealthy,
    input.revenueFresh,
    input.invalidTrafficHealthy,
    input.adAccountHealthy,
    input.contributionMarginHealthy,
    input.identityResetsHealthy,
    input.fraudHealthy,
    input.exposureHealthy,
  ].every(Boolean);
  await db.prepare(`
    INSERT INTO referral_roi_controls
      (id, reserve_healthy, revenue_fresh, invalid_traffic_healthy,
       ad_account_healthy, contribution_margin_healthy, identity_resets_healthy,
       fraud_healthy, exposure_healthy, evidence_id, warnings_json,
       updated_by, updated_at, expires_at)
    VALUES ('singleton', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
      reserve_healthy = excluded.reserve_healthy,
      revenue_fresh = excluded.revenue_fresh,
      invalid_traffic_healthy = excluded.invalid_traffic_healthy,
      ad_account_healthy = excluded.ad_account_healthy,
      contribution_margin_healthy = excluded.contribution_margin_healthy,
      identity_resets_healthy = excluded.identity_resets_healthy,
      fraud_healthy = excluded.fraud_healthy,
      exposure_healthy = excluded.exposure_healthy,
      evidence_id = excluded.evidence_id,
      warnings_json = excluded.warnings_json,
      updated_by = excluded.updated_by,
      updated_at = excluded.updated_at,
      expires_at = excluded.expires_at
  `).bind(
    input.reserveHealthy ? 1 : 0,
    input.revenueFresh ? 1 : 0,
    input.invalidTrafficHealthy ? 1 : 0,
    input.adAccountHealthy ? 1 : 0,
    input.contributionMarginHealthy ? 1 : 0,
    input.identityResetsHealthy ? 1 : 0,
    input.fraudHealthy ? 1 : 0,
    input.exposureHealthy ? 1 : 0,
    input.evidenceId.trim(),
    JSON.stringify(warnings),
    boundedText(input.updatedBy, 'Control operator'),
    input.updatedAt,
    input.expiresAt,
  ).run();
  return { status: 'recorded', pauseRecommended: !healthy || warnings.length > 0 };
}

export async function calculateWeeklyRoi(
  db: D1Database,
  input: {
    weekId: string;
    actorId: string;
    costs?: RoiCosts;
    additionalWarnings?: string[];
    calculatedAt: number;
  },
): Promise<Record<string, unknown>> {
  const costs = input.costs ?? {};
  const reviewCostInr = nonNegativeInteger(costs.reviewCostInr, 'Review cost');
  const fraudCostInr = nonNegativeInteger(costs.fraudCostInr, 'Fraud cost');
  const reversalCostInr = nonNegativeInteger(costs.reversalCostInr, 'Reversal cost');
  const supportCostInr = nonNegativeInteger(costs.supportCostInr, 'Support cost');
  const operatingCostInr = nonNegativeInteger(costs.operatingCostInr, 'Operating cost');
  const week = await db.prepare(`
    SELECT id, starts_at, ends_at FROM referral_weeks WHERE id = ?
  `).bind(input.weekId).first<WeekRow>();
  if (!week) throw new Error('Referral week not found');

  const revenue = await db.prepare(`
    SELECT * FROM ad_revenue_reports
    WHERE network = ? AND period_start = ? AND period_end = ? AND finalized = 1
    ORDER BY finalized_through_at DESC, created_at DESC LIMIT 1
  `).bind(APPROVED_NETWORK, week.starts_at, week.ends_at).first<RevenueRow>();
  const control = await db.prepare(`
    SELECT * FROM referral_roi_controls WHERE id = 'singleton'
  `).first<ControlRow>();
  const envelope = await db.prepare(`
    SELECT funded_cap_inr, reserved_inr, worst_case_exposure_inr
    FROM referral_weekly_envelopes WHERE week_id = ?
  `).bind(input.weekId).first<{
    funded_cap_inr: number;
    reserved_inr: number;
    worst_case_exposure_inr: number;
  }>();
  const [clicks, identityCounts, statements, payouts] = await Promise.all([
    db.prepare(`
      SELECT COUNT(*) AS count FROM referral_claim_events
      WHERE week_id = ? AND event_type = 'visit'
    `).bind(input.weekId).first<{ count: number }>(),
    db.prepare(`
      SELECT
        COUNT(DISTINCT CASE WHEN state != 'rejected' AND identity_confidence = 'browser' THEN identity_hash END)
          AS unique_browsers,
        COUNT(DISTINCT CASE WHEN state != 'rejected' AND account_id IS NOT NULL THEN account_id END)
          AS authenticated_accounts,
        COUNT(CASE WHEN state = 'mature' THEN 1 END) AS mature_verified,
        COUNT(DISTINCT CASE WHEN state != 'rejected' AND event_count > 1 THEN identity_hash END)
          AS repeat_week_visitors
      FROM referral_weekly_claims WHERE week_id = ?
    `).bind(input.weekId).first<{
      unique_browsers: number;
      authenticated_accounts: number;
      mature_verified: number;
      repeat_week_visitors: number;
    }>(),
    db.prepare(`
      SELECT COUNT(*) AS payable, COALESCE(SUM(
        CASE WHEN status NOT IN ('reversed', 'clawed_back') THEN gross_amount_inr ELSE 0 END
      ), 0) AS reserved
      FROM referral_weekly_statements WHERE week_id = ? AND gross_amount_inr > 0
    `).bind(input.weekId).first<{ payable: number; reserved: number }>(),
    db.prepare(`
      SELECT COALESCE(SUM(CASE WHEN status = 'paid' THEN amount_inr ELSE 0 END), 0) AS cash_paid
      FROM referral_payouts WHERE week_id = ?
    `).bind(input.weekId).first<{ cash_paid: number }>(),
  ]);

  const trueProgramCostInr = (statements?.reserved ?? envelope?.reserved_inr ?? 0)
    + reviewCostInr
    + fraudCostInr
    + reversalCostInr
    + supportCostInr
    + operatingCostInr;
  const warnings: string[] = (input.additionalWarnings ?? [])
    .filter(item => typeof item === 'string')
    .map(item => item.trim())
    .filter(Boolean)
    .slice(0, 16);
  if (!revenue) warnings.push('provider-revenue-missing');
  if (revenue && revenue.freshness_expires_at <= input.calculatedAt) warnings.push('provider-revenue-stale');
  if (!revenue || revenue.finalized !== 1) warnings.push('revenue-not-finalized');
  if (revenue && revenue.network !== APPROVED_NETWORK) warnings.push('revenue-network-not-approved');
  if (!control || control.expires_at <= input.calculatedAt) warnings.push('roi-control-evidence-stale');
  if (!control) warnings.push('roi-control-evidence-missing');
  if (control) {
    if (!control.reserve_healthy) warnings.push('reserve-shortage');
    if (!control.revenue_fresh) warnings.push('revenue-freshness-failed');
    if (!control.invalid_traffic_healthy) warnings.push('invalid-traffic-warning');
    if (!control.ad_account_healthy) warnings.push('ad-account-enforcement');
    if (!control.contribution_margin_healthy) warnings.push('negative-contribution-margin');
    if (!control.identity_resets_healthy) warnings.push('abnormal-identity-resets');
    if (!control.fraud_healthy) warnings.push('fraud-spike');
    if (!control.exposure_healthy) warnings.push('reward-exposure-above-policy');
    warnings.push(...parseWarnings(control.warnings_json));
  }
  if (!envelope || envelope.funded_cap_inr <= 0) warnings.push('funded-envelope-missing');
  if ((envelope?.reserved_inr ?? 0) > REFERRAL_POLICY.maximumWeeklyRewardExposureInr) {
    warnings.push('reward-exposure-above-policy');
  }
  if (envelope && envelope.reserved_inr > envelope.funded_cap_inr) warnings.push('reserve-shortage');

  const finalizedNetAdRevenuePaise = revenue?.net_revenue_paise ?? null;
  const contributionMarginPaise = finalizedNetAdRevenuePaise === null
    ? null
    : finalizedNetAdRevenuePaise - (trueProgramCostInr * 100);
  if (contributionMarginPaise !== null && contributionMarginPaise < 0) {
    warnings.push('negative-contribution-margin');
  }
  const uniqueWarnings = [...new Set(warnings)];
  const pauseRecommended = uniqueWarnings.length > 0;
  const dataQuality: RoiDataQuality = uniqueWarnings.length === 0
    ? 'healthy'
    : finalizedNetAdRevenuePaise === null || pauseRecommended
      ? 'blocked'
      : 'warning';
  const paybackRatioMilli = finalizedNetAdRevenuePaise === null || trueProgramCostInr === 0
    ? null
    : Math.trunc((finalizedNetAdRevenuePaise / (trueProgramCostInr * 100)) * 1_000);
  const now = input.calculatedAt;
  const reportId = crypto.randomUUID();
  await db.prepare(`
    INSERT INTO referral_weekly_roi_reports
      (id, week_id, revenue_report_id, referral_clicks, unique_browser_identities,
       authenticated_accounts, mature_verified_visitors, repeat_week_visitors,
       payable_statements, cash_paid_inr, reserved_rewards_inr, review_cost_inr,
       fraud_cost_inr, reversal_cost_inr, support_cost_inr, operating_cost_inr,
       true_program_cost_inr, actual_monetized_impressions,
       finalized_net_ad_revenue_paise, contribution_margin_paise, payback_ratio_milli,
       data_quality, pause_recommended, warnings_json, generated_by, generated_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(week_id) DO UPDATE SET
      revenue_report_id = excluded.revenue_report_id,
      referral_clicks = excluded.referral_clicks,
      unique_browser_identities = excluded.unique_browser_identities,
      authenticated_accounts = excluded.authenticated_accounts,
      mature_verified_visitors = excluded.mature_verified_visitors,
      repeat_week_visitors = excluded.repeat_week_visitors,
      payable_statements = excluded.payable_statements,
      cash_paid_inr = excluded.cash_paid_inr,
      reserved_rewards_inr = excluded.reserved_rewards_inr,
      review_cost_inr = excluded.review_cost_inr,
      fraud_cost_inr = excluded.fraud_cost_inr,
      reversal_cost_inr = excluded.reversal_cost_inr,
      support_cost_inr = excluded.support_cost_inr,
      operating_cost_inr = excluded.operating_cost_inr,
      true_program_cost_inr = excluded.true_program_cost_inr,
      actual_monetized_impressions = excluded.actual_monetized_impressions,
      finalized_net_ad_revenue_paise = excluded.finalized_net_ad_revenue_paise,
      contribution_margin_paise = excluded.contribution_margin_paise,
      payback_ratio_milli = excluded.payback_ratio_milli,
      data_quality = excluded.data_quality,
      pause_recommended = excluded.pause_recommended,
      warnings_json = excluded.warnings_json,
      generated_by = excluded.generated_by,
      generated_at = excluded.generated_at,
      updated_at = excluded.updated_at
  `).bind(
    reportId,
    week.id,
    revenue?.id ?? null,
    clicks?.count ?? 0,
    identityCounts?.unique_browsers ?? 0,
    identityCounts?.authenticated_accounts ?? 0,
    identityCounts?.mature_verified ?? 0,
    identityCounts?.repeat_week_visitors ?? 0,
    statements?.payable ?? 0,
    payouts?.cash_paid ?? 0,
    statements?.reserved ?? envelope?.reserved_inr ?? 0,
    reviewCostInr,
    fraudCostInr,
    reversalCostInr,
    supportCostInr,
    operatingCostInr,
    trueProgramCostInr,
    revenue?.monetized_impressions ?? 0,
    finalizedNetAdRevenuePaise,
    contributionMarginPaise,
    paybackRatioMilli,
    dataQuality,
    pauseRecommended ? 1 : 0,
    JSON.stringify(uniqueWarnings),
    input.actorId,
    now,
    now,
  ).run();

  if (pauseRecommended) {
    const reason = `ROI control pause: ${uniqueWarnings.join(', ')}`.slice(0, 1_000);
    await db.batch([
      db.prepare(`
        UPDATE referral_program_state
        SET state = 'paused', pause_effective_at = ?, updated_by = 'roi-monitor', updated_at = ?
        WHERE id = 'singleton' AND state = 'active'
      `).bind(now, now),
      db.prepare(`
        UPDATE referral_accrual_intervals
        SET state = 'paused', ends_at = ?, pause_reason = ?, actor_id = ?
        WHERE state = 'open' AND starts_at <= ?
      `).bind(now, reason, input.actorId, now),
    ]);
  }
  return {
    week_id: week.id,
    data_quality: dataQuality,
    pause_recommended: pauseRecommended,
    warnings: uniqueWarnings,
    referral_clicks: clicks?.count ?? 0,
    unique_browser_identities: identityCounts?.unique_browsers ?? 0,
    authenticated_accounts: identityCounts?.authenticated_accounts ?? 0,
    mature_verified_visitors: identityCounts?.mature_verified ?? 0,
    repeat_week_visitors: identityCounts?.repeat_week_visitors ?? 0,
    payable_statements: statements?.payable ?? 0,
    cash_paid_inr: payouts?.cash_paid ?? 0,
    reserved_rewards_inr: statements?.reserved ?? envelope?.reserved_inr ?? 0,
    true_program_cost_inr: trueProgramCostInr,
    actual_monetized_impressions: revenue?.monetized_impressions ?? 0,
    finalized_net_ad_revenue_paise: finalizedNetAdRevenuePaise,
    contribution_margin_paise: contributionMarginPaise,
    payback_ratio_milli: paybackRatioMilli,
  };
}

export async function roiDashboard(
  db: D1Database,
  limit = 12,
): Promise<Record<string, unknown>> {
  const safeLimit = Math.min(52, Math.max(1, Math.trunc(limit)));
  const [inventory, controls, reports, reconciliation] = await Promise.all([
    listAdNetworkInventory(db),
    db.prepare(`SELECT * FROM referral_roi_controls WHERE id = 'singleton'`).first<ControlRow>(),
    db.prepare(`
      SELECT r.*, w.week_key
      FROM referral_weekly_roi_reports r
      JOIN referral_weeks w ON w.id = r.week_id
      ORDER BY w.starts_at DESC LIMIT ?
    `).bind(safeLimit).all<Record<string, unknown>>(),
    db.prepare(`
      SELECT id, status, started_at, completed_at, weeks, fetched, imported,
             idempotent, calculated, failures_json
      FROM adsense_reconciliation_status
      WHERE id = 'singleton'
    `).first<ReconciliationStatusRow>(),
  ]);
  return {
    inventory,
    controls: controls ? {
      evidence_id: controls.evidence_id,
      reserve_healthy: controls.reserve_healthy === 1,
      revenue_fresh: controls.revenue_fresh === 1,
      invalid_traffic_healthy: controls.invalid_traffic_healthy === 1,
      ad_account_healthy: controls.ad_account_healthy === 1,
      contribution_margin_healthy: controls.contribution_margin_healthy === 1,
      identity_resets_healthy: controls.identity_resets_healthy === 1,
      fraud_healthy: controls.fraud_healthy === 1,
      exposure_healthy: controls.exposure_healthy === 1,
      warnings: parseWarnings(controls.warnings_json),
      updated_at: controls.updated_at,
      expires_at: controls.expires_at,
    } : null,
    reports: reports.results,
    reconciliation: reconciliation ? {
      status: reconciliation.status,
      started_at: reconciliation.started_at,
      completed_at: reconciliation.completed_at,
      weeks: reconciliation.weeks,
      fetched: reconciliation.fetched,
      imported: reconciliation.imported,
      idempotent: reconciliation.idempotent,
      calculated: reconciliation.calculated,
      failures: parseFailureSummaries(reconciliation.failures_json),
    } : null,
  };
}