#!/usr/bin/env node

/**
 * Repeatable production timing probe for the native API Worker chat path.
 *
 * It discovers a published generated chapter, then measures:
 *   1. direct D1 chapter RAG (no embedding/Vectorize/web)
 *   2. a freshness query eligible for bounded web retrieval
 *
 * The probe validates source_card → token → syrabit_done ordering and fails
 * when either first useful token exceeds CHAT_FIRST_TOKEN_TARGET_MS (3000 ms).
 */

import process from 'node:process';
import { randomUUID } from 'node:crypto';

function positiveInteger(name, raw, minimum = 1) {
  if (!/^\d+$/.test(raw)) throw new Error(`${name} must be a positive integer`);
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum) {
    throw new Error(`${name} must be an integer >= ${minimum}`);
  }
  return value;
}

const origin = (process.env.CHAT_API_ORIGIN || 'https://api.syrabit.ai').replace(/\/+$/, '');
const targetMs = positiveInteger(
  'CHAT_FIRST_TOKEN_TARGET_MS',
  process.env.CHAT_FIRST_TOKEN_TARGET_MS || '3000',
);
const requestTimeoutMs = positiveInteger(
  'CHAT_REQUEST_TIMEOUT_MS',
  process.env.CHAT_REQUEST_TIMEOUT_MS || '60000',
);
const samples = positiveInteger(
  'CHAT_PERFORMANCE_SAMPLES',
  process.env.CHAT_PERFORMANCE_SAMPLES || '3',
  3,
);
const mode = process.env.CHAT_PERFORMANCE_MODE || 'both';
if (!['both', 'direct', 'web'].includes(mode)) {
  throw new Error('CHAT_PERFORMANCE_MODE must be one of: both, direct, web');
}

