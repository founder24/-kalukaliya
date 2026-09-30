import { spawnSync } from 'node:child_process';
import {
  chmodSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { delimiter, dirname, join, resolve, sep } from 'node:path';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, it } from 'vitest';

const API_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const SCRIPT = resolve(API_DIR, 'scripts/ai-cancellation-smoke.mjs');
const EXPECTED_EDGE_ORIGIN = 'https://syrabitworker-staging.axomxplain.workers.dev';
const SECRET_SENTINEL = 'TEST_ONLY_SECRET_VALUE_SENTINEL';
const PROMPT_SENTINEL = 'TEST_ONLY_PROMPT_SENTINEL';
const RESPONSE_SENTINEL = 'TEST_ONLY_MODEL_RESPONSE_SENTINEL';

const harnesses = new Set();

function makeHarness(mode = 'success') {
  const root = mkdtempSync(join(tmpdir(), 'syrabit-ai-cancel-test-'));
  const binDir = join(root, 'bin');
  mkdirSync(binDir, { recursive: true });
  const wranglerLog = join(root, 'wrangler.jsonl');
  const fetchLog = join(root, 'fetch.jsonl');
  const preloadPath = join(root, 'fetch-preload.mjs');
  const pnpmPath = join(binDir, 'pnpm');

  writeFileSync(pnpmPath, String.raw`#!/usr/bin/env node
const { appendFileSync, readFileSync } = require('node:fs');
const args = process.argv.slice(2);
if (args[0] !== 'exec' || args[1] !== 'wrangler') process.exit(90);
const commandArgs = args.slice(2);
const command = commandArgs[0];
const configIndex = commandArgs.indexOf('--config');
const configPath = configIndex >= 0 ? commandArgs[configIndex + 1] : null;
const configText = configPath ? readFileSync(configPath, 'utf8') : '';
const names = [...configText.matchAll(/^\s*name\s*=\s*"([^"]+)"/gm)].map((match) => match[1]);
const workerName = names[1] || names[0] || null;
const record = { commandArgs, command, configPath, configText, workerName };
appendFileSync(process.env.AI_CANCEL_TEST_WRANGLER_LOG, JSON.stringify(record) + '\n');

if (command === 'deploy') {
  process.stdout.write('https://' + workerName + '.axomxplain.workers.dev\n');
} else if (command === 'secret' && commandArgs[1] === 'put') {
  process.stdout.write('Creating the secret for the Worker "' + workerName + '"\n');
} else if (command === 'secret' && commandArgs[1] === 'list') {
  process.stdout.write(JSON.stringify([{ name: 'STAGING_ACCESS_TOKEN' }]));
} else if (command === 'delete') {
  process.stdout.write('Deleted disposable staging Worker\n');
} else {
  process.exitCode = 91;
}
`, { mode: 0o700 });
  chmodSync(pnpmPath, 0o700);

  writeFileSync(preloadPath, String.raw`import { appendFileSync } from 'node:fs';

globalThis.fetch = async (input) => {
  const url = new URL(typeof input === 'string' ? input : input.url);
  appendFileSync(
    process.env.AI_CANCEL_TEST_FETCH_LOG,
    JSON.stringify({ origin: url.origin, path: url.pathname }) + '\n',
  );

  const mode = process.env.AI_CANCEL_TEST_MODE;
  const headers = {
    'content-type': 'application/json',
    'x-syrabit-ai-cancel-probe': 'staging',
  };
  let status = 200;
  let body;

  if (url.pathname === '/ready') {
    body = { target: 'staging', status: 'ready' };
  } else if (url.pathname === '/stream-stop' && mode === 'failure') {
    status = 500;
    body = {
      target: 'staging',
      status: 'probe_stream_failed',
      errorName: 'TypeError',
      prompt: 'TEST_ONLY_PROMPT_SENTINEL',
      modelResponse: 'TEST_ONLY_MODEL_RESPONSE_SENTINEL',
      secretValue: 'TEST_ONLY_SECRET_VALUE_SENTINEL',
    };
  } else if (url.pathname === '/stream-stop') {
    body = {
      target: 'staging',
      firstDeltaReceived: true,
      firstDeltaMs: 42,
      abortedAfterFirstDelta: true,
      bindingSignalAborted: true,
      postAbortOutcome: 'rejected',
      bindingReaderCancelCalled: true,
      bindingReaderCancelSettled: true,
      bindingReaderCancelMs: 1,
      prompt: 'TEST_ONLY_PROMPT_SENTINEL',
      modelResponse: 'TEST_ONLY_MODEL_RESPONSE_SENTINEL',
      secretValue: 'TEST_ONLY_SECRET_VALUE_SENTINEL',
    };
  } else if (url.pathname === '/buffered-timeout') {
    body = {
      target: 'staging',
      helperStatus: 'rejected',
      helperReportedTimeout: true,
      helperMs: 500,
      timeoutSignalAborted: true,
      bindingCallMade: true,
      bindingPromiseSettled: true,
      bindingPromiseOutcome: 'rejected',
      bindingSettledMs: 500,
      prompt: 'TEST_ONLY_PROMPT_SENTINEL',
      modelResponse: 'TEST_ONLY_MODEL_RESPONSE_SENTINEL',
      secretValue: 'TEST_ONLY_SECRET_VALUE_SENTINEL',
    };
  } else {
    status = 404;
    body = { target: 'staging', status: 'probe_route_not_found' };
  }

  return new Response(JSON.stringify(body), { status, headers });
};
`);

  const harness = {
    root,
    wranglerLog,
    fetchLog,
    run(args, { edgeUrl = EXPECTED_EDGE_ORIGIN, runMode = mode } = {}) {
      const env = {
        ...process.env,
        PATH: `${binDir}${delimiter}${process.env.PATH ?? ''}`,
        NODE_OPTIONS: `--import=${preloadPath}`,
        AI_CANCEL_TEST_WRANGLER_LOG: wranglerLog,
        AI_CANCEL_TEST_FETCH_LOG: fetchLog,
        AI_CANCEL_TEST_MODE: runMode,
        STAGING_ACCESS_TOKEN: SECRET_SENTINEL,
        CLOUDFLARE_API_TOKEN: 'TEST_ONLY_CLOUDFLARE_TOKEN_SENTINEL',
        SYRABIT_STAGING_EDGE_URL: edgeUrl,
      };
      return spawnSync(process.execPath, [SCRIPT, ...args], {
        cwd: API_DIR,
        env,
        encoding: 'utf8',
        timeout: 30_000,
        maxBuffer: 1024 * 1024,
      });
    },
    fetchCalls() {
      if (!existsSync(fetchLog)) return [];
      return readFileSync(fetchLog, 'utf8')
        .trim()
        .split('\n')
        .filter(Boolean)
        .map((line) => JSON.parse(line));
    },
    wranglerCalls() {
      if (!existsSync(wranglerLog)) return [];
      return readFileSync(wranglerLog, 'utf8')
        .trim()
        .split('\n')
        .filter(Boolean)
        .map((line) => JSON.parse(line));
    },
    cleanup() {
      for (const call of this.wranglerCalls()) {
        const configPath = call.configPath;
        if (
          configPath
          && configPath.startsWith(`${API_DIR}${sep}`)
          && configPath.split(sep).at(-1)?.startsWith('wrangler.ai-cancel-probe-')
        ) {
          rmSync(configPath, { force: true });
        }
      }
      rmSync(root, { recursive: true, force: true });
      harnesses.delete(this);
    },
  };
  harnesses.add(harness);
  return harness;
}

