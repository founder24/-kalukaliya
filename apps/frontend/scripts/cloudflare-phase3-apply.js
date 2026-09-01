#!/usr/bin/env node
/**
 * cloudflare-phase3-apply.js — Cloudflare Phase 3: Zero Trust Access
 *
 * Idempotent apply script for Phase 3 resources:
 *   1. Cloudflare Access application — one audience covering staff UI and
 *      privileged API compatibility paths (8 h session)
 *   2. Access policy — allows only listed team email addresses
 *   3. A narrower cron app requires a Cloudflare Access service token for
 *      /api/v1/admin/cron* automation; application-level cron auth remains.
 *   3. Optional Waiting Room reconciliation when APPLY_WAITING_ROOM=true.
 *      It is disabled by default so Access changes cannot alter traffic queues.
 *
 * Required env:
 *   CLOUDFLARE_API_TOKEN   — Access: Apps and Policies Edit
 *                            Access: Service Tokens Read
 *                            Access: Identity Providers Read is recommended
 *                            so the script can report the active login method.
 *   STAFF_EMAILS           — comma-separated list of team emails allowed through Access
 *                            e.g. "alice@syrabit.ai,bob@syrabit.ai"
 *                            ADMIN_EMAILS remains accepted as a legacy alias.
 *   CLOUDFLARE_ZONE_ID     — optional, defaults to syrabit.ai zone
 *   CLOUDFLARE_ACCOUNT_ID  — optional, defaults to Syrabit account
 *
 *   Optional:
 *   APPLY_WAITING_ROOM                   — default false
 *   WAITING_ROOM_NEW_USERS_PER_MINUTE  — default 200 (tuned to Railway hobby plan)
 *   WAITING_ROOM_TOTAL_ACTIVE_USERS    — default 400
 *
 * Usage:
 *   node artifacts/syrabit/scripts/cloudflare-phase3-apply.js
 *
 * Idempotency:
 *   Checks for existing resources by name before creating.
 *   Safe to re-run — will skip resources that already exist and only
 *   reconcile enabled state.
 */

import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname  = dirname(__filename);

const TOKEN      = process.env.CLOUDFLARE_API_TOKEN;
const ZONE_ID    = process.env.CLOUDFLARE_ZONE_ID    || '5b8c97df4431491dc7f60ea72fb61871';
const ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID || 'd66e40eac539fff1db270fddf384a5ec';
const API        = 'https://api.cloudflare.com/client/v4';

const STAFF_EMAILS_RAW = process.env.STAFF_EMAILS || process.env.ADMIN_EMAILS || '';
const STAFF_EMAILS     = STAFF_EMAILS_RAW
  .split(',')
  .map(e => e.trim())
  .filter(Boolean);

const WAITING_ROOM_NEW_PER_MIN    = parseInt(process.env.WAITING_ROOM_NEW_USERS_PER_MINUTE || '200', 10);
const WAITING_ROOM_TOTAL_ACTIVE   = parseInt(process.env.WAITING_ROOM_TOTAL_ACTIVE_USERS   || '400', 10);
const APPLY_WAITING_ROOM          = process.env.APPLY_WAITING_ROOM === 'true';
const ADMIN_DESTINATIONS = [
  'syrabit.ai/staff*',
  'api.syrabit.ai/api/v1/admin*',
  'api.syrabit.ai/admin*',
];
const CRON_DESTINATION = 'api.syrabit.ai/api/v1/admin/cron*';

if (!TOKEN) { console.error('CLOUDFLARE_API_TOKEN is not set'); process.exit(1); }
if (!STAFF_EMAILS.length) {
  console.error('STAFF_EMAILS is not set — re-run with STAFF_EMAILS=you@example.com');
  process.exit(1);
}

const headers = { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };

