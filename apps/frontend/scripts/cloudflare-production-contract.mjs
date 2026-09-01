/**
 * Shared contract for the Cloudflare Workers that serve production traffic.
 *
 * Keep service names and binding expectations here so scheduled audits cannot
 * drift apart when the active infrastructure changes.
 */

export const PRODUCTION_SERVICES = Object.freeze({
  edge: 'syrabitworker-prod',
  api: 'syrabit-api-prod',
});

export const PRODUCTION_ENDPOINTS = Object.freeze({
  edgeHealth: 'https://api.syrabit.ai/health',
  apiWorkerHealth: 'https://syrabit-api-prod.axomxplain.workers.dev/health',
  cloudflareGraphql: 'https://api.cloudflare.com/client/v4/graphql',
});

export const CLOUDFLARE_ANALYTICS_CONTRACT = Object.freeze({
  endpoint: PRODUCTION_ENDPOINTS.cloudflareGraphql,
  rangeHours: 24,
  groupsField: 'httpRequests1hGroups',
  dimensions: 'datetime',
});

/**
 * Build the read-only query used by the scheduled analytics health check.
 * Keep this next to the production endpoint contract so CI does not silently
 * drift from the query shape used by the analytics dashboard.
 */
export function buildCloudflareAnalyticsHealthQuery(now = new Date()) {
  const until = now instanceof Date ? now : new Date(now);
  if (Number.isNaN(until.getTime())) {
    throw new TypeError('now must be a valid Date or date string');
  }
  const since = new Date(
    until.getTime() - CLOUDFLARE_ANALYTICS_CONTRACT.rangeHours * 60 * 60 * 1000,
  );
  const sinceIso = since.toISOString();
  const untilIso = until.toISOString();

  return `
    query ($zoneTag: string) {
      viewer {
        zones(filter: {zoneTag: $zoneTag}) {
          ${CLOUDFLARE_ANALYTICS_CONTRACT.groupsField}(
            limit: ${CLOUDFLARE_ANALYTICS_CONTRACT.rangeHours}
            filter: {
              datetime_geq: "${sinceIso}"
              datetime_lt: "${untilIso}"
            }
          ) {
            dimensions { ${CLOUDFLARE_ANALYTICS_CONTRACT.dimensions} }
            sum { requests pageViews threats bytes }
            uniq { uniques }
          }
          uniqueVisitors: httpRequestsAdaptiveGroups(
            limit: 1
            filter: {datetime_geq: "${sinceIso}", datetime_lt: "${untilIso}"}
          ) {
            uniq { uniques }
          }
        }
      }
    }
  `;
}

export const AI_PLUGIN_METADATA = Object.freeze({
  schema_version: 'v1',
  name_for_human: 'Syrabit.ai',
  name_for_model: 'syrabit_ai',
  url: 'https://syrabit.ai',
  api_schema_url: 'https://syrabit.ai/.well-known/openapi.json',
  api_url: 'https://api.syrabit.ai',
  contact_email: 'founder@syrabit.ai',
});

/**
 * Each tuple is [binding name, Cloudflare binding type, optional service target].
 * The shape intentionally matches the checks in the annual review, nightly
 * smoke, and full audit.
 */
export const EXPECTED_PRODUCTION_BINDINGS = Object.freeze([
  {
    service: PRODUCTION_SERVICES.edge,
    bindings: Object.freeze([
      Object.freeze(['RATE_LIMIT_DO', 'durable_object_namespace']),
      Object.freeze(['API_WORKER', 'service', PRODUCTION_SERVICES.api]),
      Object.freeze(['RATE_LIMIT_KV', 'kv_namespace']),
      Object.freeze(['ISR_CACHE_KV', 'kv_namespace']),
      Object.freeze(['CONTENT_KV', 'kv_namespace']),
      Object.freeze(['R2_BUCKET', 'r2_bucket']),
      Object.freeze(['AI', 'ai']),
    ]),
  },
  {
    service: PRODUCTION_SERVICES.api,
    bindings: Object.freeze([
      Object.freeze(['DB', 'd1']),
      Object.freeze(['R2_BUCKET', 'r2_bucket']),
      Object.freeze(['CONTENT_KV', 'kv_namespace']),
      Object.freeze(['RATE_LIMIT_KV', 'kv_namespace']),
      Object.freeze(['VECTORIZE', 'vectorize']),
      Object.freeze(['AI', 'ai']),
    ]),
  },
]);

/**
 * Names that every scheduled audit must continue to cover. The aliases in
 * comments map implementation names to the product terminology used in the
 * audit requirement: DB is D1, and the *_KV bindings are KV.
 */
export const REQUIRED_PRODUCTION_BINDINGS = Object.freeze([
  'RATE_LIMIT_DO',
  'API_WORKER',
  'DB',        // D1
  'VECTORIZE',
  'R2_BUCKET', // R2
  'CONTENT_KV', // KV
  'RATE_LIMIT_KV',
  'AI',        // Workers AI
]);