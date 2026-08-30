import { describe, expect, it } from "vitest";
import {
  hasNonEmptyLibraryBundle,
  isStrictCurriculumBuild,
  validateLibrarySnapshot,
  validatePrerenderManifest,
} from "../../scripts/release-guards.mjs";
import {
  fetchWithFallback,
  validateJsonPayload,
} from "../../scripts/generate-static-data.mjs";

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