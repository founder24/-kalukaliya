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

const MAX_ANALYTICS_BODY_BYTES = 16 * 1024;
const MAX_ANALYTICS_PAYLOAD_BYTES = 8 * 1024;
const MAX_ANALYTICS_DEPTH = 4;
const MAX_ANALYTICS_KEYS = 32;
const MAX_ANALYTICS_ARRAY_ITEMS = 20;
const MAX_ANALYTICS_STRING_LENGTH = 512;
const SENSITIVE_ANALYTICS_KEY = /(?:pass(?:word)?|secret|token|authorization|cookie|email|phone|address)/i;

type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

function sanitizeAnalyticsDimension(key: string, value: unknown, depth: number): JsonValue | undefined {
  const clean = sanitizeAnalyticsPayload(value, depth);
  if (!/^(?:route|path|url)$/i.test(key) || typeof clean !== 'string') return clean;
  try {
    // Query strings often carry search terms or identifiers; they are never an
    // analytics dimension we need to retain.
    return new URL(clean, 'https://syrabit.ai').pathname.slice(0, 512) || '/';
  } catch {
    return undefined;
  }
}

/**
 * Keep useful custom-beacon dimensions while preventing an analytics endpoint
 * from becoming an unbounded or accidental PII store.
 */
export function sanitizeAnalyticsPayload(value: unknown, depth = 0): JsonValue | undefined {
  if (value === null || typeof value === 'boolean') return value;
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined;
  if (typeof value === 'string') return value.slice(0, MAX_ANALYTICS_STRING_LENGTH);
  if (depth >= MAX_ANALYTICS_DEPTH) return undefined;

  if (Array.isArray(value)) {
    return value
      .slice(0, MAX_ANALYTICS_ARRAY_ITEMS)
      .map(item => sanitizeAnalyticsPayload(item, depth + 1))
      .filter((item): item is JsonValue => item !== undefined);
  }

  if (typeof value !== 'object') return undefined;
  const sanitized: { [key: string]: JsonValue } = {};
  for (const [key, item] of Object.entries(value).slice(0, MAX_ANALYTICS_KEYS)) {
    if (SENSITIVE_ANALYTICS_KEY.test(key)) continue;
    const clean = sanitizeAnalyticsDimension(key, item, depth + 1);
    if (clean !== undefined) sanitized[key.slice(0, 128)] = clean;
  }
  return sanitized;
}

function analyticsRoutePath(payload: JsonValue): string | null {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) return null;
  const candidate = payload.route ?? payload.path ?? payload.url;
  if (typeof candidate !== 'string' || candidate.length === 0) return null;
  try {
    // Persist only a pathname: query strings frequently contain identifiers.
    return new URL(candidate, 'https://syrabit.ai').pathname.slice(0, 512) || '/';
  } catch {
    return null;
  }
}

/**
 * Browser beacons remain acknowledgement-first: malformed or failed durable
 * writes cannot interrupt navigation. Valid, bounded payloads are retained in
 * D1 for operational analytics.
 */
analyticsRouter.post('/:event', async (c) => {
  const event = c.req.param('event');
  if (!ANALYTICS_BEACONS.has(event)) {
    return c.json({ detail: 'Not found' }, 404);
  }

  try {
    const declaredLength = Number(c.req.header('Content-Length'));
    if (Number.isFinite(declaredLength) && declaredLength > MAX_ANALYTICS_BODY_BYTES) {
      await c.req.raw.body?.cancel();
      return c.json({ status: 'ok' });
    }

    const raw = await c.req.text();
    if (new TextEncoder().encode(raw).byteLength > MAX_ANALYTICS_BODY_BYTES) {
      return c.json({ status: 'ok' });
    }
    const payload = sanitizeAnalyticsPayload(JSON.parse(raw));
    if (payload === undefined) return c.json({ status: 'ok' });

    const serialized = JSON.stringify(payload);
    if (new TextEncoder().encode(serialized).byteLength > MAX_ANALYTICS_PAYLOAD_BYTES) {
      return c.json({ status: 'ok' });
    }

    await c.env.DB.prepare(`
      INSERT INTO analytics_events (id, event_name, payload, route_path, created_at)
      VALUES (?, ?, ?, ?, ?)
    `).bind(
      crypto.randomUUID(),
      event,
      serialized,
      analyticsRoutePath(payload),
      Math.floor(Date.now() / 1000),
    ).run();
  } catch {
    // sendBeacon clients must not observe parsing or D1 availability failures.
  }

  return c.json({ status: 'ok' });
});

analyticsRouter.get('/top-routes', async (c) => {
  const since = Math.floor(Date.now() / 1000) - (7 * 24 * 60 * 60);
  try {
    const rows = await c.env.DB.prepare(`
      SELECT route_path AS route, COUNT(*) AS count
      FROM analytics_events
      WHERE event_name = 'page-view' AND route_path IS NOT NULL AND created_at >= ?
      GROUP BY route_path
      ORDER BY count DESC, route_path ASC
      LIMIT 20
    `).bind(since).all<{ route: string; count: number }>();
    return c.json({ routes: rows.results, period: '7d' });
  } catch {
    // Preserve the existing cache-friendly empty response during a D1 outage.
    return c.json({ routes: [], period: '7d' });
  }
});

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