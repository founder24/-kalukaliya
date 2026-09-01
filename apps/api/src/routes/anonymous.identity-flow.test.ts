import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { getPlatformProxy } from 'wrangler';

import type { Env } from '../types';
import { signAccessToken } from '../middleware/auth';

const ANON_ID = 'anon_0123456789abcdef0123456789abcdef';
const OTHER_ANON_ID = 'anon_fedcba9876543210fedcba9876543210';
const COOKIE_ANON_ID = 'anon_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const OTHER_COOKIE_ANON_ID = 'anon_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const COOKIE_SECRET = 'anonymous-cookie-flow-secret-at-least-32-characters';
const API_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../');

let env: Env;
let disposeProxy: () => Promise<void>;
let workerFetch: (request: Request) => Promise<Response>;
let background: Promise<unknown>[];
let generationCalls = 0;
let generationBarrier: Promise<void> | null = null;

async function signedCookie(id: string): Promise<string> {
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(COOKIE_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = Array.from(new Uint8Array(
    await crypto.subtle.sign('HMAC', key, encoder.encode(id)),
  )).map(byte => byte.toString(16).padStart(2, '0')).join('');
  return `syrabit_anon_id=${id}.${signature}`;
}

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
      generationCalls += 1;
      if (generationBarrier) await generationBarrier;

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
    EDGE_SHARED_SECRET: COOKIE_SECRET,
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
  it('charges one quota slot when the same logical chat request is retried', async () => {
    const anonId = 'anon_cccccccccccccccccccccccccccccccc';
    const clientRequestId = `chat-request-${crypto.randomUUID()}`;
    const makeRequest = () => new Request('https://api.example/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-anon-id': anonId,
      },
      body: JSON.stringify({
        message: 'Explain gravity',
        lang: 'en',
        client_request_id: clientRequestId,
      }),
    });

    const generationCallsBefore = generationCalls;
    const first = await workerFetch(makeRequest());
    expect(first.status).toBe(200);
    await first.text();
    await Promise.all(background);
    expect(generationCalls).toBe(generationCallsBefore + 1);

    const retry = await workerFetch(makeRequest());
    expect(retry.status).toBe(200);
    expect(retry.headers.get('X-Chat-Replayed')).toBe('true');
    const replayText = await retry.text();
    await Promise.all(background);
    expect(replayText).toContain('"replayed":true');
    expect(generationCalls).toBe(generationCallsBefore + 1);

    const quota = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(quota?.count).toBe(1);

    const claim = await env.DB.prepare(
      'SELECT status FROM chat_request_claims WHERE request_id = ?',
    ).bind(clientRequestId).first<{ status: string }>();
    expect(claim?.status).toBe('completed');

    const chats = await env.DB.prepare(
      'SELECT COUNT(*) AS count FROM chats WHERE user_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(chats?.count).toBe(2);
  });

  it('replays an authenticated completed request without duplicating stats or history', async () => {
    const userId = crypto.randomUUID();
    await env.DB.prepare(
      `INSERT INTO users (id, email, role, subscription_tier, session_valid_after)
       VALUES (?, ?, 'student', 'free', 0)`,
    ).bind(userId, `${userId}@example.test`).run();
    const accessToken = await signAccessToken(userId, 'student', env.JWT_SECRET);
    const clientRequestId = `chat-request-${crypto.randomUUID()}`;
    const makeRequest = () => new Request('https://api.example/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${accessToken}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        message: 'Explain inertia',
        lang: 'en',
        client_request_id: clientRequestId,
      }),
    });

    const generationCallsBefore = generationCalls;
    const first = await workerFetch(makeRequest());
    await first.text();
    await Promise.all(background);

    const replay = await workerFetch(makeRequest());
    expect(replay.headers.get('X-Chat-Replayed')).toBe('true');
    await replay.text();
    await Promise.all(background);

    expect(generationCalls).toBe(generationCallsBefore + 1);
    const stats = await env.DB.prepare(
      `SELECT monthly_message_count, total_lifetime_messages
       FROM users WHERE id = ?`,
    ).bind(userId).first<{
      monthly_message_count: number;
      total_lifetime_messages: number;
    }>();
    expect(stats).toEqual({
      monthly_message_count: 1,
      total_lifetime_messages: 1,
    });
    const chats = await env.DB.prepare(
      'SELECT COUNT(*) AS count FROM chats WHERE user_id = ?',
    ).bind(userId).first<{ count: number }>();
    expect(chats?.count).toBe(2);
  });

  it('waits for an in-flight request instead of starting a second generation', async () => {
    const anonId = 'anon_dddddddddddddddddddddddddddddddd';
    const clientRequestId = `chat-request-${crypto.randomUUID()}`;
    const makeRequest = () => new Request('https://api.example/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-anon-id': anonId,
      },
      body: JSON.stringify({
        message: 'Explain momentum',
        lang: 'en',
        client_request_id: clientRequestId,
      }),
    });
    let releaseGeneration!: () => void;
    generationBarrier = new Promise<void>(resolve => {
      releaseGeneration = resolve;
    });
    const generationCallsBefore = generationCalls;

    try {
      const first = await workerFetch(makeRequest());
      const firstTextPromise = first.text();
      for (let attempt = 0; attempt < 50 && generationCalls === generationCallsBefore; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 10));
      }
      expect(generationCalls).toBe(generationCallsBefore + 1);

      const recovery = await workerFetch(makeRequest());
      expect(recovery.headers.get('X-Chat-Recovery-Wait')).toBe('true');
      const recoveryTextPromise = recovery.text();

      releaseGeneration();
      const [firstText, recoveryText] = await Promise.all([
        firstTextPromise,
        recoveryTextPromise,
      ]);
      await Promise.all(background);

      expect(firstText).toContain('Plants use light to make food.');
      expect(recoveryText).toContain('Plants use light to make food.');
      expect(recoveryText).toContain('"replayed":true');
      expect(generationCalls).toBe(generationCallsBefore + 1);

      const quota = await env.DB.prepare(
        'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
      ).bind(anonId).first<{ count: number }>();
      expect(quota?.count).toBe(1);
      const chats = await env.DB.prepare(
        'SELECT COUNT(*) AS count FROM chats WHERE user_id = ?',
      ).bind(anonId).first<{ count: number }>();
      expect(chats?.count).toBe(2);
    } finally {
      releaseGeneration();
      generationBarrier = null;
    }
  });

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

  it('isolates credits and history between cookie-only browsers sharing one network', async () => {
    const browserCookie = await signedCookie(COOKIE_ANON_ID);
    const otherBrowserCookie = await signedCookie(OTHER_COOKIE_ANON_ID);
    const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Cookie: browserCookie,
        'CF-Connecting-IP': '203.0.113.25',
      },
      body: JSON.stringify({ message: 'Explain plant cells', lang: 'en' }),
    }));
    expect(chat.status).toBe(200);
    const streamText = await chat.text();
    await Promise.all(background);
    const events = streamText
      .split('\n')
      .filter(line => line.startsWith('data: '))
      .map(line => JSON.parse(line.slice(6)) as Record<string, unknown>);
    const sessionId = events.find(event => event.event === 'source_card')?.conversation_id;
    expect(sessionId).toEqual(expect.any(String));

    const reloadHeaders = {
      Cookie: browserCookie,
      'CF-Connecting-IP': '198.51.100.77',
    };
    const credits = await workerFetch(new Request(
      'https://api.example/api/v1/user/credits',
      { headers: reloadHeaders },
    ));
    await expect(credits.json()).resolves.toMatchObject({
      anon_id: COOKIE_ANON_ID,
      credits_used: 1,
      credits_remaining: 29,
    });
    const history = await workerFetch(new Request(
      'https://api.example/api/v1/conversations/anon',
      { headers: reloadHeaders },
    ));
    await expect(history.json()).resolves.toMatchObject({
      conversations: [{ id: sessionId }],
    });

    const otherHeaders = {
      Cookie: otherBrowserCookie,
      'CF-Connecting-IP': '203.0.113.25',
    };
    const otherCredits = await workerFetch(new Request(
      'https://api.example/api/v1/user/credits',
      { headers: otherHeaders },
    ));
    await expect(otherCredits.json()).resolves.toMatchObject({
      anon_id: OTHER_COOKIE_ANON_ID,
      credits_used: 0,
      credits_remaining: 30,
    });
    const otherHistory = await workerFetch(new Request(
      'https://api.example/api/v1/conversations/anon',
      { headers: otherHeaders },
    ));
    await expect(otherHistory.json()).resolves.toMatchObject({
      conversations: [],
      pagination: { total: 0 },
    });
  });
});