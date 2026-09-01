import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import {
  buildCloudflareAnalyticsHealthQuery,
  CLOUDFLARE_ANALYTICS_CONTRACT,
  EXPECTED_PRODUCTION_BINDINGS,
  PRODUCTION_ENDPOINTS,
  PRODUCTION_SERVICES,
  REQUIRED_PRODUCTION_BINDINGS,
} from '../../scripts/cloudflare-production-contract.mjs';

const scriptsDir = path.resolve(process.cwd(), 'scripts');
const repoRoot = path.resolve(process.cwd(), '../..');
const scheduledAudits = [
  'cloudflare-annual-review.js',
  'nightly-smoke.js',
  'cloudflare-full-audit.js',
];

function readAuditSource(fileName) {
  return fs.readFileSync(path.join(scriptsDir, fileName), 'utf8');
}

describe('scheduled Cloudflare audit contract', () => {
  it('defines the active production services and all required binding types once', () => {
    expect(PRODUCTION_SERVICES).toEqual({
      edge: 'syrabitworker-prod',
      api: 'syrabit-api-prod',
    });

    const bindings = EXPECTED_PRODUCTION_BINDINGS.flatMap(({ bindings: entries }) => entries);
    const bindingNames = bindings.map(([name]) => name);

    for (const name of REQUIRED_PRODUCTION_BINDINGS) {
      expect(bindingNames, `missing shared production binding: ${name}`).toContain(name);
    }

    expect(bindings.find(([name]) => name === 'RATE_LIMIT_DO')).toEqual([
      'RATE_LIMIT_DO',
      'durable_object_namespace',
    ]);
    expect(bindings.find(([name]) => name === 'API_WORKER')).toEqual([
      'API_WORKER',
      'service',
      PRODUCTION_SERVICES.api,
    ]);
    expect(bindings.find(([name]) => name === 'DB')).toEqual(['DB', 'd1']);
    expect(bindings.find(([name]) => name === 'VECTORIZE')).toEqual(['VECTORIZE', 'vectorize']);
    expect(bindings.find(([name]) => name === 'R2_BUCKET')).toEqual(['R2_BUCKET', 'r2_bucket']);
    expect(bindings.find(([name]) => name === 'CONTENT_KV')).toEqual([
      'CONTENT_KV',
      'kv_namespace',
    ]);
    expect(bindings.find(([name]) => name === 'AI')).toEqual(['AI', 'ai']);
  });

  it('defines the Cloudflare-native analytics endpoint and hourly query shape', () => {
    expect(PRODUCTION_ENDPOINTS.cloudflareGraphql).toBe(
      'https://api.cloudflare.com/client/v4/graphql',
    );
    expect(CLOUDFLARE_ANALYTICS_CONTRACT).toMatchObject({
      endpoint: PRODUCTION_ENDPOINTS.cloudflareGraphql,
      rangeHours: 24,
      groupsField: 'httpRequests1hGroups',
      dimensions: 'datetime',
    });

    const query = buildCloudflareAnalyticsHealthQuery('2026-09-01T12:00:00.000Z');
    expect(query).toContain('httpRequests1hGroups');
    expect(query).toContain('uniqueVisitors: httpRequestsAdaptiveGroups');
    expect(query).toContain('datetime_geq: "2026-08-31T12:00:00.000Z"');
    expect(query).toContain('datetime_lt: "2026-09-01T12:00:00.000Z"');
  });

  it('keeps scheduled health workflows on the active contract with non-blocking escalation', () => {
    const analyticsWorkflow = fs.readFileSync(
      path.join(repoRoot, '.github/workflows/cloudflare-analytics-health.yml'),
      'utf8',
    );
    expect(analyticsWorkflow).toContain('buildCloudflareAnalyticsHealthQuery');
    expect(analyticsWorkflow).toContain('CLOUDFLARE_ANALYTICS_CONTRACT.endpoint');
    expect(analyticsWorkflow).not.toContain('/api/v1/admin/cron/cloudflare-analytics-health');

    const uptimeWorkflow = fs.readFileSync(
      path.join(repoRoot, '.github/workflows/agent-uptime-monitor.yml'),
      'utf8',
    );
    expect(uptimeWorkflow).toMatch(/permissions:\s+[\s\S]*contents: write/);
    expect(uptimeWorkflow).toMatch(
      /id: dispatch\s+continue-on-error: true[\s\S]*createDispatchEvent/,
    );
    expect(uptimeWorkflow).toContain('Notification result (non-blocking)');
    expect(uptimeWorkflow).toContain('Production uptime probes failed');
  });

  it.each(scheduledAudits)('%s consumes the shared service and binding contract', (fileName) => {
    const source = readAuditSource(fileName);

    expect(source).toContain("from './cloudflare-production-contract.mjs'");
    expect(source).toContain('EXPECTED_PRODUCTION_BINDINGS');
    expect(source).toMatch(
      /for\s*\(\s*const\s*\{\s*service,\s*bindings:\s*expectedBindings\s*\}\s*of\s+EXPECTED_PRODUCTION_BINDINGS\s*\)/,
    );
    expect(source).not.toMatch(/const\s+PRODUCTION_SERVICES\s*=/);
    expect(source).not.toMatch(/const\s+EXPECTED_PRODUCTION_BINDINGS\s*=/);
  });
});