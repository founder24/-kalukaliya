import fs from 'node:fs';

export function readD1MigrationNamesFromDirectories(directories) {
  if (!Array.isArray(directories) || directories.length === 0) {
    throw new TypeError('At least one D1 migration directory is required.');
  }

  const names = [];
  const sourceByName = new Map();

  for (const entry of directories) {
    if (
      !entry
      || typeof entry.directory !== 'string'
      || entry.directory.length === 0
      || typeof entry.label !== 'string'
      || entry.label.length === 0
    ) {
      throw new TypeError('Each D1 migration directory requires a path and label.');
    }

    const files = fs.readdirSync(entry.directory, { withFileTypes: true });
    for (const file of files) {
      if (!file.isFile() || !file.name.endsWith('.sql')) continue;

      const previousSource = sourceByName.get(file.name);
      if (previousSource) {
        throw new Error(
          `D1 migration identity ${file.name} appears in both ${previousSource} and ${entry.label}.`,
        );
      }

      sourceByName.set(file.name, entry.label);
      names.push(file.name);
    }
  }

  return names.sort();
}