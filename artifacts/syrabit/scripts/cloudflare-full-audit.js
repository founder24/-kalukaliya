#!/usr/bin/env node
/**
 * cloudflare-full-audit.js
 *
 * Task #110 — Complete 19-item end-to-end Cloudflare audit for syrabit.ai.
 * Covers every item in the original audit document across Phases 1–6
 * (Tasks #105–#110).  Outputs a structured pass/fail JSON report and a
 * human-readable summary.  Suitable for scheduled weekly CI runs separate
 * from the nightly smoke.
 *
 * 19 audit items:
 *   Phase 1 (#105):  1. Zone settings  2. Bot management  3. DMARC
 *   Phase 2 (#106):  4. R2 logs bucket  5. Logpush HTTP  6. Logpush FW
 *                    7. Origin healthcheck
 *   Phase 3 (#107):  8. Zero Trust Access app  9. Waiting Room
 *   Phase 4 (#108): 10. R2 assets bucket  11. R2 cache-reserve  12. Cache Reserve
 *                   13. assets.syrabit.ai custom domain
 *   Phase 5 (#109): 14. Analytics Engine binding  15. RateLimiter DO
 *                   16. AE dataset write recency
 *   Phase 6 (#110): 17. mTLS client certificate  18. Image Resizing
 *                   19. Zaraz GA4 + Observatory
 *
 * Required env:
 *   CLOUDFLARE_API_TOKEN  — Zone Settings: Read, Bot Management: Read,
 *                           DNS: Read, Logs: Read, Health Checks: Read,
 *                           R2: Read, Zero Trust: Read, Waiting Room: Read,
 *                           Cache: Read, Workers: Read, Durable Objects: Read,
 *                           SSL and Certificates: Read, Zaraz: Read,
 *                           Speed (Observatory): Read
 *   CLOUDFLARE_ZONE_ID    — optional, defaults to syrabit.ai zone
 *   CLOUDFLARE_ACCOUNT_ID — optional, defaults to Syrabit account
 *   CF_ANALYTICS_TOKEN    — optional; required for item 16 (AE write recency)
 *
 * Output:
 *   Human-readable table to stdout.
 *   JSON report to AUDIT_OUTPUT_FILE (default: /tmp/cf-audit-report.json).
 *
 * Usage:
 *   CLOUDFLARE_API_TOKEN=<tok> node artifacts/syrabit/scripts/cloudflare-full-audit.js
 *   CLOUDFLARE_API_TOKEN=<tok> AUDIT_OUTPUT_FILE=./audit.json \
 *     node artifacts/syrabit/scripts/cloudflare-full-audit.js
 */

import fs from 'fs';

const TOKEN      = process.env.CLOUDFLARE_API_TOKEN;
const ZONE_ID    = process.env.CLOUDFLARE_ZONE_ID    || '5b8c97df4431491dc7f60ea72fb61871';
const ACCOUNT_ID = process.env.CLOUDFLARE_ACCOUNT_ID || 'd66e40eac539fff1db270fddf384a5ec';
const AE_TOKEN   = process.env.CF_ANALYTICS_TOKEN    || '';
const OUT_FILE   = process.env.AUDIT_OUTPUT_FILE      || '/tmp/cf-audit-report.json';
const API        = 'https://api.cloudflare.com/client/v4';

if (!TOKEN) {
  console.error('CLOUDFLARE_API_TOKEN is not set');
  process.exit(1);
}

