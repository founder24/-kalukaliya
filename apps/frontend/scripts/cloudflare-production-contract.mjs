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