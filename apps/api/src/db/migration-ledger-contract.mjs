export function assertD1MigrationLedgerMatchesFiles(appliedMigrationNames, trackedMigrationNames) {
  if (!Array.isArray(appliedMigrationNames) || !Array.isArray(trackedMigrationNames)) {
    throw new TypeError('Applied and tracked D1 migration names must be arrays.');
  }

  if (
    appliedMigrationNames.some(name => typeof name !== 'string' || !name.endsWith('.sql'))
    || trackedMigrationNames.some(name => typeof name !== 'string' || !name.endsWith('.sql'))
  ) {
    throw new TypeError('D1 migration identities must be .sql filenames.');
  }

  const trackedNames = new Set(trackedMigrationNames);
  const missing = [...new Set(appliedMigrationNames)]
    .filter(name => !trackedNames.has(name))
    .sort();

  if (missing.length > 0) {
    throw new Error(
      'Production D1 has applied migration identities with no matching tracked SQL files: '
      + `${missing.join(', ')}. Restore the exact filenames or reconcile the production ledger `
      + 'before release; refusing to apply pending migrations.',
    );
  }

  return { appliedMigrationCount: new Set(appliedMigrationNames).size };
}