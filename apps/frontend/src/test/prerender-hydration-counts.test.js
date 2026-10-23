import { describe, expect, it } from "vitest";
import { countPrerenderHydrationKinds } from "../../scripts/_prerender-hydration-counts.mjs";

describe("prerender hydration counts", () => {
  it("does not count topic pages as chapters when both share the chapter hydrator", () => {
    const pages = [
      {
        rel: "ahsec/hs-1st-year/physics/laws-of-motion/index.html",
        body: '<div id="root" data-hydrate="chapter">',
      },
      {
        rel: "ahsec/hs-1st-year/physics/laws-of-motion/topic/inertia/index.html",
        body: '<div id="root" data-hydrate="chapter">',
      },
      {
        rel: "library/index.html",
        body: '<div id="root" data-hydrate="subject">',
      },
    ];

    expect(countPrerenderHydrationKinds(pages)).toEqual({
      subjects: 1,
      chapters: 1,
    });
  });

  it("matches the release manifest counts when topic outputs are present", () => {
    const chapterPages = Array.from({ length: 194 }, (_, index) => ({
      rel: `ahsec/hs-1st-year/physics/chapter-${index}/index.html`,
      body: '<div id="root" data-hydrate="chapter">',
    }));
    const topicPages = Array.from({ length: 670 }, (_, index) => ({
      rel: `ahsec/hs-1st-year/physics/chapter-${index}/topic/topic-${index}/index.html`,
      body: '<div id="root" data-hydrate="chapter">',
    }));

    expect(
      countPrerenderHydrationKinds([...chapterPages, ...topicPages]).chapters,
    ).toBe(194);
  });
});
