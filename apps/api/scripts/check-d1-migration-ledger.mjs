import fs from 'node:fs';
import path from 'node:path';

import { assertD1MigrationLedgerMatchesFiles } from '../src/db/migration-ledger-contract.mjs';

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

function getMigrationsDirectory(args) {
  const optionIndex = args.indexOf('--migrations-dir');
  if (optionIndex === -1 || !args[optionIndex + 1]) {
    throw new Error('Usage: node check-d1-migration-ledger.mjs --migrations-dir <directory>');
  }
  return path.resolve(process.cwd(), args[optionIndex + 1]);
}

async function readStdin() {
  const chunks = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString('utf8');
}

async function main() {
  const migrationsDirectory = getMigrationsDirectory(process.argv.slice(2));
  const trackedMigrationNames = fs.readdirSync(migrationsDirectory, { withFileTypes: true })
    .filter(entry => entry.isFile() && entry.name.endsWith('.sql'))
    .map(entry => entry.name);
  const appliedMigrationNames = parseWranglerMigrationRows(await readStdin());
  const result = assertD1MigrationLedgerMatchesFiles(appliedMigrationNames, trackedMigrationNames);

  console.log(
    `Production D1 migration ledger matches ${result.appliedMigrationCount} tracked SQL migration files.`,
  );
}

main().catch(error => {
  const message = error instanceof Error ? error.message : String(error);
  console.error(`::error::${message}`);
  process.exitCode = 1;
});