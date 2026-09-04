/**
 * Contract tests for site-operation routes that must remain available without
 * the retired external fallback. These use a small D1 double to exercise the public
 * response envelopes and cache-friendly artifact types without remote bindings.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './index';
import { sanitizeAnalyticsPayload } from './operations';
import type { Env } from '../types';

const chapterRows = [{
  title: 'Newton’s Laws',
  chapter_slug: 'newtons-laws',
  notes_en: 'A complete explanation of Newton’s laws of motion for students.',
  published_topics: '[{"topic_slug":"first-law"}]',
  updated_at: 1_735_689_600,
  created_at: 1_735_603_200,
  subject_name: 'Physics',
  subject_slug: 'physics',
  board_slug: 'ahsec',
  class_slug: 'class-12',
}];

function testEnv(config: Record<string, string | undefined> = {}): Env {
  const database = {
    prepare: (query: string) => {
      let bindings: unknown[] = [];
      const statement = {
        bind: (...values: unknown[]) => {
          bindings = values;
          return statement;
        },
        first: async () => {
          const key = String(bindings[0] ?? '');
          return config[key] === undefined ? null : { value: config[key] };
        },
        all: async () => ({ results: query.includes('FROM chapters') ? chapterRows : [] }),
      };
      return statement;
    },
  };

  return {
    DB: database,
    INDEXNOW_API_KEY: 'indexnow-key',
    INDEXNOW_INTERNAL_SECRET: 'submission-secret',
    ...config,
  } as unknown as Env;
}

function request(path: string, init?: RequestInit): Request {
  return new Request(`https://api.syrabit.ai${path}`, init);
}

afterEach(() => vi.unstubAllGlobals());

describe('Worker-native site-operation routes', () => {
  it('acknowledges analytics beacons even when browser beacon payload parsing fails', async () => {
    const response = await api.fetch(request('/api/v1/analytics/page-view', {
      method: 'POST',
      headers: { 'Content-Type': 'text/plain' },
      body: 'beacon payload',
    }), testEnv());

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({ status: 'ok' });
  });

  it('persists a bounded, sanitized analytics payload without storing PII', async () => {
    const writes: { query: string; bindings: unknown[] }[] = [];
    const database = {
      prepare: (query: string) => {
        let bindings: unknown[] = [];
        const statement = {
          bind: (...values: unknown[]) => {
            bindings = values;
            return statement;
          },
          run: async () => {
            writes.push({ query, bindings });
            return { meta: { changes: 1 } };
          },
          all: async () => ({ results: [] }),
          first: async () => null,
        };
        return statement;
      },
    };
    const env = { ...testEnv(), DB: database } as unknown as Env;

    const response = await api.fetch(request('/api/v1/analytics/page-view', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: '/physics?student_email=private@example.com',
        email: 'private@example.com',
        nested: { token: 'do-not-store', label: 'chapter' },
      }),
    }), env);

    expect(response.status).toBe(200);
    expect(writes).toHaveLength(1);
    const write = writes[0]!;
    expect(write.query).toContain('INSERT INTO analytics_events');
    expect(JSON.parse(String(write.bindings[2]))).toEqual({
      path: '/physics',
      nested: { label: 'chapter' },
    });
    expect(write.bindings[3]).toBe('/physics');
  });

  it('bounds and removes sensitive values from custom analytics payloads', () => {
    const payload = sanitizeAnalyticsPayload({
      password: 'never persisted',
      name: 'x'.repeat(600),
      items: Array.from({ length: 30 }, (_, index) => index),
    });

    expect(payload).toEqual({
      name: 'x'.repeat(512),
      items: Array.from({ length: 20 }, (_, index) => index),
    });
  });

  it('returns null for an intentionally unconfigured Trustpilot module', async () => {
    const absent = await api.fetch(request('/api/v1/config/trustpilot'), testEnv());
    expect(await absent.json()).toBeNull();
  });

  it('uses Worker-synchronized Trustpilot values from the canonical source', async () => {
    const env = testEnv({
      TRUSTPILOT_PROFILE_URL: 'https://www.trustpilot.com/review/syrabit.ai',
      TRUSTPILOT_BUSINESS_UNIT_ID: 'unit-123',
      TRUSTPILOT_RATING_VALUE: '4.8',
      TRUSTPILOT_RATING_COUNT: '321',
    });
    const [config, aggregate] = await Promise.all([
      api.fetch(request('/api/v1/config/trustpilot'), env),
      api.fetch(request('/api/v1/config/trustpilot/aggregate'), env),
    ]);

    await expect(config.json()).resolves.toEqual({
      profileUrl: 'https://www.trustpilot.com/review/syrabit.ai',
      businessUnitId: 'unit-123',
    });
    await expect(aggregate.json()).resolves.toEqual({
      ratingValue: 4.8,
      ratingCount: 321,
    });
  });

  it('authenticates and batches IndexNow submissions at 100 URLs', async () => {
    const outboundBodies: string[] = [];
    const outboundFetch = vi.fn(async (_input: unknown, init?: RequestInit) => {
      outboundBodies.push(String(init?.body ?? ''));
      return new Response(null, { status: 202 });
    });
    vi.stubGlobal('fetch', outboundFetch);
    const urls = Array.from({ length: 101 }, (_, index) => `https://syrabit.ai/page-${index}`);

    const response = await api.fetch(request('/api/v1/indexnow/submit', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-IndexNow-Secret': 'submission-secret',
      },
      body: JSON.stringify({ urls }),
    }), testEnv());

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({ submitted: 101, failed: 0 });
    expect(outboundFetch).toHaveBeenCalledTimes(2);
    expect(JSON.parse(outboundBodies[0] ?? '').urlList).toHaveLength(100);
    expect(JSON.parse(outboundBodies[1] ?? '').urlList).toHaveLength(1);
  });

  it('rejects unauthenticated IndexNow submissions without sending an outbound request', async () => {
    const outboundFetch = vi.fn();
    vi.stubGlobal('fetch', outboundFetch);

    const response = await api.fetch(request('/api/v1/indexnow/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ urls: ['https://syrabit.ai/test'] }),
    }), testEnv());

    expect(response.status).toBe(403);
    await expect(response.json()).resolves.toEqual({ detail: 'Missing IndexNow secret' });
    expect(outboundFetch).not.toHaveBeenCalled();
  });

  it('serves changelog and D1-derived crawler artifacts with their expected contracts', async () => {
    const env = testEnv();
    const [changelog, sitemap, jsonFeed, rssFeed, notesFeed, llms] = await Promise.all([
      api.fetch(request('/api/v1/changelog'), env),
      api.fetch(request('/api/v1/seo/sitemap-chapters.xml'), env),
      api.fetch(request('/api/v1/seo/feed.json'), env),
      api.fetch(request('/api/v1/seo/feed.xml'), env),
      api.fetch(request('/api/v1/seo/feed/notes.xml'), env),
      api.fetch(request('/api/v1/seo/llms-full.txt'), env),
    ]);

    await expect(changelog.json()).resolves.toEqual([{
      version: '3.0.0',
      date: '2025-01-01',
      changes: ['Initial stable API release'],
    }]);
    expect(sitemap.headers.get('Content-Type')).toContain('application/xml');
    await expect(sitemap.text()).resolves.toContain('/ahsec/class-12/physics/newtons-laws');
    expect(jsonFeed.headers.get('Content-Type')).toContain('application/feed+json');
    await expect(jsonFeed.json()).resolves.toMatchObject({
      version: 'https://jsonfeed.org/version/1.1',
      items: [expect.objectContaining({ title: 'Newton’s Laws' })],
    });
    expect(rssFeed.headers.get('Content-Type')).toContain('application/rss+xml');
    await expect(rssFeed.text()).resolves.toContain('<rss version="2.0"');
    expect(notesFeed.headers.get('Content-Type')).toContain('application/rss+xml');
    await expect(notesFeed.text()).resolves.toContain('Study Notes &amp; Exam Prep');
    await expect(llms.text()).resolves.toContain('[Newton’s Laws](https://syrabit.ai/ahsec/class-12/physics/newtons-laws)');
  });
});