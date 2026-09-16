import { describe, expect, it } from "vitest";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import {
  hasNonEmptyLibraryBundle,
  isStrictCurriculumBuild,
  validateChapterPreload,
  validateLibrarySnapshot,
  validatePrerenderManifest,
  validateSubjectPreload,
} from "../../scripts/release-guards.mjs";
import {
  fetchWithFallback,
  JSON_ENDPOINTS,
  validateJsonPayload,
} from "../../scripts/generate-static-data.mjs";
import { injectPrerenderPath } from "../../scripts/_prerender-marker.mjs";
import { rewriteHead } from "../../scripts/prerender-routes.mjs";
import { rewriteHead as rewriteStaticHead } from "../../scripts/prerender-static-routes.mjs";

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

  it.skipIf(process.env.REQUIRE_HYDRATION_BROWSER !== "true")(
    "checks chapter metadata and conditional FAQ structured data in the browser",
    () => {
      const fixtureDir = mkdtempSync(path.join(tmpdir(), "syrabit-seo-"));
      const verifierPath = path.resolve(
        process.cwd(),
        "scripts/verify-hydration.mjs",
      );
      const route = "/fixture-chapter";
      const chapterPath = path.join(fixtureDir, "fixture-chapter", "index.html");
      const chapterHead = (
        canonicalTags,
        structuredData = '<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Article","@id":"https://syrabit.ai/fixture-chapter#article","url":"https://syrabit.ai/fixture-chapter"},{"@type":"BreadcrumbList","itemListElement":[{"item":"https://syrabit.ai/"},{"item":"https://syrabit.ai/library"},{"item":"https://syrabit.ai/fixture-chapter"}]}]}</script>',
        preloadScript = "",
      ) => [
        "<!doctype html>",
        "<html><head>",
        "<title>Fixture Chapter</title>",
        '<meta name="description" content="Fixture chapter notes for browser verification." />',
        canonicalTags,
        '<meta property="og:url" content="https://syrabit.ai/fixture-chapter" />',
        '<meta property="og:title" content="Fixture Chapter" />',
        '<meta property="og:description" content="Fixture chapter notes for browser verification." />',
        '<meta property="og:image:alt" content="Fixture Chapter — Syrabit.ai" />',
        '<meta name="twitter:card" content="summary_large_image" />',
        '<meta name="twitter:title" content="Fixture Chapter" />',
        '<meta name="twitter:description" content="Fixture chapter notes for browser verification." />',
        '<meta name="twitter:image" content="https://syrabit.ai/opengraph.jpg" />',
        '<meta name="twitter:image:alt" content="Fixture Chapter — Syrabit.ai" />',
        structuredData,
        "</head><body>",
        '<div id="root" data-hydrate="chapter">Fixture chapter</div>',
        preloadScript,
        "</body></html>",
      ].join("");
      const faqPreload = `<script>window.__CHAPTER_PRELOAD__=${JSON.stringify({
        data: {
          faq_entries: [
            {
              question: "What does this fixture verify?",
              answer: "It verifies that chapter FAQ structured data reaches the browser.",
            },
            {
              question: "Why does this check matter?",
              answer: "It prevents available chapter questions from losing search markup.",
            },
          ],
        },
      })};</script>`;
      const faqStructuredData =
        '<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Article","url":"https://syrabit.ai/fixture-chapter"},{"@type":"FAQPage","mainEntity":[{"@type":"Question","name":"What does this fixture verify?","acceptedAnswer":{"@type":"Answer","text":"It verifies that chapter FAQ structured data reaches the browser."}},{"@type":"Question","name":"Why does this check matter?","acceptedAnswer":{"@type":"Answer","text":"It prevents available chapter questions from losing search markup."}}]}]}</script>';

      function runVerifier(document) {
        mkdirSync(path.dirname(chapterPath), { recursive: true });
        writeFileSync(chapterPath, document);
        try {
          return execFileSync(process.execPath, [verifierPath], {
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
          return error;
        }
      }

      try {
        const valid = runVerifier(
          chapterHead(
            '<link rel="canonical" href="https://syrabit.ai/fixture-chapter" />',
          ),
        );
        expect(valid.status).toBeUndefined();
        expect(valid).toContain("OK — 1 route(s) checked");

        const validFaq = runVerifier(
          chapterHead(
            '<link rel="canonical" href="https://syrabit.ai/fixture-chapter" />',
            faqStructuredData,
            faqPreload,
          ),
        );
        expect(validFaq.status).toBeUndefined();
        expect(validFaq).toContain("OK — 1 route(s) checked");

        const invalid = runVerifier(
          chapterHead(
            '<link rel="canonical" href="https://syrabit.ai/fixture-chapter" />' +
              '<link rel="canonical" href="https://syrabit.ai/stale-route" />',
            '<script type="application/ld+json">{malformed json</script>',
            faqPreload,
          ),
        );
        expect(invalid.status).toBe(1);
        const output = `${invalid.stdout || ""}\n${invalid.stderr || ""}`;
        expect(output).toContain(
          "[chapter /fixture-chapter] (seo) SEO canonical on /fixture-chapter: expected exactly 1 matching tag, observed 2",
        );
        expect(output).toContain(
          "[chapter /fixture-chapter] (structured-data) Structured data on /fixture-chapter: script 1 is not valid JSON",
        );
        expect(output).toContain(
          "[chapter /fixture-chapter] (structured-data) Structured data on /fixture-chapter: chapter preload contains FAQ entries but no FAQPage JSON-LD object was found",
        );
        expect(output).toContain("across 1 checked route(s)");

        const staleStructuredData = runVerifier(
          chapterHead(
            '<link rel="canonical" href="https://syrabit.ai/fixture-chapter" />',
            '<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Article","url":"https://syrabit.ai/stale-chapter"}]}</script>',
          ),
        );
        expect(staleStructuredData.status).toBe(1);
        const staleOutput = `${staleStructuredData.stdout || ""}\n${staleStructuredData.stderr || ""}`;
        expect(staleOutput).toContain(
          "[chapter /fixture-chapter] (structured-data) Structured data on /fixture-chapter: script 1 has a same-origin URL at @graph[0].url that does not belong to the route; observed https://syrabit.ai/stale-chapter",
        );
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

describe("chapter preload snapshot guards", () => {
  const validPreload = {
    board: "ahsec",
    classSlug: "class-12",
    subjectSlug: "physics",
    chapterSlug: "motion",
    data: {
      chapter_id: "chapter-motion",
      title: "Motion",
      content: "## Motion\n\nNewton's laws of motion.",
    },
  };

  const routeDocument = (payload) =>
    `<div id="root" data-hydrate="chapter">rendered chapter</div>` +
    `<script>window.__CHAPTER_PRELOAD__=${payload};</script>`;

  it("accepts a valid chapter preload from a route document", () => {
    const result = validateChapterPreload(
      "ahsec/class-12/physics/motion/index.html",
      routeDocument(JSON.stringify(validPreload)),
    );

    expect(result.failures).toEqual([]);
    expect(result.preload).toEqual(validPreload);
  });

  it("fails malformed preload JSON with an actionable route", () => {
    const result = validateChapterPreload(
      "ahsec/class-12/physics/motion/index.html",
      routeDocument('{"board":"ahsec",'),
    );

    expect(result.failures).toHaveLength(1);
    expect(result.failures[0]).toContain(
      "ahsec/class-12/physics/motion/index.html: window.__CHAPTER_PRELOAD__ is not valid JSON",
    );
  });

  it("fails when required route or chapter data fields are missing", () => {
    const result = validateChapterPreload(
      "ahsec/class-12/physics/motion/index.html",
      routeDocument(
        JSON.stringify({
          ...validPreload,
          subjectSlug: "",
          data: {
            ...validPreload.data,
            chapter_id: "",
          },
        }),
      ),
    );

    expect(result.failures).toEqual([
      "ahsec/class-12/physics/motion/index.html: window.__CHAPTER_PRELOAD__.subjectSlug must be a non-empty route string",
      "ahsec/class-12/physics/motion/index.html: window.__CHAPTER_PRELOAD__.data.chapter_id must be a non-empty chapter ID",
    ]);
  });

  it.each([
    [
      "missing",
      (({ content, ...data }) => data)(validPreload.data),
    ],
    ["blank", { ...validPreload.data, content: "   " }],
    ["non-string", { ...validPreload.data, content: ["## Motion"] }],
  ])("fails when chapter content is %s", (_label, data) => {
    const route = "ahsec/class-12/physics/motion/index.html";
    const result = validateChapterPreload(
      route,
      routeDocument(JSON.stringify({ ...validPreload, data })),
    );

    expect(result.failures).toEqual([
      `${route}: window.__CHAPTER_PRELOAD__.data.content must be a non-empty string`,
    ]);
  });

  it.each([
    [
      "notes",
      { content_type: "notes", content: "## Notes\n\nStudy material." },
    ],
    [
      "question-paper",
      { content_type: "question_paper", content: "# Question Paper\n\n1. Explain." },
    ],
    [
      "Assamese",
      {
        content_type: "notes",
        content: "## গতি\n\nঅসমীয়া অধ্যায়ৰ বিষয়বস্তু।",
        content_as: "## গতি\n\nঅসমীয়া অধ্যায়ৰ বিষয়বস্তু।",
      },
    ],
  ])("accepts valid %s chapter content snapshots", (_label, data) => {
    const result = validateChapterPreload(
      "ahsec/class-12/physics/motion/index.html",
      routeDocument(
        JSON.stringify({
          ...validPreload,
          data: { ...validPreload.data, ...data },
        }),
      ),
    );

    expect(result.failures).toEqual([]);
  });
});

describe("subject preload snapshot guards", () => {
  const subjectRoute = "ahsec/class-12/physics/index.html";
  const validQueries = [
    {
      key: ["resolve-subject", "ahsec", "class-12", "physics"],
      data: {
        id: "subject-physics",
        name: "Physics",
      },
    },
    {
      key: ["chapters", "subject-physics"],
      data: [{ id: "chapter-motion", slug: "motion", title: "Motion" }],
    },
  ];

  const routeDocument = (payload) =>
    `<div id="root" data-hydrate="subject">rendered subject</div>` +
    `<script>window.__SSR_QUERIES__=${payload};</script>`;

  it("accepts a valid subject query preload", () => {
    const result = validateSubjectPreload(
      subjectRoute,
      routeDocument(JSON.stringify(validQueries)),
    );

    expect(result.failures).toEqual([]);
    expect(result.queries).toEqual(validQueries);
  });

  it("fails malformed subject preload JSON with an actionable route", () => {
    const result = validateSubjectPreload(
      subjectRoute,
      routeDocument('[{"key":["resolve-subject"],'),
    );

    expect(result.failures).toHaveLength(1);
    expect(result.failures[0]).toContain(
      `${subjectRoute}: window.__SSR_QUERIES__ is not valid JSON`,
    );
  });

  it("fails when required subject queries or fields are missing", () => {
    const result = validateSubjectPreload(
      subjectRoute,
      routeDocument(
        JSON.stringify([
          {
            key: ["resolve-subject", "ahsec", "", "physics"],
            data: { name: "Physics" },
          },
          {
            key: ["chapters", ""],
            data: {},
          },
        ]),
      ),
    );

    expect(result.failures).toEqual([
      `${subjectRoute}: resolve-subject query key must include non-empty board, class, and subject slugs`,
      `${subjectRoute}: resolve-subject query subject data must include a non-empty id or _id`,
      `${subjectRoute}: chapters query key must include a non-empty subject ID`,
      `${subjectRoute}: chapters query must contain a chapter array`,
    ]);
  });
});

describe("chapter release-document SEO metadata", () => {
  const chapterRoute =
    "/ahsec/class-12/physics/newtons-laws-of-motion";
  const chapterCanonical = `https://syrabit.ai${chapterRoute}`;
  const chapterTitle =
    "Newton's Laws of Motion — Physics | AHSEC Class 12 Complete Notes";
  const chapterDescription =
    "Complete Newton's laws of motion notes for AHSEC Class 12 Physics students.";
  const otherRoute = "/ahsec/class-11/chemistry/atomic-structure";

  const releaseShell = `<!doctype html>
<html lang="en">
  <head>
    <title>Syrabit.ai</title>
    <meta name="description" content="Generic description" />
    <link rel="canonical" href="https://syrabit.ai${otherRoute}" />
    <link rel="canonical" href="https://syrabit.ai/old-route" />
    <meta property="og:url" content="https://syrabit.ai/old-route" />
    <meta property="og:title" content="Generic title" />
    <meta property="og:description" content="Generic description" />
    <meta property="og:image:alt" content="Generic image alt" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="Generic title" />
    <meta name="twitter:description" content="Generic description" />
    <meta name="twitter:image" content="https://syrabit.ai/opengraph.jpg" />
    <meta name="twitter:image:alt" content="Generic image alt" />
  </head>
  <body><div id="root"></div></body>
</html>`;

  const count = (document, pattern) => document.match(pattern)?.length ?? 0;

  it("keeps one route-specific SEO set after final route injection", () => {
    const document = injectPrerenderPath(
      rewriteHead(releaseShell, {
        title: chapterTitle,
        description: chapterDescription,
        canonical: chapterCanonical,
        ogImageAlt: `${chapterTitle} — Syrabit.ai`,
      }),
      chapterRoute,
    );

    expect(count(document, /<title>/g)).toBe(1);
    expect(document).toContain(`<title>Newton&#39;s Laws of Motion`);
    expect(count(document, /<meta name="description"/g)).toBe(1);
    expect(document).toContain(
      'content="Complete Newton&#39;s laws of motion notes for AHSEC Class 12 Physics students."',
    );

    expect(count(document, /<link rel="canonical"/g)).toBe(1);
    expect(document).toContain(
      `<link rel="canonical" href="${chapterCanonical}" />`,
    );
    expect(count(document, /<link rel="alternate" hreflang="en-IN"/g)).toBe(1);
    expect(document).toContain(
      `<link rel="alternate" hreflang="en-IN" href="${chapterCanonical}" />`,
    );

    const socialMetadata = [
      [
        /<meta property="og:url"/g,
        `<meta property="og:url" content="${chapterCanonical}" />`,
      ],
      [
        /<meta property="og:title"/g,
        'content="Newton&#39;s Laws of Motion — Physics | AHSEC Class 12 Complete Notes"',
      ],
      [
        /<meta property="og:description"/g,
        'content="Complete Newton&#39;s laws of motion notes for AHSEC Class 12 Physics students."',
      ],
      [
        /<meta property="og:image:alt"/g,
        'content="Newton&#39;s Laws of Motion — Physics | AHSEC Class 12 Complete Notes — Syrabit.ai"',
      ],
      [
        /<meta name="twitter:title"/g,
        'content="Newton&#39;s Laws of Motion — Physics | AHSEC Class 12 Complete Notes"',
      ],
      [
        /<meta name="twitter:description"/g,
        'content="Complete Newton&#39;s laws of motion notes for AHSEC Class 12 Physics students."',
      ],
      [
        /<meta name="twitter:image" content=/g,
        'content="https://syrabit.ai/opengraph.jpg"',
      ],
      [
        /<meta name="twitter:image:alt"/g,
        'content="Newton&#39;s Laws of Motion — Physics | AHSEC Class 12 Complete Notes — Syrabit.ai"',
      ],
    ];

    for (const [selector, expected] of socialMetadata) {
      expect(count(document, selector)).toBe(1);
      expect(document).toContain(expected);
    }

    expect(count(document, /name="syrabit-prerender-path"/g)).toBe(1);
    expect(document).toContain(
      `<meta name="syrabit-prerender-path" content="${chapterRoute}" />`,
    );
    expect(document).not.toContain(otherRoute);
    expect(document).not.toContain("Generic description");
    expect(document).not.toContain("Generic title");
  });
});

describe("subject and static release-document SEO metadata", () => {
  const subjectRoute = "/ahsec/class-12/physics";
  const subjectCanonical = `https://syrabit.ai${subjectRoute}`;
  const subjectTitle =
    "Physics — AHSEC Class 12 Notes, MCQs & PYQs | Syrabit.ai";
  const subjectDescription =
    "Complete AHSEC Class 12 Physics notes, MCQs and solved previous-year questions.";
  const staticRoute = "/ahsec/hs-2nd-year";
  const staticCanonical = `https://syrabit.ai${staticRoute}`;
  const staticTitle =
    "AHSEC HS 2nd Year (Class 12) Notes, MCQs & PYQs — Syrabit.ai";
  const staticDescription =
    "Free AHSEC Class 12 notes, MCQs, definitions and solved previous-year questions for all subjects.";

  const releaseShell = `<!doctype html>
<html lang="en">
  <head>
    <title>Stale route title</title>
    <meta name="description" content="Stale route description" />
    <link rel="canonical" href="https://syrabit.ai/stale-route" />
    <meta property="og:url" content="https://syrabit.ai/stale-route" />
    <meta property="og:title" content="Stale route title" />
    <meta property="og:description" content="Stale route description" />
    <meta property="og:image:alt" content="Stale route image" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content="Stale route title" />
    <meta name="twitter:description" content="Stale route description" />
    <meta name="twitter:image" content="https://syrabit.ai/opengraph.jpg" />
    <meta name="twitter:image:alt" content="Stale route image" />
  </head>
  <body><div id="root"></div></body>
</html>`;

  const count = (document, pattern) => document.match(pattern)?.length ?? 0;
  const escapeHtml = (value) =>
    String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  function expectRouteMetadata(document, {
    route,
    canonical,
    title,
    description,
    imageAlt,
  }) {
    const escapedTitle = escapeHtml(title);
    const escapedDescription = escapeHtml(description);
    const escapedImageAlt = escapeHtml(imageAlt);

    expect(count(document, /<title>/g)).toBe(1);
    expect(document).toContain(`<title>${escapedTitle}</title>`);
    expect(count(document, /<meta name="description"/g)).toBe(1);
    expect(document).toContain(`content="${escapedDescription}"`);

    expect(count(document, /<link rel="canonical"/g)).toBe(1);
    expect(document).toContain(`<link rel="canonical" href="${canonical}" />`);
    expect(count(document, /<meta property="og:url"/g)).toBe(1);
    expect(document).toContain(
      `<meta property="og:url" content="${canonical}" />`,
    );
    expect(count(document, /<meta property="og:title"/g)).toBe(1);
    expect(document).toContain(`content="${escapedTitle}"`);
    expect(count(document, /<meta property="og:description"/g)).toBe(1);
    expect(document).toContain(`content="${escapedDescription}"`);
    expect(count(document, /<meta property="og:image:alt"/g)).toBe(1);
    expect(document).toContain(`content="${escapedImageAlt}"`);

    expect(count(document, /<meta name="twitter:title"/g)).toBe(1);
    expect(document).toContain(`content="${escapedTitle}"`);
    expect(count(document, /<meta name="twitter:description"/g)).toBe(1);
    expect(document).toContain(`content="${escapedDescription}"`);
    expect(count(document, /<meta name="twitter:image"/g)).toBe(1);
    expect(document).toContain(
      'content="https://syrabit.ai/opengraph.jpg"',
    );
    expect(count(document, /<meta name="twitter:image:alt"/g)).toBe(1);
    expect(document).toContain(`content="${escapedImageAlt}"`);

    expect(document).toContain(
      `<meta name="syrabit-prerender-path" content="${route}" />`,
    );
    expect(document).not.toContain("Stale route");
    expect(document).not.toContain("stale-route");
  }

  it("keeps subject metadata route-specific through subject prerender assembly", () => {
    const document = injectPrerenderPath(
      rewriteHead(releaseShell, {
        title: subjectTitle,
        description: subjectDescription,
        canonical: subjectCanonical,
        ogImageAlt: `${subjectTitle} — Syrabit.ai`,
      }),
      subjectRoute,
    );

    expectRouteMetadata(document, {
      route: subjectRoute,
      canonical: subjectCanonical,
      title: subjectTitle,
      description: subjectDescription,
      imageAlt: `${subjectTitle} — Syrabit.ai`,
    });
  });

  it("keeps static metadata route-specific through static prerender assembly", () => {
    const document = injectPrerenderPath(
      rewriteStaticHead(releaseShell, {
        title: staticTitle,
        description: staticDescription,
        canonical: staticCanonical,
        ogImageAlt: "AHSEC Class 12 study materials — Syrabit.ai",
      }),
      staticRoute,
    );

    expectRouteMetadata(document, {
      route: staticRoute,
      canonical: staticCanonical,
      title: staticTitle,
      description: staticDescription,
      imageAlt: "AHSEC Class 12 study materials — Syrabit.ai",
    });
  });
});