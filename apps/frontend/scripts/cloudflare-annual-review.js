#!/usr/bin/env node
/**
 * cloudflare-annual-review.js
 *
 * Read-only audit of syrabit.ai Cloudflare zone configuration.
 * Run before each annual review to surface gaps against the target state
 * established in Phase 1 (Task #105) and updated through Phase 6 (Task #110).
 *
 * Required env:
 *   CLOUDFLARE_API_TOKEN   — Zone Settings: Read, DNS: Read, Bot Management: Read,
 *                            Logs: Read (Phase 2 Logpush), Health Checks: Read,
 *                            R2: Read (Phase 2 + 4 buckets), Zero Trust: Read (Phase 3),
 *                            Waiting Room: Read (Phase 3), Cache: Read (Phase 4),
 *                            Workers: Read (Phase 5),
 *                            SSL and Certificates: Read, Zaraz: Read,
 *                            Speed (Observatory): Read (Phase 6)
 *   CLOUDFLARE_ZONE_ID     — optional, defaults to syrabit.ai zone
 *   CLOUDFLARE_ACCOUNT_ID  — optional, defaults to Syrabit account
 *
 * Usage:
 *   node apps/frontend/scripts/cloudflare-annual-review.js
 *   CLOUDFLARE_API_TOKEN=<tok> node apps/frontend/scripts/cloudflare-annual-review.js
 */

import {
  EXPECTED_PRODUCTION_BINDINGS,
  PRODUCTION_SERVICES,
} from './cloudflare-production-contract.mjs';

const TOKEN      = process.env.CLOUDFLARE_API_TOKEN;
const ZONE_ID    = process.env.CLOUDFLARE_ZONE_ID    || '5b8c97df4431491dc7f60ea72fb61871';
const ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID || 'd66e40eac539fff1db270fddf384a5ec';
const API        = 'https://api.cloudflare.com/client/v4';
const SITE_URL   = (process.env.SITE_URL || 'https://syrabit.ai').replace(/\/+$/, '');
const IMAGE_URL  = process.env.CF_AUDIT_IMAGE_URL || `${SITE_URL}/opengraph.jpg`;

if (!TOKEN) {
  console.error('CLOUDFLARE_API_TOKEN is not set');
  process.exit(1);
}

