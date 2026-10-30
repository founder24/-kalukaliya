import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { verifySitemapRenderedHtml } from "../../scripts/verify-sitemap-rendered-html.mjs";
import { SITEMAP_ENDPOINTS } from "../../scripts/generate-static-data.mjs";

const tempDirs = [];

function createDist({
  genericShell = false,
  omitPage = false,
  malformedTopic = false,
  chapterRoute = "/ahsec/hs-1st-year/physics/laws-of-motion",
  assameseChapterRoute = null,
  assameseFaqCount = 2,
  assameseHtmlLang = "as",
  assameseDescription = "আধুনিক অসমৰ ইতিহাস আৰু সমাজৰ অসমীয়া পাঠ্যক্ৰম।",
  assameseRobotsNoindex = false,
  englishFaqCount = 5,
  subjectOrigin = "https://syrabit.ai",
  duplicateTopicLoc = null,
} = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "syrabit-sitemap-html-"));
  tempDirs.push(root);
  fs.writeFileSync(
    path.join(root, "sitemap-index.xml"),
    `<?xml version="1.0"?><sitemapindex>
      <sitemap><loc>https://syrabit.ai/sitemap-subjects.xml</loc></sitemap>
      <sitemap><loc>https://syrabit.ai/sitemap-chapters.xml</loc></sitemap>
      <sitemap><loc>https://syrabit.ai/sitemap-topics.xml</loc></sitemap>
      <sitemap><loc>https://syrabit.ai/sitemap-static.xml</loc></sitemap>
    </sitemapindex>`,
  );
  fs.writeFileSync(
    path.join(root, "sitemap-subjects.xml"),
    `<urlset><url><loc>${subjectOrigin}/ahsec/hs-1st-year/physics</loc></url></urlset>`,
  );
  fs.writeFileSync(
    path.join(root, "sitemap-chapters.xml"),
    `<urlset><url><loc>https://syrabit.ai${encodeURI(chapterRoute)}</loc></url>
      ${assameseChapterRoute ? `<url><loc>https://syrabit.ai${encodeURI(assameseChapterRoute)}</loc></url>` : ""}
    </urlset>`,
  );
  fs.writeFileSync(
    path.join(root, "sitemap-topics.xml"),
    `<urlset>
      <url><loc>https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion/topic/inertia</loc></url>
      ${duplicateTopicLoc ? `<url><loc>${duplicateTopicLoc}</loc></url>` : ""}
    </urlset>`,
  );
  fs.writeFileSync(path.join(root, "sitemap-static.xml"), "<urlset></urlset>");

  const shellTitle = "Syrabit.ai — Educational Browser For Assam Board Students";
  const isMakersSyllabus = chapterRoute.includes(
    "/course-syllabus-mdc-01013-makers-of-modern-assam-semester-1",
  );
  const chapterTitle = genericShell
    ? shellTitle
    : isMakersSyllabus
      ? "Makers of Modern Assam — Gauhati University FYUGP Notes"
      : "Laws of Motion — Physics | AHSEC Notes";
  const englishFaq = {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: Array.from({ length: englishFaqCount }, (_, index) => ({
      "@type": "Question",
      name: `Question ${index + 1} about the course?`,
      acceptedAnswer: { "@type": "Answer", text: `A reviewed course answer number ${index + 1}.` },
    })),
  };
  const bilingualAlternates = assameseChapterRoute
    ? `<link rel="alternate" hreflang="en" href="https://syrabit.ai${encodeURI(chapterRoute)}">
       <link rel="alternate" hreflang="as" href="https://syrabit.ai${encodeURI(assameseChapterRoute)}">`
    : "";
  const englishCourseText = Array.from(
    { length: 45 },
    (_, index) => `syllabus notes ${index}`,
  ).join(" ");
  const subjectHtml = `<html><head><title>Physics — AHSEC Notes</title>
    <link rel="canonical" href="https://syrabit.ai/ahsec/hs-1st-year/physics">
    <meta name="syrabit-prerender-path" content="/ahsec/hs-1st-year/physics"></head>
    <body><noscript><h1>${shellTitle}</h1></noscript>
      <div id="root" data-hydrate="subject"><main><h1>Physics</h1></main></div>
    </body></html>`;
  const chapterHtml = `<html lang="en"><head><title>${chapterTitle}</title>
    <meta name="description" content="${isMakersSyllabus ? "Gauhati University FYUGP Semester 1 syllabus for MDC-01013." : "Study notes for Laws of Motion."}">
    <link rel="canonical" href="https://syrabit.ai${encodeURI(chapterRoute)}">
    ${bilingualAlternates}
    ${isMakersSyllabus ? `<script type="application/ld+json" data-pm="1">${JSON.stringify(englishFaq)}</script>` : ""}
    <meta name="syrabit-prerender-path" content="${chapterRoute}"></head>
    <body><noscript><h1>${shellTitle}</h1></noscript>
      <div id="root" data-hydrate="chapter"><main><h1>${genericShell ? shellTitle : isMakersSyllabus ? "Makers of Modern Assam" : "Laws of Motion"}</h1>${isMakersSyllabus ? `<p>${englishCourseText}</p>` : ""}</main></div>
    </body></html>`;
  const topicBody = malformedTopic
    ? `<h1>Inertia</h1><p>Short.</p>`
    : `<h1>Inertia</h1>
        <p>Inertia is the property of matter by which an object resists a change in its state of rest or motion. The amount of inertia depends on the mass of the object. A heavier object has greater inertia than a lighter object. This concept explains why force is needed to change motion.</p>
        <a href="/ahsec/hs-1st-year/physics/laws-of-motion">Read the chapter</a>`;
  const topicHtml = `<html lang="${malformedTopic ? 'as' : 'en'}"><head><title>Inertia — Laws of Motion | Physics Notes</title>
    <link rel="canonical" href="https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion/topic/inertia">
    <meta name="syrabit-prerender-path" content="/ahsec/hs-1st-year/physics/laws-of-motion/topic/inertia"></head>
    <body><noscript><h1>${shellTitle}</h1></noscript>
      <div id="root" data-hydrate="chapter"><main>${topicBody}</main></div>
    </body></html>`;

  fs.mkdirSync(path.join(root, "ahsec/hs-1st-year/physics"), { recursive: true });
  fs.writeFileSync(path.join(root, "ahsec/hs-1st-year/physics/index.html"), subjectHtml);
  if (!omitPage) {
    const chapterOutputDir = path.join(root, ...chapterRoute.split("/").filter(Boolean));
    fs.mkdirSync(chapterOutputDir, { recursive: true });
    fs.writeFileSync(
      path.join(chapterOutputDir, "index.html"),
      chapterHtml,
    );
  }
  if (assameseChapterRoute) {
    const localizedFaq = {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: Array.from({ length: assameseFaqCount }, (_, index) => ({
        "@type": "Question",
        name: `অসমীয়া প্ৰশ্ন ${index + 1}?`,
        acceptedAnswer: { "@type": "Answer", text: `অসমীয়া উত্তৰ ${index + 1} এই পাঠ্যক্ৰমৰ বিষয়ে।` },
      })),
    };
    const localizedTitle = "আধুনিক অসমৰ নিৰ্মাতা সকল";
    const englishCanonical = `https://syrabit.ai${encodeURI(chapterRoute)}`;
    const localizedCanonical = `https://syrabit.ai${encodeURI(assameseChapterRoute)}`;
    const localizedCourseText = Array.from(
      { length: 45 },
      (_, index) => `অসমীয়া পাঠ্যক্ৰমৰ টোকা ${index}`,
    ).join(" ");
    const localizedHtml = `<html lang="${assameseHtmlLang}"><head><title>${localizedTitle} — গুৱাহাটী বিশ্ববিদ্যালয় | Syrabit.ai</title>
      <meta name="description" content="${assameseDescription}">
      ${assameseRobotsNoindex ? `<meta name="robots" content="noindex">` : ""}
      <link rel="canonical" href="${localizedCanonical}">
      <link rel="alternate" hreflang="en" href="${englishCanonical}">
      <link rel="alternate" hreflang="as" href="${localizedCanonical}">
      <meta name="syrabit-prerender-path" content="${assameseChapterRoute}">
      <script type="application/ld+json" data-pm="1">${JSON.stringify(localizedFaq)}</script></head>
      <body><noscript><h1>${shellTitle}</h1></noscript>
        <div id="root" data-hydrate="chapter"><main><h1>${localizedTitle}</h1><p>${assameseDescription} ${localizedCourseText}</p></main></div>
      </body></html>`;
    const localizedOutputDir = path.join(
      root,
      ...assameseChapterRoute.split("/").filter(Boolean),
    );
    fs.mkdirSync(localizedOutputDir, { recursive: true });
    fs.writeFileSync(path.join(localizedOutputDir, "index.html"), localizedHtml);
  }
  fs.mkdirSync(
    path.join(root, "ahsec/hs-1st-year/physics/laws-of-motion/topic/inertia"),
    { recursive: true },
  );
  fs.writeFileSync(
    path.join(root, "ahsec/hs-1st-year/physics/laws-of-motion/topic/inertia/index.html"),
    topicHtml,
  );
  return root;
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
});

