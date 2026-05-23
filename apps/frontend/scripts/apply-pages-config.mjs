#!/usr/bin/env node
// Idempotent runbook script that brings the Cloudflare Pages project
// `syrabit-analytics` (account env CLOUDFLARE_ACCOUNT_ID) in line with
// artifacts/syrabit/CLOUDFLARE_PAGES.md. Captures the exact API
// operations applied for Task #521 so the work is auditable and
// re-applicable.
//
// Required env:
//   CLOUDFLARE_ACCOUNT_ID  - account that owns the Pages project
//   CF_PAGES_API_TOKEN     - token with Pages:Edit + Account:Read scope
//                            (must NOT be the backend CF_ANALYTICS_API_TOKEN
//                             — wrong scope, will leak into Pages logs)
//
// Usage:
//   node artifacts/syrabit/scripts/apply-pages-config.mjs            # dry run
//   node artifacts/syrabit/scripts/apply-pages-config.mjs --apply    # PATCH
//   node artifacts/syrabit/scripts/apply-pages-config.mjs --deploy   # +deploy

const PROJECT = "syrabit-analytics";

const BUILD_CONFIG = {
  build_command:
    "corepack enable && corepack prepare pnpm@10.26.1 --activate && " +
    "pnpm install --filter @workspace/syrabit... --frozen-lockfile && " +
    "pnpm --filter @workspace/syrabit run build",
  destination_dir: "artifacts/syrabit/dist",
  root_dir: "",
  build_caching: true,
};

// Required production env vars per CLOUDFLARE_PAGES.md (the five
// required vars; VITE_CF_ANALYTICS_TOKEN is optional and not enforced
// here). VITE_GA4_ID is required by the doc but must be a real GA4
// Measurement ID matching /^G-[A-Z0-9]{6,12}$/. The script reads the
// value from the env var VITE_GA4_ID; if it is missing or malformed,
// the script prints a loud warning and (by default) continues so the
// rest of the config still gets applied. Pass --strict-ga4 to instead
// hard-fail the run when GA4 is missing/invalid — use that mode in
// CI/release pipelines once the real Measurement ID is known.
const REQUIRED_PROD_ENV = {
  NODE_ENV: "production",
  // Node version per CLOUDFLARE_PAGES.md ("20 or 22"). Cloudflare Pages
  // reads NODE_VERSION as a build-time env var to pick the build image's
  // Node runtime, so this is the canonical place to pin it.
  NODE_VERSION: "22",
  VITE_BACKEND_URL: process.env.VITE_BACKEND_URL || "https://api.syrabit.ai",
  PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD: "1",
  PUPPETEER_SKIP_DOWNLOAD: "1",
};
const GA4_RE = /^G-[A-Z0-9]{6,12}$/;
const ga4Id = process.env.VITE_GA4_ID;
if (ga4Id && GA4_RE.test(ga4Id)) {
  REQUIRED_PROD_ENV.VITE_GA4_ID = ga4Id;
}

// Backend / Worker secrets that must NEVER be set on Pages — they get
// baked into the public build log. If found on the project, this script
// nulls them out (which removes them) and prints them so you know what
// to rotate at the source-of-truth provider.
const DO_NOT_SET = new Set([
  "CF_ANALYTICS_API_TOKEN",
  "CF_ZONE_ID",
  "D1_SYNC_SECRET",
  "EDGE_WORKER_URL",
  "SUPABASE_DB_URL",
  "SUPABASE_SERVICE_ROLE_KEY",
  "SUPABASE_SERVICE_KEY",
  "SUPABASE_URL",
  "ADMIN_EMAILS",
  "ADMIN_NAMES",
  "ADMIN_PASSWORDS",
  "ADMIN_JWT_SECRET",
  "RAZORPAY_KEY_ID",
  "RAZORPAY_KEY_SECRET",
  "RAZORPAY_WEBHOOK_SECRET",
  "OPENAI_API_KEY",
  "GROQ_API_KEY",
  "GROQ_API_KEY_2",
  "GEMINI_API_KEY",
  "OPENROUTER_API_KEY",
  "CEREBRAS_API_KEY",
  "SARVAM_API_KEY",
  "SARVAM_API_KEY_2",
  "SARVAM_API_KEY_3",
  "TRUSTPILOT_API_KEY",
  "JWT_SECRET",
  "SESSION_SECRET",
  "GOOGLE_CLIENT_SECRET",
  "MONGO_URL",
  "UPSTASH_REDIS_REST_TOKEN",
  "UPSTASH_REDIS_REST_URL",
  "CORS_ORIGINS",
  "DB_NAME",
  "GA4_PROPERTY_ID",
  "GOOGLE_OAUTH_CLIENT_ID",
  "SECURE_COOKIES",
  "TRUSTPILOT_BUSINESS_UNIT_ID",
]);

