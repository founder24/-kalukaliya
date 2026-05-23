#!/usr/bin/env node
/**
 * cloudflare-phase4-apply.js  — Phase 4: R2 Student Asset Storage + Cache Reserve
 *
 * Idempotent: safe to re-run at any time.  Each step checks current state
 * before creating or patching, so re-running after a partial failure is safe.
 *
 * What it creates / configures:
 *   1. R2 bucket: syrabit-assets  (student PDFs, syllabi, past papers)
 *   2. Custom domain: assets.syrabit.ai → syrabit-assets bucket
 *   3. R2 bucket: syrabit-cache-reserve  (Cache Reserve backing store)
 *   4. Cache Reserve: enabled on syrabit.ai zone → syrabit-cache-reserve bucket
 *
 * Worker changes (wrangler.toml + src/index.ts) are NOT applied here — they
 * require a `wrangler deploy` and are tracked in workers/edge-proxy/.
 *
 * Required env:
 *   CLOUDFLARE_API_TOKEN  — needs:
 *       R2: Edit    (bucket create + custom domain)
 *       Cache: Edit (Cache Reserve enable)
 *   CLOUDFLARE_ZONE_ID     — optional, defaults to syrabit.ai zone
 *   CLOUDFLARE_ACCOUNT_ID  — optional, defaults to Syrabit account
 *
 * Usage:
 *   node artifacts/syrabit/scripts/cloudflare-phase4-apply.js
 *   CLOUDFLARE_API_TOKEN=<tok> node artifacts/syrabit/scripts/cloudflare-phase4-apply.js
 */

const TOKEN      = process.env.CLOUDFLARE_API_TOKEN;
const ZONE_ID    = process.env.CLOUDFLARE_ZONE_ID    || '5b8c97df4431491dc7f60ea72fb61871';
const ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID || 'd66e40eac539fff1db270fddf384a5ec';
const API        = 'https://api.cloudflare.com/client/v4';

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

