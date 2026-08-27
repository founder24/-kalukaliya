import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { getPlatformProxy } from 'wrangler';

import type { Env } from '../types';

const ANON_ID = 'anon_0123456789abcdef0123456789abcdef';
const OTHER_ANON_ID = 'anon_fedcba9876543210fedcba9876543210';
const API_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../');

let env: Env;
let disposeProxy: () => Promise<void>;
let workerFetch: (request: Request) => Promise<Response>;
let background: Promise<unknown>[];

function migrationStatements(): string[] {
  const directory = path.join(API_ROOT, 'drizzle/migrations');
  return fs.readdirSync(directory)
    .filter(file => file.endsWith('.sql'))
    .sort()
    .flatMap(file => fs.readFileSync(path.join(directory, file), 'utf8').split(';'))
    .map(fragment => fragment
      .split('\n')
      .filter(line => line.trim() && !line.trim().startsWith('--'))
      .join('\n')
      .trim())
    .filter(Boolean);
}

function aiBinding(): Ai {
  return {
    run: async (model: string) => {
      if (model === '@cf/baai/bge-m3') {
        return { data: [{ values: [0.1, 0.2, 0.3] }] };
      }

      const bytes = new TextEncoder().encode(
        'data: {"response":"Plants use light to make food."}\n\ndata: [DONE]\n\n',
      );
      return new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(bytes);
          controller.close();
        },
      });
    },
  } as unknown as Ai;
}

beforeAll(async () => {
  const proxy = await getPlatformProxy<Env>({
    configPath: path.join(API_ROOT, 'wrangler.toml'),
    remoteBindings: false,
    persist: false,
  });
  disposeProxy = proxy.dispose;
  env = {
    ...proxy.env,
    AI: aiBinding(),
    VECTORIZE: {
      query: async () => ({ matches: [] }),
    } as unknown as VectorizeIndex,
    JWT_SECRET: 'anonymous-flow-test-secret',
    ALLOWED_ORIGINS: '*',
    APP_ENV: 'test',
  };

  for (const statement of migrationStatements()) {
    await env.DB.prepare(statement).run();
  }

  const { default: worker } = await import('../index.js');
  workerFetch = (request: Request) => {
    background = [];
    const context = {
      waitUntil(promise: Promise<unknown>) {
        background.push(promise);
      },
      passThroughOnException() {},
    } as unknown as ExecutionContext;
    return (worker.fetch as (
      request: Request,
      env: Env,
      context: ExecutionContext,
    ) => Promise<Response>)(request, env, context);
  };
}, 60_000);

afterAll(async () => {
  await disposeProxy?.();
});

describe('anonymous identity flow', () => {
  it('keeps chat reservation, persistence, reload, credits, and history on one browser ID', async () => {
    const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-anon-id': ANON_ID,
        'CF-Connecting-IP': '203.0.113.9',
      },
      body: JSON.stringify({ message: 'Explain photosynthesis', lang: 'en' }),
    }));

    expect(chat.status).toBe(200);
    const streamText = await chat.text();
    await Promise.all(background);
    expect(streamText).toContain('Plants use light to make food.');

    const events = streamText
      .split('\n')
      .filter(line => line.startsWith('data: '))
      .map(line => JSON.parse(line.slice(6)) as Record<string, unknown>);
    const sourceCard = events.find(event => event.event === 'source_card');
    const sessionId = sourceCard?.conversation_id;
    expect(sessionId).toEqual(expect.any(String));

    const persisted = await env.DB.prepare(
      'SELECT user_id, session_id, role FROM chats WHERE session_id = ? ORDER BY created_at',
    ).bind(sessionId).all<{ user_id: string; session_id: string; role: string }>();
    expect(persisted.results).toHaveLength(2);
    expect(persisted.results?.every(row => row.user_id === ANON_ID)).toBe(true);

    const reloadHeaders = {
      'x-anon-id': ANON_ID,
      // The persistent browser ID must survive a changed network address.
      'CF-Connecting-IP': '198.51.100.44',
    };
    const credits = await workerFetch(new Request(
      'https://api.example/api/v1/user/credits',
      { headers: reloadHeaders },
    ));
    await expect(credits.json()).resolves.toMatchObject({
      anon_id: ANON_ID,
      credits_used: 1,
      credits_remaining: 29,
      monthly_limit: 30,
    });

    const list = await workerFetch(new Request(
      'https://api.example/api/v1/conversations/anon',
      { headers: reloadHeaders },
    ));
    await expect(list.json()).resolves.toMatchObject({
      conversations: [{ id: sessionId, message_count: 2 }],
    });

    const detail = await workerFetch(new Request(
      `https://api.example/api/v1/conversations/anon/${sessionId}`,
      { headers: reloadHeaders },
    ));
    await expect(detail.json()).resolves.toMatchObject({
      id: sessionId,
      messages: [
        { role: 'user', content: 'Explain photosynthesis' },
        { role: 'assistant', content: 'Plants use light to make food.' },
      ],
    });

    const otherHeaders = { 'x-anon-id': OTHER_ANON_ID };
    const otherCredits = await workerFetch(new Request(
      'https://api.example/api/v1/user/credits',
      { headers: otherHeaders },
    ));
    await expect(otherCredits.json()).resolves.toMatchObject({
      anon_id: OTHER_ANON_ID,
      credits_used: 0,
      credits_remaining: 30,
      monthly_limit: 30,
    });

    const otherList = await workerFetch(new Request(
      'https://api.example/api/v1/conversations/anon',
      { headers: otherHeaders },
    ));
    await expect(otherList.json()).resolves.toMatchObject({
      conversations: [],
      pagination: { total: 0 },
    });

    const otherDetail = await workerFetch(new Request(
      `https://api.example/api/v1/conversations/anon/${sessionId}`,
      { headers: otherHeaders },
    ));
    expect(otherDetail.status).toBe(404);
  });
});