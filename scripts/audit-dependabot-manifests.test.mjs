import assert from 'node:assert/strict';
import test from 'node:test';
import {
  auditDependabotManifests,
  formatAuditReport,
} from './audit-dependabot-manifests.mjs';

const repo = 'owner/project';
const token = 'test-token';
const json = (body, status = 200, headers = {}) => new Response(JSON.stringify(body), {
  status,
  headers: { 'content-type': 'application/json', ...headers },
});

function alert(number, manifestPath) {
  return {
    number,
    html_url: `https://github.com/${repo}/security/dependabot/${number}`,
    dependency: { manifest_path: manifestPath },
    security_advisory: { severity: 'high', ghsa_id: `GHSA-test-${number}` },
  };
}

test('follows all alert pages and reports tree evidence without mutating alerts', async () => {
  const requests = [];
  const fetchImpl = async (url, init) => {
    requests.push({ url: String(url), method: init.method });
    if (String(url).includes('page=2')) return json([alert(12, 'old/removed.lock')]);
    if (String(url).includes('/dependabot/alerts?')) {
      return json([alert(11, 'requirements.txt')], 200, {
        link: `<https://api.github.com/repos/${repo}/dependabot/alerts?state=open&per_page=100&page=2>; rel="next"`,
      });
    }
    if (String(url) === `https://api.github.com/repos/${repo}`) {
      return json({ default_branch: 'main' });
    }
    if (String(url).endsWith('/commits/main')) {
      return json({ sha: 'commit-sha', commit: { tree: { sha: 'tree-sha' } } });
    }
    if (String(url).includes('/git/trees/tree-sha?recursive=1')) {
      return json({
        truncated: false,
        tree: [{ path: 'requirements.txt', type: 'blob' }],
      });
    }
    throw new Error(`Unexpected request: ${url}`);
  };

  const report = await auditDependabotManifests({ repository: repo, token, fetchImpl });
  const markdown = formatAuditReport(report);

  assert.equal(report.alertCount, 2);
  assert.equal(report.branch, 'main');
  assert.equal(report.rows[0].status, 'present');
  assert.equal(report.rows[1].status, 'missing');
  assert.match(report.rows[1].evidence, /complete recursive tree tree-sha/);
  assert.match(markdown, /#12/);
  assert.match(markdown, /old\/removed\.lock/);
  assert.match(markdown, /read-only/);
  assert.ok(requests.every((request) => request.method === 'GET'));
});

test('falls back to contents lookups when the recursive tree is truncated', async () => {
  const requests = [];
  const fetchImpl = async (url, init) => {
    requests.push({ url: String(url), method: init.method });
    if (String(url).includes('/dependabot/alerts?')) {
      return json([alert(21, 'present/requirements.txt'), alert(22, 'gone/requirements.txt')]);
    }
    if (String(url) === `https://api.github.com/repos/${repo}`) {
      return json({ default_branch: 'main' });
    }
    if (String(url).endsWith('/commits/main')) {
      return json({ sha: 'commit-sha', commit: { tree: { sha: 'tree-sha' } } });
    }
    if (String(url).includes('/git/trees/tree-sha?recursive=1')) {
      return json({ truncated: true, tree: [] });
    }
    if (String(url).includes('/contents/present/requirements.txt')) return json({ type: 'file' });
    if (String(url).includes('/contents/gone/requirements.txt')) return new Response('', { status: 404 });
    throw new Error(`Unexpected request: ${url}`);
  };

  const report = await auditDependabotManifests({ repository: repo, token, fetchImpl });

  assert.deepEqual(report.rows.map((row) => row.status), ['present', 'missing']);
  assert.match(report.rows[1].evidence, /HTTP 404/);
  assert.ok(requests.every((request) => request.method === 'GET'));
});

test('does not probe invalid paths and fails explicitly on unexpected API errors', async () => {
  const requests = [];
  const fetchImpl = async (url, init) => {
    requests.push({ url: String(url), method: init.method });
    if (String(url).includes('/dependabot/alerts?')) return json([alert(31, '../outside.lock')]);
    if (String(url) === `https://api.github.com/repos/${repo}`) {
      return json({ default_branch: 'main' });
    }
    if (String(url).endsWith('/commits/main')) {
      return json({ sha: 'commit-sha', commit: { tree: { sha: 'tree-sha' } } });
    }
    if (String(url).includes('/git/trees/tree-sha?recursive=1')) {
      return json({ truncated: false, tree: [] });
    }
    throw new Error(`Unexpected request: ${url}`);
  };

  const report = await auditDependabotManifests({ repository: repo, token, fetchImpl });
  assert.equal(report.rows[0].status, 'invalid');
  assert.equal(requests.some((request) => request.url.includes('/contents/')), false);
  await assert.rejects(
    auditDependabotManifests({
      repository: repo,
      token,
      fetchImpl: async () => new Response('unavailable', { status: 503 }),
    }),
    /HTTP 503/,
  );
});

test('rejects malformed repository identifiers and missing credentials', async () => {
  await assert.rejects(
    auditDependabotManifests({ repository: 'owner/../project', token, fetchImpl: async () => {} }),
    /owner\/repository format/,
  );
  await assert.rejects(
    auditDependabotManifests({ repository: repo, token: '', fetchImpl: async () => {} }),
    /GITHUB_TOKEN is required/,
  );
});