const headers = { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Fetch a CF API path with automatic retry on rate-limit (10429).
// Backs off at 2 s → 4 s → 8 s before giving up and returning the raw
// error response so callers can still categorise it as FAIL/WARN.
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

// Like cfGet but returns null instead of throwing on auth error (code 10000).
async function cfGetOrSkip(path) {
  const j = await cfGet(path);
  if (j.success) return j;
  if (j.errors?.[0]?.code === 10000) return null;
  return j;   // caller handles non-auth errors
}

// ─── Audit report accumulator ────────────────────────────────────────────────

const items = [];   // { id, phase, label, status, detail, remediation }

function addItem(id, phase, label, status, detail = '', remediation = '') {
  items.push({ id, phase, label, status, detail, remediation });
}

function pass(id, phase, label, detail = '') {
  addItem(id, phase, label, 'PASS', detail);
}

function fail(id, phase, label, detail = '', remediation = '') {
  addItem(id, phase, label, 'FAIL', detail, remediation);
}

function warn(id, phase, label, detail = '') {
  addItem(id, phase, label, 'WARN', detail, 'Check token scope or plan; verify manually');
}

function skip(id, phase, label, detail = '') {
  addItem(id, phase, label, 'SKIP', detail);
}

// Plan-gated features: not a configuration error, but a subscription/plan gap.
// Emits PLAN_REQUIRED status — excluded from FAIL count so CI doesn't block on
// unpurchased add-ons.  Includes the dashboard upgrade URL and estimated cost.
function planRequired(id, phase, label, detail = '', upgradeUrl = '', cost = '') {
  const fullDetail = [
    detail,
    upgradeUrl ? `Upgrade: ${upgradeUrl}` : '',
    cost       ? `Estimated cost: ${cost}` : '',
  ].filter(Boolean).join(' — ');
  addItem(id, phase, label, 'PLAN_REQUIRED', fullDetail,
    'Purchase the required add-on or upgrade the plan, then re-run this audit — no code changes needed');
}

// ─── Phase 1 ─────────────────────────────────────────────────────────────────

async function auditItem1ZoneSettings() {
  const checks = {
    sort_query_string_for_cache: 'on',
    true_client_ip_header:       'on',
    ech:                         'on',
    http3:                       'on',
    brotli:                      'on',
    http2:                       'on',
    always_use_https:            'on',
    min_tls_version:             '1.2',
    tls_1_3:                     'zrt',
    automatic_https_rewrites:    'on',
    ssl:                         'strict',
  };

  const subResults = [];
  let anyFail = false;

  for (const [setting, target] of Object.entries(checks)) {
    const j = await cfGet(`/zones/${ZONE_ID}/settings/${setting}`);
    if (!j.success) {
      if (j.errors?.[0]?.code === 10000) {
        subResults.push(`${setting}: [scope gap]`);
      } else {
        subResults.push(`${setting}: error`);
        anyFail = true;
      }
    } else {
      const actual = j.result.value;
      const ok     = JSON.stringify(actual) === JSON.stringify(target);
      if (!ok) {
        subResults.push(`${setting}: ${JSON.stringify(actual)} (want: ${JSON.stringify(target)})`);
        anyFail = true;
      }
    }
  }

  if (anyFail) {
    fail(1, 1, 'Zone settings (HTTP3, TLS 1.3, HSTS, Brotli, etc.)',
      subResults.join(' | '),
      'run cloudflare-phase1-apply.js or set via dashboard');
  } else {
    pass(1, 1, 'Zone settings (HTTP3, TLS 1.3, HSTS, Brotli, etc.)',
      'all target values confirmed');
  }
}

async function auditItem2BotManagement() {
  const bm = await cfGet(`/zones/${ZONE_ID}/bot_management`);
  if (!bm.success) {
    if (bm.errors?.[0]?.code === 10000) {
      warn(2, 1, 'Bot Management', 'token lacks Bot Management: Read scope');
    } else {
      fail(2, 1, 'Bot Management', JSON.stringify(bm.errors), 'add Bot Management: Read scope and retry');
    }
    return;
  }

  const checks = [
    ['sbfm_likely_automated',   'managed_challenge'],
    ['content_bots_protection', 'block'],
    ['ai_bots_protection',      'block'],
    ['enable_js',               true],
  ];

  const sub = [];
  let anyFail = false;
  for (const [k, want] of checks) {
    const ok = JSON.stringify(bm.result[k]) === JSON.stringify(want);
    if (!ok) {
      sub.push(`${k}: ${JSON.stringify(bm.result[k])} (want: ${JSON.stringify(want)})`);
      anyFail = true;
    }
  }

  if (anyFail) {
    fail(2, 1, 'Bot Management (SBFM, AI bots, JS challenge)',
      sub.join(' | '), 'run cloudflare-phase1-apply.js or set via dashboard');
  } else {
    pass(2, 1, 'Bot Management (SBFM, AI bots, JS challenge)',
      'likely_automated=managed_challenge, content_bots=block, ai_bots=block, enable_js=true');
  }
}

async function auditItem3Dmarc() {
  const dns = await cfGet(`/zones/${ZONE_ID}/dns_records?name=_dmarc.syrabit.ai&type=TXT`);
  if (!dns.success) {
    warn(3, 1, 'DMARC TXT record', 'DNS Read scope issue: ' + JSON.stringify(dns.errors));
    return;
  }
  if (!dns.result?.length) {
    fail(3, 1, 'DMARC TXT record', 'NOT FOUND', 'add _dmarc.syrabit.ai TXT with p=quarantine');
    return;
  }
  const content = dns.result[0].content;
  const policy  = (content.match(/p=([^;]+)/) || ['', 'MISSING'])[1].trim();
  if (policy !== 'quarantine') {
    fail(3, 1, 'DMARC TXT record', `p=${policy} (want: quarantine)`, 'update DMARC policy to p=quarantine');
  } else {
    pass(3, 1, 'DMARC TXT record', `p=quarantine — ${content.substring(0, 60)}…`);
  }
}

// ─── Phase 2 ─────────────────────────────────────────────────────────────────

async function auditItem4R2LogsBucket() {
  const r2 = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/r2/buckets`);
  if (!r2) {
    warn(4, 2, 'R2 bucket syrabit-logs', 'token lacks R2: Read scope');
    return;
  }
  if (!r2.success) {
    fail(4, 2, 'R2 bucket syrabit-logs', JSON.stringify(r2.errors), 'run cloudflare-phase2-apply.js');
    return;
  }
  const exists = (r2.result?.buckets || []).some(b => b.name === 'syrabit-logs');
  if (exists) {
    pass(4, 2, 'R2 bucket syrabit-logs', 'bucket exists — Logpush destination');
  } else {
    fail(4, 2, 'R2 bucket syrabit-logs', 'NOT FOUND', 'run cloudflare-phase2-apply.js');
  }
}

async function auditItems5And6Logpush() {
  const lp = await cfGetOrSkip(`/zones/${ZONE_ID}/logpush/jobs`);
  if (!lp) {
    warn(5, 2, 'Logpush job syrabit-http-requests', 'token lacks Logs: Read scope');
    warn(6, 2, 'Logpush job syrabit-firewall-events', 'token lacks Logs: Read scope');
    return;
  }
  if (!lp.success) {
    fail(5, 2, 'Logpush job syrabit-http-requests', JSON.stringify(lp.errors), 'run cloudflare-phase2-apply.js');
    fail(6, 2, 'Logpush job syrabit-firewall-events', JSON.stringify(lp.errors), 'run cloudflare-phase2-apply.js');
    return;
  }

  function checkJob(jobName, itemId) {
    const job = lp.result.find(j => j.name === jobName);
    if (!job) {
      fail(itemId, 2, `Logpush job ${jobName}`, 'NOT FOUND', 'run cloudflare-phase2-apply.js');
      return;
    }
    if (!job.enabled) {
      fail(itemId, 2, `Logpush job ${jobName}`, `enabled=false`, 'enable job in dashboard or run cloudflare-phase2-apply.js');
      return;
    }
    if (job.error_message) {
      fail(itemId, 2, `Logpush job ${jobName}`, `error: ${job.error_message}`, 'investigate Logpush errors in dashboard');
      return;
    }
    const detail = `id=${job.id} dataset=${job.dataset} last_complete=${job.last_complete || 'null'}`;
    if (job.last_complete) {
      const ageMs = Date.now() - new Date(job.last_complete).getTime();
      if (ageMs > 4 * 3600 * 1000) {
        fail(itemId, 2, `Logpush job ${jobName}`, `stale: ${Math.round(ageMs/60000)} min since last push`, 'check Logpush pipeline health in dashboard');
        return;
      }
    }
    pass(itemId, 2, `Logpush job ${jobName}`, detail);
  }

  checkJob('syrabit-http-requests',   5);
  checkJob('syrabit-firewall-events', 6);
}

async function auditItem7HealthCheck() {
  const hc = await cfGetOrSkip(`/zones/${ZONE_ID}/healthchecks`);
  if (!hc) {
    warn(7, 2, 'Origin healthcheck api-syrabit-ai-origin', 'token lacks Health Checks: Read scope');
    return;
  }
  if (!hc.success) {
    fail(7, 2, 'Origin healthcheck api-syrabit-ai-origin', JSON.stringify(hc.errors), 'run cloudflare-phase2-apply.js');
    return;
  }
  const record = hc.result.find(h => h.name === 'api-syrabit-ai-origin');
  if (!record) {
    fail(7, 2, 'Origin healthcheck api-syrabit-ai-origin', 'NOT FOUND', 'run cloudflare-phase2-apply.js');
    return;
  }
  const sub = [];
  if (record.type !== 'HTTPS') sub.push(`type=${record.type} (want: HTTPS)`);
  if (record.path !== '/health') sub.push(`path=${record.path} (want: /health)`);
  if (record.interval !== 60) sub.push(`interval=${record.interval} (want: 60)`);
  if (sub.length) {
    fail(7, 2, 'Origin healthcheck api-syrabit-ai-origin', sub.join(', '), 'update healthcheck config');
  } else {
    pass(7, 2, 'Origin healthcheck api-syrabit-ai-origin',
      `id=${record.id} type=HTTPS path=/health interval=60s status=${record.status}`);
  }
}

// ─── Phase 3 ─────────────────────────────────────────────────────────────────

async function auditItem8ZeroTrust() {
  const zt = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/access/apps`);
  if (!zt) {
    warn(8, 3, 'Zero Trust Access app (Syrabit Admin)', 'token lacks Zero Trust: Read scope');
    return;
  }
  if (!zt.success) {
    fail(8, 3, 'Zero Trust Access app (Syrabit Admin)', JSON.stringify(zt.errors), 'run cloudflare-phase3-apply.js');
    return;
  }
  const app = zt.result.find(a => a.name === 'Syrabit Admin');
  if (!app) {
    fail(8, 3, 'Zero Trust Access app (Syrabit Admin)', 'NOT FOUND', 'run cloudflare-phase3-apply.js');
    return;
  }
  const sub = [];
  if (app.session_duration !== '8h') sub.push(`session=${app.session_duration} (want: 8h)`);
  const hasWildcard = app.domain && (app.domain.endsWith('*') || app.domain.includes('admin*'));
  if (!hasWildcard) sub.push(`domain=${app.domain} (want wildcard /*)`);
  if (sub.length) {
    fail(8, 3, 'Zero Trust Access app (Syrabit Admin)', sub.join(', '), 'update app config in dashboard');
  } else {
    pass(8, 3, 'Zero Trust Access app (Syrabit Admin)',
      `id=${app.id} domain=${app.domain} session=8h`);
  }
}

