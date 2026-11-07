#!/usr/bin/env node
import { buildStudentSearchDocuments } from '../src/services/student-search.ts';

const DEFAULT_ACCOUNT_ID = 'd66e40eac539fff1db270fddf384a5ec';
const DEFAULT_DATABASE_ID = 'ff8e76ec-02c5-45f3-92ea-4d67d7d2a510';
const DEFAULT_INSTANCE_ID = 'syrabit-student-curriculum';
const OWNED_KEY_PREFIX = 'student-search/v1/';
const PAGE_SIZE = 25;
const MAX_RECORDS = 5_000;
const UPLOAD_CONCURRENCY = 3;

function optionsFromArgs(args) {
  const options = {
    apply: false,
    accountId: process.env.CLOUDFLARE_ACCOUNT_ID || DEFAULT_ACCOUNT_ID,
    databaseId: process.env.SYRABIT_D1_DATABASE_ID || DEFAULT_DATABASE_ID,
    instanceId: process.env.AI_SEARCH_INSTANCE_ID || DEFAULT_INSTANCE_ID,
    maxRecords: MAX_RECORDS,
  };

  for (const arg of args) {
    if (arg === '--') continue;
    else if (arg === '--apply') options.apply = true;
    else if (arg === '--help' || arg === '-h') options.help = true;
    else if (arg.startsWith('--max-records=')) {
      options.maxRecords = Number(arg.slice('--max-records='.length));
    } else {
      throw new Error(`Unknown argument: ${arg}`);
    }
  }

  if (!Number.isSafeInteger(options.maxRecords) || options.maxRecords < 1 || options.maxRecords > MAX_RECORDS) {
    throw new Error(`--max-records must be an integer from 1 to ${MAX_RECORDS}`);
  }
  return options;
}

function printHelp() {
  console.log([
    'Sync published English and Assamese curriculum into the private AI Search instance.',
    '',
    'Usage:',
    '  pnpm --filter syrabit-api student-search:sync                 # dry run',
    '  pnpm --filter syrabit-api student-search:sync -- --apply      # upload and remove stale owned items',
    '',
    'The sync reads only published chapters with published subjects from production D1.',
    'It uploads missing/current versions first and removes stale student-search/v1/* items only',
    'after every D1 page and upload succeeds. Search feature flags are not changed.',
  ].join('\n'));
}

function assertEnvelope(payload, operation) {
  if (!payload || payload.success !== true || !Array.isArray(payload.result)) {
    throw new Error(`${operation} failed`);
  }
  return payload.result;
}

