#!/usr/bin/env node
/**
 * cloudflare-phase5-apply.js
 *
 * Task #109 — Cloudflare Phase 5: Workers Analytics Engine + Durable Objects.
 *
 * This script:
 *   1. Verifies the syrabit-edge Worker script exists.
 *   2. Checks whether the ANALYTICS (Analytics Engine) binding is present
 *      in the deployed worker's bindings (informational only — the binding
 *      is declared in wrangler.toml and applied by `wrangler deploy`).
 *   3. Checks whether the RateLimiter Durable Object namespace exists
 *      (created automatically by `wrangler deploy` via [[migrations]]).
 *   4. Prints the deploy commands to run if any bindings are missing.
 *
 * Unlike previous phase scripts (R2 buckets, Logpush, etc.), Phase 5
 * resources are NOT created via REST API calls — they are declared as
 * wrangler.toml bindings and provisioned automatically by `wrangler deploy`.
 *
 * Required env:
 *   CLOUDFLARE_API_TOKEN  — Workers: Read, Durable Objects: Read scopes
 *                           (Zone Settings: Edit scope is NOT required here)
 *   CLOUDFLARE_ACCOUNT_ID — optional, defaults to syrabit account
 *
 * Usage:
 *   node artifacts/syrabit/scripts/cloudflare-phase5-apply.js
 *   CLOUDFLARE_API_TOKEN=<tok> node cloudflare-phase5-apply.js
 */

import process from 'process';

const TOKEN      = process.env.CLOUDFLARE_API_TOKEN;
const ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID || 'd66e40eac539fff1db270fddf384a5ec';
const WORKER_NAME = 'syrabit-edge';
const AE_DATASET  = 'syrabit-edge-metrics';
const DO_CLASS    = 'RateLimiter';
const API         = 'https://api.cloudflare.com/client/v4';

if (!TOKEN) {
  console.error('CLOUDFLARE_API_TOKEN is not set');
  process.exit(1);
}

const headers = { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };

async function cfGet(path) {
  const res = await fetch(`${API}${path}`, { headers });
  const j   = await res.json();
  return j;
}

/** Returns null on auth error (code 10000), throws for other errors. */
async function cfGetOrSkip(path) {
  const res = await fetch(`${API}${path}`, { headers });
  const j   = await res.json();
  if (j.success)                              return j;
  if (j.errors?.[0]?.code === 10000)          return null;
  throw new Error(`CF error on ${path}: ${JSON.stringify(j.errors)}`);
}