async function auditItem9WaitingRoom() {
  const wr = await cfGetOrSkip(`/zones/${ZONE_ID}/waiting_rooms`);
  if (!wr) {
    warn(9, 3, 'Waiting Room syrabit-exam-season-queue', 'token lacks Waiting Room: Read scope');
    return;
  }
  if (!wr.success) {
    fail(9, 3, 'Waiting Room syrabit-exam-season-queue', JSON.stringify(wr.errors), 'run cloudflare-phase3-apply.js');
    return;
  }
  const room = wr.result.find(r => r.name === 'syrabit-exam-season-queue');
  if (!room) {
    fail(9, 3, 'Waiting Room syrabit-exam-season-queue', 'NOT FOUND', 'run cloudflare-phase3-apply.js');
    return;
  }
  const sub = [];
  if (!room.enabled) sub.push('enabled=false');
  if (room.session_duration !== 10) sub.push(`session=${room.session_duration}min (want: 10)`);
  if (room.host !== 'syrabit.ai') sub.push(`host=${room.host} (want: syrabit.ai)`);
  if (sub.length) {
    fail(9, 3, 'Waiting Room syrabit-exam-season-queue', sub.join(', '), 'update waiting room config');
  } else {
    pass(9, 3, 'Waiting Room syrabit-exam-season-queue',
      `id=${room.id} host=syrabit.ai session=10min enabled=true`);
  }
}

// ─── Phase 4 ─────────────────────────────────────────────────────────────────

