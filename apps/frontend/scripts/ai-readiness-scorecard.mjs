import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { verifySitemapRenderedHtml } from "./verify-sitemap-rendered-html.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_DIST_DIR = path.resolve(__dirname, "../dist");
export const RUBRIC_VERSION = "1.0.0";
export const SCORE_WEIGHTS = Object.freeze({
  technicalSeo: { indexability: 12, metadata: 10, pageExperience: 8 },
  geo: { curriculumContext: 8, sourceGrounding: 9, languageQuality: 8 },
  aeo: { directAnswers: 12, answerExtraction: 8, structuredMarkup: 5 },
  crawler: { policyAlignment: 8, identityAuthorization: 6, deliveryTelemetry: 6 },
});

const POLICY_BOTS = [
  ["ChatGPT-User", true],
  ["OAI-SearchBot", true],
  ["PerplexityBot", true],
  ["Perplexity-User", true],
  ["YouBot", true],
  ["GPTBot", false],
  ["ClaudeBot", false],
  ["CCBot", false],
  ["Google-Extended", false],
];

function decodeXml(value) {
  return value
    .replaceAll("&amp;", "&")
    .replaceAll("&lt;", "<")
    .replaceAll("&gt;", ">")
    .replaceAll("&quot;", '"')
    .replaceAll("&apos;", "'");
}

function sitemapLocs(xml) {
  return [...xml.matchAll(/<loc>\s*([\s\S]*?)\s*<\/loc>/gi)]
    .map((match) => decodeXml(match[1].trim()));
}

function parseRobots(text) {
  const groups = [];
  let group = null;
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/#.*/, "").trim();
    if (!line) continue;
    const separator = line.indexOf(":");
    if (separator < 0) continue;
    const key = line.slice(0, separator).trim().toLowerCase();
    const value = line.slice(separator + 1).trim();
    if (key === "user-agent") {
      if (!group || group.rules.length) {
        group = { agents: [], rules: [] };
        groups.push(group);
      }
      group.agents.push(value.toLowerCase());
    } else if (key === "allow" || key === "disallow") {
      if (group) group.rules.push({ directive: key, path: value });
    }
  }
  return groups;
}

function allowsPath(groups, userAgent, pathname = "/") {
  const matching = groups.filter((group) =>
    group.agents.includes(userAgent.toLowerCase()),
  );
  const wildcard = groups.filter((group) => group.agents.includes("*"));
  const applicable = matching.length ? matching : wildcard;
  const rules = applicable.flatMap((group) => group.rules)
    .filter((rule) => rule.path && pathname.startsWith(rule.path))
    .sort((a, b) => b.path.length - a.path.length);
  if (!rules.length) return true;
  const longest = rules.filter((rule) => rule.path.length === rules[0].path.length);
  return longest.some((rule) => rule.directive === "allow");
}

function getApiRobotsText() {
  const source = fs.readFileSync(path.resolve(__dirname, "../../api/src/routes/seo.ts"), "utf8");
  const match = source.match(/const ROBOTS_TXT = `([\s\S]*?)`;/);
  if (!match) throw new Error("Could not find API ROBOTS_TXT source");
  return match[1].replaceAll("${SITE_URL}", "https://syrabit.ai");
}

function safePageFile(distDir, pathname) {
  let decoded;
  try {
    decoded = decodeURIComponent(pathname);
  } catch {
    return null;
  }
  const root = path.resolve(distDir);
  const candidate = path.resolve(root, `.${decoded}`, "index.html");
  return candidate.startsWith(`${root}${path.sep}`) ? candidate : null;
}

function topicDimensions(url) {
  const segments = new URL(url).pathname.split("/").filter(Boolean);
  const isAssamese = segments[0] === "as";
  if (isAssamese) segments.shift();
  return {
    board: segments[0] || "unknown",
    class: segments[1] || "unknown",
    language: isAssamese ? "as" : "en",
  };
}