async function cloudflareRequest(path, init = {}) {
  const token = process.env.CLOUDFLARE_API_TOKEN;
  if (!token) throw new Error('CLOUDFLARE_API_TOKEN is required; configure it through workspace Secrets.');

  const response = await fetch(`https://api.cloudflare.com/client/v4${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...init.headers,
    },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok || payload?.success !== true) {
    const apiMessage = Array.isArray(payload?.errors)
      ? payload.errors.map(error => error.message).filter(Boolean).join('; ')
      : '';
    throw new Error(`Cloudflare API request failed (${response.status})${apiMessage ? `: ${apiMessage}` : ''}`);
  }
  return payload;
}

async function d1Query(options, sql, params = []) {
  const payload = await cloudflareRequest(
    `/accounts/${options.accountId}/d1/database/${options.databaseId}/query`,
    { method: 'POST', body: JSON.stringify({ sql, params: params.map(String) }) },
  );
  const result = assertEnvelope(payload, 'Production D1 query');
  const rows = result[0]?.results;
  if (!Array.isArray(rows)) throw new Error('Production D1 query returned an unexpected result shape');
  return rows;
}

async function getPublishedCount(options) {
  const rows = await d1Query(options, `
    SELECT COUNT(*) AS total
    FROM chapters c
    JOIN subjects s ON s.id = c.subject_id
    JOIN streams str ON str.id = s.stream_id
    JOIN classes cl ON cl.id = str.class_id
    JOIN boards b ON b.id = cl.board_id
    WHERE c.status = 'published' AND s.is_published = 1
  `);
  const total = Number(rows[0]?.total);
  if (!Number.isSafeInteger(total) || total < 0 || total > options.maxRecords) {
    throw new Error(`Published curriculum count exceeds the safe cap (${options.maxRecords}); no writes were made.`);
  }
  return total;
}

async function getPublishedVersionSnapshot(options, expectedCount) {
  const versions = [];
  let afterId = '';
  while (versions.length < expectedCount) {
    const rows = await d1Query(options, `
      SELECT c.id, c.updated_at
      FROM chapters c
      JOIN subjects s ON s.id = c.subject_id
      JOIN streams str ON str.id = s.stream_id
      JOIN classes cl ON cl.id = str.class_id
      JOIN boards b ON b.id = cl.board_id
      WHERE c.status = 'published'
        AND s.is_published = 1
        AND c.id > ?
      ORDER BY c.id COLLATE BINARY
      LIMIT ?
    `, [afterId, PAGE_SIZE]);
    if (rows.length === 0) break;
    for (const row of rows) {
      afterId = String(row.id);
      versions.push(`${row.id}\u0000${row.updated_at ?? 0}`);
    }
  }
  return versions;
}

async function loadPublishedPage(options, afterId) {
  const rows = await d1Query(options, `
    SELECT
      c.id,
      c.title,
      c.title_as AS title_as,
      c.status,
      c.updated_at,
      s.id AS subject_id,
      s.is_published AS subject_is_published,
      s.name AS subject_name,
      s.slug AS subject_slug,
      b.slug AS board_slug,
      cl.slug AS class_slug,
      str.slug AS stream_slug,
      c.rag_sections_en,
      c.rag_sections_as,
      c.rag_text AS rag_text_en,
      c.rag_text_as,
      c.notes_en,
      c.notes_as
    FROM chapters c
    JOIN subjects s ON s.id = c.subject_id
    JOIN streams str ON str.id = s.stream_id
    JOIN classes cl ON cl.id = str.class_id
    JOIN boards b ON b.id = cl.board_id
    WHERE c.status = 'published'
      AND s.is_published = 1
      AND c.id > ?
    ORDER BY c.id COLLATE BINARY
    LIMIT ?
  `, [afterId, PAGE_SIZE]);
  return rows;
}

function mapSource(row) {
  return {
    id: row.id,
    title: row.title,
    titleAs: row.title_as,
    status: row.status,
    updatedAt: row.updated_at,
    subjectId: row.subject_id,
    subjectIsPublished: row.subject_is_published === 1,
    subjectName: row.subject_name,
    boardSlug: row.board_slug,
    classSlug: row.class_slug,
    streamSlug: row.stream_slug,
    subjectSlug: row.subject_slug,
    ragSectionsEn: row.rag_sections_en,
    ragSectionsAs: row.rag_sections_as,
    ragTextEn: row.rag_text_en,
    ragTextAs: row.rag_text_as,
    notesEn: row.notes_en,
    notesAs: row.notes_as,
  };
}

async function getInstanceAndItems(options) {
  const instancePath = `/accounts/${options.accountId}/ai-search/instances/${options.instanceId}`;
  const instancePayload = await cloudflareRequest(instancePath);
  const instance = instancePayload.result;
  const expectedFields = new Map([
    ['board_slug', 'text'],
    ['class_slug', 'text'],
    ['stream_slug', 'text'],
    ['language', 'text'],
    ['updated_at', 'number'],
  ]);
  for (const field of instance.custom_metadata ?? []) {
    if (expectedFields.get(field.field_name) === field.data_type) expectedFields.delete(field.field_name);
  }
  if (expectedFields.size > 0) {
    throw new Error(`AI Search instance metadata schema mismatch: ${[...expectedFields.keys()].join(', ')}`);
  }
  if (instance.public_endpoint_params?.enabled === true) {
    throw new Error('AI Search public endpoint is enabled; refusing to sync into a non-private instance.');
  }

  const items = [];
  const itemsBase = `/accounts/${options.accountId}/ai-search/namespaces/default/instances/${options.instanceId}/items`;
  for (let page = 1; page <= 100; page += 1) {
    const payload = await cloudflareRequest(`${itemsBase}?page=${page}&per_page=50&source=builtin`);
    const result = assertEnvelope(payload, 'AI Search item listing');
    items.push(...result);
    const total = Number(payload.result_info?.total_count ?? items.length);
    if (items.length >= total) return { items, itemsBase };
    if (result.length === 0) throw new Error('AI Search item listing ended before all pages were returned.');
  }
  throw new Error('AI Search item listing exceeded the safe 5,000-item page bound.');
}

async function uploadDocument(itemsBase, document) {
  const form = new FormData();
  form.append('file', new Blob([document.content], { type: 'text/markdown;charset=utf-8' }), document.key);
  // The multipart REST API accepts metadata as a JSON string and requires
  // scalar values in that envelope to be strings; the instance schema applies
  // the field's text/number type during indexing.
  const metadata = Object.fromEntries(
    Object.entries(document.metadata).map(([key, value]) => [key, String(value)]),
  );
  form.append('metadata', JSON.stringify(metadata));
  const response = await cloudflareRequest(itemsBase, { method: 'POST', body: form });
  return response.result;
}

async function deleteItem(itemsBase, itemId) {
  const payload = await cloudflareRequest(`${itemsBase}/${encodeURIComponent(itemId)}`, { method: 'DELETE' });
  return payload.success === true;
}

async function runPool(tasks, concurrency, run) {
  let next = 0;
  const workers = Array.from({ length: Math.min(concurrency, tasks.length) }, async () => {
    while (next < tasks.length) {
      const index = next;
      next += 1;
      await run(tasks[index]);
    }
  });
  await Promise.all(workers);
}

async function main() {
  const options = optionsFromArgs(process.argv.slice(2));
  if (options.help) return printHelp();
  const totalRecords = await getPublishedCount(options);
  const initialVersionSnapshot = await getPublishedVersionSnapshot(options, totalRecords);
  if (initialVersionSnapshot.length !== totalRecords) {
    throw new Error('Published curriculum changed during preflight; no writes were made.');
  }
  const { items: existingItems, itemsBase } = await getInstanceAndItems(options);
  const existingByKey = new Map(existingItems.map(item => [item.key, item]));
  const desiredKeys = new Set();
  const uploadQueue = [];
  let scanned = 0;
  let eligibleEnglish = 0;
  let eligibleAssamese = 0;
  let lastId = '';

  while (scanned < totalRecords) {
    const rows = await loadPublishedPage(options, lastId);
    if (rows.length === 0) throw new Error('Published curriculum changed while reading; refusing cleanup.');
    for (const row of rows) {
      lastId = String(row.id);
      scanned += 1;
      const documents = buildStudentSearchDocuments(mapSource(row));
      for (const document of documents) {
        desiredKeys.add(document.key);
        if (document.metadata.language === 'en') eligibleEnglish += 1;
        else eligibleAssamese += 1;

        const current = existingByKey.get(document.key);
        if (!current || current.status === 'error' || current.status === 'skipped' || current.status === 'outdated') {
          uploadQueue.push(document);
        }
      }
    }
    if (rows.length < PAGE_SIZE && scanned < totalRecords) {
      throw new Error('Production D1 changed during pagination; refusing stale-item cleanup.');
    }
  }

  if (scanned !== totalRecords) {
    throw new Error(`Expected ${totalRecords} published chapters but scanned ${scanned}; refusing cleanup.`);
  }
  const currentVersionSnapshot = await getPublishedVersionSnapshot(options, totalRecords);
  if (
    currentVersionSnapshot.length !== initialVersionSnapshot.length
    || currentVersionSnapshot.some((version, index) => version !== initialVersionSnapshot[index])
  ) {
    throw new Error('Published curriculum changed while reading; refusing stale-item cleanup.');
  }

  const staleItems = existingItems.filter(item =>
    typeof item.key === 'string'
    && item.key.startsWith(OWNED_KEY_PREFIX)
    && !desiredKeys.has(item.key),
  );
  const summary = {
    mode: options.apply ? 'apply' : 'dry-run',
    instance: options.instanceId,
    publicEndpointEnabled: false,
    publishedChaptersScanned: scanned,
    eligibleEnglishDocuments: eligibleEnglish,
    eligibleAssameseDocuments: eligibleAssamese,
    alreadyCurrentDocuments: desiredKeys.size - uploadQueue.length,
    documentsToUpload: uploadQueue.length,
    staleOwnedItemsToDelete: staleItems.length,
  };

  if (!options.apply) {
    console.log(JSON.stringify(summary));
    return;
  }

  let uploaded = 0;
  await runPool(uploadQueue, UPLOAD_CONCURRENCY, async (document) => {
    await uploadDocument(itemsBase, document);
    uploaded += 1;
  });

  // Cleanup is deliberately last: a failed page or upload never removes the last
  // indexed copy. D1 revalidation remains the final guard against stale results.
  let deleted = 0;
  await runPool(staleItems, UPLOAD_CONCURRENCY, async (item) => {
    if (typeof item.id !== 'string' || !item.id) {
      throw new Error('AI Search returned a stale item without an ID; refusing cleanup.');
    }
    if (await deleteItem(itemsBase, item.id)) deleted += 1;
  });

  console.log(JSON.stringify({ ...summary, uploaded, deleted }));
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : 'Student Search sync failed.');
  process.exitCode = 1;
});
