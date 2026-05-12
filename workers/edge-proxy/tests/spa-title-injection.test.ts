import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { _slugToTitle, _resolveSpaRouteMeta, _injectSpaTitleForBot, _OG_IMAGE_BASE } from "../src/index";

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

    it("matches nested chapter path (minimum-segment prefix)", () => {
      const meta = _resolveSpaRouteMeta("/notes/class-11/physics/chapter-1");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Physics — Class 11 Notes | Syrabit.ai");
    });

    it("matches deeply nested path", () => {
      const meta = _resolveSpaRouteMeta("/notes/class-11/physics/chapter-1/notes");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Physics — Class 11 Notes | Syrabit.ai");
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

    it("matches nested chapter path — acceptance example from task spec", () => {
      const meta = _resolveSpaRouteMeta("/notes/class-12/chemistry/some-chapter");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Chemistry — Class 12 Notes | Syrabit.ai");
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

    it("matches nested chapter path under degree", () => {
      const meta = _resolveSpaRouteMeta("/notes/degree/1st-semester/economics/chapter-2");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Economics — 1st Semester Degree Notes | Syrabit.ai");
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

    it("matches nested path", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/hs-1st-year/biology/cell-structure");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Biology — AHSEC HS 1st Year | Syrabit.ai");
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

    it("matches nested path", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/hs-2nd-year/mathematics/integration");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Mathematics — AHSEC HS 2nd Year | Syrabit.ai");
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

    it("matches nested section path", () => {
      const meta = _resolveSpaRouteMeta("/learn/photosynthesis/light-reactions");
      expect(meta).not.toBeNull();
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

    it("returns null for unknown route family", () => {
      expect(_resolveSpaRouteMeta("/pricing")).toBeNull();
      expect(_resolveSpaRouteMeta("/about")).toBeNull();
    });

    it("returns null for /ahsec/hs-1st-year without subject", () => {
      expect(_resolveSpaRouteMeta("/ahsec/hs-1st-year")).toBeNull();
    });

    it("returns null for /notes/degree with only 3 segments (missing subject)", () => {
      expect(_resolveSpaRouteMeta("/notes/degree/1st-semester")).toBeNull();
    });
  });

  describe("board hub pages", () => {
    it("/ahsec returns board hub title", () => {
      const meta = _resolveSpaRouteMeta("/ahsec");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("AHSEC Study Materials | Syrabit.ai");
      expect(meta!.description).toContain("AHSEC");
    });

    it("/ahsec/ (trailing slash) returns board hub title", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("AHSEC Study Materials | Syrabit.ai");
    });

    it("/seba returns board hub title", () => {
      const meta = _resolveSpaRouteMeta("/seba");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("SEBA Study Materials | Syrabit.ai");
      expect(meta!.description).toContain("SEBA");
    });

    it("/degree returns board hub title", () => {
      const meta = _resolveSpaRouteMeta("/degree");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Degree Study Materials | Syrabit.ai");
      expect(meta!.description).toContain("Degree");
    });

    it("/ahsec/class-11 returns board+class hub title", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-11");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("AHSEC Class 11 Study Materials | Syrabit.ai");
      expect(meta!.description).toContain("Class 11");
    });

    it("/ahsec/class-12 returns board+class hub title", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-12");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("AHSEC Class 12 Study Materials | Syrabit.ai");
      expect(meta!.description).toContain("Class 12");
    });

    it("/ahsec/class-11/ (trailing slash) returns board+class hub title", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-11/");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("AHSEC Class 11 Study Materials | Syrabit.ai");
    });

    it("does not match /ahsec/class-11/physics (subject present — falls through to subject handler)", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-11/physics");
      expect(meta).toBeNull();
    });
  });

  describe("notes hub pages", () => {
    it("/notes returns notes hub title", () => {
      const meta = _resolveSpaRouteMeta("/notes");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Study Notes | Syrabit.ai");
      expect(meta!.description).toContain("AHSEC");
    });

    it("/notes/ (trailing slash) returns notes hub title", () => {
      const meta = _resolveSpaRouteMeta("/notes/");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Study Notes | Syrabit.ai");
    });

    it("/notes/class-11 returns class-11 notes hub title", () => {
      const meta = _resolveSpaRouteMeta("/notes/class-11");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Class 11 Notes | Syrabit.ai");
      expect(meta!.description).toContain("Class 11");
    });

    it("/notes/class-12 returns class-12 notes hub title", () => {
      const meta = _resolveSpaRouteMeta("/notes/class-12");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Class 12 Notes | Syrabit.ai");
      expect(meta!.description).toContain("Class 12");
    });

    it("/notes/class-11/ (trailing slash) returns class-11 notes hub title", () => {
      const meta = _resolveSpaRouteMeta("/notes/class-11/");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Class 11 Notes | Syrabit.ai");
    });

    it("does not match /notes/class-11/physics (subject present — falls through to subject handler)", () => {
      const meta = _resolveSpaRouteMeta("/notes/class-11/physics");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Physics — Class 11 Notes | Syrabit.ai");
    });
  });

  describe("_injectSpaTitleForBot — behaviour guard", () => {
    it("returns original response unchanged for non-bot flag", async () => {
      const html = '<html><head><title>Syrabit.ai</title><meta name="description" content="old"></head></html>';
      const original = new Response(html, {
        headers: { "Content-Type": "text/html; charset=utf-8" },
      });
      const out = _injectSpaTitleForBot(original, "/notes/class-12/chemistry", false);
      const body = await out.text();
      expect(body).toContain("Syrabit.ai");
      expect(body).not.toContain("Chemistry — Class 12 Notes");
    });

    it("returns original response unchanged for non-HTML content-type", () => {
      const jsonResp = new Response('{"key":"value"}', {
        headers: { "Content-Type": "application/json" },
      });
      const out = _injectSpaTitleForBot(jsonResp, "/notes/class-12/chemistry", true);
      expect(out.headers.get("content-type")).toContain("application/json");
    });

    it("returns original response unchanged for unmatched HTML path", async () => {
      const html = '<html><head><title>Syrabit.ai</title></head></html>';
      const resp = new Response(html, { headers: { "Content-Type": "text/html" } });
      const out = _injectSpaTitleForBot(resp, "/pricing", true);
      const body = await out.text();
      expect(body).toContain("Syrabit.ai");
    });

    describe("onMiss callback (Task #9)", () => {
      beforeEach(() => {
        function MockHTMLRewriter(this: object) {}
        MockHTMLRewriter.prototype.on = function() { return this; };
        MockHTMLRewriter.prototype.transform = function(r: Response) { return r; };
        vi.stubGlobal("HTMLRewriter", MockHTMLRewriter);
      });
      afterEach(() => { vi.unstubAllGlobals(); });

      it("calls onMiss when isBotGet=true and HTML but no matching pattern", () => {
        const onMiss = vi.fn();
        const resp = new Response("<html><head><title>x</title></head></html>", {
          headers: { "Content-Type": "text/html" },
        });
        _injectSpaTitleForBot(resp, "/pricing", true, onMiss);
        expect(onMiss).toHaveBeenCalledOnce();
        expect(onMiss).toHaveBeenCalledWith("/pricing");
      });

      it("does NOT call onMiss when isBotGet=false", () => {
        const onMiss = vi.fn();
        const resp = new Response("<html><head><title>x</title></head></html>", {
          headers: { "Content-Type": "text/html" },
        });
        _injectSpaTitleForBot(resp, "/pricing", false, onMiss);
        expect(onMiss).not.toHaveBeenCalled();
      });

      it("does NOT call onMiss when content-type is not HTML", () => {
        const onMiss = vi.fn();
        const resp = new Response('{}', { headers: { "Content-Type": "application/json" } });
        _injectSpaTitleForBot(resp, "/pricing", true, onMiss);
        expect(onMiss).not.toHaveBeenCalled();
      });

      it("does NOT call onMiss when a matching pattern exists (rewrite fires instead)", () => {
        const onMiss = vi.fn();
        const resp = new Response("<html><head><title>Old</title></head></html>", {
          headers: { "Content-Type": "text/html" },
        });
        _injectSpaTitleForBot(resp, "/notes/class-12/chemistry", true, onMiss);
        expect(onMiss).not.toHaveBeenCalled();
      });

      it("passes the full pathname to onMiss so callers can slice as needed", () => {
        const onMiss = vi.fn();
        const resp = new Response("<html>", { headers: { "Content-Type": "text/html" } });
        _injectSpaTitleForBot(resp, "/some/unknown/path/with/segments", true, onMiss);
        expect(onMiss).toHaveBeenCalledWith("/some/unknown/path/with/segments");
      });
    });

    describe("positive rewrite path (HTMLRewriter mocked for Node.js env)", () => {
      type ElementHandler = {
        element: (el: { setInnerContent: (s: string) => void; setAttribute: (k: string, v: string) => void }) => void;
      };

      let capturedHandlers: Record<string, ElementHandler>;
      let capturedTransformArg: Response;
      const transformedResponse = new Response("rewritten", { headers: { "Content-Type": "text/html" } });

      beforeEach(() => {
        capturedHandlers = {};
        capturedTransformArg = new Response("");
        function MockHTMLRewriter(this: { on: typeof MockHTMLRewriter.prototype.on; transform: typeof MockHTMLRewriter.prototype.transform }) {
          // constructor body intentionally empty
        }
        MockHTMLRewriter.prototype.on = function(selector: string, handler: ElementHandler) {
          capturedHandlers[selector] = handler;
          return this;
        };
        MockHTMLRewriter.prototype.transform = function(resp: Response) {
          capturedTransformArg = resp;
          return transformedResponse;
        };
        vi.stubGlobal("HTMLRewriter", MockHTMLRewriter);
      });

      afterEach(() => {
        vi.unstubAllGlobals();
      });

      it("rewrites title for bot GET on matched HTML route", () => {
        const resp = new Response("<html><head><title>Old</title></head></html>", {
          headers: { "Content-Type": "text/html" },
        });
        const out = _injectSpaTitleForBot(resp, "/notes/class-12/chemistry", true);
        expect(out).toBe(transformedResponse);

        const titleEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers["title"].element(titleEl);
        expect(titleEl.setInnerContent).toHaveBeenCalledWith("Chemistry — Class 12 Notes | Syrabit.ai");
      });

      it("rewrites description meta for bot GET on matched HTML route", () => {
        const resp = new Response('<html><head><meta name="description" content="old"></head></html>', {
          headers: { "Content-Type": "text/html" },
        });
        _injectSpaTitleForBot(resp, "/notes/class-11/physics/chapter-1", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[name="description"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", expect.stringContaining("Physics"));
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", expect.stringContaining("Class 11"));
      });

      it("rewrites og:title for bot GET on matched HTML route (Task #8)", () => {
        const resp = new Response(
          '<html><head><meta property="og:title" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-12/chemistry", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:title"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", "Chemistry — Class 12 Notes | Syrabit.ai");
      });

      it("rewrites og:description for bot GET on matched HTML route (Task #8)", () => {
        const resp = new Response(
          '<html><head><meta property="og:description" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-11/physics/chapter-1", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:description"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", expect.stringContaining("Physics"));
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", expect.stringContaining("Class 11"));
      });

      it("og:title and og:description use the same values as title and description", () => {
        const resp = new Response("<html><head></head></html>", {
          headers: { "Content-Type": "text/html" },
        });
        _injectSpaTitleForBot(resp, "/ahsec/class-12", true);

        const titleEl    = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        const ogTitleEl  = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        const descEl     = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        const ogDescEl   = { setInnerContent: vi.fn(), setAttribute: vi.fn() };

        capturedHandlers["title"].element(titleEl);
        capturedHandlers['meta[property="og:title"]'].element(ogTitleEl);
        capturedHandlers['meta[name="description"]'].element(descEl);
        capturedHandlers['meta[property="og:description"]'].element(ogDescEl);

        const titleValue  = titleEl.setInnerContent.mock.calls[0][0] as string;
        const ogTitleVal  = ogTitleEl.setAttribute.mock.calls[0][1] as string;
        const descValue   = descEl.setAttribute.mock.calls[0][1] as string;
        const ogDescValue = ogDescEl.setAttribute.mock.calls[0][1] as string;

        expect(ogTitleVal).toBe(titleValue);
        expect(ogDescValue).toBe(descValue);
      });

      it("applies rewrite for deep chapter path (nested route) — acceptance example", () => {
        const resp = new Response("<html><head><title>Old</title></head></html>", {
          headers: { "Content-Type": "text/html" },
        });
        const out = _injectSpaTitleForBot(resp, "/notes/class-12/chemistry/some-chapter", true);
        expect(out).toBe(transformedResponse);

        const titleEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers["title"].element(titleEl);
        expect(titleEl.setInnerContent).toHaveBeenCalledWith("Chemistry — Class 12 Notes | Syrabit.ai");
      });

      it("rewrites twitter:title for bot GET on matched HTML route (Task #15)", () => {
        const resp = new Response(
          '<html><head><meta name="twitter:title" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-12/chemistry", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[name="twitter:title"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", "Chemistry — Class 12 Notes | Syrabit.ai");
      });

      it("rewrites twitter:description for bot GET on matched HTML route (Task #15)", () => {
        const resp = new Response(
          '<html><head><meta name="twitter:description" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-11/physics/chapter-1", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[name="twitter:description"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", expect.stringContaining("Physics"));
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", expect.stringContaining("Class 11"));
      });

      it("sets twitter:card to summary_large_image on matched HTML route (Task #15)", () => {
        const resp = new Response(
          '<html><head><meta name="twitter:card" content="summary"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-12/chemistry", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[name="twitter:card"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", "summary_large_image");
      });

      it("rewrites og:image for subject route to CDN PNG URL (Task #17)", () => {
        const resp = new Response(
          '<html><head><meta property="og:image" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-12/chemistry", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:image"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith(
          "content",
          `${_OG_IMAGE_BASE}/chemistry.png`,
        );
      });

      it("rewrites og:image for board hub route to CDN PNG URL (Task #17)", () => {
        const resp = new Response(
          '<html><head><meta property="og:image" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/ahsec", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:image"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith(
          "content",
          `${_OG_IMAGE_BASE}/ahsec.png`,
        );
      });

      it("og:image URL uses _OG_IMAGE_BASE constant prefix (Task #17)", () => {
        const resp = new Response(
          '<html><head><meta property="og:image" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-11/physics", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:image"]'].element(metaEl);
        const ogImageUrl = metaEl.setAttribute.mock.calls[0][1] as string;
        expect(ogImageUrl).toMatch(/^https:\/\/cdn\.syrabit\.ai\/og\//);
        expect(ogImageUrl).toMatch(/\.png$/);
      });
    });
  });
});