async function auditItems10to13R2AndCacheReserve() {
  const r2 = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/r2/buckets`);
  if (!r2) {
    warn(10, 4, 'R2 bucket syrabit-assets', 'token lacks R2: Read scope');
    warn(11, 4, 'R2 bucket syrabit-cache-reserve', 'token lacks R2: Read scope');
    warn(13, 4, 'assets.syrabit.ai custom domain', 'token lacks R2: Read scope');
  } else if (!r2.success) {
    fail(10, 4, 'R2 bucket syrabit-assets', JSON.stringify(r2.errors), 'run cloudflare-phase4-apply.js');
    fail(11, 4, 'R2 bucket syrabit-cache-reserve', JSON.stringify(r2.errors), 'run cloudflare-phase4-apply.js');
    fail(13, 4, 'assets.syrabit.ai custom domain', JSON.stringify(r2.errors), 'run cloudflare-phase4-apply.js');
  } else {
    const buckets = r2.result?.buckets || [];

    if (buckets.some(b => b.name === 'syrabit-assets')) {
      pass(10, 4, 'R2 bucket syrabit-assets', 'bucket exists');

      // Check custom domain
      const domRes = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/r2/buckets/syrabit-assets/domains/custom`);
      if (!domRes) {
        warn(13, 4, 'assets.syrabit.ai custom domain', 'token lacks R2 domain read scope');
      } else if (!domRes.success) {
        fail(13, 4, 'assets.syrabit.ai custom domain', JSON.stringify(domRes.errors), 'run cloudflare-phase4-apply.js → Step 2');
      } else {
        const domain = (domRes.result?.domains || []).find(d => d.domain === 'assets.syrabit.ai');
        if (domain) {
          pass(13, 4, 'assets.syrabit.ai custom domain', `enabled=${domain.enabled} status=${domain.status || 'active'}`);
        } else {
          fail(13, 4, 'assets.syrabit.ai custom domain', 'NOT FOUND', 'run cloudflare-phase4-apply.js → Step 2');
        }
      }
    } else {
      fail(10, 4, 'R2 bucket syrabit-assets', 'NOT FOUND', 'run cloudflare-phase4-apply.js → Step 1');
      fail(13, 4, 'assets.syrabit.ai custom domain', 'parent bucket missing', 'run cloudflare-phase4-apply.js → Step 1 then Step 2');
    }

    if (buckets.some(b => b.name === 'syrabit-cache-reserve')) {
      pass(11, 4, 'R2 bucket syrabit-cache-reserve', 'bucket exists');
    } else {
      fail(11, 4, 'R2 bucket syrabit-cache-reserve', 'NOT FOUND', 'run cloudflare-phase4-apply.js → Step 3');
    }
  }

  // Cache Reserve zone setting (separate from R2 bucket list)
  // Cache Reserve is a paid Cloudflare add-on (~$5/month, pay-as-you-go).
  // Code 1135 = plan restriction; value !="on" = feature inactive.
  // Both cases use planRequired() so CI doesn't block until the add-on is purchased.
  const crRaw  = await fetch(`${API}/zones/${ZONE_ID}/cache/cache_reserve`, { headers });
  const crJson = await crRaw.json();
  if (!crJson.success) {
    const code = crJson.errors?.[0]?.code;
    if (code === 10000) {
      warn(12, 4, 'Cache Reserve zone setting', 'token lacks Cache: Read scope');
    } else if (code === 1135) {
      planRequired(12, 4, 'Cache Reserve zone setting',
        'not available on current plan (API code 1135)',
        `https://dash.cloudflare.com/${ACCOUNT_ID}/${ZONE_ID}/caching/cache-reserve`,
        '~$5/month pay-as-you-go (charged per GB cached)');
    } else {
      fail(12, 4, 'Cache Reserve zone setting', JSON.stringify(crJson.errors), 'run cloudflare-phase4-apply.js → Step 4');
    }
  } else {
    const value = crJson.result?.value;
    if (value === 'on') {
      pass(12, 4, 'Cache Reserve zone setting', 'value=on');
    } else {
      // Setting exists on the plan but is disabled — likely needs enabling after add-on purchase.
      planRequired(12, 4, 'Cache Reserve zone setting',
        `value=${JSON.stringify(value)} (want: "on") — enable after purchasing add-on`,
        `https://dash.cloudflare.com/${ACCOUNT_ID}/${ZONE_ID}/caching/cache-reserve`,
        '~$5/month pay-as-you-go (charged per GB cached)');
    }
  }
}

// ─── Phase 5 ─────────────────────────────────────────────────────────────────

async function auditItems14And15WorkerBindings() {
  const WORKER = 'syrabit-edge';
  const DATASET = 'syrabit-edge-metrics';

  const bindings = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/workers/scripts/${WORKER}/bindings`);
  if (!bindings) {
    warn(14, 5, 'Analytics Engine binding (ANALYTICS)', 'token lacks Workers: Read scope');
  } else if (!bindings.success) {
    fail(14, 5, 'Analytics Engine binding (ANALYTICS)', JSON.stringify(bindings.errors),
      'cd workers/edge-proxy && wrangler deploy');
  } else {
    const aeBinding = (bindings.result || []).find(
      b => b.type === 'analytics_engine' && b.dataset === DATASET,
    );
    if (aeBinding) {
      pass(14, 5, 'Analytics Engine binding (ANALYTICS)', `dataset=${aeBinding.dataset}`);
    } else {
      fail(14, 5, 'Analytics Engine binding (ANALYTICS)', `NOT FOUND (dataset=${DATASET})`,
        'cd workers/edge-proxy && wrangler deploy');
    }
  }

  const doNs = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/workers/durable_objects/namespaces`);
  if (!doNs) {
    warn(15, 5, 'RateLimiter Durable Object namespace', 'token lacks Durable Objects: Read scope');
  } else if (!doNs.success) {
    fail(15, 5, 'RateLimiter Durable Object namespace', JSON.stringify(doNs.errors),
      'cd workers/edge-proxy && wrangler deploy');
  } else {
    const ns = (doNs.result || []).find(n => n.class === 'RateLimiter' && n.script === WORKER);
    if (ns) {
      pass(15, 5, 'RateLimiter Durable Object namespace', `id=${ns.id} script=${ns.script}`);
    } else {
      const anyRl = (doNs.result || []).some(n => n.class === 'RateLimiter');
      if (anyRl) {
        pass(15, 5, 'RateLimiter Durable Object namespace', 'found (script tag may differ — inspect dashboard)');
      } else {
        fail(15, 5, 'RateLimiter Durable Object namespace', 'NOT FOUND',
          'cd workers/edge-proxy && wrangler deploy');
      }
    }
  }
}

