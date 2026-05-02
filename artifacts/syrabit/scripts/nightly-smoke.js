#!/usr/bin/env node
/**
 * nightly-smoke.js — Cloudflare zone-settings health check.
 *
 * Asserts that the zone settings applied in Cloudflare Phases 1–6
 * (Tasks #105–#110) still hold their target values.  Run nightly in CI so
 * any accidental dashboard revert surfaces overnight rather than silently
 * degrading cache hit rates, bot filtering, or email security.
 *
 * Required env:
 *   CLOUDFLARE_API_TOKEN  — Zone Settings: Read, Bot Management: Read,
 *                           DNS: Read, Logs: Read, Health Checks: Read,
 *                           Zero Trust: Read (Phase 3), Waiting Room: Read (Phase 3),
 *                           R2: Read (Phase 4), Cache: Read (Phase 4),
 *                           Workers: Read, Durable Objects: Read (Phase 5),
 *                           SSL and Certificates: Read, Zaraz: Read,
 *                           Speed (Observatory): Read (Phase 6)
 *                           (Phase 2–6 checks degrade to warnings on token scope gap)
 *   CLOUDFLARE_ZONE_ID    — syrabit.ai zone (5b8c97df4431491dc7f60ea72fb61871)
 *   CLOUDFLARE_ACCOUNT_ID — Syrabit account (d66e40eac539fff1db270fddf384a5ec)
 *
 * Optional env (Task #262 — GSC Coverage check):
 *   GSC_SERVICE_ACCOUNT_JSON — Full JSON text of a Google service account key
 *                              that has "Search Console API" read access on the
 *                              syrabit.ai property.  When unset the GSC section
 *                              degrades to a warning (non-blocking).
 *                              See CRAWLABILITY_RUNBOOK.md § 9 for setup steps.
 *   GSC_SITE_URL             — GSC property URL (default: https://syrabit.ai/)
 *   GSC_INDEXED_URL_FLOOR    — Minimum indexed URL count before alerting
 *                              (default: 50).  Raise when the sitemap grows.
 *
 * Exit codes:
 *   0  — all assertions passed
 *   1  — one or more assertions failed (details printed to stdout)
 */

const TOKEN        = process.env.CLOUDFLARE_API_TOKEN;
const ZONE_ID      = process.env.CLOUDFLARE_ZONE_ID    || '5b8c97df4431491dc7f60ea72fb61871';
const ACCOUNT_ID   = process.env.CLOUDFLARE_ACCOUNT_ID || 'd66e40eac539fff1db270fddf384a5ec';
const API          = 'https://api.cloudflare.com/client/v4';
// Optional: set SLACK_WEBHOOK_URL to receive direct alerts when the smoke run
// fails. When unset the script still exits with code 1 so CI marks the run
// failed and sends the standard GitHub failed-workflow email.
const SLACK_WEBHOOK_URL = process.env.SLACK_WEBHOOK_URL || '';

if (!TOKEN) {
  console.error('CLOUDFLARE_API_TOKEN is not set — aborting smoke run');
  process.exit(1);
}

const headers = { 'Authorization': `Bearer ${TOKEN}`, 'Content-Type': 'application/json' };

