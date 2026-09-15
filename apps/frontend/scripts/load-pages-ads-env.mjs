#!/usr/bin/env node
/**
 * Load public AdSense manual-slot configuration from Cloudflare Pages before
 * the release build. Slot IDs are public, but keeping Pages as the source of
 * truth avoids baking a production configuration into the repository.
 *
 * In GitHub Actions, values are appended to GITHUB_ENV. Locally, the script
 * prints only the configured key names and never prints slot values.
 */

import { appendFile } from 'node:fs/promises';
import {
  ADSENSE_SLOT_ENV_KEYS,
  validateAdsenseSlotEnv,
} from '../src/utils/adsenseSlotConfig.js';

const token = process.env.CLOUDFLARE_API_TOKEN?.trim();
const accountId = process.env.CLOUDFLARE_ACCOUNT_ID?.trim();
const project = process.env.CLOUDFLARE_PAGES_PROJECT?.trim() || 'syrabitfrontend';
const githubEnv = process.env.GITHUB_ENV;

if (!token || !accountId) {
  throw new Error('CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are required to load Pages ad configuration.');
}

const response = await fetch(
  `https://api.cloudflare.com/client/v4/accounts/${encodeURIComponent(accountId)}/pages/projects/${encodeURIComponent(project)}`,
  { headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' } },
);
const payload = await response.json();
if (!response.ok || !payload.success) {
  throw new Error(`Cloudflare Pages configuration read failed (HTTP ${response.status}).`);
}

const envVars = payload.result?.deployment_configs?.production?.env_vars || {};
const configured = Object.fromEntries(
  ADSENSE_SLOT_ENV_KEYS.map((key) => [
    key,
    String(envVars[key]?.value || '').trim(),
  ]),
);
const validation = validateAdsenseSlotEnv(configured);

if (!validation.valid) {
  const problems = [];
  if (validation.missing.length) {
    problems.push(`missing: ${validation.missing.join(', ')}`);
  }
  if (validation.invalid.length) {
    problems.push(`invalid numeric ID: ${validation.invalid.join(', ')}`);
  }
  if (validation.duplicates.length) {
    problems.push(`duplicate IDs: ${validation.duplicates.join('; ')}`);
  }
  throw new Error(
    `Cloudflare Pages must define one unique numeric AdSense slot ID per declared placement (${problems.join(' | ')}).`,
  );
}

if (githubEnv) {
  const lines = Object.entries(configured).map(([key, value]) => `${key}=${value}`);
  await appendFile(githubEnv, `${lines.join('\n')}\n`, 'utf8');
}

console.log(
  `Loaded ${Object.keys(configured).length} unique production AdSense slot configuration key(s) from Cloudflare Pages: ${Object.keys(configured).join(', ')}`,
);