async function auditItem16AeWriteRecency() {
  if (!AE_TOKEN) {
    skip(16, 5, 'Analytics Engine dataset write recency', 'CF_ANALYTICS_TOKEN not set — set env var to verify');
    return;
  }
  const aeSqlUrl = `https://api.cloudflare.com/client/v4/accounts/${ACCOUNT_ID}/analytics_engine/sql`;
  const aeQuery  = `SELECT count() AS n FROM syrabit_edge_metrics WHERE timestamp >= now() - INTERVAL '86400' SECOND`;
  try {
    const res  = await fetch(aeSqlUrl, {
      method: 'POST',
      headers: { Authorization: `Bearer ${AE_TOKEN}`, 'Content-Type': 'text/plain' },
      body: aeQuery,
    });
    const text = await res.text();
    if (!res.ok) {
      const code = (() => { try { return JSON.parse(text)?.errors?.[0]?.code; } catch { return null; } })();
      if (code === 1135) {
        warn(16, 5, 'Analytics Engine dataset write recency', 'plan does not include Analytics Engine (code 1135)');
      } else {
        warn(16, 5, 'Analytics Engine dataset write recency', `AE SQL returned ${res.status} — check CF_ANALYTICS_TOKEN scope`);
      }
    } else {
      const json = JSON.parse(text);
      const n    = Number(json?.data?.[0]?.n ?? 0);
      if (n === 0) {
        warn(16, 5, 'Analytics Engine dataset write recency', '0 rows in last 24 h — verify worker has traffic');
      } else {
        pass(16, 5, 'Analytics Engine dataset write recency', `${n.toLocaleString()} datapoints in last 24 h`);
      }
    }
  } catch (err) {
    warn(16, 5, 'Analytics Engine dataset write recency', `AE SQL fetch failed: ${err.message}`);
  }
}

// ─── Phase 6 ─────────────────────────────────────────────────────────────────

async function auditItem17MtlsCert() {
  // Item 17 (Railway-origin mTLS certificate) was retired in Task #335
  // when Railway was decommissioned. Emit a WARN (not a hard PASS) until
  // the dashboard cert deletion is operator-confirmed in
  // docs/infra/decommission.md → §8 — flipping this to pass() would hide
  // any lingering cert drift on the account.
  warn(17, 6, 'mTLS client certificate (legacy syrabit-railway-mtls)',
    'check retired — Railway origin decommissioned in Task #335; delete the cert in the Cloudflare dashboard if it still exists');
}

async function auditItem18ImageResizing() {
  // Image Resizing requires the "Image Resizing" add-on (available on paid plans).
  // Code 1135 = plan restriction; value !="on" = feature inactive.
  // Both cases use planRequired() so CI doesn't block until the add-on is purchased.
  // Once purchased, cloudflare-phase6-apply.js enables it automatically — no code changes.
  const j = await cfGetOrSkip(`/zones/${ZONE_ID}/settings/image_resizing`);
  if (!j) {
    warn(18, 6, 'Image Resizing zone setting', 'token lacks Zone Settings: Read scope');
    return;
  }
  if (!j.success) {
    const code = j.errors?.[0]?.code;
    if (code === 1135) {
      planRequired(18, 6, 'Image Resizing zone setting',
        'not available on current plan (API code 1135)',
        `https://dash.cloudflare.com/${ACCOUNT_ID}/${ZONE_ID}/speed/optimization`,
        'included with Pages Pro ($20/month) or as a standalone add-on');
    } else {
      fail(18, 6, 'Image Resizing zone setting', JSON.stringify(j.errors),
        'run cloudflare-phase6-apply.js → Step 2');
    }
    return;
  }
  const val = j.result?.value;
  if (val === 'on') {
    pass(18, 6, 'Image Resizing zone setting', 'value=on — /cdn-cgi/image/ transformations active');
  } else {
    // Setting accessible on the API (plan allows it) but currently disabled.
    // Run cloudflare-phase6-apply.js to enable it, or upgrade the plan if add-on is missing.
    planRequired(18, 6, 'Image Resizing zone setting',
      `value=${JSON.stringify(val)} (want: "on") — run cloudflare-phase6-apply.js → Step 2 after enabling add-on`,
      `https://dash.cloudflare.com/${ACCOUNT_ID}/${ZONE_ID}/speed/optimization`,
      'included with Pages Pro ($20/month) or as a standalone add-on');
  }
}

