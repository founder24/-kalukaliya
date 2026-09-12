import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import {
  buildReport,
  failedRouteMessages,
  recurringOutlierWarnings,
  summarizeRoute,
  validateProbeEvents,
  validateRouteResult,
} from './worker-chat-performance-gate.mjs';
import { atomicWriteJson } from './warn-worker-chat-performance-history.mjs';

const targetMs = 3000;
const sample = (name, firstTokenMs, extra = {}) => ({
  name,
  first_token_ms: firstTokenMs,
  target_met: firstTokenMs <= targetMs,
  model: '@cf/meta/llama-3.1-8b-instruct-fast',
  ...extra,
});

function reportFor(directTimings, webTimings, workerTimings = {}) {
  const directSamples = directTimings.map((timing, index) =>
    sample(`direct_chapter_rag_${index + 1}`, timing, {
      rag_path: 'chapter_direct',
      ...(workerTimings.direct?.[index] && {
        worker_timings_ms: workerTimings.direct[index],
      }),
    }));
  const webSamples = webTimings.map((timing, index) =>
    sample(`rag_plus_bounded_web_${index + 1}`, timing, {
      web_used: true,
      web_status: 'ok',
      attributed_web_sources: 1,
      ...(workerTimings.web?.[index] && {
        worker_timings_ms: workerTimings.web[index],
      }),
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

test('one invalid stream is a failed sample without aborting the majority rule', () => {
  const summary = summarizeRoute([
    sample('rag_plus_bounded_web_1', targetMs + 1, {
      target_met: false,
      probe_error: 'incomplete SSE stream',
    }),
    sample('rag_plus_bounded_web_2', 1700),
    sample('rag_plus_bounded_web_3', 2100),
  ], targetMs);

  assert.equal(summary.samples, 3);
  assert.equal(summary.passing_samples, 2);
  assert.equal(summary.first_token_median_ms, 2100);
  assert.equal(summary.passed, true);
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

test('recurring tolerated outliers warn separately by route without changing pass results', () => {
  const current = reportFor([1200, 4200, 1800], [1500, 1700, 1900]);
  const previous = reportFor([1100, 3900, 1600], [4100, 1400, 1700]);

  assert.equal(current.summary.direct_chapter_rag.passed, true);
  assert.equal(previous.summary.direct_chapter_rag.passed, true);
  assert.deepEqual(recurringOutlierWarnings([current, previous]), [{
    route: 'direct_chapter_rag',
    label: 'Direct chapter RAG',
    affected_runs: 2,
    compared_runs: 2,
    maxima_ms: [4200, 3900],
    message: 'Direct chapter RAG had a tolerated first-token outlier above the release target in 2/2 recent deployment runs',
  }]);
});

test('a single tolerated outlier or a failed run does not create a recurring warning', () => {
  const clean = reportFor([1200, 1400, 1800], [1500, 1700, 1900]);
  const oneOutlier = reportFor([1200, 4200, 1800], [1500, 1700, 1900]);
  const failed = reportFor([4200, 4500, 1800], [1500, 1700, 1900]);

  assert.deepEqual(recurringOutlierWarnings([clean, oneOutlier]), []);
  assert.deepEqual(recurringOutlierWarnings([oneOutlier, failed]), []);
});

test('recurring warnings name each route dominant slow phase from timed outliers', () => {
  const current = reportFor(
    [1200, 4200, 1800],
    [4100, 1400, 1700],
    {
      direct: [{}, { auth_ms: 100, retrieval_ms: 2200, prompt_ms: 300 }],
      web: [{ retrieval_ms: 2400, web_ms: 1800, prompt_ms: 200 }],
    },
  );
  const previous = reportFor(
    [1100, 3900, 1600],
    [4300, 1500, 1800],
    {
      direct: [{}, { auth_ms: 200, retrieval_ms: 1800, prompt_ms: 400 }],
      web: [{ retrieval_ms: 2600, web_ms: 2100, prompt_ms: 300 }],
    },
  );

  const warnings = recurringOutlierWarnings([current, previous]);
  assert.deepEqual(warnings.map(warning => ({
    route: warning.route,
    dominant_slow_phase: warning.dominant_slow_phase,
  })), [
    {
      route: 'direct_chapter_rag',
      dominant_slow_phase: { phase: 'retrieval_ms', average_ms: 2000, samples: 2 },
    },
    {
      route: 'rag_plus_bounded_web',
      dominant_slow_phase: { phase: 'retrieval_ms', average_ms: 2500, samples: 2 },
    },
  ]);
  assert.match(warnings[0].message, /dominant slow worker phase: retrieval_ms/);
});

test('missing and invalid older worker timing fields do not break recurring comparison', () => {
  const current = reportFor(
    [1200, 4200, 1800],
    [1500, 1700, 1900],
    { direct: [{}, { retrieval_ms: 2100, total_ms: 4100, prompt_ms: Number.NaN }] },
  );
  const previous = reportFor([1100, 3900, 1600], [1500, 1700, 1900]);

  const [warning] = recurringOutlierWarnings([current, previous]);
  assert.deepEqual(warning.dominant_slow_phase, {
    phase: 'retrieval_ms',
    average_ms: 2100,
    samples: 1,
  });
  assert.equal(warning.affected_runs, 2);
  assert.equal(current.summary.direct_chapter_rag.passed, true);
  assert.equal(previous.summary.direct_chapter_rag.passed, true);
});

test('warning CLI emits actionable annotations while tolerating unreadable and timing-free history', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'chat-performance-warning-'));
  const historyDirectory = join(directory, 'history');
  const currentPath = join(directory, 'current.json');
  const notificationStatePath = join(directory, 'notification-state.json');
  t.after(() => rm(directory, { recursive: true, force: true }));

  await mkdir(historyDirectory);
  await Promise.all([
    writeFile(currentPath, JSON.stringify(reportFor(
      [1200, 4200, 1800],
      [1500, 1700, 1900],
      { direct: [{}, { auth_ms: 100, retrieval_ms: 2200, prompt_ms: 300 }] },
    ))),
    writeFile(
      join(historyDirectory, '2026-09-08.json'),
      JSON.stringify(reportFor([1100, 3900, 1600], [1500, 1700, 1900])),
    ),
    writeFile(join(historyDirectory, '2026-09-09.json'), '{not valid JSON'),
  ]);

  const result = spawnSync(
    process.execPath,
    [join(import.meta.dirname, 'warn-worker-chat-performance-history.mjs'), currentPath, historyDirectory],
    {
      encoding: 'utf8',
      env: {
        ...process.env,
        CHAT_PERFORMANCE_NOTIFICATION_RUNS: '2',
        CHAT_PERFORMANCE_NOTIFICATION_STATE_PATH: notificationStatePath,
      },
    },
  );

  assert.equal(result.status, 0, result.stderr);
  const notificationState = JSON.parse(await readFile(notificationStatePath, 'utf8'));
  assert.deepEqual(notificationState, {
    comparison_complete: true,
    minimum_runs: 2,
    compared_runs: 2,
    indeterminate_routes: [],
    active_warnings: [{
      route: 'direct_chapter_rag',
      label: 'Direct chapter RAG',
      affected_runs: 2,
      compared_runs: 2,
      maxima_ms: [4200, 3900],
      dominant_slow_phase: {
        phase: 'retrieval_ms',
        average_ms: 2200,
        samples: 1,
      },
      message: 'Direct chapter RAG had a tolerated first-token outlier above the release target in 2/2 recent deployment runs; dominant slow worker phase: retrieval_ms (2200 ms average across 1 timed outliers)',
    }],
  });
  assert.match(
    result.stderr,
    /::notice title=Chat performance history::Skipped unreadable report 2026-09-09\.json:/,
  );
  assert.match(result.stderr, /::warning title=Recurring Direct chapter RAG latency outliers::/);
  assert.match(result.stderr, /dominant slow worker phase: retrieval_ms/);
  assert.match(result.stderr, /Affected-run maxima: 4200, 3900 ms/);
  assert.match(result.stderr, /The 3000 ms strict-majority release gate is unchanged\./);
});

test('atomic notification write preserves complete JSON and cleans up after an interrupted write', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'chat-performance-warning-atomic-'));
  const statePath = join(directory, 'notification-state.json');
  const previousState = { active_warnings: [{ route: 'direct_chapter_rag' }] };
  t.after(() => rm(directory, { recursive: true, force: true }));
  await writeFile(statePath, `${JSON.stringify(previousState)}\n`);

  await assert.rejects(
    atomicWriteJson(statePath, { active_warnings: [] }, {
      writeFile: async temporaryPath => {
        await writeFile(temporaryPath, '{"active_warnings":');
        throw new Error('simulated interrupted write');
      },
    }),
    /simulated interrupted write/,
  );

  assert.deepEqual(JSON.parse(await readFile(statePath, 'utf8')), previousState);
  assert.deepEqual(await readdir(directory), ['notification-state.json']);
});

