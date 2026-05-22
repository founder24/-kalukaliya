#!/usr/bin/env node
/**
 * cloudflare-phase2-apply.js — Cloudflare Phase 2: Edge Visibility (Task #106)
 *
 * Idempotent apply script for Phase 2 resources:
 *   1. R2 bucket `syrabit-logs`  (created by Task #106, 2026-04-30)
 *   2. Logpush job: http_requests  → R2 (5-min gzip batches)
 *   3. Logpush job: firewall_events → R2 (5-min gzip batches)
 *   4. Origin Healthcheck polling https://api.syrabit.ai/health every 60 s
 *
 * Required env:
 *   CLOUDFLARE_API_TOKEN   — Zone Settings: Read + Logs: Edit + Health Checks: Edit
 *                            (current token lacks Logs: Edit and Health Checks: Edit —
 *                             add those scopes in the Cloudflare dashboard at
 *                             https://dash.cloudflare.com/profile/api-tokens before running)
 *   CLOUDFLARE_ZONE_ID     — optional, defaults to syrabit.ai zone
 *   CLOUDFLARE_ACCOUNT_ID  — optional, defaults to Syrabit account
 *   ADMIN_EMAIL            — email address for healthcheck failure notifications
 *   SLACK_WEBHOOK_URL      — optional, Slack webhook for healthcheck alerts
 *
 * Usage:
 *   node artifacts/syrabit/scripts/cloudflare-phase2-apply.js
 *   ADMIN_EMAIL=you@example.com node artifacts/syrabit/scripts/cloudflare-phase2-apply.js
 *
 * Idempotency: checks for existing resources by name/prefix before creating.
 * Safe to re-run — will skip resources that already exist.
 */

const TOKEN      = process.env.CLOUDFLARE_API_TOKEN;
const ZONE_ID    = process.env.CLOUDFLARE_ZONE_ID    || '5b8c97df4431491dc7f60ea72fb61871';
const ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID || 'd66e40eac539fff1db270fddf384a5ec';
const ADMIN_EMAIL = process.env.ADMIN_EMAIL;
const SLACK_WEBHOOK = process.env.SLACK_WEBHOOK_URL;
const API        = 'https://api.cloudflare.com/client/v4';
const BUCKET     = 'syrabit-logs';

if (!TOKEN) { console.error('CLOUDFLARE_API_TOKEN is not set'); process.exit(1); }

const headers = { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };

