import { randomUUID } from 'node:crypto';
import { readFileSync, unlinkSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const API_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const EDGE_DIR = resolve(API_DIR, '../edge');
const API_CONFIG = resolve(API_DIR, 'wrangler.toml');
const EDGE_CONFIG = resolve(EDGE_DIR, 'wrangler.toml');
const PROBE_CONFIG = resolve(API_DIR, 'wrangler.ai-cancellation-smoke.toml');
const EXPECTED_EDGE_ORIGIN = 'https://syrabitworker-staging.axomxplain.workers.dev';
const PROBE_PREFIX = 'syrabit-ai-cancel-probe-staging-';
const DEFAULT_TIMEOUT_MS = 15_000;

class SmokeFailure extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

function readTomlSections(source, header) {
  const lines = source.split(/\r?\n/);
  const sections = [];
  for (let start = 0; start < lines.length; start += 1) {
    if (lines[start].trim() !== header) continue;
    const body = [];
    for (let index = start + 1; index < lines.length; index += 1) {
      if (/^\s*\[/.test(lines[index])) break;
      body.push(lines[index]);
    }
    sections.push(body.join('\n'));
  }
  if (sections.length === 0) throw new SmokeFailure('staging_config_missing');
  return sections;
}

function readTomlSection(source, header) {
  return readTomlSections(source, header)[0];
}

function tomlValue(section, key) {
  const escapedKey = key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = section.match(
    new RegExp(`^\\s*${escapedKey}\\s*=\\s*(?:"([^"]*)"|(true|false))\\s*$`, 'm'),
  );
  return match ? match[1] ?? match[2] : undefined;
}

function requireTomlValue(source, header, key, expected) {
  if (tomlValue(readTomlSection(source, header), key) !== expected) {
    throw new SmokeFailure('staging_config_mismatch');
  }
}

function requireBindingValue(source, header, binding, key, expected) {
  const matchingBinding = readTomlSections(source, header)
    .find((section) => tomlValue(section, 'binding') === binding);
  if (!matchingBinding || tomlValue(matchingBinding, key) !== expected) {
    throw new SmokeFailure('staging_config_mismatch');
  }
}

function requireStageResourceIsolation(source, stagingHeader, bindingName) {
  const stagingBindings = readTomlSections(source, stagingHeader);
  const comparisonHeaders = [
    `[[${bindingName}]]`,
    `[[env.production.${bindingName}]]`,
  ];

  for (const stagingBinding of stagingBindings) {
    const binding = tomlValue(stagingBinding, 'binding');
    const stagingResource = tomlValue(stagingBinding, 'id')
      ?? tomlValue(stagingBinding, 'database_id')
      ?? tomlValue(stagingBinding, 'bucket_name')
      ?? tomlValue(stagingBinding, 'index_name');
    if (!binding || !stagingResource) throw new SmokeFailure('staging_resource_missing');

    for (const header of comparisonHeaders) {
      const productionBinding = readTomlSections(source, header)
        .find((section) => tomlValue(section, 'binding') === binding);
      if (!productionBinding) continue;
      const productionResource = tomlValue(productionBinding, 'id')
        ?? tomlValue(productionBinding, 'database_id')
        ?? tomlValue(productionBinding, 'bucket_name')
        ?? tomlValue(productionBinding, 'index_name');
      if (productionResource === stagingResource) {
        throw new SmokeFailure('staging_resource_shared_with_production');
      }
    }
  }
}

function validateStagingConfigs() {
  const apiToml = readFileSync(API_CONFIG, 'utf8');
  const edgeToml = readFileSync(EDGE_CONFIG, 'utf8');
  const probeToml = readFileSync(PROBE_CONFIG, 'utf8');

  requireTomlValue(apiToml, '[env.staging]', 'name', 'syrabit-api-staging');
  requireTomlValue(apiToml, '[env.staging]', 'workers_dev', 'false');
  requireTomlValue(apiToml, '[env.staging.vars]', 'APP_ENV', 'staging');
  requireTomlValue(
    apiToml,
    '[[env.staging.d1_databases]]',
    'database_name',
    'syrabit-db-staging',
  );
  requireTomlValue(
    apiToml,
    '[[env.staging.r2_buckets]]',
    'bucket_name',
    'syrabit-assets-staging',
  );
  requireTomlValue(
    apiToml,
    '[[env.staging.vectorize]]',
    'index_name',
    'syrabit-rag-staging',
  );
  requireStageResourceIsolation(apiToml, '[[env.staging.d1_databases]]', 'd1_databases');
  requireStageResourceIsolation(apiToml, '[[env.staging.r2_buckets]]', 'r2_buckets');
  requireStageResourceIsolation(apiToml, '[[env.staging.kv_namespaces]]', 'kv_namespaces');
  requireStageResourceIsolation(apiToml, '[[env.staging.vectorize]]', 'vectorize');

  requireTomlValue(edgeToml, '[env.staging]', 'name', 'syrabitworker-staging');
  requireTomlValue(edgeToml, '[env.staging]', 'workers_dev', 'true');
  requireTomlValue(edgeToml, '[env.staging.vars]', 'APP_ENV', 'staging');
  requireTomlValue(edgeToml, '[[env.staging.services]]', 'binding', 'API_WORKER');
  requireTomlValue(
    edgeToml,
    '[[env.staging.services]]',
    'service',
    'syrabit-api-staging',
  );
  requireTomlValue(edgeToml, '[[env.staging.r2_buckets]]', 'bucket_name', 'syrabit-assets-staging');
  requireTomlValue(edgeToml, '[env.staging.ai]', 'binding', 'AI');
  requireBindingValue(
    edgeToml,
    '[[env.staging.kv_namespaces]]',
    'RATE_LIMIT_KV',
    'id',
    'f8ddefe50e7b4b46a8a4289bb9702fb2',
  );
  requireBindingValue(
    edgeToml,
    '[[env.staging.kv_namespaces]]',
    'ISR_CACHE_KV',
    'id',
    'da52862834ec4d53839de590279f64e7',
  );
  requireBindingValue(
    edgeToml,
    '[[env.staging.kv_namespaces]]',
    'CONTENT_KV',
    'id',
    '2e7df499ca5e48838c9afae928a4d504',
  );
  requireStageResourceIsolation(edgeToml, '[[env.staging.r2_buckets]]', 'r2_buckets');
  requireStageResourceIsolation(edgeToml, '[[env.staging.kv_namespaces]]', 'kv_namespaces');

  requireTomlValue(probeToml, '[env.staging]', 'workers_dev', 'true');
  requireTomlValue(probeToml, '[env.staging.vars]', 'APP_ENV', 'staging');
  requireTomlValue(probeToml, '[env.staging.ai]', 'binding', 'AI');
  if (tomlValue(readTomlSection(probeToml, '[env.staging]'), 'name')
    !== 'syrabit-ai-cancel-probe-staging') {
    throw new SmokeFailure('probe_environment_name_mismatch');
  }
  const probeTopLevel = probeToml.split(/^\s*\[/m, 1)[0];
  if (tomlValue(probeTopLevel, 'legacy_env') !== 'true') {
    throw new SmokeFailure('probe_environment_mode_mismatch');
  }

  const probeTables = [...probeToml.matchAll(/^\s*(\[\[?[^\]]+\]\]?)\s*$/gm)]
    .map((match) => match[1]);
  const allowedProbeTables = new Set([
    '[env.staging]',
    '[env.staging.vars]',
    '[env.staging.ai]',
  ]);
  if (probeTables.some((table) => !allowedProbeTables.has(table))) {
    throw new SmokeFailure('probe_has_non_staging_binding');
  }
}

function getStagingEdgeOrigin() {
  const configured = process.env.SYRABIT_STAGING_EDGE_URL ?? EXPECTED_EDGE_ORIGIN;
  let url;
  try {
    url = new URL(configured);
  } catch {
    throw new SmokeFailure('unsafe_stage_target');
  }
  if (
    url.origin !== EXPECTED_EDGE_ORIGIN
    || url.pathname !== '/'
    || url.search
    || url.hash
  ) {
    throw new SmokeFailure('unsafe_stage_target');
  }
  return url.origin;
}

function runWrangler(args, input) {
  const result = spawnSync('pnpm', ['exec', 'wrangler', ...args], {
    cwd: API_DIR,
    env: process.env,
    input,
    encoding: 'utf8',
    timeout: 120_000,
    maxBuffer: 2 * 1024 * 1024,
    windowsHide: true,
  });
  return {
    ok: !result.error && result.status === 0,
    stdout: result.stdout ?? '',
    stderr: result.stderr ?? '',
  };
}

function requireWrangler(args, input) {
  const result = runWrangler(args, input);
  if (!result.ok) {
    const output = `${result.stdout}\n${result.stderr}`
      .replace(/\u001b\[[0-9;]*m/g, '');
    const errorIndex = output.lastIndexOf('✘ [ERROR]');
    const diagnostic = (errorIndex >= 0 ? output.slice(errorIndex) : output).toLowerCase();
    const code = /not allowed in named service environments|service environment configuration/.test(diagnostic)
      ? 'wrangler_environment_config_failed'
      : /workers\.dev|workers_dev/.test(diagnostic)
        ? 'wrangler_workers_dev_setup_failed'
        : /permission|unauthorized|forbidden|authentication|api token/.test(diagnostic)
          ? 'wrangler_auth_or_permission_failed'
          : /quota|billing|account limit/.test(diagnostic)
            ? 'wrangler_account_limit'
            : 'wrangler_command_failed';
    throw new SmokeFailure(code);
  }
  return `${result.stdout}\n${result.stderr}`;
}

function getProbeUrl(deployOutput, workerName, stagingOrigin) {
  const accountSuffix = new URL(stagingOrigin).hostname.split('.').slice(1).join('.');
  const expectedHost = `${workerName}.${accountSuffix}`;
  const urls = [...deployOutput.matchAll(/https:\/\/[a-z0-9.-]+\.workers\.dev/gi)]
    .map((match) => new URL(match[0]));
  const probeUrl = urls.find((url) => (
    url.hostname === expectedHost
    && url.protocol === 'https:'
    && url.pathname === '/'
    && !url.search
    && !url.hash
  ));
  if (!probeUrl) throw new SmokeFailure('probe_url_not_staging');
  return probeUrl.origin;
}

async function bestEffortCancel(body, timeoutMs = 750) {
  if (!body) return;
  let timer;
  await Promise.race([
    body.cancel().catch(() => {}),
    new Promise((resolve) => {
      timer = setTimeout(resolve, timeoutMs);
    }),
  ]);
  clearTimeout(timer);
}

function parseDataFrame(frame) {
  const data = frame
    .replace(/\r/g, '')
    .split('\n')
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trim())
    .join('\n');
  if (!data) return false;
  try {
    const event = JSON.parse(data);
    return typeof event?.content === 'string' && event.content.length > 0;
  } catch {
    return false;
  }
}

async function checkStagingHealth(edgeOrigin, token) {
  const response = await fetch(`${edgeOrigin}/health`, {
    headers: { 'X-Syrabit-Staging-Token': token },
    signal: AbortSignal.timeout(10_000),
  });
  await bestEffortCancel(response.body);
  return response.status;
}

async function runEdgeChatStop(edgeOrigin, token) {
  const requestId = randomUUID();
  const anonymousId = `staging-cancel-smoke-${randomUUID()}`;
  const controller = new AbortController();
  const startedAt = Date.now();
  let timedOut = false;
  let requestSent = false;
  let httpStatus = null;
  let firstDeltaReceived = false;
  let firstDeltaMs = null;
  let streamReadErrorName = null;
  let serverCancelStatus = null;
  let serverCancelMs = null;
  const firstDeltaTimer = setTimeout(() => {
    timedOut = true;
    controller.abort(new DOMException('First delta deadline elapsed', 'TimeoutError'));
  }, DEFAULT_TIMEOUT_MS);

  try {
    requestSent = true;
    const response = await fetch(`${edgeOrigin}/api/v1/chat/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Syrabit-Staging-Token': token,
        'x-anon-id': anonymousId,
      },
      body: JSON.stringify({
        message: 'Explain Newton’s first law with several examples.',
        lang: 'en',
        client_request_id: requestId,
      }),
      signal: controller.signal,
    });
    httpStatus = response.status;

    if (!response.ok || !response.body) {
      await bestEffortCancel(response.body);
    } else {
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      try {
        while (!firstDeltaReceived) {
          const part = await reader.read();
          if (part.done) break;
          buffer = `${buffer}${decoder.decode(part.value, { stream: true })}`.replace(/\r\n/g, '\n');

          let boundary;
          while ((boundary = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, boundary);
            buffer = buffer.slice(boundary + 2);
            if (parseDataFrame(frame)) {
              firstDeltaReceived = true;
              firstDeltaMs = Date.now() - startedAt;
              controller.abort(new DOMException('Stopped after first delta', 'AbortError'));
              break;
            }
          }
        }
      } catch (error) {
        streamReadErrorName = error instanceof Error ? error.name : 'NonError';
      } finally {
        let cancelTimer;
        await Promise.race([
          reader.cancel().catch(() => {}),
          new Promise((resolve) => {
            cancelTimer = setTimeout(resolve, 750);
          }),
        ]);
        clearTimeout(cancelTimer);
      }
    }
  } catch (error) {
    streamReadErrorName = error instanceof Error ? error.name : 'NonError';
  } finally {
    clearTimeout(firstDeltaTimer);
    if (!controller.signal.aborted) {
      controller.abort(new DOMException('Staging smoke request ended', 'AbortError'));
    }

    if (requestSent) {
      const cancelStartedAt = Date.now();
      try {
        const cancelResponse = await fetch(`${edgeOrigin}/api/v1/chat/cancel`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Syrabit-Staging-Token': token,
            'x-anon-id': anonymousId,
          },
          body: JSON.stringify({ client_request_id: requestId }),
          signal: AbortSignal.timeout(10_000),
        });
        serverCancelStatus = cancelResponse.status;
        await bestEffortCancel(cancelResponse.body);
      } catch {
        serverCancelStatus = null;
      }
      serverCancelMs = Date.now() - cancelStartedAt;
    }
  }

  return {
    httpStatus,
    firstDeltaReceived,
    firstDeltaMs,
    abortedAfterFirstDelta: firstDeltaReceived && controller.signal.aborted,
    timeoutBeforeFirstDelta: timedOut && !firstDeltaReceived,
    streamReadErrorName,
    serverCancelStatus,
    serverCancelMs,
  };
}

async function postProbe(probeOrigin, token, path) {
  const startedAt = Date.now();
  const response = await fetch(`${probeOrigin}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-Syrabit-Staging-Token': token,
    },
    body: '{}',
    signal: AbortSignal.timeout(30_000),
  });
  const body = await response.json().catch(() => null);
  return {
    httpStatus: response.status,
    requestMs: Date.now() - startedAt,
    probeMarker: response.headers.get('x-syrabit-ai-cancel-probe') === 'staging',
    body: body && typeof body === 'object' ? body : null,
  };
}

async function waitForProbeReady(probeOrigin, token) {
  const startedAt = Date.now();
  let lastStatus = null;
  let targetIsStaging = false;
  let authStatus = 'unknown';

  for (let attempt = 0; attempt < 20; attempt += 1) {
    const response = await postProbe(probeOrigin, token, '/ready');
    lastStatus = response.httpStatus;
    targetIsStaging = response.body?.target === 'staging';
    if (response.body?.status === 'probe_auth_not_configured') {
      authStatus = 'probe_auth_not_configured';
    } else if (response.body?.status === 'probe_auth_rejected') {
      authStatus = 'probe_auth_rejected';
      break;
    }
    if (lastStatus === 200 && targetIsStaging && response.body?.status === 'ready') {
      return {
        httpStatus: lastStatus,
        ready: true,
        targetIsStaging,
        waitMs: Date.now() - startedAt,
        authStatus: 'authenticated',
      };
    }
    if (attempt < 19) await new Promise((resolve) => setTimeout(resolve, 500));
  }

  return {
    httpStatus: lastStatus,
    ready: false,
    targetIsStaging,
    waitMs: Date.now() - startedAt,
    authStatus,
  };
}

function numericField(value) {
  return Number.isFinite(value) ? value : null;
}

function sanitizeStreamProbe(result) {
  const body = result.body ?? {};
  return {
    httpStatus: result.httpStatus,
    requestMs: numericField(result.requestMs),
    workerResponseSeen: result.probeMarker === true,
    targetIsStaging: body.target === 'staging',
    authStatus: ['probe_auth_not_configured', 'probe_auth_rejected'].includes(body.status)
      ? body.status
      : 'authenticated_or_unknown',
    routeStatus: body.status === 'probe_route_not_found'
      ? 'route_not_found'
      : body.status === 'probe_stream_failed'
        ? 'handler_error'
        : result.probeMarker ? 'matched_or_unknown' : 'worker_response_missing',
    probeErrorName: typeof body.errorName === 'string' && /^[A-Za-z]{1,32}$/.test(body.errorName)
      ? body.errorName
      : null,
    firstDeltaOutcome: typeof body.firstDeltaOutcome === 'string'
      && /^[A-Za-z_]{1,32}$/.test(body.firstDeltaOutcome)
      ? body.firstDeltaOutcome
      : 'unknown',
    requestedPath: ['/stream-stop', '/buffered-timeout', '/'].includes(body.requestedPath)
      ? body.requestedPath
      : body.requestedPath ? 'other' : null,
    firstDeltaReceived: body.firstDeltaReceived === true,
    firstDeltaMs: numericField(body.firstDeltaMs),
    abortedAfterFirstDelta: body.abortedAfterFirstDelta === true,
    bindingSignalAborted: body.bindingSignalAborted === true,
    postAbortOutcome: ['rejected', 'done', 'yielded', 'pending'].includes(body.postAbortOutcome)
      ? body.postAbortOutcome
      : 'unknown',
    bindingReaderCancelCalled: body.bindingReaderCancelCalled === true,
    bindingReaderCancelSettled: body.bindingReaderCancelSettled === true,
    bindingReaderCancelMs: numericField(body.bindingReaderCancelMs),
  };
}

function sanitizeBufferedProbe(result) {
  const body = result.body ?? {};
  return {
    httpStatus: result.httpStatus,
    requestMs: numericField(result.requestMs),
    workerResponseSeen: result.probeMarker === true,
    targetIsStaging: body.target === 'staging',
    authStatus: ['probe_auth_not_configured', 'probe_auth_rejected'].includes(body.status)
      ? body.status
      : 'authenticated_or_unknown',
    routeStatus: body.status === 'probe_route_not_found' ? 'route_not_found' : 'matched_or_unknown',
    requestedPath: ['/stream-stop', '/buffered-timeout', '/'].includes(body.requestedPath)
      ? body.requestedPath
      : body.requestedPath ? 'other' : null,
    helperStatus: ['fulfilled', 'rejected'].includes(body.helperStatus)
      ? body.helperStatus
      : 'unknown',
    helperReportedTimeout: body.helperReportedTimeout === true,
    helperMs: numericField(body.helperMs),
    timeoutSignalAborted: body.timeoutSignalAborted === true,
    bindingCallMade: body.bindingCallMade === true,
    bindingPromiseSettled: body.bindingPromiseSettled === true,
    bindingPromiseOutcome: ['fulfilled', 'rejected', 'pending', 'not_started'].includes(
      body.bindingPromiseOutcome,
    ) ? body.bindingPromiseOutcome : 'unknown',
    bindingSettledMs: numericField(body.bindingSettledMs),
  };
}

function streamProbePassed(probe) {
  return probe.httpStatus === 200
    && probe.targetIsStaging
    && probe.firstDeltaReceived
    && probe.abortedAfterFirstDelta
    && probe.bindingSignalAborted
    && probe.postAbortOutcome === 'rejected'
    && probe.bindingReaderCancelCalled
    && probe.bindingReaderCancelSettled;
}

function bufferedProbePassed(probe) {
  return probe.httpStatus === 200
    && probe.targetIsStaging
    && probe.helperStatus === 'rejected'
    && probe.helperReportedTimeout
    && probe.timeoutSignalAborted
    && probe.bindingCallMade
    && probe.bindingPromiseSettled
    && probe.bindingPromiseOutcome === 'rejected';
}

function probeWorkerName() {
  const suffix = randomUUID().replaceAll('-', '').slice(0, 10);
  return `${PROBE_PREFIX}${suffix}`;
}

function createRunProbeConfig(workerName) {
  const template = readFileSync(PROBE_CONFIG, 'utf8');
  const templateStageName = 'name = "syrabit-ai-cancel-probe-staging"';
  if (!template.includes(templateStageName)) {
    throw new SmokeFailure('probe_config_template_mismatch');
  }
  const runtimeConfig = resolve(API_DIR, `wrangler.ai-cancel-probe-${randomUUID().replaceAll('-', '')}.toml`);
  writeFileSync(
    runtimeConfig,
    template.replace(templateStageName, `name = "${workerName}"`),
    { flag: 'wx', mode: 0o600 },
  );
  return runtimeConfig;
}

function removeRunProbeConfig(runtimeConfig) {
  if (!runtimeConfig) return true;
  try {
    unlinkSync(runtimeConfig);
    return true;
  } catch {
    return false;
  }
}

function runDelete(workerName, configPath) {
  if (!workerName.startsWith(PROBE_PREFIX)) {
    return { attempted: false, removed: false, safeName: false };
  }
  const result = runWrangler([
    'delete',
    workerName,
    '--force',
    '--config',
    configPath ?? PROBE_CONFIG,
  ]);
  const notFound = /Worker does not exist|Worker not found/i.test(result.stderr);
  return {
    attempted: true,
    removed: result.ok || notFound,
    alreadyAbsent: notFound,
  };
}

function printResult(result, exitCode) {
  console.log(JSON.stringify(result));
  process.exitCode = exitCode;
}

async function main() {
  const rawArgs = process.argv.slice(2);
  const args = rawArgs[0] === '--' ? rawArgs.slice(1) : rawArgs;
  const allowedArgs = new Set(['--staging-only', '--preflight-only', '--probe-only']);
  if (
    !args.includes('--staging-only')
    || args.some((argument) => !allowedArgs.has(argument))
    || args.filter((argument) => argument === '--staging-only').length !== 1
    || args.filter((argument) => argument === '--preflight-only').length > 1
    || args.filter((argument) => argument === '--probe-only').length > 1
    || (args.includes('--preflight-only') && args.includes('--probe-only'))
  ) {
    printResult({
      target: 'refused',
      status: 'refused',
      reason: 'explicit_staging_opt_in_required',
    }, 2);
    return;
  }

  const result = {
    target: 'staging',
    status: 'failed',
    providerComputeTermination: 'unverified_without_provider_telemetry',
    preflight: 'pending',
    healthStatus: null,
    edgeChat: null,
    probeReady: null,
    probeSecretVisible: null,
    bindingStream: null,
    bufferedTimeout: null,
    cleanup: { attempted: false, removed: false },
  };
  let workerName;
  let runtimeProbeConfig;
  let deploymentAttempted = false;
  let edgeOrigin;
  let failureCode;

  try {
    edgeOrigin = getStagingEdgeOrigin();
    validateStagingConfigs();
    result.preflight = 'passed';

    if (args.includes('--preflight-only')) {
      result.status = 'preflight_passed';
      printResult(result, 0);
      return;
    }

    if (!process.env.STAGING_ACCESS_TOKEN || !process.env.CLOUDFLARE_API_TOKEN) {
      throw new SmokeFailure('required_secret_missing');
    }
    const token = process.env.STAGING_ACCESS_TOKEN;

    if (!args.includes('--probe-only')) {
      result.healthStatus = await checkStagingHealth(edgeOrigin, token);
      if (result.healthStatus !== 200) throw new SmokeFailure('staging_health_failed');
      result.edgeChat = await runEdgeChatStop(edgeOrigin, token);
    }

    workerName = probeWorkerName();
    runtimeProbeConfig = createRunProbeConfig(workerName);
    deploymentAttempted = true;
    const deployOutput = requireWrangler([
      'deploy',
      '--config',
      runtimeProbeConfig,
      '--env',
      'staging',
    ]);
    const probeOrigin = getProbeUrl(deployOutput, workerName, edgeOrigin);

    const secretUpload = runWrangler([
      'secret',
      'put',
      'STAGING_ACCESS_TOKEN',
      '--config',
      runtimeProbeConfig,
      '--env',
      'staging',
    ], `${token}\n`);
    if (!secretUpload.ok) throw new SmokeFailure('probe_secret_setup_failed');
    const secretOutput = `${secretUpload.stdout}\n${secretUpload.stderr}`;
    const secretTarget = secretOutput
      .match(/Creating the secret for the Worker "([^"]+)"(?: \(([^)]+)\))?/)?.slice(1);
    if (secretTarget?.[0] !== workerName) {
      throw new SmokeFailure('probe_secret_target_mismatch');
    }

    const secretList = requireWrangler([
      'secret',
      'list',
      '--config',
      runtimeProbeConfig,
      '--env',
      'staging',
      '--format',
      'json',
    ]);
    let listedSecrets;
    try {
      listedSecrets = JSON.parse(secretList);
    } catch {
      throw new SmokeFailure('probe_secret_verification_failed');
    }
    result.probeSecretVisible = Array.isArray(listedSecrets)
      && listedSecrets.some((secret) => secret?.name === 'STAGING_ACCESS_TOKEN');
    if (!result.probeSecretVisible) {
      throw new SmokeFailure('probe_secret_not_visible');
    }

    result.probeReady = await waitForProbeReady(probeOrigin, token);
    if (!result.probeReady.ready) throw new SmokeFailure('probe_worker_not_ready');

    result.bindingStream = sanitizeStreamProbe(
      await postProbe(probeOrigin, token, '/stream-stop'),
    );
    result.bufferedTimeout = sanitizeBufferedProbe(
      await postProbe(probeOrigin, token, '/buffered-timeout'),
    );

    const edgeChatPassed = result.edgeChat === null
      || (
        result.edgeChat.httpStatus === 200
        && result.edgeChat.firstDeltaReceived
        && result.edgeChat.abortedAfterFirstDelta
        && Number.isInteger(result.edgeChat.serverCancelStatus)
        && result.edgeChat.serverCancelStatus >= 200
        && result.edgeChat.serverCancelStatus < 300
      );
    if (!edgeChatPassed) {
      failureCode = result.edgeChat?.httpStatus === 503
        ? 'staging_chat_stream_unavailable'
        : 'staging_edge_cancellation_check_failed';
    } else if (!streamProbePassed(result.bindingStream)) {
      failureCode = 'binding_stream_cancellation_check_failed';
    } else if (!bufferedProbePassed(result.bufferedTimeout)) {
      failureCode = 'buffered_timeout_check_failed';
    }
  } catch (error) {
    failureCode = error instanceof SmokeFailure ? error.code : 'smoke_failed';
  } finally {
    if (deploymentAttempted && workerName) {
      result.cleanup = runDelete(workerName, runtimeProbeConfig);
      if (!result.cleanup.removed && !failureCode) failureCode = 'probe_cleanup_failed';
    }
    if (!removeRunProbeConfig(runtimeProbeConfig) && !failureCode) {
      failureCode = 'probe_config_cleanup_failed';
    }
  }

  if (failureCode) {
    result.failureCode = failureCode;
  } else {
    result.status = 'passed';
  }
  const exitCode = result.status === 'passed' ? 0 : 1;
  printResult(result, exitCode);
}

await main();