export interface D1MigrationDirectory {
  directory: string;
  label: string;
}

export declare function readD1MigrationNamesFromDirectories(
  directories: readonly D1MigrationDirectory[],
): string[];