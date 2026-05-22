#!/usr/bin/env node
/* eslint-disable no-console */
/**
 * post-deploy-lighthouse.js
 *
 * Task #131 — Trigger a Cloudflare Observatory Lighthouse run after every
 * production Pages deploy and fail the pipeline if Core Web Vitals regress.
 *
 * What it does:
 *   1. Polls the Cloudflare Pages API for the deployment whose commit SHA
 *      matches COMMIT_SHA (populated from GITHUB_SHA in CI).  This ensures the
 *      check always measures the specific commit being deployed, not a previous
 *      release that already reached "success".  Waits until that deployment
 *      itself reaches "success" before proceeding.
 *   2. Fires POST /zones/{id}/speed/tests for the homepage and chapter page.
 *   3. Polls GET /zones/{id}/speed/tests?url={url} until the new test result
 *      is available (up to LIGHTHOUSE_POLL_TIMEOUT_MS, default 10 min).
 *   4. Checks LCP, CLS, and INP against the "needs improvement" thresholds:
 *        LCP > 2500 ms  →  fail
 *        CLS > 0.1      →  fail
 *        INP > 200 ms   →  fail
 *      Missing or unavailable metrics are also treated as failures so a
 *      Lighthouse run that couldn't measure a vital doesn't produce a false green.
 *   5. Prints a pass/fail table and exits 0 (all green) or 1 (any breach).
 *
 * Emergency bypass:
 *   Set SKIP_LIGHTHOUSE=1 to skip the entire script and exit 0.  Use only for
 *   hotfixes where a known regression is already tracked.  Set the env var in
 *   the GitHub Actions workflow via the Actions UI → "Run workflow" → inputs,
 *   or per the instructions in docs/CLOUDFLARE_OBSERVATORY.md.
 *
 * Required env:
 *   CLOUDFLARE_API_TOKEN  — needs Speed (Observatory): Edit + Read and
 *                           Cloudflare Pages: Read scopes.
 *   COMMIT_SHA            — git SHA of the commit being deployed; in CI this
 *                           is set from GITHUB_SHA.  Without it, the script
 *                           falls back to watching the newest deployment
 *                           (weaker guarantee; a warning is emitted).
 *
 * Optional env:
 *   CLOUDFLARE_ZONE_ID          — defaults to the syrabit.ai zone
 *   CLOUDFLARE_ACCOUNT_ID       — defaults to the Syrabit account
 *   CLOUDFLARE_PAGES_PROJECT    — Pages project name (default: syrabit-analytics)
 *   SKIP_PAGES_WAIT             — set to "1" to skip polling Pages for deploy
 *                                 completion (use for local runs or manual
 *                                 re-runs after a Pages poll timeout)
 *   SKIP_LIGHTHOUSE             — set to "1" to bypass the entire script (emergency)
 *   LIGHTHOUSE_POLL_TIMEOUT_MS  — max ms to wait for a test result (default: 600000)
 *   LIGHTHOUSE_POLL_INTERVAL_MS — polling interval ms (default: 15000)
 *   LIGHTHOUSE_REGION           — Cloudflare region for the test (default: us-central1)
 *   PAGES_POLL_TIMEOUT_MS       — max ms to wait for Pages deploy (default: 1200000)
 *   PAGES_POLL_INTERVAL_MS      — Pages polling interval ms (default: 20000)
 *
 * Usage:
 *   CLOUDFLARE_API_TOKEN=<tok> COMMIT_SHA=<sha> \
 *     node artifacts/syrabit/scripts/post-deploy-lighthouse.js
 *
 *   Local test (skip Pages wait, measure live site):
 *   CLOUDFLARE_API_TOKEN=<tok> SKIP_PAGES_WAIT=1 \
 *     node artifacts/syrabit/scripts/post-deploy-lighthouse.js
 *
 * Exit codes:
 *   0  — all Lighthouse scores within thresholds (or SKIP_LIGHTHOUSE=1)
 *   1  — one or more scores breach a threshold, metric unavailable, or
 *         an unrecoverable API error
 */

import { fileURLToPath } from 'node:url';

