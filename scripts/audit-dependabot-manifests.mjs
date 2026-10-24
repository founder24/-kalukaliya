import { appendFile } from 'node:fs/promises';

const API_ROOT = 'https://api.github.com';
const API_VERSION = '2022-11-28';

function validateRepository(repository) {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository || '')) {
    throw new Error('GITHUB_REPOSITORY must be in owner/repository format');
  }
}

function isSafeRepoPath(path) {
  return typeof path === 'string'
    && path.length > 0
    && !path.startsWith('/')
    && !path.includes('\\')
    && !path.includes('\0')
    && !path.split('/').some((part) => part === '' || part === '.' || part === '..');
}

function nextLink(linkHeader) {
  const match = linkHeader?.match(/<([^>]+)>\s*;\s*rel="?next"?/);
  return match?.[1] || null;
}

async function getJson(url, { token, fetchImpl }) {
  const response = await fetchImpl(url, {
    method: 'GET',
    headers: {
      Accept: 'application/vnd.github+json',
      Authorization: `Bearer ${token}`,
      'X-GitHub-Api-Version': API_VERSION,
    },
  });

  if (!response.ok) {
    throw new Error(`GitHub GET returned HTTP ${response.status}`);
  }
  return response.json();
}

async function getAlerts(repository, { token, fetchImpl }) {
  let url = `${API_ROOT}/repos/${repository}/dependabot/alerts?state=open&per_page=100`;
  const alerts = [];

  while (url) {
    const response = await fetchImpl(url, {
      method: 'GET',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${token}`,
        'X-GitHub-Api-Version': API_VERSION,
      },
    });
    if (!response.ok) {
      throw new Error(`Dependabot alerts request returned HTTP ${response.status}`);
    }
    const page = await response.json();
    if (!Array.isArray(page)) {
      throw new Error('Dependabot alerts API returned an unexpected response');
    }
    alerts.push(...page);
    url = nextLink(response.headers.get('link'));
  }

  return alerts;
}

function encodeRepoPath(path) {
  return path.split('/').map(encodeURIComponent).join('/');
}

async function inspectTruncatedTree(paths, repository, branch, options) {
  const results = new Map();

  for (const path of paths) {
    if (!isSafeRepoPath(path)) {
      results.set(path, {
        status: 'invalid',
        evidence: 'Manifest path is not a safe repository-relative path; no path lookup was made.',
      });
      continue;
    }

    const url = `${API_ROOT}/repos/${repository}/contents/${encodeRepoPath(path)}?ref=${encodeURIComponent(branch)}`;
    const response = await options.fetchImpl(url, {
      method: 'GET',
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${options.token}`,
        'X-GitHub-Api-Version': API_VERSION,
      },
    });

    if (response.status === 200) {
      results.set(path, {
        status: 'present',
        evidence: `Contents API returned HTTP 200 for ref ${branch}.`,
      });
    } else if (response.status === 404) {
      results.set(path, {
        status: 'missing',
        evidence: `Contents API returned HTTP 404 for ref ${branch}.`,
      });
    } else {
      throw new Error(`Manifest lookup returned HTTP ${response.status}`);
    }
  }

  return results;
}