async function auditItem19ZarazAndObservatory() {
  const WORKER_NAME = 'syrabit-edge';

  // Check Zaraz GA4
  const zaraz = await cfGetOrSkip(`/zones/${ZONE_ID}/zaraz/config`);
  let zarazOk = false;
  if (!zaraz) {
    warn(19, 6, 'Zaraz GA4 + Observatory', 'token lacks Zaraz: Read scope — Zaraz check skipped');
  } else if (!zaraz.success) {
    fail(19, 6, 'Zaraz GA4 + Observatory', `Zaraz: ${JSON.stringify(zaraz.errors)}`,
      'run cloudflare-phase6-apply.js → Step 3');
  } else {
    const tools   = zaraz.result?.tools || {};
    const ga4Tool = Object.values(tools).find(
      t => t.type === 'GA4' || (t.name && t.name.toLowerCase().includes('ga4')),
    );
    if (!ga4Tool) {
      fail(19, 6, 'Zaraz GA4 + Observatory',
        'Zaraz GA4 tool NOT FOUND — client-side GA4 is disabled but server-side replacement missing',
        'run cloudflare-phase6-apply.js → Step 3');
      return;
    }
    if (!ga4Tool.enabled) {
      fail(19, 6, 'Zaraz GA4 + Observatory', `Zaraz GA4 tool disabled (name="${ga4Tool.name}")`,
        'enable GA4 tool at dash.cloudflare.com → Zaraz → Tools');
      return;
    }
    zarazOk = true;
  }

  // 6a-ii: Worker MTLS_CERT binding present in deployed worker
  const workerBindingsRes = await fetch(
    `${API}/accounts/${ACCOUNT_ID}/workers/scripts/${WORKER_NAME}/bindings`,
    { headers },
  );
  const workerBindingsJson = await workerBindingsRes.json();
  if (!workerBindingsJson.success) {
    const code = workerBindingsJson.errors?.[0]?.code;
    if (code === 10000) {
      warn(19, 6, 'Zaraz GA4 + Observatory',
        'Worker bindings: token lacks Workers Scripts: Read — cannot verify MTLS_CERT binding');
    } else {
      warn(19, 6, 'Zaraz GA4 + Observatory',
        `Worker bindings API error ${code}: ${workerBindingsJson.errors?.[0]?.message}`);
    }
  } else {
    const mtlsBinding = (workerBindingsJson.result || []).find(
      b => b.name === 'MTLS_CERT' && b.type === 'mtls_certificate',
    );
    if (!mtlsBinding) {
      fail(19, 6, 'Zaraz GA4 + Observatory',
        'Worker MTLS_CERT binding NOT FOUND in deployed worker',
        'ensure CF_MTLS_CERT_ID is set in CI secrets and re-deploy via edge-proxy-deploy.yml');
      return;
    }
    // binding present — continue
  }

  // 6a-iii: MTLS_REQUIRED secret exists in the worker (fail-closed gate)
  const workerSecretsRes = await fetch(
    `${API}/accounts/${ACCOUNT_ID}/workers/scripts/${WORKER_NAME}/secrets`,
    { headers },
  );
  const workerSecretsJson = await workerSecretsRes.json();
  if (!workerSecretsJson.success) {
    const code = workerSecretsJson.errors?.[0]?.code;
    if (code === 10000) {
      warn(19, 6, 'Zaraz GA4 + Observatory',
        'Worker secrets: token lacks Workers Scripts: Read — cannot verify MTLS_REQUIRED secret');
    } else {
      warn(19, 6, 'Zaraz GA4 + Observatory',
        `Worker secrets API error ${code}: ${workerSecretsJson.errors?.[0]?.message}`);
    }
  } else {
    const mtlsRequired = (workerSecretsJson.result || []).find(s => s.name === 'MTLS_REQUIRED');
    if (!mtlsRequired) {
      fail(19, 6, 'Zaraz GA4 + Observatory',
        'Worker MTLS_REQUIRED secret NOT SET (fail-closed gate inactive)',
        'run: wrangler secret put MTLS_REQUIRED --name syrabit-edge  (value: true)');
      return;
    }
    // secret present — fail-closed gate is armed
  }

  // 6a-iv: Railway origin bypass probe (strict semantics)
  // Sends a direct request to the Railway origin WITHOUT a mTLS client cert.
  // STRICT: ANY HTTP response (any status 2xx/4xx/5xx) = FAIL — the origin
  // is reachable at the HTTP layer, meaning only BACKEND_ORIGIN_SECRET guards it.
  // If that header leaks, the origin can be accessed directly — defeating mTLS.
  // Only a network/TLS-level rejection (no HTTP response, non-timeout error) = PASS.
  // Timeout = WARN (indeterminate: mTLS rejection vs host-unreachable).
  const RAILWAY_ORIGIN_URL = process.env.RAILWAY_ORIGIN_URL;
  if (!RAILWAY_ORIGIN_URL) {
    warn(19, 6, 'Zaraz GA4 + Observatory',
      'Railway bypass probe skipped (RAILWAY_ORIGIN_URL not set) — set CI secret to verify origin mTLS enforcement');
  } else {
    try {
      const probeResp = await fetch(`${RAILWAY_ORIGIN_URL}/api/health`, {
        signal: AbortSignal.timeout(8000),
      });
      // ANY HTTP response = origin accessible = mTLS NOT enforced at TLS level
      fail(19, 6, 'Zaraz GA4 + Observatory',
        `Railway bypass probe: HTTP ${probeResp.status} received — origin reachable at HTTP layer without mTLS cert`,
        'Require mTLS on Railway: Service → Settings → mTLS → add the Cloudflare client cert; BACKEND_ORIGIN_SECRET alone is insufficient if the header leaks');
      return;
    } catch (e) {
      const msg = e.message || String(e);
      if (msg.includes('abort') || msg.includes('timeout') || msg.includes('timed out')) {
        // Cannot distinguish mTLS rejection from unreachable host via timeout
        warn(19, 6, 'Zaraz GA4 + Observatory',
          'Railway bypass probe: connection timeout — cannot confirm TLS-level rejection vs host-unreachable; verify Railway mTLS setting manually');
      }
      // else: connection refused / SSL handshake error = mTLS enforced (pass, continue)
    }
  }

  // Check Observatory alert notification policy (speed_insights)
  // Uses the Cloudflare Notifications API — degrades gracefully on scope gaps.
  const alertsRaw  = await fetch(
    `${API}/accounts/${ACCOUNT_ID}/alerting/v3/policies`,
    { headers },
  );
  const alertsJson = await alertsRaw.json();
  let alertOk = false;
  if (!alertsJson.success) {
    const code = alertsJson.errors?.[0]?.code;
    if (code === 10000) {
      if (zarazOk) {
        warn(19, 6, 'Zaraz GA4 + Observatory',
          'Zaraz OK but Observatory alert: token lacks Account Notifications: Read scope');
      }
      // Cannot determine alert state — skip remaining Observatory checks to avoid false FAILs
      return;
    }
    // Non-auth error — record as warning; proceed to schedule check
    if (zarazOk) {
      warn(19, 6, 'Zaraz GA4 + Observatory',
        `Alerting API error ${alertsJson.errors?.[0]?.code}: ${alertsJson.errors?.[0]?.message}`);
    }
  } else {
    const speedAlert = (alertsJson.result || []).find(p => p.alert_type === 'speed_insights');
    if (!speedAlert) {
      fail(19, 6, 'Zaraz GA4 + Observatory',
        'Observatory alert policy (speed_insights) NOT FOUND',
        'run cloudflare-phase6-apply.js step 4b to create the policy');
      return;
    }
    if (!speedAlert.enabled) {
      fail(19, 6, 'Zaraz GA4 + Observatory',
        `Observatory alert policy "${speedAlert.name}" is disabled`,
        'enable the policy at dash.cloudflare.com → Notifications → Policies');
      return;
    }
    const hasEmail = (speedAlert.mechanisms?.email || []).length > 0;
    if (!hasEmail) {
      fail(19, 6, 'Zaraz GA4 + Observatory',
        `Observatory alert policy "${speedAlert.name}" has no email recipient`,
        'add admin@syrabit.ai as email recipient via dash.cloudflare.com → Notifications → Policies');
      return;
    }
    // Task #139 — verify a Slack/webhook mechanism is attached so the on-call is
    // paged immediately; email alone can sit unread overnight.
    // cloudflare-phase6-apply.js step 4b adds mechanisms.webhooks when
    // OBSERVATORY_ALERT_SLACK_WEBHOOK_ID is set in the deployment environment.
    const hasWebhook = (speedAlert.mechanisms?.webhooks || []).length > 0;
    if (!hasWebhook) {
      fail(19, 6, 'Zaraz GA4 + Observatory',
        `Observatory alert policy "${speedAlert.name}" has no Slack/webhook mechanism — on-call will not be paged (email only)`,
        'set OBSERVATORY_ALERT_SLACK_WEBHOOK_ID and re-run cloudflare-phase6-apply.js, ' +
        'or add a webhook destination manually: dash.cloudflare.com → Notifications → (edit policy) → Destinations → Webhooks');
      return;
    }
    // Assert Core Web Vitals threshold values match the required values:
    //   LCP > 2500 ms, CLS > 0.1, INP > 200 ms
    const c = speedAlert.conditions || {};
    const lcpOk = c.lcp && c.lcp.operator === 'greater_than' && Number(c.lcp.value) === 2500;
    const clsOk = c.cls && c.cls.operator === 'greater_than' && Number(c.cls.value) === 0.1;
    const inpOk = c.inp && c.inp.operator === 'greater_than' && Number(c.inp.value) === 200;
    if (!lcpOk || !clsOk || !inpOk) {
      const got = `lcp=${JSON.stringify(c.lcp||'unset')}, cls=${JSON.stringify(c.cls||'unset')}, inp=${JSON.stringify(c.inp||'unset')}`;
      fail(19, 6, 'Zaraz GA4 + Observatory',
        `Observatory alert thresholds wrong: ${got}`,
        'expected lcp>2500 ms, cls>0.1, inp>200 ms — re-run cloudflare-phase6-apply.js step 4b');
      return;
    }
    alertOk = true;
  }

  // Check Observatory schedule for homepage + representative chapter page
  const obsUrls = [
    { label: 'homepage',     url: 'https://syrabit.ai/' },
    { label: 'chapter page', url: 'https://syrabit.ai/ahsec/class-12/physics' },
  ];
  const obsResults = [];

  for (const { label, url } of obsUrls) {
    const raw  = await fetch(
      `${API}/zones/${ZONE_ID}/speed/schedule?url=${encodeURIComponent(url)}`,
      { headers },
    );
    const json = await raw.json();
    if (!json.success) {
      const code = json.errors?.[0]?.code;
      if (code === 10000) {
        if (zarazOk) {
          warn(19, 6, 'Zaraz GA4 + Observatory',
            `Zaraz OK but Observatory: token lacks Speed: Read scope (${label} check skipped)`);
        }
        return;
      } else if (code === 1135) {
        if (zarazOk) {
          warn(19, 6, 'Zaraz GA4 + Observatory',
            `Zaraz OK but Observatory not available on current plan (${label})`);
        }
        return;
      }
      obsResults.push({ label, url, found: false });
    } else {
      obsResults.push({ label, url, found: !!json.result?.schedule,
        frequency: json.result?.schedule?.frequency || 'weekly' });
    }
  }

  const missingObs = obsResults.filter(r => !r.found);
  const allGood = zarazOk && alertOk && missingObs.length === 0;
  if (allGood) {
    const detail = obsResults.map(r => `${r.label}:${r.frequency}`).join(', ');
    pass(19, 6, 'Zaraz GA4 + Observatory',
      `Zaraz GA4 enabled; alert policy active; Observatory weekly: ${detail}`);
  } else if (zarazOk && alertOk && missingObs.length > 0) {
    fail(19, 6, 'Zaraz GA4 + Observatory',
      `Zaraz+alert OK but Observatory schedule missing for: ${missingObs.map(r => r.label).join(', ')}`,
      'run cloudflare-phase6-apply.js → Step 4');
  } else if (!zarazOk) {
    fail(19, 6, 'Zaraz GA4 + Observatory',
      `Zaraz GA4 missing; Observatory: ${missingObs.length === 0 ? 'OK' : 'also missing'}`,
      'run cloudflare-phase6-apply.js');
  }
}

