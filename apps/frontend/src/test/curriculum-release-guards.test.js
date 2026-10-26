import { describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import {
  hasNonEmptyLibraryBundle,
  isStrictCurriculumBuild,
  validateLibrarySnapshot,
  validatePrerenderManifest,
} from "../../scripts/release-guards.mjs";
import {
  fetchWithFallback,
  JSON_ENDPOINTS,
  validateJsonPayload,
} from "../../scripts/generate-static-data.mjs";
import {
  chapterLookupRecovery,
  enumerateSitemapSubjectRoutes,
  selectChapterCandidates,
} from "../../scripts/_prerender-chapter-policy.mjs";
import {
  collectPrerenderOutputPaths,
  includeChapterParentSubjectPaths,
  readChapterSitemapPaths,
  readSubjectSitemapPaths,
  readTopicSitemapPaths,
  validateChapterOutputCoverage,
  validateSubjectOutputCoverage,
  validateTopicOutputCoverage,
} from "../../scripts/_prerender-output-coverage.mjs";

describe("release verifier syntax", () => {
  it("parses without duplicate declarations", () => {
    const verifierPath = path.resolve(process.cwd(), "scripts/verify-all.mjs");

    expect(() =>
      execFileSync(process.execPath, ["--check", verifierPath], {
        stdio: "pipe",
      }),
    ).not.toThrow();
  });
});

describe("sitemap subject route resolution", () => {
  it("keeps the stream-specific subject selected by the published chapter paths", () => {
    const routes = [
      {
        board: "ahsec",
        classSlug: "hs-2nd-year",
        streamSlug: "arts",
        subjectSlug: "economics",
        subject: { id: "arts-economics" },
      },
      {
        board: "ahsec",
        classSlug: "hs-2nd-year",
        streamSlug: "commerce",
        subjectSlug: "economics",
        subject: { id: "commerce-economics" },
      },
    ];
    const chapterPaths = new Set([
      "/ahsec/hs-2nd-year/economics/government-budget-and-the-economy",
      "/ahsec/hs-2nd-year/economics/indian-economic-development",
      "/ahsec/hs-2nd-year/economics/introductory-macroeconomics",
    ]);
    const chapters = [
      { subject_id: "arts-economics", slug: "indian-economic-development" },
      { subject_id: "commerce-economics", slug: "government-budget-and-the-economy" },
      { subject_id: "commerce-economics", slug: "introductory-macroeconomics" },
    ];

    expect(enumerateSitemapSubjectRoutes(chapterPaths, routes, chapters)).toEqual([
      {
        ...routes[1],
        board: "ahsec",
        classSlug: "hs-2nd-year",
        subjectSlug: "economics",
      },
    ]);
  });

  it("selects the canonical lexical stream when duplicate records have identical published chapters", () => {
    const routes = ["bsc", "ba", "bcom"].map((streamSlug) => ({
      board: "ahsec",
      classSlug: "hs-2nd-year",
      streamSlug,
      subjectSlug: "economics",
      subject: { id: `${streamSlug}-economics` },
    }));
    const chapterPaths = new Set([
      "/ahsec/hs-2nd-year/economics/government-budget-and-the-economy",
    ]);
    const chapters = routes.map((route) => ({
      subject_id: route.subject.id,
      slug: "government-budget-and-the-economy",
    }));

    expect(enumerateSitemapSubjectRoutes(chapterPaths, routes, chapters)[0].streamSlug)
      .toBe("ba");
  });

  it("fails rather than guessing when tied stream records have different chapter sets", () => {
    const routes = ["arts", "commerce"].map((streamSlug) => ({
      board: "ahsec",
      classSlug: "hs-2nd-year",
      streamSlug,
      subjectSlug: "economics",
      subject: { id: `${streamSlug}-economics` },
    }));
    const chapterPaths = new Set([
      "/ahsec/hs-2nd-year/economics/government-budget-and-the-economy",
    ]);
    const chapters = [
      { subject_id: "arts-economics", slug: "government-budget-and-the-economy" },
      { subject_id: "arts-economics", slug: "arts-only-chapter" },
      { subject_id: "commerce-economics", slug: "government-budget-and-the-economy" },
      { subject_id: "commerce-economics", slug: "commerce-only-chapter" },
    ];

    expect(() => enumerateSitemapSubjectRoutes(chapterPaths, routes, chapters))
      .toThrow(/ambiguous stream for published subject/);
  });
});

describe("headless hydration release verification", () => {
  it.skipIf(process.env.REQUIRE_HYDRATION_BROWSER !== "true")(
    "fails with the static route and page error when a public shell crashes",
    () => {
      const fixtureDir = mkdtempSync(path.join(tmpdir(), "syrabit-hydration-"));
      const verifierPath = path.resolve(
        process.cwd(),
        "scripts/verify-hydration.mjs",
      );
      const staticRoutes = ["home", "login", "terms"];

      try {
        for (const route of staticRoutes) {
          mkdirSync(path.join(fixtureDir, route), { recursive: true });
          writeFileSync(
            path.join(fixtureDir, route, "index.html"),
            [
              "<!doctype html>",
              "<html><body><div id=\"root\"></div>",
              route === "home"
                ? "<script>throw new Error('fixture static route crashed');</script>"
                : "",
              "</body></html>",
            ].join(""),
          );
        }

        mkdirSync(path.join(fixtureDir, "fixture-subject"), { recursive: true });
        writeFileSync(
          path.join(fixtureDir, "fixture-subject", "index.html"),
          [
            '<div id="root" data-hydrate="subject">subject fixture</div>',
            "<script>console.error('Hydration failed: fixture warning');</script>",
          ].join(""),
        );
        mkdirSync(path.join(fixtureDir, "fixture-subject", "fixture-chapter"), {
          recursive: true,
        });
        writeFileSync(
          path.join(
            fixtureDir,
            "fixture-subject",
            "fixture-chapter",
            "index.html",
          ),
          '<div id="root" data-hydrate="chapter">chapter fixture</div>',
        );

        let result;
        try {
          execFileSync(process.execPath, [verifierPath], {
            cwd: process.cwd(),
            env: {
              ...process.env,
              REQUIRE_HYDRATION_BROWSER: "true",
              VERIFY_HYDRATION_DIST_DIR: fixtureDir,
            },
            encoding: "utf8",
            stdio: ["ignore", "pipe", "pipe"],
          });
        } catch (error) {
          result = error;
        }

        expect(result?.status).toBe(1);
        const output = `${result?.stdout || ""}\n${result?.stderr || ""}`;
        expect(output).toContain("[static /home] (pageerror)");
        expect(output).toContain("fixture static route crashed");
        expect(output).toContain("[subject /fixture-subject] (error)");
        expect(output).toContain("Hydration failed: fixture warning");
        expect(output).toContain("loading subject route /fixture-subject");
        expect(output).toContain(
          "loading chapter route /fixture-subject/fixture-chapter",
        );
        expect(output).toContain("across 5 checked route(s)");
      } finally {
        rmSync(fixtureDir, { recursive: true, force: true });
      }
    },
  );
});

describe("curriculum release strictness", () => {
  it.each([
    [
      "local production offline build",
      {
        NODE_ENV: "production",
        ALLOW_INCOMPLETE_CURRICULUM_BUILD: "true",
      },
      false,
    ],
    [
      "production build without the local opt-out",
      { NODE_ENV: "production" },
      true,
    ],
    [
      "development build",
      {
        NODE_ENV: "development",
        ALLOW_INCOMPLETE_CURRICULUM_BUILD: "true",
      },
      false,
    ],
  ])("%s has the expected strictness", (_name, env, expected) => {
    expect(isStrictCurriculumBuild(env)).toBe(expected);
  });

  it("never lets the incomplete-build opt-out weaken a Cloudflare release", () => {
    expect(
      isStrictCurriculumBuild({
        NODE_ENV: "production",
        CLOUDFLARE_RELEASE_BUILD: "true",
        ALLOW_INCOMPLETE_CURRICULUM_BUILD: "true",
      }),
    ).toBe(true);
  });
});

describe("static curriculum payload guards", () => {
  it("does not make retired subscription plans a release dependency", () => {
    expect(JSON_ENDPOINTS.some(({ apiPath }) => apiPath === "/subscription/plans")).toBe(false);
    expect(JSON_ENDPOINTS.some(({ file }) => file === "plans.json")).toBe(false);
  });

  it("rejects malformed JSON and zero-subject curriculum payloads", () => {
    expect(() => validateJsonPayload("subjects.json", "{not-json")).toThrow(
      "invalid JSON",
    );
    expect(() => validateJsonPayload("subjects.json", "[]")).toThrow(
      "zero subjects",
    );
    expect(() =>
      validateJsonPayload("library-bundle.json", JSON.stringify({ subjects: [] })),
    ).toThrow("zero subjects");
  });

  it("surfaces a failed fetch instead of treating it as an empty release", async () => {
    const fetchImpl = async () => {
      throw new Error("offline test fetch");
    };

    await expect(
      fetchWithFallback("hierarchy/subjects.json", "/content/subjects", {
        fetchImpl,
      }),
    ).rejects.toThrow("offline test fetch");
  });
});

describe("prerender release manifest guards", () => {
  const completeManifest = {
    counts: {
      subjects_selected: 2,
      subjects_written: 2,
      subjects_failed: 0,
      chapters_selected: 4,
      chapters_written: 4,
      chapters_failed: 0,
    },
    budget_exceeded: false,
  };

  it("accepts a complete non-empty manifest", () => {
    const result = validatePrerenderManifest(completeManifest, { strict: true });
    expect(result.failures).toEqual([]);
    expect(result.warnings).toEqual([]);
  });

  it("fails when the prerender manifest is missing", () => {
    const result = validatePrerenderManifest(null, { strict: true });
    expect(result.failures).toEqual([
      "no prerender-manifest.json — prerender step likely soft-failed",
    ]);
  });

  it("fails a zero-entry manifest in release mode", () => {
    const result = validatePrerenderManifest(
      {
        counts: {
          subjects_selected: 0,
          subjects_written: 0,
          subjects_failed: 0,
          chapters_selected: 0,
          chapters_written: 0,
          chapters_failed: 0,
        },
        budget_exceeded: false,
      },
      { strict: true },
    );

    expect(result.failures.join("\n")).toContain(
      "release build requires non-empty subject and chapter prerenders",
    );
    expect(result.failures.join("\n")).toContain(
      "release manifest requires non-zero selected coverage",
    );
  });

  it("rejects malformed manifest counts", () => {
    const result = validatePrerenderManifest(
      {
        counts: { ...completeManifest.counts, chapters_written: "not-a-count" },
        budget_exceeded: false,
      },
      { strict: true },
    );
    expect(result.failures.join("\n")).toContain(
      "counts.chapters_written must be a non-negative integer",
    );
  });
});

describe("published chapter prerender coverage", () => {
  const subjectUrl = "/ahsec/hs-1st-year/physics";
  const published = new Set([`${subjectUrl}/laws-of-motion`]);

  it("excludes stale or unpublished candidates when a sitemap is available", () => {
    const selected = selectChapterCandidates({
      subjectUrl,
      rankedChapters: [
        { slug: "old-chapter" },
        { slug: "laws-of-motion" },
        { slug: "old-chapter" },
      ],
      publishedChapterPaths: published,
      strict: false,
      limit: 10,
    });

    expect(selected.map((chapter) => chapter.slug)).toEqual(["laws-of-motion"]);
  });

  it("keeps the local development fallback when no sitemap exists", () => {
    const selected = selectChapterCandidates({
      subjectUrl,
      rankedChapters: [{ slug: "draft-preview" }],
      publishedChapterPaths: new Set(),
      strict: false,
      limit: 10,
    });

    expect(selected.map((chapter) => chapter.slug)).toEqual(["draft-preview"]);
  });

  it("uses static recovery or fails rather than silently dropping a published chapter", () => {
    const path = `${subjectUrl}/laws-of-motion`;
    expect(chapterLookupRecovery(path, published, true)).toBe("use-static-fallback");
    expect(() => chapterLookupRecovery(path, published, false)).toThrow(
      "refusing to emit a shell instead of a chapter snapshot",
    );
    expect(chapterLookupRecovery(`${subjectUrl}/draft`, published, false)).toBe(
      "skip-unpublished",
    );
    expect(
      chapterLookupRecovery(`${subjectUrl}/draft`, new Set(), true, true),
    ).toBe("use-static-fallback");
  });
});

describe("chapter output coverage", () => {
  const chapter = "/ahsec/hs-1st-year/physics/laws-of-motion";
  const topic = `${chapter}/topic/inertia`;

  it("counts chapter routes separately from topic pages using the same hydrate marker", () => {
    const pages = [
      { rel: `${chapter.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
      { rel: `${topic.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
    ];
    const expectedPaths = readChapterSitemapPaths(
      `<urlset><url><loc>https://syrabit.ai${chapter}</loc></url></urlset>`,
    );

    expect(collectPrerenderOutputPaths(pages).chapters).toEqual(
      new Set([chapter]),
    );
    expect(collectPrerenderOutputPaths(pages).topics).toEqual(
      new Set([topic]),
    );
    expect(
      validateChapterOutputCoverage({
        pages,
        expectedPaths,
        manifestCount: 1,
        strict: true,
      }),
    ).toEqual({ chaptersOnDisk: 1, failures: [] });
    expect(
      validateTopicOutputCoverage({
        pages,
        expectedPaths: readTopicSitemapPaths(
          `<urlset><url><loc>https://syrabit.ai${topic}</loc></url></urlset>`,
        ),
        strict: true,
      }),
    ).toEqual({ topicsOnDisk: 1, failures: [] });
  });

  it.each([
    ["spaces", "/ahsec/hs-1st-year/physics/laws of motion"],
    ["non-ASCII characters", "/ahsec/hs-1st-year/physics/গতিৰ নিয়ম"],
  ])("matches sitemap and generated output routes containing %s", (_label, route) => {
    const pages = [
      { rel: `${route.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
    ];
    const expectedPaths = readChapterSitemapPaths(
      `<urlset><url><loc>https://syrabit.ai${encodeURI(route)}</loc></url></urlset>`,
    );

    expect(expectedPaths).toEqual(new Set([route]));
    expect(collectPrerenderOutputPaths(pages).chapters).toEqual(expectedPaths);
    expect(
      validateChapterOutputCoverage({
        pages,
        expectedPaths,
        manifestCount: 1,
        strict: true,
      }),
    ).toEqual({ chaptersOnDisk: 1, failures: [] });
  });

  it("rejects stale chapter snapshots outside the chapter sitemap", () => {
    const stale = "/ahsec/hs-1st-year/physics/old-chapter";
    const pages = [
      { rel: `${chapter.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
      { rel: `${stale.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
      { rel: `${topic.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
    ];

    const result = validateChapterOutputCoverage({
      pages,
      expectedPaths: new Set([chapter]),
      manifestCount: 1,
      strict: true,
    });

    expect(result.chaptersOnDisk).toBe(2);
    expect(result.failures.join("\n")).toContain(
      "chapter output paths differ from sitemap",
    );
  });

  it("fails strict release coverage when the chapter sitemap is missing", () => {
    const result = validateChapterOutputCoverage({
      pages: [
        { rel: `${chapter.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
      ],
      expectedPaths: null,
      manifestCount: 1,
      strict: true,
    });

    expect(result.failures).toContain(
      "required chapter sitemap missing: dist/sitemap-chapters.xml",
    );
  });

  it("keeps missing chapter sitemap coverage optional outside strict releases", () => {
    const result = validateChapterOutputCoverage({
      pages: [
        { rel: `${chapter.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
      ],
      expectedPaths: null,
      manifestCount: 1,
      strict: false,
    });

    expect(result.failures).toEqual([]);
  });

  it("rejects an unpublished topic snapshot outside the topic sitemap", () => {
    const stale = `${chapter}/topic/unpublished-topic`;
    const pages = [
      { rel: `${chapter.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
      { rel: `${topic.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
      { rel: `${stale.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
    ];
    const expectedPaths = readTopicSitemapPaths(
      `<urlset><url><loc>https://syrabit.ai${topic}</loc></url></urlset>`,
    );

    const result = validateTopicOutputCoverage({
      pages,
      expectedPaths,
      strict: true,
    });

    expect(result.topicsOnDisk).toBe(2);
    expect(result.failures).toEqual([
      `topic output paths differ from sitemap: missing=0; unexpected=1 (${stale})`,
    ]);
  });

  it("reports topic pages missing from the topic sitemap output", () => {
    const pages = [
      { rel: `${chapter.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
      { rel: `${topic.slice(1)}/index.html`, body: '<div data-hydrate="chapter">' },
    ];

    const result = validateTopicOutputCoverage({
      pages,
      expectedPaths: new Set([topic, `${chapter}/topic/another-topic`]),
      strict: true,
    });

    expect(result.failures).toEqual([
      `topic output paths differ from sitemap: missing=1 (${chapter}/topic/another-topic); unexpected=0`,
    ]);
  });
});

describe("subject output coverage", () => {
  const subject = "/ahsec/hs-1st-year/physics";
  const stale = "/ahsec/hs-2nd-year/chemistry";
  const pages = [
    { rel: `${subject.slice(1)}/index.html`, body: '<div data-hydrate="subject">' },
  ];

  it("compares exact subject routes with the sitemap in strict releases", () => {
    const expectedPaths = readSubjectSitemapPaths(
      `<urlset><url><loc>https://syrabit.ai${subject}</loc></url></urlset>`,
    );

    expect(expectedPaths).toEqual(new Set([subject]));
    expect(
      validateSubjectOutputCoverage({ pages, expectedPaths, strict: true }),
    ).toEqual({ subjectsOnDisk: 1, failures: [] });
  });

  it("allows a subject parent required by a published chapter URL", () => {
    const chapter = `${stale}/published-chapter`;
    const expectedPaths = includeChapterParentSubjectPaths(
      new Set([subject]),
      new Set([chapter]),
    );
    const pagesWithParent = [
      ...pages,
      { rel: `${stale.slice(1)}/index.html`, body: '<div data-hydrate="subject">' },
    ];

    expect(expectedPaths).toEqual(new Set([subject, stale]));
    expect(
      validateSubjectOutputCoverage({
        pages: pagesWithParent,
        expectedPaths,
        strict: true,
      }),
    ).toEqual({ subjectsOnDisk: 2, failures: [] });
  });

  it("rejects subject routes missing from the sitemap or output", () => {
    const missingOutput = validateSubjectOutputCoverage({
      pages: [],
      expectedPaths: new Set([subject]),
      strict: true,
    });
    const unexpectedOutput = validateSubjectOutputCoverage({
      pages: [
        ...pages,
        { rel: `${stale.slice(1)}/index.html`, body: '<div data-hydrate="subject">' },
      ],
      expectedPaths: new Set([subject]),
      strict: true,
    });

    expect(missingOutput.failures).toEqual([
      `subject output paths differ from sitemap: missing=1 (${subject}); unexpected=0`,
    ]);
    expect(unexpectedOutput.failures).toEqual([
      `subject output paths differ from sitemap: missing=0; unexpected=1 (${stale})`,
    ]);
  });

  it("requires the subject sitemap only for strict releases", () => {
    expect(
      validateSubjectOutputCoverage({
        pages,
        expectedPaths: null,
        strict: true,
      }).failures,
    ).toEqual(["required subject sitemap missing: dist/sitemap-subjects.xml"]);
    expect(
      validateSubjectOutputCoverage({
        pages,
        expectedPaths: null,
        strict: false,
      }).failures,
    ).toEqual([]);
  });

  it("keeps non-strict subject route mismatches non-blocking", () => {
    expect(
      validateSubjectOutputCoverage({
        pages,
        expectedPaths: new Set([stale]),
        strict: false,
      }),
    ).toEqual({ subjectsOnDisk: 1, failures: [] });
  });
});

describe("library and browser snapshot guards", () => {
  it.each(["library/index.html", "browser/index.html"])(
    "rejects an empty inlined bundle in %s",
    (route) => {
      const html =
        '<div id="root" data-hydrate="library">rendered shell</div>' +
        `<script>window.__LIBRARY_BUNDLE__=${JSON.stringify({ subjects: [] })};` +
        "window.__SSR_QUERIES__={}</script>";

      const result = validateLibrarySnapshot(route, html, {
        strict: true,
      });
      expect(result.failures).toEqual([
        `${route}: inlined library bundle contains zero subjects`,
      ]);
    },
  );

  it("rejects a missing inlined bundle in a strict build", () => {
    const result = validateLibrarySnapshot(
      "library/index.html",
      '<div id="root" data-hydrate="library">rendered shell</div>',
      { strict: true },
    );
    expect(result.failures).toEqual([
      "library/index.html: missing inlined library bundle payload",
    ]);
  });

  it("keeps the shared non-empty bundle predicate strict", () => {
    expect(hasNonEmptyLibraryBundle({ subjects: [{ id: "subject-1" }] })).toBe(
      true,
    );
    expect(hasNonEmptyLibraryBundle({ subjects: [] })).toBe(false);
    expect(hasNonEmptyLibraryBundle(null)).toBe(false);
  });
});