const headers = { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Fetch a CF API path; retries on rate-limit (10429) with exponential backoff
// (2 s → 4 s → 8 s) so a single annual-review run doesn't get throttled.
async function cfGet(path, { _attempt = 0 } = {}) {
  const res = await fetch(`${API}${path}`, { headers });
  const j   = await res.json();
  if (!j.success && j.errors?.[0]?.code === 10429 && _attempt < 3) {
    const wait = 2000 * (2 ** _attempt);
    console.warn(`[rate-limit] 10429 on ${path} — waiting ${wait}ms (attempt ${_attempt + 1}/3)`);
    await sleep(wait);
    return cfGet(path, { _attempt: _attempt + 1 });
  }
  return j;
}

function row(label, value, target, note = '') {
  const ok   = target !== undefined ? JSON.stringify(value) === JSON.stringify(target) : null;
  const mark = ok === null ? '  ' : ok ? '✓ ' : '✗ ';
  const exp  = target !== undefined && !ok ? `  (want: ${JSON.stringify(target)})` : '';
  const n    = note ? `  [${note}]` : '';
  console.log(`  ${mark}${label.padEnd(40)} ${JSON.stringify(value)}${exp}${n}`);
}

function warning(label, detail) {
  console.log(`  ⚠  ${label}  [${detail}]`);
}

function planOnly(label, detail) {
  console.log(`  💳 ${label}  [${detail}]`);
}

async function main() {
  console.log('════════════════════════════════════════');
  console.log(' Cloudflare Annual Review — syrabit.ai');
  console.log(`════════════════════════════════════════\n`);
  console.log(`Zone:    ${ZONE_ID}`);
  console.log(`Account: ${ACCOUNT_ID}\n`);

  // ── Phase 1: Zone settings ────────────────────────────────────────────
  console.log('── Phase 1: Zone Settings ──');
  const settingTargets = {
    sort_query_string_for_cache: 'on',
    true_client_ip_header:       'on',
    ech:                         'on',
    // minify: CF Enterprise API accepts PATCH but does not apply — use dashboard
    minify:                      { css: 'on', html: 'on', js: 'on' },
    http3:                       'on',
    brotli:                      'on',
    http2:                       'on',
    always_use_https:            'on',
    min_tls_version:             '1.2',
    // tls_1_3: "zrt" = TLS 1.3 + 0-RTT (correct; "on" is the legacy label)
    tls_1_3:                     'zrt',
    automatic_https_rewrites:    'on',
    // ssl: "strict" is better than "full" — validates origin cert
    ssl:                         'strict',
    // hsts: not a standalone zone settings endpoint — inspect via SSL tab
    security_level:              null,   // inspect only
  };

  for (const [setting, target] of Object.entries(settingTargets)) {
    const j = await cfGet(`/zones/${ZONE_ID}/settings/${setting}`);
    if (!j.success) {
      console.log(`  ?  ${setting.padEnd(40)} error: ${JSON.stringify(j.errors)}`);
    } else {
      const note = setting === 'minify' ? 'Enterprise API non-functional; use dashboard' : '';
      row(setting, j.result.value, target || undefined, note);
    }
  }

  // ── Phase 1: Bot Management ───────────────────────────────────────────
  console.log('\n── Phase 1: Bot Management ──');
  const bm = await cfGet(`/zones/${ZONE_ID}/bot_management`);
  if (bm.success) {
    row('sbfm_likely_automated',        bm.result.sbfm_likely_automated,        'managed_challenge');
    row('sbfm_definitely_automated',    bm.result.sbfm_definitely_automated,    'managed_challenge');
    row('sbfm_verified_bots',           bm.result.sbfm_verified_bots,           'allow');
    row('content_bots_protection',      bm.result.content_bots_protection,      'block');
    row('ai_bots_protection',           bm.result.ai_bots_protection,           'block');
    row('crawler_protection',           bm.result.crawler_protection,           'enabled');
    row('enable_js',                    bm.result.enable_js,                    true);
    row('using_latest_model',           bm.result.using_latest_model,           true);
    // sbfm_static_resource_protection: false = only check page requests (not static assets)
    row('sbfm_static_resource_protection', bm.result.sbfm_static_resource_protection, false);
    if (bm.result.stale_zone_configuration?.fight_mode) {
      console.log(`  ✗  stale_zone_configuration.fight_mode     true  (needs CF support ticket to clear)`);
    }
  } else {
    console.log('  ?  Bot Management read error:', JSON.stringify(bm.errors));
  }

  // ── Phase 1: DMARC ────────────────────────────────────────────────────
  console.log('\n── Phase 1: DNS & Email Security ──');
  const dmarc = await cfGet(`/zones/${ZONE_ID}/dns_records?name=_dmarc.syrabit.ai&type=TXT`);
  if (dmarc.success && dmarc.result.length) {
    const content = dmarc.result[0].content;
    const policy  = (content.match(/p=([^;]+)/) || ['', 'MISSING'])[1].trim();
    row('DMARC p= policy (_dmarc.syrabit.ai)', policy, 'quarantine');
    console.log(`    full record: ${content}`);
  } else {
    console.log('  ✗  _dmarc.syrabit.ai TXT record: NOT FOUND');
  }

  // ── Phase 2: R2 Logs Bucket ──────────────────────────────────────────
  console.log('\n── Phase 2: R2 Logs Bucket (Task #106) ──');
  const r2 = await cfGet(`/accounts/${ACCOUNT_ID}/r2/buckets`);
  if (!r2.success) {
    const authErr = r2.errors?.[0]?.code === 10000;
    console.log(`  ?  R2 bucket syrabit-logs${authErr ? '  [token lacks R2: Read]' : ': ' + JSON.stringify(r2.errors)}`);
  } else {
    const exists = (r2.result?.buckets || []).some(b => b.name === 'syrabit-logs');
    row('R2 bucket syrabit-logs exists', exists, true,
      exists ? 'Logpush destination' : 'run cloudflare-phase2-apply.js');
  }

  // ── Phase 2: Logpush jobs ─────────────────────────────────────────────
  console.log('\n── Phase 2: Logpush Jobs (Task #106) ──');
  console.log('  Target: 2 jobs (syrabit-http-requests, syrabit-firewall-events) → R2, enabled');
  const lp = await cfGet(`/zones/${ZONE_ID}/logpush/jobs`);
  if (!lp.success) {
    const authErr = lp.errors?.[0]?.code === 10000;
    console.log(`  ?  Logpush jobs${authErr
      ? '  [token lacks Logs: Read — add scope at dash.cloudflare.com/profile/api-tokens]'
      : ': ' + JSON.stringify(lp.errors)}`);
  } else {
    const httpJob  = lp.result.find(j => j.name === 'syrabit-http-requests');
    const fwJob    = lp.result.find(j => j.name === 'syrabit-firewall-events');
    if (httpJob) {
      row('syrabit-http-requests enabled', httpJob.enabled, true,
        `id=${httpJob.id} dataset=${httpJob.dataset}`);
    } else {
      row('syrabit-http-requests', 'NOT FOUND', 'EXISTS', 'run cloudflare-phase2-apply.js');
    }
    if (fwJob) {
      row('syrabit-firewall-events enabled', fwJob.enabled, true,
        `id=${fwJob.id} dataset=${fwJob.dataset}`);
    } else {
      row('syrabit-firewall-events', 'NOT FOUND', 'EXISTS', 'run cloudflare-phase2-apply.js');
    }
    if (lp.result.length > 2) {
      console.log(`  ℹ  ${lp.result.length - 2} additional job(s): ${lp.result.filter(j=>j.name!=='syrabit-http-requests'&&j.name!=='syrabit-firewall-events').map(j=>j.name).join(', ')}`);
    }
  }

  // ── Phase 2: Origin Healthcheck ───────────────────────────────────────
  console.log('\n── Phase 2: Origin Healthcheck (Task #106) ──');
  console.log('  Target: api-syrabit-ai-origin polls https://api.syrabit.ai/health every 60 s');
  const hc = await cfGet(`/zones/${ZONE_ID}/healthchecks`);
  if (!hc.success) {
    const authErr = hc.errors?.[0]?.code === 10000;
    console.log(`  ?  Healthcheck${authErr
      ? '  [token lacks Health Checks: Read — add scope at dash.cloudflare.com/profile/api-tokens]'
      : ': ' + JSON.stringify(hc.errors)}`);
  } else {
    const hcRecord = hc.result.find(h => h.name === 'api-syrabit-ai-origin');
    if (hcRecord) {
      row('api-syrabit-ai-origin exists', true, true,
        `id=${hcRecord.id} interval=${hcRecord.interval}s status=${hcRecord.status}`);
      row('  type', hcRecord.type, 'HTTPS');
      row('  path', hcRecord.path, '/health');
      row('  interval', hcRecord.interval, 60);
    } else {
      row('api-syrabit-ai-origin', 'NOT FOUND', 'EXISTS', 'run cloudflare-phase2-apply.js');
    }
  }

  // ── Phase 3: Zero Trust Access ────────────────────────────────────────
  console.log('\n── Phase 3: Zero Trust Access (Task #107) ──');
  console.log('  Target: Syrabit Admin app covers api.syrabit.ai/admin* (wildcard), session=8h');
  const zt = await cfGet(`/accounts/${ACCOUNT_ID}/access/apps`);
  if (!zt.success) {
    const authErr = zt.errors?.[0]?.code === 10000;
    console.log(`  ?  Access apps${authErr
      ? '  [token lacks Zero Trust: Read — add scope at dash.cloudflare.com/profile/api-tokens]'
      : ': ' + JSON.stringify(zt.errors)}`);
  } else {
    const adminApp = zt.result.find(a => a.name === 'Syrabit Admin');
    if (adminApp) {
      const hasWildcard = adminApp.domain && adminApp.domain.includes('admin*');
      row('Syrabit Admin app exists', true, true,
        `id=${adminApp.id} domain=${adminApp.domain}`);
      row('  domain covers admin/* (wildcard)', hasWildcard, true,
        hasWildcard ? '' : 'SECURITY: update domain to api.syrabit.ai/admin* to cover nested routes');
      row('  session_duration', adminApp.session_duration, '8h');
      // Check policy count
      const pol = await cfGet(`/accounts/${ACCOUNT_ID}/access/apps/${adminApp.id}/policies`);
      if (pol.success) {
        row('  policies', pol.result.length >= 1, true,
          `${pol.result.length} policy(ies): ${pol.result.map(p=>p.name).join(', ')}`);
      } else {
        console.log('  ?  Policy read error:', JSON.stringify(pol.errors));
      }
    } else {
      row('Syrabit Admin app', 'NOT FOUND', 'EXISTS', 'run cloudflare-phase3-apply.js');
    }
  }

  // ── Phase 3: Waiting Room ─────────────────────────────────────────────
  console.log('\n── Phase 3: Waiting Room (Task #107) ──');
  console.log('  Target: syrabit-exam-season-queue on syrabit.ai/*, 10-min session, enabled');
  const wr = await cfGet(`/zones/${ZONE_ID}/waiting_rooms`);
  if (!wr.success) {
    const authErr = wr.errors?.[0]?.code === 10000;
    console.log(`  ?  Waiting rooms${authErr
      ? '  [token lacks Waiting Room: Read — add scope at dash.cloudflare.com/profile/api-tokens]'
      : ': ' + JSON.stringify(wr.errors)}`);
  } else {
    const room = wr.result.find(r => r.name === 'syrabit-exam-season-queue');
    if (room) {
      row('syrabit-exam-season-queue exists', true, true,
        `id=${room.id} host=${room.host}`);
      row('  enabled', room.enabled, true);
      row('  session_duration', room.session_duration, 10);
      row('  new_users_per_minute', room.new_users_per_minute, undefined,
        'enable only when production origin capacity is ready');
      row('  total_active_users', room.total_active_users, undefined);
    } else {
      row('syrabit-exam-season-queue', 'NOT FOUND', 'EXISTS', 'run cloudflare-phase3-apply.js');
    }
  }

  // ── Phase 4: R2 Asset Storage + Cache Reserve (Task #108) ────────────────
  console.log('\n── Phase 4: R2 Asset Storage + Cache Reserve (Task #108) ──');
  console.log('  Targets:');
  console.log('    syrabit-assets      — student PDFs served at assets.syrabit.ai');
  console.log('    syrabit-cache-reserve — Cache Reserve backing bucket');
  console.log('    Cache Reserve: on   — cold-cache misses resolve from R2');

  // Re-fetch R2 buckets (same endpoint used in Phase 2 check above, but re-call
  // so Phase 4 stands alone when cross-referenced in future reviews).
  const r2p4 = await cfGet(`/accounts/${ACCOUNT_ID}/r2/buckets`);
  if (!r2p4.success) {
    const authErr = r2p4.errors?.[0]?.code === 10000;
    console.log(`  ?  R2 buckets${authErr
      ? '  [token lacks R2: Read — add scope at dash.cloudflare.com/profile/api-tokens]'
      : ': ' + JSON.stringify(r2p4.errors)}`);
  } else {
    const buckets = r2p4.result?.buckets || [];

    const assets = buckets.find(b => b.name === 'syrabit-assets');
    if (assets) {
      row('syrabit-assets exists', true, true,
        `location=${assets.location || 'auto'} created=${assets.creation_date || 'N/A'}`);
      // Check custom domain
      const domainRes = await cfGet(`/accounts/${ACCOUNT_ID}/r2/buckets/syrabit-assets/domains/custom`);
      if (domainRes.success) {
        const domain = (domainRes.result?.domains || []).find(d => d.domain === 'assets.syrabit.ai');
        if (domain) {
          row('  assets.syrabit.ai custom domain', domain.enabled, true,
            `status=${domain.status || 'unknown'}`);
        } else {
          row('  assets.syrabit.ai custom domain', 'NOT FOUND', 'EXISTS',
            'run cloudflare-phase4-apply.js → Step 2');
        }
      } else {
        const authErr = domainRes.errors?.[0]?.code === 10000;
        console.log(`  ?  assets.syrabit.ai domain${authErr ? '  [token lacks R2: Read]' : ': ' + JSON.stringify(domainRes.errors)}`);
      }
    } else {
      row('syrabit-assets', 'NOT FOUND', 'EXISTS', 'run cloudflare-phase4-apply.js → Step 1');
    }

    const cacheReserveBucket = buckets.find(b => b.name === 'syrabit-cache-reserve');
    if (cacheReserveBucket) {
      row('syrabit-cache-reserve exists', true, true);
    } else {
      row('syrabit-cache-reserve', 'NOT FOUND', 'EXISTS', 'run cloudflare-phase4-apply.js → Step 3');
    }
  }

  // Cache Reserve zone setting
  const cr = await cfGet(`/zones/${ZONE_ID}/cache/cache_reserve`);
  if (!cr.success) {
    const code = cr.errors?.[0]?.code;
    if (code === 10000) {
      console.log('  ?  Cache Reserve  [token lacks Cache: Read — add scope at dash.cloudflare.com/profile/api-tokens]');
    } else if (code === 1135) {
      planOnly('Cache Reserve',
        'not available on current plan — requires a Cache Reserve subscription');
    } else {
      row('Cache Reserve API', JSON.stringify(cr.errors), 'success');
    }
  } else {
    const value = cr.result?.value;
    if (value === 'on') {
      row('Cache Reserve (zone setting)', value, 'on');
    } else {
      planOnly('Cache Reserve',
        `value=${JSON.stringify(value)}; requires a Cache Reserve subscription`);
    }
  }

  // ── Phase 5: Active production Worker bindings ─────────────────────────────
  console.log('\n── Phase 5: Active Production Worker Bindings ──');
  console.log('  Targets:');
  console.log(`    ${PRODUCTION_SERVICES.edge}: RATE_LIMIT_DO, API_WORKER, KV, R2, Workers AI`);
  console.log(`    ${PRODUCTION_SERVICES.api}: D1, Vectorize, KV, R2, Workers AI`);

  for (const { service, bindings: expectedBindings } of EXPECTED_PRODUCTION_BINDINGS) {
    const path = `/accounts/${ACCOUNT_ID}/workers/scripts/${service}/bindings`;
    const bindingRes = await cfGet(path);
    if (!bindingRes.success) {
      const code = bindingRes.errors?.[0]?.code;
      if ([10000, 9109, 7003].includes(code)) {
        warning(`${service} bindings`,
          'token lacks Workers: Read — add scope at dash.cloudflare.com/profile/api-tokens');
      } else if (code === 10429) {
        warning(`${service} bindings`, 'Cloudflare API rate-limited — re-run the review later');
      } else {
        row(`${service} bindings API`, JSON.stringify(bindingRes.errors), 'success',
          'verify the active production service and Cloudflare API availability');
      }
      continue;
    }

    const deployed = bindingRes.result || [];
    for (const [name, type, serviceTarget] of expectedBindings) {
      const found = deployed.find((binding) => binding.name === name);
      const typeMatches = found?.type === type;
      const targetMatches = !serviceTarget || found?.service === serviceTarget;
      const actual = found
        ? `${found.type}${found.service ? ` → ${found.service}` : ''}`
        : 'missing';
      const expected = `${type}${serviceTarget ? ` → ${serviceTarget}` : ''}`;
      row(`${service}.${name}`, typeMatches && targetMatches, true,
        `${actual}; expected ${expected}`);
    }
  }

  // ── Phase 6: Zaraz, Image Resizing, Observatory ────────────────────────
  console.log('\n── Phase 6: Zaraz GA4, Image Resizing, Observatory (Task #110) ──');
  console.log('  Targets:');
  console.log('    image_resizing: on              — CF Image Resizing enabled for /cdn-cgi/image/');
  console.log('    Zaraz GA4 tool — enabled, server-side event forwarding');
  console.log('    Observatory — weekly Lighthouse for homepage + chapter page');

  // 6b: Image Resizing — the live edge response is authoritative. Cloudflare's
  // zone setting can report ambiguous values even while transforms are active.
  const imageSettingPath = `/zones/${ZONE_ID}/settings/image_resizing`;
  const imgRes = await cfGet(imageSettingPath);
  const imageSettingCode = imgRes.errors?.[0]?.code;
  const imageSettingState = imgRes.success
    ? (imgRes.result?.value || 'unknown')
    : imageSettingCode === 1135
      ? 'plan-gated'
      : [10000, 9109, 7003].includes(imageSettingCode)
        ? 'scope-gap'
        : imageSettingCode === 10429
          ? 'rate-limited'
          : 'api-error';
  const transformUrl =
    `${SITE_URL}/cdn-cgi/image/width=1,quality=1,format=webp/${encodeURI(IMAGE_URL)}`;
  try {
    const imageResponse = await fetch(transformUrl, {
      signal: AbortSignal.timeout(10_000),
      headers: { Accept: 'image/avif,image/webp,image/*,*/*;q=0.8' },
    });
    const cfResized = imageResponse.headers.get('cf-resized') || '';
    const transformed = imageResponse.ok &&
      (cfResized.startsWith('internal=ok') || cfResized.startsWith('internal=ram'));

    if (transformed) {
      row('Image Resizing live probe', true, true,
        `cf-resized=${cfResized}; zone setting=${imageSettingState}`);
    } else if (imageSettingState === 'scope-gap') {
      warning('Image Resizing live probe',
        `HTTP ${imageResponse.status}, cf-resized missing; token lacks Zone Settings: Read`);
    } else if (imageSettingState === 'rate-limited') {
      warning('Image Resizing live probe',
        `HTTP ${imageResponse.status}, cf-resized missing; Cloudflare API rate-limited`);
    } else if (imageSettingState === 'plan-gated') {
      planOnly('Image Resizing live probe',
        `HTTP ${imageResponse.status}, cf-resized missing; add-on not available on current plan`);
    } else {
      row('Image Resizing live probe', false, true,
        `HTTP ${imageResponse.status}, cf-resized=${JSON.stringify(cfResized || 'missing')}`);
    }
  } catch (error) {
    if (imageSettingState === 'scope-gap' || imageSettingState === 'rate-limited') {
      warning('Image Resizing live probe',
        `probe failed (${error.message}); zone setting=${imageSettingState}`);
    } else if (imageSettingState === 'plan-gated') {
      planOnly('Image Resizing live probe',
        `probe failed (${error.message}); add-on not available on current plan`);
    } else {
      row('Image Resizing live probe', `probe failed: ${error.message}`, 'live cf-resized response');
    }
  }

  // 6c: Zaraz GA4 tool
  const zaraz = await cfGet(`/zones/${ZONE_ID}/zaraz/config`);
  if (!zaraz.success) {
    const authErr = zaraz.errors?.[0]?.code === 10000;
    console.log(`  ?  Zaraz config${authErr
      ? '  [token lacks Zaraz: Read — add scope]'
      : ': ' + JSON.stringify(zaraz.errors)}`);
  } else {
    const tools   = zaraz.result?.tools || {};
    const ga4Tool = Object.values(tools).find(
      t => t.type === 'GA4' || (t.name && t.name.toLowerCase().includes('ga4')),
    );
    if (ga4Tool) {
      row('Zaraz GA4 tool exists', true, true,
        `name="${ga4Tool.name}" enabled=${ga4Tool.enabled}`);
      row('  Zaraz GA4 enabled', ga4Tool.enabled, true);
    } else {
      row('Zaraz GA4 tool', 'NOT FOUND', 'EXISTS', 'run cloudflare-phase6-apply.js → Step 3');
    }
  }

  // 6d-alert: Observatory alert policy (speed_insights)
  const alertsRes = await cfGet(`/accounts/${ACCOUNT_ID}/alerting/v3/policies`);
  if (!alertsRes.success) {
    const code = alertsRes.errors?.[0]?.code;
    if (code === 10000) {
      console.log('  ?  Observatory alert policy  [token lacks Account Notifications: Read]');
    } else {
      console.log(`  ?  Observatory alert policy: ${JSON.stringify(alertsRes.errors)}`);
    }
  } else {
    const speedAlert = (alertsRes.result || []).find(p => p.alert_type === 'speed_insights');
    if (speedAlert) {
      const emailCount = (speedAlert.mechanisms?.email || []).length;
      row('Observatory speed_insights alert policy', true, true,
        `id=${speedAlert.id} enabled=${speedAlert.enabled} email_recipients=${emailCount}`);
      row('  alert policy enabled', speedAlert.enabled, true);
      if (!emailCount) {
        console.log('  ✗  WARN: alert policy has no email recipient — add admin@syrabit.ai');
      }
    } else {
      row('Observatory speed_insights alert policy', 'NOT FOUND', 'EXISTS',
        'run cloudflare-phase6-apply.js → Step 4b');
    }
  }

  // 6d: Observatory scheduled runs — homepage + representative chapter page
  const obsTargets = [
    { label: 'Observatory homepage schedule',     url: 'https://syrabit.ai/' },
    { label: 'Observatory chapter page schedule', url: 'https://syrabit.ai/ahsec/class-12/physics' },
  ];
  for (const { label, url } of obsTargets) {
    const obsRes = await cfGet(
      `/zones/${ZONE_ID}/speed/schedule?url=${encodeURIComponent(url)}`,
    );
    if (!obsRes.success) {
      const code = obsRes.errors?.[0]?.code;
      if (code === 10000) {
        console.log(`  ?  ${label}  [token lacks Speed: Read]`);
        break;  // same scope issue for all targets
      } else if (code === 1135) {
        planOnly(label, 'not available on current plan — requires Observatory access');
        break;
      } else {
        console.log(`  ?  ${label}: ${JSON.stringify(obsRes.errors)}`);
      }
    } else if (obsRes.result?.schedule) {
      row(label, true, true, `frequency=${obsRes.result.schedule.frequency || 'unknown'}`);
    } else {
      row(label, 'NOT FOUND', 'EXISTS', 'run cloudflare-phase6-apply.js → Step 4');
    }
  }

  console.log('\n────────────────────────────────────────');
  console.log('Review complete.');
}

main().catch((err) => {
  console.error('Review error:', err.message);
  process.exit(1);
});
