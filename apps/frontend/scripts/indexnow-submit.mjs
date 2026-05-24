#!/usr/bin/env node
/**
 * IndexNow URL Submission Script
 *
 * Reads prerender-manifest.json and submits all prerendered URLs
 * to IndexNow via the backend API endpoint. Designed to run after
 * deployment to Cloudflare Pages.
 *
 * Environment variables:
 *   INDEXNOW_BACKEND_URL  - Backend URL (default: https://syrabit.ai)
 *   INDEXNOW_API_KEY      - Required for submission
 */
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(__dirname, '..', 'dist');
const manifestPath = path.join(distDir, 'prerender-manifest.json');

const BACKEND_URL = (process.env.INDEXNOW_BACKEND_URL || process.env.VITE_BACKEND_URL || 'https://syrabit.ai').replace(/\/$/, '');
const BATCH_SIZE = 100;
const SITE_ORIGIN = 'https://syrabit.ai';

async function main() {
  if (!process.env.INDEXNOW_API_KEY) {
    console.log('[indexnow-submit] INDEXNOW_API_KEY not set, skipping submission');
    return;
  }

  if (!fs.existsSync(manifestPath)) {
    console.warn('[indexnow-submit] prerender-manifest.json not found, skipping');
    return;
  }

  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf-8'));
  const subjectsWritten = manifest?.counts?.subjects_written ?? 0;
  const chaptersWritten = manifest?.counts?.chapters_written ?? 0;
  console.log(`[indexnow-submit] Manifest: ${subjectsWritten} subjects, ${chaptersWritten} chapters prerendered`);

  if (subjectsWritten + chaptersWritten === 0) {
    console.log('[indexnow-submit] No subjects or chapters were prerendered, skipping submission');
    return;
  }

  // Collect all prerendered URLs by walking the dist directory for index.html files
  const urls = [];
  function walkDir(dir, basePath = '') {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory()) {
        walkDir(path.join(dir, entry.name), `${basePath}/${entry.name}`);
      } else if (entry.name === 'index.html' && basePath) {
        urls.push(`${SITE_ORIGIN}${basePath}`);
      }
    }
  }
  walkDir(distDir);

  if (urls.length === 0) {
    console.log('[indexnow-submit] No prerendered URLs found');
    return;
  }

  console.log(`[indexnow-submit] Found ${urls.length} URLs to submit`);

  // Submit in batches
  let totalSubmitted = 0;
  for (let i = 0; i < urls.length; i += BATCH_SIZE) {
    const batch = urls.slice(i, i + BATCH_SIZE);
    const batchNum = Math.floor(i / BATCH_SIZE) + 1;
    const totalBatches = Math.ceil(urls.length / BATCH_SIZE);

    try {
      const resp = await fetch(`${BACKEND_URL}/api/v1/indexnow/submit`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-IndexNow-Secret': process.env.INDEXNOW_API_KEY,
        },
        body: JSON.stringify({ urls: batch }),
      });

      if (resp.ok) {
        const data = await resp.json();
        totalSubmitted += data.submitted || batch.length;
        console.log(`[indexnow-submit] Batch ${batchNum}/${totalBatches}: ${data.status} (${batch.length} URLs)`);
      } else {
        const text = await resp.text();
        console.warn(`[indexnow-submit] Batch ${batchNum}/${totalBatches}: HTTP ${resp.status} - ${text.slice(0, 200)}`);
      }
    } catch (err) {
      console.warn(`[indexnow-submit] Batch ${batchNum}/${totalBatches} failed: ${err.message}`);
    }

    // Small delay between batches to avoid overwhelming the API
    if (i + BATCH_SIZE < urls.length) {
      await new Promise(r => setTimeout(r, 500));
    }
  }

  console.log(`[indexnow-submit] Done. Submitted ${totalSubmitted} URLs total.`);
}

main().catch((err) => {
  console.error('[indexnow-submit] Fatal error:', err);
  // Non-fatal: don't fail the deploy pipeline
  process.exit(0);
});
