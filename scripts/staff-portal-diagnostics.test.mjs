import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { chromium } from '../apps/frontend/node_modules/@playwright/test/index.mjs';
import {
  STAFF_PORTAL_TRACE_OPTIONS,
  addLoginTokensToRedactions,
  saveRedactedTrace,
} from './staff-portal-diagnostics.mjs';

test('staff portal traces omit screenshots and redact raw and encoded credentials', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'staff-portal-diagnostics-test-'));
  const rawTracePath = join(directory, 'raw.zip');
  const tracePath = join(directory, 'redacted.zip');
  const secret = 'sentinel/password+token';
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();

  try {
    await context.tracing.start(STAFF_PORTAL_TRACE_OPTIONS);
    const page = await context.newPage();
    await page.setContent(
      `<main>${secret} ${encodeURIComponent(secret)} ${Buffer.from(secret).toString('base64')}</main>`,
    );
    await context.tracing.stop({ path: rawTracePath });
    await saveRedactedTrace(rawTracePath, tracePath, [secret]);

    const inspection = spawnSync('python3', [
      '-c',
      [
        'import json, sys, zipfile',
        'with zipfile.ZipFile(sys.argv[1]) as archive:',
        '    entries = archive.namelist()',
        '    content = b"\\n".join(archive.read(name) for name in entries)',
        'print(json.dumps({"entries": entries, "content": content.decode("utf-8", "ignore")}))',
      ].join('\n'),
      tracePath,
    ], { encoding: 'utf8' });
    assert.equal(inspection.status, 0, inspection.stderr);
    const archive = JSON.parse(inspection.stdout);
    assert.equal(
      archive.entries.some(name => /screenshot|\\.jpe?g$|\\.png$/i.test(name)),
      false,
      `trace contains screenshot resources: ${archive.entries.join(', ')}`,
    );
    assert.doesNotMatch(archive.content, /sentinel\/password\+token/);
    assert.doesNotMatch(archive.content, /sentinel%2Fpassword%2Btoken/);
    assert.doesNotMatch(archive.content, /c2VudGluZWwvcGFzc3dvcmQrdG9rZW4=/);
  } finally {
    await context.close().catch(() => {});
    await browser.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test('partial login responses redact every token present before validation fails', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'staff-portal-partial-token-test-'));
  const rawTracePath = join(directory, 'raw.zip');
  const tracePath = join(directory, 'redacted.zip');
  const accessToken = 'partial.access-token+sentinel';
  const sensitiveValues = new Set();

  try {
    addLoginTokensToRedactions({ access_token: accessToken }, sensitiveValues);
    assert.deepEqual([...sensitiveValues], [accessToken]);

    const fixture = [
      accessToken,
      encodeURIComponent(accessToken),
      Buffer.from(accessToken).toString('base64'),
    ].join('\n');
    const creation = spawnSync('python3', [
      '-c',
      [
        'import sys, zipfile',
        'with zipfile.ZipFile(sys.argv[1], "w") as archive:',
        '    archive.writestr("trace.network", sys.argv[2])',
      ].join('\n'),
      rawTracePath,
      fixture,
    ], { encoding: 'utf8' });
    assert.equal(creation.status, 0, creation.stderr);

    await saveRedactedTrace(rawTracePath, tracePath, [...sensitiveValues]);
    const inspection = spawnSync('python3', [
      '-c',
      'import sys, zipfile; print(zipfile.ZipFile(sys.argv[1]).read(\"trace.network\").decode())',
      tracePath,
    ], { encoding: 'utf8' });
    assert.equal(inspection.status, 0, inspection.stderr);
    assert.doesNotMatch(inspection.stdout, /partial/);
    assert.doesNotMatch(inspection.stdout, /cGFydGlhbC5hY2Nlc3MtdG9rZW4rc2VudGluZWw=/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});