#!/usr/bin/env node

/**
 * Repeatable production timing probe for the native API Worker chat path.
 *
 * It discovers a published generated chapter, then measures:
 *   1. direct D1 chapter RAG (no embedding/Vectorize/web)
 *   2. a freshness query eligible for bounded web retrieval
 *
 * The probe validates source_card → token → syrabit_done ordering and requires
 * a strict majority of each route's samples to meet the 3000 ms target.
 */

import process from 'node:process';
import { randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import {
  buildReport,
  failedRouteMessages,
  validateProbeEvents,
  validateRouteResult,
} from './worker-chat-performance-gate.mjs';

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
const reportPath = process.env.CHAT_PERFORMANCE_REPORT_PATH;
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
        }
      }
    }

    const { sourceCard, done } = validateProbeEvents(name, events);
    if (firstTokenMs === null) throw new Error(`${name} emitted no useful token`);
    const result = {
      name,
      headers_ms: Math.round(headersMs),
      source_card_ms: Math.round(firstSourceCardMs ?? 0),
      first_token_ms: Math.round(firstTokenMs),
      target_met: firstTokenMs <= targetMs,
      total_ms: done?.latency_ms,
      source_type: sourceCard?.source_type,
      rag_path: done?.route_trace?.rag_path,
      web_used: done?.route_trace?.web_used,
      web_status: done?.route_trace?.web_status,
      attributed_web_sources: Array.isArray(sourceCard?.web_sources)
        ? sourceCard.web_sources.length
        : 0,
      worker_timings_ms: done?.route_trace?.timings_ms,
      model: done?.model,
    };
    return result;
  } finally {
    clearTimeout(timer);
  }
}

async function collectProbeSample(route, name, body) {
  try {
    const result = await probe(name, body);
    validateRouteResult(route, result);
    return result;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(
      `::warning title=Chat probe sample failed::${name}: ${message}`,
    );
    return {
      name,
      // Count an invalid or incomplete stream as a failed sample while still
      // collecting the remaining samples needed by the strict-majority rule.
      first_token_ms: targetMs + 1,
      target_met: false,
      probe_error: message,
      web_used: false,
      web_status: 'error',
    };
  }
}

const { subject, chapter } = await discoverGeneratedChapter();
const directSamples = [];
const webSamples = [];
for (let sample = 1; sample <= samples; sample += 1) {
  if (mode !== 'web') {
    const direct = await collectProbeSample('direct', `direct_chapter_rag_${sample}`, {
      message: `Explain the main idea of ${chapter.title} in two sentences.`,
      lang: 'en',
      chapter_id: chapter.chapter_id,
      chapter_name: chapter.title,
      subject_id: subject.id,
      subject_name: subject.name,
    });
    directSamples.push(direct);
    console.error(`[chat-performance] ${direct.name}: first token ${direct.first_token_ms} ms`);
  }

  if (mode !== 'direct') {
    const web = await collectProbeSample('web', `rag_plus_bounded_web_${sample}`, {
      // Keep the freshness probe inside the same discovered curriculum.
      // Use the institution abbreviation so "Education Council" is not
      // misread as an explicit request for the school subject "Education".
      message: 'Has AHSEC been merged into ASSEB now? Use current web context.',
      lang: 'en',
      subject_id: subject.id,
      subject_name: subject.name,
    });
    webSamples.push(web);
    console.error(`[chat-performance] ${web.name}: first token ${web.first_token_ms} ms, web ${web.web_status}`);
  }
}

const report = buildReport({
  origin,
  targetMs,
  subject,
  chapter,
  directSamples,
  webSamples,
});
const reportJson = `${JSON.stringify(report, null, 2)}\n`;
console.log(reportJson.trimEnd());
if (reportPath) await writeFile(reportPath, reportJson, 'utf8');

for (const result of report.probes) {
  const annotation = result.target_met ? 'notice' : 'warning';
  console.error(
    `::${annotation} title=Chat first-token sample::${result.name}: ${result.first_token_ms} ms `
    + `(${result.target_met ? 'met' : 'exceeded'} ${targetMs} ms target)`,
  );
}

const failedRoutes = failedRouteMessages(report.summary, targetMs);
if (failedRoutes.length > 0) {
  throw new Error(`Persistent first-token regression: ${failedRoutes.join('; ')}`);
}
