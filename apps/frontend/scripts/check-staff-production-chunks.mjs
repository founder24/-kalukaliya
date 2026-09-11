import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { chromium } from 'playwright';

const TARGETS = [
  'src/components/admin/AdminDashboard.jsx',
  'src/components/admin/AdminHealth.jsx',
];
const TDZ_ERROR = /cannot access .+ before initialization|can't access lexical declaration|temporal dead zone/i;
const port = Number(process.env.STAFF_CHUNK_SMOKE_PORT || 4173);
const origin = `http://127.0.0.1:${port}`;

function stronglyConnectedComponents(graph) {
  let index = 0;
  const stack = [];
  const onStack = new Set();
  const indices = new Map();
  const lowLinks = new Map();
  const components = [];

  function visit(node) {
    indices.set(node, index);
    lowLinks.set(node, index);
    index += 1;
    stack.push(node);
    onStack.add(node);

    for (const dependency of graph.get(node) || []) {
      if (!graph.has(dependency)) continue;
      if (!indices.has(dependency)) {
        visit(dependency);
        lowLinks.set(node, Math.min(lowLinks.get(node), lowLinks.get(dependency)));
      } else if (onStack.has(dependency)) {
        lowLinks.set(node, Math.min(lowLinks.get(node), indices.get(dependency)));
      }
    }

    if (lowLinks.get(node) !== indices.get(node)) return;
    const component = [];
    let member;
    do {
      member = stack.pop();
      onStack.delete(member);
      component.push(member);
    } while (member !== node);
    components.push(component);
  }

  for (const node of graph.keys()) {
    if (!indices.has(node)) visit(node);
  }
  return components;
}

async function waitForServer(child) {
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Vite preview exited early with code ${child.exitCode}`);
    }
    try {
      const response = await fetch(origin);
      if (response.ok) return;
    } catch {
      // Preview is still starting.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`Timed out waiting for Vite preview at ${origin}`);
}

const manifest = JSON.parse(await readFile(new URL('../dist/.vite/manifest.json', import.meta.url)));
const targetEntries = TARGETS.map((source) => {
  const entry = manifest[source];
  if (!entry?.file) throw new Error(`Production manifest is missing staff chunk: ${source}`);
  return { source, file: entry.file };
});

const graph = new Map(
  Object.entries(manifest)
    .filter(([, entry]) => entry.file?.endsWith('.js'))
    .map(([key, entry]) => [key, entry.imports || []]),
);
const cycles = stronglyConnectedComponents(graph).filter((component) => component.length > 1);
if (cycles.length) {
  throw new Error(
    `Unexpected cross-chunk cycle(s) in production manifest:\n${cycles
      .map((component) => `  - ${component.join(' -> ')}`)
      .join('\n')}`,
  );
}

const preview = spawn(
  process.execPath,
  ['./node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
  { cwd: new URL('..', import.meta.url), stdio: ['ignore', 'pipe', 'pipe'] },
);
let previewOutput = '';
preview.stdout.on('data', (chunk) => { previewOutput += chunk; });
preview.stderr.on('data', (chunk) => { previewOutput += chunk; });

let browser;
try {
  await waitForServer(preview);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = [];
  await page.route('https://api.syrabit.ai/**', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }),
  );
  await page.route(`${origin}/__staff-chunk-smoke`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/html',
      body: '<!doctype html><html><body><div id="root"></div></body></html>',
    }),
  );
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text());
  });

  // Establish the preview origin without loading the SPA entry, which performs
  // session discovery. The imports below still fetch the real built assets.
  await page.goto(`${origin}/__staff-chunk-smoke`, { waitUntil: 'domcontentloaded' });
  for (const { source, file } of targetEntries) {
    try {
      await page.evaluate(async (url) => {
        const loaded = await import(url);
        if (typeof loaded.default !== 'function') {
          throw new Error('chunk did not expose a default React component');
        }
      }, `/${file}`);
    } catch (error) {
      errors.push(`${source}: ${error.message}`);
    }
  }

  const tdzErrors = errors.filter((message) => TDZ_ERROR.test(message));
  if (errors.length) {
    throw new Error(
      `Staff production chunks emitted browser errors:\n${errors
        .map((message) => `  - ${message}`)
        .join('\n')}${tdzErrors.length ? '\nTemporal-dead-zone failure detected.' : ''}`,
    );
  }
  console.log(`Verified ${targetEntries.map(({ file }) => file).join(' and ')} in Chromium; no cross-chunk cycles found.`);
} finally {
  await browser?.close();
  preview.kill('SIGTERM');
  await new Promise((resolve) => {
    if (preview.exitCode !== null) return resolve();
    preview.once('exit', resolve);
    setTimeout(resolve, 3_000).unref();
  });
  if (preview.exitCode && preview.exitCode !== 143) {
    console.error(previewOutput);
  }
}