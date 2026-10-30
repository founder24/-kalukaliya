import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { decodeHtmlEntities } from "./_html-text.mjs";
import { decodeSitemapPath } from "./_topic-sitemap-path.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_DIST_DIR = path.resolve(__dirname, "..", "dist");
const CANONICAL_SITE_ORIGIN = "https://syrabit.ai";
const GENERIC_SHELL =
  /Syrabit\.ai\s*(?:—|&mdash;)\s*Educational Browser For Assam Board Students/i;

function decodeXml(value) {
  return decodeHtmlEntities(value);
}

function sitemapLocs(xml) {
  return [...xml.matchAll(/<loc>\s*([\s\S]*?)\s*<\/loc>/gi)].map((match) =>
    decodeXml(match[1].trim()),
  );
}

function normalizedPathname(pathname) {
  if (!pathname || pathname === "/") return "/";
  return decodeSitemapPath(pathname).replace(/\/+$/, "");
}

function attribute(tag, name) {
  const match = tag.match(
    new RegExp(`(?:^|\\s)${name}\\s*=\\s*(["'])(.*?)\\1`, "i"),
  );
  return match ? decodeXml(match[2]) : null;
}

function textContent(fragment) {
  return decodeXml(fragment.replace(/<[^>]*>/g, ""))
    .replace(/\s+/g, " ")
    .trim();
}