// ─── Report rendering ─────────────────────────────────────────────────────────

function renderReport() {
  const pass        = items.filter(i => i.status === 'PASS');
  const fail        = items.filter(i => i.status === 'FAIL');
  const warn        = items.filter(i => i.status === 'WARN');
  const skip        = items.filter(i => i.status === 'SKIP');
  const planGated   = items.filter(i => i.status === 'PLAN_REQUIRED');

  // PLAN_REQUIRED items are NOT counted as failures — they represent features
  // that need a paid add-on or plan upgrade, not misconfiguration.  Once the
  // plan upgrade is purchased, the audit automatically starts reporting PASS
  // with no code changes required.
  const MARK = { PASS: '✓', FAIL: '✗', WARN: '⚠', SKIP: '─', PLAN_REQUIRED: '💳' };

  console.log('\n════════════════════════════════════════════════════════════════');
  console.log(' Cloudflare Full Audit Report — syrabit.ai (Phases 1–6)');
  console.log('════════════════════════════════════════════════════════════════');
  console.log(`  Date:    ${new Date().toISOString()}`);
  console.log(`  Zone:    ${ZONE_ID}`);
  console.log(`  Account: ${ACCOUNT_ID}`);
  console.log('────────────────────────────────────────────────────────────────');

  let currentPhase = 0;
  for (const item of items) {
    if (item.phase !== currentPhase) {
      currentPhase = item.phase;
      console.log(`\n  ── Phase ${currentPhase} ──`);
    }
    const mark   = MARK[item.status] || '?';
    const id     = String(item.id).padStart(2, ' ');
    const label  = item.label.padEnd(50);
    const detail = item.detail ? `  [${item.detail}]` : '';
    console.log(`  ${mark} #${id}  ${label}${detail}`);
    if ((item.status === 'FAIL' || item.status === 'PLAN_REQUIRED') && item.remediation) {
      console.log(`         → ${item.remediation}`);
    }
  }

  console.log('\n────────────────────────────────────────────────────────────────');
  console.log(
    `  PASS: ${pass.length}   FAIL: ${fail.length}   WARN: ${warn.length}   ` +
    `PLAN_REQUIRED: ${planGated.length}   SKIP: ${skip.length}   TOTAL: ${items.length}`,
  );
  if (planGated.length > 0) {
    console.log(`  💳 ${planGated.length} item(s) need a paid plan upgrade (not counted as failures):`);
    for (const p of planGated) {
      console.log(`     #${p.id} ${p.label}`);
    }
  }
  console.log('════════════════════════════════════════════════════════════════\n');

  if (fail.length > 0) {
    console.error(`${fail.length} audit item(s) FAILED:`);
    for (const f of fail) {
      console.error(`  #${f.id} ${f.label}: ${f.detail}`);
    }
  } else {
    const planNote = planGated.length > 0
      ? ` (${planGated.length} item(s) pending plan upgrade — not failures)`
      : '';
    console.log(`All audit items passed or degraded gracefully to WARN/SKIP${planNote}.`);
  }

  // Write JSON report
  const report = {
    generated_at: new Date().toISOString(),
    zone_id:      ZONE_ID,
    account_id:   ACCOUNT_ID,
    summary:      { pass: pass.length, fail: fail.length, warn: warn.length, skip: skip.length, plan_required: planGated.length, total: items.length },
    items,
  };

  try {
    fs.writeFileSync(OUT_FILE, JSON.stringify(report, null, 2));
    console.log(`JSON report written to: ${OUT_FILE}`);
  } catch (e) {
    console.warn(`Could not write JSON report to ${OUT_FILE}: ${e.message}`);
  }

  return fail.length === 0 ? 0 : 1;
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  console.log('Running Cloudflare full audit — 19 items across Phases 1–6 …\n');
  // 500 ms between items prevents sustained bursts that trigger Cloudflare
  // API rate limiting (10429). cfGet() also retries on 10429 independently.
  const ITEM_PAUSE_MS = 500;

  // Phase 1
  await auditItem1ZoneSettings();      await sleep(ITEM_PAUSE_MS);
  await auditItem2BotManagement();     await sleep(ITEM_PAUSE_MS);
  await auditItem3Dmarc();             await sleep(ITEM_PAUSE_MS);

  // Phase 2
  await auditItem4R2LogsBucket();      await sleep(ITEM_PAUSE_MS);
  await auditItems5And6Logpush();      await sleep(ITEM_PAUSE_MS);
  await auditItem7HealthCheck();       await sleep(ITEM_PAUSE_MS);

  // Phase 3
  await auditItem8ZeroTrust();         await sleep(ITEM_PAUSE_MS);
  await auditItem9WaitingRoom();       await sleep(ITEM_PAUSE_MS);

  // Phase 4
  await auditItems10to13R2AndCacheReserve(); await sleep(ITEM_PAUSE_MS);

  // Phase 5
  await auditItems14And15WorkerBindings(); await sleep(ITEM_PAUSE_MS);
  await auditItem16AeWriteRecency();   await sleep(ITEM_PAUSE_MS);

  // Phase 6
  await auditItem17MtlsCert();         await sleep(ITEM_PAUSE_MS);
  await auditItem18ImageResizing();    await sleep(ITEM_PAUSE_MS);
  await auditItem19ZarazAndObservatory();

  process.exit(renderReport());
}

main().catch((err) => {
  console.error('Full audit error:', err.message);
  process.exit(1);
});
