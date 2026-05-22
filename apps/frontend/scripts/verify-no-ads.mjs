// Task #529: enforce the "no ads on /chat, /library, /chapter" policy at
// build time. Task #526 placed comment-block warnings at the top of
// these three route files, but a comment is easy to miss in code review.
// This script hard-fails the build if any of the guarded files import
// the AdSlot component (or any module under src/components/ads/),
// turning the policy from advisory into enforced.
//
// To add a new ad-free route, append it to GUARDED_FILES below.

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.resolve(__dirname, "..", "src");

const GUARDED_FILES = [
  "pages/ChatPage.jsx",
  "pages/LibraryPage.jsx",
  "pages/ChapterPage.jsx",
];

// Strip /* … */ block comments and // line comments so the AD POLICY
// banner (which mentions <AdSlot /> by name on purpose) cannot trip
// the scanner. Keeps line numbers intact by replacing comment bodies
// with same-length whitespace / blank lines.
function stripComments(src) {
  let out = src.replace(/\/\*[\s\S]*?\*\//g, (m) =>
    m.replace(/[^\n]/g, " "),
  );
  out = out.replace(/(^|[^:])\/\/[^\n]*/g, (_m, p1) => p1 + "");
  return out;
}

// Find the 1-based line number for a character offset (post-strip text
// has the same line breaks as the original, so this maps back cleanly).
function lineOf(src, offset) {
  let n = 1;
  for (let i = 0; i < offset && i < src.length; i++) {
    if (src.charCodeAt(i) === 10) n++;
  }
  return n;
}

// Patterns that match across newlines so wrapped imports can't slip
// past. Each captures the specifier in group 1.
const PATTERNS = [
  // Static: import … from "…"   (specifier may live on a later line)
  /import\s[\s\S]*?from\s*["']([^"']+)["']/g,
  // Static side-effect: import "…"
  /import\s*["']([^"']+)["']/g,
  // Dynamic: import("…")
  /import\s*\(\s*["']([^"']+)["']\s*\)/g,
  // CommonJS: require("…")
  /require\s*\(\s*["']([^"']+)["']\s*\)/g,
];

function isForbidden(spec) {
  return /(?:^|\/)components\/ads(?:\/|$)/.test(spec);
}

// Task #550: also reject any literal mention of the Google AdSense
// loader URL in guarded files. The new `useAdsenseAutoAds` hook is
// already covered by the import scan (it lives under
// `src/components/ads/`), but a contributor could theoretically inline
// the script tag or a `document.createElement('script'); s.src = "…"`
// snippet directly. This second pass catches that.
const FORBIDDEN_LITERALS = [
  "pagead2.googlesyndication.com/pagead/js/adsbygoogle.js",
];

const violations = [];

for (const rel of GUARDED_FILES) {
  const abs = path.join(srcDir, rel);
  if (!fs.existsSync(abs)) {
    violations.push(`${rel}: file not found (guard list out of date?)`);
    continue;
  }
  const raw = fs.readFileSync(abs, "utf8");
  const code = stripComments(raw);
  for (const re of PATTERNS) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(code)) !== null) {
      const spec = m[1];
      if (isForbidden(spec)) {
        const ln = lineOf(code, m.index);
        violations.push(`${rel}:${ln}: forbidden ad import → ${spec}`);
      }
    }
  }
  for (const literal of FORBIDDEN_LITERALS) {
    let from = 0;
    while (true) {
      const idx = code.indexOf(literal, from);
      if (idx === -1) break;
      const ln = lineOf(code, idx);
      violations.push(`${rel}:${ln}: forbidden ad script URL → ${literal}`);
      from = idx + literal.length;
    }
  }
}

if (violations.length > 0) {
  console.error("\n[verify-no-ads] AD POLICY VIOLATION");
  console.error("These routes are designated AD-FREE (see ADS.md). Importing");
  console.error("the AdSlot component or anything under src/components/ads/");
  console.error("from them is not allowed:\n");
  for (const v of violations) console.error("  • " + v);
  console.error("\nIf the policy has changed, update GUARDED_FILES in");
  console.error("scripts/verify-no-ads.mjs and the routes table in ADS.md.\n");
  process.exit(1);
}

console.log(
  `[verify-no-ads] OK — ${GUARDED_FILES.length} guarded routes contain no ad imports.`,
);