async function fetchJson(path) {
  const response = await fetch(`${origin}${path}`, {
    headers: { Accept: 'application/json' },
    signal: AbortSignal.timeout(requestTimeoutMs),
  });
  if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}`);
  return response.json();
}

async function discoverGeneratedChapter() {
  const subjects = await fetchJson('/api/v1/content/subjects');
  if (!Array.isArray(subjects)) throw new Error('subjects response is not an array');
  const candidates = subjects.slice(0, 12);
  const chapterLists = await Promise.all(candidates.map(async subject => ({
    subject,
    chapters: await fetchJson(`/api/v1/content/chapters/${encodeURIComponent(subject.id)}`),
  })));
  for (const { subject, chapters } of chapterLists) {
    const chapter = Array.isArray(chapters)
      ? chapters.find(item => item?.notes_generated === true && item?.chapter_id)
      : undefined;
    if (chapter) return { subject, chapter };
  }
  throw new Error('No generated public chapter found for the direct-RAG probe');
}

function anonymousHeaders() {
  return {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
    'x-anon-id': `anon_${randomUUID().replaceAll('-', '')}`,
  };
}

async function probe(name, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), requestTimeoutMs);
  const started = performance.now();
  try {
    const response = await fetch(`${origin}/api/v1/chat/stream`, {
      method: 'POST',
      headers: anonymousHeaders(),
      body: JSON.stringify({
        ...body,
        client_request_id: `perf_${randomUUID().replaceAll('-', '')}`,
      }),
      signal: controller.signal,
    });
    const headersMs = performance.now() - started;
    if (!response.ok || !response.body) {
      throw new Error(`${name} returned HTTP ${response.status}: ${await response.text()}`);
    }

    const events = [];
    let firstSourceCardMs = null;
    let firstTokenMs = null;
    let buffer = '';
    const decoder = new TextDecoder();
    for await (const chunk of response.body) {
      buffer += decoder.decode(chunk, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const event = JSON.parse(line.slice(6));
        events.push(event);
        if (event.event === 'source_card' && firstSourceCardMs === null) {
          firstSourceCardMs = performance.now() - started;
        }
        if (typeof event.content === 'string' && event.content.length > 0 && !event.done && firstTokenMs === null) {
          firstTokenMs = performance.now() - started;
          if (firstTokenMs > targetMs) {
            controller.abort();
            throw new Error(
              `${name} first token ${Math.round(firstTokenMs)} ms exceeds ${targetMs} ms target`,
            );
          }
        }
      }
    }

    const sourceIndex = events.findIndex(event => event.event === 'source_card');
    const tokenIndex = events.findIndex(event =>
      typeof event.content === 'string' && event.content.length > 0 && !event.done);
    const doneIndex = events.findIndex(event => event.event === 'syrabit_done');
    if (!(sourceIndex === 0 && tokenIndex > sourceIndex && doneIndex > tokenIndex)) {
      throw new Error(`${name} SSE order invalid: source=${sourceIndex}, token=${tokenIndex}, done=${doneIndex}`);
    }
    if (firstTokenMs === null) throw new Error(`${name} emitted no useful token`);
    const sourceCard = events[sourceIndex];
    const done = events[doneIndex];
    const result = {
      name,
      headers_ms: Math.round(headersMs),
      source_card_ms: Math.round(firstSourceCardMs ?? 0),
      first_token_ms: Math.round(firstTokenMs),
      total_ms: done?.latency_ms,
      source_type: sourceCard?.source_type,
      rag_path: done?.route_trace?.rag_path,
      web_used: done?.route_trace?.web_used,
      web_status: done?.route_trace?.web_status,
      worker_timings_ms: done?.route_trace?.timings_ms,
      model: done?.model,
    };
    if (typeof result.model !== 'string' || !result.model.startsWith('@cf/')) {
      throw new Error(`${name} did not report a native Workers AI model: ${result.model}`);
    }
    if (firstTokenMs > targetMs) {
      throw new Error(`${name} first token ${Math.round(firstTokenMs)} ms exceeds ${targetMs} ms target`);
    }
    return result;
  } finally {
    clearTimeout(timer);
  }
}

const { subject, chapter } = await discoverGeneratedChapter();
const directSamples = [];
const webSamples = [];
for (let sample = 1; sample <= samples; sample += 1) {
  if (mode !== 'web') {
    const direct = await probe(`direct_chapter_rag_${sample}`, {
      message: `Explain the main idea of ${chapter.title} in two sentences.`,
      lang: 'en',
      chapter_id: chapter.chapter_id,
      chapter_name: chapter.title,
      subject_id: subject.id,
      subject_name: subject.name,
    });
    if (direct.rag_path !== 'chapter_direct') {
      throw new Error(`Direct RAG probe used unexpected path: ${direct.rag_path}`);
    }
    directSamples.push(direct);
    console.error(`[chat-performance] ${direct.name}: first token ${direct.first_token_ms} ms`);
  }

  if (mode !== 'direct') {
    const web = await probe(`rag_plus_bounded_web_${sample}`, {
      message: 'What is the current status of the Assam Higher Secondary Education Council? Use web context if needed.',
      lang: 'en',
    });
    if (web.web_used !== true || web.web_status !== 'ok') {
      throw new Error(`Web probe did not return attributed web context: ${JSON.stringify(web)}`);
    }
    webSamples.push(web);
    console.error(`[chat-performance] ${web.name}: first token ${web.first_token_ms} ms, web ${web.web_status}`);
  }
}

function summarize(results) {
  const values = results.map(result => result.first_token_ms).sort((a, b) => a - b);
  const p95Index = Math.max(0, Math.ceil(values.length * 0.95) - 1);
  return {
    samples: values.length,
    first_token_p95_ms: values[p95Index],
    first_token_max_ms: values.at(-1),
  };
}

console.log(JSON.stringify({
  origin,
  first_token_target_ms: targetMs,
  chapter: {
    id: chapter.chapter_id,
    title: chapter.title,
    subject: subject.name,
  },
  summary: {
    ...(directSamples.length > 0 && { direct_chapter_rag: summarize(directSamples) }),
    ...(webSamples.length > 0 && { rag_plus_bounded_web: summarize(webSamples) }),
  },
  probes: [...directSamples, ...webSamples],
}, null, 2));