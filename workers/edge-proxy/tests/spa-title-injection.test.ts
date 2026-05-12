import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { _slugToTitle, _resolveSpaRouteMeta, _injectSpaTitleForBot, _OG_IMAGE_BASE, OG_IMAGE_WIDTH, OG_IMAGE_HEIGHT } from "../src/index";

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

    it("returns subject-specific og:image for /ahsec/class-11/physics (Task #50)", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-11/physics");
      expect(meta).not.toBeNull();
      expect(meta!.title).toBe("Physics — AHSEC Class 11 | Syrabit.ai");
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/physics.png`);
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

      it("rewrites twitter:image for subject route to the same CDN PNG URL as og:image (Task #22)", () => {
        const resp = new Response(
          '<html><head><meta name="twitter:image" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-12/chemistry", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[name="twitter:image"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith(
          "content",
          `${_OG_IMAGE_BASE}/chemistry.png`,
        );
      });

      it("rewrites twitter:image for hub route to the same CDN PNG URL as og:image (Task #22)", () => {
        const resp = new Response(
          '<html><head><meta name="twitter:image" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/ahsec", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[name="twitter:image"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith(
          "content",
          `${_OG_IMAGE_BASE}/ahsec.png`,
        );
      });

      it("twitter:image matches og:image value for the same route (Task #22)", () => {
        const resp = new Response(
          '<html><head><meta property="og:image" content="old"><meta name="twitter:image" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-11/physics", true);

        const ogEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        const twEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:image"]'].element(ogEl);
        capturedHandlers['meta[name="twitter:image"]'].element(twEl);
        const ogVal = ogEl.setAttribute.mock.calls[0][1] as string;
        const twVal = twEl.setAttribute.mock.calls[0][1] as string;
        expect(twVal).toBe(ogVal);
      });

      it("rewrites twitter:image:alt for subject route with subject name (Task #22)", () => {
        const resp = new Response(
          '<html><head><meta name="twitter:image:alt" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-11/physics", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[name="twitter:image:alt"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith(
          "content",
          expect.stringContaining("Physics"),
        );
      });

      it("rewrites twitter:image:alt for hub route with board name (Task #22)", () => {
        const resp = new Response(
          '<html><head><meta name="twitter:image:alt" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/ahsec", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[name="twitter:image:alt"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith(
          "content",
          expect.stringContaining("AHSEC"),
        );
      });

      it("twitter:image:alt matches og:image:alt value for the same route (Task #22)", () => {
        const resp = new Response(
          '<html><head><meta property="og:image:alt" content="old"><meta name="twitter:image:alt" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-12/chemistry", true);

        const ogAltEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        const twAltEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:image:alt"]'].element(ogAltEl);
        capturedHandlers['meta[name="twitter:image:alt"]'].element(twAltEl);
        const ogAltVal = ogAltEl.setAttribute.mock.calls[0][1] as string;
        const twAltVal = twAltEl.setAttribute.mock.calls[0][1] as string;
        expect(twAltVal).toBe(ogAltVal);
      });

      it("does NOT register twitter:image:alt handler for unmatched route (Task #22)", () => {
        const resp = new Response(
          '<html><head><title>x</title></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/pricing", true);
        expect(capturedHandlers['meta[name="twitter:image:alt"]']).toBeUndefined();
      });

      it("rewrites twitter:image:width to OG_IMAGE_WIDTH for subject route (Task #22)", () => {
        const resp = new Response(
          '<html><head><meta name="twitter:image:width" content="0"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-12/chemistry", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[name="twitter:image:width"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", OG_IMAGE_WIDTH);
      });

      it("rewrites twitter:image:height to OG_IMAGE_HEIGHT for subject route (Task #22)", () => {
        const resp = new Response(
          '<html><head><meta name="twitter:image:height" content="0"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-12/chemistry", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[name="twitter:image:height"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", OG_IMAGE_HEIGHT);
      });

      it("twitter:image:width matches og:image:width value (Task #22)", () => {
        const resp = new Response(
          '<html><head><meta property="og:image:width" content="0"><meta name="twitter:image:width" content="0"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/ahsec", true);

        const ogEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        const twEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:image:width"]'].element(ogEl);
        capturedHandlers['meta[name="twitter:image:width"]'].element(twEl);
        expect(twEl.setAttribute.mock.calls[0][1]).toBe(ogEl.setAttribute.mock.calls[0][1]);
      });

      it("twitter:image:height matches og:image:height value (Task #22)", () => {
        const resp = new Response(
          '<html><head><meta property="og:image:height" content="0"><meta name="twitter:image:height" content="0"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/ahsec", true);

        const ogEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        const twEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:image:height"]'].element(ogEl);
        capturedHandlers['meta[name="twitter:image:height"]'].element(twEl);
        expect(twEl.setAttribute.mock.calls[0][1]).toBe(ogEl.setAttribute.mock.calls[0][1]);
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

      it("rewrites og:image:width to OG_IMAGE_WIDTH constant when ogImage present (Task #18)", () => {
        const resp = new Response(
          '<html><head><meta property="og:image:width" content="0"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-12/chemistry", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:image:width"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", OG_IMAGE_WIDTH);
        expect(OG_IMAGE_WIDTH).toBe("1200");
      });

      it("rewrites og:image:height to OG_IMAGE_HEIGHT constant when ogImage present (Task #18)", () => {
        const resp = new Response(
          '<html><head><meta property="og:image:height" content="0"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-12/chemistry", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:image:height"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith("content", OG_IMAGE_HEIGHT);
        expect(OG_IMAGE_HEIGHT).toBe("630");
      });

      it("rewrites og:image:alt with subject name for subject route (Task #18)", () => {
        const resp = new Response(
          '<html><head><meta property="og:image:alt" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/notes/class-11/physics", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:image:alt"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith(
          "content",
          expect.stringContaining("Physics"),
        );
      });

      it("rewrites og:image:alt with board name for hub route (Task #18)", () => {
        const resp = new Response(
          '<html><head><meta property="og:image:alt" content="old"></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/ahsec", true);

        const metaEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
        capturedHandlers['meta[property="og:image:alt"]'].element(metaEl);
        expect(metaEl.setAttribute).toHaveBeenCalledWith(
          "content",
          expect.stringContaining("AHSEC"),
        );
      });

      it("does NOT register og:image:alt handler for unmatched route (Task #18)", () => {
        const resp = new Response(
          '<html><head><title>x</title></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/pricing", true);
        expect(capturedHandlers['meta[property="og:image:alt"]']).toBeUndefined();
      });

      it("does NOT register og:image:width or og:image:height handlers for unmatched route (Task #18)", () => {
        const resp = new Response(
          '<html><head><title>x</title></head></html>',
          { headers: { "Content-Type": "text/html" } },
        );
        _injectSpaTitleForBot(resp, "/pricing", true);
        expect(capturedHandlers['meta[property="og:image:width"]']).toBeUndefined();
        expect(capturedHandlers['meta[property="og:image:height"]']).toBeUndefined();
      });

      it("ogImageAlt is present and non-empty for subject route metadata contract (Task #18)", () => {
        const meta = _resolveSpaRouteMeta("/notes/class-11/physics");
        expect(meta).not.toBeNull();
        expect(typeof meta!.ogImageAlt).toBe("string");
        expect(meta!.ogImageAlt!.length).toBeGreaterThan(0);
      });

      it("ogImageAlt is present and non-empty for board hub metadata contract (Task #18)", () => {
        const meta = _resolveSpaRouteMeta("/ahsec");
        expect(meta).not.toBeNull();
        expect(typeof meta!.ogImageAlt).toBe("string");
        expect(meta!.ogImageAlt!.length).toBeGreaterThan(0);
      });

      it("og:image:width and og:image:height constants are 1200 and 630 (Task #18)", () => {
        expect(OG_IMAGE_WIDTH).toBe("1200");
        expect(OG_IMAGE_HEIGHT).toBe("630");
      });
    });
  });
});

describe("SPA shell index.html — OG image dimension & alt placeholder tags (Task #21)", () => {
  let indexHtml: string;

  beforeEach(() => {
    const indexPath = resolve(__dirname, "../../../artifacts/syrabit/index.html");
    indexHtml = readFileSync(indexPath, "utf-8");
  });

  it("contains meta[property='og:image:width'] so the HTMLRewriter has a node to rewrite", () => {
    expect(indexHtml).toMatch(/meta[^>]+property=["']og:image:width["']/);
  });

  it("contains meta[property='og:image:height'] so the HTMLRewriter has a node to rewrite", () => {
    expect(indexHtml).toMatch(/meta[^>]+property=["']og:image:height["']/);
  });

  it("contains meta[property='og:image:alt'] so the HTMLRewriter has a node to rewrite", () => {
    expect(indexHtml).toMatch(/meta[^>]+property=["']og:image:alt["']/);
  });

  it("og:image:width has numeric content value (1200)", () => {
    expect(indexHtml).toMatch(/property=["']og:image:width["'][^>]+content=["']1200["']/);
  });

  it("og:image:height has numeric content value (630)", () => {
    expect(indexHtml).toMatch(/property=["']og:image:height["'][^>]+content=["']630["']/);
  });

  it("og:image:alt has non-empty content value", () => {
    const match = indexHtml.match(/property=["']og:image:alt["'][^>]+content=["']([^"']+)["']/);
    expect(match).not.toBeNull();
    expect(match![1].length).toBeGreaterThan(0);
  });

  it("all three og:image dimension/alt tags appear inside <head>", () => {
    const headMatch = indexHtml.match(/<head[\s\S]*?<\/head>/i);
    expect(headMatch).not.toBeNull();
    const head = headMatch![0];
    expect(head).toMatch(/og:image:width/);
    expect(head).toMatch(/og:image:height/);
    expect(head).toMatch(/og:image:alt/);
  });

  describe("end-to-end rewrite using real index.html as bot response body (Task #21)", () => {
    type ElementHandler = {
      element: (el: { setInnerContent: (s: string) => void; setAttribute: (k: string, v: string) => void }) => void;
    };

    let capturedHandlers: Record<string, ElementHandler>;

    beforeEach(() => {
      capturedHandlers = {};
      function MockHTMLRewriter(this: object) {}
      MockHTMLRewriter.prototype.on = function(selector: string, handler: ElementHandler) {
        capturedHandlers[selector] = handler;
        return this;
      };
      MockHTMLRewriter.prototype.transform = function(r: Response) { return r; };
      vi.stubGlobal("HTMLRewriter", MockHTMLRewriter);
    });

    afterEach(() => { vi.unstubAllGlobals(); });

    it("og:image:width, og:image:height, and og:image:alt all get rewrite handlers when real index.html is the bot response", () => {
      const resp = new Response(indexHtml, { headers: { "Content-Type": "text/html" } });
      _injectSpaTitleForBot(resp, "/notes/class-11/physics", true);

      const widthEl  = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
      const heightEl = { setInnerContent: vi.fn(), setAttribute: vi.fn() };
      const altEl    = { setInnerContent: vi.fn(), setAttribute: vi.fn() };

      expect(capturedHandlers['meta[property="og:image:width"]']).toBeDefined();
      expect(capturedHandlers['meta[property="og:image:height"]']).toBeDefined();
      expect(capturedHandlers['meta[property="og:image:alt"]']).toBeDefined();

      capturedHandlers['meta[property="og:image:width"]'].element(widthEl);
      capturedHandlers['meta[property="og:image:height"]'].element(heightEl);
      capturedHandlers['meta[property="og:image:alt"]'].element(altEl);

      expect(widthEl.setAttribute).toHaveBeenCalledWith("content", OG_IMAGE_WIDTH);
      expect(heightEl.setAttribute).toHaveBeenCalledWith("content", OG_IMAGE_HEIGHT);
      expect(altEl.setAttribute).toHaveBeenCalledWith("content", expect.stringContaining("Physics"));
    });
  });
});

describe("SPA shell index.html — Twitter card placeholder tags (Task #16)", () => {
  let indexHtml: string;

  beforeEach(() => {
    const indexPath = resolve(__dirname, "../../../artifacts/syrabit/index.html");
    indexHtml = readFileSync(indexPath, "utf-8");
  });

  it("contains meta[name='twitter:card'] so the HTMLRewriter has a node to rewrite", () => {
    expect(indexHtml).toMatch(/meta[^>]+name=["']twitter:card["'][^>]+content=["']summary_large_image["']/);
  });

  it("contains meta[name='twitter:title'] so the HTMLRewriter has a node to rewrite", () => {
    expect(indexHtml).toMatch(/meta[^>]+name=["']twitter:title["']/);
  });

  it("contains meta[name='twitter:description'] so the HTMLRewriter has a node to rewrite", () => {
    expect(indexHtml).toMatch(/meta[^>]+name=["']twitter:description["']/);
  });

  it("all three Twitter card tags appear inside <head>", () => {
    const headMatch = indexHtml.match(/<head[\s\S]*?<\/head>/i);
    expect(headMatch).not.toBeNull();
    const head = headMatch![0];
    expect(head).toMatch(/twitter:card/);
    expect(head).toMatch(/twitter:title/);
    expect(head).toMatch(/twitter:description/);
  });
});

describe("SPA shell index.html — OG image dimension + alt placeholder tags (Task #21)", () => {
  let indexHtml: string;
  let head: string;

  beforeEach(() => {
    const indexPath = resolve(__dirname, "../../../artifacts/syrabit/index.html");
    indexHtml = readFileSync(indexPath, "utf-8");
    const headMatch = indexHtml.match(/<head[\s\S]*?<\/head>/i);
    expect(headMatch).not.toBeNull();
    head = headMatch![0];
  });

  it("contains og:image:width so the HTMLRewriter has a node to rewrite", () => {
    expect(head).toMatch(/meta[^>]+property=["']og:image:width["']/);
  });

  it("og:image:width is 1200", () => {
    expect(head).toMatch(/property=["']og:image:width["'][^>]+content=["']1200["']/);
  });

  it("contains og:image:height so the HTMLRewriter has a node to rewrite", () => {
    expect(head).toMatch(/meta[^>]+property=["']og:image:height["']/);
  });

  it("og:image:height is 630", () => {
    expect(head).toMatch(/property=["']og:image:height["'][^>]+content=["']630["']/);
  });

  it("contains og:image:alt so the HTMLRewriter has a node to rewrite (Task #21)", () => {
    expect(head).toMatch(/meta[^>]+property=["']og:image:alt["']/);
  });

  it("og:image:alt has non-empty default content", () => {
    const match = head.match(/property=["']og:image:alt["'][^>]+content=["']([^"']+)["']/);
    expect(match).not.toBeNull();
    expect(match![1].trim().length).toBeGreaterThan(0);
  });

  it("og:image:alt, og:image:width, og:image:height all appear after og:image", () => {
    const ogImageIdx    = head.indexOf('property="og:image"');
    const ogWidthIdx    = head.indexOf('property="og:image:width"');
    const ogHeightIdx   = head.indexOf('property="og:image:height"');
    const ogAltIdx      = head.indexOf('property="og:image:alt"');
    expect(ogImageIdx).toBeGreaterThan(-1);
    expect(ogWidthIdx).toBeGreaterThan(ogImageIdx);
    expect(ogHeightIdx).toBeGreaterThan(ogImageIdx);
    expect(ogAltIdx).toBeGreaterThan(ogImageIdx);
  });
});

// Task #50 — OG image URL wiring audit
// Verifies that every live subject/hub URL resolves to an existing CDN slug
// and never falls through to a missing or wrong image.
describe("OG image URL wiring audit (Task #50)", () => {
  // Known subject slugs that have a generated og/<slug>.png on the CDN.
  // This list mirrors the filenames in scripts/og-images/generated/.
  const SUBJECT_SLUGS = [
    "accountancy",
    "accountancy-honours",
    "assamese-honours",
    "assamese-mil",
    "bengali-mil",
    "biology",
    "botany",
    "business-administration",
    "business-studies",
    "chemistry",
    "chemistry-honours",
    "computer-science",
    "economics",
    "economics-honours",
    "education",
    "education-honours",
    "english-core",
    "english-first",
    "environmental-education",
    "general-mathematics",
    "general-science",
    "geography",
    "geography-honours",
    "hindi-mil",
    "history",
    "history-honours",
    "life-science",
    "logic-philosophy",
    "mass-communication",
    "mathematics",
    "mathematics-honours",
    "modern-indian-language-assamese",
    "philosophy",
    "physical-science",
    "physics",
    "physics-honours",
    "political-science",
    "political-science-honours",
    "social-science",
    "sociology",
    "sociology-honours",
    "statistics-honours",
    "zoology",
  ] as const;

  // Hub slugs that have dedicated generated images.
  const HUB_SLUGS: Record<string, string> = {
    "/ahsec":          "ahsec.png",
    "/seba":           "seba.png",
    "/degree":         "degree.png",
    "/ahsec/class-11": "ahsec-class-11.png",
    "/ahsec/class-12": "ahsec-class-12.png",
    "/notes":          "notes.png",
    "/notes/class-11": "notes-class-11.png",
    "/notes/class-12": "notes-class-12.png",
  };

  describe("task spec sample URLs resolve correctly", () => {
    it("/notes/class-12/chemistry → chemistry.png (task spec example)", () => {
      const meta = _resolveSpaRouteMeta("/notes/class-12/chemistry");
      expect(meta).not.toBeNull();
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/chemistry.png`);
    });

    it("/degree/ba → degree.png (graceful fallback, task spec example)", () => {
      const meta = _resolveSpaRouteMeta("/degree/ba");
      expect(meta).not.toBeNull();
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/degree.png`);
    });

    it("/seba → seba.png (task spec example)", () => {
      const meta = _resolveSpaRouteMeta("/seba");
      expect(meta).not.toBeNull();
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/seba.png`);
    });
  });

  describe("/ahsec/class-11/:subject — new handler (Task #50)", () => {
    it("resolves to subject-specific PNG, not generic", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-11/physics");
      expect(meta).not.toBeNull();
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/physics.png`);
    });

    it("uses subject slug from URL as image filename", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-11/chemistry");
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/chemistry.png`);
    });

    it("deep chapter path inherits subject image", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-11/mathematics/chapter-3/integration");
      expect(meta).not.toBeNull();
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/mathematics.png`);
    });

    it("title includes subject name and AHSEC Class 11", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-11/biology");
      expect(meta!.title).toBe("Biology — AHSEC Class 11 | Syrabit.ai");
    });

    it("ogImageAlt is non-empty and contains subject name", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-11/physics");
      expect(meta!.ogImageAlt).toBeDefined();
      expect(meta!.ogImageAlt).toContain("Physics");
    });
  });

  describe("/ahsec/class-12/:subject — new handler (Task #50)", () => {
    it("resolves to subject-specific PNG, not generic", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-12/chemistry");
      expect(meta).not.toBeNull();
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/chemistry.png`);
    });

    it("uses subject slug from URL as image filename", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-12/economics");
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/economics.png`);
    });

    it("deep chapter path inherits subject image", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-12/biology/chapter-1/cell-biology");
      expect(meta).not.toBeNull();
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/biology.png`);
    });

    it("title includes subject name and AHSEC Class 12", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-12/mathematics");
      expect(meta!.title).toBe("Mathematics — AHSEC Class 12 | Syrabit.ai");
    });

    it("ogImageAlt is non-empty and contains subject name", () => {
      const meta = _resolveSpaRouteMeta("/ahsec/class-12/chemistry");
      expect(meta!.ogImageAlt).toBeDefined();
      expect(meta!.ogImageAlt).toContain("Chemistry");
    });
  });

  describe("/degree/:program — new hub handler (Task #50)", () => {
    it("/degree/ba → degree.png (graceful fallback)", () => {
      const meta = _resolveSpaRouteMeta("/degree/ba");
      expect(meta).not.toBeNull();
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/degree.png`);
    });

    it("/degree/bcom → degree.png (graceful fallback)", () => {
      const meta = _resolveSpaRouteMeta("/degree/bcom");
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/degree.png`);
    });

    it("/degree/bsc → degree.png (graceful fallback)", () => {
      const meta = _resolveSpaRouteMeta("/degree/bsc");
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/degree.png`);
    });

    it("title includes program name", () => {
      const meta = _resolveSpaRouteMeta("/degree/ba");
      expect(meta!.title).toContain("Ba");
      expect(meta!.title).toContain("Degree");
    });

    it("ogImageAlt mentions program and Degree", () => {
      const meta = _resolveSpaRouteMeta("/degree/bcom");
      expect(meta!.ogImageAlt).toContain("Degree");
    });

    it("does NOT match /degree alone (that is the 1-segment hub)", () => {
      const meta = _resolveSpaRouteMeta("/degree");
      expect(meta).not.toBeNull();
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/degree.png`);
      expect(meta!.title).toBe("Degree Study Materials | Syrabit.ai");
    });
  });

  describe("/seba/:class — new hub handler (Task #50)", () => {
    it("/seba/class-10 → seba.png (graceful fallback)", () => {
      const meta = _resolveSpaRouteMeta("/seba/class-10");
      expect(meta).not.toBeNull();
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/seba.png`);
    });

    it("/seba/class-9 → seba.png (graceful fallback)", () => {
      const meta = _resolveSpaRouteMeta("/seba/class-9");
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/seba.png`);
    });

    it("title includes class name and SEBA", () => {
      const meta = _resolveSpaRouteMeta("/seba/class-10");
      expect(meta!.title).toContain("SEBA");
      expect(meta!.title).toContain("Class 10");
    });

    it("ogImageAlt mentions SEBA", () => {
      const meta = _resolveSpaRouteMeta("/seba/class-9");
      expect(meta!.ogImageAlt).toContain("SEBA");
    });

    it("does NOT match /seba alone (that is the 1-segment hub)", () => {
      const meta = _resolveSpaRouteMeta("/seba");
      expect(meta).not.toBeNull();
      expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/seba.png`);
      expect(meta!.title).toBe("SEBA Study Materials | Syrabit.ai");
    });
  });

  describe("hub pages — all resolve to their dedicated CDN image", () => {
    for (const [pathname, expectedFile] of Object.entries(HUB_SLUGS)) {
      it(`${pathname} → ${expectedFile}`, () => {
        const meta = _resolveSpaRouteMeta(pathname);
        expect(meta).not.toBeNull();
        expect(meta!.ogImage).toBe(`${_OG_IMAGE_BASE}/${expectedFile}`);
      });
    }
  });

  describe("subject slug → CDN filename consistency across all route families", () => {
    for (const slug of SUBJECT_SLUGS) {
      const expectedUrl = `${_OG_IMAGE_BASE}/${slug}.png`;

      it(`${slug}: /notes/class-11/${slug} → ${slug}.png`, () => {
        const meta = _resolveSpaRouteMeta(`/notes/class-11/${slug}`);
        expect(meta).not.toBeNull();
        expect(meta!.ogImage).toBe(expectedUrl);
      });

      it(`${slug}: /notes/class-12/${slug} → ${slug}.png`, () => {
        const meta = _resolveSpaRouteMeta(`/notes/class-12/${slug}`);
        expect(meta).not.toBeNull();
        expect(meta!.ogImage).toBe(expectedUrl);
      });

      it(`${slug}: /ahsec/class-11/${slug} → ${slug}.png`, () => {
        const meta = _resolveSpaRouteMeta(`/ahsec/class-11/${slug}`);
        expect(meta).not.toBeNull();
        expect(meta!.ogImage).toBe(expectedUrl);
      });

      it(`${slug}: /ahsec/class-12/${slug} → ${slug}.png`, () => {
        const meta = _resolveSpaRouteMeta(`/ahsec/class-12/${slug}`);
        expect(meta).not.toBeNull();
        expect(meta!.ogImage).toBe(expectedUrl);
      });

      it(`${slug}: /ahsec/hs-1st-year/${slug} → ${slug}.png`, () => {
        const meta = _resolveSpaRouteMeta(`/ahsec/hs-1st-year/${slug}`);
        expect(meta).not.toBeNull();
        expect(meta!.ogImage).toBe(expectedUrl);
      });

      it(`${slug}: /ahsec/hs-2nd-year/${slug} → ${slug}.png`, () => {
        const meta = _resolveSpaRouteMeta(`/ahsec/hs-2nd-year/${slug}`);
        expect(meta).not.toBeNull();
        expect(meta!.ogImage).toBe(expectedUrl);
      });
    }
  });

  describe("og:image URL shape invariants", () => {
    it("every matched route produces a URL under _OG_IMAGE_BASE", () => {
      const paths = [
        "/notes/class-11/physics",
        "/notes/class-12/chemistry",
        "/ahsec/class-11/biology",
        "/ahsec/class-12/mathematics",
        "/ahsec/hs-1st-year/economics",
        "/ahsec/hs-2nd-year/history",
        "/notes/degree/1st-semester/philosophy",
        "/ahsec",
        "/seba",
        "/degree",
        "/ahsec/class-11",
        "/ahsec/class-12",
        "/notes",
        "/notes/class-11",
        "/notes/class-12",
        "/degree/ba",
        "/degree/bcom",
        "/seba/class-10",
      ];
      for (const p of paths) {
        const meta = _resolveSpaRouteMeta(p);
        expect(meta, `expected non-null meta for ${p}`).not.toBeNull();
        expect(meta!.ogImage, `ogImage for ${p}`).toMatch(
          /^https:\/\/cdn\.syrabit\.ai\/og\/.+\.png$/,
        );
      }
    });

    it("no matched route produces a URL ending in /undefined.png", () => {
      const paths = [
        "/notes/class-11/physics",
        "/ahsec/class-11/chemistry",
        "/ahsec/class-12/biology",
        "/degree/ba",
        "/seba/class-10",
      ];
      for (const p of paths) {
        const meta = _resolveSpaRouteMeta(p);
        expect(meta!.ogImage, `ogImage for ${p} must not be undefined.png`).not.toContain(
          "undefined.png",
        );
      }
    });
  });
});
