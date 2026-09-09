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

if (!fs.existsSync(root)) {
  console.error(`[check-hashed-assets] missing build directory: ${root}`);
  process.exit(1);
}
if (!fs.existsSync(assetsRoot)) {
  console.error(`[check-hashed-assets] missing asset directory: ${assetsRoot}`);
  process.exit(1);
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
for (const file of htmlFiles) {
  const html = fs.readFileSync(file, "utf8");
  // Pages documents use root-relative Vite asset URLs. Ignore external URLs
  // and query strings while preserving the exact hashed filename.
  for (const match of html.matchAll(/(?:src|href)=["'](\/assets\/[^"'?#]+)["']/g)) {
    const asset = path.join(root, match[1].replace(/^\//, ""));
    if (!fs.existsSync(asset)) missing.add(`${path.relative(root, file)} -> ${match[1]}`);
  }
}

if (missing.size) {
  console.error("[check-hashed-assets] stale/missing HTML asset references:");
  for (const reference of missing) console.error(`  ${reference}`);
  process.exit(1);
}

console.log(`[check-hashed-assets] OK: ${htmlFiles.length} HTML documents reference existing bundles`);