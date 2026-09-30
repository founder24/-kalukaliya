import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

import { readD1MigrationNamesFromDirectories } from './migration-ledger-files.mjs';

const temporaryDirectories: string[] = [];

function createMigrationDirectory(root: string, name: string, files: string[]) {
  const directory = path.join(root, name);
  mkdirSync(directory, { recursive: true });
  for (const file of files) {
    writeFileSync(path.join(directory, file), '-- fixture\n');
  }
  return directory;
}

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

describe('D1 migration source directories', () => {
  it('combines active and archived SQL identities and ignores non-SQL files', () => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'd1-migration-ledger-'));
    temporaryDirectories.push(root);
    const activeDirectory = createMigrationDirectory(root, 'active', [
      '0034_user_course_selection.sql',
      '0035_auth_rate_limits.sql',
      'README.md',
    ]);
    const archiveDirectory = createMigrationDirectory(root, 'archive', [
      '0034_referral_access_rewards.sql',
      '0035_chapter_slug_redirects.sql',
    ]);

    expect(readD1MigrationNamesFromDirectories([
      { directory: activeDirectory, label: 'active migrations' },
      { directory: archiveDirectory, label: 'applied migration archive' },
    ])).toEqual([
      '0034_referral_access_rewards.sql',
      '0034_user_course_selection.sql',
      '0035_auth_rate_limits.sql',
      '0035_chapter_slug_redirects.sql',
    ]);
  });

  it('rejects duplicate migration identities across active and archived directories', () => {
    const root = mkdtempSync(path.join(os.tmpdir(), 'd1-migration-ledger-'));
    temporaryDirectories.push(root);
    const activeDirectory = createMigrationDirectory(root, 'active', [
      '0034_user_course_selection.sql',
    ]);
    const archiveDirectory = createMigrationDirectory(root, 'archive', [
      '0034_user_course_selection.sql',
    ]);

    expect(() => readD1MigrationNamesFromDirectories([
      { directory: activeDirectory, label: 'active migrations' },
      { directory: archiveDirectory, label: 'applied migration archive' },
    ])).toThrow(
      'D1 migration identity 0034_user_course_selection.sql appears in both active migrations and applied migration archive.',
    );
  });
});