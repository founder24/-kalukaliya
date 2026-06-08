#!/usr/bin/env node
/**
 * set-github-secret.js — Set a GitHub Actions secret using the correct
 * libsodium sealed-box encryption (NOT RSA).
 *
 * Usage:
 *   GITHUB_TOKEN=ghp_xxx node infra/scripts/set-github-secret.js \
 *     --repo founder24/-kalukaliya \
 *     --secret VITE_BACKEND_URL \
 *     --value "https://api.syrabit.ai"
 *
 * Install deps (once, from infra/scripts/):
 *   npm install libsodium-wrappers
 *
 * Requires Node 18+ (uses the built-in global `fetch`).
 * GitHub Secrets REST API requires NaCl crypto_box_seal (NOT RSA).
 * Sending RSA-encrypted values produces a secret that the Actions runner
 * cannot decrypt — GitHub stores it silently but injects an empty string.
 * HTTP 204 on PUT = success (no body = not a failure).
 */

const sodium = require('libsodium-wrappers');

const args = process.argv.slice(2);
const get = (flag) => {
  const i = args.indexOf(flag);
  return i !== -1 ? args[i + 1] : null;
};

const REPO    = get('--repo')   || process.env.GITHUB_REPO;
const SECRET  = get('--secret') || process.env.SECRET_NAME;
const VALUE   = get('--value')  || process.env.SECRET_VALUE;
const TOKEN   = process.env.GITHUB_TOKEN;

if (!REPO || !SECRET || !VALUE || !TOKEN) {
  console.error('Usage: GITHUB_TOKEN=xxx node set-github-secret.js --repo owner/repo --secret NAME --value VALUE');
  console.error('Missing: ' + [!REPO&&'--repo', !SECRET&&'--secret', !VALUE&&'--value', !TOKEN&&'GITHUB_TOKEN'].filter(Boolean).join(', '));
  process.exit(1);
}

async function setGitHubSecret(repo, token, secretName, secretValue) {
  await sodium.ready;

  const keyRes = await fetch(
    `https://api.github.com/repos/${repo}/actions/secrets/public-key`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
    }
  );

  if (!keyRes.ok) {
    const body = await keyRes.text();
    throw new Error(`Failed to fetch repo public key: HTTP ${keyRes.status} — ${body}`);
  }

  const { key, key_id } = await keyRes.json();

  const publicKey    = sodium.from_base64(key, sodium.base64_variants.ORIGINAL);
  const messageBytes = sodium.from_string(secretValue);
  const encryptedBytes = sodium.crypto_box_seal(messageBytes, publicKey);
  const encryptedValue = sodium.to_base64(encryptedBytes, sodium.base64_variants.ORIGINAL);

  const putRes = await fetch(
    `https://api.github.com/repos/${repo}/actions/secrets/${secretName}`,
    {
      method: 'PUT',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'Content-Type': 'application/json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
      body: JSON.stringify({ encrypted_value: encryptedValue, key_id }),
    }
  );

  if (putRes.status === 201) {
    console.log(`✅ Secret '${secretName}' created (HTTP 201)`);
  } else if (putRes.status === 204) {
    console.log(`✅ Secret '${secretName}' updated (HTTP 204)`);
  } else {
    const body = await putRes.text();
    throw new Error(`Unexpected response: HTTP ${putRes.status} — ${body}`);
  }
}

setGitHubSecret(REPO, TOKEN, SECRET, VALUE).catch((err) => {
  console.error('❌ Error:', err.message);
  process.exit(1);
});
