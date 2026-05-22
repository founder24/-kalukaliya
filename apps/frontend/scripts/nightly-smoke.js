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
 *   CLOUDFLARE_API_TOKEN       — Zone Settings: Read, Bot Management: Read,
 *                                DNS: Read, Logs: Read, Health Checks: Read,
 *                                Zero Trust: Read (Phase 3), Waiting Room: Read (Phase 3),
 *                                R2: Read (Phase 4), Cache: Read (Phase 4),
 *                                Workers: Read, Durable Objects: Read (Phase 5),
 *                                SSL and Certificates: Read, Zaraz: Read,
 *                                Speed (Observatory): Read (Phase 6),
 *                                Load Balancer: Read (Task #76/87)
 *                                (Phase 2–6 checks degrade to warnings on token scope gap)
 *   CLOUDFLARE_ANALYTICS_TOKEN — Backend runtime token (Vectorize, cache purge).
 *                                Task #87 probes GET /accounts/{id}/vectorize/v2/indexes.
 *   CLOUDFLARE_PAGES_TOKEN     — Pages CI deploy token.
 *                                Fallback: CF_PAGES_API_TOKEN (legacy name).
 *                                Task #87 probes GET /accounts/{id}/pages/projects.
 *   CLOUDFLARE_ZONE_ID         — syrabit.ai zone (5b8c97df4431491dc7f60ea72fb61871).
 *                                Required for LB Read zone probe (Task #87).
 *   CLOUDFLARE_ACCOUNT_ID      — Syrabit account (d66e40eac539fff1db270fddf384a5ec)
 *
 * Optional env (Task #262 — GSC Coverage check):
 *   GSC_SERVICE_ACCOUNT_JSON — Full JSON text of a Google service account key
 *                              that has "Search Console API" read access on the
 *                              syrabit.ai property.  When unset the GSC section
 *                              degrades to a warning (non-blocking).
 *                              All other GSC errors (bad credential, API error,
 *                              permission denied) are hard failures.
 *                              See CRAWLABILITY_RUNBOOK.md § 8 for setup steps.
 *   GSC_SITE_URL             — GSC property URL (default: https://syrabit.ai/)
 *   GSC_INDEXED_URL_FLOOR    — Minimum total indexed URL count (default: 50)
 *   GSC_DROP_THRESHOLD_PCT   — Day-over-day drop % that triggers a hard failure
 *                              (default: 10).  Requires UPSTASH_REDIS_REST_URL
 *                              and UPSTASH_REDIS_REST_TOKEN for persistence.
 *   UPSTASH_REDIS_REST_URL   — Upstash Redis REST endpoint (already in secrets)
 *   UPSTASH_REDIS_REST_TOKEN — Upstash Redis REST bearer token (already in secrets)
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

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Fetch a CF API path; retries on rate-limit (10429) with exponential backoff
// (2 s → 4 s → 8 s).  After all retries, pushes to warnings[] and throws a
// tagged error so cfGetOrSkip callers degrade gracefully (remaining checks run).
async function cfGet(path, { _attempt = 0 } = {}) {
  const res = await fetch(`${API}${path}`, { headers });
  const j = await res.json();
  if (!j.success && j.errors?.[0]?.code === 10429) {
    if (_attempt < 3) {
      const wait = 2000 * (2 ** _attempt);
      console.warn(`[rate-limit] 10429 on ${path} — waiting ${wait}ms (attempt ${_attempt + 1}/3)`);
      await sleep(wait);
      return cfGet(path, { _attempt: _attempt + 1 });
    }
    // All retries exhausted — push warning so the summary includes it, then throw
    // a tagged error so cfGetOrSkip can degrade gracefully instead of aborting the run.
    warnings.push(`CF API rate-limited on ${path} after 3 retries — re-run smoke checks later`);
    const rlErr = new Error(`CF rate-limited on ${path} after 3 retries`);
    rlErr._rate_limited = true;
    throw rlErr;
  }
  if (!j.success) throw new Error(`CF error on ${path}: ${JSON.stringify(j.errors)}`);
  return j;
}

// Returns null on auth error (10000) or rate-limit exhaustion (warning already pushed);
// throws for all other errors.
async function cfGetOrSkip(path) {
  try {
    return await cfGet(path);
  } catch (e) {
    if (e._rate_limited) return null;   // degrade gracefully — caller sees null like a scope gap
    const msg = e.message || '';
    // 10000 = Authentication error (token lacks the required scope)
    // 9109  = Unauthorized to access requested resource (same root cause)
    // 7003  = Could not route to /zones/... (zone not found for this token)
    if (
      msg.includes('"code":10000') || msg.includes('"code": 10000') ||
      msg.includes('"code":9109')  || msg.includes('"code": 9109')  ||
      msg.includes('"code":7003')  || msg.includes('"code": 7003')
    ) return null;
    throw e;
  }
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
  // Uses cfGetOrSkip so a token lacking Zone Settings: Read scope degrades
  // to a warning rather than aborting the run (matches Phase 2–6 behaviour).
  console.log('Phase 1 — Zone settings:');

  const sqsc = await cfGetOrSkip(`/zones/${ZONE_ID}/settings/sort_query_string_for_cache`);
  if (!sqsc) { warn('sort_query_string_for_cache', 'token lacks Zone Settings: Read'); }
  else { assert('sort_query_string_for_cache', sqsc.result.value, 'on'); }

  const tcip = await cfGetOrSkip(`/zones/${ZONE_ID}/settings/true_client_ip_header`);
  if (!tcip) { warn('true_client_ip_header', 'token lacks Zone Settings: Read'); }
  else { assert('true_client_ip_header', tcip.result.value, 'on'); }

  const ech  = await cfGetOrSkip(`/zones/${ZONE_ID}/settings/ech`);
  if (!ech) { warn('ech', 'token lacks Zone Settings: Read'); }
  else { assert('ech', ech.result.value, 'on'); }

  // ── Phase 1: Bot Management ───────────────────────────────────────────
  console.log('\nPhase 1 — Bot Management:');
  const bm = await cfGetOrSkip(`/zones/${ZONE_ID}/bot_management`);
  if (!bm) {
    warn('bot_management', 'token lacks Bot Management: Read — add scope to check');
  } else {
    assert('sbfm_likely_automated',   bm.result.sbfm_likely_automated,   'managed_challenge');
    assert('content_bots_protection', bm.result.content_bots_protection, 'block');
    // Task #259: sbfm_verified_bots MUST be 'allow' so Googlebot / Bingbot can
    // crawl. Any other value (e.g. 'block' or 'managed_challenge') silently drops
    // all verified-bot traffic and causes zero indexing even when sitemaps are
    // valid. This is asserted as a hard failure (not a warning) because it is the
    // single most impactful crawlability knob in the Cloudflare dashboard.
    assert('sbfm_verified_bots',      bm.result.sbfm_verified_bots,      'allow');
  }

  // ── Phase 1: DMARC ────────────────────────────────────────────────────
  console.log('\nPhase 1 — DMARC:');
  const dns = await cfGetOrSkip(`/zones/${ZONE_ID}/dns_records?name=_dmarc.syrabit.ai&type=TXT`);
  if (!dns) {
    warn('_dmarc.syrabit.ai DMARC', 'token lacks DNS: Read — add scope to check');
  } else if (!dns.result.length) {
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
      warnings.push('R2 bucket syrabit-cache-reserve NOT FOUND — run cloudflare-phase4-apply.js to create it');
      console.log('  ⚠  R2 bucket syrabit-cache-reserve: NOT FOUND — run cloudflare-phase4-apply.js');
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
  const WORKER_NAME = 'syrabitworker';
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

  // ── Phase 6: Image Resizing, Zaraz GA4, Observatory ───────────────────
  // These resources are provisioned by cloudflare-phase6-apply.js.
  // All checks degrade to warnings on token scope gaps (code 10000) or
  // plan-restriction errors (code 1135) so CI doesn't block on new accounts.
  //
  // The Railway-origin mTLS sub-checks (6a-i…6a-iv) were removed in
  // Task #335 when Railway was decommissioned. The corresponding
  // Cloudflare client certificate (`syrabit-railway-mtls`), worker
  // binding (`MTLS_CERT`), worker secret (`MTLS_REQUIRED`), and the
  // RAILWAY_ORIGIN_URL bypass probe are no longer relevant; delete the
  // Cloudflare cert and the GitHub Actions secret as part of the same
  // cleanup. See docs/infra/decommission.md.
  console.log('\nPhase 6 — Image Resizing, Zaraz GA4, Observatory:');
  /* mTLS-related Phase 6 checks (cert lookup, worker MTLS_CERT binding,
     MTLS_REQUIRED secret, RAILWAY_ORIGIN_URL bypass probe) intentionally
     removed in Task #335 along with the Railway origin. */

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
      warnings.push('Observatory alert policy (speed_insights) NOT FOUND — run cloudflare-phase6-apply.js (step 4b creates it)');
      console.log('  ⚠  Observatory alert policy: NOT FOUND — run cloudflare-phase6-apply.js (step 4b creates it)');
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
        warnings.push('Observatory alert policy has no Slack/webhook mechanism — on-call will not be paged (email only)');
        console.log('  ⚠  Observatory alert policy: no Slack/webhook mechanism found');
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
      warnings.push(`Observatory schedule for ${url} (NOT FOUND — run cloudflare-phase6-apply.js to create it)`);
      console.log(`  ⚠  Observatory schedule (${label}): NOT FOUND — run cloudflare-phase6-apply.js`);
    }
  }

  // ── Task #87: Cloudflare token scope verification ────────────────────
  // Ports verify_cf_tokens.sh into the nightly smoke pipeline so permission
  // regressions (e.g. a rotated token that lost the Load Balancer:Read scope)
  // surface overnight — 12 months before they would fail the annual review.
  //
  //   Token 1  CLOUDFLARE_API_TOKEN       → GET /user/tokens/verify
  //   Token 2  CLOUDFLARE_ANALYTICS_TOKEN → GET /accounts/{id}/vectorize/v2/indexes
  //   Token 3  CLOUDFLARE_PAGES_TOKEN     → GET /accounts/{id}/pages/projects
  //   Scope 4  LB Read (zone)             → GET /zones/{id}/load_balancers
  //   Scope 5  LB Read (account)          → GET /accounts/{id}/load_balancers/pools
  //
  // To fix a FAIL: go to dash.cloudflare.com/profile/api-tokens, edit the
  // named token, add the missing permission, save, then re-run this script.
  console.log('\nTask #87 — CF token scope verification:');
  {
    const CF_ANALYTICS_TOKEN = process.env.CLOUDFLARE_ANALYTICS_TOKEN || '';
    const CF_PAGES_TOKEN     = process.env.CLOUDFLARE_PAGES_TOKEN     ||
                               process.env.CF_PAGES_API_TOKEN         || '';

    // Helper: probe a URL with a given bearer token; never throws.
    async function cfTokenProbe(url, token) {
      try {
        const r = await fetch(url, {
          headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
          signal: AbortSignal.timeout(15000),
        });
        return { ok: r.status >= 200 && r.status < 300, status: r.status };
      } catch (e) {
        return { ok: false, status: 0 };
      }
    }

    // 1) Deploy token — CLOUDFLARE_API_TOKEN (already required; probe verifies it is
    //    still valid in isolation, not just used as-is throughout the script).
    {
      const r = await cfTokenProbe(`${API}/user/tokens/verify`, TOKEN);
      const mark = r.ok ? '✓' : '✗';
      console.log(`  ${mark}  CLOUDFLARE_API_TOKEN (deploy/Wrangler): HTTP ${r.status}`);
      if (!r.ok) failures.push(
        `CLOUDFLARE_API_TOKEN validity probe failed (HTTP ${r.status}) — token may be revoked or malformed`,
      );
    }

    // 2) Runtime token — CLOUDFLARE_ANALYTICS_TOKEN (Vectorize, cache purge).
    if (!CF_ANALYTICS_TOKEN) {
      warn(
        'CLOUDFLARE_ANALYTICS_TOKEN (runtime/Vectorize)',
        'env var not set — backend Vectorize REST calls will fail at runtime. ' +
        'Set the token in CI secrets.',
      );
    } else {
      const r = await cfTokenProbe(
        `${API}/accounts/${ACCOUNT_ID}/vectorize/v2/indexes`,
        CF_ANALYTICS_TOKEN,
      );
      const mark = r.ok ? '✓' : '✗';
      console.log(`  ${mark}  CLOUDFLARE_ANALYTICS_TOKEN (runtime/Vectorize): HTTP ${r.status}`);
      if (!r.ok) failures.push(
        `CLOUDFLARE_ANALYTICS_TOKEN probe failed (HTTP ${r.status}) — ` +
        `token may be missing Account > Vectorize:Read. ` +
        `Edit at dash.cloudflare.com/profile/api-tokens.`,
      );
    }

    // 3) Pages CI token — CLOUDFLARE_PAGES_TOKEN (wrangler pages deploy).
    if (!CF_PAGES_TOKEN) {
      warn(
        'CLOUDFLARE_PAGES_TOKEN (Pages CI)',
        'env var not set — Pages deploys will fall back to the legacy CF_PAGES_API_TOKEN ' +
        'if present, but the spec-named var should be set. ' +
        'Set CLOUDFLARE_PAGES_TOKEN in CI secrets.',
      );
    } else {
      const r = await cfTokenProbe(
        `${API}/accounts/${ACCOUNT_ID}/pages/projects`,
        CF_PAGES_TOKEN,
      );
      const mark = r.ok ? '✓' : '✗';
      console.log(`  ${mark}  CLOUDFLARE_PAGES_TOKEN (Pages CI): HTTP ${r.status}`);
      if (!r.ok) failures.push(
        `CLOUDFLARE_PAGES_TOKEN probe failed (HTTP ${r.status}) — ` +
        `token may be missing Account > Cloudflare Pages:Edit. ` +
        `Edit at dash.cloudflare.com/profile/api-tokens.`,
      );
    }

    // 4 & 5) Load Balancer Read scope on CLOUDFLARE_API_TOKEN — Task #76.
    //   The 2026 annual review (Task #66) hit a 403 here because LB:Read was
    //   absent.  Both zone-level and account-level probes must pass.
    //   Fix: add "Zone > Load Balancer: Read" and "Account > Load Balancer: Read"
    //   to CLOUDFLARE_API_TOKEN at dash.cloudflare.com/profile/api-tokens.
    if (!ZONE_ID) {
      warn(
        'LB Read scope — zone probe (Task #76)',
        'CLOUDFLARE_ZONE_ID not set — set to 5b8c97df4431491dc7f60ea72fb61871 for the full probe',
      );
    } else {
      const r = await cfTokenProbe(`${API}/zones/${ZONE_ID}/load_balancers`, TOKEN);
      const mark = r.ok ? '✓' : '⚠';
      console.log(`  ${mark}  CLOUDFLARE_API_TOKEN — LB Read scope (zone): HTTP ${r.status}`);
      if (!r.ok) warnings.push(
        `CLOUDFLARE_API_TOKEN missing Load Balancer:Read scope (zone) — HTTP ${r.status}. ` +
        `Add "Zone > Load Balancer: Read" at dash.cloudflare.com/profile/api-tokens.`,
      );
    }
    {
      const r = await cfTokenProbe(
        `${API}/accounts/${ACCOUNT_ID}/load_balancers/pools`,
        TOKEN,
      );
      const mark = r.ok ? '✓' : '⚠';
      console.log(`  ${mark}  CLOUDFLARE_API_TOKEN — LB Read scope (account): HTTP ${r.status}`);
      if (!r.ok) warnings.push(
        `CLOUDFLARE_API_TOKEN missing Load Balancer:Read scope (account) — HTTP ${r.status}. ` +
        `Add "Account > Load Balancer: Read" at dash.cloudflare.com/profile/api-tokens.`,
      );
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
      const mark = (r.status === 200 && isXml) ? '✓' : '⚠';
      const detail = `HTTP ${r.status}  Content-Type: ${ct || '(none)'}`;
      console.log(`  ${mark}  ${path}  ${detail}`);
      if (r.status !== 200) {
        warnings.push(`Sitemap ${path}: HTTP ${r.status} (want 200) — Worker SEO_PASSTHROUGH_RE or backend route may be misconfigured`);
      } else if (isHtml) {
        warnings.push(`Sitemap ${path}: Content-Type is text/html — Worker proxy gap; Googlebot receives SPA shell instead of XML`);
      } else if (!isXml) {
        warnings.push(`Sitemap ${path}: Content-Type "${ct}" is not application/xml`);
      }
    } catch (e) {
      const msg = e.message || String(e);
      warn(`Sitemap HEAD ${path}`, msg.includes('abort') || msg.includes('timeout') || msg.includes('timed out')
        ? 'request timed out — site may be warming up; re-run smoke after 60 s'
        : `fetch failed — ${msg}`);
    }
  }

  // ── Task #262: Google Search Console Coverage check ──────────────────
  // Uses the GSC Webmasters API (service account JWT auth) to read the
  // Sitemaps report and assert:
  //   (a) total indexed URL count >= GSC_INDEXED_URL_FLOOR (absolute floor)
  //   (b) day-over-day drop does not exceed GSC_DROP_THRESHOLD_PCT (default 10%)
  // The previous count is persisted in Upstash Redis between CI runs.
  //
  // Failure semantics (per code-review #262):
  //   • GSC_SERVICE_ACCOUNT_JSON missing → warn() only (non-blocking)
  //   • All other errors (bad credential, JWT failure, API error, 403,
  //     no sitemaps found) → failures.push() (hard failure)
  // See CRAWLABILITY_RUNBOOK.md § 8 for setup steps.
  {
    const GSC_SA_JSON       = process.env.GSC_SERVICE_ACCOUNT_JSON || '';
    const GSC_SITE_URL      = process.env.GSC_SITE_URL      || 'https://syrabit.ai/';
    const _gscFloorRaw  = process.env.GSC_INDEXED_URL_FLOOR  || '50';
    const _gscDropRaw   = process.env.GSC_DROP_THRESHOLD_PCT || '10';
    const GSC_INDEXED_FLOOR = parseInt(_gscFloorRaw,  10);
    const GSC_DROP_PCT      = parseInt(_gscDropRaw,   10);
    if (isNaN(GSC_INDEXED_FLOOR) || GSC_INDEXED_FLOOR < 0) {
      failures.push(`GSC Coverage: GSC_INDEXED_URL_FLOOR="${_gscFloorRaw}" is not a valid non-negative integer`);
    }
    if (isNaN(GSC_DROP_PCT) || GSC_DROP_PCT < 0 || GSC_DROP_PCT > 100) {
      failures.push(`GSC Coverage: GSC_DROP_THRESHOLD_PCT="${_gscDropRaw}" must be an integer 0–100`);
    }
    const REDIS_URL         = process.env.UPSTASH_REDIS_REST_URL            || '';
    const REDIS_TOKEN       = process.env.UPSTASH_REDIS_REST_TOKEN          || '';
    const REDIS_KEY         = 'gsc_indexed_count';

    console.log('\nTask #262 — GSC Coverage report check:');

    if (!GSC_SA_JSON) {
      warn(
        'GSC Coverage check',
        'GSC_SERVICE_ACCOUNT_JSON not set — skipping. ' +
        'Set the env var to activate nightly indexing regression detection ' +
        '(see CRAWLABILITY_RUNBOOK.md § 8).',
      );
    } else {
      // Helper: read previous indexed count from Upstash Redis.
      // Returns null when Redis is not configured or the key doesn't exist yet.
      // Emits a warning (not a failure) when Redis is configured but the
      // request fails — this makes delta-check degradation visible in CI logs.
      async function rediGet() {
        if (!REDIS_URL || !REDIS_TOKEN) return null;
        try {
          const r = await fetch(`${REDIS_URL}/get/${REDIS_KEY}`, {
            headers: { Authorization: `Bearer ${REDIS_TOKEN}` },
            signal: AbortSignal.timeout(5000),
          });
          if (!r.ok) {
            warn('GSC Coverage (Redis GET)', `HTTP ${r.status} — delta check will be skipped this run`);
            return null;
          }
          const j = await r.json();
          const v = parseInt(j.result, 10);
          return isNaN(v) ? null : v;
        } catch (e) {
          warn('GSC Coverage (Redis GET)', `${e.message} — delta check will be skipped this run`);
          return null;
        }
      }

      // Helper: persist current indexed count to Upstash Redis.
      // Skips silently when Redis is not configured; warns on error so
      // a broken write doesn't silently disable future delta checks.
      async function rediSet(value) {
        if (!REDIS_URL || !REDIS_TOKEN) return;
        try {
          const r = await fetch(`${REDIS_URL}/set/${REDIS_KEY}/${value}`, {
            headers: { Authorization: `Bearer ${REDIS_TOKEN}` },
            signal: AbortSignal.timeout(5000),
          });
          if (!r.ok) {
            warn('GSC Coverage (Redis SET)', `HTTP ${r.status} — next run's delta check may use a stale baseline`);
          }
        } catch (e) {
          warn('GSC Coverage (Redis SET)', `${e.message} — next run's delta check may use a stale baseline`);
        }
      }

      try {
        // ── Step 1: Parse and validate service account creds ─────────────
        let saCreds;
        try {
          saCreds = JSON.parse(GSC_SA_JSON);
        } catch (e) {
          failures.push(`GSC Coverage: GSC_SERVICE_ACCOUNT_JSON is not valid JSON — ${e.message}`);
          throw null; // jump to catch, which re-throws only non-null
        }
        const { client_email, private_key } = saCreds;
        if (!client_email || !private_key) {
          failures.push('GSC Coverage: GSC_SERVICE_ACCOUNT_JSON is missing client_email or private_key');
          throw null;
        }

        // ── Step 2: Build RS256-signed JWT (no external deps) ─────────────
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
        const sigInput  = `${jwtHeader}.${jwtClaims}`;
        let sig;
        try {
          sig = nodeCrypto.createSign('RSA-SHA256').update(sigInput).sign(private_key, 'base64url');
        } catch (e) {
          failures.push(`GSC Coverage: JWT signing failed — private_key may be malformed: ${e.message}`);
          throw null;
        }
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
          failures.push(
            `GSC Coverage: OAuth2 token exchange failed (HTTP ${tokenRes.status}) — ` +
            `${tokenJson.error || JSON.stringify(tokenJson)}. ` +
            'Check that the service account key is not expired/revoked.',
          );
          throw null;
        }

        // ── Step 4: Fetch the Sitemaps report ─────────────────────────────
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
          failures.push(
            `GSC Coverage: 403 Forbidden — service account "${client_email}" ` +
            `needs "Restricted" (read) access on property "${GSC_SITE_URL}". ` +
            'Grant access via GSC → Settings → Users and permissions ' +
            '(CRAWLABILITY_RUNBOOK.md § 8c).',
          );
          console.log('  ✗  GSC Sitemaps API: 403 Forbidden');
          throw null;
        } else if (!sitemapsRes.ok) {
          failures.push(
            `GSC Coverage: Sitemaps API HTTP ${sitemapsRes.status} — ` +
            `${JSON.stringify(sitemapsJson)}`,
          );
          throw null;
        }

        const sitemaps = sitemapsJson.sitemap || [];
        if (sitemaps.length === 0) {
          failures.push(
            `GSC Coverage: no sitemaps found on property "${GSC_SITE_URL}" — ` +
            'submit sitemap-index.xml via GSC dashboard (CRAWLABILITY_RUNBOOK.md § 2)',
          );
          console.log('  ✗  GSC: no sitemaps registered');
          throw null;
        }

        // ── Step 5: Sum indexed counts ────────────────────────────────────
        // Parent sitemap-index rows typically report 0; child sitemaps carry
        // the real per-type counts. Sum all contents[].indexed entries.
        let totalIndexed   = 0;
        let totalSubmitted = 0;
        for (const sm of sitemaps) {
          for (const c of (sm.contents || [])) {
            totalIndexed   += parseInt(c.indexed   || '0', 10);
            totalSubmitted += parseInt(c.submitted || '0', 10);
          }
        }

        // ── Step 6: Absolute floor check ──────────────────────────────────
        const floorOk = totalIndexed >= GSC_INDEXED_FLOOR;
        console.log(
          `  ${floorOk ? '✓' : '✗'}  Indexed URLs: ${totalIndexed} / submitted: ${totalSubmitted}` +
          `  (floor: ${GSC_INDEXED_FLOOR})`,
        );
        if (!floorOk) {
          failures.push(
            `GSC Coverage: indexed URL count ${totalIndexed} < floor ${GSC_INDEXED_FLOOR} ` +
            `— possible indexing regression; check GSC Coverage report for "${GSC_SITE_URL}"`,
          );
        }

        // ── Step 7: Day-over-day delta check (via Upstash Redis) ──────────
        const prevCount = await rediGet();
        if (prevCount === null) {
          if (!REDIS_URL || !REDIS_TOKEN) {
            console.log('  ℹ  Delta check skipped — UPSTASH_REDIS_REST_URL/TOKEN not set');
          } else {
            console.log(`  ℹ  Delta check: no baseline stored yet — seeding with ${totalIndexed}`);
          }
        } else {
          const dropPct = prevCount > 0
            ? Math.round(((prevCount - totalIndexed) / prevCount) * 100)
            : 0;
          const deltaOk = dropPct <= GSC_DROP_PCT;
          console.log(
            `  ${deltaOk ? '✓' : '✗'}  Delta: ${prevCount} → ${totalIndexed}` +
            ` (${dropPct > 0 ? '-' : '+'}${Math.abs(dropPct)}%  threshold: ${GSC_DROP_PCT}%)`,
          );
          if (!deltaOk) {
            failures.push(
              `GSC Coverage: indexed URL count dropped ${dropPct}% overnight ` +
              `(${prevCount} → ${totalIndexed}), exceeding ${GSC_DROP_PCT}% threshold ` +
              `— check for new noindex tags, robots.txt changes, or soft-404 waves`,
            );
          }
        }
        // Always update the stored baseline after a successful API read.
        await rediSet(totalIndexed);

        // ── Per-sitemap detail ─────────────────────────────────────────────
        for (const sm of sitemaps) {
          const smIdx = (sm.contents || []).reduce((s, c) => s + parseInt(c.indexed   || '0', 10), 0);
          const smSub = (sm.contents || []).reduce((s, c) => s + parseInt(c.submitted || '0', 10), 0);
          const smErr = parseInt(sm.errors || '0', 10);
          const errNote = smErr > 0 ? `  errors=${smErr}` : '';
          console.log(`       ${sm.path}: submitted=${smSub} indexed=${smIdx}${errNote}`);
        }

      } catch (e) {
        // null sentinel means we already pushed a failure message above; skip.
        if (e !== null) {
          const msg = e.message || String(e);
          if (msg.includes('abort') || msg.includes('timeout') || msg.includes('timed out')) {
            failures.push('GSC Coverage: request timed out — GSC API unreachable from this runner');
            console.log('  ✗  GSC: request timed out');
          } else {
            failures.push(`GSC Coverage: unexpected error — ${msg}`);
            console.log(`  ✗  GSC: ${msg}`);
          }
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