const TOKEN           = process.env.CLOUDFLARE_API_TOKEN;
const ZONE_ID         = process.env.CLOUDFLARE_ZONE_ID         || '5b8c97df4431491dc7f60ea72fb61871';
const ACCOUNT_ID      = process.env.CLOUDFLARE_ACCOUNT_ID      || 'd66e40eac539fff1db270fddf384a5ec';
const PAGES_PROJECT   = process.env.CLOUDFLARE_PAGES_PROJECT   || 'syrabit-analytics';
const COMMIT_SHA      = process.env.COMMIT_SHA                 || process.env.GITHUB_SHA || '';
const SKIP_PAGES_WAIT = process.env.SKIP_PAGES_WAIT            === '1';
const SKIP_LIGHTHOUSE = process.env.SKIP_LIGHTHOUSE            === '1';
const REGION          = process.env.LIGHTHOUSE_REGION          || 'us-central1';

const LIGHTHOUSE_POLL_TIMEOUT_MS  = Number(process.env.LIGHTHOUSE_POLL_TIMEOUT_MS)  || 600_000;
const LIGHTHOUSE_POLL_INTERVAL_MS = Number(process.env.LIGHTHOUSE_POLL_INTERVAL_MS) || 15_000;
const PAGES_POLL_TIMEOUT_MS       = Number(process.env.PAGES_POLL_TIMEOUT_MS)       || 1_200_000;
const PAGES_POLL_INTERVAL_MS      = Number(process.env.PAGES_POLL_INTERVAL_MS)      || 20_000;

const API = 'https://api.cloudflare.com/client/v4';

const TARGETS = [
  { label: 'homepage',     url: 'https://syrabit.ai/' },
  { label: 'chapter page', url: 'https://syrabit.ai/ahsec/class-12/physics' },
];

const THRESHOLDS = {
  lcp: { limit: 2500, unit: 'ms', description: 'Largest Contentful Paint' },
  cls: { limit: 0.1,  unit: '',   description: 'Cumulative Layout Shift'  },
  inp: { limit: 200,  unit: 'ms', description: 'Interaction to Next Paint' },
};

// ─── helpers ──────────────────────────────────────────────────────────────────

function ok(msg)   { console.log(`  ✓  ${msg}`); }
function fail(msg) { console.log(`  ✗  ${msg}`); }
function info(msg) { console.log(`  ℹ  ${msg}`); }
function warn(msg) { console.log(`  ⚠  ${msg}`); }

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function elapsed(startMs) {
  return `${((Date.now() - startMs) / 1000).toFixed(1)}s`;
}

const authHeaders = () => ({
  'Authorization': `Bearer ${TOKEN}`,
  'Content-Type': 'application/json',
});

async function cfGet(path) {
  const res = await fetch(`${API}${path}`, { headers: authHeaders() });
  return res.json();
}

async function cfPost(path, body) {
  const res = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  return res.json();
}

/**
 * Return true if `deployment` was triggered by `commitSha`.
 * The Cloudflare Pages API surfaces the triggering commit under
 * deployment_trigger.metadata.commit_hash (full SHA).
 */
function deployMatchesCommit(deployment, commitSha) {
  const hash = deployment?.deployment_trigger?.metadata?.commit_hash || '';
  // Accept full SHA match or the CI-provided SHA starting with the stored one
  // (some Pages versions truncate to 12 chars; accept either direction).
  if (!hash || !commitSha) return false;
  return hash === commitSha
    || commitSha.startsWith(hash)
    || hash.startsWith(commitSha);
}

// ─── Step 1: Wait for the commit's Pages deploy to reach "success" ─────────────

