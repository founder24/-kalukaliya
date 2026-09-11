import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const scriptPath = fileURLToPath(
  new URL('./warn-worker-chat-performance-history.mjs', import.meta.url),
);

function report(directMax, webMax, { directPassed = true, webPassed = true } = {}) {
  const route = (maximum, passed) => ({
    passed,
    first_token_max_ms: maximum,
  });
  return {
    first_token_target_ms: 3000,
    summary: {
      direct_chapter_rag: route(directMax, directPassed),
      rag_plus_bounded_web: route(webMax, webPassed),
    },
    probes: [],
  };
}

async function runWithReports(current, history) {
  const directory = await mkdtemp(join(tmpdir(), 'chat-performance-history-'));
  const historyDirectory = join(directory, 'history');
  const currentPath = join(directory, 'current.json');
  const statePath = join(directory, 'state.json');
  await mkdir(historyDirectory);
  await writeFile(currentPath, JSON.stringify(current));
  await Promise.all(history.map((item, index) =>
    writeFile(join(historyDirectory, `${index}.json`), JSON.stringify(item))));

  const result = spawnSync(
    process.execPath,
    [scriptPath, currentPath, historyDirectory],
    {
      encoding: 'utf8',
      env: {
        ...process.env,
        CHAT_PERFORMANCE_HISTORY_RUNS: '5',
        CHAT_PERFORMANCE_NOTIFICATION_RUNS: '3',
        CHAT_PERFORMANCE_NOTIFICATION_STATE_PATH: statePath,
      },
    },
  );
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(await readFile(statePath, 'utf8'));
}

test('notification state keeps routes separate and requires three recurring releases', async () => {
  const state = await runWithReports(
    report(4300, 4400),
    [report(4100, 4200), report(3900, 4000)],
  );

  assert.equal(state.comparison_complete, true);
  assert.deepEqual(
    state.active_warnings.map(warning => warning.route),
    ['direct_chapter_rag', 'rag_plus_bounded_web'],
  );
  assert.deepEqual(state.indeterminate_routes, []);
});

test('notification state reports recovery without clearing a route whose gate failed', async () => {
  const state = await runWithReports(
    report(1800, 4500, { webPassed: false }),
    [report(4100, 4200), report(3900, 4000)],
  );

  assert.equal(state.comparison_complete, true);
  assert.deepEqual(state.active_warnings, []);
  assert.deepEqual(state.indeterminate_routes, ['rag_plus_bounded_web']);
});

test('notification reconciliation is disabled when history is incomplete', async () => {
  const state = await runWithReports(report(4300, 1800), [report(4100, 1700)]);

  assert.equal(state.comparison_complete, false);
  assert.equal(state.compared_runs, 2);
});