async function cfReq(method, path, body = undefined) {
  const opts = { method, headers };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(`${API}${path}`, opts);
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

// ── Step 1: Create syrabit-assets R2 bucket ──────────────────────────────────
async function ensureAssetsBucket() {
  console.log('\nStep 1 — R2 bucket: syrabit-assets');

  const list = await cfGet(`/accounts/${ACCOUNT_ID}/r2/buckets`);
  if (!list.success) {
    if (list.errors?.[0]?.code === 10000) {
      fail('syrabit-assets bucket', authErrMsg('R2: Edit'));
    } else {
      fail('syrabit-assets bucket', JSON.stringify(list.errors));
    }
    return false;
  }

  const existing = (list.result?.buckets || []).find(b => b.name === 'syrabit-assets');
  if (existing) {
    ok('syrabit-assets already exists', `location=${existing.location || 'auto'}`);
    return true;
  }

  // Create in auto-location (Cloudflare picks the closest region to the account)
  const create = await cfReq('POST', `/accounts/${ACCOUNT_ID}/r2/buckets`, {
    name:     'syrabit-assets',
    // No explicit location_hint — Cloudflare auto-places near the account region.
    // AHSEC/SEBA student base is in Assam → Asia Pacific; set to APAC if you
    // want to pin: uncomment below and set location_hint = 'APAC'.
    // location_hint: 'APAC',
  });

  if (create.success) {
    ok('syrabit-assets created');
    return true;
  }
  fail('syrabit-assets create', JSON.stringify(create.errors));
  return false;
}

// ── Step 2: Configure assets.syrabit.ai custom domain → syrabit-assets ───────
async function ensureAssetsDomain() {
  console.log('\nStep 2 — Custom domain: assets.syrabit.ai → syrabit-assets');

  // List existing custom domains on the bucket
  const list = await cfGet(`/accounts/${ACCOUNT_ID}/r2/buckets/syrabit-assets/domains/custom`);
  if (!list.success) {
    if (list.errors?.[0]?.code === 10000) {
      fail('assets.syrabit.ai custom domain', authErrMsg('R2: Edit'));
    } else if (list.errors?.[0]?.code === 10006) {
      // Bucket not found — Step 1 failed
      fail('assets.syrabit.ai custom domain', 'bucket syrabit-assets not found — run Step 1 first');
    } else {
      fail('assets.syrabit.ai custom domain', JSON.stringify(list.errors));
    }
    return;
  }

  const domains = list.result?.domains || [];
  const existing = domains.find(d => d.domain === 'assets.syrabit.ai');
  if (existing) {
    // Reconcile: ensure it is enabled
    if (!existing.enabled) {
      console.log('  ⚠  assets.syrabit.ai exists but is disabled — enabling');
      const patch = await cfReq('PUT',
        `/accounts/${ACCOUNT_ID}/r2/buckets/syrabit-assets/domains/custom/assets.syrabit.ai`,
        { enabled: true }
      );
      if (patch.success) ok('assets.syrabit.ai re-enabled');
      else fail('assets.syrabit.ai enable', JSON.stringify(patch.errors));
    } else {
      ok('assets.syrabit.ai already configured',
        `status=${existing.status || 'active'} enabled=${existing.enabled}`);
    }
    return;
  }

  // Create the custom domain
  // Prerequisite: assets.syrabit.ai must have a CNAME to <bucket>.r2.cloudflarestorage.com
  // or Cloudflare will provision it automatically when the domain is under the same zone.
  const create = await cfReq('POST',
    `/accounts/${ACCOUNT_ID}/r2/buckets/syrabit-assets/domains/custom`,
    {
      domain:  'assets.syrabit.ai',
      enabled: true,
      // minTTL 0 = use the CDN TTL from Cache-Control headers served by R2
      minTTL: 0,
    }
  );

  if (create.success) {
    ok('assets.syrabit.ai custom domain created',
      `status=${create.result?.status || 'pending'}`);
    console.log('  ℹ  DNS will propagate within 5–60 minutes.');
    console.log('  ℹ  Verify at: https://dash.cloudflare.com — R2 → syrabit-assets → Settings → Custom domains');
  } else {
    fail('assets.syrabit.ai custom domain create', JSON.stringify(create.errors));
    console.log('  ℹ  If the error is "domain already in use", check the R2 dashboard — the domain');
    console.log('  ℹ  may be registered in a different bucket or under the Pages custom-domains panel.');
  }
}

// ── Step 3: Create syrabit-cache-reserve R2 bucket ───────────────────────────
async function ensureCacheReserveBucket() {
  console.log('\nStep 3 — R2 bucket: syrabit-cache-reserve');

  const list = await cfGet(`/accounts/${ACCOUNT_ID}/r2/buckets`);
  if (!list.success) {
    if (list.errors?.[0]?.code === 10000) {
      fail('syrabit-cache-reserve bucket', authErrMsg('R2: Edit'));
    } else {
      fail('syrabit-cache-reserve bucket', JSON.stringify(list.errors));
    }
    return false;
  }

  const existing = (list.result?.buckets || []).find(b => b.name === 'syrabit-cache-reserve');
  if (existing) {
    ok('syrabit-cache-reserve already exists');
    return true;
  }

  const create = await cfReq('POST', `/accounts/${ACCOUNT_ID}/r2/buckets`, {
    name: 'syrabit-cache-reserve',
  });

  if (create.success) {
    ok('syrabit-cache-reserve created');
    return true;
  }
  fail('syrabit-cache-reserve create', JSON.stringify(create.errors));
  return false;
}

// ── Step 4: Enable Cache Reserve on the syrabit.ai zone ──────────────────────
async function ensureCacheReserve() {
  console.log('\nStep 4 — Cache Reserve: syrabit.ai zone');

  const current = await cfGet(`/zones/${ZONE_ID}/cache/cache_reserve`);
  if (!current.success) {
    const code = current.errors?.[0]?.code;
    if (code === 10000) {
      fail('Cache Reserve', authErrMsg('Cache: Edit'));
    } else if (code === 1135) {
      console.log('  ⚠  Cache Reserve: not available on current plan — requires Cache Reserve');
      console.log('  ⚠  subscription add-on. Enable at:');
      console.log('  ⚠    https://dash.cloudflare.com → Caching → Cache Reserve');
      console.log('  ⚠  Once subscribed, re-run this script to activate it on the zone.');
      return;
    } else {
      fail('Cache Reserve', JSON.stringify(current.errors));
    }
    return;
  }

  const value = current.result?.value;
  if (value === 'on') {
    ok('Cache Reserve already enabled on syrabit.ai');
    return;
  }

  console.log(`  Current Cache Reserve value: ${JSON.stringify(value)} — enabling`);

  // PATCH to enable Cache Reserve
  // The syrabit-cache-reserve bucket is automatically linked by Cloudflare
  // to the zone's Cache Reserve; no explicit bucket link API exists on zone-level.
  // The bucket must exist in the same account (ensured by Step 3).
  const patch = await cfReq('PATCH', `/zones/${ZONE_ID}/cache/cache_reserve`, {
    value: 'on',
  });

  if (patch.success) {
    ok('Cache Reserve enabled on syrabit.ai',
      `value=${patch.result?.value}`);
    console.log('  ℹ  Cache Reserve begins filling as origin responses are cached.');
    console.log('  ℹ  Cold-cache misses now resolve from R2 (syrabit-cache-reserve)');
    console.log('  ℹ  rather than forwarding to Railway.');
  } else {
    fail('Cache Reserve enable', JSON.stringify(patch.errors));
    console.log('  ℹ  Cache Reserve requires a Cloudflare Cache Reserve subscription.');
    console.log('  ℹ  Check: https://dash.cloudflare.com → Caching → Cache Reserve');
  }
}

// ── Step 5: Report worker action items ───────────────────────────────────────
function reportWorkerNextSteps() {
  console.log('\nStep 5 — Worker ASSETS binding (manual deploy required)');
  console.log('  ℹ  The ASSETS R2 binding and POST /admin/assets/upload route are');
  console.log('  ℹ  configured in workers/edge-proxy/wrangler.toml and src/index.ts.');
  console.log('  ℹ  To deploy:');
  console.log('  ℹ    cd workers/edge-proxy && wrangler deploy');
  console.log('  ℹ  After deploy, upload a PDF:');
  console.log('  ℹ    curl -X POST https://api.syrabit.ai/admin/assets/upload \\');
  console.log('  ℹ      -H "Authorization: Bearer $ADMIN_JWT" \\');
  console.log('  ℹ      -F "file=@question-paper.pdf" \\');
  console.log('  ℹ      -F "key=ahsec/2024/physics.pdf"');
  console.log('  ℹ  The PDF will be served at:');
  console.log('  ℹ    https://assets.syrabit.ai/ahsec/2024/physics.pdf');
}

// ── Main ──────────────────────────────────────────────────────────────────────
async function main() {
  console.log('Cloudflare Phase 4 — R2 Asset Storage + Cache Reserve');
  console.log(`Account: ${ACCOUNT_ID}   Zone: ${ZONE_ID}\n`);
  console.log('Token scope requirements: R2: Edit, Cache: Edit');

  const assetsBucketOk = await ensureAssetsBucket();
  if (assetsBucketOk) await ensureAssetsDomain();
  else console.log('  ⚠  Skipping custom domain — bucket creation failed');

  await ensureCacheReserveBucket();
  await ensureCacheReserve();
  reportWorkerNextSteps();

  console.log('');
  if (errors.length === 0) {
    console.log('Phase 4 apply complete — all steps OK.');
    console.log('Run nightly-smoke.js to verify the new assertions.');
  } else {
    console.error(`\n${errors.length} step(s) failed:\n  ${errors.join('\n  ')}`);
    console.error('\nMost common cause: token scope gap.');
    console.error('Add R2: Edit and Cache: Edit at:');
    console.error('  https://dash.cloudflare.com/profile/api-tokens');
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('Phase 4 apply error:', err.message);
  process.exit(1);
});