async function waitForPagesDeploySuccess() {
  if (SKIP_PAGES_WAIT) {
    info('SKIP_PAGES_WAIT=1 — skipping Pages deploy poll; assuming deploy is already live.');
    return;
  }

  if (COMMIT_SHA) {
    console.log(`\n── Waiting for Pages deploy: ${PAGES_PROJECT} (commit ${COMMIT_SHA.slice(0, 12)}) ──`);
    info(`Polling every ${PAGES_POLL_INTERVAL_MS / 1000}s, timeout ${PAGES_POLL_TIMEOUT_MS / 1000}s`);
  } else {
    warn('COMMIT_SHA / GITHUB_SHA is not set — falling back to newest deployment.');
    warn('This is less reliable: the most-recent successful deploy may belong to a different commit.');
    info('In CI this variable is set automatically from GITHUB_SHA; pass it explicitly for local runs.');
    console.log(`\n── Waiting for Pages deploy: ${PAGES_PROJECT} (newest) ──`);
  }

  const deadline = Date.now() + PAGES_POLL_TIMEOUT_MS;
  const startMs  = Date.now();

  while (Date.now() < deadline) {
    // Fetch recent deployments (per_page=10 so we can scan back a bit in case
    // the commit's deployment was created slightly before we started polling).
    const r = await cfGet(
      `/accounts/${ACCOUNT_ID}/pages/projects/${PAGES_PROJECT}/deployments?env=production&per_page=10`,
    );

    if (!r.success) {
      const code = r.errors?.[0]?.code;
      if (code === 10000) {
        warn('Pages API: token lacks "Cloudflare Pages: Read" scope — skipping deploy poll.');
        warn('The Lighthouse run will proceed without confirming the deploy is live for this commit.');
        return;
      }
      warn(`Pages API error (will retry): ${JSON.stringify(r.errors)}`);
      await sleep(PAGES_POLL_INTERVAL_MS);
      continue;
    }

    const deployments = r.result || [];

    if (deployments.length === 0) {
      info(`No Pages deployments found yet — retrying… (${elapsed(startMs)} elapsed)`);
      await sleep(PAGES_POLL_INTERVAL_MS);
      continue;
    }

    // When COMMIT_SHA is known, look for the deployment that matches it.
    // Fall back to the newest deployment when SHA is unavailable.
    const target = COMMIT_SHA
      ? deployments.find(d => deployMatchesCommit(d, COMMIT_SHA))
      : deployments[0];

    if (!target) {
      // Deployment for this commit hasn't been created yet — keep waiting.
      info(`Deployment for commit ${COMMIT_SHA.slice(0, 12)} not found yet — retrying… (${elapsed(startMs)} elapsed)`);
      await sleep(PAGES_POLL_INTERVAL_MS);
      continue;
    }

    const status = target.latest_stage?.status;
    const shortId = target.id?.slice(0, 8) ?? '?';
    info(`Deploy ${shortId} (commit ${(target.deployment_trigger?.metadata?.commit_hash || '?').slice(0, 12)}) status: ${status ?? '?'} (${elapsed(startMs)} elapsed)`);

    if (status === 'success') {
      ok(`Pages deploy ${target.id} reached "success" in ${elapsed(startMs)}`);
      return;
    }
    if (status === 'failure') {
      fail(`Pages deploy ${target.id} failed — aborting Lighthouse check (nothing to measure).`);
      process.exit(1);
    }

    // status is active / idle / queued / running — keep polling.
    await sleep(PAGES_POLL_INTERVAL_MS);
  }

  // Timeout: the deploy did not finish in time.  Running Lighthouse now would
  // measure whatever version was previously deployed, missing any regression
  // introduced by the current commit.  Fail hard.
  fail(`Pages deploy did not reach "success" within ${PAGES_POLL_TIMEOUT_MS / 1000}s — aborting.`);
  console.log('');
  console.log('  If the deploy eventually succeeded, re-run this workflow via:');
  console.log('  Actions → post-deploy-lighthouse → Run workflow → skip_pages_wait = true');
  process.exit(1);
}

// ─── Step 2: Trigger a Lighthouse test ────────────────────────────────────────

async function triggerTest(url) {
  const r = await cfPost(`/zones/${ZONE_ID}/speed/tests`, { url, region: REGION });
  if (!r.success) {
    throw new Error(`Failed to trigger test for ${url}: ${JSON.stringify(r.errors)}`);
  }
  const testId = r.result?.id;
  if (!testId) {
    throw new Error(`No test id returned for ${url}: ${JSON.stringify(r.result)}`);
  }
  return testId;
}

// ─── Step 3: Poll until the test result is available ─────────────────────────

/**
 * The Cloudflare Observatory API returns tests for a URL via:
 *   GET /zones/{zone_id}/speed/tests?url={encoded_url}
 *
 * Each entry has:
 *   id, date, region, scheduleFrequency, lhr (Lighthouse JSON report)
 *
 * We watch for the entry whose id matches the one we just triggered.
 * "lhr" being non-null signals the test is complete.
 */
