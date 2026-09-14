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
const configured = Object.entries(envVars)
  .filter(([key]) => /^VITE_ADS_ADSENSE_[A-Z0-9_]+_SLOT$/.test(key))
  .map(([key, definition]) => [key, String(definition?.value || '').trim()])
  .filter(([, value]) => value);

for (const [key, value] of configured) {
  if (!/^\d{5,20}$/.test(value)) {
    throw new Error(`${key} is not a valid numeric AdSense slot ID.`);
  }
}

if (!configured.length) {
  throw new Error('No production AdSense manual slot IDs are configured in Cloudflare Pages.');
}

if (githubEnv) {
  const lines = configured.map(([key, value]) => `${key}=${value}`);
  await appendFile(githubEnv, `${lines.join('\n')}\n`, 'utf8');
}

console.log(
  `Loaded ${configured.length} production AdSense slot configuration key(s) from Cloudflare Pages: ${configured.map(([key]) => key).join(', ')}`,
);