async function main() {
  console.log('════════════════════════════════════════════════════════');
  console.log(' Cloudflare Phase 5 Apply — Workers AE + DO rate limiter');
  console.log('════════════════════════════════════════════════════════\n');
  console.log(`Account:  ${ACCOUNT_ID}`);
  console.log(`Worker:   ${WORKER_NAME}\n`);

  let needsDeploy = false;

  // ── Step 1: Verify the Worker script exists ──────────────────────────
  console.log('Step 1 — Verify syrabit-edge worker exists:');
  const script = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/workers/scripts/${WORKER_NAME}`);
  if (!script) {
    console.log('  ? Worker script — token lacks Workers: Read scope');
    console.log('    Skipping worker metadata checks; run wrangler deploy manually.');
  } else if (script.success === false) {
    const code = script.errors?.[0]?.code;
    if (code === 10007) {
      console.log('  ✗  Worker script NOT FOUND. Deploy with:');
      console.log('       cd workers/edge-proxy && wrangler deploy');
      needsDeploy = true;
    } else {
      console.log(`  ?  Worker script check error: ${JSON.stringify(script.errors)}`);
    }
  } else {
    console.log(`  ✓  Worker script ${WORKER_NAME} exists.`);
  }

  // ── Step 2: Check Analytics Engine dataset binding ───────────────────
  console.log('\nStep 2 — Check Analytics Engine dataset binding:');
  console.log(`  Target dataset: ${AE_DATASET}`);
  const bindings = await cfGetOrSkip(
    `/accounts/${ACCOUNT_ID}/workers/scripts/${WORKER_NAME}/bindings`,
  );
  if (!bindings) {
    console.log('  ? Bindings — token lacks Workers: Read scope');
    console.log('    Cannot verify AE binding remotely; check the Workers dashboard.');
    console.log(`    After deploy: dash.cloudflare.com → Workers → ${WORKER_NAME} → Settings → Bindings`);
  } else if (bindings.success) {
    const aeBinding = (bindings.result || []).find(
      (b) => b.type === 'analytics_engine' && b.dataset === AE_DATASET,
    );
    if (aeBinding) {
      console.log(`  ✓  ANALYTICS binding found (dataset: ${aeBinding.dataset})`);
    } else {
      console.log('  ✗  ANALYTICS analytics_engine binding NOT found in deployed worker.');
      console.log('      Ensure wrangler.toml has [analytics_engine_datasets] ANALYTICS binding,');
      console.log('      then deploy: cd workers/edge-proxy && wrangler deploy');
      needsDeploy = true;
    }
  } else {
    console.log(`  ?  Bindings error: ${JSON.stringify(bindings.errors)}`);
  }

  // ── Step 3: Check Durable Object namespace ───────────────────────────
  console.log('\nStep 3 — Check RateLimiter Durable Object namespace:');
  const doNamespaces = await cfGetOrSkip(
    `/accounts/${ACCOUNT_ID}/workers/durable_objects/namespaces`,
  );
  if (!doNamespaces) {
    console.log('  ? DO namespaces — token lacks Durable Objects: Read scope');
    console.log('    Cannot verify DO namespace remotely; check the Workers dashboard.');
    console.log(`    After deploy: dash.cloudflare.com → Workers → Durable Objects`);
  } else if (doNamespaces.success) {
    const ns = (doNamespaces.result || []).find(
      (n) => n.class === DO_CLASS && n.script === WORKER_NAME,
    );
    if (ns) {
      console.log(`  ✓  RateLimiter DO namespace found (id=${ns.id})`);
    } else {
      // Could also be present under a slightly different matcher — list them
      const matchByClass = (doNamespaces.result || []).filter((n) => n.class === DO_CLASS);
      if (matchByClass.length > 0) {
        console.log(`  ✓  RateLimiter DO namespace found on another script:`);
        matchByClass.forEach((n) => console.log(`      id=${n.id} script=${n.script}`));
      } else {
        console.log('  ✗  RateLimiter Durable Object namespace NOT found.');
        console.log('      The [[migrations]] in wrangler.toml creates it on first deploy.');
        console.log('      Run: cd workers/edge-proxy && wrangler deploy');
        needsDeploy = true;
      }
    }
  } else {
    console.log(`  ?  DO namespaces error: ${JSON.stringify(doNamespaces.errors)}`);
  }

  // ── Step 4: CF_ANALYTICS_TOKEN reminder ─────────────────────────────
  console.log('\nStep 4 — CF_ANALYTICS_TOKEN secret reminder:');
  console.log('  The /api/edge/analytics route requires a CF API token with');
  console.log('  Account Analytics: Read scope (separate from the main CF token).');
  console.log('  If not already set, run:');
  console.log('    cd workers/edge-proxy && wrangler secret put CF_ANALYTICS_TOKEN');
  console.log('  Then enter a token created at: dash.cloudflare.com/profile/api-tokens');
  console.log('  with "Account → Analytics → Read" permission.');

  // ── Summary ──────────────────────────────────────────────────────────
  console.log('\n────────────────────────────────────────────────────────');
  if (needsDeploy) {
    console.log('ACTION REQUIRED — some Phase 5 bindings are not yet deployed.');
    console.log('  cd workers/edge-proxy');
    console.log('  wrangler deploy');
    console.log('\nThis will:');
    console.log('  • Create the RateLimiter DO namespace (via [[migrations]] tag v1)');
    console.log('  • Register the ANALYTICS [analytics_engine_datasets] binding');
    console.log('  • Export RateLimiter class and add checkRateLimitWithDO() routing');
    console.log('\nAfter deploy, verify in the dashboard:');
    console.log('  dash.cloudflare.com → Workers → syrabit-edge → Settings → Bindings');
    process.exit(1);
  } else {
    console.log('Phase 5 apply check complete.');
    console.log('If bindings were not verified (token scope gaps), run `wrangler deploy`');
    console.log('and check the Workers dashboard to confirm all bindings are present.');
  }
}

main().catch((err) => {
  console.error('Phase 5 apply error:', err.message);
  process.exit(1);
});
