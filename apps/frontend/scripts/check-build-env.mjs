// Task #535: fail fast on missing/invalid build-time env vars so a
// misconfigured Cloudflare Pages project does not waste 5+ minutes
// producing a silently-broken bundle.
//
// Hard-fails on:
//   * VITE_BACKEND_URL missing/blank
//   * VITE_GA4_ID set but not matching ^G-[A-Z0-9]{6,12}$
//
// Warns (non-fatal) when VITE_GA4_ID is unset — the ga4 plugin
// already strips the placeholder cleanly and the build succeeds, but
// gtag won't load and Realtime traffic won't show up.

import { readFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

// Load .env.production as fallback when running locally (Vite reads it
// automatically during vite build, but this pre-check runs in raw Node).
const __dirname = dirname(fileURLToPath(import.meta.url));
const envProdPath = resolve(__dirname, "../.env.production");
try {
  const raw = readFileSync(envProdPath, "utf8");
  for (const line of raw.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    const val = trimmed.slice(eq + 1).trim();
    if (key && !(key in process.env)) process.env[key] = val;
  }
} catch {
  // .env.production absent — rely on Cloudflare Pages build env vars.
}

const errors = [];
const warnings = [];

const backend = (process.env.VITE_BACKEND_URL || "").trim();
if (!backend) {
  errors.push(
    "VITE_BACKEND_URL is unset or blank. Set it on Cloudflare Pages " +
      "→ Settings → Environment variables (production) to https://api.syrabit.ai " +
      "(or the appropriate backend origin). Without it the bundle hard-codes " +
      "localhost:8000 and every API call in the deployed site fails.",
  );
} else {
  try {
    const u = new URL(backend);
    if (!/^https?:$/.test(u.protocol)) {
      errors.push(`VITE_BACKEND_URL has unsupported protocol: ${u.protocol}`);
    }
  } catch (err) {
    errors.push(`VITE_BACKEND_URL is not a valid URL: ${backend} (${err.message})`);
  }
}

const GA4_RE = /^G-[A-Z0-9]{6,12}$/;
const ga4 = (process.env.VITE_GA4_ID || "").trim();
if (ga4 && !GA4_RE.test(ga4)) {
  errors.push(
    `VITE_GA4_ID="${ga4}" does not match the required Measurement ID ` +
      `format /^G-[A-Z0-9]{6,12}$/. Use the GA4 Measurement ID (G-XXXXXXXXXX), ` +
      `not the Property ID or a UA-* legacy ID. The ga4 plugin would silently ` +
      `drop this value and ship a build with no analytics.`,
  );
} else if (!ga4) {
  warnings.push(
    "VITE_GA4_ID is unset — gtag will not load and Realtime traffic will not appear. " +
      "Set the GA4 Measurement ID on Pages to enable analytics.",
  );
}

for (const w of warnings) {
  console.warn(`[check-build-env] WARN: ${w}`);
}

if (errors.length) {
  console.error("[check-build-env] FAIL:");
  for (const e of errors) console.error("  - " + e);
  process.exit(1);
}

console.log(
  `[check-build-env] OK — VITE_BACKEND_URL=${backend}` +
    (ga4 ? `, VITE_GA4_ID=${ga4}` : " (no GA4 ID)"),
);
