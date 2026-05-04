#!/usr/bin/env node
/**
 * cloudflare-phase6-apply.js
 *
 * Task #110 — Cloudflare Phase 6: mTLS origin hardening, Zaraz analytics,
 * Cloudflare Images, and Observatory.
 *
 * Steps performed:
 *   1. Issue a Cloudflare mTLS client certificate for api.syrabit.ai.
 *   2. Enable Cloudflare Image Resizing on the zone.
 *   3. Configure Zaraz with a GA4 tool (page-view + click events).
 *   4. Schedule weekly Observatory Lighthouse runs for homepage + chapter page.
 *   5. Print post-apply instructions (wrangler secret, wrangler.toml update).
 *
 * Required env:
 *   CLOUDFLARE_API_TOKEN  — must have:
 *       SSL and Certificates: Edit  (mTLS cert issuance)
 *       Zone Settings: Edit         (Image Resizing enable)
 *       Zaraz: Edit                 (GA4 tool configuration)
 *       Speed (Observatory): Edit   (Lighthouse scheduling)
 *   CLOUDFLARE_ZONE_ID    — optional, defaults to syrabit.ai zone
 *   CLOUDFLARE_ACCOUNT_ID — optional, defaults to Syrabit account
 *   GA4_MEASUREMENT_ID    — GA4 Measurement ID (format G-XXXXXXXXXX)
 *
 * Usage:
 *   CLOUDFLARE_API_TOKEN=<tok> GA4_MEASUREMENT_ID=G-XXXXXXX \
 *     node artifacts/syrabit/scripts/cloudflare-phase6-apply.js
 *
 *   Dry-run (skip mutating calls, validate config only):
 *   DRY_RUN=1 CLOUDFLARE_API_TOKEN=<tok> \
 *     node artifacts/syrabit/scripts/cloudflare-phase6-apply.js
 */

const crypto     = require('crypto');

const TOKEN      = process.env.CLOUDFLARE_API_TOKEN;
const ZONE_ID    = process.env.CLOUDFLARE_ZONE_ID    || '5b8c97df4431491dc7f60ea72fb61871';
const ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID || 'd66e40eac539fff1db270fddf384a5ec';
const GA4_ID     = process.env.GA4_MEASUREMENT_ID    || '';
const DRY_RUN    = process.env.DRY_RUN === '1';
const API        = 'https://api.cloudflare.com/client/v4';

if (!TOKEN) {
  console.error('CLOUDFLARE_API_TOKEN is not set');
  process.exit(1);
}

const headers = { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };

async function cfGet(path) {
  const res = await fetch(`${API}${path}`, { headers });
  const j   = await res.json();
  return j;
}

async function cfPost(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
  const j = await res.json();
  return j;
}

async function cfPut(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'PUT',
    headers,
    body: JSON.stringify(body),
  });
  const j = await res.json();
  return j;
}

async function cfPatch(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'PATCH',
    headers,
    body: JSON.stringify(body),
  });
  const j = await res.json();
  return j;
}

function ok(label)  { console.log(`  ✓  ${label}`); }
function err(label) { console.log(`  ✗  ${label}`); }
function info(msg)  { console.log(`  ℹ  ${msg}`); }
function dry(label) { console.log(`  ─  [DRY-RUN] would: ${label}`); }

/**
 * Compute the SHA-256 fingerprint of a PEM-encoded certificate.
 * The fingerprint is derived from the DER (binary) form of the certificate,
 * which matches what `openssl x509 -fingerprint -sha256` produces.
 * Returns lowercase hex without colons, matching the format expected by
 * MtlsClientCertMiddleware's MTLS_CERT_SHA256 comparison.
 *
 * @param {string} pem  PEM-encoded certificate (BEGIN CERTIFICATE … END CERTIFICATE)
 * @returns {string}    64-character lowercase hex SHA-256 fingerprint
 */
function computeCertFingerprint(pem) {
  const der = Buffer.from(
    pem.replace(/-----[^-]+-----/g, '').replace(/\s+/g, ''),
    'base64',
  );
  return crypto.createHash('sha256').update(der).digest('hex');
}

