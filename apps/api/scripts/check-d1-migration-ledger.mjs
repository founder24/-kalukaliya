import path from 'node:path';

import { assertD1MigrationLedgerMatchesFiles } from '../src/db/migration-ledger-contract.mjs';
import { readD1MigrationNamesFromDirectories } from '../src/db/migration-ledger-files.mjs';

function parseWranglerMigrationRows(rawOutput) {
  let payload;
  try {
    payload = JSON.parse(rawOutput);
  } catch {
    throw new Error('Wrangler returned invalid JSON while reading the production D1 migration ledger.');
  }

  if (!Array.isArray(payload)) {
    throw new Error('Wrangler returned an unexpected JSON shape for the production D1 migration ledger.');
  }

  const names = [];
  for (const result of payload) {
    if (!result || !Array.isArray(result.results)) {
      throw new Error('Wrangler returned a result without a migration ledger rows array.');
    }

    for (const row of result.results) {
      if (!row || typeof row.name !== 'string' || !row.name.endsWith('.sql')) {
        throw new Error('Production D1 returned an invalid migration identity.');
      }
      names.push(row.name);
    }
  }
  return names;
}

function getDirectoryArgument(args, option, { required = false } = {}) {
  const optionIndexes = args
    .map((argument, index) => argument === option ? index : -1)
    .filter(index => index !== -1);

  if (optionIndexes.length > 1) {
    throw new Error(`Only one ${option} value may be supplied.`);
  }

  const optionIndex = optionIndexes[0];
  if (optionIndex === undefined) {
    if (required) {
      throw new Error(
        'Usage: node check-d1-migration-ledger.mjs --migrations-dir <directory> '
        + '[--archive-dir <directory>]',
      );
    }
    return null;
  }

  const value = args[optionIndex + 1];
  if (!value || value.startsWith('--')) {
    throw new Error(`${option} requires a directory.`);
  }
  return path.resolve(process.cwd(), value);
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString('utf8');
}

async function main() {
  const args = process.argv.slice(2);
  const migrationsDirectory = getDirectoryArgument(args, '--migrations-dir', { required: true });
  const archiveDirectory = getDirectoryArgument(args, '--archive-dir');
  const trackedDirectories = [
    { directory: migrationsDirectory, label: 'active migrations' },
  ];
  if (archiveDirectory) {
    trackedDirectories.push({ directory: archiveDirectory, label: 'applied migration archive' });
  }
  const trackedMigrationNames = readD1MigrationNamesFromDirectories(trackedDirectories);
  const appliedMigrationNames = parseWranglerMigrationRows(await readStdin());
  const result = assertD1MigrationLedgerMatchesFiles(appliedMigrationNames, trackedMigrationNames);

  console.log(
    `Production D1 migration ledger matches ${result.appliedMigrationCount} active or archived SQL migration identities.`,
  );
}

main().catch(error => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`::error::${message}`);
  process.exitCode = 1;
});