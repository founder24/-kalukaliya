import { describe, it, expect } from "vitest";
import { _slugToTitle, _resolveSpaRouteMeta } from "../src/index";

describe("_slugToTitle", () => {
  it("title-cases single word", () => {
    expect(_slugToTitle("physics")).toBe("Physics");
  });

  it("title-cases hyphenated slug", () => {
    expect(_slugToTitle("physical-world")).toBe("Physical World");
  });

  it("title-cases multi-word slug", () => {
    expect(_slugToTitle("units-and-measurements")).toBe("Units And Measurements");
  });

  it("handles already-uppercase first letter", () => {
    expect(_slugToTitle("Physics")).toBe("Physics");
  });

  it("handles empty string without throwing", () => {
    expect(_slugToTitle("")).toBe("");
  });

  it("handles single char", () => {
    expect(_slugToTitle("a")).toBe("A");
  });
});

describe("_resolveSpaRouteMeta", () => {
  describe("/notes/class-11/:subject", () => {
    it("returns correct title and description", () => {
      const meta = _resolveSpaRouteMeta("/notes/class-11/physics");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Physics — Class 11 Notes | Syrabit.ai");
      expect(meta!.description).toContain("Class 11");
      expect(meta!.description).toContain("Physics");
    });

    it("handles hyphenated subject", () => {
      const meta = _resolveSpaRouteMeta("/notes/class-11/political-science");
      expect(meta!.title).toBe("Political Science — Class 11 Notes | Syrabit.ai");
    });

    it("strips trailing slash", () => {
      const meta = _resolveSpaRouteMeta("/notes/class-11/physics/");
      expect(meta).not.toBeNull();
      expect(meta!.title).toContain("Physics");
    });
  });

  describe("/notes/class-12/:subject", () => {
    it("returns correct title and description", () => {
      const meta = _resolveSpaRouteMeta("/notes/class-12/chemistry");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Chemistry — Class 12 Notes | Syrabit.ai");
      expect(meta!.description).toContain("Class 12");
      expect(meta!.description).toContain("Chemistry");
    });
  });

  describe("/notes/degree/:sem/:subject", () => {
    it("returns correct title and description", () => {
      const meta = _resolveSpaRouteMeta("/notes/degree/1st-semester/economics");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Economics — 1st Semester Degree Notes | Syrabit.ai");
      expect(meta!.description).toContain("Economics");
      expect(meta!.description).toContain("1st Semester");
    });
  });

  describe("/ahsec/hs-1st-year/:subject", () => {
    it("returns correct title and description", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/hs-1st-year/biology");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Biology — AHSEC HS 1st Year | Syrabit.ai");
      expect(meta!.description).toContain("Biology");
      expect(meta!.description).toContain("AHSEC HS 1st Year");
    });
  });

  describe("/ahsec/hs-2nd-year/:subject", () => {
    it("returns correct title and description", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/hs-2nd-year/mathematics");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Mathematics — AHSEC HS 2nd Year | Syrabit.ai");
      expect(meta!.description).toContain("Mathematics");
      expect(meta!.description).toContain("AHSEC HS 2nd Year");
    });
  });

  describe("/learn/:slug", () => {
    it("returns correct title and description", () => {
      const meta = _resolveSpaRouteMeta("/learn/newtons-laws-of-motion");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Newtons Laws Of Motion — Learn | Syrabit.ai");
      expect(meta!.description).toContain("Newtons Laws Of Motion");
    });

    it("handles single-word slug", () => {
      const meta = _resolveSpaRouteMeta("/learn/photosynthesis");
      expect(meta!.title).toBe("Photosynthesis — Learn | Syrabit.ai");
    });
  });

  describe("non-matching routes", () => {
    it("returns null for homepage", () => {
      expect(_resolveSpaRouteMeta("/")).toBeNull();
    });

    it("returns null for API routes", () => {
      expect(_resolveSpaRouteMeta("/api/notes")).toBeNull();
    });

    it("returns null for /notes without subpath", () => {
      expect(_resolveSpaRouteMeta("/notes")).toBeNull();
    });

    it("returns null for /notes/class-11 without subject", () => {
      expect(_resolveSpaRouteMeta("/notes/class-11")).toBeNull();
    });

    it("returns null for unknown route family", () => {
      expect(_resolveSpaRouteMeta("/pricing")).toBeNull();
      expect(_resolveSpaRouteMeta("/about")).toBeNull();
    });

    it("returns null for /ahsec without year segment", () => {
      expect(_resolveSpaRouteMeta("/ahsec/hs-1st-year")).toBeNull();
    });
  });
});