export async function auditDependabotManifests({
  repository,
  token,
  fetchImpl = fetch,
}) {
  validateRepository(repository);
  if (!token) throw new Error('GITHUB_TOKEN is required');

  const options = { token, fetchImpl };
  const repo = await getJson(`${API_ROOT}/repos/${repository}`, options);
  const branch = repo.default_branch;
  if (typeof branch !== 'string' || !branch) {
    throw new Error('Repository response did not include a default branch');
  }

  const alerts = await getAlerts(repository, options);
  const commit = await getJson(
    `${API_ROOT}/repos/${repository}/commits/${encodeURIComponent(branch)}`,
    options,
  );
  const commitSha = commit.sha;
  const treeSha = commit.commit?.tree?.sha;
  if (!commitSha || !treeSha) {
    throw new Error('Default-branch commit response did not include commit and tree SHAs');
  }

  const tree = await getJson(
    `${API_ROOT}/repos/${repository}/git/trees/${treeSha}?recursive=1`,
    options,
  );
  if (!Array.isArray(tree.tree)) {
    throw new Error('Git tree API returned an unexpected response');
  }

  const paths = [...new Set(alerts.map((alert) => alert.dependency?.manifest_path ?? ''))];
  const pathResults = new Map();

  if (tree.truncated) {
    const fallback = await inspectTruncatedTree(paths, repository, branch, options);
    for (const [path, result] of fallback) pathResults.set(path, result);
  } else {
    const treePaths = new Set(tree.tree.map((entry) => entry.path));
    for (const path of paths) {
      if (!isSafeRepoPath(path)) {
        pathResults.set(path, {
          status: 'invalid',
          evidence: 'Manifest path is not a safe repository-relative path.',
        });
      } else if (treePaths.has(path)) {
        pathResults.set(path, {
          status: 'present',
          evidence: `Path is present in recursive tree ${treeSha} at commit ${commitSha}.`,
        });
      } else {
        pathResults.set(path, {
          status: 'missing',
          evidence: `Path is absent from complete recursive tree ${treeSha} at commit ${commitSha}.`,
        });
      }
    }
  }

  const rows = alerts.map((alert) => {
    const manifestPath = alert.dependency?.manifest_path ?? '';
    return {
      alertNumber: alert.number,
      alertUrl: alert.html_url || '',
      manifestPath,
      severity: alert.security_advisory?.severity || 'unknown',
      ghsaId: alert.security_advisory?.ghsa_id || 'unknown',
      ...pathResults.get(manifestPath),
    };
  });

  return {
    repository,
    branch,
    commitSha,
    treeSha,
    alertCount: alerts.length,
    rows,
    findings: rows.filter((row) => row.status === 'missing' || row.status === 'invalid'),
  };
}

function markdownCell(value) {
  return String(value ?? '')
    .replaceAll('|', '\\|')
    .replaceAll('\r', ' ')
    .replaceAll('\n', ' ')
    .replaceAll('`', "'")
    .replaceAll('[', '\\[')
    .replaceAll(']', '\\]');
}

export function formatAuditReport(report) {
  const lines = [
    '## Dependabot manifest-path audit',
    '',
    `- Repository: \`${markdownCell(report.repository)}\``,
    `- Default branch: \`${markdownCell(report.branch)}\``,
    `- Default-branch commit: \`${markdownCell(report.commitSha)}\``,
    `- Git tree: \`${markdownCell(report.treeSha)}\``,
    `- Open Dependabot alerts checked: ${report.alertCount}`,
    '',
  ];

  if (report.rows.length === 0) {
    lines.push('No open Dependabot alerts were found.');
    return `${lines.join('\n')}\n`;
  }

  lines.push(
    '| Alert | Severity | Manifest path | Result | Evidence |',
    '| --- | --- | --- | --- | --- |',
  );
  for (const row of report.rows) {
    const alertNumber = Number.isInteger(row.alertNumber) ? row.alertNumber : 'unknown';
    const alert = Number.isInteger(row.alertNumber)
      ? `[#${alertNumber}](https://github.com/${report.repository}/security/dependabot/${alertNumber})`
      : '#unknown';
    lines.push(
      `| ${alert} (${markdownCell(row.ghsaId)}) | ${markdownCell(row.severity)} | `
      + `${markdownCell(row.manifestPath)} | **${markdownCell(row.status)}** | `
      + `${markdownCell(row.evidence)} |`,
    );
  }

  lines.push(
    '',
    'This audit is read-only. It does not dismiss, alter, or close Dependabot alerts.',
    '',
  );
  return `${lines.join('\n')}\n`;
}

async function main() {
  const report = await auditDependabotManifests({
    repository: process.env.GITHUB_REPOSITORY,
    token: process.env.GITHUB_TOKEN,
  });
  const markdown = formatAuditReport(report);

  console.log(markdown);
  if (process.env.GITHUB_STEP_SUMMARY) {
    await appendFile(process.env.GITHUB_STEP_SUMMARY, markdown);
  }

  if (report.findings.length > 0) {
    console.error(`Found ${report.findings.length} alert(s) whose manifest path is missing or invalid.`);
    process.exitCode = 1;
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch(async (error) => {
    const message = `## Dependabot manifest-path audit failed\n\n${error.message}\n`;
    console.error(error.message);
    if (process.env.GITHUB_STEP_SUMMARY) {
      await appendFile(process.env.GITHUB_STEP_SUMMARY, message).catch(() => {});
    }
    process.exitCode = 1;
  });
}
