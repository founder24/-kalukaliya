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
  invalidProbeSample,
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

const SAFE_PROBE_ERROR_CLASSES = new Set([
  'AbortError',
  'Error',
  'NetworkError',
  'RangeError',
  'TimeoutError',
  'TypeError',
]);

function sanitizedProbeErrorClass(error) {
  const name = typeof error === 'string'
    ? error
    : error && typeof error === 'object'
      ? error.name
      : undefined;
  return typeof name === 'string' && SAFE_PROBE_ERROR_CLASSES.has(name)
    ? name
    : 'ProbeError';
}

function sanitizedProbeErrorCode(error, timedOut = false) {
  const errorClass = sanitizedProbeErrorClass(error);
  if (timedOut || errorClass === 'TimeoutError') return 'timeout';
  if (errorClass === 'AbortError') return 'aborted';
  if (errorClass === 'TypeError' || errorClass === 'NetworkError') return 'transport_error';
  return 'probe_failure';
}

function sanitizedDiagnosticTag(value) {
  return typeof value === 'string' && /^[a-z0-9_]{1,48}$/i.test(value)
    ? value
    : 'unknown';
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
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException('Chat performance probe timed out', 'TimeoutError'));
  }, requestTimeoutMs);
  const started = performance.now();
  const events = [];
  let failureStage = 'request';
  let headersMs = null;
  let firstSourceCardMs = null;
  let firstTokenMs = null;
  let tokenEvents = 0;
  let outputChars = 0;
  let terminalEvent = null;
  let terminalFailureStage = null;
  let terminalEventMs = null;
  let observedSourceCard = null;
  let workerTimingsMs = null;
  const observations = (failure = {}) => ({
    elapsed_ms: Math.round(performance.now() - started),
    failure_stage: terminalFailureStage ?? failureStage,
    tokens_emitted: tokenEvents > 0,
    terminal_sse_marker_observed: terminalEvent !== null,
    ...(headersMs !== null && { headers_ms: Math.round(headersMs) }),
    ...(firstSourceCardMs !== null && { source_card_ms: Math.round(firstSourceCardMs) }),
    ...(firstTokenMs !== null && { observed_first_token_ms: Math.round(firstTokenMs) }),
    ...(terminalEventMs !== null && { terminal_event_ms: Math.round(terminalEventMs) }),
    event_count: events.length,
    token_events: tokenEvents,
    output_chars: outputChars,
    ...(terminalEvent !== null && { terminal_event: terminalEvent }),
    ...(observedSourceCard && {
      source_type: observedSourceCard.source_type,
      rag_path: observedSourceCard.rag_path,
      web_used: observedSourceCard.web_used,
      web_status: observedSourceCard.web_status,
    }),
    ...(workerTimingsMs && { worker_timings_ms: workerTimingsMs }),
    ...failure,
  });
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
    failureStage = 'response_headers';
    headersMs = performance.now() - started;
    if (!response.ok || !response.body) {
      throw new Error(`${name} returned HTTP ${response.status}`);
    }

    let buffer = '';
    const decoder = new TextDecoder();
    failureStage = 'response_body';
    for await (const chunk of response.body) {
      buffer += decoder.decode(chunk, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        failureStage = 'sse_event_parse';
        const event = JSON.parse(line.slice(6));
        events.push(event);
        if (event.event === 'source_card' && firstSourceCardMs === null) {
          firstSourceCardMs = performance.now() - started;
          observedSourceCard = event;
        }
        if (typeof event.content === 'string' && event.content.length > 0 && !event.done && firstTokenMs === null) {
          firstTokenMs = performance.now() - started;
        }
        if (typeof event.content === 'string' && event.content.length > 0 && !event.done) {
          tokenEvents += 1;
          outputChars += event.content.length;
        }
        if (event.event === 'chat_error' || event.event === 'syrabit_done') {
          if (event.event === 'chat_error') {
            terminalFailureStage = sanitizedDiagnosticTag(event.failure_stage);
          }
          terminalEvent = event.event === 'chat_error'
            ? `${event.event}:${sanitizedDiagnosticTag(event.error_code)}:${sanitizedDiagnosticTag(event.failure_stage)}`
            : event.event;
          terminalEventMs = performance.now() - started;
          workerTimingsMs = event.timings_ms ?? event.route_trace?.timings_ms ?? workerTimingsMs;
        }
        failureStage = 'response_body';
      }
    }

    failureStage = 'sample_validation';
    const { sourceCard, done } = validateProbeEvents(name, events);
    if (firstTokenMs === null) throw new Error(`${name} emitted no useful token`);
    const result = {
      name,
      elapsed_ms: Math.round(performance.now() - started),
      headers_ms: Math.round(headersMs),
      source_card_ms: Math.round(firstSourceCardMs ?? 0),
      first_token_ms: Math.round(firstTokenMs),
      tokens_emitted: tokenEvents > 0,
      terminal_sse_marker_observed: terminalEvent !== null,
      terminal_event: terminalEvent,
      token_events: tokenEvents,
      output_chars: outputChars,
      event_count: events.length,
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
  } catch (error) {
    const sanitized = {
      error_class: sanitizedProbeErrorClass(error),
      error_code: sanitizedProbeErrorCode(error, timedOut),
    };
    const enriched = new Error(`${sanitized.error_class}:${sanitized.error_code}`);
    enriched.probeDiagnostics = observations(sanitized);
    throw enriched;
  } finally {
    clearTimeout(timer);
  }
}

async function collectProbeSample(route, name, body) {
  let result;
  try {
    result = await probe(name, body);
    validateRouteResult(route, result);
    return result;
  } catch (error) {
    const probeDiagnostics = error && typeof error === 'object' && error.probeDiagnostics
      ? error.probeDiagnostics
      : result
        ? {
            elapsed_ms: result.elapsed_ms,
            failure_stage: 'route_validation',
            tokens_emitted: result.tokens_emitted,
            terminal_sse_marker_observed: result.terminal_sse_marker_observed,
            headers_ms: result.headers_ms,
            source_card_ms: result.source_card_ms,
            observed_first_token_ms: result.first_token_ms,
            token_events: result.token_events,
            output_chars: result.output_chars,
            event_count: result.event_count,
            source_type: result.source_type,
            rag_path: result.rag_path,
            web_used: result.web_used,
            web_status: result.web_status,
            worker_timings_ms: result.worker_timings_ms,
          }
        : { failure_stage: 'probe_setup' };
    const errorClass = sanitizedProbeErrorClass(
      typeof probeDiagnostics.error_class === 'string'
        ? probeDiagnostics.error_class
        : error,
    );
    const errorCode = typeof probeDiagnostics.error_code === 'string'
      ? sanitizedDiagnosticTag(probeDiagnostics.error_code)
      : sanitizedProbeErrorCode(error);
    const errorSummary = `${errorClass}:${errorCode}`;
    const observations = {
      ...probeDiagnostics,
      error_class: errorClass,
      error_code: errorCode,
    };
    const failed = invalidProbeSample(name, targetMs, errorSummary, observations);
    console.error(
      `::warning title=Chat probe sample failed::${name}: ${errorSummary}; `
      + `stage=${observations.failure_stage}; elapsed_ms=${observations.elapsed_ms ?? 'unknown'}; `
      + `observed=${JSON.stringify(observations)}`,
    );
    return failed;
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
