import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';
import { getPlatformProxy } from 'wrangler';

import type { Env } from '../types';
import { signAccessToken } from '../middleware/auth';
import { AI_MODEL_PRIMARY } from '../services/ai';

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

async function withTrustedEdgeIdentity(request: Request): Promise<Request> {
  const anonId = request.headers.get('x-anon-id');
  if (!anonId) return request;
  const timestamp = Math.floor(Date.now() / 1000).toString();
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    'raw',
    encoder.encode(COOKIE_SECRET),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const message = `${timestamp}:anonymous:${new URL(request.url).pathname}`;
  const signature = Array.from(new Uint8Array(
    await crypto.subtle.sign('HMAC', key, encoder.encode(message)),
  )).map(byte => byte.toString(16).padStart(2, '0')).join('');
  const headers = new Headers(request.headers);
  headers.set('X-User-ID', 'anonymous');
  headers.set('X-Edge-Timestamp', timestamp);
  headers.set('X-Edge-Signature', signature);
  return new Request(request, { headers });
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
  workerFetch = async (request: Request) => {
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
    ) => Promise<Response>)(await withTrustedEdgeIdentity(request), env, context);
  };
}, 60_000);

afterAll(async () => {
  await disposeProxy?.();
});

describe('anonymous identity flow', () => {
  it('emits web attribution before Workers AI tokens for eligible current queries', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/official-notification/',
      title: { rendered: 'Official Notifications' },
      content: { rendered: 'HS Final Examination routine for the 2026-27 session.' },
    }]));
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
        },
        body: JSON.stringify({
          message: 'What is the latest AHSEC examination routine?',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      const events = streamText
        .split('\n')
        .filter(line => line.startsWith('data: '))
        .map(line => JSON.parse(line.slice(6)) as Record<string, unknown>);

      expect(events[0]).toMatchObject({
        event: 'source_card',
        source_type: 'llm_only',
        web_used: true,
        web_status: 'ok',
      });
      expect(events[0]?.web_sources).toBeUndefined();
      expect(events[0]?.sources).toEqual([]);
      expect(events[1]).toMatchObject({ done: false });
      expect(events.at(-1)).toMatchObject({
        event: 'syrabit_done',
        done: true,
        route_trace: {
          model: AI_MODEL_PRIMARY,
          web_used: true,
          web_status: 'ok',
          web_results: 1,
        },
      });
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it.each([
    ['1', 'empty', 'empty', 'What is the latest AHSEC syllabus update?', 'en'],
    ['2', 'error', 'error', 'What is the latest AHSEC scholarship update?', 'en'],
    ['3', 'timeout', 'timeout', 'What is the latest AHSEC admission update?', 'en'],
    ['4', 'empty', 'empty', 'অসম ব’ৰ্ডৰ শেহতীয়া পাঠ্যক্ৰম দেখুৱাওক', 'as'],
    ['5', 'empty', 'empty', 'What is the AHSEC result date?', 'en'],
    ['6', 'empty', 'empty', 'Show me the SEBA Class 10 examination routine', 'en'],
    ['7', 'empty', 'empty', 'What is the AHSEC result re-checking deadline?', 'en'],
    ['8', 'error', 'error', 'What is the deadline for AHSEC admission?', 'en'],
    ['9', 'timeout', 'timeout', 'What is the AHSEC scholarship deadline?', 'en'],
    ['10', 'empty', 'empty', 'What is the current status of AHSEC teacher recruitment?', 'en'],
    ['11', 'empty', 'empty', 'What is the current AHSEC syllabus?', 'en'],
    ['12', 'error', 'error', 'What is the current AHSEC academic calendar?', 'en'],
    ['13', 'empty', 'empty', "What is AHSEC's current grading policy?", 'en'],
    ['14', 'empty', 'empty', 'Does the current AHSEC syllabus include alternating current?', 'en'],
    ['15', 'error', 'error', 'Is electric current currently included in the AHSEC syllabus?', 'en'],
    ['16', 'empty', 'empty', 'Does the current AHSEC syllabus include quantum mechanics?', 'en'],
    ['17', 'empty', 'empty', 'What are the AHSEC exam dates?', 'en'],
    ['18', 'error', 'error', 'Show me the AHSEC exam dates', 'en'],
    ['19', 'timeout', 'timeout', 'When are the AHSEC exam dates?', 'en'],
    ['20', 'empty', 'empty', 'What is the AHSEC form fill-up deadline?', 'en'],
    ['21', 'empty', 'empty', 'When is the AHSEC merit-list announcement date?', 'en'],
    ['22', 'empty', 'empty', 'What is the AHSEC correction-window deadline?', 'en'],
    ['23', 'empty', 'empty', 'How soon after AHSEC exams are results released?', 'en'],
    ['24', 'error', 'error', 'How long after the AHSEC exam are results released?', 'en'],
    ['25', 'timeout', 'timeout', 'What time does the AHSEC exam start?', 'en'],
    ['26', 'empty', 'empty', 'AHSEC পৰীক্ষা কেইটা বজাত আৰম্ভ হয়?', 'as'],
  ] as const)('does not generate an unsupported current answer when official web search case %s is %s', async (
    suffix,
    mode,
    expectedStatus,
    question,
    lang,
  ) => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(
      (_input: RequestInfo | URL, init?: RequestInit) => {
        if (mode === 'empty') return Promise.resolve(Response.json([]));
        if (mode === 'error') return Promise.resolve(new Response('unavailable', { status: 503 }));
        return new Promise<Response>((_resolve, reject) => {
          init?.signal?.addEventListener('abort', () => reject(new Error('aborted')));
        });
      },
    );
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': `anon_${suffix.repeat(32)}`,
        },
        body: JSON.stringify({
          message: question,
          lang,
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      const events = streamText
        .split('\n')
        .filter(line => line.startsWith('data: '))
        .map(line => JSON.parse(line.slice(6)) as Record<string, unknown>);
      expect(events[0]).toMatchObject({
        event: 'source_card',
        web_status: expectedStatus,
        web_used: false,
      });
      expect(events.at(-1)).toMatchObject({
        event: 'chat_error',
        error_code: 'verified_web_evidence_unavailable',
        failure_stage: 'web_evidence',
      });
      expect(streamText).not.toContain('Plants use light to make food.');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('does not generate a current board answer when verified web retrieval is disabled', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'false';
    const fetchMock = vi.spyOn(globalThis, 'fetch');
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_66666666666666666666666666666666',
        },
        body: JSON.stringify({
          message: 'What is the AHSEC result date?',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      const events = streamText
        .split('\n')
        .filter(line => line.startsWith('data: '))
        .map(line => JSON.parse(line.slice(6)) as Record<string, unknown>);
      expect(events[0]).toMatchObject({
        event: 'source_card',
        web_status: 'skipped',
        web_used: false,
      });
      expect(events.at(-1)).toMatchObject({
        event: 'chat_error',
        error_code: 'verified_web_evidence_unavailable',
      });
      expect(fetchMock).not.toHaveBeenCalled();
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('fails closed for a bare AHSEC examination schedule request when retrieval is disabled', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'false';
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_88888888888888888888888888888888',
        },
        body: JSON.stringify({
          message: 'Show me the AHSEC examination schedule',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('verified_web_evidence_unavailable');
      expect(streamText).not.toContain('Plants use light to make food.');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('fails closed for an unsupported AHSEC form-fill deadline when retrieval is disabled', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'false';
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_50000000000000000000000000000000',
        },
        body: JSON.stringify({
          message: 'What is the AHSEC form fill-up deadline?',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('verified_web_evidence_unavailable');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('keeps a physical-and-chemical-changes explanation on normal curriculum generation', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      Response.json({ message: { items: [] } }),
    );
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_99999999999999999999999999999999',
        },
        body: JSON.stringify({
          message: 'Explain physical and chemical changes',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('Plants use light to make food.');
      expect(streamText).not.toContain('verified_web_evidence_unavailable');
      expect(generationCalls).toBe(generationCallsBefore + 1);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('keeps an AHSEC electric-current curriculum question off the web-evidence gate', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      Response.json({ message: { items: [] } }),
    );
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_cccccccccccccccccccccccccccccccd',
        },
        body: JSON.stringify({
          message: 'Explain electric current for AHSEC physics',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('Plants use light to make food.');
      expect(streamText).not.toContain('verified_web_evidence_unavailable');
      expect(generationCalls).toBe(generationCallsBefore + 1);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it.each([
    'For AHSEC physics, explain when total internal reflection occurs.',
    "For AHSEC physics, when is Ohm's law applicable?",
  ])('keeps a conditional curriculum when-question on normal generation: %s', async question => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'false';
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': `anon_${question.includes('reflection') ? '6' : '7'}${'0'.repeat(31)}`,
        },
        body: JSON.stringify({ message: question, lang: 'en' }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('Plants use light to make food.');
      expect(streamText).not.toContain('verified_web_evidence_unavailable');
      expect(generationCalls).toBe(generationCallsBefore + 1);
    } finally {
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('recognizes Assamese freshness and board wording without falling through to Crossref', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    let requestedUrl = '';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async input => {
      requestedUrl = String(input);
      return Response.json([{
        link: 'https://ahsec.assam.gov.in/index.php/academic-calendar/',
        title: { rendered: 'Academic Calendar' },
        content: { rendered: 'Academic calendar for the 2026-27 session.' },
      }]);
    });
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_44444444444444444444444444444444',
        },
        body: JSON.stringify({
          message: 'অসম ব’ৰ্ডৰ শেহতীয়া পঞ্জিকা দেখুৱাওক',
          lang: 'as',
        }),
      }));
      await chat.text();
      await Promise.all(background);
      expect(requestedUrl).toContain('ahsec.assam.gov.in');
      expect(requestedUrl).not.toContain('crossref.org');
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('returns attributed evidence for the exact production institution-status probe', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      '<html><title>ASSEB Official</title><body>Formed under the Assam State School Education Board merger.</body></html>',
    ));
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_ddddddddddddddddddddddddddddddde',
        },
        body: JSON.stringify({
          message: 'What is the current status of the Assam Higher Secondary Education Council? Use web context if needed.',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      const events = streamText
        .split('\n')
        .filter(line => line.startsWith('data: '))
        .map(line => JSON.parse(line.slice(6)) as Record<string, unknown>);
      expect(events[0]).toMatchObject({
        event: 'source_card',
        web_status: 'ok',
        web_used: true,
      });
      expect(streamText).toContain('Plants use light to make food.');
      expect(generationCalls).toBe(generationCallsBefore + 1);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('retrieves authoritative D1 and official web paths for a current syllabus request', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/hs-syllabus-26-27/',
      title: { rendered: 'HS Syllabus 2026-27' },
      content: { rendered: 'The current syllabus for the 2026-27 academic session includes alternating current.' },
      modified: '2026-08-01T08:00:00',
    }]));
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeef',
        },
        body: JSON.stringify({
          message: 'Does the current AHSEC syllabus include alternating current?',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      const events = streamText
        .split('\n')
        .filter(line => line.startsWith('data: '))
        .map(line => JSON.parse(line.slice(6)) as Record<string, unknown>);
      expect(events[0]).toMatchObject({
        event: 'source_card',
        authoritative_intent: 'syllabus',
        rag_path: 'syllabus_d1',
        web_status: 'ok',
        web_used: true,
      });
      expect(streamText).toContain('Plants use light to make food.');
      expect(generationCalls).toBe(generationCallsBefore + 1);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('fails closed when official evidence mismatches Assamese class and session qualifiers', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/hs-1st-year-syllabus-25-26/',
      title: { rendered: 'HS 1st Year Syllabus 2025-26' },
      content: { rendered: 'Class XI syllabus for the 2025-26 session.' },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_77777777777777777777777777777777',
        },
        body: JSON.stringify({
          message: 'অসম ব’ৰ্ডৰ শেহতীয়া দ্বাদশ শ্ৰেণীৰ ২০২৬-২৭ পাঠ্যক্ৰম দেখুৱাওক',
          lang: 'as',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      const events = streamText
        .split('\n')
        .filter(line => line.startsWith('data: '))
        .map(line => JSON.parse(line.slice(6)) as Record<string, unknown>);
      expect(events[0]).toMatchObject({ web_status: 'empty', web_used: false });
      expect(events.at(-1)).toMatchObject({
        event: 'chat_error',
        error_code: 'verified_web_evidence_unavailable',
      });
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('does not generate from an admission page that omits the requested deadline', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/registration-admission/',
      title: { rendered: 'Registration and Admission' },
      content: { rendered: 'Official information about the admission process and required documents.' },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaab',
        },
        body: JSON.stringify({
          message: 'What is the AHSEC admission deadline?',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('verified_web_evidence_unavailable');
      expect(streamText).not.toContain('Plants use light to make food.');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('does not generate from stale result evidence for a current when-question', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/official-notification/',
      title: { rendered: 'Official Result Notification' },
      content: { rendered: 'Results will be announced on 15 May 2024.' },
      modified: '2026-08-20T08:00:00',
    }]));
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbc',
        },
        body: JSON.stringify({
          message: 'When will AHSEC results be announced?',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('verified_web_evidence_unavailable');
      expect(streamText).not.toContain('Plants use light to make food.');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('does not generate when fresh evidence omits an Assamese substantive claim', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/hs-syllabus-26-27/',
      title: { rendered: 'HS Syllabus 2026-27' },
      content: { rendered: 'পাঠ্যক্ৰম 2026-27 শিক্ষাবৰ্ষৰ বাবে প্ৰকাশ কৰা হৈছে।' },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_fffffffffffffffffffffffffffffff0',
        },
        body: JSON.stringify({
          message: 'বৰ্তমান AHSEC পাঠ্যক্ৰমত কোৱাণ্টাম অন্তৰ্ভুক্ত নেকি?',
          lang: 'as',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('verified_web_evidence_unavailable');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it.each([
    [
      'AHSEC নামভৰ্তিৰ সময়সীমা কি?',
      'Official admission deadline is 15 May 2026.',
    ],
    [
      'অসমৰ বৰ্ডৰ পৰীক্ষাৰ ৰুটিন কি?',
      'HS examination routine for the 2026-27 session.',
    ],
  ])('generates from valid English evidence for Assamese framing: %s', async (
    question,
    content,
  ) => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/current-information/',
      title: { rendered: 'Official AHSEC Information' },
      content: { rendered: content },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': `anon_${question.includes('নামভৰ্তি') ? '1' : '2'}${'0'.repeat(31)}`,
        },
        body: JSON.stringify({ message: question, lang: 'as' }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).not.toContain('verified_web_evidence_unavailable');
      expect(generationCalls).toBe(generationCallsBefore + 1);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('does not generate from Class 11 evidence for a hyphenated HS 2nd-year claim', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/hs-syllabus-26-27/',
      title: { rendered: 'HS Syllabus 2026-27' },
      content: { rendered: 'Class 11 syllabus for 2026-27 includes quantum mechanics.' },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_30000000000000000000000000000000',
        },
        body: JSON.stringify({
          message: 'Does the current AHSEC HS 2nd-year syllabus include quantum mechanics?',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('curriculum_scope_ambiguous');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('does not generate from a fresh exam-dates passage without a concrete date', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/examination-notification/',
      title: { rendered: 'Examination Notification 2026' },
      content: { rendered: 'Examination dates for 2026 will be notified separately.' },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_40000000000000000000000000000000',
        },
        body: JSON.stringify({
          message: 'What are the AHSEC exam dates?',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('verified_web_evidence_unavailable');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('does not generate when a neighboring date belongs to a different event', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
      title: { rendered: 'Result Notification 2026' },
      content: {
        rendered: 'Registration closes 15 May 2026. Result date will be notified later.',
      },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      await env.CONTENT_KV.delete('web-search:official:asseb:v2:division-ii:result');
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': 'anon_80000000000000000000000000000000',
        },
        body: JSON.stringify({
          message: 'What is the AHSEC result date?',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('verified_web_evidence_unavailable');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it.each([
    [
      'What are the AHSEC exam dates?',
      'The examination lasts three hours in 2026.',
    ],
    [
      'What is the AHSEC exam start time?',
      'The examination starts on 15 May 2026.',
    ],
    [
      'What is the AHSEC exam timing?',
      'General examination information for the 2026 session.',
    ],
  ])('does not generate from the wrong temporal evidence type: %s', async (
    question,
    content,
  ) => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/examination-notification/',
      title: { rendered: 'Examination Notification 2026' },
      content: { rendered: content },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      await env.CONTENT_KV.delete('web-search:official:asseb:v2:division-ii:examination');
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': `anon_${question.includes('dates') ? '9' : 'a'}${'0'.repeat(31)}`,
        },
        body: JSON.stringify({ message: question, lang: 'en' }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('verified_web_evidence_unavailable');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it.each([
    [
      'How soon after AHSEC exams are results released?',
      'Results are released 30 days after examinations end in 2026.',
      'result',
    ],
    [
      'What is the AHSEC exam start time?',
      'The examination starts at 9:00 AM on 15 May 2026.',
      'examination',
    ],
    [
      'What is the AHSEC exam timing?',
      'The examination starts at 9:00 AM on 15 May 2026.',
      'examination',
    ],
  ])('generates from event-bound evidence of the requested temporal type: %s', async (
    question,
    content,
    topic,
  ) => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/current-information/',
      title: { rendered: 'Official Information 2026' },
      content: { rendered: content },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      await env.CONTENT_KV.delete(`web-search:official:asseb:v2:division-ii:${topic}`);
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': `anon_${topic === 'result' ? 'b' : 'c'}${'0'.repeat(31)}`,
        },
        body: JSON.stringify({ message: question, lang: 'en' }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).not.toContain('verified_web_evidence_unavailable');
      expect(generationCalls).toBe(generationCallsBefore + 1);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it.each([
    [
      'What is the current AHSEC Class 12 result date?',
      'Class 12 registration closes 15 May 2026. Class 11 result date is 20 May 2026.',
    ],
    [
      'Is the AHSEC result date 15 May 2026?',
      'Registration closes 15 May 2026. Result date is 20 May 2026.',
    ],
  ])('does not generate when a neighboring event supplies the qualifier: %s', async (
    question,
    content,
  ) => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
      title: { rendered: 'Result Notification 2026' },
      content: { rendered: content },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      await env.CONTENT_KV.delete('web-search:official:asseb:v2:division-ii:result');
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': `anon_${question.includes('Class 12') ? 'd' : 'e'}${'0'.repeat(31)}`,
        },
        body: JSON.stringify({ message: question, lang: 'en' }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain(
        question.includes('Class 12')
          ? 'curriculum_scope_ambiguous'
          : 'verified_web_evidence_unavailable',
      );
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it.each([
    [
      'What is the current AHSEC Class 12 result date?',
      'Class 12 result date is 20 May 2026.',
    ],
    [
      'Is the AHSEC result date 15 May 2026?',
      'Result date is 15 May 2026.',
    ],
  ])('generates when qualifiers are bound to the requested event: %s', async (
    question,
    content,
  ) => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
      title: { rendered: 'Result Notification 2026' },
      content: { rendered: content },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      await env.CONTENT_KV.delete('web-search:official:asseb:v2:division-ii:result');
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': `anon_${question.includes('Class 12') ? 'f' : '0'}${'1'.repeat(31)}`,
        },
        body: JSON.stringify({ message: question, lang: 'en' }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      if (question.includes('Class 12')) {
        expect(streamText).toContain('curriculum_scope_ambiguous');
        expect(generationCalls).toBe(generationCallsBefore);
      } else {
        expect(streamText).not.toContain('verified_web_evidence_unavailable');
        expect(generationCalls).toBe(generationCallsBefore + 1);
      }
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it.each([
    '<ul><li>Class 12 registration closes 15 May 2026</li><li>Class 11 result date is 20 May 2026</li></ul>',
    '<table><tr><td>Class 12 registration</td><td>15 May 2026</td></tr><tr><td>Class 11 result date</td><td>20 May 2026</td></tr></table>',
  ])('does not generate when structured HTML separates the requested qualifier', async content => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
      title: { rendered: 'Result Notification 2026' },
      content: { rendered: content },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      await env.CONTENT_KV.delete('web-search:official:asseb:v2:division-ii:result');
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': `anon_${content.startsWith('<ul') ? '2' : '3'}${'2'.repeat(31)}`,
        },
        body: JSON.stringify({
          message: 'What is the current AHSEC Class 12 result date?',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('curriculum_scope_ambiguous');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it('fails closed on explicit class scope before evaluating a matching structured row', async () => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
      title: { rendered: 'Result Notification 2026' },
      content: {
        rendered: '<table><tr><td><p>Class 12 result date</p></td><td><p>20 May 2026</p></td></tr></table>',
      },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      await env.CONTENT_KV.delete('web-search:official:asseb:v2:division-ii:result');
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': `anon_4${'2'.repeat(31)}`,
        },
        body: JSON.stringify({
          message: 'What is the current AHSEC Class 12 result date?',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('curriculum_scope_ambiguous');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

  it.each([
    '<ul><li><table><tr><td><p>Class 12 registration</p></td><td><p>15 May 2026</p></td></tr><tr><td><p>Class 11 result date</p></td><td><p>20 May 2026</p></td></tr></table></li></ul>',
    `<p>Class 12 result ${'general information '.repeat(35)}date is 20 May 2026.</p>`,
  ])('does not generate from structurally or spatially separated validating facts', async content => {
    const originalWebSearchFlag = env.WEB_SEARCH_ENABLED;
    env.WEB_SEARCH_ENABLED = 'true';
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(Response.json([{
      link: 'https://ahsec.assam.gov.in/index.php/result-notification/',
      title: { rendered: 'Result Notification 2026' },
      content: { rendered: content },
    }]));
    const generationCallsBefore = generationCalls;
    try {
      await env.CONTENT_KV.delete('web-search:official:asseb:v2:division-ii:result');
      const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-anon-id': `anon_${content.startsWith('<ul') ? '5' : '6'}${'2'.repeat(31)}`,
        },
        body: JSON.stringify({
          message: 'What is the current AHSEC Class 12 result date?',
          lang: 'en',
        }),
      }));
      const streamText = await chat.text();
      await Promise.all(background);
      expect(streamText).toContain('curriculum_scope_ambiguous');
      expect(generationCalls).toBe(generationCallsBefore);
    } finally {
      fetchMock.mockRestore();
      if (originalWebSearchFlag === undefined) {
        delete env.WEB_SEARCH_ENABLED;
      } else {
        env.WEB_SEARCH_ENABLED = originalWebSearchFlag;
      }
    }
  });

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

  it('keeps edge-owned quota out of D1 while preserving request replay', async () => {
    const anonId = 'anon_d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1d1';
    const clientRequestId = `chat-request-${crypto.randomUUID()}`;
    const makeRequest = () => new Request('https://api.example/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-anon-id': anonId,
        'X-Rate-Limited-By': 'edge',
        'X-RateLimit-Limit': '6',
        'X-RateLimit-Remaining': '4',
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
    const firstText = await first.text();
    await Promise.all(background);
    expect(firstText).toContain('"credits_used_total":2');
    expect(generationCalls).toBe(generationCallsBefore + 1);

    const retry = await workerFetch(makeRequest());
    expect(retry.status).toBe(200);
    expect(retry.headers.get('X-Chat-Replayed')).toBe('true');
    await retry.text();
    await Promise.all(background);
    expect(generationCalls).toBe(generationCallsBefore + 1);

    const quota = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ?',
    ).bind(anonId).first<{ count: number }>();
    expect(quota).toBeNull();

    const claim = await env.DB.prepare(
      'SELECT status, quota_reserved FROM chat_request_claims WHERE request_id = ?',
    ).bind(clientRequestId).first<{ status: string; quota_reserved: number }>();
    expect(claim).toEqual({ status: 'completed', quota_reserved: 0 });
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
      credits_remaining: 5,
      rpm_limit: 6,
      quota_period: 'minute',
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
      credits_remaining: 6,
      rpm_limit: 6,
      quota_period: 'minute',
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
      credits_remaining: 5,
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
      credits_remaining: 6,
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

  it('lets only the anonymous owner cancel a reserved request and releases its minute slot once', async () => {
    const requestId = `cancel_${crypto.randomUUID().replace(/-/g, '')}`;
    const period = new Date().toISOString().slice(0, 16);
    const ownerCookie = await signedCookie(COOKIE_ANON_ID);
    const otherCookie = await signedCookie(OTHER_COOKIE_ANON_ID);
    await env.DB.batch([
      env.DB.prepare(
        `INSERT INTO anonymous_quota_usage (anon_id, period, count) VALUES (?, ?, 2)
         ON CONFLICT (anon_id, period) DO UPDATE SET count = 2`,
      ).bind(COOKIE_ANON_ID, period),
      env.DB.prepare(
        `INSERT INTO chat_request_claims
         (request_id, user_id, period, is_anon, status, expires_at)
         VALUES (?, ?, ?, 1, 'reserved', ?)`,
      ).bind(requestId, COOKIE_ANON_ID, period, Math.floor(Date.now() / 1000) + 3600),
    ]);

    const denied = await workerFetch(new Request('https://api.example/api/v1/chat/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Cookie: otherCookie },
      body: JSON.stringify({ client_request_id: requestId }),
    }));
    await expect(denied.json()).resolves.toEqual({ cancelled: false });

    const cancellations = await Promise.all([1, 2].map(() => workerFetch(new Request(
      'https://api.example/api/v1/chat/cancel',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Cookie: ownerCookie },
        body: JSON.stringify({ client_request_id: requestId }),
      },
    ))));
    expect(cancellations.map(response => response.status).sort()).toEqual([200, 202]);

    const claim = await env.DB.prepare(
      'SELECT status, cancelled_at FROM chat_request_claims WHERE request_id = ?',
    ).bind(requestId).first<{ status: string; cancelled_at: number | null }>();
    expect(claim?.status).toBe('cancelled');
    expect(claim?.cancelled_at).toEqual(expect.any(Number));
    const quota = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ? AND period = ?',
    ).bind(COOKIE_ANON_ID, period).first<{ count: number }>();
    expect(quota?.count).toBe(1);

    const repeated = await workerFetch(new Request('https://api.example/api/v1/chat/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Cookie: ownerCookie },
      body: JSON.stringify({ client_request_id: requestId }),
    }));
    await expect(repeated.json()).resolves.toEqual({ cancelled: true });
    const afterRepeat = await env.DB.prepare(
      'SELECT count FROM anonymous_quota_usage WHERE anon_id = ? AND period = ?',
    ).bind(COOKIE_ANON_ID, period).first<{ count: number }>();
    expect(afterRepeat?.count).toBe(1);
  });

  it('records an early cancellation tombstone that prevents later reservation', async () => {
    const requestId = `early_${crypto.randomUUID().replace(/-/g, '')}`;
    const ownerCookie = await signedCookie(COOKIE_ANON_ID);
    const cancelled = await workerFetch(new Request('https://api.example/api/v1/chat/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Cookie: ownerCookie },
      body: JSON.stringify({ client_request_id: requestId }),
    }));
    expect(cancelled.status).toBe(202);

    const chat = await workerFetch(new Request('https://api.example/api/v1/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Cookie: ownerCookie },
      body: JSON.stringify({
        client_request_id: requestId,
        message: 'Explain photosynthesis',
        lang: 'en',
      }),
    }));
    expect(chat.status).toBe(409);
    await expect(chat.json()).resolves.toMatchObject({
      error_code: 'chat_request_cancelled',
    });
  });
});