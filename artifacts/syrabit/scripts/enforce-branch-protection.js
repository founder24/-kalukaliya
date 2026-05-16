#!/usr/bin/env node
/**
 * enforce-branch-protection.js
 *
 * Task #141  — Idempotently adds the post-deploy Lighthouse check as a
 *              required status check on the master/main branch protection rule.
 * Task #141b — Also enforces the backend-import-smoke check (all-tests workflow)
 *              so a broken server.py import cannot be merged.
 *
 * Required status check contexts (GitHub Actions format: "workflow / job"):
 *   "post-deploy-lighthouse / Lighthouse post-deploy check (LCP / CLS / INP)"
 *   "all-tests / Backend import smoke test"
 *
 * The script:
 *   1. Iterates over BRANCHES (comma-separated, default: "Replit-agent,main").
 *   2. For each branch: GETs the current protection rule.
 *        - 404 → branch exists but has no protection yet → creates a minimal rule.
 *        - 403 → PAT lacks admin permission → exits 1 immediately.
 *        - branch not found (422/404 on branch) → skips silently (repo may
 *          use only one of master/main).
 *   3. Checks whether ALL required contexts are already present → no-op if so.
 *   4. If any are missing: PUTs the updated rule, preserving *all* known
 *      settings from the GET response including:
 *        required_status_checks (strict, contexts)
 *        enforce_admins
 *        required_pull_request_reviews (all sub-fields)
 *        restrictions (users, teams, apps)
 *        required_linear_history
 *        allow_force_pushes
 *        allow_deletions
 *        block_creations
 *        required_conversation_resolution
 *        lock_branch
 *        allow_fork_syncing
 *
 * Required env vars:
 *   BRANCH_PROTECTION_TOKEN  — PAT with `repo` scope; the token owner must
 *                              have admin permission on the repository.
 *                              Do NOT use `secrets.GITHUB_TOKEN` (the built-in
 *                              Actions automation token) — it cannot write
 *                              branch protection rules.
 *   GITHUB_REPOSITORY        — "owner/repo" — set automatically by GitHub Actions.
 *
 * Optional env vars:
 *   BRANCHES  — comma-separated list of branches to update (default: "Replit-agent,main")
 *   DRY_RUN   — set to "1" to print the payload without sending it
 */

'use strict';

// All contexts that must be present as required status checks.
// Add new entries here; the script adds any that are missing in one PUT.
const REQUIRED_CONTEXTS = [
  // Task #141 — post-deploy Lighthouse perf gate (LCP / CLS / INP)
  'post-deploy-lighthouse / Lighthouse post-deploy check (LCP / CLS / INP)',
  // Task #141b — Python backend import smoke test (all-tests workflow)
  'all-tests / Backend import smoke test',
];

function env(name, fallback) {
  const val = process.env[name];
  if (!val && fallback === undefined) {
    console.error(`Error: environment variable ${name} is not set.`);
    process.exit(1);
  }
  return val || fallback;
}

// Build headers using the PAT secret (BRANCH_PROTECTION_TOKEN).
function headers() {
  const token = env('BRANCH_PROTECTION_TOKEN');
  return {
    Authorization: `Bearer ${token}`,
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'Content-Type': 'application/json',
  };
}

async function ghFetch(url, options = {}) {
  return fetch(url, { ...options, headers: { ...headers(), ...options.headers } });
}

/**
 * Reconstruct the full PUT payload from the GET response object.
 * GitHub's PUT /branches/{branch}/protection is a full replacement — every
 * field must be supplied.  Fields absent from the GET response default to
 * their safe "off" values so existing relaxed settings are not silently
 * downgraded to more restrictive ones.
 */
function buildPayload(current, newContexts, strict) {
  const prReviews    = current?.required_pull_request_reviews ?? null;
  const restrictions = current?.restrictions                  ?? null;

  return {
    // ── Required status checks ───────────────────────────────────────────
    required_status_checks: {
      strict,
      contexts: newContexts,
    },

    // ── Admin enforcement ────────────────────────────────────────────────
    enforce_admins: current?.enforce_admins?.enabled ?? false,

    // ── Pull-request review requirements ─────────────────────────────────
    required_pull_request_reviews: prReviews
      ? {
          dismissal_restrictions: prReviews.dismissal_restrictions
            ? {
                users: (prReviews.dismissal_restrictions.users || []).map(u => u.login),
                teams: (prReviews.dismissal_restrictions.teams || []).map(t => t.slug),
                apps:  (prReviews.dismissal_restrictions.apps  || []).map(a => a.slug),
              }
            : undefined,
          bypass_pull_request_allowances: prReviews.bypass_pull_request_allowances
            ? {
                users: (prReviews.bypass_pull_request_allowances.users || []).map(u => u.login),
                teams: (prReviews.bypass_pull_request_allowances.teams || []).map(t => t.slug),
                apps:  (prReviews.bypass_pull_request_allowances.apps  || []).map(a => a.slug),
              }
            : undefined,
          dismiss_stale_reviews:           prReviews.dismiss_stale_reviews           ?? false,
          require_code_owner_reviews:      prReviews.require_code_owner_reviews      ?? false,
          required_approving_review_count: prReviews.required_approving_review_count ?? 1,
          require_last_push_approval:      prReviews.require_last_push_approval      ?? false,
        }
      : null,

    // ── Push / delete restrictions ────────────────────────────────────────
    restrictions: restrictions
      ? {
          users: (restrictions.users || []).map(u => u.login),
          teams: (restrictions.teams || []).map(t => t.slug),
          apps:  (restrictions.apps  || []).map(a => a.slug),
        }
      : null,

    // ── Additional protection flags (preserve; default to false/off) ──────
    required_linear_history:          current?.required_linear_history?.enabled          ?? false,
    allow_force_pushes:               current?.allow_force_pushes?.enabled               ?? false,
    allow_deletions:                  current?.allow_deletions?.enabled                  ?? false,
    block_creations:                  current?.block_creations?.enabled                  ?? false,
    required_conversation_resolution: current?.required_conversation_resolution?.enabled ?? false,
    lock_branch:                      current?.lock_branch?.enabled                      ?? false,
    allow_fork_syncing:               current?.allow_fork_syncing?.enabled               ?? false,
  };
}

