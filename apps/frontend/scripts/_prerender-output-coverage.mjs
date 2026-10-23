import { decodeSitemapPath } from "./_topic-sitemap-path.mjs";

function routePathFromOutputRel(rel) {
  const route = rel.replace(/\/index\.html$/, "");
  return `/${decodeSitemapPath(route).replace(/^\/+|\/+$/g, "")}`;
}

export function collectPrerenderOutputPaths(pages) {
  const subjects = new Set();
  const chapters = new Set();
  const topics = new Set();

  for (const page of pages) {
    if (/data-hydrate="subject"/.test(page.body)) {
      subjects.add(routePathFromOutputRel(page.rel));
    }
    if (!/data-hydrate="chapter"/.test(page.body)) continue;

    const route = routePathFromOutputRel(page.rel);
    if (route.split("/").includes("topic")) {
      topics.add(route);
      continue;
    }

    const segments = route.split("/").filter(Boolean);
    // Topic pages use ChapterPage for SSR and hydration too, but have a
    // separate sitemap/output contract from chapter routes.
    if (segments.length === 4) chapters.add(route);
  }

  return { subjects, chapters, topics };
}

function readSitemapPaths(xml, isExpectedRoute) {
  const paths = new Set();
  for (const match of xml.matchAll(/<loc>\s*([\s\S]*?)\s*<\/loc>/gi)) {
    try {
      const pathname = new URL(match[1].trim(), "https://syrabit.ai").pathname;
      const route = `/${decodeSitemapPath(pathname).replace(/^\/+|\/+$/g, "")}`;
      if (isExpectedRoute(route)) paths.add(route);
    } catch {
      // The sitemap validation stage reports malformed locations. Keep this
      // helper limited to collecting paths it can safely compare.
    }
  }
  return paths;
}

export function readChapterSitemapPaths(xml) {
  return readSitemapPaths(
    xml,
    (route) => route.split("/").filter(Boolean).length === 4,
  );
}

export function readTopicSitemapPaths(xml) {
  return readSitemapPaths(xml, (route) => route.split("/").includes("topic"));
}

export function validateChapterOutputCoverage({
  pages,
  expectedPaths,
  manifestCount,
  strict = false,
}) {
  const { chapters } = collectPrerenderOutputPaths(pages);
  const failures = [];

  if (strict && chapters.size !== manifestCount) {
    failures.push(
      `chapter output count mismatch: manifest=${manifestCount}, disk=${chapters.size}`,
    );
  }

  if (strict && expectedPaths) {
    const missing = [...expectedPaths].filter((route) => !chapters.has(route));
    const unexpected = [...chapters].filter((route) => !expectedPaths.has(route));
    if (missing.length || unexpected.length) {
      failures.push(
        `chapter output paths differ from sitemap: missing=${missing.length}` +
          `${missing.length ? ` (${missing.slice(0, 5).join(", ")})` : ""}; ` +
          `unexpected=${unexpected.length}` +
          `${unexpected.length ? ` (${unexpected.slice(0, 5).join(", ")})` : ""}`,
      );
    }
  }

  return { chaptersOnDisk: chapters.size, failures };
}

export function validateTopicOutputCoverage({
  pages,
  expectedPaths,
  strict = false,
}) {
  const { topics } = collectPrerenderOutputPaths(pages);
  const failures = [];

  if (strict && expectedPaths) {
    const missing = [...expectedPaths].filter((route) => !topics.has(route));
    const unexpected = [...topics].filter((route) => !expectedPaths.has(route));
    if (missing.length || unexpected.length) {
      failures.push(
        `topic output paths differ from sitemap: missing=${missing.length}` +
          `${missing.length ? ` (${missing.slice(0, 5).join(", ")})` : ""}; ` +
          `unexpected=${unexpected.length}` +
          `${unexpected.length ? ` (${unexpected.slice(0, 5).join(", ")})` : ""}`,
      );
    }
  }

  return { topicsOnDisk: topics.size, failures };
}
