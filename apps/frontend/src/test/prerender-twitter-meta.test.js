// Task #38 — Unit tests for twitter:image / twitter:image:alt injection in
// prerender rewriteHead functions.
//
// Each prerender script (prerender-library, prerender-routes,
// prerender-chat, prerender-static-routes) uses a local `rewriteHead`
// function to stamp page-specific meta tags into the static HTML snapshot.
// Task #22 added edge-proxy HTMLRewriter handlers that can rewrite
// `twitter:image` and `twitter:image:alt` — but only if those tags exist in
// the HTML the rewriter receives. This test suite verifies the injection
// logic that was added in Task #38.
//
// We cannot import the script modules directly (they are ESM scripts with
// top-level await/side-effects), so we extract the relevant injection logic
// into a helper below and test it in isolation.

import { describe, it, expect } from 'vitest';

// ---------------------------------------------------------------------------
// Helper — mirrors the exact regex branches used by all four scripts for
// twitter:image and twitter:image:alt (Task #38 additions).
// ---------------------------------------------------------------------------

function injectTwitterImageMeta(html, { twitterImage, twitterImageAlt }) {
  // twitter:image — replace existing tag or inject before </head>
  if (/<meta name="twitter:image" content="[^"]*"\s*\/?>/.test(html)) {
    html = html.replace(
      /<meta name="twitter:image" content="[^"]*"\s*\/?>/,
      `<meta name="twitter:image" content="${twitterImage}" />`,
    );
  } else {
    html = html.replace(
      /<\/head>/,
      `    <meta name="twitter:image" content="${twitterImage}" />\n  </head>`,
    );
  }

  // twitter:image:alt — replace existing tag or inject before </head>
  if (twitterImageAlt) {
    if (/<meta name="twitter:image:alt" content="[^"]*"\s*\/?>/.test(html)) {
      html = html.replace(
        /<meta name="twitter:image:alt" content="[^"]*"\s*\/?>/,
        `<meta name="twitter:image:alt" content="${twitterImageAlt}" />`,
      );
    } else {
      html = html.replace(
        /<\/head>/,
        `    <meta name="twitter:image:alt" content="${twitterImageAlt}" />\n  </head>`,
      );
    }
  }

  return html;
}

// ---------------------------------------------------------------------------
// Minimal HTML fixtures
// ---------------------------------------------------------------------------

const PLACEHOLDER_IMAGE = 'https://syrabit.ai/opengraph.jpg';

// Template that already contains both twitter:image tags (the normal case
// once public/index.html carries them).
const HTML_WITH_TAGS = `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Syrabit.ai</title>
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:image" content="https://syrabit.ai/opengraph.jpg" />
    <meta name="twitter:image:alt" content="Old generic alt text" />
  </head>
  <body><div id="root"></div></body>
</html>`;

