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

function manualSubscore(rows, expectedUrls, weight, field) {
  if (!Array.isArray(rows) || rows.length !== expectedUrls.length || expectedUrls.length === 0) {
    return { status: "n/a", weight, points: null, reason: "Complete reviewed evidence for the fixed sample was not supplied." };
  }
  const byUrl = new Map(rows.map((row) => [row?.url, row]));
  if (byUrl.size !== expectedUrls.length || expectedUrls.some((url) => !byUrl.has(url))) {
    return { status: "n/a", weight, points: null, reason: "Evidence URLs do not exactly match the fixed sample." };
  }
  const reviewed = expectedUrls.map((url) => byUrl.get(url));
  if (reviewed.some((row) => row.reviewers !== 2 || row.reconciled !== true || typeof row[field] !== "boolean")) {
    return { status: "n/a", weight, points: null, reason: "Each result requires two reviewers, reconciled disagreements, and a binary decision." };
  }
  const passed = reviewed.filter((row) => row[field]).length;
  return { status: "scored", weight, passed, total: reviewed.length, points: scoreRate(weight, passed, reviewed.length) };
}

function performanceSubscore(evidence) {
  if (
    !evidence || !Number.isInteger(evidence.sampleSize) || evidence.sampleSize < 1 ||
    !evidence.source || !evidence.window ||
    !Number.isFinite(evidence.lcpP75Ms) || !Number.isFinite(evidence.inpP75Ms) ||
    !Number.isFinite(evidence.clsP75)
  ) {
    return { status: "n/a", weight: SCORE_WEIGHTS.technicalSeo.pageExperience, points: null, reason: "Representative field Core Web Vitals data with source, window, and sample size was not supplied." };
  }
  const checks = [
    { metric: "LCP", value: evidence.lcpP75Ms, threshold: 2500, weight: 3 },
    { metric: "INP", value: evidence.inpP75Ms, threshold: 200, weight: 3 },
    { metric: "CLS", value: evidence.clsP75, threshold: 0.1, weight: 2 },
  ];
  const passed = checks.filter((check) => check.value <= check.threshold).reduce((sum, check) => sum + check.weight, 0);
  return {
    status: "scored",
    weight: SCORE_WEIGHTS.technicalSeo.pageExperience,
    points: passed,
    source: evidence.source,
    window: evidence.window,
    sampleSize: evidence.sampleSize,
    checks: checks.map((check) => ({ ...check, pass: check.value <= check.threshold })),
  };
}

function crawlerEvidenceSubscores(evidence) {
  const weights = SCORE_WEIGHTS.crawler;
  if (
    !evidence || evidence.tollbitPolicyVerified !== true || !evidence.source || !evidence.window ||
    !Number.isInteger(evidence.sampledRequests) || evidence.sampledRequests < 1 ||
    !Number.isInteger(evidence.verifiedAgentRequests) ||
    evidence.verifiedAgentRequests < 0 || evidence.verifiedAgentRequests > evidence.sampledRequests
  ) {
    return {
      identityAuthorization: { status: "n/a", weight: weights.identityAuthorization, points: null, reason: "Verified Cloudflare identity, publisher Tollbit policy, source, window, and request denominator are required." },
      deliveryTelemetry: { status: "n/a", weight: weights.deliveryTelemetry, points: null, reason: "A complete, time-bounded verified-agent request dataset with response-content evidence was not supplied." },
    };
  }
  const identityAuthorization = {
    status: "scored",
    weight: weights.identityAuthorization,
    verifiedAgentRequests: evidence.verifiedAgentRequests,
    sampledRequests: evidence.sampledRequests,
    points: scoreRate(weights.identityAuthorization, evidence.verifiedAgentRequests, evidence.sampledRequests),
    source: evidence.source,
    window: evidence.window,
  };
  const delivery = evidence.delivery;
  if (
    evidence.datasetComplete !== true || evidence.responseContentEvidence !== true ||
    !delivery || !Number.isInteger(delivery.finalHtml2xx) || !Number.isInteger(delivery.contentVerified) ||
    delivery.finalHtml2xx < 0 || delivery.contentVerified < 0 ||
    delivery.finalHtml2xx > evidence.verifiedAgentRequests ||
    delivery.contentVerified > evidence.verifiedAgentRequests
  ) {
    return {
      identityAuthorization,
      deliveryTelemetry: { status: "n/a", weight: weights.deliveryTelemetry, points: null, reason: "Complete dataset and response-content evidence with valid verified-agent outcome counts are required." },
    };
  }
  return {
    identityAuthorization,
    deliveryTelemetry: {
      status: "scored",
      weight: weights.deliveryTelemetry,
      points:
        scoreRate(3, delivery.finalHtml2xx, evidence.verifiedAgentRequests) +
        scoreRate(3, delivery.contentVerified, evidence.verifiedAgentRequests),
      verifiedAgentRequests: evidence.verifiedAgentRequests,
      finalHtml2xx: delivery.finalHtml2xx,
      contentVerified: delivery.contentVerified,
      source: evidence.source,
      window: evidence.window,
    },
  };
}

