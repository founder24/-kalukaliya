#!/usr/bin/env node

import process from 'node:process';
import { randomUUID } from 'node:crypto';
import { readdir, readFile, rename, unlink, writeFile } from 'node:fs/promises';
import { basename, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { recurringOutlierWarnings } from './worker-chat-performance-gate.mjs';

async function readReport(path) {
  return JSON.parse(await readFile(path, 'utf8'));
}

async function detectMalformedNotificationState(path) {
  try {
    JSON.parse(await readFile(path, 'utf8'));
    return false;
  } catch (error) {
    if (error?.code === 'ENOENT') return false;
    if (!(error instanceof SyntaxError)) throw error;
    console.error(
      `::warning title=Chat performance notification state recovery::Malformed existing notification state `
      + `at ${path}: ${error.message}. Rebuilding it from the current report comparison.`,
    );
    return true;
  }
}

export async function atomicWriteJson(path, value, operations = {}) {
  const write = operations.writeFile ?? writeFile;
  const move = operations.rename ?? rename;
  const remove = operations.unlink ?? unlink;
  const temporaryPath = resolve(
    dirname(path),
    `.${basename(path)}.${process.pid}.${randomUUID()}.tmp`,
  );

  try {
    await write(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, { flag: 'wx' });
    await move(temporaryPath, path);
  } catch (error) {
    try {
      await remove(temporaryPath);
    } catch (cleanupError) {
      if (cleanupError?.code !== 'ENOENT') {
        error.cleanupError = cleanupError;
      }
    }
    throw error;
  }
}

async function main() {
  const currentPath = process.argv[2] || 'chat-performance-report.json';
  const historyDirectory = process.argv[3] || 'chat-performance-history';
  const recentReportLimit = Number(process.env.CHAT_PERFORMANCE_HISTORY_RUNS || '5');
  const notificationMinimumRuns = Number(process.env.CHAT_PERFORMANCE_NOTIFICATION_RUNS || '3');
  const notificationStatePath =
    process.env.CHAT_PERFORMANCE_NOTIFICATION_STATE_PATH || 'chat-performance-notification-state.json';

  if (!Number.isSafeInteger(recentReportLimit) || recentReportLimit < 1) {
    throw new Error('CHAT_PERFORMANCE_HISTORY_RUNS must be a positive integer');
  }
  if (!Number.isSafeInteger(notificationMinimumRuns) || notificationMinimumRuns < 2) {
    throw new Error('CHAT_PERFORMANCE_NOTIFICATION_RUNS must be an integer of at least 2');
  }
  if (notificationMinimumRuns > recentReportLimit + 1) {
    throw new Error('CHAT_PERFORMANCE_NOTIFICATION_RUNS cannot exceed the available report window');
  }

  const currentReport = await readReport(currentPath);
  await detectMalformedNotificationState(notificationStatePath);
  let historyFiles = [];
  try {
    historyFiles = (await readdir(historyDirectory, { recursive: true }))
      .filter(path => path.endsWith('.json'))
      .sort()
      .slice(-recentReportLimit);
  } catch (error) {
    if (error?.code !== 'ENOENT') throw error;
  }

  const historicalReports = [];
  for (const path of historyFiles) {
    try {
      historicalReports.push(await readReport(resolve(historyDirectory, path)));
    } catch (error) {
      console.error(`::notice title=Chat performance history::Skipped unreadable report ${path}: ${error.message}`);
    }
  }

  const reports = [currentReport, ...historicalReports].slice(0, recentReportLimit + 1);
  const warnings = recurringOutlierWarnings(reports);
  const notificationWarnings = recurringOutlierWarnings(reports, notificationMinimumRuns);
  const comparisonComplete = reports.length >= notificationMinimumRuns;
  const indeterminateRoutes = Object.entries(currentReport?.summary ?? {})
    .filter(([, summary]) => summary?.passed !== true)
    .map(([route]) => route);
  await atomicWriteJson(notificationStatePath, {
    comparison_complete: comparisonComplete,
    minimum_runs: notificationMinimumRuns,
    compared_runs: reports.length,
    indeterminate_routes: indeterminateRoutes,
    active_warnings: notificationWarnings,
  });

  if (!comparisonComplete) {
    console.error(
      `::notice title=Chat performance notification::Only ${reports.length}/${notificationMinimumRuns} `
      + 'required reports were available; maintainer notification state will not be changed.',
    );
  }
  if (warnings.length === 0) {
    console.error(
      `::notice title=Chat performance history::Compared ${reports.length} deployment timing report(s); `
      + 'no route has repeated tolerated first-token outliers.',
    );
  } else {
    for (const warning of warnings) {
      console.error(
        `::warning title=Recurring ${warning.label} latency outliers::${warning.message}. `
        + `Affected-run maxima: ${warning.maxima_ms.join(', ')} ms. `
        + `The 3000 ms strict-majority release gate is unchanged.`,
      );
    }
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main();
}