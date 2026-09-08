/**
 * Post-deploy script: discovers all prerendered URLs from dist/ and submits
 * them to the backend IndexNow endpoint for rapid search engine indexing.
 *
 * Env vars:
 *   INDEXNOW_SECRET       - shared secret for the backend X-IndexNow-Secret header
 *   INDEXNOW_BACKEND_URL  - backend base URL (default: https://api.syrabit.ai)
 *   INDEXNOW_API_KEY      - ownership key used only by --verify-key-file
 */

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(__dirname, "..", "dist");
const SITE_ORIGIN = "https://syrabit.ai";
const BACKEND_URL = process.env.INDEXNOW_BACKEND_URL || "https://api.syrabit.ai";
const SECRET = process.env.INDEXNOW_SECRET || "";
const BATCH_SIZE = 100;

function verifyKeyFile(directory) {
  const key = process.env.INDEXNOW_API_KEY || "";
  if (!key) {
    throw new Error("INDEXNOW_API_KEY is required to verify the ownership file");
  }

  const keyFile = path.join(directory, `${key}.txt`);
  if (!fs.existsSync(keyFile)) {
    throw new Error("The IndexNow ownership file for the configured release key is missing");
  }

  const contents = fs.readFileSync(keyFile, "utf8").replace(/[\r\n]/g, "");
  if (contents !== key) {
    throw new Error("The IndexNow ownership file does not match the configured release key");
  }

  console.log("[indexnow-submit] IndexNow ownership file matches the configured release key");
}

function discoverUrls() {
  const urls = [];

  // Try to read the prerender manifest first
  const manifestPath = path.join(distDir, "prerender-manifest.json");
  if (fs.existsSync(manifestPath)) {
    console.log("[indexnow-submit] Found prerender-manifest.json");
  }

  // Walk dist/ for index.html files (each represents a prerendered route)
  function walk(dir, base = "") {
    if (!fs.existsSync(dir)) return;
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory()) {
        // Skip assets and internal directories
        if (entry.name === "assets" || entry.name.startsWith(".")) continue;
        walk(path.join(dir, entry.name), `${base}/${entry.name}`);
      } else if (entry.name === "index.html" && base) {
        urls.push(`${SITE_ORIGIN}${base}`);
      }
    }
  }

  walk(distDir);
  return urls;
}

async function submitBatch(urls) {
  const endpoint = `${BACKEND_URL}/api/v1/indexnow/submit`;
  const resp = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-IndexNow-Secret": SECRET,
    },
    body: JSON.stringify({ urls }),
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`IndexNow submit failed: HTTP ${resp.status} - ${text}`);
  }
  return resp.json();
}

async function main() {
  if (!SECRET) {
    console.warn("[indexnow-submit] INDEXNOW_SECRET not set; skipping submission");
    process.exit(0);
  }

  const urls = discoverUrls();
  console.log(`[indexnow-submit] Discovered ${urls.length} prerendered URLs`);

  if (urls.length === 0) {
    console.log("[indexnow-submit] No URLs to submit");
    return;
  }

  let totalSubmitted = 0;
  let totalFailed = 0;

  for (let i = 0; i < urls.length; i += BATCH_SIZE) {
    const batch = urls.slice(i, i + BATCH_SIZE);
    try {
      const result = await submitBatch(batch);
      totalSubmitted += result.submitted || 0;
      totalFailed += result.failed || 0;
      console.log(
        `[indexnow-submit] Batch ${Math.floor(i / BATCH_SIZE) + 1}: ` +
          `submitted=${result.submitted}, failed=${result.failed}`,
      );
    } catch (err) {
      console.error(`[indexnow-submit] Batch error: ${err.message}`);
      totalFailed += batch.length;
    }
  }

  console.log(
    `[indexnow-submit] Done: ${totalSubmitted} submitted, ${totalFailed} failed out of ${urls.length} total`,
  );
}

if (process.argv[2] === "--verify-key-file") {
  try {
    const directory = path.resolve(process.argv[3] || distDir);
    verifyKeyFile(directory);
  } catch (err) {
    console.error(`[indexnow-submit] Ownership verification failed: ${err.message}`);
    process.exit(1);
  }
} else {
  main().catch((err) => {
    console.error("[indexnow-submit] Fatal error:", err);
    // Non-fatal: don't fail the deploy if IndexNow is unreachable
    process.exit(0);
  });
}