describe("sitemap rendered HTML release check", () => {
  it("stages topic sitemap data so topic URLs are covered when published", () => {
    expect(SITEMAP_ENDPOINTS).toContainEqual(
      expect.objectContaining({
        apiPath: "/seo/sitemap-topics.xml",
        file: "sitemap-topics.xml",
      }),
    );
  });

  it("accepts subject and chapter pages with route-specific prerendered content", () => {
    const result = verifySitemapRenderedHtml({ distDir: createDist() });

    expect(result.checked).toBe(3);
    expect(result.errors).toEqual([]);
  });

  it("accepts paired syllabus pages with Assamese metadata and FAQ schema in initial HTML", () => {
    const englishRoute =
      "/degree/1st-semester/makers-of-modern-assam-mdc-01013/course-syllabus-mdc-01013-makers-of-modern-assam-semester-1";
    const result = verifySitemapRenderedHtml({
      distDir: createDist({
        chapterRoute: englishRoute,
        assameseChapterRoute: `/as${englishRoute}`,
        assameseFaqCount: 5,
      }),
    });

    expect(result.checked).toBe(4);
    expect(result.errors).toEqual([]);
  });

  it("rejects an Assamese syllabus snapshot without five matching FAQ answers", () => {
    const englishRoute =
      "/degree/1st-semester/makers-of-modern-assam-mdc-01013/course-syllabus-mdc-01013-makers-of-modern-assam-semester-1";
    const result = verifySitemapRenderedHtml({
      distDir: createDist({
        chapterRoute: englishRoute,
        assameseChapterRoute: `/as${englishRoute}`,
        assameseFaqCount: 4,
      }),
    });

    expect(result.errors.some((error) => error.includes("exactly five FAQPage questions"))).toBe(true);
  });

  it("rejects Assamese sitemap pages with English metadata or the wrong document language", () => {
    const result = verifySitemapRenderedHtml({
      distDir: createDist({
        assameseChapterRoute: "/as/ahsec/hs-1st-year/physics/গতিৰ-নিয়ম",
        assameseHtmlLang: "en",
        assameseDescription: "English description only.",
      }),
    });

    expect(result.errors.some((error) => error.includes("HTML lang must be as"))).toBe(true);
    expect(result.errors.some((error) => error.includes("title, H1, and description"))).toBe(true);
  });

  it("rejects a published bilingual syllabus page marked noindex", () => {
    const englishRoute =
      "/degree/1st-semester/makers-of-modern-assam-mdc-01013/course-syllabus-mdc-01013-makers-of-modern-assam-semester-1";
    const result = verifySitemapRenderedHtml({
      distDir: createDist({
        chapterRoute: englishRoute,
        assameseChapterRoute: `/as${englishRoute}`,
        assameseFaqCount: 5,
        assameseRobotsNoindex: true,
      }),
    });

    expect(result.errors.some((error) => error.includes("must not be marked noindex"))).toBe(true);
  });

  it("rejects a subject sitemap URL on a non-canonical host", () => {
    const result = verifySitemapRenderedHtml({
      distDir: createDist({ subjectOrigin: "https://alternate.example" }),
    });

    expect(
      result.errors.some((error) =>
        error.includes("sitemap page URL origin is not canonical"),
      ),
    ).toBe(true);
  });

  it("rejects duplicate curriculum URLs across content sitemaps", () => {
    const result = verifySitemapRenderedHtml({
      distDir: createDist({
        duplicateTopicLoc: "https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion",
      }),
    });

    expect(
      result.errors.some((error) =>
        error.includes("duplicate curriculum sitemap URL"),
      ),
    ).toBe(true);
  });

  it.each([
    ["spaces", "/ahsec/hs-1st-year/physics/laws of motion"],
    ["non-ASCII characters", "/ahsec/hs-1st-year/physics/গতিৰ নিয়ম"],
  ])("resolves percent-encoded sitemap routes containing %s to normalized output files", (_label, chapterRoute) => {
    const result = verifySitemapRenderedHtml({
      distDir: createDist({ chapterRoute }),
    });

    expect(result.checked).toBe(3);
    expect(result.errors).toEqual([]);
  });

  it("ignores the generic no-script fallback H1 outside the prerendered app root", () => {
    const result = verifySitemapRenderedHtml({ distDir: createDist() });

    expect(result.errors).toEqual([]);
  });

  it("fails when a sitemap-listed chapter has no output file", () => {
    const result = verifySitemapRenderedHtml({
      distDir: createDist({ omitPage: true }),
    });

    expect(
      result.errors.some((error) => error.includes("prerendered file missing")),
    ).toBe(true);
  });

  it("fails when a chapter is rendered as the generic SPA shell", () => {
    const result = verifySitemapRenderedHtml({
      distDir: createDist({ genericShell: true }),
    });

    expect(
      result.errors.some((error) => error.includes("generic SPA shell")),
    ).toBe(true);
  });

  it("fails when a topic snapshot is thin, mislabeled, or lacks its parent chapter link", () => {
    const result = verifySitemapRenderedHtml({
      distDir: createDist({ malformedTopic: true }),
    });

    expect(result.errors.some((error) => error.includes("topic language"))).toBe(true);
    expect(result.errors.some((error) => error.includes("substantial rendered content"))).toBe(true);
    expect(result.errors.some((error) => error.includes("parent chapter"))).toBe(true);
  });
});
