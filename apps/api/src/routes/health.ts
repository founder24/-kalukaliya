import { Hono } from 'hono';
import type { Env } from '../types';

export const healthRouter = new Hono<{ Bindings: Env }>();

type CheckStatus = 'healthy' | 'error' | 'unbound';

interface HealthCheck {
  status: CheckStatus;
  latency_ms: number;
  detail?: string;
}

const PROBE_TIMEOUT_MS = 4_000;
const REQUIRED_BINDINGS = [
  'DB',
  'AI',
  'VECTORIZE',
  'R2_BUCKET',
  'CONTENT_KV',
  'RATE_LIMIT_KV',
] as const;

async function boundedProbe(
  operation: () => Promise<unknown>,
  timeoutMs = PROBE_TIMEOUT_MS,
): Promise<HealthCheck> {
  const startedAt = Date.now();
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      operation(),
      new Promise<never>((_, reject) => {
        timeoutId = setTimeout(
          () => reject(new Error(`probe timed out after ${timeoutMs}ms`)),
          timeoutMs,
        );
      }),
    ]);
    return { status: 'healthy', latency_ms: Date.now() - startedAt };
  } catch (err) {
    return {
      status: 'error',
      latency_ms: Date.now() - startedAt,
      detail: err instanceof Error ? err.message : 'unknown probe failure',
    };
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
  }
}

function unboundCheck(): HealthCheck {
  return { status: 'unbound', latency_ms: 0, detail: 'required binding is absent' };
}

export async function runDeepHealthChecks(env: Env): Promise<{
  checks: Record<string, HealthCheck>;
  missing_bindings: string[];
}> {
  const missingBindings = REQUIRED_BINDINGS.filter((name) => !env[name]);
  const checks: Record<string, HealthCheck> = {};

  const probes: Array<[string, keyof Env, () => Promise<unknown>]> = [
    ['d1', 'DB', () => env.DB.prepare('SELECT 1').first()],
    [
      'workers_ai',
      'AI',
      () => env.AI.run('@cf/baai/bge-m3' as any, { text: ['health probe'] }),
    ],
    ['vectorize', 'VECTORIZE', () => env.VECTORIZE.describe()],
    ['r2', 'R2_BUCKET', () => env.R2_BUCKET.head('__health_probe__')],
    ['content_kv', 'CONTENT_KV', () => env.CONTENT_KV.get('__health_probe__')],
    ['rate_limit_kv', 'RATE_LIMIT_KV', () => env.RATE_LIMIT_KV.get('__health_probe__')],
  ];

  await Promise.all(probes.map(async ([label, binding, operation]) => {
    checks[label] = env[binding] ? await boundedProbe(operation) : unboundCheck();
  }));

  return { checks, missing_bindings: missingBindings };
}

healthRouter.get('/', async (c) => {
  const db = c.env.DB;
  let dbStatus = 'unknown';

  try {
    await db.prepare('SELECT 1').first();
    dbStatus = 'healthy';
  } catch (err) {
    dbStatus = `error: ${err instanceof Error ? err.message : 'unknown'}`;
  }

  const vectorizeStatus = c.env.VECTORIZE ? 'bound' : 'unbound';
  const r2Status = c.env.R2_BUCKET ? 'bound' : 'unbound';
  const kvStatus = c.env.CONTENT_KV ? 'bound' : 'unbound';

  const allHealthy = dbStatus === 'healthy';

  return c.json({
    status: allHealthy ? 'healthy' : 'degraded',
    service: 'syrabit-api',
    runtime: 'cloudflare-workers',
    timestamp: new Date().toISOString(),
    components: {
      d1: dbStatus,
      vectorize: vectorizeStatus,
      r2: r2Status,
      kv: kvStatus,
    },
  }, allHealthy ? 200 : 503);
});

healthRouter.get('/deep', async (c) => {
  const authorization = c.req.header('Authorization');
  if (
    !c.env.EDGE_SHARED_SECRET
    || authorization !== `Bearer ${c.env.EDGE_SHARED_SECRET}`
  ) {
    return c.json({ detail: 'Deep health authorization required' }, 401);
  }

  const { checks, missing_bindings } = await runDeepHealthChecks(c.env);
  const allHealthy = Object.values(checks).every((check) => check.status === 'healthy');

  return c.json({
    status: allHealthy ? 'healthy' : 'degraded',
    service: 'syrabit-api',
    runtime: 'cloudflare-workers',
    timestamp: new Date().toISOString(),
    checks,
    missing_bindings,
    mutation_free: true,
  }, allHealthy ? 200 : 503);
});
