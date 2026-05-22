// Task #545: positive counterpart to verify-no-ads.mjs.
//
// verify-no-ads.mjs hard-fails the build if a guarded route imports
// an ad component. This script does the opposite: it hard-fails the
// build if a *monetised* route file no longer mentions one of the
// placement keys that ad-ops is paying for. A future refactor of
// LearnPage.jsx or PYQReplicaPage.jsx could silently delete a slot
// and we'd lose revenue with no signal in CI — this script closes
// that gap.
//
// Source of truth for the required keys per file is ADS.md (the
// "Routes that DO show ads" table). Keep both in sync.

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.resolve(__dirname, "..", "src");

const REQUIRED = {
  "pages/PYQReplicaPage.jsx": [
    "pyq.topOfContent",
    "pyq.inContent",
    "pyq.endOfContent",
  ],
  "pages/LearnPage.jsx": [
    "learn.topOfContent",
    "learn.inContent",
    "learn.afterPyqs",
    "learn.afterFlashcards",
    "learn.endOfContent",
    "learn.sidebar",
  ],
};

// Strip /* … */ block comments and // line comments so a comment that
// merely *mentions* a placement key (e.g. "TODO: re-add learn.sidebar")
// can never satisfy the check. Whitespace-pad to keep line numbers
// stable for any future error messages.
function stripComments(src) {
  let out = src.replace(/\/\*[\s\S]*?\*\//g, (m) =>
    m.replace(/[^\n]/g, " "),
  );
  out = out.replace(/(^|[^:])\/\/[^\n]*/g, (_m, p1) => p1 + "");
  return out;
}

// A key counts as "present" only if it appears as a string literal
// (single, double, or backtick-quoted). This rules out variable names
// that happen to share the prefix and any stray comment text. The
// backreference `\\1` enforces matching opening and closing quotes,
// so a typo like `"key'` would not satisfy the check.
function keyIsPresent(code, key) {
  // Escape the dot so it's matched literally.
  const escaped = key.replace(/\./g, "\\.");
  const re = new RegExp(`(["'\`])${escaped}\\1`);
  return re.test(code);
}

const violations = [];

for (const [rel, keys] of Object.entries(REQUIRED)) {
  const abs = path.join(srcDir, rel);
  if (!fs.existsSync(abs)) {
    violations.push(`${rel}: file not found (required-ads list out of date?)`);
    continue;
  }
  const code = stripComments(fs.readFileSync(abs, "utf8"));
  for (const key of keys) {
    if (!keyIsPresent(code, key)) {
      violations.push(`${rel}: missing required placement key → "${key}"`);
    }
  }
}

if (violations.length > 0) {
  console.error("\n[verify-required-ads] AD POLICY VIOLATION");
  console.error("These routes are monetised (see ADS.md). Every placement");
  console.error("key listed in the routes table must appear as a string");
  console.error("literal in its owning file:\n");
  for (const v of violations) console.error("  • " + v);
  console.error("\nIf the policy has changed, update REQUIRED in");
  console.error("scripts/verify-required-ads.mjs and the routes table in");
  console.error("ADS.md. Otherwise restore the missing slot — removing it");
  console.error("silently kills ad revenue on that page.\n");
  process.exit(1);
}

const totalKeys = Object.values(REQUIRED).reduce(
  (n, ks) => n + ks.length,
  0,
);
console.log(
  `[verify-required-ads] OK — ${totalKeys} required placement keys present across ${Object.keys(REQUIRED).length} monetised routes.`,
);