// ────────────────────────────────────────────────────────────────────────────
// Step 1: Issue mTLS client certificate (RETIRED in Task #335)
// ────────────────────────────────────────────────────────────────────────────
// The Railway-origin mTLS cert ("syrabit-railway-mtls") was issued to
// authenticate the Cloudflare worker against the Railway backend. Railway
// was decommissioned in Task #335; this step is now a no-op so the rest
// of the phase-6 apply script keeps working. Delete the cert in the
// Cloudflare dashboard (SSL/TLS → Client Certificates) as part of the
// same cleanup.
async function stepMtlsCert() {
  console.log('\n── Step 1: Issue mTLS client certificate (skipped) ──');
  info('Railway origin decommissioned in Task #335 — mTLS issuance no longer required.');
  info('Delete the legacy "syrabit-railway-mtls" cert in the Cloudflare dashboard if it still exists.');
  return { id: null, fingerprint: null };
}

// ────────────────────────────────────────────────────────────────────────────
// Step 2: Enable Cloudflare Image Resizing on the zone
// ────────────────────────────────────────────────────────────────────────────
async function stepImageResizing() {
  console.log('\n── Step 2: Enable Cloudflare Image Resizing ──');
  console.log('  Target: zone setting image_resizing = "on"');

  const current = await cfGet(`/zones/${ZONE_ID}/settings/image_resizing`);
  if (current.success && current.result?.value === 'on') {
    ok('image_resizing is already on');
    return;
  }

  if (DRY_RUN) {
    dry('PATCH /zones/{id}/settings/image_resizing — set value: "on"');
    return;
  }

  const r = await cfPatch(`/zones/${ZONE_ID}/settings/image_resizing`, { value: 'on' });
  if (r.success) {
    ok('image_resizing enabled (value: "on")');
    info('The frontend imageCdn.js helper already routes thumbnails through');
    info('/cdn-cgi/image/{opts}/{src}; this setting enables the transformer.');
  } else {
    const code = r.errors?.[0]?.code;
    if (code === 1135) {
      err('image_resizing: not available on current plan');
      info('Requires "Image Resizing" add-on: dash.cloudflare.com → Speed → Optimization.');
    } else {
      err(`image_resizing failed: ${JSON.stringify(r.errors)}`);
    }
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Step 3: Configure Zaraz with GA4 tool
// ────────────────────────────────────────────────────────────────────────────
async function stepZaraz() {
  console.log('\n── Step 3: Configure Zaraz with GA4 ──');

  if (!GA4_ID || !/^G-[A-Z0-9]{6,12}$/.test(GA4_ID)) {
    err(`GA4_MEASUREMENT_ID not set or invalid (got: "${GA4_ID}")`);
    info('Set GA4_MEASUREMENT_ID=G-XXXXXXXXXX and re-run this script.');
    info('The Measurement ID is at analytics.google.com → Admin → Data Streams.');
    return;
  }

  console.log(`  GA4 Measurement ID: ${GA4_ID}`);

  // Read current Zaraz config
  const current = await cfGet(`/zones/${ZONE_ID}/zaraz/config`);
  if (!current.success) {
    const code = current.errors?.[0]?.code;
    if (code === 10000) {
      err('Zaraz: token lacks Zaraz: Edit scope — add it to the API token and re-run.');
    } else {
      err(`Zaraz config read failed: ${JSON.stringify(current.errors)}`);
    }
    info('Alternative: configure GA4 in Zaraz via dash.cloudflare.com → Zaraz → Tools → Add tool → Google Analytics.');
    return;
  }

  // Check if a GA4 tool is already configured
  const tools = current.result?.tools || {};
  const ga4Tool = Object.values(tools).find(
    t => t.type === 'GA4' || (t.name && t.name.toLowerCase().includes('ga4')),
  );

  if (ga4Tool) {
    ok(`Zaraz GA4 tool already configured: "${ga4Tool.name}"`);
    return;
  }

  if (DRY_RUN) {
    dry(`PUT /zones/{id}/zaraz/config — add GA4 tool (measurementId=${GA4_ID})`);
    return;
  }

  // Build the updated Zaraz config with a GA4 tool.
  // Zaraz config is a full-document PUT — we merge our tool into the existing config.
  const toolId   = `ga4-${Date.now()}`;
  const newTool  = {
    name:    'Google Analytics 4',
    type:    'GA4',
    enabled: true,
    settings: {
      measurementId: GA4_ID,
    },
    // Standard triggers: page view on every navigation, click on interactive elements.
    rules: [
      {
        match:  { type: 'pageview' },
        action: { type: 'pageview' },
      },
      {
        match:  { type: 'click', selector: 'a, button' },
        action: { type: 'event', value: 'click' },
      },
    ],
    // Zaraz server-side mode — GA4 events are sent from Cloudflare edge, not
    // student devices. No GA4 JavaScript loads on the client.
    permissions: ['server'],
  };

  const updatedConfig = {
    ...current.result,
    tools: { ...tools, [toolId]: newTool },
  };

  const r = await cfPut(`/zones/${ZONE_ID}/zaraz/config`, updatedConfig);
  if (r.success) {
    ok(`Zaraz GA4 tool added: toolId=${toolId} measurementId=${GA4_ID}`);
    info('GA4 events now fire server-side from the Cloudflare edge — no gtag.js on student devices.');
    info('The existing window.gtag() calls in usePageTracking.js are forwarded via the Zaraz compatibility layer.');
  } else {
    err(`Zaraz config update failed: ${JSON.stringify(r.errors)}`);
    info('Fallback: add the GA4 tool manually at dash.cloudflare.com → Zaraz → Tools → Add → Google Analytics.');
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Step 4: Schedule Observatory (Lighthouse) runs + verify alert policies
// ────────────────────────────────────────────────────────────────────────────
async function stepObservatory() {
  console.log('\n── Step 4: Schedule Observatory Lighthouse runs ──');
  console.log('  Target: weekly Lighthouse for homepage + a chapter page, region us-central1');
  console.log('  Alert thresholds: LCP>2.5 s, CLS>0.1, INP>200 ms → email admin@syrabit.ai');

  const targets = [
    { label: 'homepage',     url: 'https://syrabit.ai/' },
    { label: 'chapter page', url: 'https://syrabit.ai/ahsec/class-12/physics' },
  ];

  for (const target of targets) {
    // Check if a schedule already exists for this URL
    const existing = await cfGet(`/zones/${ZONE_ID}/speed/schedule?url=${encodeURIComponent(target.url)}`);
    if (existing.success && existing.result?.schedule) {
      ok(`Observatory schedule already exists for ${target.label}: frequency=${existing.result.schedule.frequency}`);
      continue;
    }

    if (DRY_RUN) {
      dry(`POST /zones/{id}/speed/schedule — ${target.label} (${target.url}), weekly`);
      continue;
    }

    const r = await cfPost(`/zones/${ZONE_ID}/speed/schedule`, {
      url:                target.url,
      region:             'us-central1',
      schedule_frequency: 'weekly',
    });

    if (r.success) {
      ok(`Observatory scheduled: ${target.label} — weekly from us-central1`);
    } else {
      const code = r.errors?.[0]?.code;
      if (code === 1135) {
        err(`Observatory: not available on current plan for ${target.label}`);
        info('Requires Workers Paid or Enterprise plan with Observatory access.');
      } else {
        err(`Observatory schedule failed for ${target.label}: ${JSON.stringify(r.errors)}`);
      }
    }
  }

  // ── Step 4b: Verify/create Observatory alert notification policy ──────────
  // Cloudflare Notifications API: /accounts/{id}/alerting/v3/policies
  // Alert type "speed_insights" covers Observatory Core Web Vitals regression.
  //
  // Slack paging: set OBSERVATORY_ALERT_SLACK_WEBHOOK_ID to the ID of a
  // pre-registered Cloudflare notification webhook destination that points at
  // the Slack incoming-webhook URL.  Register it once at:
  //   dash.cloudflare.com → Notifications → Destinations → Webhooks → Add
  // then paste the returned ID into the env var.
  console.log('\n── Step 4b: Observatory alert notification policy ──');
  console.log('  Target: speed_insights alert → email admin@syrabit.ai + Slack webhook');
  console.log('  Thresholds: LCP>2.5 s, CLS>0.1, INP>200 ms');

  const ALERT_TYPE          = 'speed_insights';
  const ADMIN_EMAIL         = process.env.OBSERVATORY_ALERT_EMAIL         || 'admin@syrabit.ai';
  const SLACK_WEBHOOK_ID    = process.env.OBSERVATORY_ALERT_SLACK_WEBHOOK_ID || '';
  const policies            = await cfGet(`/accounts/${ACCOUNT_ID}/alerting/v3/policies`);

  if (!policies.success) {
    const code = policies.errors?.[0]?.code;
    if (code === 10000) {
      err('Alerting: token lacks Notifications: Edit scope — skipping alert policy setup');
      info('Add "Account Notifications: Edit" scope to the API token and re-run to auto-create the policy.');
    } else {
      err(`Alerting API error: ${JSON.stringify(policies.errors)}`);
    }
    info('Manual fallback: dash.cloudflare.com → Notifications → Add → "Speed Insights" → select Observatory thresholds.');
    return;
  }

  const existing = (policies.result || []).find(
    p => p.alert_type === ALERT_TYPE && p.name && p.name.toLowerCase().includes('observatory'),
  );

  if (existing) {
    ok(`Observatory alert policy already exists: "${existing.name}" (id=${existing.id})`);
    const emailMechs   = (existing.mechanisms?.email    || []).map(m => m.id);
    const webhookMechs = (existing.mechanisms?.webhooks || []).map(m => m.id);
    if (emailMechs.length) {
      ok(`  Email recipients: ${emailMechs.join(', ')}`);
    } else {
      info(`  No email recipient on existing policy — add ${ADMIN_EMAIL} via dashboard.`);
    }
    if (webhookMechs.length) {
      ok(`  Slack/webhook destinations: ${webhookMechs.join(', ')}`);
    }

    // If a Slack webhook ID is provided but not yet on the policy, update the
    // existing policy to add it rather than returning without making changes.
    // We preserve all existing mechanisms and conditions to stay idempotent.
    const slackMissing = SLACK_WEBHOOK_ID && !webhookMechs.includes(SLACK_WEBHOOK_ID);
    if (!slackMissing) {
      if (!SLACK_WEBHOOK_ID) {
        info('  OBSERVATORY_ALERT_SLACK_WEBHOOK_ID not set — Slack mechanism will not be added.');
        info('  Set the env var and re-run to page on-call via Slack.');
      }
      return;
    }

    info(`  Slack webhook id=${SLACK_WEBHOOK_ID} not yet on policy — updating now.`);
    if (DRY_RUN) {
      dry(`PUT /accounts/{id}/alerting/v3/policies/${existing.id} — add Slack webhook id=${SLACK_WEBHOOK_ID}`);
      return;
    }

    // Build merged mechanisms: preserve existing emails/webhooks, add new webhook ID.
    const mergedMechanisms = {
      ...existing.mechanisms,
      email:    [...(existing.mechanisms?.email    || [])],
      webhooks: [
        ...(existing.mechanisms?.webhooks || []),
        { id: SLACK_WEBHOOK_ID },
      ],
    };
    // Ensure the admin email is always present.
    if (!mergedMechanisms.email.find(m => m.id === ADMIN_EMAIL)) {
      mergedMechanisms.email.push({ id: ADMIN_EMAIL });
    }

    const updateBody = {
      name:        existing.name,
      description: existing.description,
      enabled:     existing.enabled,
      alert_type:  existing.alert_type,
      mechanisms:  mergedMechanisms,
      conditions:  existing.conditions,
      filters:     existing.filters,
    };

    const ur = await cfPut(`/accounts/${ACCOUNT_ID}/alerting/v3/policies/${existing.id}`, updateBody);
    if (ur.success) {
      ok(`Observatory alert policy updated: added Slack webhook id=${SLACK_WEBHOOK_ID}`);
    } else {
      err(`Observatory alert policy update failed: ${JSON.stringify(ur.errors)}`);
      info('Manual fix: dash.cloudflare.com → Notifications → (edit policy) → Destinations → Webhooks → Add.');
    }
    return;
  }

  if (DRY_RUN) {
    const slackNote = SLACK_WEBHOOK_ID ? ` + Slack webhook id=${SLACK_WEBHOOK_ID}` : ' (no OBSERVATORY_ALERT_SLACK_WEBHOOK_ID set — Slack skipped)';
    dry(`POST /accounts/{id}/alerting/v3/policies — speed_insights alert → ${ADMIN_EMAIL}${slackNote}`);
    return;
  }

  // Create the Observatory alert policy.
  // The Cloudflare Notifications API creates a policy; threshold values for
  // LCP/CLS/INP are configured via Observatory-specific "conditions" fields.
  //
  // mechanisms.webhooks references a pre-registered Cloudflare notification
  // webhook destination (Slack incoming-webhook URL registered at
  // dash.cloudflare.com → Notifications → Destinations → Webhooks).
  // When OBSERVATORY_ALERT_SLACK_WEBHOOK_ID is set, Cloudflare will POST the
  // speed_insights alert JSON to that Slack webhook so the on-call is paged
  // immediately instead of waiting for email.
  const mechanisms = {
    email: [{ id: ADMIN_EMAIL }],
  };
  if (SLACK_WEBHOOK_ID) {
    mechanisms.webhooks = [{ id: SLACK_WEBHOOK_ID }];
    info(`Slack webhook destination id=${SLACK_WEBHOOK_ID} will be added to the policy.`);
  } else {
    info('OBSERVATORY_ALERT_SLACK_WEBHOOK_ID not set — policy will use email only.');
    info('To page on-call via Slack: register a webhook destination at');
    info('  dash.cloudflare.com → Notifications → Destinations → Webhooks → Add');
    info('then re-run with OBSERVATORY_ALERT_SLACK_WEBHOOK_ID=<id>.');
  }

  const alertBody = {
    name:        'Observatory Core Web Vitals regression — syrabit.ai',
    description: 'Fires when weekly Lighthouse run detects LCP>2.5 s, CLS>0.1, or INP>200 ms',
    enabled:     true,
    alert_type:  ALERT_TYPE,
    mechanisms,
    // Observatory-specific conditions — thresholds interpreted by CF Observatory
    conditions: {
      lcp:  { operator: 'greater_than', value: 2500 },    // ms
      cls:  { operator: 'greater_than', value: 0.1  },
      inp:  { operator: 'greater_than', value: 200  },    // ms
    },
    // Scope to the syrabit.ai zone
    filters: {
      zones: [ZONE_ID],
    },
  };

  const ar = await cfPost(`/accounts/${ACCOUNT_ID}/alerting/v3/policies`, alertBody);
  if (ar.success) {
    ok(`Observatory alert policy created: id=${ar.result.id}`);
    ok(`  LCP>2.5 s, CLS>0.1, INP>200 ms → email ${ADMIN_EMAIL}`);
    if (SLACK_WEBHOOK_ID) {
      ok(`  Slack webhook destination: id=${SLACK_WEBHOOK_ID}`);
    }
  } else {
    err(`Alert policy creation failed: ${JSON.stringify(ar.errors)}`);
    info(`Manual setup required: dash.cloudflare.com → Notifications → Add → "Speed Insights".`);
    info(`Required thresholds: LCP>2.5 s (2500 ms), CLS>0.1, INP>200 ms. Recipient: ${ADMIN_EMAIL}.`);
    if (SLACK_WEBHOOK_ID) {
      info(`Also add Slack webhook destination id=${SLACK_WEBHOOK_ID} manually.`);
    }
  }
}

// ────────────────────────────────────────────────────────────────────────────
// Step 5: Post-apply summary
// ────────────────────────────────────────────────────────────────────────────
function printSummary(certId, fingerprint) {
  console.log('\n── Phase 6 Post-Apply Checklist ──');
  console.log('');
  if (certId) {
    console.log('  mTLS certificate provisioned. Complete the setup:');
    console.log(`  ① Store the private key:     echo "<pem>" | wrangler secret put MTLS_PRIVATE_KEY --name syrabit-edge`);
    console.log(`  ② Update wrangler.toml:      set certificate_id = "${certId}" in [[mtls_certificates]]`);
    console.log('  ③ Deploy the worker:         cd workers/edge-proxy && wrangler deploy');
    console.log('  ④ Configure Railway mTLS:    add the certificate to the Railway service (see docs/CLOUDFLARE_MTLS.md)');
    console.log('');
    if (fingerprint) {
      console.log('  ══════════════════════════════════════════════════════════════');
      console.log('  SHA-256 fingerprint (lowercase hex, no colons):');
      console.log(`    ${fingerprint}`);
      console.log('');
      console.log('  Store as Wrangler secret (used by MtlsClientCertMiddleware):');
      console.log(`    echo "${fingerprint}" | wrangler secret put MTLS_CERT_SHA256 --name syrabit-edge`);
      console.log('');
      console.log('  Store as Railway environment variable:');
      console.log(`    CF_MTLS_CERT_SHA256=${fingerprint}`);
      console.log('  ══════════════════════════════════════════════════════════════');
      console.log('');
    } else {
      console.log('  SHA-256 fingerprint: not available (cert PEM was not returned).');
      console.log('  Compute it manually from the saved cert PEM:');
      console.log('    openssl x509 -fingerprint -sha256 -noout -in cert.pem \\');
      console.log('      | sed "s/.*=//;s/://g" | tr A-F a-f');
      console.log('  Then run:');
      console.log('    echo "<fingerprint>" | wrangler secret put MTLS_CERT_SHA256 --name syrabit-edge');
      console.log('  And set Railway env var: CF_MTLS_CERT_SHA256=<fingerprint>');
      console.log('');
    }
  } else {
    console.log('  mTLS certificate was not issued (dry-run or error above).');
    console.log('  If not yet done, issue it manually at dash.cloudflare.com → SSL/TLS → Client Certificates.');
    console.log('');
  }
  console.log('  Zaraz + GA4:');
  console.log('  • Verify GA4 events at dash.cloudflare.com → Zaraz → Tools');
  console.log('  • Check realtime GA4 reports at analytics.google.com');
  console.log('  • Ensure VITE_GA4_ID is NOT set in the production build env');
  console.log('    (client-side gtag.js is disabled — Zaraz handles all tracking)');
  console.log('');
  console.log('  Observatory:');
  console.log('  • Step 4b above auto-creates the speed_insights notification policy');
  console.log('    (LCP > 2.5 s, CLS > 0.1, INP > 200 ms → email admin@syrabit.ai + Slack).');
  console.log('  • Slack paging: set OBSERVATORY_ALERT_SLACK_WEBHOOK_ID to the ID of a');
  console.log('    Cloudflare webhook destination (Slack incoming-webhook URL). Register at:');
  console.log('    dash.cloudflare.com → Notifications → Destinations → Webhooks → Add');
  console.log('    Then re-run this script with the ID set to add it to the policy.');
  console.log('    If Step 4b printed a manual-fallback link, add the policy at:');
  console.log('    dash.cloudflare.com → Notifications → Add → "Speed Insights".');
  console.log('    See docs/CLOUDFLARE_OBSERVATORY.md for the full on-call runbook.');
  console.log('');
  console.log('  Nightly smoke:');
  console.log('  • Phase 6 assertions are added to nightly-smoke.js and will run in the next CI cycle.');
  console.log('  • Run manually: CLOUDFLARE_API_TOKEN=<tok> node artifacts/syrabit/scripts/nightly-smoke.js');
}

// ────────────────────────────────────────────────────────────────────────────
// Main
// ────────────────────────────────────────────────────────────────────────────
async function main() {
  console.log('════════════════════════════════════════════════════════════════');
  console.log(' Cloudflare Phase 6 Apply — syrabit.ai');
  if (DRY_RUN) console.log(' *** DRY-RUN MODE — no mutating API calls will be made ***');
  console.log('════════════════════════════════════════════════════════════════');
  console.log(`Zone:    ${ZONE_ID}`);
  console.log(`Account: ${ACCOUNT_ID}`);
  console.log(`GA4 ID:  ${GA4_ID || '(not set — Zaraz step will be skipped)'}`);
  console.log('');

  const { id: certId, fingerprint } = await stepMtlsCert();
  await stepImageResizing();
  await stepZaraz();
  await stepObservatory();
  printSummary(certId, fingerprint);
}

main().catch((err) => {
  console.error('Phase 6 apply error:', err.message);
  process.exit(1);
});
