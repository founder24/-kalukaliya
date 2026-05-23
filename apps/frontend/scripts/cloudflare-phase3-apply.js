#!/usr/bin/env node
/**
 * cloudflare-phase3-apply.js — Cloudflare Phase 3: Zero Trust + Waiting Room (Task #107)
 *
 * Idempotent apply script for Phase 3 resources:
 *   1. Cloudflare Access application — covers api.syrabit.ai/admin/*  (8 h session)
 *   2. Access policy — allows only listed team email addresses
 *   3. Waiting Room — syrabit.ai/* (exam-season traffic queue, 10-min session cookie)
 *      with branded custom HTML page
 *
 * Required env:
 *   CLOUDFLARE_API_TOKEN   — Zero Trust: Edit, Waiting Room: Edit
 *                            (current token lacks these scopes — add them at
 *                             https://dash.cloudflare.com/profile/api-tokens then re-run)
 *   ADMIN_EMAILS           — comma-separated list of team emails allowed through Access
 *                            e.g. "alice@syrabit.ai,bob@syrabit.ai"
 *   CLOUDFLARE_ZONE_ID     — optional, defaults to syrabit.ai zone
 *   CLOUDFLARE_ACCOUNT_ID  — optional, defaults to Syrabit account
 *
 *   Optional:
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

const ADMIN_EMAILS_RAW = process.env.ADMIN_EMAILS || '';
const ADMIN_EMAILS     = ADMIN_EMAILS_RAW
  .split(',')
  .map(e => e.trim())
  .filter(Boolean);

const WAITING_ROOM_NEW_PER_MIN    = parseInt(process.env.WAITING_ROOM_NEW_USERS_PER_MINUTE || '200', 10);
const WAITING_ROOM_TOTAL_ACTIVE   = parseInt(process.env.WAITING_ROOM_TOTAL_ACTIVE_USERS   || '400', 10);

if (!TOKEN) { console.error('CLOUDFLARE_API_TOKEN is not set'); process.exit(1); }
if (!ADMIN_EMAILS.length) {
  console.error('ADMIN_EMAILS is not set — re-run with ADMIN_EMAILS=you@example.com');
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
    if (list.errors?.[0]?.code === 10000) fail('Access application', authErrMsg('Zero Trust: Edit'));
    else fail('Access application', JSON.stringify(list.errors));
    return null;
  }

  const existing = list.result.find(a => a.name === 'Syrabit Admin');
  if (existing) {
    ok('Access application: Syrabit Admin', `id=${existing.id}`);
    // Reconcile — patch if critical security settings have drifted
    const needsPatch = (
      existing.domain !== 'api.syrabit.ai/admin*' ||
      existing.session_duration !== '8h'
    );
    if (needsPatch) {
      console.log(`  ⚠  Drift detected: domain=${existing.domain} session=${existing.session_duration} — patching`);
      const patch = await cfReq('PUT', `/accounts/${ACCOUNT_ID}/access/apps/${existing.id}`, {
        ...existing,
        domain:           'api.syrabit.ai/admin*',
        session_duration: '8h',
      });
      if (patch.success) ok('  Patched domain/session to target state');
      else fail(`  Patch Access app`, JSON.stringify(patch.errors));
    }
    return existing.id;
  }

  const create = await cfReq('POST', `/accounts/${ACCOUNT_ID}/access/apps`, {
    name:                'Syrabit Admin',
    type:                'self_hosted',
    // Wildcard suffix is required to cover all nested admin routes
    // (api.syrabit.ai/admin alone would only protect the exact path)
    domain:              'api.syrabit.ai/admin*',
    session_duration:    '8h',
    // Security hardening
    http_only_cookie_attribute:  true,
    same_site_cookie_attribute:  'strict',
    enable_binding_cookie:       true,
    // UX
    app_launcher_visible:        false,
    auto_redirect_to_identity:   false,
    // Restrict to the Syrabit CF account's configured identity providers
    allowed_idps: [],   // empty = all configured IDPs on the account
  });

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
    if (list.errors?.[0]?.code === 10000) fail('Access policy', authErrMsg('Zero Trust: Edit'));
    else fail('Access policy', JSON.stringify(list.errors));
    return;
  }

  const existing = list.result.find(p => p.name === 'Team email allowlist');
  if (existing) {
    ok('Access policy: Team email allowlist', `id=${existing.id}`);
    const currentEmails = (existing.include || [])
      .filter(r => r.email)
      .map(r => r.email.email);
    const missing = ADMIN_EMAILS.filter(e => !currentEmails.includes(e));
    if (missing.length) {
      console.log(`  ⚠  Policy exists but missing emails: ${missing.join(', ')} — update via dashboard`);
    }
    return;
  }

  const includeRules = ADMIN_EMAILS.map(email => ({ email: { email } }));
  console.log(`  Creating policy for: ${ADMIN_EMAILS.join(', ')}`);

  const create = await cfReq('POST', `/accounts/${ACCOUNT_ID}/access/apps/${appId}/policies`, {
    name:       'Team email allowlist',
    decision:   'allow',
    include:    includeRules,
    exclude:    [],
    require:    [],
    precedence: 1,
  });

  if (create.success) {
    ok('Access policy created', `id=${create.result.id} emails=${ADMIN_EMAILS.join(',')}`);
  } else {
    fail('Access policy', JSON.stringify(create.errors));
  }
}

// ── Step 3: Identity provider check ──────────────────────────────────────
async function checkIdentityProviders() {
  console.log('\nStep 3: Identity providers');
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
  console.log('Cloudflare Phase 3 Apply — Zero Trust + Waiting Room (Task #107)');
  console.log(`Zone: ${ZONE_ID}  Account: ${ACCOUNT_ID}`);
  console.log(`Admin emails: ${ADMIN_EMAILS.join(', ')}\n`);

  const appId = await ensureAccessApp();
  await ensureAccessPolicy(appId);
  await checkIdentityProviders();
  await ensureWaitingRoom();

  console.log('\n────────────────────────────────────────');
  if (errors.length === 0) {
    console.log('Phase 3 apply complete — all resources in place.');
    console.log('\nNext step: verify at https://one.dash.cloudflare.com');
  } else {
    console.error(`${errors.length} step(s) failed:\n  ${errors.join('\n  ')}`);
    console.error('\nFix the issues above and re-run. The script is idempotent.');
    console.error('\nRequired token scopes for Phase 3:');
    console.error('  • Zero Trust: Edit  — for Access apps and policies');
    console.error('  • Waiting Room: Edit — for Waiting Room');
    console.error('Add at: https://dash.cloudflare.com/profile/api-tokens');
    process.exit(1);
  }
}

main().catch(err => { console.error('Apply error:', err.message); process.exit(1); });
