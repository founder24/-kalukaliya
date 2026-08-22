/**
 * Low-latency operational routes that previously depended on Cloud Run.
 *
 * These endpoints intentionally keep the public contracts small and stable:
 * browser beacons must never fail a navigation, public configuration must
 * degrade to null, and IndexNow remains an authenticated server-to-server API.
 */

import { Hono } from 'hono';
import type { Env } from '../types';

export const analyticsRouter = new Hono<{ Bindings: Env }>();
export const configRouter = new Hono<{ Bindings: Env }>();
export const indexNowRouter = new Hono<{ Bindings: Env }>();
export const changelogRouter = new Hono<{ Bindings: Env }>();

const ANALYTICS_BEACONS = new Set([
  'session-ping',
  'session-end',
  'page-view',
  'review-prompt-event',
  'ad-impression',
  'hydrate-event',
]);

/**
 * Cloud Run treated analytics as best-effort logging. Preserve that behaviour:
 * accept any JSON or beacon payload, never surface parsing errors to the client,
 * and return the established acknowledgement envelope.
 */
analyticsRouter.post('/:event', async (c) => {
  const event = c.req.param('event');
  if (!ANALYTICS_BEACONS.has(event)) {
    return c.json({ detail: 'Not found' }, 404);
  }

  try {
    // Consume the payload so beacon bodies are fully handled before responding.
    // No durable write was performed by the Cloud Run implementation.
    await c.req.json();
  } catch {
    // sendBeacon and older browser clients can send an empty or malformed body.
  }

  return c.json({ status: 'ok' });
});

analyticsRouter.get('/top-routes', (c) =>
  c.json({ routes: [], period: '7d' }));

/**
 * Trustpilot values are safe public configuration. GCP Secret Manager is the
 * canonical production source and deploy.yml mirrors configured values into
 * Worker bindings. Keeping one source avoids an undocumented D1 override that
 * could make a deployment’s public response unexpectedly stale.
 */
configRouter.get('/trustpilot', async (c) => {
  const profileUrl = c.env.TRUSTPILOT_PROFILE_URL ?? null;
  const businessUnitId = c.env.TRUSTPILOT_BUSINESS_UNIT_ID ?? null;

  if (!profileUrl && !businessUnitId) return c.json(null);
  return c.json({ profileUrl, businessUnitId });
});

configRouter.get('/trustpilot/aggregate', async (c) => {
  const ratingValue = Number(c.env.TRUSTPILOT_RATING_VALUE);
  const ratingCount = Number(c.env.TRUSTPILOT_RATING_COUNT);

  if (!Number.isFinite(ratingValue) || !Number.isFinite(ratingCount) || ratingCount <= 0) {
    return c.json(null);
  }
  return c.json({ ratingValue, ratingCount: Math.trunc(ratingCount) });
});

indexNowRouter.post('/submit', async (c) => {
  const suppliedSecret = c.req.header('X-IndexNow-Secret');
  if (!suppliedSecret) return c.json({ detail: 'Missing IndexNow secret' }, 403);
  if (!c.env.INDEXNOW_API_KEY) return c.json({ detail: 'INDEXNOW_API_KEY not configured' }, 500);
  if (!c.env.INDEXNOW_INTERNAL_SECRET) {
    return c.json({ detail: 'INDEXNOW_INTERNAL_SECRET not configured' }, 500);
  }
  if (suppliedSecret !== c.env.INDEXNOW_INTERNAL_SECRET) {
    return c.json({ detail: 'Invalid IndexNow secret' }, 403);
  }

  let body: { urls?: unknown };
  try {
    body = await c.req.json<{ urls?: unknown }>();
  } catch {
    return c.json({ detail: 'Invalid request body' }, 422);
  }
  if (!Array.isArray(body.urls) || body.urls.some((url) => typeof url !== 'string')) {
    return c.json({ detail: 'urls must be an array of strings' }, 422);
  }

  const urls = body.urls;
  if (urls.length === 0) {
    return c.json({ submitted: 0, failed: 0, detail: 'No URLs provided' });
  }

  let submitted = 0;
  let failed = 0;
  for (let i = 0; i < urls.length; i += 100) {
    const batch = urls.slice(i, i + 100);
    try {
      const response = await fetch('https://api.indexnow.org/indexnow', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host: 'syrabit.ai', key: c.env.INDEXNOW_API_KEY, urlList: batch }),
      });
      if (response.status === 200 || response.status === 202) submitted += batch.length;
      else failed += batch.length;
    } catch {
      failed += batch.length;
    }
  }

  return c.json({
    submitted,
    failed,
    detail: `Processed ${urls.length} URLs in batches of 100`,
  });
});

const CHANGELOG = [{
  version: '3.0.0',
  date: '2025-01-01',
  changes: ['Initial stable API release'],
}];

changelogRouter.get('/', (c) => c.json(CHANGELOG));
changelogRouter.get('/changelog', (c) => c.json(CHANGELOG));