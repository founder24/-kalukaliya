import { spawnSync } from 'node:child_process';
import { rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';

export const STAFF_PORTAL_TRACE_OPTIONS = Object.freeze({
  screenshots: false,
  snapshots: true,
  sources: false,
});

export function addLoginTokensToRedactions(tokens, sensitiveValues) {
  for (const name of ['access_token', 'refresh_token']) {
    if (typeof tokens?.[name] === 'string' && tokens[name]) {
      sensitiveValues.add(tokens[name]);
    }
  }
}

function sensitiveVariants(value) {
  const buffer = Buffer.from(value);
  return [
    value,
    encodeURIComponent(value),
    buffer.toString('base64'),
    buffer.toString('base64url'),
    JSON.stringify(value).slice(1, -1),
  ];
}

export async function saveRedactedTrace(rawTracePath, tracePath, sensitiveValues) {
  const redactions = [
    ...new Set(
      sensitiveValues
        .filter(Boolean)
        .flatMap(sensitiveVariants)
        .filter(Boolean),
    ),
  ];
  const redactionsPath = resolve(
    tmpdir(),
    `staff-portal-trace-redactions-${process.pid}.json`,
  );
  await writeFile(redactionsPath, JSON.stringify(redactions), { mode: 0o600 });
  try {
    const sanitizer = spawnSync('python3', [
      '-c',
      [
        'import json, sys, zipfile',
        'source, target, redactions_path = sys.argv[1:]',
        'with open(redactions_path, encoding="utf-8") as handle:',
        '    redactions = [value.encode() for value in json.load(handle) if value]',
        'with zipfile.ZipFile(source, "r") as incoming, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as outgoing:',
        '    for info in incoming.infolist():',
        '        data = incoming.read(info.filename)',
        '        for value in redactions:',
        '            data = data.replace(value, b"[REDACTED]")',
        '        outgoing.writestr(info, data)',
      ].join('\n'),
      rawTracePath,
      tracePath,
      redactionsPath,
    ], { encoding: 'utf8' });
    if (sanitizer.status !== 0) {
      throw new Error(sanitizer.stderr.trim() || `trace sanitizer exited ${sanitizer.status}`);
    }
  } finally {
    await rm(redactionsPath, { force: true });
  }
}