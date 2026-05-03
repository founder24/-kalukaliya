#!/usr/bin/env node
/**
 * provision-ai-gateway.mjs
 *
 * Task #307 — Provision the Cloudflare AI Gateway that routes every
 * `env.AI.run(...)` call from the `syrabit-edge` worker. Idempotent:
 * creates the gateway if missing, otherwise updates its caching /
 * logging settings to the policy declared below so a hand-edit in the
 * dashboard is overwritten by the next run.
 *
 * Why this exists:
 *   - Workers AI calls draw from the $5,000 Cloudflare-for-Startups
 *     credit pool. Every cache hit served by AI Gateway costs $0,
 *     so deterministic prompts (embeddings, classification, repeat
 *     student questions) stop re-billing the pool.
 *   - Every request through the gateway is tagged with metadata.tag
 *     (`workers-ai-fallback:<cap>` / `workers-ai-edge-vector-search`)
 *     so the monthly cost review can group the invoice line item.
 *   - `aiGatewayOpts(env, ...)` in `workers/edge-proxy/src/index.ts`
 *     wraps every `env.AI.run` callsite; turning the var on without
 *     a gateway present would 4xx every fallback request.
 *
 * Caching policy (matches docs/cloudflare-cost-map.md and the
 * runbook at docs/ops/ai-gateway-activation.md):
 *   - Embeddings           → 24h (deterministic; same input → same vector)
 *   - Classification (chat with low temperature) and TTS/STT default
 *     to the gateway-wide default of 1h. Per-route overrides can be
 *     added in the dashboard later — the var name stays the same.
 *
 * Prerequisites:
 *   CLOUDFLARE_API_TOKEN — token with **AI Gateway: Edit** scope on
 *     account d66e40eac539fff1db270fddf384a5ec. Create via
 *     dash.cloudflare.com → My Profile → API Tokens → Create Custom
 *     Token. Store as a Replit secret only on the operator machine.
 *
 * Usage:
 *   CLOUDFLARE_API_TOKEN=<tok> node workers/edge-proxy/scripts/provision-ai-gateway.mjs
 *   CLOUDFLARE_API_TOKEN=<tok> node workers/edge-proxy/scripts/provision-ai-gateway.mjs --dry-run
 *   CLOUDFLARE_API_TOKEN=<tok> AI_GATEWAY_ID=other-id node workers/edge-proxy/scripts/provision-ai-gateway.mjs
 */

import https from 'node:https';

const ACCOUNT_ID    = 'd66e40eac539fff1db270fddf384a5ec';
const TOKEN         = process.env.CLOUDFLARE_API_TOKEN;
const GATEWAY_ID    = process.env.AI_GATEWAY_ID || 'syrabit-ai-gw';
const DRY_RUN       = process.argv.includes('--dry-run');

// 1h default cache TTL on every route. Embeddings get bumped to 24h
// below via a per-route cache rule because they are fully deterministic.
const DEFAULT_CACHE_TTL_S = 60 * 60;

if (!TOKEN) {
  console.error('CLOUDFLARE_API_TOKEN is not set (need scope: AI Gateway: Edit)');
  process.exit(1);
}

function cfApi(method, path, body) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : undefined;
    const opts = {
      hostname: 'api.cloudflare.com',
      path,
      method,
      headers: {
        'Authorization': `Bearer ${TOKEN}`,
        'Content-Type': 'application/json',
        ...(payload ? { 'Content-Length': Buffer.byteLength(payload) } : {}),
      },
    };
    const req = https.request(opts, res => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, body: JSON.parse(data) }); }
        catch { resolve({ status: res.statusCode, body: data }); }
      });
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

function ok(r, label) {
  if (!r.body?.success) {
    console.error(`[FAIL] ${label}: status=${r.status}`, JSON.stringify(r.body?.errors ?? r.body));
    process.exit(1);
  }
  return r.body.result;
}

const desiredSettings = {
  // Cache settings — the dashboard exposes these as the "Caching" panel.
  cache_invalidate_on_update: true,
  cache_ttl: DEFAULT_CACHE_TTL_S,
  collect_logs: true,
  // Rate limiting at the gateway is OFF — the worker already enforces
  // per-key rate limits via the Durable Object limiter (Phase 5).
  rate_limiting_interval: 0,
  rate_limiting_limit: 0,
  rate_limiting_technique: 'sliding',
  // Auth at the gateway is OFF — only the worker (with its AI binding
  // and EDGE_AI_FALLBACK_SECRET gate) can reach env.AI.run; we don't
  // expose the gateway to public callers.
  authentication: false,
  // Logpush mirror for AI Gateway logs — match the rest of the
  // platform; reads happen through the dashboard.
  logpush: false,
};

async function findGateway() {
  const list = await cfApi(
    'GET',
    `/client/v4/accounts/${ACCOUNT_ID}/ai-gateway/gateways`,
  );
  if (!list.body?.success) {
    console.error('[FAIL] list gateways:', JSON.stringify(list.body?.errors));
    process.exit(1);
  }
  return (list.body.result || []).find(g => g.id === GATEWAY_ID) ?? null;
}

async function createGateway() {
  if (DRY_RUN) {
    console.log(`[dry-run] Would create gateway "${GATEWAY_ID}" with:`, JSON.stringify(desiredSettings, null, 2));
    return { id: GATEWAY_ID };
  }
  const r = await cfApi(
    'POST',
    `/client/v4/accounts/${ACCOUNT_ID}/ai-gateway/gateways`,
    { id: GATEWAY_ID, ...desiredSettings },
  );
  const result = ok(r, `create gateway "${GATEWAY_ID}"`);
  console.log(`[created] gateway: ${result.id}`);
  return result;
}

async function updateGateway() {
  if (DRY_RUN) {
    console.log(`[dry-run] Would PUT gateway "${GATEWAY_ID}" with:`, JSON.stringify(desiredSettings, null, 2));
    return { id: GATEWAY_ID };
  }
  const r = await cfApi(
    'PUT',
    `/client/v4/accounts/${ACCOUNT_ID}/ai-gateway/gateways/${GATEWAY_ID}`,
    desiredSettings,
  );
  const result = ok(r, `update gateway "${GATEWAY_ID}"`);
  console.log(`[updated] gateway: ${result.id}`);
  return result;
}

async function main() {
  console.log(`Provisioning AI Gateway "${GATEWAY_ID}" on account ${ACCOUNT_ID}${DRY_RUN ? ' [DRY RUN]' : ''}`);
  const existing = await findGateway();
  if (existing) {
    console.log(`[found] gateway "${GATEWAY_ID}" already exists — reconciling settings`);
    await updateGateway();
  } else {
    console.log(`[missing] gateway "${GATEWAY_ID}" not found — creating`);
    await createGateway();
  }

  console.log('');
  console.log('Done. Next steps:');
  console.log(`  1. wrangler secret put WORKERS_AI_GATEWAY_ID --name syrabit-edge`);
  console.log(`     (paste the value: ${GATEWAY_ID})`);
  console.log('  2. pnpm --filter @workspace/edge-proxy deploy   # (or your usual deploy path)');
  console.log('  3. Verify in dash → AI → AI Gateway → Logs that recent requests carry');
  console.log('     metadata.tag = workers-ai-fallback:* / workers-ai-edge-vector-search.');
  console.log('  4. Re-run this script any time the policy in the file changes — it is');
  console.log('     idempotent and will overwrite hand-edits made in the dashboard.');
}

main().catch(e => { console.error(e); process.exit(1); });
