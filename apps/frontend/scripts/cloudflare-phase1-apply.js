#!/usr/bin/env node
/**
 * cloudflare-phase1-apply.js — Phase 1: Zone Settings Hardening
 *
 * Idempotent: safe to re-run at any time. Each step checks current state
 * before patching, so re-running after a partial failure is safe.
 *
 * What it configures:
 *   1. sort_query_string_for_cache: on
 *   2. true_client_ip_header: on
 *   3. ech: on
 *   4. http3: on
 *   5. brotli: on
 *   6. http2: on
 *   7. always_use_https: on
 *   8. min_tls_version: 1.2
 *   9. tls_1_3: zrt
 *   10. automatic_https_rewrites: on
 *   11. ssl: strict
 *
 * Required env:
 *   CLOUDFLARE_API_TOKEN  — needs Zone Settings: Edit
 *   CLOUDFLARE_ZONE_ID    — optional, defaults to syrabit.ai zone
 *
 * Usage:
 *   CLOUDFLARE_API_TOKEN=<tok> node artifacts/syrabit/scripts/cloudflare-phase1-apply.js
 */

const TOKEN   = process.env.CLOUDFLARE_API_TOKEN;
const ZONE_ID = process.env.CLOUDFLARE_ZONE_ID || '5b8c97df4431491dc7f60ea72fb61871';
const API     = 'https://api.cloudflare.com/client/v4';

if (!TOKEN) {
  console.error('CLOUDFLARE_API_TOKEN is not set');
  process.exit(1);
}

const headers = {
  'Authorization': `Bearer ${TOKEN}`,
  'Content-Type':  'application/json',
};

async function cfGet(path) {
  const res = await fetch(`${API}${path}`, { headers });
  return res.json();
}

async function cfPatch(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(body),
  });
  return res.json();
}

const errors = [];

function ok(label, note = '') {
  const n = note ? `  [${note}]` : '';
  console.log(`  ✓  ${label}${n}`);
}

function fail(label, detail) {
  console.log(`  ✗  ${label}: ${detail}`);
  errors.push(label);
}

function authErrMsg(scope) {
  return `token lacks ${scope} — add at https://dash.cloudflare.com/profile/api-tokens`;
}

async function ensureZoneSetting(setting, targetValue, description) {
  const current = await cfGet(`/zones/${ZONE_ID}/settings/${setting}`);
  
  if (!current.success) {
    const code = current.errors?.[0]?.code;
    if (code === 10000) {
      fail(description, authErrMsg('Zone Settings: Edit'));
      return false;
    } else {
      fail(description, JSON.stringify(current.errors));
      return false;
    }
  }

  const actual = current.result.value;
  if (JSON.stringify(actual) === JSON.stringify(targetValue)) {
    ok(description, `value=${actual}`);
    return true;
  }

  console.log(`  Current ${setting}: ${JSON.stringify(actual)} — updating to ${JSON.stringify(targetValue)}`);

  const patch = await cfPatch(`/zones/${ZONE_ID}/settings/${setting}`, {
    value: targetValue,
  });

  if (patch.success) {
    ok(description, `updated from ${JSON.stringify(actual)} to ${JSON.stringify(targetValue)}`);
    return true;
  } else {
    fail(description, JSON.stringify(patch.errors));
    return false;
  }
}

async function main() {
  console.log('Cloudflare Phase 1 — Zone Settings Hardening');
  console.log(`Zone: ${ZONE_ID}\n`);
  console.log('Token scope requirement: Zone Settings: Edit\n');

  const checks = [
    ['sort_query_string_for_cache', 'on', 'Sort Query String for Cache'],
    ['true_client_ip_header', 'on', 'True Client IP Header'],
    ['ech', 'on', 'Encrypted Client Hello (ECH)'],
    ['http3', 'on', 'HTTP/3'],
    ['brotli', 'on', 'Brotli Compression'],
    ['http2', 'on', 'HTTP/2'],
    ['always_use_https', 'on', 'Always Use HTTPS'],
    ['min_tls_version', '1.2', 'Minimum TLS Version'],
    ['tls_1_3', 'zrt', 'TLS 1.3 (Zero Round Trip Time)'],
    ['automatic_https_rewrites', 'on', 'Automatic HTTPS Rewrites'],
    ['ssl', 'strict', 'SSL/TLS Encryption Mode'],
  ];

  for (const [setting, targetValue, description] of checks) {
    await ensureZoneSetting(setting, targetValue, description);
  }

  console.log('');
  if (errors.length === 0) {
    console.log('Phase 1 apply complete — all zone settings OK.');
  } else {
    console.error(`\n${errors.length} setting(s) failed:\n  ${errors.join('\n  ')}`);
    console.error('\nMost common cause: token scope gap.');
    console.error('Add Zone Settings: Edit at:');
    console.error('  https://dash.cloudflare.com/profile/api-tokens');
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('Phase 1 apply error:', err.message);
  process.exit(1);
});
