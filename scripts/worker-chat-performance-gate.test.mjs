import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildReport,
  failedRouteMessages,
  validateProbeEvents,
  validateRouteResult,
} from './worker-chat-performance-gate.mjs';

const targetMs = 3000;
const sample = (name, firstTokenMs, extra = {}) => ({
  name,
  first_token_ms: firstTokenMs,
  target_met: firstTokenMs <= targetMs,
  model: '@cf/meta/llama-3.1-8b-instruct-fast',
  ...extra,
});

function reportFor(directTimings, webTimings) {
  const directSamples = directTimings.map((timing, index) =>
    sample(`direct_chapter_rag_${index + 1}`, timing, { rag_path: 'chapter_direct' }));
  const webSamples = webTimings.map((timing, index) =>
    sample(`rag_plus_bounded_web_${index + 1}`, timing, {
      web_used: true,
      web_status: 'ok',
      attributed_web_sources: 1,
    }));
  directSamples.forEach(result => validateRouteResult('direct', result));
  webSamples.forEach(result => validateRouteResult('web', result));
  return buildReport({
    origin: 'https://example.test',
    targetMs,
    subject: { name: 'English' },
    chapter: { chapter_id: 'chapter-1', title: 'A Chapter' },
    directSamples,
    webSamples,
  });
}

test('one slow sample out of three passes for both routes and remains in the report', () => {
  const report = reportFor([1200, 4500, 1800], [4100, 1500, 1700]);

  assert.equal(report.summary.direct_chapter_rag.passed, true);
  assert.equal(report.summary.rag_plus_bounded_web.passed, true);
  assert.equal(report.summary.direct_chapter_rag.passing_samples, 2);
  assert.equal(report.summary.rag_plus_bounded_web.passing_samples, 2);
  assert.equal(report.probes.length, 6);
  assert.match(JSON.stringify(report), /rag_plus_bounded_web_3/);
  assert.deepEqual(failedRouteMessages(report.summary, targetMs), []);
});

test('two slow samples out of three fail only the affected route', () => {
  const report = reportFor([1200, 4500, 4800], [4100, 1500, 1700]);

  assert.equal(report.summary.direct_chapter_rag.passed, false);
  assert.equal(report.summary.rag_plus_bounded_web.passed, true);
  assert.deepEqual(failedRouteMessages(report.summary, targetMs), [
    'direct_chapter_rag (1/3 samples met 3000 ms; median 4500 ms)',
  ]);
});

test('SSE order and native Workers AI model remain required', () => {
  const validEvents = [
    { event: 'source_card', web_sources: [{ url: 'https://example.test/source' }] },
    { content: 'First token', done: false },
    {
      event: 'syrabit_done',
      model: '@cf/meta/llama-3.1-8b-instruct-fast',
      route_trace: { web_used: true, web_status: 'ok' },
    },
  ];
  const { done } = validateProbeEvents('web', validEvents);
  assert.equal(done.model, '@cf/meta/llama-3.1-8b-instruct-fast');
  assert.throws(
    () => validateProbeEvents('bad-order', [validEvents[1], validEvents[0], validEvents[2]]),
    /SSE order invalid/,
  );
  assert.throws(
    () => validateProbeEvents('bad-model', [
      validEvents[0],
      validEvents[1],
      { ...validEvents[2], model: 'gemini-2.5-flash' },
    ]),
    /native Workers AI model/,
  );
});

test('web route still requires successful attributed web status', () => {
  assert.doesNotThrow(() => validateRouteResult('web', {
    web_used: true,
    web_status: 'ok',
  }));
  assert.throws(() => validateRouteResult('web', {
    web_used: false,
    web_status: 'skipped',
  }), /did not return attributed web context/);
});