async function pollForResult(url, testId) {
  const deadline = Date.now() + LIGHTHOUSE_POLL_TIMEOUT_MS;
  const startMs  = Date.now();
  let   attempt  = 0;

  while (Date.now() < deadline) {
    attempt++;
    await sleep(LIGHTHOUSE_POLL_INTERVAL_MS);

    const r = await cfGet(
      `/zones/${ZONE_ID}/speed/tests?url=${encodeURIComponent(url)}`,
    );

    if (!r.success) {
      warn(`Poll attempt ${attempt}: API error: ${JSON.stringify(r.errors)} — retrying…`);
      continue;
    }

    const tests = Array.isArray(r.result) ? r.result : [];
    const entry = tests.find(t => t.id === testId);

    if (!entry) {
      info(`Poll attempt ${attempt}: test ${testId} not yet visible (${elapsed(startMs)} elapsed) — waiting…`);
      continue;
    }

    if (entry.lhr) {
      ok(`Test ${testId} complete after ${elapsed(startMs)} (${attempt} poll${attempt !== 1 ? 's' : ''})`);
      return entry.lhr;
    }

    const state = entry.status || entry.latest_stage?.status || 'pending';
    info(`Poll attempt ${attempt}: test state="${state}" (${elapsed(startMs)} elapsed) — waiting…`);
  }

  throw new Error(
    `Timed out waiting for Lighthouse result for ${url} after ${LIGHTHOUSE_POLL_TIMEOUT_MS / 1000}s`,
  );
}

// ─── Step 4: Extract CWV metrics from the Lighthouse report ──────────────────

/**
 * Extract LCP (ms), CLS (dimensionless), and INP (ms) from a Lighthouse
 * report object (the `lhr` field from the Observatory API response).
 *
 * Lighthouse report structure:
 *   lhr.audits['largest-contentful-paint'].numericValue  (ms)
 *   lhr.audits['cumulative-layout-shift'].numericValue
 *   lhr.audits['interaction-to-next-paint'].numericValue (ms)
 *
 * Returns undefined for any audit that is missing from the report; callers
 * must treat undefined as a hard failure (see checkThresholds).
 */
function extractMetrics(lhr) {
  const audits = lhr?.audits || {};
  return {
    lcp: audits['largest-contentful-paint']?.numericValue,
    cls: audits['cumulative-layout-shift']?.numericValue,
    inp: audits['interaction-to-next-paint']?.numericValue,
  };
}

// ─── Step 5: Compare metrics against thresholds ───────────────────────────────

/**
 * Compare extracted metrics against the defined thresholds.
 * A missing / undefined metric is treated as a FAILURE — a Lighthouse run that
 * couldn't capture a Core Web Vital must not produce a false green.
 */
function checkThresholds(label, metrics) {
  const results = {};
  let breached = false;

  for (const [key, cfg] of Object.entries(THRESHOLDS)) {
    const value = metrics[key];

    if (value === undefined || value === null) {
      fail(`${label}: ${cfg.description} (${key.toUpperCase()}) was not captured by this Lighthouse run — treating as failure.`);
      info(`  This can happen when the page failed to load or the Observatory API did not return the audit.`);
      results[key] = { status: 'unavailable', value, limit: cfg.limit };
      breached = true;
      continue;
    }

    const passed = value <= cfg.limit;
    const displayValue = cfg.unit === 'ms'
      ? `${(value / 1000).toFixed(2)} s`
      : value.toFixed(3);
    const displayLimit = cfg.unit === 'ms'
      ? `${cfg.limit / 1000} s`
      : cfg.limit;

    if (passed) {
      ok(`${label}: ${cfg.description} = ${displayValue} (limit ${displayLimit}) ✓`);
    } else {
      fail(`${label}: ${cfg.description} = ${displayValue} EXCEEDS limit ${displayLimit} ✗`);
      breached = true;
    }
    results[key] = { status: passed ? 'pass' : 'fail', value, limit: cfg.limit };
  }

  return { breached, results };
}

