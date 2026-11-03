// Confirms that every approved Monetag learning route keeps the provider loader
// and that the two user-approved zones remain configured.
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.resolve(__dirname, "..", "src");
const REQUIRED_MONETAG_PAGES = {
  "pages/ChapterPage.jsx": "MonetagPageAds",
  "pages/LearnPage.jsx": "MonetagPageAds",
  "pages/PYQReplicaPage.jsx": "MonetagPageAds",
  "pages/SubjectLandingPage.jsx": "MonetagPageAds",
};
const REQUIRED_MONETAG_TAGS = [
  ["12180636", "https://al5sm.com/tag.min.js"],
  ["12180642", "https://nap5k.com/tag.min.js"],
];

function stripComments(src) {
  let out = src.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, " "));
  out = out.replace(/(^|[^:])\/\/[^\n]*/g, (_m, p1) => p1 + "");
  return out;
}

function keyIsPresent(code, key) {
  const escaped = key.replace(/\./g, "\\.");
  const re = new RegExp('(["\'])' + escaped + '\\1');
  return re.test(code);
}

const violations = [];
for (const [rel, marker] of Object.entries(REQUIRED_MONETAG_PAGES)) {
  const abs = path.join(srcDir, rel);
  if (!fs.existsSync(abs)) {
    violations.push(rel + ": file not found (Monetag route list out of date?)");
    continue;
  }
  const code = stripComments(fs.readFileSync(abs, "utf8"));
  if (!code.includes(marker)) {
    violations.push(rel + ': missing required Monetag component "' + marker + '"');
  }
}

const tagConfigPath = path.join(srcDir, "utils", "monetagAds.js");
if (!fs.existsSync(tagConfigPath)) {
  violations.push("utils/monetagAds.js: file not found");
} else {
  const tagConfig = stripComments(fs.readFileSync(tagConfigPath, "utf8"));
  for (const [zone, scriptUrl] of REQUIRED_MONETAG_TAGS) {
    if (!keyIsPresent(tagConfig, zone)) {
      violations.push('utils/monetagAds.js: missing approved zone "' + zone + '"');
    }
    if (!tagConfig.includes(scriptUrl)) {
      violations.push('utils/monetagAds.js: missing approved script URL "' + scriptUrl + '"');
    }
  }
}

if (violations.length > 0) {
  console.error("\n[verify-required-ads] AD POLICY VIOLATION");
  console.error("Learning routes must include the approved Monetag component and both configured zones:\n");
  for (const violation of violations) console.error("  • " + violation);
  console.error("\nIf policy changes, update REQUIRED_MONETAG_PAGES, REQUIRED_MONETAG_TAGS, and ADS.md.\n");
  process.exit(1);
}

console.log(
  "[verify-required-ads] OK — " + Object.keys(REQUIRED_MONETAG_PAGES).length +
  " learning routes and " + REQUIRED_MONETAG_TAGS.length + " Monetag zones configured.",
);
