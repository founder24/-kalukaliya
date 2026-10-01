import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import {
  collectStaffAssetReferences,
  verifyProductionStaffAssets,
} from './check-production-staff-assets.mjs';

const SITE_URL = 'https://staff.test';
const ACCESS_ID = 'access-id-sentinel';
const ACCESS_SECRET = 'access-secret-sentinel';
const QUIET_LOGGER = { info() {}, warn() {} };

function pageHtml(jsName = 'app-abcdefgh.js', cssName = 'index-12345678.css') {
  return `<script type="module" src="/assets/${jsName}"></script>
    <link rel="stylesheet" href="/assets/${cssName}">`;
}

function htmlResponse(html) {
  return new Response(html, {
    status: 200,
    headers: { 'content-type': 'text/html; charset=utf-8' },
  });
}

function assetResponse(pathname, status = 200) {
  const type = pathname.endsWith('.js') ? 'application/javascript' : 'text/css';
  return new Response(status === 200 ? '/* verified asset */' : 'missing', {
    status,
    headers: { 'content-type': status === 200 ? type : 'text/plain' },
  });
}

function baseOptions(overrides = {}) {
  return {
    siteUrl: SITE_URL,
    accessClientId: ACCESS_ID,
    accessClientSecret: ACCESS_SECRET,
    requestTimeoutMs: 0,
    retryDelayMs: 25,
    maxAttempts: 4,
    now: () => 1234,
    wait: async () => {},
    logger: QUIET_LOGGER,
    ...overrides,
  };
}

test('collects only same-origin /assets references and identifies hashed bundles', () => {
  const assets = collectStaffAssetReferences(
    `<script src="/assets/app-abcdefgh.js"></script>
     <link href="/assets/index-12345678.css">
     <script src="https://cdn.test/assets/external-abcdefgh.js"></script>
     <img src="/icons/logo.svg">`,
    `${SITE_URL}/staff`,
  );

  assert.deepEqual(
    assets.map(({ pathname, hashedBundle }) => ({ pathname, hashedBundle })),
    [
      { pathname: '/assets/app-abcdefgh.js', hashedBundle: true },
      { pathname: '/assets/index-12345678.css', hashedBundle: true },
    ],
  );
});

test('verifies a fresh protected shell and every referenced asset without sending Access headers to assets', async () => {
  const requests = [];
  const fetchImpl = async (input, options) => {
    const url = new URL(input);
    requests.push({ url, options });
    if (url.pathname === '/staff') return htmlResponse(pageHtml());
    return assetResponse(url.pathname);
  };

  const result = await verifyProductionStaffAssets(baseOptions({ fetchImpl }));

  assert.deepEqual(result, { attempt: 1, assetCount: 2 });
  const documentRequest = requests.find(({ url }) => url.pathname === '/staff');
  assert.ok(documentRequest.url.searchParams.has('__staff_asset_readiness'));
  assert.equal(documentRequest.options.headers['CF-Access-Client-Id'], ACCESS_ID);
  assert.equal(documentRequest.options.headers['CF-Access-Client-Secret'], ACCESS_SECRET);
  assert.equal(documentRequest.options.headers['Cache-Control'], 'no-cache, no-store');

  const assetRequests = requests.filter(({ url }) => url.pathname.startsWith('/assets/'));
  assert.equal(assetRequests.length, 2);
  for (const { options } of assetRequests) {
    assert.equal(options.headers['CF-Access-Client-Id'], undefined);
    assert.equal(options.headers['CF-Access-Client-Secret'], undefined);
  }
});

test('fetches a fresh document and retries only after a hashed bundle returns 404', async () => {
  let documentReads = 0;
  const documentUrls = [];
  const waits = [];
  const fetchImpl = async input => {
    const url = new URL(input);
    if (url.pathname === '/staff') {
      documentReads += 1;
      documentUrls.push(url.href);
      return htmlResponse(documentReads === 1
        ? pageHtml('app-oldhash1.js', 'index-oldcss1.css')
        : pageHtml('app-newhash2.js', 'index-newcss2.css'));
    }
    if (url.pathname === '/assets/app-oldhash1.js') {
      return assetResponse(url.pathname, 404);
    }
    return assetResponse(url.pathname);
  };

  const result = await verifyProductionStaffAssets(baseOptions({
    fetchImpl,
    wait: async milliseconds => waits.push(milliseconds),
  }));

  assert.deepEqual(result, { attempt: 2, assetCount: 2 });
  assert.equal(documentReads, 2);
  assert.notEqual(documentUrls[0], documentUrls[1]);
  assert.deepEqual(waits, [25]);
});

