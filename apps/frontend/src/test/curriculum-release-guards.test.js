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
} from "../../scripts/release-guards.mjs";
import {
  fetchWithFallback,
  JSON_ENDPOINTS,
  validateJsonPayload,
} from "../../scripts/generate-static-data.mjs";
import { injectPrerenderPath } from "../../scripts/_prerender-marker.mjs";
import { rewriteHead } from "../../scripts/prerender-routes.mjs";

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