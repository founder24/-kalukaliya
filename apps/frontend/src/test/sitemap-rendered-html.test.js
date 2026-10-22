import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { verifySitemapRenderedHtml } from "../../scripts/verify-sitemap-rendered-html.mjs";
import { SITEMAP_ENDPOINTS } from "../../scripts/generate-static-data.mjs";

const tempDirs = [];

function createDist({ genericShell = false, omitPage = false } = {}) {
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
    `<urlset><url><loc>https://syrabit.ai/ahsec/hs-1st-year/physics</loc></url></urlset>`,
  );
  fs.writeFileSync(
    path.join(root, "sitemap-chapters.xml"),
    `<urlset><url><loc>https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion</loc></url></urlset>`,
  );
  fs.writeFileSync(
    path.join(root, "sitemap-topics.xml"),
    `<urlset><url><loc>https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion/topic/inertia</loc></url></urlset>`,
  );
  fs.writeFileSync(path.join(root, "sitemap-static.xml"), "<urlset></urlset>");

  const shellTitle = genericShell
    ? "Syrabit.ai — Educational Browser For Assam Board Students"
    : "Laws of Motion — Physics | AHSEC Notes";
  const subjectHtml = `<html><head><title>Physics — AHSEC Notes</title>
    <link rel="canonical" href="https://syrabit.ai/ahsec/hs-1st-year/physics">
    <meta name="syrabit-prerender-path" content="/ahsec/hs-1st-year/physics"></head>
    <body><main data-hydrate="subject"><h1>Physics</h1></main></body></html>`;
  const chapterHtml = `<html><head><title>${shellTitle}</title>
    <link rel="canonical" href="https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion">
    <meta name="syrabit-prerender-path" content="/ahsec/hs-1st-year/physics/laws-of-motion"></head>
    <body><main data-hydrate="chapter"><h1>${genericShell ? shellTitle : "Laws of Motion"}</h1></main></body></html>`;
  const topicHtml = `<html><head><title>Inertia — Laws of Motion | Physics Notes</title>
    <link rel="canonical" href="https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion/topic/inertia">
    <meta name="syrabit-prerender-path" content="/ahsec/hs-1st-year/physics/laws-of-motion/topic/inertia"></head>
    <body><main data-hydrate="chapter"><h1>Inertia</h1></main></body></html>`;

  fs.mkdirSync(path.join(root, "ahsec/hs-1st-year/physics"), { recursive: true });
  fs.writeFileSync(path.join(root, "ahsec/hs-1st-year/physics/index.html"), subjectHtml);
  if (!omitPage) {
    fs.mkdirSync(path.join(root, "ahsec/hs-1st-year/physics/laws-of-motion"), {
      recursive: true,
    });
    fs.writeFileSync(
      path.join(root, "ahsec/hs-1st-year/physics/laws-of-motion/index.html"),
      chapterHtml,
    );
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
});
