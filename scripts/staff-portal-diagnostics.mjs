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
        'import json, re, sys, zipfile',
        'source, target, redactions_path = sys.argv[1:]',
        'with open(redactions_path, encoding="utf-8") as handle:',
        '    redactions = [value.encode() for value in json.load(handle) if value]',
        'signatures = [',
        '    ("authorization header", re.compile(rb"authorization[\'\\\" ]*[:=][ ]*[\'\\\"]?(?:bearer|basic)[ ]+(?!\\[REDACTED\\])[^\\s\'\\\",}]+", re.I)),',
        '    ("authorization header record", re.compile(rb"[\'\\\"]name[\'\\\"]\\s*:\\s*[\'\\\"]authorization[\'\\\"].{0,160}?[\'\\\"]value[\'\\\"]\\s*:\\s*[\'\\\"](?:bearer|basic)\\s+(?!\\[REDACTED\\])[^\'\\\"]+", re.I | re.S)),',
        '    ("JWT-shaped value", re.compile(rb"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])")),',
        '    ("Cloudflare Access credential", re.compile(rb"cf-access-client-(?:id|secret)[\'\\\" ]*[:=][ ]*[\'\\\"]?(?!\\[REDACTED\\])[^\\s\'\\\",}]+", re.I)),',
        '    ("Cloudflare Access header record", re.compile(rb"[\'\\\"]name[\'\\\"]\\s*:\\s*[\'\\\"]cf-access-client-(?:id|secret)[\'\\\"].{0,160}?[\'\\\"]value[\'\\\"]\\s*:\\s*[\'\\\"](?!\\[REDACTED\\])[^\'\\\"]+", re.I | re.S)),',
        '    ("auth storage value", re.compile(rb"(?:access_token|refresh_token|authToken)[\'\\\" ]*[:=][ ]*[\'\\\"]?(?!\\[REDACTED\\])[^\\s\'\\\",}]+", re.I)),',
        '    ("auth storage record", re.compile(rb"[\'\\\"]name[\'\\\"]\\s*:\\s*[\'\\\"](?:access_token|refresh_token|authToken)[\'\\\"].{0,160}?[\'\\\"]value[\'\\\"]\\s*:\\s*[\'\\\"](?!\\[REDACTED\\])[^\'\\\"]+", re.I | re.S)),',
        ']',
        'findings = set()',
        'with zipfile.ZipFile(source, "r") as incoming, zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as outgoing:',
        '    for info in incoming.infolist():',
        '        data = incoming.read(info.filename)',
        '        for value in redactions:',
        '            data = data.replace(value, b"[REDACTED]")',
        '        for label, pattern in signatures:',
        '            if pattern.search(data):',
        '                findings.add((label, info.filename))',
        '        outgoing.writestr(info, data)',
        'if findings:',
        '    summary = ", ".join(f"{label} in {name}" for label, name in sorted(findings))',
        '    raise SystemExit(f"unsafe credential signature(s) remained after trace sanitization: {summary}")',
      ].join('\n'),
      rawTracePath,
      tracePath,
      redactionsPath,
    ], { encoding: 'utf8' });
    if (sanitizer.status !== 0) {
      throw new Error(sanitizer.stderr.trim() || `trace sanitizer exited ${sanitizer.status}`);
    }
  } catch (error) {
    await rm(tracePath, { force: true });
    throw error;
  } finally {
    await rm(redactionsPath, { force: true });
  }
}