const accountId = process.env.CLOUDFLARE_ACCOUNT_ID;
const token = process.env.CF_PAGES_API_TOKEN;
if (!accountId || !token) {
  console.error(
    "Missing CLOUDFLARE_ACCOUNT_ID or CF_PAGES_API_TOKEN. See header.",
  );
  process.exit(1);
}

const APPLY = process.argv.includes("--apply") || process.argv.includes("--deploy");
const DEPLOY = process.argv.includes("--deploy");
const STRICT_GA4 = process.argv.includes("--strict-ga4");

if (STRICT_GA4 && !REQUIRED_PROD_ENV.VITE_GA4_ID) {
  console.error(
    "ERROR (--strict-ga4): VITE_GA4_ID must be set to a valid GA4 " +
      "Measurement ID matching /^G-[A-Z0-9]{6,12}$/. Got: " +
      JSON.stringify(process.env.VITE_GA4_ID),
  );
  process.exit(2);
}

async function cf(path, init = {}) {
  const r = await fetch(`https://api.cloudflare.com/client/v4${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...(init.headers || {}),
    },
  });
  const j = await r.json();
  if (!j.success) {
    throw new Error(`CF API ${path} failed: ${JSON.stringify(j.errors)}`);
  }
  return j.result;
}

const project = await cf(`/accounts/${accountId}/pages/projects/${PROJECT}`);
const prod = project.deployment_configs?.production?.env_vars || {};
const preview = project.deployment_configs?.preview?.env_vars || {};

const prodEnvPatch = {};
for (const [k, v] of Object.entries(REQUIRED_PROD_ENV)) {
  prodEnvPatch[k] = { type: "plain_text", value: v };
}
const stripped = { production: [], preview: [] };
for (const k of Object.keys(prod)) {
  if (DO_NOT_SET.has(k)) {
    prodEnvPatch[k] = null;
    stripped.production.push(k);
  }
}
const previewEnvPatch = {};
for (const k of Object.keys(preview)) {
  if (DO_NOT_SET.has(k)) {
    previewEnvPatch[k] = null;
    stripped.preview.push(k);
  }
}
// Mirror VITE_GA4_ID onto preview so PR previews report under the same
// stream as production. Without this, preview keeps whatever stale
// value was there before (in our case the GA4 *Property ID* 530170895,
// which fails the regex and silently disables gtag).
if (REQUIRED_PROD_ENV.VITE_GA4_ID) {
  previewEnvPatch.VITE_GA4_ID = {
    type: "plain_text",
    value: REQUIRED_PROD_ENV.VITE_GA4_ID,
  };
}

const body = {
  build_config: BUILD_CONFIG,
  deployment_configs: {
    production: { env_vars: prodEnvPatch },
    preview: { env_vars: previewEnvPatch },
  },
};

console.log("=== Planned changes ===");
console.log("build_command:", BUILD_CONFIG.build_command);
console.log("destination_dir:", BUILD_CONFIG.destination_dir);
// Cloudflare's API represents the repo-root project root as the empty
// string; the dashboard surfaces this as "/". Both are equivalent.
console.log("root_dir:", BUILD_CONFIG.root_dir === "" ? '"" (= "/" — repo root)' : BUILD_CONFIG.root_dir);
console.log("Setting on production env:", Object.keys(REQUIRED_PROD_ENV));
console.log("Stripping from production (leaked):", stripped.production);
console.log("Stripping from preview (leaked):", stripped.preview);
if (!REQUIRED_PROD_ENV.VITE_GA4_ID) {
  console.warn(
    "\n!!! VITE_GA4_ID is required by CLOUDFLARE_PAGES.md but no valid " +
      "Measurement ID (G-XXXXXXXXXX) was supplied via the VITE_GA4_ID " +
      "env var. The script will leave it unset and the deployed bundle " +
      "will not load gtag. Re-run this script with VITE_GA4_ID=G-... " +
      "once the correct value is known.",
  );
}

if (!APPLY) {
  console.log("\n(dry run — pass --apply to PATCH, --deploy to also trigger a deploy)");
  process.exit(0);
}

await cf(`/accounts/${accountId}/pages/projects/${PROJECT}`, {
  method: "PATCH",
  body: JSON.stringify(body),
});
console.log("PATCH applied.");

if (stripped.production.length || stripped.preview.length) {
  console.log(
    "\n!!! Rotate the following credentials at their source-of-truth provider:",
  );
  for (const k of [...stripped.production, ...stripped.preview]) {
    console.log("  -", k);
  }
}

if (DEPLOY) {
  const dep = await cf(
    `/accounts/${accountId}/pages/projects/${PROJECT}/deployments`,
    { method: "POST" },
  );
  console.log("\nDeployment triggered:", dep.id);
  console.log(
    "Watch:  https://dash.cloudflare.com/" +
      accountId +
      "/pages/view/" +
      PROJECT +
      "/" +
      dep.id,
  );
}