async function cfGet(path_) {
  const res = await fetch(`${API}${path_}`, { headers });
  const j = await res.json();
  return j;
}
async function cfReq(method, path_, body) {
  const res = await fetch(`${API}${path_}`, {
    method, headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json();
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

function authErrMsg(scope) {
  return `Authentication error — add "${scope}" to CLOUDFLARE_API_TOKEN at ` +
         'https://dash.cloudflare.com/profile/api-tokens then re-run this script';
}

// ── Step 1: Zero Trust Access application ────────────────────────────────
async function ensureAccessApp() {
  console.log('\nStep 1: Zero Trust Access application');
  const list = await cfGet(`/accounts/${ACCOUNT_ID}/access/apps`);
  if (!list.success) {
    if (list.errors?.[0]?.code === 10000) fail('Access application', authErrMsg('Access: Apps and Policies Edit'));
    else fail('Access application', JSON.stringify(list.errors));
    return null;
  }

  const existing = list.result.find(a => a.name === 'Syrabit Admin');
  const desiredDestinations = ADMIN_DESTINATIONS.map(uri => ({ type: 'public', uri }));
  const appPayload = {
    name:                'Syrabit Admin',
    type:                'self_hosted',
    destinations:        desiredDestinations,
    session_duration:    '8h',
    http_only_cookie_attribute:  true,
    same_site_cookie_attribute:  'strict',
    enable_binding_cookie:       true,
    app_launcher_visible:        false,
    auto_redirect_to_identity:   false,
    allowed_idps: [],
  };
  if (existing) {
    ok('Access application: Syrabit Admin', `id=${existing.id}`);
    // Reconcile — patch if critical security settings have drifted
    const currentDestinations = (existing.destinations || [])
      .filter(destination => destination.type === 'public')
      .map(destination => destination.uri)
      .sort();
    const needsPatch = (
      JSON.stringify(currentDestinations) !== JSON.stringify([...ADMIN_DESTINATIONS].sort()) ||
      existing.session_duration !== '8h'
    );
    if (needsPatch) {
      console.log(`  ⚠  Drift detected: destinations=${currentDestinations.join(',')} session=${existing.session_duration} — patching`);
      const patch = await cfReq('PUT', `/accounts/${ACCOUNT_ID}/access/apps/${existing.id}`, appPayload);
      if (patch.success) ok('  Patched destinations/session to target state');
      else fail(`  Patch Access app`, JSON.stringify(patch.errors));
    }
    return existing.id;
  }

  const create = await cfReq('POST', `/accounts/${ACCOUNT_ID}/access/apps`, appPayload);

  if (create.success) {
    ok('Access application created: Syrabit Admin', `id=${create.result.id}`);
    return create.result.id;
  }
  fail('Access application', JSON.stringify(create.errors));
  return null;
}

// ── Step 2: Access policy ─────────────────────────────────────────────────
async function ensureAccessPolicy(appId) {
  console.log('\nStep 2: Access policy');
  if (!appId) {
    skip('Access policy', 'app not created');
    return;
  }

  const list = await cfGet(`/accounts/${ACCOUNT_ID}/access/apps/${appId}/policies`);
  if (!list.success) {
    if (list.errors?.[0]?.code === 10000) fail('Access policy', authErrMsg('Access: Apps and Policies Edit'));
    else fail('Access policy', JSON.stringify(list.errors));
    return;
  }

  const existing = list.result.find(p => p.name === 'Team email allowlist');
  if (existing) {
    ok('Access policy: Team email allowlist', `id=${existing.id}`);
    const currentEmails = (existing.include || [])
      .filter(r => r.email)
      .map(r => r.email.email)
      .sort();
    const desiredEmails = [...STAFF_EMAILS].sort();
    if (JSON.stringify(currentEmails) !== JSON.stringify(desiredEmails)) {
      console.log('  ⚠  Allowlist drift detected — reconciling exact approved addresses');
      const updated = await cfReq('PUT', `/accounts/${ACCOUNT_ID}/access/apps/${appId}/policies/${existing.id}`, {
        name: 'Team email allowlist',
        decision: 'allow',
        include: STAFF_EMAILS.map(email => ({ email: { email } })),
        exclude: [],
        require: [],
        precedence: 1,
      });
      if (updated.success) ok('Access policy allowlist reconciled');
      else fail('Access policy allowlist reconcile', JSON.stringify(updated.errors));
    }
    return;
  }

  const includeRules = STAFF_EMAILS.map(email => ({ email: { email } }));
  console.log(`  Creating policy for: ${STAFF_EMAILS.join(', ')}`);

  const create = await cfReq('POST', `/accounts/${ACCOUNT_ID}/access/apps/${appId}/policies`, {
    name:       'Team email allowlist',
    decision:   'allow',
    include:    includeRules,
    exclude:    [],
    require:    [],
    precedence: 1,
  });

  if (create.success) {
    ok('Access policy created', `id=${create.result.id} emails=${STAFF_EMAILS.join(',')}`);
  } else {
    fail('Access policy', JSON.stringify(create.errors));
  }
}

async function ensureAdminCiServiceAuth(appId) {
  console.log('\nStep 3: Cutover CI service authentication');
  if (!appId) {
    skip('Cutover CI Service Auth policy', 'admin app not created');
    return;
  }
  const serviceTokens = await cfGet(`/accounts/${ACCOUNT_ID}/access/service_tokens`);
  if (!serviceTokens.success) {
    fail('Cutover CI service token lookup', JSON.stringify(serviceTokens.errors));
    return;
  }
  const serviceToken = serviceTokens.result.find(token =>
    token.name === 'Syrabit GitHub Cron' && token.enabled !== false
  );
  if (!serviceToken) {
    fail('Cutover CI service token', 'Syrabit GitHub Cron is missing or disabled');
    return;
  }
  const policies = await cfGet(`/accounts/${ACCOUNT_ID}/access/apps/${appId}/policies`);
  if (!policies.success) {
    fail('Cutover CI Service Auth policy', JSON.stringify(policies.errors));
    return;
  }
  const existing = policies.result.find(policy =>
    policy.name === 'GitHub cutover service authentication' &&
    policy.decision === 'non_identity' &&
    (policy.include || []).some(rule => rule.service_token?.token_id === serviceToken.id)
  );
  if (existing) {
    ok('Cutover CI Service Auth policy', `id=${existing.id}`);
    return;
  }
  const created = await cfReq('POST', `/accounts/${ACCOUNT_ID}/access/apps/${appId}/policies`, {
    name: 'GitHub cutover service authentication',
    decision: 'non_identity',
    precedence: 2,
    include: [{ service_token: { token_id: serviceToken.id } }],
    exclude: [],
    require: [],
  });
  if (created.success) ok('Cutover CI Service Auth policy created', `id=${created.result.id}`);
  else fail('Cutover CI Service Auth policy', JSON.stringify(created.errors));
}

async function ensureCronServiceAuth() {
  console.log('\nStep 4: Two-layer cron service authentication');
  const apps = await cfGet(`/accounts/${ACCOUNT_ID}/access/apps`);
  if (!apps.success) {
    fail('Cron Access application', JSON.stringify(apps.errors));
    return;
  }

  let app = apps.result.find(candidate => candidate.name === 'Syrabit Admin Cron API');
  if (!app) {
    const created = await cfReq('POST', `/accounts/${ACCOUNT_ID}/access/apps`, {
      name: 'Syrabit Admin Cron API',
      type: 'self_hosted',
      destinations: [{ type: 'public', uri: CRON_DESTINATION }],
      session_duration: '15m',
      app_launcher_visible: false,
      auto_redirect_to_identity: false,
    });
    if (!created.success) {
      fail('Cron Access application', JSON.stringify(created.errors));
      return;
    }
    app = created.result;
    ok('Cron Access application created', `id=${app.id}`);
  } else {
    ok('Cron Access application', `id=${app.id}`);
    const destinations = (app.destinations || [])
      .filter(destination => destination.type === 'public')
      .map(destination => destination.uri);
    if (destinations.length !== 1 || destinations[0] !== CRON_DESTINATION) {
      const updated = await cfReq('PUT', `/accounts/${ACCOUNT_ID}/access/apps/${app.id}`, {
        name: 'Syrabit Admin Cron API',
        type: 'self_hosted',
        destinations: [{ type: 'public', uri: CRON_DESTINATION }],
        session_duration: '15m',
        app_launcher_visible: false,
        auto_redirect_to_identity: false,
      });
      if (updated.success) {
        app = updated.result;
        ok('Cron Access destination reconciled');
      } else {
        fail('Cron Access destination reconcile', JSON.stringify(updated.errors));
        return;
      }
    }
  }

  const serviceTokens = await cfGet(`/accounts/${ACCOUNT_ID}/access/service_tokens`);
  if (!serviceTokens.success) {
    fail('Cron service token lookup', JSON.stringify(serviceTokens.errors));
    return;
  }
  const serviceToken = serviceTokens.result.find(token =>
    token.name === 'Syrabit GitHub Cron' && token.enabled !== false
  );
  if (!serviceToken) {
    fail('Cron service token', 'Syrabit GitHub Cron is missing or disabled');
    return;
  }

  const policies = await cfGet(`/accounts/${ACCOUNT_ID}/access/apps/${app.id}/policies`);
  if (!policies.success) {
    fail('Cron Service Auth policy', JSON.stringify(policies.errors));
    return;
  }
  for (const unsafePolicy of policies.result.filter(policy => policy.decision === 'bypass')) {
    const removed = await cfReq(
      'DELETE',
      `/accounts/${ACCOUNT_ID}/access/apps/${app.id}/policies/${unsafePolicy.id}`,
    );
    if (!removed.success) {
      fail('Unsafe cron bypass removal', JSON.stringify(removed.errors));
      return;
    }
    ok('Removed unsafe cron bypass policy', `id=${unsafePolicy.id}`);
  }
  const existing = policies.result.find(policy =>
    policy.name === 'GitHub cron service authentication' &&
    policy.decision === 'non_identity' &&
    (policy.include || []).some(rule => rule.service_token?.token_id === serviceToken.id)
  );
  if (existing) {
    ok('Cron Service Auth policy', `id=${existing.id}`);
    return;
  }
  const created = await cfReq('POST', `/accounts/${ACCOUNT_ID}/access/apps/${app.id}/policies`, {
    name: 'GitHub cron service authentication',
    decision: 'non_identity',
    precedence: 1,
    include: [{ service_token: { token_id: serviceToken.id } }],
    exclude: [],
    require: [],
  });
  if (created.success) ok('Cron Service Auth policy created', `id=${created.result.id}`);
  else fail('Cron Service Auth policy', JSON.stringify(created.errors));
}

// ── Step 5: Identity provider check ──────────────────────────────────────
async function checkIdentityProviders() {
  console.log('\nStep 5: Identity providers');
  const list = await cfGet(`/accounts/${ACCOUNT_ID}/access/identity_providers`);
  if (!list.success) {
    if (list.errors?.[0]?.code === 10000) {
      console.log('  –  Identity provider check skipped  [token lacks Zero Trust: Read]');
      console.log('     Ensure at least one IDP (Google or GitHub) is configured at:');
      console.log('     https://one.dash.cloudflare.com/access/identity-providers');
    } else {
      console.log('  ?  Identity provider check error:', JSON.stringify(list.errors));
    }
    return;
  }

  if (!list.result.length) {
    console.log('  ⚠  No identity providers configured — Access will use One-time PIN (OTP) email fallback.');
    console.log('     Recommended: add Google Workspace at https://one.dash.cloudflare.com/access/identity-providers');
  } else {
    list.result.forEach(idp => {
      ok(`Identity provider: ${idp.name}`, `type=${idp.type} id=${idp.id}`);
    });
  }
}

// ── Step 4: Waiting Room ──────────────────────────────────────────────────
async function ensureWaitingRoom() {
  console.log('\nStep 4: Waiting Room');

  // Load the branded HTML template from disk
  const htmlPath = join(__dirname, 'waiting-room-page.html');
  let customPageHtml;
  try {
    customPageHtml = readFileSync(htmlPath, 'utf8');
  } catch {
    fail('Waiting Room HTML template', `Could not read ${htmlPath}`);
    return;
  }

  const list = await cfGet(`/zones/${ZONE_ID}/waiting_rooms`);
  if (!list.success) {
    if (list.errors?.[0]?.code === 10000) fail('Waiting Room', authErrMsg('Waiting Room: Edit'));
    else fail('Waiting Room', JSON.stringify(list.errors));
    return;
  }

  const existing = list.result.find(w => w.name === 'syrabit-exam-season-queue');
  if (existing) {
    ok('Waiting Room: syrabit-exam-season-queue', `id=${existing.id} enabled=${existing.enabled}`);
    // Reconcile — patch if throughput thresholds, session, or enabled have drifted
    const driftFields = {};
    if (!existing.enabled)
      driftFields.enabled = true;
    if (existing.session_duration !== 10)
      driftFields.session_duration = 10;
    if (existing.new_users_per_minute !== WAITING_ROOM_NEW_PER_MIN)
      driftFields.new_users_per_minute = WAITING_ROOM_NEW_PER_MIN;
    if (existing.total_active_users !== WAITING_ROOM_TOTAL_ACTIVE)
      driftFields.total_active_users = WAITING_ROOM_TOTAL_ACTIVE;

    if (Object.keys(driftFields).length > 0) {
      console.log(`  ⚠  Drift detected: ${JSON.stringify(driftFields)} — patching`);
      const patch = await cfReq('PATCH', `/zones/${ZONE_ID}/waiting_rooms/${existing.id}`, driftFields);
      if (patch.success) ok('  Patched Waiting Room to target state');
      else fail('  Patch Waiting Room', JSON.stringify(patch.errors));
    }
    return;
  }

  console.log(`  Creating Waiting Room:`);
  console.log(`    new_users_per_minute: ${WAITING_ROOM_NEW_PER_MIN}`);
  console.log(`    total_active_users:   ${WAITING_ROOM_TOTAL_ACTIVE}`);

  const create = await cfReq('POST', `/zones/${ZONE_ID}/waiting_rooms`, {
    name:                   'syrabit-exam-season-queue',
    host:                   'syrabit.ai',
    path:                   '/',
    // Throughput thresholds — tuned to Railway hobby plan concurrency.
    // Increase these when upgrading to Railway Pro (or cf: increase new_users_per_minute
    // to ~500 and total_active_users to ~1000 when on Railway Pro).
    new_users_per_minute:   WAITING_ROOM_NEW_PER_MIN,
    total_active_users:     WAITING_ROOM_TOTAL_ACTIVE,
    // Session cookie lasts 10 minutes — active students are not re-queued mid-session
    session_duration:       10,
    cookie_suffix:          'syrabit',
    // Disable the waiting room outside exam season via this flag:
    // PATCH /zones/{id}/waiting_rooms/{wr_id} { "enabled": false }
    enabled:                true,
    // Queue method: fifo (first-in-first-out), not random
    queueing_method:        'fifo',
    // Disable for JSON API paths so native apps are not affected
    json_response_enabled:  false,
    // Custom branded page (Cloudflare template syntax)
    custom_page_html:       customPageHtml,
    default_template_language: 'en-US',
  });

  if (create.success) {
    ok('Waiting Room created: syrabit-exam-season-queue', `id=${create.result.id}`);
  } else {
    fail('Waiting Room', JSON.stringify(create.errors));
  }
}

// ── Main ──────────────────────────────────────────────────────────────────
async function main() {
  console.log('Cloudflare Phase 3 Apply — Zero Trust Access');
  console.log(`Zone: ${ZONE_ID}  Account: ${ACCOUNT_ID}`);
  console.log(`Staff emails: ${STAFF_EMAILS.join(', ')}\n`);

  const appId = await ensureAccessApp();
  await ensureAccessPolicy(appId);
  await ensureAdminCiServiceAuth(appId);
  await ensureCronServiceAuth();
  await checkIdentityProviders();
  if (APPLY_WAITING_ROOM) {
    await ensureWaitingRoom();
  } else {
    skip('Waiting Room', 'out of scope; set APPLY_WAITING_ROOM=true for an intentional reconciliation');
  }

  console.log('\n────────────────────────────────────────');
  if (errors.length === 0) {
    console.log('Phase 3 apply complete — all resources in place.');
    console.log('\nNext step: verify at https://one.dash.cloudflare.com');
  } else {
    console.error(`${errors.length} step(s) failed:\n  ${errors.join('\n  ')}`);
    console.error('\nFix the issues above and re-run. The script is idempotent.');
    console.error('\nRequired token scopes for Phase 3:');
    console.error('  • Access: Apps and Policies Edit — for Access apps and policies');
    console.error('  • Access: Service Tokens Read — to bind the GitHub cron token');
    console.error('  • Access: Identity Providers Read — to report the login method');
    console.error('Add at: https://dash.cloudflare.com/profile/api-tokens');
    process.exit(1);
  }
}

main().catch(err => { console.error('Apply error:', err.message); process.exit(1); });