function textFromHtml(value) {
  return decodeXml(value.replace(/&#(?:0*39|x0*27);/gi, "'"))
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function readJsonLdNodes(html) {
  const scripts = [...html.matchAll(/<script\b[^>]*type=["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi)];
  const nodes = [];
  let invalid = 0;
  for (const [, json] of scripts) {
    try {
      const parsed = JSON.parse(json);
      nodes.push(...(Array.isArray(parsed["@graph"]) ? parsed["@graph"] : [parsed]));
    } catch {
      invalid++;
    }
  }
  return { nodes, invalid };
}

function buildPolicyResult(staticRobotsPath) {
  const staticText = fs.readFileSync(staticRobotsPath, "utf8");
  const apiText = getApiRobotsText();
  const apiGroups = parseRobots(apiText);
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
  const normalizeSignal = (text) =>
    text.match(/^Content-Signal:\s*(.+)$/im)?.[1]?.replace(/\s+/g, "").toLowerCase() ?? null;
  const expectedSignal = "search=yes,ai-input=yes,ai-train=no";
  checks.push({
    agent: "Content-Signal",
    expected: expectedSignal,
    api: normalizeSignal(apiText),
    static: normalizeSignal(staticText),
    pass: normalizeSignal(apiText) === expectedSignal && normalizeSignal(staticText) === expectedSignal,
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

export function buildReadinessReport({ distDir = DEFAULT_DIST_DIR, robotsPath, evidence = null } = {}) {
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
  const editorialSample = selectEditorialSample(topicUrls);
  const answerUrls = selectEditorialSample(topicUrls, 25);
  const answerQuestions = answerUrls.map((url) => {
    const file = safePageFile(absoluteDist, new URL(url).pathname);
    const html = file && fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
    // The first H1 can belong to the shared site shell. Topic prerenders expose
    // the route-specific title in <title> as "topic — chapter"; prefer that
    // source so the fixed AEO prompts actually ask about the curriculum topic.
    const documentTitle = textFromHtml(html.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i)?.[1] ?? "");
    const title = documentTitle.split(/\s+[—–|]\s+/, 1)[0].trim()
      || textFromHtml(html.match(/<h3\b[^>]*>([\s\S]*?)<\/h3>/i)?.[1] ?? "")
      || "topic";
    return {
      url,
      question: `What should a student know about ${title}?`,
      reviewStatus: "pending",
    };
  });
  const inventory = {};
  for (const url of topicUrls) {
    const { board, class: className, language } = topicDimensions(url);
    const key = `${board}/${className}/${language}`;
    inventory[key] ??= { total: null, eligible: 0, excluded: null, failed: 0 };
    inventory[key].eligible++;
  }

  const validation = verifySitemapRenderedHtml({ distDir: absoluteDist });
  const validationErrors = validation.errors;
  const topicValidation = topicUrls.map((url) => {
    const pathname = new URL(url).pathname;
    const issues = validationErrors.filter((error) => error.includes(url) || error.includes(pathname));
    return { url, pass: issues.length === 0, issues };
  });
  for (const entry of topicValidation) {
    const { board, class: className, language } = topicDimensions(entry.url);
    if (!entry.pass) inventory[`${board}/${className}/${language}`].failed++;
  }
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
  const metadataFailureCounts = { file: 0, title: 0, uniqueTitle: 0, h1: 0, description: 0, canonical: 0, language: 0 };
  const metadataRows = [];
  const structuredRows = [];
  for (const url of topicUrls) {
    const pathname = new URL(url).pathname;
    const file = safePageFile(absoluteDist, pathname);
    const checks = { file: Boolean(file && fs.existsSync(file)), title: false, uniqueTitle: false, h1: false, description: false, canonical: false, language: false };
    let h1 = "";
    let html = "";
    if (checks.file) {
      html = fs.readFileSync(file, "utf8");
      const title = textFromHtml(html.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i)?.[1] ?? "");
      h1 = textFromHtml(html.match(/<h1\b[^>]*>([\s\S]*?)<\/h1>/i)?.[1] ?? "");
      const canonical = html.match(/<link\b[^>]*rel=["']canonical["'][^>]*href=["']([^"']+)["']/i)?.[1]
        ?? html.match(/<link\b[^>]*href=["']([^"']+)["'][^>]*rel=["']canonical["']/i)?.[1];
      const lang = html.match(/<html\b[^>]*\blang=["']([^"']+)["']/i)?.[1]?.toLowerCase();
      const description = html.match(/<meta\b[^>]*name=["']description["'][^>]*content=["']([^"']+)["']/i)?.[1]
        ?? html.match(/<meta\b[^>]*content=["']([^"']+)["'][^>]*name=["']description["']/i)?.[1];
      checks.title = Boolean(title);
      checks.h1 = Boolean(h1);
      checks.description = Boolean(description?.trim());
      checks.canonical = Boolean(canonical && new URL(canonical, url).pathname.replace(/\/+$/, "") === pathname.replace(/\/+$/, ""));
      checks.language = lang === (pathname.startsWith("/as/") ? "as" : "en");
      metadataRows.push({ url, title, checks });
      const schema = readJsonLdNodes(html);
      const learningResources = schema.nodes.filter((node) => {
        const types = Array.isArray(node["@type"]) ? node["@type"] : [node["@type"]];
        return types.includes("LearningResource");
      });
      structuredRows.push({
        url,
        pass: schema.invalid === 0 && learningResources.some((node) => String(node.name ?? "").trim() === h1),
        invalidJsonLd: schema.invalid,
        learningResourceCount: learningResources.length,
      });
    } else {
      metadataRows.push({ url, title: "", checks });
      structuredRows.push({ url, pass: false, invalidJsonLd: 0, learningResourceCount: 0 });
    }
  }
  const titleCounts = new Map();
  for (const row of metadataRows) {
    if (row.title) titleCounts.set(row.title.toLowerCase(), (titleCounts.get(row.title.toLowerCase()) ?? 0) + 1);
  }
  for (const row of metadataRows) {
    row.checks.uniqueTitle = Boolean(row.title && titleCounts.get(row.title.toLowerCase()) === 1);
    const pass = Object.values(row.checks).every(Boolean);
    if (pass) metadataPassed++;
    else {
      for (const [key, passed] of Object.entries(row.checks)) if (!passed) metadataFailureCounts[key]++;
      if (metadataFailures.length < 20) metadataFailures.push({ url: row.url, checks: row.checks });
    }
  }
  const metadata = {
    status: topicUrls.length ? "scored" : "n/a",
    weight: SCORE_WEIGHTS.technicalSeo.metadata,
    passed: metadataPassed,
    total: topicUrls.length,
    points: scoreRate(SCORE_WEIGHTS.technicalSeo.metadata, metadataPassed, topicUrls.length),
    failed: topicUrls.length - metadataPassed,
    failureCounts: metadataFailureCounts,
    failures: metadataFailures,
  };
  const structuredPassed = structuredRows.filter((row) => row.pass).length;
  const structuredMarkup = {
    status: topicUrls.length ? "scored" : "n/a",
    weight: SCORE_WEIGHTS.aeo.structuredMarkup,
    passed: structuredPassed,
    total: topicUrls.length,
    points: scoreRate(SCORE_WEIGHTS.aeo.structuredMarkup, structuredPassed, topicUrls.length),
    failed: topicUrls.length - structuredPassed,
    failures: structuredRows.filter((row) => !row.pass).slice(0, 20),
  };

  const policy = buildPolicyResult(absoluteRobots);
  const geoReviews = evidence?.geoReviews;
  const aeoReviews = evidence?.aeoReviews;
  const crawlerScores = crawlerEvidenceSubscores(evidence?.crawler);
  const subscores = {
    technicalSeo: {
      indexability,
      metadata,
      pageExperience: performanceSubscore(evidence?.pageExperience),
    },
    geo: {
      curriculumContext: manualSubscore(geoReviews, editorialSample, SCORE_WEIGHTS.geo.curriculumContext, "curriculumContext"),
      sourceGrounding: manualSubscore(geoReviews, editorialSample, SCORE_WEIGHTS.geo.sourceGrounding, "sourceGrounding"),
      languageQuality: manualSubscore(geoReviews, editorialSample, SCORE_WEIGHTS.geo.languageQuality, "languageQuality"),
    },
    aeo: {
      directAnswers: manualSubscore(aeoReviews, answerUrls, SCORE_WEIGHTS.aeo.directAnswers, "directAnswer"),
      answerExtraction: manualSubscore(aeoReviews, answerUrls, SCORE_WEIGHTS.aeo.answerExtraction, "answerExtraction"),
      structuredMarkup,
    },
    crawler: {
      policyAlignment: policy,
      ...crawlerScores,
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
      excludedReason: "The static release artifact contains eligible URLs only; it does not expose the broader candidate population needed to calculate total or excluded counts.",
      countsReason: "Per-stratum total and excluded counts are null for the same reason; failed counts are sitemap-render validation failures among eligible URLs.",
      byBoardClassLanguage: inventory,
    },
    editorialSample,
    answerQuestions,
    sitemapValidationErrors: validationErrors,
  };
  return report;
}

function main() {
  const outputFlag = process.argv.indexOf("--output");
  const output = outputFlag >= 0 ? process.argv[outputFlag + 1] : null;
  const evidenceFlag = process.argv.indexOf("--evidence");
  const evidencePath = evidenceFlag >= 0 ? process.argv[evidenceFlag + 1] : null;
  const evidence = evidencePath ? JSON.parse(fs.readFileSync(path.resolve(evidencePath), "utf8")) : null;
  if (evidence && evidence.rubricVersion !== RUBRIC_VERSION) {
    throw new Error(`Evidence rubricVersion must be ${RUBRIC_VERSION}`);
  }
  const report = buildReadinessReport({ evidence });
  const serialized = `${JSON.stringify(report, null, 2)}\n`;
  if (output) {
    const outputPath = path.resolve(output);
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.writeFileSync(outputPath, serialized);
    console.log(`Wrote AI readiness report to ${outputPath}`);
  } else {
    const summary = {
      rubricVersion: report.rubricVersion,
      score: report.score,
      categories: Object.fromEntries(Object.entries(report.categories).map(([key, category]) => [
        key,
        { status: category.status, points: category.points, availablePoints: category.availablePoints, maximum: category.maximum },
      ])),
      inventory: report.inventory,
      policyChecks: report.categories.crawler.subscores.policyAlignment.checks,
      editorialSampleCount: report.editorialSample.length,
      answerQuestionCount: report.answerQuestions.length,
      note: "Pass --output <path> to save the complete report, including sample URLs and failure examples.",
    };
    process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main();
}