test('retries a network failure for a hashed bundle but not other request types', async () => {
  let documentReads = 0;
  let failedBundleRequests = 0;
  const waits = [];
  const fetchImpl = async input => {
    const url = new URL(input);
    if (url.pathname === '/staff') {
      documentReads += 1;
      return htmlResponse(pageHtml());
    }
    if (url.pathname === '/assets/app-abcdefgh.js' && failedBundleRequests++ === 0) {
      throw new TypeError('network failure sentinel');
    }
    return assetResponse(url.pathname);
  };

  const result = await verifyProductionStaffAssets(baseOptions({
    fetchImpl,
    wait: async milliseconds => waits.push(milliseconds),
  }));

  assert.deepEqual(result, { attempt: 2, assetCount: 2 });
  assert.equal(documentReads, 2);
  assert.deepEqual(waits, [25]);
});

test('fails immediately on a protected-document authorization response', async () => {
  let requests = 0;
  await assert.rejects(
    verifyProductionStaffAssets(baseOptions({
      fetchImpl: async () => {
        requests += 1;
        return new Response('denied', { status: 403 });
      },
    })),
    error => {
      assert.match(error.message, /\/staff returned HTTP 403/);
      assert.doesNotMatch(error.message, /access-secret-sentinel/);
      return true;
    },
  );
  assert.equal(requests, 1);
});

test('fails immediately when a 200 HTML response is not a staff app shell', async () => {
  let requests = 0;
  await assert.rejects(
    verifyProductionStaffAssets(baseOptions({
      fetchImpl: async () => {
        requests += 1;
        return htmlResponse('<html><body>Access login</body></html>');
      },
    })),
    /fresh \/staff document contains no hashed JavaScript bundle/,
  );
  assert.equal(requests, 1);
});

test('fails immediately on a hashed asset authorization or server error', async t => {
  for (const status of [403, 500]) {
    await t.test(`HTTP ${status}`, async () => {
      let documentReads = 0;
      const waits = [];
      const fetchImpl = async input => {
        const url = new URL(input);
        if (url.pathname === '/staff') {
          documentReads += 1;
          return htmlResponse(pageHtml());
        }
        if (url.pathname === '/assets/app-abcdefgh.js') {
          return assetResponse(url.pathname, status);
        }
        return assetResponse(url.pathname);
      };

      await assert.rejects(
        verifyProductionStaffAssets(baseOptions({
          fetchImpl,
          wait: async milliseconds => waits.push(milliseconds),
        })),
        new RegExp(`app-abcdefgh\\.js returned HTTP ${status}`),
      );
      assert.equal(documentReads, 1);
      assert.deepEqual(waits, []);
    });
  }
});

test('does not retry a non-hashed /assets path that returns 404', async () => {
  let documentReads = 0;
  const fetchImpl = async input => {
    const url = new URL(input);
    if (url.pathname === '/staff') {
      documentReads += 1;
      return htmlResponse(
        `${pageHtml()}<img src="/assets/logo.svg">`,
      );
    }
    if (url.pathname === '/assets/logo.svg') return assetResponse(url.pathname, 404);
    return assetResponse(url.pathname);
  };

  await assert.rejects(
    verifyProductionStaffAssets(baseOptions({ fetchImpl })),
    /\/assets\/logo\.svg returned HTTP 404/,
  );
  assert.equal(documentReads, 1);
});

test('fails after the bounded attempt count when hashed assets remain missing', async () => {
  let documentReads = 0;
  const waits = [];
  const fetchImpl = async input => {
    const url = new URL(input);
    if (url.pathname === '/staff') {
      documentReads += 1;
      return htmlResponse(pageHtml());
    }
    if (url.pathname === '/assets/app-abcdefgh.js') {
      return assetResponse(url.pathname, 404);
    }
    return assetResponse(url.pathname);
  };

  await assert.rejects(
    verifyProductionStaffAssets(baseOptions({
      fetchImpl,
      maxAttempts: 3,
      wait: async milliseconds => waits.push(milliseconds),
    })),
    /readiness window ended after 3 attempts/,
  );
  assert.equal(documentReads, 3);
  assert.deepEqual(waits, [25, 25]);
});

test('runs production asset readiness before creating the disposable account or checking the portal', async () => {
  const workflow = await readFile(
    new URL('../.github/workflows/deploy-cloudflare.yml', import.meta.url),
    'utf8',
  );
  const staffJobStart = workflow.indexOf('  disposable-staff-auth:');
  const nextJobStart = workflow.indexOf('\n  release-check-summary:', staffJobStart);
  assert.notEqual(staffJobStart, -1);
  assert.notEqual(nextJobStart, -1);

  const staffJob = workflow.slice(staffJobStart, nextJobStart);
  const readiness = staffJob.indexOf('run: node scripts/check-production-staff-assets.mjs');
  const fixture = staffJob.indexOf('run: bash scripts/run-disposable-staff-auth-cutover.sh');
  const portal = staffJob.indexOf('run: bash scripts/run-disposable-staff-portal-check.sh');

  assert.notEqual(readiness, -1);
  assert.ok(readiness < fixture);
  assert.ok(fixture < portal);
});