async function enforceBranch(repo, branch, dryRun) {
  const baseUrl = `https://api.github.com/repos/${repo}/branches/${branch}/protection`;

  // ── Step 1: GET current protection ──────────────────────────────────────
  console.log(`\n── ${branch} ──`);
  console.log(`  Fetching branch protection for ${repo}@${branch} …`);
  const getRes = await ghFetch(baseUrl);

  let current = null;

  if (getRes.status === 404) {
    // The 404 can mean either (a) the branch exists but has no protection, or
    // (b) the branch itself doesn't exist.  Distinguish by checking for the
    // "Branch not found" message vs "Branch not protected".
    const body = await getRes.json().catch(() => ({}));
    const msg  = body?.message ?? '';
    if (/branch not found/i.test(msg)) {
      console.log(`  Branch "${branch}" does not exist in this repository — skipping.`);
      return 'skipped';
    }
    console.log(`  No protection rule on "${branch}" — one will be created.`);
    // current stays null; buildPayload handles null gracefully.
  } else if (getRes.status === 403) {
    console.error(
      '  403 — BRANCH_PROTECTION_TOKEN lacks admin permission on this repository.\n' +
      '  Ensure the PAT has the `repo` scope and its owner is a repository admin.\n' +
      '  Do NOT use the built-in Actions GITHUB_TOKEN — it cannot write branch protection.',
    );
    process.exit(1);
  } else if (!getRes.ok) {
    const body = await getRes.text();
    throw new Error(`GET protection failed ${getRes.status}: ${body}`);
  } else {
    current = await getRes.json();
  }

  // ── Step 2: Check idempotency ────────────────────────────────────────────
  const existingContexts = current?.required_status_checks?.contexts ?? [];
  const strict           = current?.required_status_checks?.strict    ?? true;

  const missingContexts = REQUIRED_CONTEXTS.filter(c => !existingContexts.includes(c));

  if (missingContexts.length === 0) {
    console.log(`  ✓  All ${REQUIRED_CONTEXTS.length} required contexts already present:`);
    for (const c of REQUIRED_CONTEXTS) console.log(`       • "${c}"`);
    console.log('     No update needed.');
    return 'ok';
  }

  console.log(`  Adding ${missingContexts.length} missing context(s):`);
  for (const c of missingContexts) console.log(`    + "${c}"`);
  console.log(
    `  Existing contexts (${existingContexts.length}): ${existingContexts.join(', ') || '(none)'}`,
  );

  // ── Step 3: Build PUT payload ────────────────────────────────────────────
  // Merge existing + missing; deduplicate in case of partial overlap.
  const newContexts = [...new Set([...existingContexts, ...REQUIRED_CONTEXTS])];
  const payload     = buildPayload(current, newContexts, strict);

  if (dryRun) {
    console.log('\n  DRY_RUN payload (PUT):\n', JSON.stringify(payload, null, 2));
    return 'dry_run';
  }

  // ── Step 4: PUT updated protection ──────────────────────────────────────
  console.log('  Applying update …');
  const putRes = await ghFetch(baseUrl, {
    method: 'PUT',
    body: JSON.stringify(payload),
  });

  if (putRes.status === 403) {
    console.error(
      '  403 — PUT protection failed: BRANCH_PROTECTION_TOKEN lacks admin permission.',
    );
    process.exit(1);
  }
  if (!putRes.ok) {
    const body = await putRes.text();
    throw new Error(`PUT protection failed ${putRes.status}: ${body}`);
  }

  console.log(
    `  ✓  Branch protection updated on ${repo}@${branch}.\n` +
    `     ${newContexts.length} required status checks now enforced:\n` +
    newContexts.map(c => `       • "${c}"`).join('\n'),
  );
  return 'updated';
}

async function main() {
  const repo     = env('GITHUB_REPOSITORY');
  const dryRun   = process.env.DRY_RUN === '1';
  const branches = (env('BRANCHES', 'Replit-agent,main'))
    .split(',')
    .map(b => b.trim())
    .filter(Boolean);

  console.log(`enforce-branch-protection — repo: ${repo}`);
  console.log(`  Branches to check: ${branches.join(', ')}`);
  console.log(`  Required contexts (${REQUIRED_CONTEXTS.length}):`);
  for (const c of REQUIRED_CONTEXTS) console.log(`    • "${c}"`);
  if (dryRun) console.log('  DRY_RUN=1 — no writes will be made.\n');

  const results = {};
  for (const branch of branches) {
    results[branch] = await enforceBranch(repo, branch, dryRun);
  }

  console.log('\n── Summary ──');
  for (const [branch, result] of Object.entries(results)) {
    const icon = result === 'updated' || result === 'ok' ? '✓' : result === 'skipped' ? '─' : '?';
    console.log(`  ${icon}  ${branch}: ${result}`);
  }
}

main().catch(err => {
  console.error(err.message);
  process.exit(1);
});
