export declare function assertD1MigrationLedgerMatchesFiles(
  appliedMigrationNames: readonly string[],
  trackedMigrationNames: readonly string[],
): { appliedMigrationCount: number };