test('atomic notification write syncs the temporary file before rename and the directory afterward', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'chat-performance-warning-sync-'));
  const statePath = join(directory, 'notification-state.json');
  const events = [];
  t.after(() => rm(directory, { recursive: true, force: true }));

  await atomicWriteJson(statePath, { active_warnings: [] }, {
    open: async path => ({
      sync: async () => events.push(`sync:${path === directory ? 'directory' : 'temporary'}`),
      close: async () => events.push(`close:${path === directory ? 'directory' : 'temporary'}`),
    }),
    rename: async (source, destination) => {
      events.push('rename');
      const contents = await readFile(source);
      await writeFile(destination, contents);
      await rm(source);
    },
  });

  assert.deepEqual(events, [
    'sync:temporary',
    'close:temporary',
    'rename',
    'sync:directory',
    'close:directory',
  ]);
  assert.deepEqual(JSON.parse(await readFile(statePath, 'utf8')), { active_warnings: [] });
  assert.deepEqual(await readdir(directory), ['notification-state.json']);
});

test('atomic notification write tolerates unsupported directory sync and closes the directory', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'chat-performance-warning-dir-sync-'));
  const statePath = join(directory, 'notification-state.json');
  let directoryClosed = false;
  t.after(() => rm(directory, { recursive: true, force: true }));

  await atomicWriteJson(statePath, { active_warnings: [] }, {
    open: async path => ({
      sync: async () => {
        if (path === directory) {
          const error = new Error('directory sync unsupported');
          error.code = 'EINVAL';
          throw error;
        }
      },
      close: async () => {
        if (path === directory) directoryClosed = true;
      },
    }),
  });

  assert.equal(directoryClosed, true);
  assert.deepEqual(JSON.parse(await readFile(statePath, 'utf8')), { active_warnings: [] });
  assert.deepEqual(await readdir(directory), ['notification-state.json']);
});

