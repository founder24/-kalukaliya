/**
 * Verify that every hashed bundle referenced by a Pages HTML document is
 * present in the same build's dist/assets directory.
 *
 * This is intentionally a build-time check. Cloudflare Pages owns deployment
 * and asset retention; the check prevents publishing a self-inconsistent
 * release and does not introduce a second asset/deployment system.
 */
import fs from "node:fs";
import path from "node:path";

const root = path.resolve(process.argv[2] || "dist");
const assetsRoot = path.join(root, "assets");
const headersPath = path.join(root, "_headers");

if (!fs.existsSync(root)) {
  console.error(`[check-hashed-assets] missing build directory: ${root}`);
  process.exit(1);
}
if (!fs.existsSync(assetsRoot)) {
  console.error(`[check-hashed-assets] missing asset directory: ${assetsRoot}`);
  process.exit(1);
}

// Hashed Link headers are emitted as Early Hints by Cloudflare before the
// navigation document arrives. They are not release-local: an edge can
// replay an older _headers file while serving current HTML, causing the
// browser to request deleted bundles. Keep this contract explicit and
// enforce it in the same build gate as HTML asset references.
if (fs.existsSync(headersPath)) {
  const headers = fs.readFileSync(headersPath, "utf8");
  if (/^\s*Link:\s*<\/assets\/[^>]+>\s*;?\s*[^#]*rel=preload/im.test(headers)) {
    console.error(
      "[check-hashed-assets] _headers must not contain hashed asset preload links",
    );
    process.exit(1);
  }
  if (!/X-Syrabit-Hashed-Preload:\s*disabled/i.test(headers)) {
    console.error(
      "[check-hashed-assets] _headers must declare hashed preload as disabled",
    );
    process.exit(1);
  }
}

const htmlFiles = [];
function walk(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const filename = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(filename);
    else if (entry.name.endsWith(".html")) htmlFiles.push(filename);
  }
}
walk(root);

const missing = new Set();
const referenceFiles = [];
function collectReferences(dir) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const filename = path.join(dir, entry.name);
    if (entry.isDirectory()) collectReferences(filename);
    else if (/\.(html|js|css)$/.test(entry.name)) referenceFiles.push(filename);
  }
}
collectReferences(root);

for (const file of referenceFiles) {
  const html = fs.readFileSync(file, "utf8");
  // HTML documents and bundled JS/CSS use root-relative Vite asset URLs.
  // Ignore external URLs and query strings while preserving exact filenames.
  const references = [
    ...html.matchAll(/(?:src|href)=["'](\/assets\/[^"'?#]+)["']/g),
    ...html.matchAll(/import\(["'](\/assets\/[^"'?#)]+)["']/g),
  ];
  for (const match of references) {
    const reference = match[1];
    const asset = path.join(root, reference.replace(/^\//, ""));
    if (!fs.existsSync(asset)) missing.add(`${path.relative(root, file)} -> ${reference}`);
  }
}

const precachePath = path.join(root, "precache-manifest.json");
if (fs.existsSync(precachePath)) {
  try {
    const precache = JSON.parse(fs.readFileSync(precachePath, "utf8"));
    if (!Array.isArray(precache)) throw new Error("manifest is not an array");
    for (const entry of precache) {
      if (typeof entry !== "string" || !entry.startsWith("/")) {
        missing.add(`precache-manifest.json -> invalid entry ${String(entry)}`);
        continue;
      }
      if (!fs.existsSync(path.join(root, entry.replace(/^\//, "")))) {
        missing.add(`precache-manifest.json -> ${entry}`);
      }
    }
  } catch (error) {
    missing.add(`precache-manifest.json -> ${error.message}`);
  }
}

if (missing.size) {
  console.error("[check-hashed-assets] stale/missing HTML asset references:");
  for (const reference of missing) console.error(`  ${reference}`);
  process.exit(1);
}

console.log(
  `[check-hashed-assets] OK: ${htmlFiles.length} HTML documents and ` +
  `${referenceFiles.length} built files reference existing bundles`,
);