async function cfGet(path) {
  const res = await fetch(`${API}${path}`, { headers });
  const j = await res.json();
  return j;
}
async function cfReq(method, path, body) {
  const res = await fetch(`${API}${path}`, {
    method, headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const j = await res.json();
  return j;
}

const errors = [];

function ok(label, detail = '') {
  console.log(`  ✓  ${label}${detail ? '  ' + detail : ''}`);
}
function fail(label, detail = '') {
  console.log(`  ✗  ${label}${detail ? '  ' + detail : ''}`);
  errors.push(label);
}
function skip(label, reason) {
  console.log(`  –  ${label}  [skipped: ${reason}]`);
}

// ── Step 1: R2 bucket ─────────────────────────────────────────────────────
async function ensureR2Bucket() {
  console.log('\nStep 1: R2 bucket');
  const list = await cfGet(`/accounts/${ACCOUNT_ID}/r2/buckets`);
  if (!list.success) {
    fail(`R2 bucket ${BUCKET}`, `List error: ${JSON.stringify(list.errors)}`);
    return false;
  }
  const existing = (list.result?.buckets || []).find(b => b.name === BUCKET);
  if (existing) {
    ok(`R2 bucket ${BUCKET}`, 'already exists');
    return true;
  }
  const create = await cfReq('PUT', `/accounts/${ACCOUNT_ID}/r2/buckets/${BUCKET}`, {});
  if (create.success) {
    ok(`R2 bucket ${BUCKET}`, 'created');
    return true;
  }
  fail(`R2 bucket ${BUCKET}`, JSON.stringify(create.errors));
  return false;
}

// ── Step 2: Logpush jobs ──────────────────────────────────────────────────
// Output format: ndjson (newline-delimited JSON), rfc3339 timestamps.
// Compression: Cloudflare automatically gzip-compresses files pushed to R2
// (destination paths end in .gz) — no extra flag needed.
// Frequency "low" = 5-minute batches (Cloudflare's smallest standard batch interval).
async function ensureLogpushJob(name, dataset, fields, prefix) {
  const destinationConf = `r2://${BUCKET}/${prefix}/{DATE}?account-id=${ACCOUNT_ID}`;
  const logpullOptions  = `fields=${fields.join(',')}&timestamps=rfc3339&CVE-2021-44228=true`;

  const list = await cfGet(`/zones/${ZONE_ID}/logpush/jobs`);
  if (!list.success) {
    const code = list.errors?.[0]?.code;
    if (code === 10000) {
      fail(`Logpush job: ${name}`,
        'Authentication error — add "Logs: Edit" scope to CLOUDFLARE_API_TOKEN at ' +
        'https://dash.cloudflare.com/profile/api-tokens then re-run this script');
    } else {
      fail(`Logpush job: ${name}`, JSON.stringify(list.errors));
    }
    return;
  }

  const existing = list.result.find(j => j.name === name);
  if (existing) {
    ok(`Logpush job: ${name}`, `id=${existing.id} enabled=${existing.enabled}`);
    // Enable if disabled
    if (!existing.enabled) {
      const patch = await cfReq('PUT', `/zones/${ZONE_ID}/logpush/jobs/${existing.id}`, { enabled: true });
      if (patch.success) ok(`  enabled ${name}`);
      else fail(`  enable ${name}`, JSON.stringify(patch.errors));
    }
    return;
  }

  const create = await cfReq('POST', `/zones/${ZONE_ID}/logpush/jobs`, {
    name,
    destination_conf: destinationConf,
    dataset,
    logpull_options:  logpullOptions,
    frequency:        'low',    // 5-minute batches (Cloudflare's "low" frequency)
    // output_type "ndjson" = newline-delimited JSON (one record per line).
    // R2 destination automatically stores output as gzip-compressed files
    // (the pushed objects have a .gz suffix).
    output_options: {
      output_type:      'ndjson',
      timestamp_format: 'rfc3339',
      sample_rate:      1,        // 100% of requests — do not sample
    },
    enabled: true,
  });

  if (create.success) {
    ok(`Logpush job: ${name}`, `id=${create.result.id}`);
  } else {
    fail(`Logpush job: ${name}`, JSON.stringify(create.errors));
  }
}

// ── Step 3: Origin Healthcheck ────────────────────────────────────────────
async function ensureHealthcheck() {
  console.log('\nStep 3: Origin Healthcheck');
  const list = await cfGet(`/zones/${ZONE_ID}/healthchecks`);
  if (!list.success) {
    const code = list.errors?.[0]?.code;
    if (code === 10000) {
      fail('Origin Healthcheck',
        'Authentication error — add "Health Checks: Edit" scope to CLOUDFLARE_API_TOKEN at ' +
        'https://dash.cloudflare.com/profile/api-tokens then re-run this script');
      return null;
    }
    fail('Origin Healthcheck', JSON.stringify(list.errors));
    return null;
  }

  const existing = list.result.find(h => h.name === 'api-syrabit-ai-origin');
  if (existing) {
    ok('Origin Healthcheck api-syrabit-ai-origin', `id=${existing.id} status=${existing.status}`);
    return existing.id;
  }

  const create = await cfReq('POST', `/zones/${ZONE_ID}/healthchecks`, {
    name:        'api-syrabit-ai-origin',
    description: 'Railway backend health — alerts admin within 60 s of origin failure',
    address:     'api.syrabit.ai',
    path:        '/health',
    type:        'HTTPS',
    port:        443,
    interval:    60,
    retries:     2,
    timeout:     10,
    method:      'GET',
    expected_codes: '200',
    follow_redirects: false,
    allow_insecure: false,
    consecutive_down: 2,
    consecutive_up:   3,
    notification_suspended: false,
    notification_email_addresses: ADMIN_EMAIL ? [ADMIN_EMAIL] : [],
    header: {},
  });

  if (create.success) {
    ok('Origin Healthcheck api-syrabit-ai-origin', `id=${create.result.id}`);
    return create.result.id;
  }
  fail('Origin Healthcheck', JSON.stringify(create.errors));
  return null;
}

// ── Webhook destination helper ────────────────────────────────────────────
// Cloudflare alerting policy mechanisms reference webhook destinations by
// their CF-assigned UUID, not by the raw webhook URL. This function ensures
// the Slack webhook URL is registered as a CF webhook destination and returns
// the destination UUID to use in the policy's `webhooks` mechanism array.
async function ensureSlackWebhookDestination() {
  if (!SLACK_WEBHOOK) return null;

  const list = await cfGet(`/accounts/${ACCOUNT_ID}/alerting/v3/destinations/webhooks`);
  if (!list.success) {
    fail('Slack webhook destination list', JSON.stringify(list.errors));
    return null;
  }
  const existing = list.result.find(w => w.name === 'syrabit-slack-alerts');
  if (existing) {
    ok('Slack webhook destination', `id=${existing.id}`);
    return existing.id;
  }
  const create = await cfReq('POST', `/accounts/${ACCOUNT_ID}/alerting/v3/destinations/webhooks`, {
    name: 'syrabit-slack-alerts',
    url:  SLACK_WEBHOOK,
  });
  if (create.success) {
    ok('Slack webhook destination created', `id=${create.result.id}`);
    return create.result.id;
  }
  fail('Slack webhook destination', JSON.stringify(create.errors));
  return null;
}

// ── Step 4: Notification policy ───────────────────────────────────────────
async function ensureNotificationPolicy(healthcheckId) {
  console.log('\nStep 4: Healthcheck notification policy');
  if (!healthcheckId) {
    skip('Notification policy', 'healthcheck not created');
    return;
  }
  if (!ADMIN_EMAIL) {
    // A healthcheck without a notification policy is silent — treat as a hard failure
    // so the operator is alerted to set ADMIN_EMAIL before the job completes.
    fail('Notification policy',
      'ADMIN_EMAIL env var is not set — re-run with ADMIN_EMAIL=you@example.com ' +
      'to ensure the healthcheck can actually alert on origin failures');
    return;
  }

  const list = await cfGet(`/accounts/${ACCOUNT_ID}/alerting/v3/policies`);
  if (!list.success) {
    fail('Notification policy list', JSON.stringify(list.errors));
    return;
  }

  const existing = list.result.find(p =>
    p.alert_type === 'health_check_status_notification' &&
    p.name === 'api.syrabit.ai origin down'
  );
  if (existing) {
    ok('Notification policy', `id=${existing.id} enabled=${existing.enabled}`);
    return;
  }

  // Register Slack webhook as a CF destination first (returns UUID, not raw URL)
  const slackDestId = await ensureSlackWebhookDestination();

  const mechanisms = {
    email: [{ id: ADMIN_EMAIL }],
    // CF alerting webhooks mechanism expects { id: <destination UUID> }, not raw URL.
    ...(slackDestId ? { webhooks: [{ id: slackDestId }] } : {}),
  };

  const create = await cfReq('POST', `/accounts/${ACCOUNT_ID}/alerting/v3/policies`, {
    name:        'api.syrabit.ai origin down',
    description: 'Alert when Railway backend fails the Cloudflare healthcheck',
    enabled:     true,
    alert_type:  'health_check_status_notification',
    filters:     { health_check_id: [healthcheckId] },
    mechanisms,
  });

  if (create.success) {
    ok('Notification policy', `id=${create.result.id}`);
  } else {
    fail('Notification policy', JSON.stringify(create.errors));
  }
}

// ── Main ──────────────────────────────────────────────────────────────────
async function main() {
  console.log('Cloudflare Phase 2 Apply — Edge Visibility (Task #106)');
  console.log(`Zone: ${ZONE_ID}  Account: ${ACCOUNT_ID}\n`);

  // Step 1: R2
  await ensureR2Bucket();

  // Step 2: Logpush — http_requests
  // Field name mapping (task spec → actual CF API field name, verified via
  //   GET /accounts/{id}/logpush/datasets/http_requests/fields):
  //   CacheStatus      → CacheCacheStatus  (CF Enterprise schema uses compound name)
  //   WAFAction        → SecurityAction    (unified action field across WAF rule types)
  //   BotScore         → (not available in http_requests dataset; VerifiedBotCategory used instead)
  //   ClientCountryName→ ClientCountry     (two-letter ISO code; no full-name variant available)
  console.log('\nStep 2a: Logpush — http_requests');
  await ensureLogpushJob(
    'syrabit-http-requests',
    'http_requests',
    [
      'ClientRequestURI', 'ClientRequestMethod', 'ClientRequestHost',
      'EdgeResponseStatus',
      'CacheCacheStatus',     // spec: CacheStatus
      'SecurityAction',       // spec: WAFAction
      'WAFAttackScore',
      'ClientCountry',        // spec: ClientCountryName (two-letter ISO; full name not available)
      'ClientASN',
      'EdgeStartTimestamp', 'OriginResponseTime',
      'VerifiedBotCategory',  // spec: BotScore (raw score not in dataset; category used instead)
      'ClientIP', 'RayID',
    ],
    'http-requests',
  );

  // Step 2b: Logpush — firewall_events
  console.log('\nStep 2b: Logpush — firewall_events');
  await ensureLogpushJob(
    'syrabit-firewall-events',
    'firewall_events',
    [
      'Action', 'ClientIP', 'ClientCountry', 'ClientASN',
      'ClientRequestHost', 'ClientRequestMethod', 'ClientRequestPath',
      'ClientRequestUserAgent', 'Datetime',
      'EdgeResponseStatus', 'RuleID', 'Source', 'RayID',
    ],
    'firewall-events',
  );

  // Steps 3 + 4: Healthcheck + notification policy
  const hcId = await ensureHealthcheck();
  await ensureNotificationPolicy(hcId);

  // Summary
  console.log('\n────────────────────────────────────────');
  if (errors.length === 0) {
    console.log('Phase 2 apply complete — all resources in place.');
  } else {
    console.error(`${errors.length} step(s) failed:\n  ${errors.join('\n  ')}`);
    console.error('\nFix the issues above and re-run. The script is idempotent.');
    process.exit(1);
  }
}

main().catch(err => { console.error('Apply error:', err.message); process.exit(1); });