// ─── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  console.log('═══════════════════════════════════════════════════════════════');
  console.log(' post-deploy-lighthouse.js — Task #131');
  console.log(' Cloudflare Observatory post-deploy check for syrabit.ai');
  console.log('═══════════════════════════════════════════════════════════════');

  if (SKIP_LIGHTHOUSE) {
    console.log('\n  SKIP_LIGHTHOUSE=1 — bypassing Lighthouse check (emergency mode).');
    console.log('  To re-enable: unset SKIP_LIGHTHOUSE in the CI workflow or re-run');
    console.log('  without the flag. See docs/CLOUDFLARE_OBSERVATORY.md for details.');
    process.exit(0);
  }

  if (!TOKEN) {
    console.error('\n  CLOUDFLARE_API_TOKEN is not set — cannot call the Observatory API.');
    console.error('  Add it as a repository secret (CLOUDFLARE_API_TOKEN) and ensure the');
    console.error('  workflow passes it into this step.');
    process.exit(1);
  }

  // Step 1 — Wait for the specific commit's Pages deploy
  await waitForPagesDeploySuccess();

  // Steps 2–5 — Run Lighthouse for each target
  const summary = [];
  let anyBreached = false;

  for (const target of TARGETS) {
    console.log(`\n── Lighthouse: ${target.label} (${target.url}) ──`);

    let testId;
    try {
      info(`Triggering Observatory test in region ${REGION}…`);
      testId = await triggerTest(target.url);
      ok(`Test triggered: id=${testId}`);
    } catch (e) {
      fail(`Could not trigger test for ${target.label}: ${e.message}`);
      if (e.message.includes('"code":10000') || e.message.includes('"code": 10000')) {
        info('Token lacks "Speed (Observatory): Edit" scope — add it to the API token and retry.');
      }
      summary.push({ label: target.label, status: 'error', error: e.message });
      anyBreached = true;
      continue;
    }

    let lhr;
    try {
      info(`Waiting for result (timeout ${LIGHTHOUSE_POLL_TIMEOUT_MS / 1000}s)…`);
      lhr = await pollForResult(target.url, testId);
    } catch (e) {
      fail(`Polling timed out for ${target.label}: ${e.message}`);
      summary.push({ label: target.label, status: 'timeout', error: e.message });
      anyBreached = true;
      continue;
    }

    const metrics = extractMetrics(lhr);
    const { breached, results } = checkThresholds(target.label, metrics);
    if (breached) anyBreached = true;
    summary.push({ label: target.label, status: breached ? 'fail' : 'pass', results });
  }

  // Summary table
  console.log('\n── Summary ──────────────────────────────────────────────────────');
  for (const entry of summary) {
    if (entry.status === 'pass') {
      ok(`${entry.label}: all thresholds met`);
    } else if (entry.status === 'fail') {
      const failing = Object.entries(entry.results)
        .filter(([, r]) => r.status === 'fail' || r.status === 'unavailable')
        .map(([k, r]) => r.status === 'unavailable' ? `${k.toUpperCase()}(unavailable)` : k.toUpperCase())
        .join(', ');
      fail(`${entry.label}: ${failing}`);
    } else {
      fail(`${entry.label}: ${entry.status} — ${entry.error}`);
    }
  }

  if (anyBreached) {
    console.log('\n  ✗  One or more pages failed the Lighthouse threshold check.');
    console.log('     Investigate the regression before re-deploying.');
    console.log('     For an emergency hotfix that must ship immediately, set');
    console.log('     SKIP_LIGHTHOUSE=1 in the workflow and open a follow-up ticket.');
    console.log('     See docs/CLOUDFLARE_OBSERVATORY.md → "Disabling for hotfixes".');
    process.exit(1);
  } else {
    console.log('\n  ✓  All pages within Core Web Vitals thresholds. Deploy approved.');
    process.exit(0);
  }
}

// ─── Exports (for unit testing) ───────────────────────────────────────────────
// Pure functions that have no side-effects and do not touch the network.
// Tests import these directly without triggering the main() entry point.
export { extractMetrics, checkThresholds, deployMatchesCommit, THRESHOLDS, main };

// ─── Entry point guard ────────────────────────────────────────────────────────
// Only run main() when this file is executed directly (node script.js).
// Importing the file in tests does NOT trigger main(), so API calls and
// process.exit() are not invoked during the test run.
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(e => {
    console.error('\nUnhandled error:', e);
    process.exit(1);
  });
}
