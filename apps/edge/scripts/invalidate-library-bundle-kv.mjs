#!/usr/bin/env node

import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

export const LIBRARY_BUNDLE_PREFIX = 'api:library-bundle:';
const ACCOUNT_API = 'https://api.cloudflare.com/client/v4';
const WORKFLOW_FILE = fileURLToPath(new URL('../wrangler.toml', import.meta.url));

export function productionIsrNamespaceId(wranglerToml) {
  const sections = [...wranglerToml.matchAll(
    /\[\[env\.production\.kv_namespaces\]\]\s*([\s\S]*?)(?=\n\[\[|\n\[|$)/g,
  )].map((match) => match[1]);
  const matches = sections.filter((section) =>
    /^\s*binding\s*=\s*"ISR_CACHE_KV"\s*$/m.test(section),
  );
  if (matches.length !== 1) {
    throw new Error(`Expected one production ISR_CACHE_KV binding, found ${matches.length}.`);
  }
  const id = matches[0].match(/^\s*id\s*=\s*"([a-f0-9]+)"\s*$/m)?.[1];
  if (!id) throw new Error('Production ISR_CACHE_KV namespace ID is missing or malformed.');
  return id;
}

async function cloudflareJson(fetchImpl, url, token, init = {}) {
  const response = await fetchImpl(url, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || payload?.success !== true) {
    const providerMessage = payload?.errors?.map((item) => item.message).filter(Boolean).join('; ');
    throw new Error(
      `Cloudflare KV request failed (HTTP ${response.status})${providerMessage ? `: ${providerMessage}` : ''}.`,
    );
  }
  return payload;
}

export async function invalidateLibraryBundleKeys({
  accountId,
  namespaceId,
  token,
  fetchImpl = fetch,
}) {
  if (!accountId || !namespaceId || !token) {
    throw new Error('Cloudflare account ID, production KV namespace ID, and API token are required.');
  }

  const base = `${ACCOUNT_API}/accounts/${encodeURIComponent(accountId)}/storage/kv/namespaces/${encodeURIComponent(namespaceId)}`;
  const keys = [];
  let cursor;
  const seenCursors = new Set();
  do {
    const url = new URL(`${base}/keys`);
    url.searchParams.set('prefix', LIBRARY_BUNDLE_PREFIX);
    url.searchParams.set('limit', '1000');
    if (cursor) url.searchParams.set('cursor', cursor);
    const payload = await cloudflareJson(fetchImpl, url, token);
    const page = payload.result;
    if (!Array.isArray(page)) throw new Error('Cloudflare KV key listing returned an invalid result.');
    for (const item of page) {
      if (typeof item?.name !== 'string' || !item.name.startsWith(LIBRARY_BUNDLE_PREFIX)) {
        throw new Error('Cloudflare returned a key outside the library-bundle prefix; refusing deletion.');
      }
      keys.push(item.name);
    }
    cursor = payload.result_info?.cursor || undefined;
    if (cursor && seenCursors.has(cursor)) {
      throw new Error('Cloudflare repeated a KV pagination cursor; refusing an incomplete cache invalidation.');
    }
    if (cursor) seenCursors.add(cursor);
  } while (cursor);

  for (const key of keys) {
    await cloudflareJson(fetchImpl, `${base}/values/${encodeURIComponent(key)}`, token, { method: 'DELETE' });
  }
  return keys.length;
}

function findMakerSubjects(bundle) {
  if (!Array.isArray(bundle?.subjects)) throw new Error('Library bundle has no flat subjects array.');
  return bundle.subjects.filter((subject) =>
    subject?.slug === 'makers-of-modern-assam-mdc-01013',
  );
}

export function assertMakerSubjectBilingual(bundle, variant) {
  const subjects = findMakerSubjects(bundle);
  if (subjects.length !== 3) {
    throw new Error(`${variant} bundle must include all three Makers of Modern Assam stream cards; found ${subjects.length}.`);
  }
  const incomplete = subjects.filter((subject) =>
    typeof subject.name_as !== 'string' || !subject.name_as.trim()
    || typeof subject.description_as !== 'string' || !subject.description_as.trim(),
  );
  if (incomplete.length) {
    throw new Error(`${variant} bundle has ${incomplete.length} Makers of Modern Assam card(s) missing Assamese name or description.`);
  }
}

async function fetchVerifiedBundle(url, label, fetchImpl) {
  const response = await fetchImpl(url, { headers: { Accept: 'application/json' }, cache: 'no-store' });
  if (!response.ok) throw new Error(`${label} library bundle returned HTTP ${response.status}.`);
  const bundle = await response.json();
  assertMakerSubjectBilingual(bundle, label);
  return bundle;
}

export async function verifyPublicLibraryBundles({
  baseUrl,
  fetchImpl = fetch,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  attempts = 6,
}) {
  const base = baseUrl.replace(/\/+$/, '');
  const fullUrl = `${base}/api/v1/content/library-bundle`;
  const retry = async (url, label) => {
    let lastError;
    for (let attempt = 1; attempt <= attempts; attempt++) {
      try {
        return await fetchVerifiedBundle(url, label, fetchImpl);
      } catch (error) {
        lastError = error;
        if (attempt === attempts) throw lastError;
        await sleep(5000);
      }
    }
    throw lastError;
  };
  const fullBundle = await retry(fullUrl, 'full');
  await retry(`${fullUrl}?slim=1`, 'slim');
  const degree = fullBundle.boards?.find((board) => board?.name === 'Degree');
  if (!degree?.id) throw new Error('Full library bundle did not include the Degree board ID.');
  await retry(`${fullUrl}?boot=${encodeURIComponent(degree.id)}`, 'Degree boot');
}

export async function main({
  env = process.env,
  fetchImpl = fetch,
  log = console.log,
  sleep,
} = {}) {
  const accountId = env.CLOUDFLARE_ACCOUNT_ID;
  const token = env.CLOUDFLARE_API_TOKEN;
  const wranglerToml = await readFile(WORKFLOW_FILE, 'utf8');
  const namespaceId = productionIsrNamespaceId(wranglerToml);
  const deleted = await invalidateLibraryBundleKeys({ accountId, namespaceId, token, fetchImpl });
  log(`Deleted ${deleted} production library-bundle KV key(s); unrelated KV keys were not touched.`);

  await verifyPublicLibraryBundles({
    baseUrl: env.PUBLIC_LIBRARY_BUNDLE_URL || 'https://syrabit.ai',
    fetchImpl,
    ...(sleep ? { sleep } : {}),
  });
  log('Verified full, slim, and Degree boot bundles contain Assamese name and description for all three Makers of Modern Assam cards.');
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  main().catch((error) => {
    console.error(`Library-bundle cache refresh failed: ${error instanceof Error ? error.message : String(error)}`);
    process.exitCode = 1;
  });
}