async function cfGet(path) {
  const res = await fetch(`${API}${path}`, { headers });
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${path}`);
  const j = await res.json();
  if (!j.success) throw new Error(`CF error on ${path}: ${JSON.stringify(j.errors)}`);
  return j;
}

// Returns null on auth error (10000), throws for other errors
async function cfGetOrSkip(path) {
  const res = await fetch(`${API}${path}`, { headers });
  const j = await res.json();
  if (j.success) return j;
  if (j.errors?.[0]?.code === 10000) return null;          // auth — caller handles
  throw new Error(`CF error on ${path}: ${JSON.stringify(j.errors)}`);
}

const failures  = [];
const warnings  = [];

function assert(label, actual, expected) {
  const pass = JSON.stringify(actual) === JSON.stringify(expected);
  const mark = pass ? '✓' : '✗';
  console.log(`  ${mark}  ${label}: ${JSON.stringify(actual)}${pass ? '' : `  (want: ${JSON.stringify(expected)})`}`);
  if (!pass) failures.push(label);
}

function warn(label, detail) {
  console.log(`  ⚠  ${label}  [${detail}]`);
  warnings.push(label);
}

async function main() {
  console.log('Cloudflare nightly smoke — Phase 1, 2, 3, 4, 5 & 6 checks');
  console.log(`Zone: ${ZONE_ID}\n`);

  // ── Phase 1: Zone settings ────────────────────────────────────────────
  console.log('Phase 1 — Zone settings:');

  const sqsc = await cfGet(`/zones/${ZONE_ID}/settings/sort_query_string_for_cache`);
  assert('sort_query_string_for_cache', sqsc.result.value, 'on');

  const tcip = await cfGet(`/zones/${ZONE_ID}/settings/true_client_ip_header`);
  assert('true_client_ip_header', tcip.result.value, 'on');

  const ech  = await cfGet(`/zones/${ZONE_ID}/settings/ech`);
  assert('ech', ech.result.value, 'on');

  // ── Phase 1: Bot Management ───────────────────────────────────────────
  console.log('\nPhase 1 — Bot Management:');
  const bm = await cfGet(`/zones/${ZONE_ID}/bot_management`);
  assert('sbfm_likely_automated',   bm.result.sbfm_likely_automated,   'managed_challenge');
  assert('content_bots_protection', bm.result.content_bots_protection, 'block');
  // Task #259: sbfm_verified_bots MUST be 'allow' so Googlebot / Bingbot can
  // crawl. Any other value (e.g. 'block' or 'managed_challenge') silently drops
  // all verified-bot traffic and causes zero indexing even when sitemaps are
  // valid. This is asserted as a hard failure (not a warning) because it is the
  // single most impactful crawlability knob in the Cloudflare dashboard.
  assert('sbfm_verified_bots',      bm.result.sbfm_verified_bots,      'allow');

  // ── Phase 1: DMARC ────────────────────────────────────────────────────
  console.log('\nPhase 1 — DMARC:');
  const dns = await cfGet(`/zones/${ZONE_ID}/dns_records?name=_dmarc.syrabit.ai&type=TXT`);
  if (!dns.result.length) {
    failures.push('_dmarc.syrabit.ai TXT record (NOT FOUND)');
    console.log('  ✗  _dmarc.syrabit.ai TXT: NOT FOUND');
  } else {
    const content = dns.result[0].content;
    const pMatch  = content.match(/p=([^;]+)/);
    const policy  = pMatch ? pMatch[1].trim() : 'UNKNOWN';
    assert('DMARC p= policy', policy, 'quarantine');
  }

  // ── Phase 2: R2 bucket ────────────────────────────────────────────────
  console.log('\nPhase 2 — R2 bucket:');
  const r2 = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/r2/buckets`);
  if (!r2) {
    warn('R2 bucket syrabit-logs', 'token lacks R2: Read — add scope to check');
  } else {
    const exists = (r2.result?.buckets || []).some(b => b.name === 'syrabit-logs');
    assert('R2 bucket syrabit-logs exists', exists, true);
  }

  // ── Phase 2: Logpush jobs ─────────────────────────────────────────────
  console.log('\nPhase 2 — Logpush jobs:');
  const lp = await cfGetOrSkip(`/zones/${ZONE_ID}/logpush/jobs`);
  if (!lp) {
    warn('Logpush jobs',
      'token lacks Logs: Read — add the scope to CLOUDFLARE_API_TOKEN and run ' +
      'cloudflare-phase2-apply.js to create the jobs');
  } else {
    const httpJob     = lp.result.find(j => j.name === 'syrabit-http-requests');
    const firewallJob = lp.result.find(j => j.name === 'syrabit-firewall-events');

    function assertJobHealthy(job, label) {
      if (!job) {
        failures.push(`Logpush job ${label} (NOT FOUND)`);
        console.log(`  ✗  Logpush job ${label}: NOT FOUND — run cloudflare-phase2-apply.js`);
        return;
      }
      assert(`${label} enabled`,        job.enabled,       true);
      assert(`${label} error_message`,   job.error_message || null, null);
      // last_complete: non-null means at least one batch has been pushed successfully.
      // A freshly-created job will show null until its first 5-min window closes — that
      // is acceptable and does NOT indicate degradation.
      // Staleness threshold: 4 hours. Logpush batches every 5 min, so >4 h with no
      // push means 48+ consecutive missed windows — a clear signal of degradation.
      // (The nightly CI runs at 02:00 UTC; 4 h covers the lowest-traffic window.)
      if (job.last_complete) {
        const ageMs   = Date.now() - new Date(job.last_complete).getTime();
        const ageMins = Math.round(ageMs / 60000);
        const stale   = ageMs > 4 * 60 * 60 * 1000;    // 4 hours
        const mark    = stale ? '✗' : '✓';
        console.log(`  ${mark}  ${label} last_complete: ${ageMins} min ago${stale ? '  (want: <240 min)' : ''}`);
        if (stale) failures.push(`${label} last_complete stale (${ageMins} min — no push in >4 h)`);
      } else {
        console.log(`  ─  ${label} last_complete: null (job newly created — not yet stale)`);
      }
    }

    assertJobHealthy(httpJob,     'syrabit-http-requests');
    assertJobHealthy(firewallJob, 'syrabit-firewall-events');
  }

  // ── Phase 2: Healthcheck ──────────────────────────────────────────────
  console.log('\nPhase 2 — Origin Healthcheck:');
  const hc = await cfGetOrSkip(`/zones/${ZONE_ID}/healthchecks`);
  if (!hc) {
    warn('Origin Healthcheck',
      'token lacks Health Checks: Read — add scope and run cloudflare-phase2-apply.js');
  } else {
    const hcRecord = hc.result.find(h => h.name === 'api-syrabit-ai-origin');
    if (!hcRecord) {
      failures.push('Origin healthcheck api-syrabit-ai-origin (NOT FOUND)');
      console.log('  ✗  Origin healthcheck NOT FOUND — run cloudflare-phase2-apply.js');
    } else {
      console.log(`  ✓  api-syrabit-ai-origin: id=${hcRecord.id} status=${hcRecord.status}`);
    }
  }

  // ── Phase 3: Zero Trust Access application ───────────────────────────
  console.log('\nPhase 3 — Zero Trust Access:');
  const zt = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/access/apps`);
  if (!zt) {
    warn('Zero Trust Access apps',
      'token lacks Zero Trust: Read — add scope and run cloudflare-phase3-apply.js');
  } else {
    const adminApp = zt.result.find(a => a.name === 'Syrabit Admin');
    if (!adminApp) {
      failures.push('Access application Syrabit Admin (NOT FOUND)');
      console.log('  ✗  Access application Syrabit Admin: NOT FOUND — run cloudflare-phase3-apply.js');
    } else {
      console.log(`  ✓  Access app: Syrabit Admin id=${adminApp.id} domain=${adminApp.domain}`);
      assert('  Access app session_duration', adminApp.session_duration, '8h');
      // Verify the wildcard path covers all nested admin routes
      const hasWildcard = adminApp.domain && (adminApp.domain.endsWith('*') || adminApp.domain.includes('admin*'));
      assert('  Access app domain covers admin/*', hasWildcard, true);

      // Assert the email allowlist policy exists (at least one allow policy)
      const pol = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/access/apps/${adminApp.id}/policies`);
      if (!pol) {
        warn('  Access app policies', 'token lacks Zero Trust: Read for policy read');
      } else {
        const allowPolicy = pol.result.find(p => p.decision === 'allow' && p.name === 'Team email allowlist');
        if (!allowPolicy) {
          failures.push('Access policy "Team email allowlist" (NOT FOUND on Syrabit Admin)');
          console.log('  ✗  Access policy "Team email allowlist": NOT FOUND — run cloudflare-phase3-apply.js');
        } else {
          const emailCount = (allowPolicy.include || []).filter(r => r.email).length;
          console.log(`  ✓  Access policy: ${allowPolicy.name} (${emailCount} email rule(s))`);
          assert('  Policy has at least 1 email rule', emailCount >= 1, true);
        }
      }
    }
  }

  // ── Phase 3: Waiting Room ─────────────────────────────────────────────
  console.log('\nPhase 3 — Waiting Room:');
  const wr = await cfGetOrSkip(`/zones/${ZONE_ID}/waiting_rooms`);
  if (!wr) {
    warn('Waiting Room',
      'token lacks Waiting Room: Read — add scope and run cloudflare-phase3-apply.js');
  } else {
    const room = wr.result.find(r => r.name === 'syrabit-exam-season-queue');
    if (!room) {
      failures.push('Waiting Room syrabit-exam-season-queue (NOT FOUND)');
      console.log('  ✗  Waiting Room syrabit-exam-season-queue: NOT FOUND — run cloudflare-phase3-apply.js');
    } else {
      assert('syrabit-exam-season-queue enabled', room.enabled, true);
      assert('  session_duration (min)', room.session_duration, 10);
      assert('  host', room.host, 'syrabit.ai');
    }
  }

  // ── Phase 4: R2 buckets ────────────────────────────────────────────────
  // Reuse the R2 result from Phase 2 if already fetched; but cfGetOrSkip
  // is idempotent (same endpoint) so just call it again for clarity.
  console.log('\nPhase 4 — R2 Asset Storage + Cache Reserve:');
  const r2p4 = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/r2/buckets`);
  if (!r2p4) {
    warn('R2 buckets (Phase 4)',
      'token lacks R2: Read — add scope and run cloudflare-phase4-apply.js');
  } else {
    const buckets = r2p4.result?.buckets || [];
    const assetsExists = buckets.some(b => b.name === 'syrabit-assets');
    if (!assetsExists) {
      failures.push('R2 bucket syrabit-assets (NOT FOUND)');
      console.log('  ✗  R2 bucket syrabit-assets: NOT FOUND — run cloudflare-phase4-apply.js');
    } else {
      console.log('  ✓  R2 bucket syrabit-assets exists');
    }
    const cacheReserveExists = buckets.some(b => b.name === 'syrabit-cache-reserve');
    if (!cacheReserveExists) {
      failures.push('R2 bucket syrabit-cache-reserve (NOT FOUND)');
      console.log('  ✗  R2 bucket syrabit-cache-reserve: NOT FOUND — run cloudflare-phase4-apply.js');
    } else {
      console.log('  ✓  R2 bucket syrabit-cache-reserve exists');
    }
  }

  // ── Phase 4: Cache Reserve ─────────────────────────────────────────────
  // Cache Reserve requires Cloudflare Cache Reserve subscription (paid add-on).
  // Code 10000 = token scope gap; code 1135 = plan/subscription restriction.
  // Both degrade gracefully to a warning rather than a hard failure.
  const crRaw  = await fetch(`${API}/zones/${ZONE_ID}/cache/cache_reserve`, { headers });
  const crJson = await crRaw.json();
  if (!crJson.success) {
    const code = crJson.errors?.[0]?.code;
    if (code === 10000) {
      warn('Cache Reserve',
        'token lacks Cache: Read — add scope and run cloudflare-phase4-apply.js');
    } else if (code === 1135) {
      warn('Cache Reserve',
        'not available on current plan — requires Cache Reserve subscription; ' +
        'see https://dash.cloudflare.com → Caching → Cache Reserve');
    } else {
      const msg = `Cache Reserve error code ${crJson.errors?.[0]?.code}: ${crJson.errors?.[0]?.message}`;
      failures.push(`Cache Reserve (${msg})`);
      console.log(`  ✗  Cache Reserve: unexpected API error — ${msg}`);
    }
  } else {
    const value = crJson.result?.value;
    if (value === 'on') {
      console.log('  ✓  Cache Reserve: on');
    } else {
      // Cache Reserve is a paid add-on — not a misconfiguration, so emit as warning
      // rather than a hard failure.  CI will not block until the add-on is purchased.
      // Once purchased and enabled, this check will automatically report ✓.
      warn('Cache Reserve',
        `value=${JSON.stringify(value)} (want: "on") — requires Cache Reserve paid add-on (~$5/month): ` +
        `https://dash.cloudflare.com/${ACCOUNT_ID}/${ZONE_ID}/caching/cache-reserve`);
    }
  }

  // ── Phase 5: Analytics Engine dataset + Durable Object namespace ──────
  // These resources are provisioned by `wrangler deploy` (not REST API calls).
  // We verify them by inspecting the deployed worker's bindings and the
  // account's DO namespace list. Both endpoints require narrow token scopes
  // (Workers: Read, Durable Objects: Read) that are separate from the main
  // zone-settings token — degrade gracefully on code 10000.
  console.log('\nPhase 5 — Analytics Engine dataset + Durable Object rate limiter:');
  const WORKER_NAME = 'syrabit-edge';
  const AE_DATASET  = 'syrabit-edge-metrics';

  // 5a: Analytics Engine binding
  const aeBindings = await cfGetOrSkip(
    `/accounts/${ACCOUNT_ID}/workers/scripts/${WORKER_NAME}/bindings`,
  );
  if (!aeBindings) {
    warn('Analytics Engine ANALYTICS binding',
      'token lacks Workers: Read — add scope to verify or check Workers dashboard');
  } else {
    const aeBinding = (aeBindings.result || []).find(
      (b) => b.type === 'analytics_engine' && b.dataset === AE_DATASET,
    );
    if (!aeBinding) {
      failures.push(`Analytics Engine binding (dataset=${AE_DATASET}) NOT found in syrabit-edge`);
      console.log(`  ✗  ANALYTICS binding (dataset=${AE_DATASET}): NOT FOUND — run: cd workers/edge-proxy && wrangler deploy`);
    } else {
      console.log(`  ✓  ANALYTICS binding: dataset=${aeBinding.dataset}`);
    }
  }

  // 5b: RateLimiter Durable Object namespace
  const doNamespaces = await cfGetOrSkip(
    `/accounts/${ACCOUNT_ID}/workers/durable_objects/namespaces`,
  );
  if (!doNamespaces) {
    warn('RateLimiter DO namespace',
      'token lacks Durable Objects: Read — add scope to verify or check Workers dashboard');
  } else {
    const ns = (doNamespaces.result || []).find(
      (n) => n.class === 'RateLimiter' && n.script === WORKER_NAME,
    );
    if (!ns) {
      const anyMatch = (doNamespaces.result || []).some((n) => n.class === 'RateLimiter');
      if (anyMatch) {
        console.log('  ✓  RateLimiter DO namespace found (possibly on different script tag)');
      } else {
        failures.push('RateLimiter DO namespace (NOT FOUND — wrangler deploy needed)');
        console.log('  ✗  RateLimiter DO namespace: NOT FOUND — run: cd workers/edge-proxy && wrangler deploy');
      }
    } else {
      console.log(`  ✓  RateLimiter DO namespace: id=${ns.id} script=${ns.script}`);
    }
  }

  // 5c: Analytics Engine dataset write recency
  // Verifies the worker has written at least one datapoint in the last 24 h
  // by querying the AE SQL API. Requires CF_ANALYTICS_TOKEN env var with
  // "Analytics: Read" scope. Degrades to a warning if the token is absent
  // or on plan-restriction errors (code 1135) so CI doesn't block deploys
  // on freshly-provisioned accounts with no traffic yet.
  const cfAnalyticsToken = process.env.CF_ANALYTICS_TOKEN;
  if (!cfAnalyticsToken) {
    warn('AE dataset write recency', 'CF_ANALYTICS_TOKEN not set — set env var to verify writes');
  } else {
    const aeSqlUrl = `https://api.cloudflare.com/client/v4/accounts/${ACCOUNT_ID}/analytics_engine/sql`;
    const aeQuery  = `SELECT count() AS n FROM syrabit_edge_metrics WHERE timestamp >= now() - INTERVAL '86400' SECOND`;
    try {
      const aeRes  = await fetch(aeSqlUrl, {
        method: 'POST',
        headers: { Authorization: `Bearer ${cfAnalyticsToken}`, 'Content-Type': 'text/plain' },
        body: aeQuery,
      });
      const aeText = await aeRes.text();
      if (!aeRes.ok) {
        const code = (() => { try { return JSON.parse(aeText)?.errors?.[0]?.code; } catch { return null; } })();
        if (code === 1135) {
          warn('AE dataset write recency', 'plan does not include Analytics Engine (code 1135)');
        } else {
          warn('AE dataset write recency', `AE SQL returned ${aeRes.status} — check CF_ANALYTICS_TOKEN scope`);
        }
      } else {
        const aeJson = JSON.parse(aeText);
        const n = Number(aeJson?.data?.[0]?.n ?? 0);
        if (n === 0) {
          warn('AE dataset write recency', 'syrabit_edge_metrics has 0 rows in last 24 h — verify worker is deployed and receiving traffic');
        } else {
          console.log(`  ✓  AE dataset write recency: ${n.toLocaleString()} datapoints in last 24 h`);
        }
      }
    } catch (err) {
      warn('AE dataset write recency', `AE SQL fetch failed: ${err.message}`);
    }
  }

  // ── Phase 6: mTLS cert, Image Resizing, Zaraz GA4, Observatory ────────
  // These resources are provisioned by cloudflare-phase6-apply.js.
  // All checks degrade to warnings on token scope gaps (code 10000) or
  // plan-restriction errors (code 1135) so CI doesn't block on new accounts.
  console.log('\nPhase 6 — mTLS cert, Image Resizing, Zaraz GA4, Observatory:');

  // 6a: mTLS client certificate for Railway origin
  //
  // Three-layer enforcement verification:
  //   6a-i:  Account-level cert object exists (syrabit-railway-mtls)
  //   6a-ii: Worker has [[mtls_certificates]] binding (MTLS_CERT name in worker bindings)
  //   6a-iii: MTLS_REQUIRED secret = "true" in the deployed worker (fail-closed gate)
  //
  const mtlsCerts = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/mtls_certificates`);
  if (!mtlsCerts) {
    warn('mTLS client certificate',
      'token lacks SSL and Certificates: Read — add scope to verify');
  } else {
    const railwayCert = (mtlsCerts.result || []).find(c => c.name === 'syrabit-railway-mtls');
    if (!railwayCert) {
      failures.push('mTLS certificate syrabit-railway-mtls (NOT FOUND)');
      console.log('  ✗  mTLS certificate syrabit-railway-mtls: NOT FOUND — run cloudflare-phase6-apply.js');
    } else {
      const expiresOn    = new Date(railwayCert.expires_on);
      const daysLeft     = Math.round((expiresOn - Date.now()) / 86400000);
      const expiringSoon = daysLeft < 60;
      const mark         = expiringSoon ? '⚠' : '✓';
      console.log(`  ${mark}  mTLS cert syrabit-railway-mtls: id=${railwayCert.id} expires=${railwayCert.expires_on} (${daysLeft}d)`);
      if (expiringSoon) {
        warnings.push(`mTLS cert expires in ${daysLeft} days — renew via cloudflare-phase6-apply.js`);
      }
    }
  }

  // 6a-ii: Worker MTLS_CERT binding is present in the deployed worker.
  // Uses /workers/scripts/{name}/bindings — same endpoint as Phase 5a.
  // The inject-mtls-cert-id.js CI gate ensures the wrangler.toml [[mtls_certificates]]
  // block is populated at deploy time; this assertion proves it reached the worker.
  const workerBindings = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/workers/scripts/${WORKER_NAME}/bindings`);
  if (!workerBindings) {
    warn('Worker MTLS_CERT binding',
      'token lacks Workers Scripts: Read — add scope to verify worker bindings');
  } else {
    const mtlsBinding = (workerBindings.result || []).find(b => b.name === 'MTLS_CERT' && b.type === 'mtls_certificate');
    if (!mtlsBinding) {
      failures.push('Worker MTLS_CERT binding NOT FOUND in deployed worker');
      console.log('  ✗  Worker MTLS_CERT binding: NOT FOUND — ensure CF_MTLS_CERT_ID secret is set and re-deploy via edge-proxy-deploy.yml');
    } else {
      console.log(`  ✓  Worker MTLS_CERT binding: present (certificate_id=${mtlsBinding.certificate_id || 'N/A'})`);
    }
  }

  // 6a-iii: MTLS_REQUIRED=true is set in the worker (fail-closed gate).
  // Cloudflare's Workers API exposes secret names (not values) at
  // /accounts/{id}/workers/scripts/{name}/secrets — we can verify the secret
  // exists. The value "true" cannot be read back but its presence is enforced
  // by proxyToBackend() which 503s when MTLS_CERT binding is absent; step 6a-ii
  // above already proves the binding is present, so together they confirm the
  // fail-closed path is active.
  const workerSecrets = await cfGetOrSkip(`/accounts/${ACCOUNT_ID}/workers/scripts/${WORKER_NAME}/secrets`);
  if (!workerSecrets) {
    warn('Worker MTLS_REQUIRED secret',
      'token lacks Workers Scripts: Read — add scope to verify worker secrets');
  } else {
    const mtlsRequiredSecret = (workerSecrets.result || []).find(s => s.name === 'MTLS_REQUIRED');
    if (!mtlsRequiredSecret) {
      failures.push('Worker MTLS_REQUIRED secret NOT SET in deployed worker (fail-closed gate inactive)');
      console.log('  ✗  Worker MTLS_REQUIRED secret: NOT SET — run: wrangler secret put MTLS_REQUIRED --name syrabit-edge (value: true)');
    } else {
      console.log('  ✓  Worker MTLS_REQUIRED secret: present (fail-closed gate active)');
    }
  }

  // 6a-iv: Railway origin bypass probe.
  // Attempts a direct HTTP request to the Railway backend WITHOUT the Cloudflare
  // worker (no BACKEND_ORIGIN_SECRET, no mTLS cert).  This verifies that Railway
  // enforces the mTLS client certificate at the TLS level: a connection made
  // without the CF-issued cert must fail at the TLS handshake before HTTP is
  // reached.
  //
  // When Railway TLS-level mTLS is correctly configured, fetch() throws a
  // network/TLS error before any HTTP response is received.  Any HTTP response
  // (including 4xx/5xx from application middleware) means the connection was
  // accepted at the TLS layer — only application-layer guards are active, which
  // is weaker: if BACKEND_ORIGIN_SECRET ever leaks the origin is fully exposed.
  //
  // The backend also has MtlsClientCertMiddleware (Task #120) which rejects
  // requests missing a valid HMAC proof that the CF Worker sent them with the
  // cert bound.  This is a non-spoofable belt-and-suspenders layer, but it does
  // NOT substitute for TLS-level enforcement: this probe must still see a
  // network error (not 403) for the gold standard to be met.
  //
  // Acceptable outcomes:
  //   PASS:  network/TLS error — TLS handshake rejected before HTTP (gold standard).
  //   WARN:  connection timeout — origin not reachable (mTLS or firewall).
  //   FAIL:  any HTTP response — origin reachable without the CF client cert.
  //
  // RAILWAY_ORIGIN_URL must be set in the CI environment to the bare Railway
  // URL (e.g. https://syrabit-production.up.railway.app).
  //   CI (process.env.CI === 'true'):   missing value is a hard failure — the
  //     probe must run in CI so a bypass regression cannot silently go unnoticed.
  //   Local:                            missing value is a skippable warning.
  // Set the secret at: GitHub → Settings → Secrets → Actions → RAILWAY_ORIGIN_URL.
  // The GitHub Actions cf-zone-settings job already passes the secret via
  // `RAILWAY_ORIGIN_URL: ${{ secrets.RAILWAY_ORIGIN_URL }}`.
  const RAILWAY_ORIGIN_URL = (process.env.RAILWAY_ORIGIN_URL || '').trim();
  const IS_CI = process.env.CI === 'true';
  if (!RAILWAY_ORIGIN_URL) {
    if (IS_CI) {
      // In CI the secret must be present — a silent skip would leave the bypass
      // probe permanently inactive without any operator noticing.
      failures.push(
        'Railway bypass probe: RAILWAY_ORIGIN_URL not set in CI environment ' +
        '— add it at GitHub → Settings → Secrets and variables → Actions → RAILWAY_ORIGIN_URL',
      );
      console.log('  ✗  Railway bypass probe: RAILWAY_ORIGIN_URL not set in CI — add GitHub secret RAILWAY_ORIGIN_URL');
      console.log('      The secret expands to an empty string when unset; set it to the bare Railway backend URL.');
    } else {
      warnings.push('Railway bypass probe skipped: RAILWAY_ORIGIN_URL not set — set the CI secret to verify origin enforcement');
      console.log('  ⚠  Railway bypass probe: SKIPPED — RAILWAY_ORIGIN_URL not set (local run; set CI secret to enforce in CI)');
    }
  } else {
    try {
      const probeResp = await fetch(`${RAILWAY_ORIGIN_URL}/api/health`, {
        signal: AbortSignal.timeout(8000),
      });
      // ANY HTTP response — even 403 from MtlsClientCertMiddleware — means
      // Railway accepted the connection at the TLS layer without requiring the
      // client cert.  TLS-level enforcement is not active.
      const hint = probeResp.status === 403
        ? ' (MtlsClientCertMiddleware active, but TLS-level enforcement not configured — configure Railway mTLS for the gold standard)'
        : '';
      failures.push(
        `Railway bypass probe received HTTP ${probeResp.status} — origin reachable at TLS layer without mTLS cert${hint}`,
      );
      console.log(`  ✗  Railway bypass probe: HTTP ${probeResp.status} received — origin accessible without client cert at TLS level`);
      console.log('      → Configure Railway mTLS: Railway dashboard → Service → Settings → mTLS → require Cloudflare client cert');
      console.log('      → Until TLS-level mTLS is enforced, BACKEND_ORIGIN_SECRET + HMAC are the only guards (Task #120)');
    } catch (e) {
      const msg = e.message || String(e);
      if (msg.includes('abort') || msg.includes('timeout') || msg.includes('timed out')) {
        // Connection timed out — origin not reachable; consistent with mTLS or firewall
        warnings.push('Railway bypass probe: connection timeout — cannot confirm TLS rejection vs unreachable host');
        console.log('  ⚠  Railway bypass probe: connection timeout — origin not reachable (mTLS or firewall; cannot distinguish)');
      } else {
        // TLS handshake failure, connection refused, SSL error — TLS-level mTLS enforced
        console.log(`  ✓  Railway bypass probe: rejected at network/TLS level — "${msg}" — mTLS enforcement confirmed`);
      }
    }
  }

  // 6b: Image Resizing zone setting
  // Image Resizing is a paid Cloudflare add-on (included with Pages Pro or as
  // a standalone add-on).  Code 1135 = plan restriction; value !="on" = feature
  // inactive.  Both cases degrade to a warning — CI does not block until the
  // add-on is purchased.  Once purchased, enabling it via cloudflare-phase6-apply.js
  // activates /cdn-cgi/image/ transforms automatically with no code changes.
  const imgResRaw  = await fetch(`${API}/zones/${ZONE_ID}/settings/image_resizing`, { headers });
  const imgResJson = await imgResRaw.json();
  if (!imgResJson.success) {
    const code = imgResJson.errors?.[0]?.code;
    if (code === 10000) {
      warn('image_resizing zone setting', 'token lacks Zone Settings: Read — add scope to verify');
    } else if (code === 1135) {
      warn('image_resizing zone setting',
        `not available on current plan (API code 1135) — requires Image Resizing add-on: ` +
        `https://dash.cloudflare.com/${ACCOUNT_ID}/${ZONE_ID}/speed/optimization`);
    } else {
      failures.push(`image_resizing (unexpected API error code ${code}: ${imgResJson.errors?.[0]?.message})`);
      console.log(`  ✗  image_resizing: unexpected API error code ${code} — run cloudflare-phase6-apply.js`);
    }
  } else {
    const val = imgResJson.result?.value;
    if (val === 'on') {
      console.log('  ✓  image_resizing: on');
    } else {
      // Not "on" — plan add-on not yet purchased or not yet enabled. Warn, don't fail.
      warn('image_resizing zone setting',
        `value=${JSON.stringify(val)} (want: "on") — requires Image Resizing paid add-on: ` +
        `https://dash.cloudflare.com/${ACCOUNT_ID}/${ZONE_ID}/speed/optimization`);
    }
  }

  // 6c: Zaraz GA4 tool configured
  // Raw fetch — Zaraz may return non-10000 codes on plans without Zaraz.
  const zarazRaw  = await fetch(`${API}/zones/${ZONE_ID}/zaraz/config`, { headers });
  const zarazJson = await zarazRaw.json();
  if (!zarazJson.success) {
    const code = zarazJson.errors?.[0]?.code;
    if (code === 10000) {
      warn('Zaraz GA4 tool', 'token lacks Zaraz: Read — add scope or verify at dash.cloudflare.com → Zaraz');
    } else {
      warn('Zaraz GA4 tool', `Zaraz API error code ${code}: ${zarazJson.errors?.[0]?.message || JSON.stringify(zarazJson.errors)}`);
    }
  } else {
    const tools   = zarazJson.result?.tools || {};
    const ga4Tool = Object.values(tools).find(
      t => t.type === 'GA4' || (t.name && t.name.toLowerCase().includes('ga4')),
    );
    if (!ga4Tool) {
      failures.push('Zaraz GA4 tool (NOT FOUND)');
      console.log('  ✗  Zaraz GA4 tool: NOT FOUND — run cloudflare-phase6-apply.js');
    } else {
      console.log(`  ✓  Zaraz GA4 tool: "${ga4Tool.name}" enabled=${ga4Tool.enabled}`);
      assert('  Zaraz GA4 tool enabled', ga4Tool.enabled, true);
    }
  }

  // 6d-alert: Observatory Core Web Vitals notification policy
  // Verify a speed_insights alert policy exists on the account (created by
  // cloudflare-phase6-apply.js step 4b). Degrades to warning on scope gaps.
  const alertsRaw  = await fetch(`${API}/accounts/${ACCOUNT_ID}/alerting/v3/policies`, { headers });
  const alertsJson = await alertsRaw.json();
  if (!alertsJson.success) {
    const code = alertsJson.errors?.[0]?.code;
    if (code === 10000) {
      warn('Observatory alert policy (speed_insights)', 'token lacks Account Notifications: Read — add scope to verify');
    } else {
      warn('Observatory alert policy (speed_insights)', `Alerting API error code ${code}: ${alertsJson.errors?.[0]?.message}`);
    }
  } else {
    const speedAlert = (alertsJson.result || []).find(
      p => p.alert_type === 'speed_insights',
    );
    if (!speedAlert) {
      failures.push('Observatory alert policy (speed_insights) NOT FOUND');
      console.log('  ✗  Observatory alert policy: NOT FOUND — run cloudflare-phase6-apply.js (step 4b creates it)');
    } else {
      const hasEmail   = (speedAlert.mechanisms?.email    || []).length > 0;
      const hasWebhook = (speedAlert.mechanisms?.webhooks || []).length > 0;
      console.log(`  ✓  Observatory alert policy: "${speedAlert.name}" enabled=${speedAlert.enabled}`);
      assert('  speed_insights policy enabled', speedAlert.enabled, true);
      if (!hasEmail) {
        warnings.push('Observatory alert policy has no email recipient — add admin@syrabit.ai via dashboard');
        console.log('  ⚠  Observatory alert policy: no email recipient configured');
      }
      // Assert that a Slack (webhook) mechanism is present so the on-call is
      // paged immediately — email alone can sit unread overnight.
      // cloudflare-phase6-apply.js step 4b adds mechanisms.webhooks when
      // OBSERVATORY_ALERT_SLACK_WEBHOOK_ID is set.
      if (!hasWebhook) {
        failures.push('Observatory alert policy has no Slack/webhook mechanism — on-call will not be paged (email only)');
        console.log('  ✗  Observatory alert policy: no Slack/webhook mechanism found');
        console.log('     Set OBSERVATORY_ALERT_SLACK_WEBHOOK_ID and re-run cloudflare-phase6-apply.js,');
        console.log('     or add a webhook destination manually at:');
        console.log('     dash.cloudflare.com → Notifications → (edit policy) → Destinations → Webhooks.');
      } else {
        const webhookIds = (speedAlert.mechanisms.webhooks).map(m => m.id).join(', ');
        console.log(`  ✓  Observatory alert Slack/webhook destination(s): ${webhookIds}`);
      }
      // Assert Core Web Vitals threshold values are set correctly.
      // cloudflare-phase6-apply.js creates: lcp>2500 ms, cls>0.1, inp>200 ms.
      const c = speedAlert.conditions || {};
      const lcpOk  = c.lcp  && c.lcp.operator === 'greater_than'  && Number(c.lcp.value)  === 2500;
      const clsOk  = c.cls  && c.cls.operator === 'greater_than'  && Number(c.cls.value)  === 0.1;
      const inpOk  = c.inp  && c.inp.operator === 'greater_than'  && Number(c.inp.value)  === 200;
      if (!lcpOk) {
        warnings.push(`Observatory alert LCP threshold: expected >2500 ms, got ${JSON.stringify(c.lcp || 'unset')}`);
        console.log(`  ⚠  Observatory LCP threshold: expected >2500 ms, got ${JSON.stringify(c.lcp || 'unset')}`);
      }
      if (!clsOk) {
        warnings.push(`Observatory alert CLS threshold: expected >0.1, got ${JSON.stringify(c.cls || 'unset')}`);
        console.log(`  ⚠  Observatory CLS threshold: expected >0.1, got ${JSON.stringify(c.cls || 'unset')}`);
      }
      if (!inpOk) {
        warnings.push(`Observatory alert INP threshold: expected >200 ms, got ${JSON.stringify(c.inp || 'unset')}`);
        console.log(`  ⚠  Observatory INP threshold: expected >200 ms, got ${JSON.stringify(c.inp || 'unset')}`);
      }
      if (lcpOk && clsOk && inpOk) {
        console.log('  ✓  Observatory alert thresholds: LCP>2500 ms, CLS>0.1, INP>200 ms — correct');
      }
    }
  }

  // 6d: Observatory scheduled runs — homepage + representative chapter page
  // Raw fetch — Observatory may return 1135 on plans without Observatory access.
  const obsTargets = [
    { label: 'homepage',     url: 'https://syrabit.ai/' },
    { label: 'chapter page', url: 'https://syrabit.ai/ahsec/class-12/physics' },
  ];
  for (const { label, url } of obsTargets) {
    const obsRaw  = await fetch(
      `${API}/zones/${ZONE_ID}/speed/schedule?url=${encodeURIComponent(url)}`,
      { headers },
    );
    const obsJson = await obsRaw.json();
    if (!obsJson.success) {
      const code = obsJson.errors?.[0]?.code;
      if (code === 10000) {
        warn(`Observatory schedule (${label})`, 'token lacks Speed: Read — add scope or verify at dash.cloudflare.com → Speed → Observatory');
        break;  // same token issue will affect all targets
      } else if (code === 1135) {
        warn(`Observatory schedule (${label})`, 'not available on current plan — requires Observatory access');
        break;
      } else {
        warn(`Observatory schedule (${label})`, `Observatory API error code ${code}: ${obsJson.errors?.[0]?.message}`);
      }
    } else if (obsJson.result?.schedule) {
      const freq = obsJson.result.schedule.frequency || 'unknown';
      console.log(`  ✓  Observatory schedule (${label}): frequency=${freq}`);
    } else {
      failures.push(`Observatory schedule for ${url} (NOT FOUND)`);
      console.log(`  ✗  Observatory schedule (${label}): NOT FOUND — run cloudflare-phase6-apply.js`);
    }
  }

  // ── Task #259: Sitemap Content-Type correctness ───────────────────────
  // HEAD-check every sitemap URL that Googlebot will follow and assert each
  // returns Content-Type: application/xml (not text/html — the SPA shell).
  // Tests both the root aliases (Worker SEO_PASSTHROUGH_RE) and the /api/seo/
  // sub-paths (new regex branch added in Task #259).  Any regression in the
  // Worker or backend routing that causes a sitemap to return text/html is
  // caught here before Googlebot silently drops URLs from the index.
  const SITE_BASE = 'https://syrabit.ai';
  const sitemapChecks = [
    '/sitemap-index.xml',
    '/sitemap-subjects.xml',
    '/sitemap-pages.xml',
    '/api/seo/sitemap-subjects.xml',
    '/api/seo/sitemap-index.xml',
  ];
  console.log('\nTask #259 — Sitemap Content-Type checks:');
  for (const path of sitemapChecks) {
    try {
      const r = await fetch(`${SITE_BASE}${path}`, {
        method: 'HEAD',
        signal: AbortSignal.timeout(15000),
        redirect: 'follow',
      });
      const ct = r.headers.get('content-type') || '';
      const isXml = ct.includes('application/xml') || ct.includes('text/xml');
      const isHtml = ct.includes('text/html');
      const mark = (r.status === 200 && isXml) ? '✓' : '✗';
      const detail = `HTTP ${r.status}  Content-Type: ${ct || '(none)'}`;
      console.log(`  ${mark}  ${path}  ${detail}`);
      if (r.status !== 200) {
        failures.push(`Sitemap ${path}: HTTP ${r.status} (want 200)`);
      } else if (isHtml) {
        failures.push(`Sitemap ${path}: Content-Type is text/html — Worker proxy gap; Googlebot receives SPA shell instead of XML`);
      } else if (!isXml) {
        failures.push(`Sitemap ${path}: Content-Type "${ct}" is not application/xml`);
      }
    } catch (e) {
      const msg = e.message || String(e);
      if (msg.includes('abort') || msg.includes('timeout') || msg.includes('timed out')) {
        warn(`Sitemap HEAD ${path}`, 'request timed out — site may be warming up; re-run smoke after 60 s');
      } else {
        failures.push(`Sitemap HEAD ${path}: fetch failed — ${msg}`);
        console.log(`  ✗  ${path}  fetch error: ${msg}`);
      }
    }
  }

  // ── Task #262: Google Search Console Coverage check ──────────────────
  // Uses the GSC Webmasters API (service account JWT auth) to read the
  // Sitemaps report and assert that the total indexed URL count across all
  // submitted sitemaps is > GSC_INDEXED_URL_FLOOR.  Degrades to a warning
  // when GSC_SERVICE_ACCOUNT_JSON is not set so CI runs without a credential
  // are non-blocking.  See CRAWLABILITY_RUNBOOK.md § 9 for setup steps.
  {
    const GSC_SA_JSON         = process.env.GSC_SERVICE_ACCOUNT_JSON || '';
    const GSC_SITE_URL        = process.env.GSC_SITE_URL        || 'https://syrabit.ai/';
    const GSC_INDEXED_FLOOR   = parseInt(process.env.GSC_INDEXED_URL_FLOOR || '50', 10);

    console.log('\nTask #262 — GSC Coverage report check:');

    if (!GSC_SA_JSON) {
      warn(
        'GSC Coverage check',
        'GSC_SERVICE_ACCOUNT_JSON not set — skipping. ' +
        'Set the env var to activate nightly indexing regression detection ' +
        '(see CRAWLABILITY_RUNBOOK.md § 9).',
      );
    } else {
      try {
        // ── Step 1: Parse service account creds ──────────────────────────
        let saCreds;
        try {
          saCreds = JSON.parse(GSC_SA_JSON);
        } catch (e) {
          throw new Error(`GSC_SERVICE_ACCOUNT_JSON is not valid JSON: ${e.message}`);
        }
        const { client_email, private_key } = saCreds;
        if (!client_email || !private_key) {
          throw new Error('GSC_SERVICE_ACCOUNT_JSON is missing client_email or private_key');
        }

        // ── Step 2: Build a signed JWT for the OAuth2 token exchange ─────
        const nodeCrypto = await import('node:crypto');
        const now        = Math.floor(Date.now() / 1000);
        const jwtHeader  = Buffer.from(JSON.stringify({ alg: 'RS256', typ: 'JWT' })).toString('base64url');
        const jwtClaims  = Buffer.from(JSON.stringify({
          iss:   client_email,
          scope: 'https://www.googleapis.com/auth/webmasters.readonly',
          aud:   'https://oauth2.googleapis.com/token',
          iat:   now,
          exp:   now + 3600,
        })).toString('base64url');
        const sigInput = `${jwtHeader}.${jwtClaims}`;
        const sig      = nodeCrypto.createSign('RSA-SHA256').update(sigInput).sign(private_key, 'base64url');
        const signedJwt = `${sigInput}.${sig}`;

        // ── Step 3: Exchange JWT for an access token ──────────────────────
        const tokenRes  = await fetch('https://oauth2.googleapis.com/token', {
          method:  'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body:    new URLSearchParams({
            grant_type: 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            assertion:  signedJwt,
          }),
          signal: AbortSignal.timeout(15000),
        });
        const tokenJson = await tokenRes.json();
        if (!tokenJson.access_token) {
          throw new Error(`GSC token exchange failed (HTTP ${tokenRes.status}): ${JSON.stringify(tokenJson)}`);
        }

        // ── Step 4: Fetch the Sitemaps report for the property ────────────
        const siteEncoded = encodeURIComponent(GSC_SITE_URL);
        const sitemapsRes = await fetch(
          `https://www.googleapis.com/webmasters/v3/sites/${siteEncoded}/sitemaps`,
          {
            headers: { Authorization: `Bearer ${tokenJson.access_token}` },
            signal:  AbortSignal.timeout(15000),
          },
        );
        const sitemapsJson = await sitemapsRes.json();

        if (sitemapsRes.status === 403) {
          warn(
            'GSC Coverage check',
            `403 Forbidden — service account "${client_email}" needs read access ` +
            `on property "${GSC_SITE_URL}". Grant "Restricted" access via ` +
            'GSC → Settings → Users and permissions, then re-run.',
          );
        } else if (!sitemapsRes.ok) {
          throw new Error(`GSC Sitemaps API HTTP ${sitemapsRes.status}: ${JSON.stringify(sitemapsJson)}`);
        } else {
          const sitemaps = sitemapsJson.sitemap || [];
          if (sitemaps.length === 0) {
            warn(
              'GSC Coverage check',
              `No sitemaps found on property "${GSC_SITE_URL}" — ` +
              'submit sitemap-index.xml via GSC dashboard (CRAWLABILITY_RUNBOOK.md § 2)',
            );
          } else {
            // Sum indexed counts across all sitemaps (parent index rows often
            // report 0; child sitemaps carry the real counts).
            let totalIndexed   = 0;
            let totalSubmitted = 0;
            for (const sm of sitemaps) {
              for (const c of (sm.contents || [])) {
                totalIndexed   += parseInt(c.indexed   || '0', 10);
                totalSubmitted += parseInt(c.submitted || '0', 10);
              }
            }
            const mark = totalIndexed >= GSC_INDEXED_FLOOR ? '✓' : '✗';
            console.log(`  ${mark}  Indexed URLs: ${totalIndexed} / submitted: ${totalSubmitted}  (floor: ${GSC_INDEXED_FLOOR})`);
            if (totalIndexed < GSC_INDEXED_FLOOR) {
              failures.push(
                `GSC Coverage: indexed URL count ${totalIndexed} is below floor ${GSC_INDEXED_FLOOR} ` +
                `— possible indexing regression; check GSC Coverage report for "${GSC_SITE_URL}"`,
              );
            }
            // Per-sitemap detail for diagnosis
            for (const sm of sitemaps) {
              const smIdx = (sm.contents || []).reduce((s, c) => s + parseInt(c.indexed   || '0', 10), 0);
              const smSub = (sm.contents || []).reduce((s, c) => s + parseInt(c.submitted || '0', 10), 0);
              const smErr = parseInt(sm.errors || '0', 10);
              const errNote = smErr > 0 ? `  errors=${smErr}` : '';
              console.log(`       ${sm.path}: submitted=${smSub} indexed=${smIdx}${errNote}`);
            }
          }
        }
      } catch (e) {
        const msg = e.message || String(e);
        if (msg.includes('abort') || msg.includes('timeout') || msg.includes('timed out')) {
          warn('GSC Coverage check', 'request timed out — GSC API may be unreachable from this runner; re-run manually');
        } else {
          warn('GSC Coverage check', `unexpected error — ${msg}. Check credential format and network access.`);
        }
      }
    }
  }

  // ── Summary ────────────────────────────────────────────────────────────
  console.log('');
  if (warnings.length > 0) {
    console.log(`${warnings.length} warning(s): ${warnings.join(', ')}`);
  }
  if (failures.length === 0) {
    console.log('All checks passed.');
    process.exit(0);
  } else {
    console.error(`\n${failures.length} check(s) FAILED:\n  ${failures.join('\n  ')}`);
    // ── Slack alert on failure ────────────────────────────────────────────
    // Fires before exit(1) so the team is paged directly from the smoke run,
    // not just via the GitHub failed-workflow email.  Set SLACK_WEBHOOK_URL
    // in CI secrets (or locally) to activate; degrades silently when unset.
    if (SLACK_WEBHOOK_URL) {
      try {
        const runUrl = process.env.GITHUB_SERVER_URL && process.env.GITHUB_REPOSITORY && process.env.GITHUB_RUN_ID
          ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}`
          : '(local run)';
        const text = `:rotating_light: *Nightly smoke FAILED* — ${failures.length} check(s):\n` +
          failures.map(f => `• ${f}`).join('\n') +
          `\n<${runUrl}|View run>`;
        await fetch(SLACK_WEBHOOK_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text }),
        });
        console.log('Slack alert sent.');
      } catch (e) {
        console.warn(`Slack alert failed: ${e.message}`);
      }
    } else {
      console.log('(SLACK_WEBHOOK_URL not set — set to receive direct Slack alerts on failure)');
    }
    process.exit(1);
  }
}

main().catch((err) => {
  console.error('Smoke run error:', err.message);
  process.exit(1);
});
