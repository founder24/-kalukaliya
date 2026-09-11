import assert from 'node:assert/strict';
import { access, mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { chromium } from '../apps/frontend/node_modules/@playwright/test/index.mjs';
import {
  STAFF_PORTAL_TRACE_OPTIONS,
  addLoginTokensToRedactions,
  saveSafeStaffPortalScreenshot,
  saveRedactedTrace,
} from './staff-portal-diagnostics.mjs';

test('safe screenshot remains useful after redacting unexpected authentication storage', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'staff-portal-screenshot-test-'));
  const screenshotPath = join(directory, 'safe.png');
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  try {
    await page.route('https://staff.test/**', route => route.fulfill({
      contentType: 'text/html',
      body: '<main><h1>Staff health</h1><p id="status">All systems operational</p></main>',
    }));
    await page.goto('https://staff.test/safe');
    await page.evaluate(() => {
      localStorage.setItem('new_provider_credential', 'unexpected-storage-credential-sentinel');
      document.querySelector('#status').append(' unexpected-storage-credential-sentinel');
    });
    await saveSafeStaffPortalScreenshot(page, screenshotPath, []);
    assert.equal((await readFile(screenshotPath)).length > 0, true);
    assert.doesNotMatch(await page.locator('body').innerText(), /unexpected-storage-credential-sentinel/);
    assert.match(await page.locator('body').innerText(), /All systems operational/);
  } finally {
    await browser.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test('safe screenshot redacts encoded credentials from text, title, controls, and path', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'staff-portal-encoded-screenshot-test-'));
  const screenshotPath = join(directory, 'safe.png');
  const secret = 'sentinel/password+token';
  const escapedSecret = 'sentinel"password\\token';
  const encoded = encodeURIComponent(secret);
  const base64 = Buffer.from(secret).toString('base64');
  const base64url = Buffer.from(secret).toString('base64url');
  const jsonEscaped = JSON.stringify(escapedSecret).slice(1, -1);
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  try {
    await page.route('https://staff.test/**', route => route.fulfill({
      contentType: 'text/html',
      body: `<title>${base64}</title>
        <main><h1>Staff health</h1><p>${encoded}</p><p>${jsonEscaped}</p>
        <input name="details" value="${base64url}"></main>`,
    }));
    await page.goto(`https://staff.test/failure/${encoded}`);
    await saveSafeStaffPortalScreenshot(page, screenshotPath, [secret, escapedSecret]);
    assert.equal((await readFile(screenshotPath)).length > 0, true);
    const snapshotText = await page.locator('body').innerText();
    for (const value of [encoded, base64, base64url, jsonEscaped]) {
      assert.doesNotMatch(snapshotText, new RegExp(value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    assert.match(snapshotText, /\[REDACTED\]/);
    assert.match(snapshotText, /Staff health/);
  } finally {
    await browser.close();
    await rm(directory, { recursive: true, force: true });
  }
});

test('unsafe screenshots are deleted for sensitive UI and credential-shaped content', async t => {
  const fixtures = [
    ['password control', '<main>Useful failure</main><input type="password" value="new-secret">'],
    ['unexpected JWT', '<main>Failure eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzdGFmZiJ9.signatureSentinel</main>'],
    [
      'JWT in generically named text input',
      '<main>Useful failure</main><input name="details" value="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzdGFmZiJ9.signatureSentinel">',
    ],
    [
      'bearer token in generically named textarea',
      '<main>Useful failure</main><textarea name="details">Bearer unexpected-bearer-sentinel</textarea>',
    ],
    ['password-labeled text', '<main>Failure password: unexpected-password-sentinel</main>'],
    ['secret-labeled text', '<main>Failure secret=unexpected-secret-sentinel</main>'],
    ['API-key-labeled text', '<main>Failure api_key: unexpected-api-key-sentinel</main>'],
    ['session-labeled text', '<main>Failure session=unexpected-session-sentinel</main>'],
    ['cookie-labeled text', '<main>Failure cookie: unexpected-cookie-sentinel</main>'],
  ];
  for (const [name, html] of fixtures) {
    await t.test(name, async () => {
      const directory = await mkdtemp(join(tmpdir(), 'staff-portal-unsafe-screenshot-test-'));
      const screenshotPath = join(directory, 'unsafe.png');
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage();
      try {
        await page.route('https://staff.test/**', route => route.fulfill({
          contentType: 'text/html',
          body: html,
        }));
        await page.goto('https://staff.test/unsafe');
        await page.screenshot({ path: screenshotPath });
        await assert.rejects(
          saveSafeStaffPortalScreenshot(page, screenshotPath, []),
          /unsafe staff portal screenshot/,
        );
        await assert.rejects(access(screenshotPath), error => error.code === 'ENOENT');
      } finally {
        await browser.close();
        await rm(directory, { recursive: true, force: true });
      }
    });
  }
});

test('capture-only credential surfaces and active rerenders cannot produce screenshots', async t => {
  const secret = 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzdGFmZiJ9.signatureSentinel';
  const fixtures = [
    ['placeholder', `<main>Useful failure</main><input name="details" placeholder="${secret}">`],
    ['iframe', `<main>Useful failure</main><iframe srcdoc="<p>${secret}</p>"></iframe>`],
    ['canvas', `<main>Useful failure</main><canvas width="200" height="50"></canvas>`],
    ['CSS generated content', `<style>main::after { content: "${secret}" }</style><main>Useful failure</main>`],
  ];
  for (const [name, html] of fixtures) {
    await t.test(name, async () => {
      const directory = await mkdtemp(join(tmpdir(), 'staff-portal-capture-surface-test-'));
      const screenshotPath = join(directory, 'unsafe.png');
      const browser = await chromium.launch({ headless: true });
      const page = await browser.newPage();
      try {
        await page.route('https://staff.test/**', route => route.fulfill({
          contentType: 'text/html',
          body: html,
        }));
        await page.goto('https://staff.test/capture-surface');
        if (name === 'canvas') {
          await page.locator('canvas').evaluate((canvas, value) => {
            canvas.getContext('2d').fillText(value, 0, 20);
          }, secret);
        }
        await assert.rejects(
          saveSafeStaffPortalScreenshot(page, screenshotPath, []),
          /unsafe staff portal screenshot/,
        );
        await assert.rejects(access(screenshotPath), error => error.code === 'ENOENT');
      } finally {
        await browser.close();
        await rm(directory, { recursive: true, force: true });
      }
    });
  }

  await t.test('timed rerender', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'staff-portal-rerender-test-'));
    const screenshotPath = join(directory, 'unsafe.png');
    const browser = await chromium.launch({ headless: true });
    const page = await browser.newPage();
    try {
      await page.route('https://staff.test/**', route => route.fulfill({
        contentType: 'text/html',
        body: `<main>Useful failure</main><script>
          setTimeout(() => document.querySelector('main').textContent += ' ${secret}', 25);
        </script>`,
      }));
      await page.goto('https://staff.test/rerender');
      await assert.rejects(
        saveSafeStaffPortalScreenshot(page, screenshotPath, []),
        /unsafe staff portal screenshot/,
      );
      await assert.rejects(access(screenshotPath), error => error.code === 'ENOENT');
    } finally {
      await browser.close();
      await rm(directory, { recursive: true, force: true });
    }
  });
});

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