function faqQuestionCount(html) {
  let max = 0;
  const collect = (value) => {
    if (Array.isArray(value)) {
      for (const entry of value) collect(entry);
      return;
    }
    if (!value || typeof value !== "object") return;
    const type = value["@type"];
    if (
      (type === "FAQPage" || (Array.isArray(type) && type.includes("FAQPage"))) &&
      Array.isArray(value.mainEntity)
    ) {
      const completeQuestions = value.mainEntity.filter((entry) =>
        entry &&
        typeof entry === "object" &&
        String(entry.name || "").trim() &&
        entry.acceptedAnswer &&
        typeof entry.acceptedAnswer === "object" &&
        String(entry.acceptedAnswer.text || "").trim(),
      );
      max = Math.max(max, completeQuestions.length);
    }
    if (value["@graph"]) collect(value["@graph"]);
  };

  for (const match of html.matchAll(
    /<script\b(?=[^>]*\btype=["']application\/ld\+json["'])[^>]*>([\s\S]*?)<\/script>/gi,
  )) {
    try {
      collect(JSON.parse(match[1]));
    } catch {
      // Invalid JSON-LD is not FAQ evidence.
    }
  }
  return max;
}

function renderedRootContent(html) {
  const rootOpen = html.match(/<div\b(?=[^>]*\bid\s*=\s*(["'])root\1)[^>]*>/i);
  if (!rootOpen || rootOpen.index == null) return null;

  const contentStart = rootOpen.index + rootOpen[0].length;
  const bodyEnd = html.toLowerCase().lastIndexOf("</body>");
  return html.slice(contentStart, bodyEnd >= contentStart ? bodyEnd : undefined);
}

function expectedOutputPath(distDir, pathname) {
  const normalized = normalizedPathname(pathname);
  const segments = normalized.split("/").filter(Boolean);
  const outputPath = path.resolve(distDir, ...segments, "index.html");
  const prefix = `${path.resolve(distDir)}${path.sep}`;
  if (!outputPath.startsWith(prefix) && outputPath !== path.join(distDir, "index.html")) {
    throw new Error(`sitemap path escapes dist/: ${pathname}`);
  }
  return outputPath;
}

function readContentSitemaps(distDir, errors) {
  const indexPath = path.join(distDir, "sitemap-index.xml");
  if (!fs.existsSync(indexPath)) {
    errors.push("dist/sitemap-index.xml is missing");
    return [];
  }

  const indexXml = fs.readFileSync(indexPath, "utf8");
  if (!/<sitemapindex\b/i.test(indexXml)) {
    errors.push("dist/sitemap-index.xml is not a sitemap index");
    return [];
  }

  const sitemapFiles = sitemapLocs(indexXml)
    .map((loc) => {
      try {
        return path.basename(new URL(loc, "https://syrabit.ai").pathname);
      } catch {
        errors.push(`invalid sitemap location in sitemap-index.xml: ${loc}`);
        return null;
      }
    })
    .filter((name) => name && /^sitemap-(?!static(?:-|\.))[^/]+\.xml$/i.test(name));

  if (sitemapFiles.length === 0) {
    errors.push("sitemap-index.xml lists no content sitemaps");
    return [];
  }

  return sitemapFiles.flatMap((filename) => {
    const sitemapPath = path.join(distDir, filename);
    if (!fs.existsSync(sitemapPath)) {
      errors.push(`${filename} is listed in sitemap-index.xml but missing from dist/`);
      return [];
    }

    const xml = fs.readFileSync(sitemapPath, "utf8");
    if (!/<urlset\b/i.test(xml)) {
      errors.push(`${filename} is not a sitemap URL set`);
      return [];
    }

    const urls = sitemapLocs(xml);
    if (/sitemap-chapters\.xml$/i.test(filename) && urls.length === 0) {
      errors.push("sitemap-chapters.xml contains no chapter URLs");
    }

    return urls.map((loc) => ({ filename, loc }));
  });
}

function validateRenderedPage({ distDir, filename, loc }) {
  const issues = [];
  let pageUrl;
  try {
    pageUrl = new URL(loc);
  } catch {
    return [`${filename}: invalid page URL: ${loc}`];
  }

  if (!/^https?:$/.test(pageUrl.protocol)) {
    return [`${filename}: unsupported page URL protocol: ${loc}`];
  }
  if (pageUrl.origin !== CANONICAL_SITE_ORIGIN) {
    issues.push(
      `${loc}: sitemap page URL origin is not canonical (expected ${CANONICAL_SITE_ORIGIN}, got ${pageUrl.origin})`,
    );
  }
  if (pageUrl.search || pageUrl.hash) {
    issues.push(`${loc}: sitemap page URLs must not include a query or fragment`);
  }

  const route = normalizedPathname(pageUrl.pathname);
  let outputPath;
  try {
    outputPath = expectedOutputPath(distDir, route);
  } catch (error) {
    return [...issues, `${loc}: ${error.message}`];
  }
  if (!fs.existsSync(outputPath)) {
    return [...issues, `${loc}: prerendered file missing (${path.relative(distDir, outputPath)})`];
  }

  const html = fs.readFileSync(outputPath, "utf8");
  const headContent = html.match(/<head\b[^>]*>([\s\S]*?)<\/head>/i)?.[1] || "";
  const title = textContent((html.match(/<title\b[^>]*>([\s\S]*?)<\/title>/i) || [])[1] || "");
  const rootContent = renderedRootContent(html);
  const h1 = textContent(
    ((rootContent || "").match(/<h1\b[^>]*>([\s\S]*?)<\/h1>/i) || [])[1] || "",
  );
  const tags = [...html.matchAll(/<(?:meta|link)\b[^>]*>/gi)].map((match) => match[0]);
  const marker = tags.find(
    (tag) =>
      tag.startsWith("<meta") &&
      (attribute(tag, "name") || "").toLowerCase() === "syrabit-prerender-path",
  );
  const markerPath = marker ? attribute(marker, "content") : null;
  const canonicalTag = tags.find(
    (tag) =>
      tag.startsWith("<link") &&
      (attribute(tag, "rel") || "").toLowerCase() === "canonical",
  );
  const canonicalHref = canonicalTag ? attribute(canonicalTag, "href") : null;
  const expectedCanonical = `${CANONICAL_SITE_ORIGIN}${route}`;
  const expectedMarkerPath = route;
  const isTopicPage = /\/topic\/[^/]+$/i.test(route);
  const isChapterSitemap = /sitemap-chapters\.xml$/i.test(filename);
  const isAssameseChapter = isChapterSitemap && route.startsWith("/as/") && !isTopicPage;
  const isMakersSyllabus =
    isChapterSitemap &&
    !isTopicPage &&
    route.includes("/course-syllabus-mdc-01013-makers-of-modern-assam-semester-1");
  const htmlLang = html.match(/<html\b[^>]*\blang=["']([^"']+)["']/i)?.[1]?.toLowerCase();
  const descriptionTag = tags.find(
    (tag) =>
      tag.startsWith("<meta") &&
      (attribute(tag, "name") || "").toLowerCase() === "description",
  );
  const description = descriptionTag ? attribute(descriptionTag, "content") || "" : "";
  const faqCount = faqQuestionCount(headContent);
  const robotsTag = tags.find(
    (tag) =>
      tag.startsWith("<meta") &&
      (attribute(tag, "name") || "").toLowerCase() === "robots",
  );
  const robotsContent = robotsTag ? (attribute(robotsTag, "content") || "").toLowerCase() : "";
  const renderedWords = textContent(rootContent || "").split(/\s+/).filter(Boolean).length;

  if (!title) issues.push(`${loc}: rendered title is missing`);
  if (GENERIC_SHELL.test(title)) issues.push(`${loc}: rendered title is the generic SPA shell`);
  if (!rootContent) issues.push(`${loc}: prerendered app root is missing`);
  if (!h1) issues.push(`${loc}: rendered H1 is missing`);
  if (GENERIC_SHELL.test(h1)) issues.push(`${loc}: rendered H1 is the generic SPA shell`);
  if (!markerPath || normalizedPathname(markerPath) !== expectedMarkerPath) {
    issues.push(
      `${loc}: prerender marker mismatch (expected ${expectedMarkerPath}, got ${markerPath || "none"})`,
    );
  }
  if (!canonicalHref) {
    issues.push(`${loc}: canonical link is missing`);
  } else {
    try {
      const canonical = new URL(canonicalHref, pageUrl.origin);
      const actual = `${canonical.origin}${normalizedPathname(canonical.pathname)}`;
      if (actual !== expectedCanonical) {
        issues.push(`${loc}: canonical mismatch (expected ${expectedCanonical}, got ${actual})`);
      }
    } catch {
      issues.push(`${loc}: canonical URL is invalid (${canonicalHref})`);
    }
  }

  const isSubjectSitemap = /sitemap-subjects\.xml$/i.test(filename);
  const hydrationKind = isSubjectSitemap ? "subject" : "chapter";
  if (!new RegExp(`\\bdata-hydrate=["']${hydrationKind}["']`, "i").test(html)) {
    issues.push(`${loc}: prerendered ${hydrationKind} hydration marker is missing`);
  }
  if (isAssameseChapter) {
    if (htmlLang !== "as") {
      issues.push(`${loc}: Assamese chapter HTML lang must be as (got ${htmlLang || "missing"})`);
    }
    if (
      !/[\u0980-\u09FF]/u.test(title) ||
      !/[\u0980-\u09FF]/u.test(h1) ||
      !/[\u0980-\u09FF]/u.test(description)
    ) {
      issues.push(`${loc}: Assamese chapter title, H1, and description must use Assamese content`);
    }
    if (faqCount < 5) {
      issues.push(`${loc}: Assamese chapter FAQPage JSON-LD needs at least five complete questions`);
    }
    if (robotsContent.includes("noindex")) {
      issues.push(`${loc}: Assamese chapter sitemap page must not be marked noindex`);
    }
  }
  if (isMakersSyllabus) {
    const expectedLanguage = route.startsWith("/as/") ? "as" : "en";
    if (htmlLang !== expectedLanguage) {
      issues.push(`${loc}: Makers of Modern Assam page has the wrong HTML language`);
    }
    if (!description.trim()) {
      issues.push(`${loc}: Makers of Modern Assam page is missing its description metadata`);
    }
    if (
      expectedLanguage === "as" &&
      (!/[\u0980-\u09FF]/u.test(title) ||
        !/[\u0980-\u09FF]/u.test(h1) ||
        !/[\u0980-\u09FF]/u.test(description))
    ) {
      issues.push(`${loc}: Assamese syllabus title, H1, and description must use Assamese content`);
    }
    if (
      expectedLanguage === "en" &&
      (!/Makers of Modern Assam/i.test(title) || !/Makers of Modern Assam/i.test(h1))
    ) {
      issues.push(`${loc}: English syllabus title and H1 must identify Makers of Modern Assam`);
    }
    if (expectedLanguage === "en" && !/\p{Script=Latin}/u.test(description)) {
      issues.push(`${loc}: English syllabus description must use English content`);
    }
    if (renderedWords < 40) {
      issues.push(`${loc}: syllabus HTML must include its substantial chapter notes and answers`);
    }
    if (robotsContent.includes("noindex")) {
      issues.push(`${loc}: published syllabus page must not be marked noindex`);
    }
    if (faqCount !== 5) {
      issues.push(`${loc}: Makers of Modern Assam page must contain exactly five FAQPage questions (got ${faqCount})`);
    }
    const alternateTags = tags.filter((tag) =>
      tag.startsWith("<link") &&
      (attribute(tag, "rel") || "").toLowerCase() === "alternate",
    );
    const alternateByLocale = new Map(
      alternateTags.map((tag) => [
        (attribute(tag, "hreflang") || "").toLowerCase(),
        attribute(tag, "href"),
      ]),
    );
    const expectedEnglishPath = route.startsWith("/as/") ? route.slice(3) : route;
    const expectedAssamesePath = route.startsWith("/as/") ? route : `/as${route}`;
    const localePathsMatch = ["en", "as"].every((locale) => {
      const href = alternateByLocale.get(locale);
      if (!href) return false;
      try {
        return normalizedPathname(new URL(href, pageUrl.origin).pathname) ===
          (locale === "en" ? expectedEnglishPath : expectedAssamesePath);
      } catch {
        return false;
      }
    });
    if (!localePathsMatch) {
      issues.push(`${loc}: bilingual syllabus page must declare en and as alternate links`);
    }
  }
  if (isTopicPage) {
    const topicTitle = h1;
    const expectedLang = route.startsWith("/as/") ? "as" : "en";
    if (htmlLang !== expectedLang) {
      issues.push(`${loc}: topic language must be ${expectedLang} (got ${htmlLang || "missing"})`);
    }
    if (!topicTitle || !title.startsWith(topicTitle)) {
      issues.push(`${loc}: topic title and H1 do not identify the same topic`);
    }
    const visibleText = textContent(rootContent || "");
    if (visibleText.split(/\s+/).filter(Boolean).length < 40) {
      issues.push(`${loc}: topic page does not contain substantial rendered content (minimum 40 words)`);
    }
    const chapterPath = route.replace(/\/topic\/[^/]+$/i, "");
    const chapterLink = [...(rootContent || "").matchAll(/<a\b[^>]*\bhref=["']([^"']+)["'][^>]*>/gi)]
      .some((match) => {
        try {
          return normalizedPathname(new URL(match[1], pageUrl.origin).pathname) === chapterPath;
        } catch {
          return false;
        }
      });
    if (!chapterLink) issues.push(`${loc}: topic page does not link to its parent chapter`);
  }

  return issues;
}

export function verifySitemapRenderedHtml({ distDir = DEFAULT_DIST_DIR } = {}) {
  const errors = [];
  const pages = readContentSitemaps(path.resolve(distDir), errors);
  const seen = new Set();
  let checked = 0;

  for (const page of pages) {
    let key;
    try {
      const url = new URL(page.loc);
      key = `${url.origin}${normalizedPathname(url.pathname)}`;
    } catch {
      errors.push(`${page.filename}: invalid page URL: ${page.loc}`);
      continue;
    }
    if (seen.has(key)) {
      errors.push(`${page.filename}: duplicate curriculum sitemap URL: ${page.loc}`);
      continue;
    }
    seen.add(key);
    checked++;
    errors.push(...validateRenderedPage({ ...page, distDir: path.resolve(distDir) }));
  }

  return { checked, errors };
}

const isDirectInvocation =
  process.argv[1] &&
  path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));

if (isDirectInvocation) {
  const { checked, errors } = verifySitemapRenderedHtml();
  if (errors.length) {
    console.error(
      `[verify-sitemap-rendered-html] FAIL — checked ${checked} sitemap pages:\n` +
        errors.map((error) => `  - ${error}`).join("\n"),
    );
    process.exitCode = 1;
  } else {
    console.log(
      `[verify-sitemap-rendered-html] PASS — ${checked} unique sitemap pages have rendered HTML.`,
    );
  }
}