function parseResult(child) {
  expect(child.error).toBeUndefined();
  expect(child.stdout).toBeTruthy();
  return JSON.parse(child.stdout.trim());
}

function assertNoSensitiveOutput(text) {
  expect(text).not.toContain(SECRET_SENTINEL);
  expect(text).not.toContain('TEST_ONLY_CLOUDFLARE_TOKEN_SENTINEL');
  expect(text).not.toContain(PROMPT_SENTINEL);
  expect(text).not.toContain(RESPONSE_SENTINEL);
}

afterEach(() => {
  for (const harness of harnesses) harness.cleanup();
});

describe('AI cancellation smoke safety', () => {
  it('refuses to run without staging opt-in before fetch or Wrangler', () => {
    const harness = makeHarness();
    const child = harness.run([]);
    const result = parseResult(child);

    expect(child.status).toBe(2);
    expect(result).toMatchObject({
      target: 'refused',
      status: 'refused',
      reason: 'explicit_staging_opt_in_required',
    });
    expect(harness.fetchCalls()).toEqual([]);
    expect(harness.wranglerCalls()).toEqual([]);
    assertNoSensitiveOutput(child.stdout);
  });

  it('rejects a non-allowlisted staging URL before fetch or Wrangler', () => {
    const harness = makeHarness();
    const child = harness.run(
      ['--staging-only', '--probe-only'],
      { edgeUrl: 'https://syrabit.ai' },
    );
    const result = parseResult(child);

    expect(child.status).toBe(1);
    expect(result.failureCode).toBe('unsafe_stage_target');
    expect(harness.fetchCalls()).toEqual([]);
    expect(harness.wranglerCalls()).toEqual([]);
    assertNoSensitiveOutput(child.stdout);
  });

  it('uses only the staging AI binding and removes the disposable Worker after success', () => {
    const harness = makeHarness();
    const child = harness.run(['--staging-only', '--probe-only']);
    const result = parseResult(child);
    const calls = harness.wranglerCalls();
    const deploy = calls.find((call) => call.command === 'deploy');
    const deleteCall = calls.find((call) => call.command === 'delete');

    expect(child.status).toBe(0);
    expect(result).toMatchObject({
      target: 'staging',
      status: 'passed',
      probeReady: { ready: true, targetIsStaging: true },
      cleanup: { attempted: true, removed: true },
      bindingStream: {
        firstDeltaReceived: true,
        firstDeltaMs: 42,
        abortedAfterFirstDelta: true,
        bindingSignalAborted: true,
        postAbortOutcome: 'rejected',
        bindingReaderCancelCalled: true,
        bindingReaderCancelSettled: true,
      },
      bufferedTimeout: {
        helperStatus: 'rejected',
        helperReportedTimeout: true,
        helperMs: 500,
        bindingPromiseSettled: true,
        bindingPromiseOutcome: 'rejected',
      },
    });

    expect(deploy).toBeDefined();
    expect(deleteCall).toBeDefined();
    expect(deploy.workerName).toMatch(/^syrabit-ai-cancel-probe-staging-[a-f0-9]{10}$/);
    expect(deleteCall.workerName).toBe(deploy.workerName);

    const config = deploy.configText;
    const tables = [...config.matchAll(/^\s*(\[\[?[^\]]+\]\]?)\s*$/gm)]
      .map((match) => match[1]);
    expect(tables).toEqual([
      '[env.staging]',
      '[env.staging.vars]',
      '[env.staging.ai]',
    ]);
    expect(config).toContain('legacy_env = true');
    expect(config).toContain('APP_ENV = "staging"');
    expect(config).toContain('binding = "AI"');
    expect(existsSync(deploy.configPath)).toBe(false);
    expect(harness.fetchCalls().map((call) => call.path)).toEqual([
      '/ready',
      '/stream-stop',
      '/buffered-timeout',
    ]);
    assertNoSensitiveOutput(child.stdout);
    assertNoSensitiveOutput(readFileSync(harness.wranglerLog, 'utf8'));
  });

  it('still attempts Worker and config cleanup when a probe fails', () => {
    const harness = makeHarness('failure');
    const child = harness.run(['--staging-only', '--probe-only']);
    const result = parseResult(child);
    const calls = harness.wranglerCalls();
    const deploy = calls.find((call) => call.command === 'deploy');
    const deleteCall = calls.find((call) => call.command === 'delete');

    expect(child.status).toBe(1);
    expect(result).toMatchObject({
      status: 'failed',
      failureCode: 'binding_stream_cancellation_check_failed',
      bindingStream: {
        httpStatus: 500,
        routeStatus: 'handler_error',
        probeErrorName: 'TypeError',
      },
      cleanup: { attempted: true, removed: true },
    });
    expect(deploy).toBeDefined();
    expect(deleteCall?.workerName).toBe(deploy?.workerName);
    expect(existsSync(deploy.configPath)).toBe(false);
    assertNoSensitiveOutput(child.stdout);
  });
});