function stableHash(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

export function selectEditorialSample(urls, limit = 30) {
  const groups = new Map();
  for (const url of urls) {
    const dimensions = topicDimensions(url);
    const key = `${dimensions.board}/${dimensions.class}/${dimensions.language}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(url);
  }
  const orderedGroups = [...groups.entries()].sort(([a], [b]) =>
    stableHash(a).localeCompare(stableHash(b)),
  );
  const selected = [];
  const selectedSet = new Set();
  for (const [, members] of orderedGroups.slice(0, limit)) {
    members.sort((a, b) => stableHash(a).localeCompare(stableHash(b)));
    selected.push(members[0]);
    selectedSet.add(members[0]);
  }
  const remaining = urls.filter((url) => !selectedSet.has(url))
    .sort((a, b) => stableHash(a).localeCompare(stableHash(b)));
  selected.push(...remaining.slice(0, Math.max(0, limit - selected.length)));
  return selected.sort((a, b) => a.localeCompare(b));
}

export function scoreRate(weight, passed, total) {
  if (!Number.isInteger(weight) || weight < 0) throw new Error("weight must be a non-negative integer");
  if (!Number.isInteger(passed) || !Number.isInteger(total) || passed < 0 || total < 0 || passed > total) {
    throw new Error("pass counts must be integers with 0 <= passed <= total");
  }
  return total === 0 ? null : weight * passed / total;
}

function textFromHtml(value) {
  return value.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
}

function hasLearningResourceSchema(html) {
  const scripts = [...html.matchAll(/<script\b[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)];
  for (const [, json] of scripts) {
    try {
      const parsed = JSON.parse(json);
      const nodes = parsed["@graph"] ?? [parsed];
      if (nodes.some((node) => {
        const types = Array.isArray(node["@type"]) ? node["@type"] : [node["@type"]];
        return types.includes("LearningResource");
      })) return true;
    } catch {
      // Invalid JSON-LD is not evidence of a valid LearningResource node.
    }
  }
  return false;
}

function buildPolicyResult(staticRobotsPath) {
  const staticText = fs.readFileSync(staticRobotsPath, "utf8");
  const apiGroups = parseRobots(getApiRobotsText());
  const staticGroups = parseRobots(staticText);
  const checks = POLICY_BOTS.map(([agent, expected]) => {
    const apiAllowed = allowsPath(apiGroups, agent);
    const staticAllowed = allowsPath(staticGroups, agent);
    return {
      agent,
      expectedAllowed: expected,
      apiAllowed,
      staticAllowed,
      pass: apiAllowed === expected && staticAllowed === expected,
    };
  });
  const passed = checks.filter((check) => check.pass).length;
  return {
    status: "scored",
    weight: SCORE_WEIGHTS.crawler.policyAlignment,
    passed,
    total: checks.length,
    points: scoreRate(SCORE_WEIGHTS.crawler.policyAlignment, passed, checks.length),
    checks,
  };
}

export function buildReadinessReport({ distDir = DEFAULT_DIST_DIR, robotsPath } = {}) {
  const absoluteDist = path.resolve(distDir);
  const absoluteRobots = robotsPath || path.resolve(__dirname, "../public/robots.txt");
  const indexPath = path.join(absoluteDist, "sitemap-index.xml");
  if (!fs.existsSync(indexPath)) throw new Error(`Missing release sitemap index: ${indexPath}`);
  const indexXml = fs.readFileSync(indexPath, "utf8");
  const sitemapFiles = sitemapLocs(indexXml)
    .map((location) => path.basename(new URL(location).pathname))
    .filter((filename) => /^sitemap-(?:topics|chapters|subjects)\.xml$/i.test(filename));
  const topicSitemap = path.join(absoluteDist, "sitemap-topics.xml");
  if (!sitemapFiles.includes("sitemap-topics.xml") || !fs.existsSync(topicSitemap)) {
    throw new Error("sitemap-index.xml does not include a built sitemap-topics.xml");
  }
  const topicUrls = [...new Set(sitemapLocs(fs.readFileSync(topicSitemap, "utf8")))];
  const inventory = {};
  for (const url of topicUrls) {
    const { board, class: className, language } = topicDimensions(url);
    const key = `${board}/${className}/${language}`;
    inventory[key] = (inventory[key] || 0) + 1;
  }

  const validation = verifySitemapRenderedHtml({ distDir: absoluteDist });
  const validationErrors = validation.errors;
  const topicValidation = topicUrls.map((url) => {
    const pathname = new URL(url).pathname;
    const issues = validationErrors.filter((error) => error.includes(url) || error.includes(pathname));
    return { url, pass: issues.length === 0, issues };
  });
  const indexabilityPassed = topicValidation.filter((entry) => entry.pass).length;
  const indexability = {
    status: topicUrls.length ? "scored" : "n/a",
    weight: SCORE_WEIGHTS.technicalSeo.indexability,
    passed: indexabilityPassed,
    total: topicUrls.length,
    points: scoreRate(SCORE_WEIGHTS.technicalSeo.indexability, indexabilityPassed, topicUrls.length),
  };

  let metadataPassed = 0;
  const metadataFailures = [];
  for (const url of topicUrls) {
    const pathname = new URL(url).pathname;
    const file = safePageFile(absoluteDist, pathname);
    const checks = { file: Boolean(file && fs.existsSync(file)), title: false, h1: false, canonical: false, language: false, learningResource: false };
    if (checks.file) {
      const html = fs.readFileSync(file, "utf8");
      const title = textFromHtml(html.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i)?.[1] ?? "");
      const h1 = textFromHtml(html.match(/<h1\b[^>]*>([\s\S]*?)<\/h1>/i)?.[1] ?? "");
      const canonical = html.match(/<link\b[^>]*rel=["']canonical["'][^>]*href=["']([^"']+)["']/i)?.[1]
        ?? html.match(/<link\b[^>]*href=["']([^"']+)["'][^>]*rel=["']canonical["']/i)?.[1];
      const lang = html.match(/<html\b[^>]*\blang=["']([^"']+)["']/i)?.[1]?.toLowerCase();
      checks.title = Boolean(title);
      checks.h1 = Boolean(h1);
      checks.canonical = Boolean(canonical && new URL(canonical, url).pathname.replace(/\/+$/, "") === pathname.replace(/\/+$/, ""));
      checks.language = lang === (pathname.startsWith("/as/") ? "as" : "en");
      checks.learningResource = hasLearningResourceSchema(html);
    }
    const pass = Object.values(checks).every(Boolean);
    if (pass) metadataPassed++;
    else metadataFailures.push({ url, checks });
  }
  const metadata = {
    status: topicUrls.length ? "scored" : "n/a",
    weight: SCORE_WEIGHTS.technicalSeo.metadata,
    passed: metadataPassed,
    total: topicUrls.length,
    points: scoreRate(SCORE_WEIGHTS.technicalSeo.metadata, metadataPassed, topicUrls.length),
    failures: metadataFailures,
  };

  const editorialSample = selectEditorialSample(topicUrls);
  const policy = buildPolicyResult(absoluteRobots);
  const missing = (weight, reason) => ({ status: "n/a", weight, points: null, reason });
  const subscores = {
    technicalSeo: {
      indexability,
      metadata,
      pageExperience: missing(SCORE_WEIGHTS.technicalSeo.pageExperience, "Representative field Core Web Vitals data was not supplied."),
    },
    geo: {
      curriculumContext: missing(SCORE_WEIGHTS.geo.curriculumContext, "Two-reviewer GEO rubric results are not supplied."),
      sourceGrounding: missing(SCORE_WEIGHTS.geo.sourceGrounding, "Two-reviewer GEO rubric results are not supplied."),
      languageQuality: missing(SCORE_WEIGHTS.geo.languageQuality, "Two-reviewer bilingual review results are not supplied."),
    },
    aeo: {
      directAnswers: missing(SCORE_WEIGHTS.aeo.directAnswers, "The versioned 25-question answer benchmark has not been reviewed."),
      answerExtraction: missing(SCORE_WEIGHTS.aeo.answerExtraction, "Reference answer and extraction results are not supplied."),
      structuredMarkup: missing(SCORE_WEIGHTS.aeo.structuredMarkup, "Visible-content/schema review results are not supplied."),
    },
    crawler: {
      policyAlignment: policy,
      identityAuthorization: missing(SCORE_WEIGHTS.crawler.identityAuthorization, "Verified Cloudflare identity and publisher Tollbit policy evidence were not supplied."),
      deliveryTelemetry: missing(SCORE_WEIGHTS.crawler.deliveryTelemetry, "A complete, time-bounded verified-agent request dataset with response-content evidence was not supplied."),
    },
  };
  const categories = Object.fromEntries(Object.entries(subscores).map(([key, values]) => {
    const entries = Object.values(values);
    const complete = entries.every((entry) => entry.status === "scored");
    return [key, {
      status: complete ? "scored" : "n/a",
      maximum: entries.reduce((sum, entry) => sum + entry.weight, 0),
      points: complete ? entries.reduce((sum, entry) => sum + entry.points, 0) : null,
      availablePoints: entries.reduce((sum, entry) => sum + (entry.points ?? 0), 0),
      subscores: values,
    }];
  }));
  const allScored = Object.values(categories).every((category) => category.status === "scored");
  const report = {
    rubricVersion: RUBRIC_VERSION,
    generatedAt: new Date().toISOString(),
    scope: "local release artifacts and checked-in policy only; no production requests",
    score: {
      status: allScored ? "scored" : "n/a",
      points: allScored ? Object.values(categories).reduce((sum, category) => sum + category.points, 0) : null,
      maximum: 100,
      reason: allScored ? null : "At least one weighted subscore lacks adequate evidence; composite withheld.",
    },
    categories,
    inventory: {
      sitemapPagesChecked: validation.checked,
      sitemapValidationFailures: validationErrors.length,
      topicUrls: topicUrls.length,
      excluded: null,
      excludedReason: "The built sitemap contains eligible URLs only and does not expose unpublished/excluded candidate counts.",
      byBoardClassLanguage: inventory,
    },
    editorialSample,
    sitemapValidationErrors: validationErrors,
  };
  return report;
}

function main() {
  const outputFlag = process.argv.indexOf("--output");
  const output = outputFlag >= 0 ? process.argv[outputFlag + 1] : null;
  const report = buildReadinessReport();
  const serialized = `${JSON.stringify(report, null, 2)}\n`;
  if (output) {
    const outputPath = path.resolve(output);
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.writeFileSync(outputPath, serialized);
    console.log(`Wrote AI readiness report to ${outputPath}`);
  } else {
    process.stdout.write(serialized);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