test('atomic notification write treats temporary-file sync failure as fatal and removes the temporary file', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'chat-performance-warning-file-sync-'));
  const statePath = join(directory, 'notification-state.json');
  const previousState = { active_warnings: [{ route: 'rag_plus_bounded_web' }] };
  let temporaryClosed = false;
  t.after(() => rm(directory, { recursive: true, force: true }));
  await writeFile(statePath, `${JSON.stringify(previousState)}\n`);

  await assert.rejects(
    atomicWriteJson(statePath, { active_warnings: [] }, {
      open: async () => ({
        sync: async () => {
          throw new Error('simulated file sync failure');
        },
        close: async () => {
          temporaryClosed = true;
        },
      }),
    }),
    /simulated file sync failure/,
  );

  assert.equal(temporaryClosed, true);
  assert.deepEqual(JSON.parse(await readFile(statePath, 'utf8')), previousState);
  assert.deepEqual(await readdir(directory), ['notification-state.json']);
});

test('warning CLI rebuilds active alerts from a pre-existing truncated notification file', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'chat-performance-warning-recovery-'));
  const historyDirectory = join(directory, 'history');
  const currentPath = join(directory, 'current.json');
  const notificationStatePath = join(directory, 'notification-state.json');
  t.after(() => rm(directory, { recursive: true, force: true }));

  await mkdir(historyDirectory);
  await Promise.all([
    writeFile(currentPath, JSON.stringify(reportFor([1200, 4200, 1800], [1500, 1700, 1900]))),
    writeFile(
      join(historyDirectory, 'previous.json'),
      JSON.stringify(reportFor([1100, 3900, 1600], [1500, 1700, 1900])),
    ),
    writeFile(notificationStatePath, '{"comparison_complete":true,"active_warnings":['),
  ]);

  const result = spawnSync(
    process.execPath,
    [join(import.meta.dirname, 'warn-worker-chat-performance-history.mjs'), currentPath, historyDirectory],
    {
      encoding: 'utf8',
      env: {
        ...process.env,
        CHAT_PERFORMANCE_NOTIFICATION_RUNS: '2',
        CHAT_PERFORMANCE_NOTIFICATION_STATE_PATH: notificationStatePath,
      },
    },
  );

  assert.equal(result.status, 0, result.stderr);
  const notificationState = JSON.parse(await readFile(notificationStatePath, 'utf8'));
  assert.equal(notificationState.comparison_complete, true);
  assert.deepEqual(
    notificationState.active_warnings.map(warning => warning.route),
    ['direct_chapter_rag'],
  );
  assert.match(
    result.stderr,
    /::warning title=Chat performance notification state recovery::Malformed existing notification state/,
  );
  assert.match(result.stderr, /Rebuilding it from the current report comparison\./);
});

test('warning CLI preserves indeterminate routes without changing notification state on insufficient history', async t => {
  const directory = await mkdtemp(join(tmpdir(), 'chat-performance-warning-insufficient-'));
  const historyDirectory = join(directory, 'history');
  const currentPath = join(directory, 'current.json');
  const notificationStatePath = join(directory, 'notification-state.json');
  t.after(() => rm(directory, { recursive: true, force: true }));

  const current = reportFor([4200, 4500, 1800], [1500, 1700, 1900]);
  await mkdir(historyDirectory);
  await writeFile(currentPath, JSON.stringify(current));

  const result = spawnSync(
    process.execPath,
    [join(import.meta.dirname, 'warn-worker-chat-performance-history.mjs'), currentPath, historyDirectory],
    {
      encoding: 'utf8',
      env: {
        ...process.env,
        CHAT_PERFORMANCE_NOTIFICATION_RUNS: '3',
        CHAT_PERFORMANCE_NOTIFICATION_STATE_PATH: notificationStatePath,
      },
    },
  );

  assert.equal(result.status, 0, result.stderr);
  assert.deepEqual(JSON.parse(await readFile(notificationStatePath, 'utf8')), {
    comparison_complete: false,
    minimum_runs: 3,
    compared_runs: 1,
    indeterminate_routes: ['direct_chapter_rag'],
    active_warnings: [],
  });
  assert.match(
    result.stderr,
    /::notice title=Chat performance notification::Only 1\/3 required reports were available;/,
  );
});