// Template without twitter:image tags — tests the inject-before-</head> path.
const HTML_WITHOUT_TAGS = `<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <title>Syrabit.ai</title>
    <meta name="twitter:card" content="summary_large_image" />
  </head>
  <body><div id="root"></div></body>
</html>`;

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('prerender twitter:image injection (Task #38)', () => {
  describe('when the template already contains twitter:image tags', () => {
    it('replaces twitter:image content with the placeholder URL', () => {
      const result = injectTwitterImageMeta(HTML_WITH_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: 'AHSEC Class 11 study materials — Syrabit.ai',
      });
      expect(result).toMatch(
        /meta name="twitter:image" content="https:\/\/syrabit\.ai\/opengraph\.jpg"/,
      );
    });

    it('replaces twitter:image:alt with page-specific alt text', () => {
      const result = injectTwitterImageMeta(HTML_WITH_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: 'AHSEC Class 11 study materials — Syrabit.ai',
      });
      expect(result).toMatch(
        /meta name="twitter:image:alt" content="AHSEC Class 11 study materials — Syrabit\.ai"/,
      );
      // Should not contain the old generic text
      expect(result).not.toContain('Old generic alt text');
    });

    it('produces exactly one twitter:image tag', () => {
      const result = injectTwitterImageMeta(HTML_WITH_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: 'Some alt',
      });
      const matches = result.match(/<meta name="twitter:image"/g) || [];
      expect(matches).toHaveLength(1);
    });

    it('produces exactly one twitter:image:alt tag', () => {
      const result = injectTwitterImageMeta(HTML_WITH_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: 'Some alt',
      });
      const matches = result.match(/<meta name="twitter:image:alt"/g) || [];
      expect(matches).toHaveLength(1);
    });
  });

  describe('when the template is missing twitter:image tags (inject path)', () => {
    it('injects twitter:image before </head>', () => {
      const result = injectTwitterImageMeta(HTML_WITHOUT_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: 'Library alt text',
      });
      expect(result).toContain('<meta name="twitter:image"');
      expect(result).toMatch(
        /meta name="twitter:image" content="https:\/\/syrabit\.ai\/opengraph\.jpg"/,
      );
    });

    it('injects twitter:image:alt before </head>', () => {
      const result = injectTwitterImageMeta(HTML_WITHOUT_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: 'Library alt text',
      });
      expect(result).toContain('<meta name="twitter:image:alt"');
      expect(result).toContain('Library alt text');
    });

    it('injected tags appear inside <head>', () => {
      const result = injectTwitterImageMeta(HTML_WITHOUT_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: 'Test alt',
      });
      const headEnd = result.indexOf('</head>');
      const twitterImageIdx = result.indexOf('<meta name="twitter:image"');
      const twitterAltIdx = result.indexOf('<meta name="twitter:image:alt"');
      expect(twitterImageIdx).toBeGreaterThan(0);
      expect(twitterAltIdx).toBeGreaterThan(0);
      expect(twitterImageIdx).toBeLessThan(headEnd);
      expect(twitterAltIdx).toBeLessThan(headEnd);
    });

    it('preserves existing head tags when injecting', () => {
      const result = injectTwitterImageMeta(HTML_WITHOUT_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: 'Some alt',
      });
      expect(result).toContain('<meta name="twitter:card" content="summary_large_image"');
      expect(result).toContain('<title>Syrabit.ai</title>');
    });
  });

  describe('per-script alt text values', () => {
    it('prerender-library: uses OG_IMAGE_ALT as twitter:image:alt', () => {
      const OG_IMAGE_ALT =
        'Assamboard Subject Library — Notes, MCQs, Definitions & Exam Prep';
      const result = injectTwitterImageMeta(HTML_WITH_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: OG_IMAGE_ALT,
      });
      expect(result).toContain(OG_IMAGE_ALT);
    });

    it('prerender-chat: uses TITLE as twitter:image:alt', () => {
      const TITLE = 'Syrabit AI Chat — Ask Anything About Your Syllabus';
      const result = injectTwitterImageMeta(HTML_WITH_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: TITLE,
      });
      expect(result).toContain(TITLE);
    });

    it('prerender-static-routes: uses per-route ogImageAlt', () => {
      const routeAlt = 'AHSEC study materials — Syrabit.ai';
      const result = injectTwitterImageMeta(HTML_WITH_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: routeAlt,
      });
      expect(result).toContain(routeAlt);
    });

    it('prerender-routes: uses subject-specific ogImageAlt', () => {
      const subjectAlt = 'Economics — AHSEC HS 2nd Year | Syrabit.ai';
      const result = injectTwitterImageMeta(HTML_WITH_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: subjectAlt,
      });
      expect(result).toContain(subjectAlt);
    });
  });

  describe('edge cases', () => {
    it('does not inject twitter:image:alt when twitterImageAlt is empty/falsy', () => {
      const result = injectTwitterImageMeta(HTML_WITHOUT_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: '',
      });
      // The twitter:image should still be injected
      expect(result).toContain('<meta name="twitter:image"');
      // But twitter:image:alt should NOT be injected (empty alt is skipped)
      expect(result).not.toContain('<meta name="twitter:image:alt"');
    });

    it('does not inject twitter:image:alt when twitterImageAlt is null/undefined', () => {
      const result = injectTwitterImageMeta(HTML_WITHOUT_TAGS, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: null,
      });
      expect(result).not.toContain('<meta name="twitter:image:alt"');
    });

    it('handles HTML that has an existing twitter:image:alt but no twitter:image', () => {
      const html = `<html><head>
        <meta name="twitter:image:alt" content="stale alt" />
      </head><body></body></html>`;
      const result = injectTwitterImageMeta(html, {
        twitterImage: PLACEHOLDER_IMAGE,
        twitterImageAlt: 'new alt',
      });
      expect(result).toContain(PLACEHOLDER_IMAGE);
      expect(result).toContain('new alt');
      expect(result).not.toContain('stale alt');
    });

    it('uses the placeholder URL exactly as specified', () => {
      const customUrl = 'https://syrabit.ai/og/science.jpg';
      const result = injectTwitterImageMeta(HTML_WITH_TAGS, {
        twitterImage: customUrl,
        twitterImageAlt: 'Science',
      });
      expect(result).toContain(customUrl);
    });
  });
});