test('credential signature scanner rejects unsafe traces without disclosing values', async t => {
  const fixtures = [
    [
      'Playwright bearer authorization header record',
      '{"headers":[{"name":"authorization","value":"Bearer bearer-secret-sentinel"}]}',
    ],
    ['JWT-shaped value', 'eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzdGFmZiJ9.signatureSentinel'],
    [
      'Playwright Cloudflare Access client ID header record',
      '{"headers":[{"name":"CF-Access-Client-Id","value":"cloudflare-id-secret-sentinel"}]}',
    ],
    [
      'Playwright Cloudflare Access client secret header record',
      '{"headers":[{"name":"CF-Access-Client-Secret","value":"cloudflare-secret-sentinel"}]}',
    ],
    ['auth storage value', '{"access_token":"storage-secret-sentinel"}'],
    ['auth storage record', '{"name":"refresh_token","value":"record-secret-sentinel"}'],
  ];

  for (const [name, fixture] of fixtures) {
    await t.test(name, async () => {
      const directory = await mkdtemp(join(tmpdir(), 'staff-portal-signature-test-'));
      const rawTracePath = join(directory, 'raw.zip');
      const tracePath = join(directory, 'redacted.zip');
      try {
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

        await assert.rejects(
          saveRedactedTrace(rawTracePath, tracePath, []),
          error => {
            assert.match(error.message, /unsafe credential signature/);
            assert.doesNotMatch(error.message, /secret-sentinel|signatureSentinel/);
            return true;
          },
        );
        await assert.rejects(readFile(tracePath), error => error.code === 'ENOENT');
      } finally {
        await rm(directory, { recursive: true, force: true });
      }
    });
  }
});

test('credential signature scanner accepts explicitly redacted trace values', async () => {
  const directory = await mkdtemp(join(tmpdir(), 'staff-portal-redacted-signature-test-'));
  const rawTracePath = join(directory, 'raw.zip');
  const tracePath = join(directory, 'redacted.zip');
  try {
    const fixture = [
      '{"authorization":"Bearer [REDACTED]"}',
      '{"headers":[{"name":"authorization","value":"Bearer [REDACTED]"}]}',
      '{"CF-Access-Client-Secret":"[REDACTED]"}',
      '{"headers":[{"name":"CF-Access-Client-Id","value":"[REDACTED]"}]}',
      '{"headers":[{"name":"CF-Access-Client-Secret","value":"[REDACTED]"}]}',
      '{"access_token":"[REDACTED]"}',
      '{"name":"refresh_token","value":"[REDACTED]"}',
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

    await saveRedactedTrace(rawTracePath, tracePath, []);
    assert.equal((await readFile(tracePath)).length > 0, true);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
