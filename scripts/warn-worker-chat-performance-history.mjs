#!/usr/bin/env node

import process from 'node:process';
import { readdir, readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

import { recurringOutlierWarnings } from './worker-chat-performance-gate.mjs';

const currentPath = process.argv[2] || 'chat-performance-report.json';
const historyDirectory = process.argv[3] || 'chat-performance-history';
const recentReportLimit = Number(process.env.CHAT_PERFORMANCE_HISTORY_RUNS || '5');

if (!Number.isSafeInteger(recentReportLimit) || recentReportLimit < 1) {
  throw new Error('CHAT_PERFORMANCE_HISTORY_RUNS must be a positive integer');
}

async function readReport(path) {
  return JSON.parse(await readFile(path, 'utf8'));
}

const currentReport = await readReport(currentPath);
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