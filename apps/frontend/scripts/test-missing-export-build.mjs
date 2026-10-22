import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { build } from 'vite';

const tempRoot = await mkdtemp(
  path.join(os.tmpdir(), 'syrabit-vite-missing-export-'),
);

try {
  const dependencyPath = path.join(tempRoot, 'dependency.js');
  const entryPath = path.join(tempRoot, 'entry.js');

  await writeFile(
    dependencyPath,
    'export const available = true;\n',
    'utf8',
  );
  await writeFile(
    entryPath,
    'import { missing } from "./dependency.js";\nconsole.log(missing);\n',
    'utf8',
  );

  const viteConfigFactory = (await import('../vite.config.js')).default;
  const viteConfig = await viteConfigFactory({ mode: 'production' });
  const onwarn = viteConfig?.build?.rollupOptions?.onwarn;
  assert.equal(typeof onwarn, 'function', 'Vite config must install the missing-export guard');

  const forwardedWarnings = [];
  onwarn(
    { code: 'CIRCULAR_DEPENDENCY', message: 'unrelated warning' },
    (warning) => forwardedWarnings.push(warning),
  );
  assert.equal(
    forwardedWarnings[0]?.code,
    'CIRCULAR_DEPENDENCY',
    'unrelated Rollup warnings should keep their default handling',
  );

  await assert.rejects(
    build({
      configFile: false,
      root: tempRoot,
      logLevel: 'silent',
      build: {
        write: false,
        rollupOptions: {
          input: entryPath,
          onwarn,
        },
      },
    }),
    /MISSING_EXPORT|not exported|does not provide an export/i,
    'Vite must reject an import of a named export that does not exist',
  );

  console.log(
    '[missing-export] passed: Vite rejects missing named exports and forwards unrelated warnings',
  );
} finally {
  await rm(tempRoot, { recursive: true, force: true });
}
