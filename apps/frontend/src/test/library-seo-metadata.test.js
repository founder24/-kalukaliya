import { describe, expect, it } from "vitest";

import {
  getLibrarySeoDescription,
  LIBRARY_SEO_DESCRIPTION,
  LIBRARY_SEO_KEYWORDS,
  LIBRARY_SEO_TITLE,
  LIBRARY_SEO_URL,
} from "../lib/librarySeo";
import { libraryLandingSchema } from "../lib/jsonld";

const AFFECTED_ROUTES = ["/library", "/browser"];

describe("public library SEO metadata", () => {
  it.each(AFFECTED_ROUTES)(
    "uses Degree vocabulary for the %s public route",
    (route) => {
      expect(LIBRARY_SEO_TITLE).toContain("Degree");
      expect(LIBRARY_SEO_TITLE).not.toMatch(/assamboard/i);
      expect(LIBRARY_SEO_DESCRIPTION).not.toMatch(/assamboard/i);
      expect(LIBRARY_SEO_KEYWORDS).not.toMatch(/assamboard/i);
      // /browser is an alias, so both public routes intentionally share the
      // library canonical URL while receiving the same public title/meta.
      expect(LIBRARY_SEO_URL).toBe("https://syrabit.ai/library");
      expect(route).toMatch(/^\/(library|browser)$/);
    },
  );

  it("keeps the dynamic description public and free of internal board labels", () => {
    const description = getLibrarySeoDescription(55, 800);
    expect(description).toContain("55 subjects across AHSEC, SEBA, and Degree");
    expect(description).not.toMatch(/assamboard/i);
  });

  it("uses public Degree names in library JSON-LD", () => {
    const schema = libraryLandingSchema(
      [{ id: "subject-1", name: "Physics" }],
      LIBRARY_SEO_URL,
    );
    const serialized = JSON.stringify(schema);
    expect(serialized).toContain("Degree Subject Library");
    expect(serialized).toContain("Degree Study Library");
    expect(serialized).not.toMatch(/assamboard/i);
  });
});