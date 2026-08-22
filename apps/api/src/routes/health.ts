import { Hono } from 'hono';
import type { Env } from '../types';

export const healthRouter = new Hono<{ Bindings: Env }>();

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
  return c.json({
    status: 'healthy',
    service: 'syrabit-api',
    runtime: 'cloudflare-workers',
    timestamp: new Date().toISOString(),
    mongodb: 'not_applicable',
    d1: 'primary',
  });
});
