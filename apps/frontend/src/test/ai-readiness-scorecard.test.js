import { afterEach, describe, expect, it } from "vitest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  buildReadinessReport,
  scoreRate,
  selectEditorialSample,
} from "../../scripts/ai-readiness-scorecard.mjs";

const tempDirs = [];

function createDist() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "syrabit-ai-readiness-"));
  tempDirs.push(root);
  const url = "https://syrabit.ai/ahsec/hs-1st-year/physics/laws-of-motion/topic/inertia";
  fs.writeFileSync(
    path.join(root, "sitemap-index.xml"),
    `<sitemapindex><sitemap><loc>https://syrabit.ai/sitemap-topics.xml</loc></sitemap></sitemapindex>`,
  );
  fs.writeFileSync(
    path.join(root, "sitemap-topics.xml"),
    `<urlset><url><loc>${url}</loc></url></urlset>`,
  );
  const output = path.join(root, "ahsec/hs-1st-year/physics/laws-of-motion/topic/inertia");
  fs.mkdirSync(output, { recursive: true });
  const body = [
    "Inertia is the property of matter by which an object resists a change in its state of rest or motion.",
    "The amount of inertia depends on the mass of the object, so a heavier object has greater inertia.",
    "This explanation describes the topic in the context of the AHSEC Class 11 Physics chapter on Laws of Motion.",
  ].join(" ");
  fs.writeFileSync(
    path.join(output, "index.html"),
    `<!doctype html><html lang="en"><head><title>Inertia — Laws of Motion</title><meta name="description" content="Study inertia in AHSEC Class 11 Physics."><link rel="canonical" href="${url}"><meta name="syrabit-prerender-path" content="/ahsec/hs-1st-year/physics/laws-of-motion/topic/inertia"></head><body><div id="root" data-hydrate="chapter"><main><h1>Inertia</h1><p>${body}</p><a href="/ahsec/hs-1st-year/physics/laws-of-motion">Read the chapter</a></main></div><script type="application/ld+json">${JSON.stringify({ "@context": "https://schema.org", "@graph": [{ "@type": "LearningResource", "name": "Inertia" }] })}</script></body></html>`,
  );
  return root;
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
});

describe("AI readiness scorecard", () => {
  it("calculates weighted pass rates without rounding", () => {
    expect(scoreRate(12, 2, 3)).toBe(8);
    expect(scoreRate(8, 0, 0)).toBeNull();
    expect(() => scoreRate(8, 2, 1)).toThrow();
  });

  it("selects a stable sample and represents populated curriculum strata", () => {
    const urls = [
      "https://syrabit.ai/ahsec/hs-1st-year/physics/chapter-a/topic/a",
      "https://syrabit.ai/ahsec/hs-1st-year/chemistry/chapter-b/topic/b",
      "https://syrabit.ai/as/ahsec/hs-1st-year/physics/chapter-a/topic/a",
      "https://syrabit.ai/seba/hs-2nd-year/english/chapter-c/topic/c",
    ];
    const first = selectEditorialSample(urls, 3);
    expect(first).toEqual(selectEditorialSample(urls, 3));
    expect(first).toHaveLength(3);
    expect(new Set(first.map((url) => {
      const parts = new URL(url).pathname.split("/").filter(Boolean);
      const as = parts[0] === "as";
      if (as) parts.shift();
      return `${parts[0]}/${parts[1]}/${as ? "as" : "en"}`;
    })).size).toBe(3);
  });

  it("scores local page checks but withholds the composite when evidence is missing", () => {
    const report = buildReadinessReport({ distDir: createDist() });

    expect(report.rubricVersion).toBeTruthy();
    expect(report.inventory.topicUrls).toBe(1);
    expect(report.inventory.excluded).toBeNull();
    expect(report.categories.technicalSeo.subscores.indexability.points).toBe(12);
    expect(report.categories.technicalSeo.subscores.metadata.points).toBe(10);
    expect(report.categories.aeo.subscores.structuredMarkup.points).toBe(5);
    expect(report.categories.crawler.subscores.policyAlignment.status).toBe("scored");
    expect(report.categories.geo.status).toBe("n/a");
    expect(report.score).toMatchObject({ status: "n/a", points: null, maximum: 100 });
    expect(report.editorialSample).toHaveLength(1);
    expect(report.answerQuestions).toHaveLength(1);
  });

  it("accepts complete rubric-versioned evidence and can produce 100/100", () => {
    const distDir = createDist();
    const baseline = buildReadinessReport({ distDir });
    const evidence = {
      rubricVersion: "1.0.0",
      pageExperience: {
        source: "test field data",
        window: "2026-10-01/2026-10-22",
        sampleSize: 100,
        lcpP75Ms: 2400,
        inpP75Ms: 190,
        clsP75: 0.08,
      },
      geoReviews: baseline.editorialSample.map((url) => ({
        url,
        reviewers: 2,
        reconciled: true,
        curriculumContext: true,
        sourceGrounding: true,
        languageQuality: true,
      })),
      aeoReviews: baseline.answerQuestions.map(({ url }) => ({
        url,
        reviewers: 2,
        reconciled: true,
        directAnswer: true,
        answerExtraction: true,
      })),
      crawler: {
        tollbitPolicyVerified: true,
        source: "verified test fixture",
        window: "2026-10-01/2026-10-22",
        sampledRequests: 10,
        verifiedAgentRequests: 10,
        datasetComplete: true,
        responseContentEvidence: true,
        delivery: { finalHtml2xx: 10, contentVerified: 10 },
      },
    };

    const report = buildReadinessReport({ distDir, evidence });

    expect(report.score).toMatchObject({ status: "scored", points: 100, maximum: 100 });
  });
});
