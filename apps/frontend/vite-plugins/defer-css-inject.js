/**
 * defer-css-inject.js
 *
 * Post-processes the built index.html to make the main Tailwind CSS bundle
 * non-render-blocking. The critical above-the-fold CSS is already inlined in
 * index.html, so the full Tailwind stylesheet can load asynchronously without
 * causing visible FOUC.
 *
 * Technique: replace the Vite-injected <link rel="stylesheet" href="/assets/index-*.css">
 * with the media="print" → onload swap trick — the same pattern used for Google Fonts.
 *
 * PageSpeed impact: eliminates the "Reduce unused CSS" finding that cited 23 KiB
 * of savings from the render-blocking Tailwind bundle.
 */
export default function deferCssInjectPlugin() {
  return {
    name: 'syrabit-defer-css-inject',
    // Only transform the final HTML output (production build).
    // In dev mode, Vite injects CSS via HMR JS — this plugin is a no-op.
    apply: 'build',
    transformIndexHtml: {
      // Run after all other plugins (including preload-headers-inject) have
      // had a chance to write their tags, so we don't rewrite preload hints.
      order: 'post',
      handler(html) {
        // Match the main entry CSS chunk: /assets/index-<hash>.css
        // Vite emits it as <link rel="stylesheet" crossorigin href="/assets/index-*.css">
        // We leave all other stylesheets (e.g. async page chunks) untouched.
        return html.replace(
          /(<link[^>]+rel="stylesheet"[^>]+href="\/assets\/index-[^"]+\.css"[^>]*>)/g,
          (match, tag) => {
            // Already deferred (idempotent guard).
            if (tag.includes('media="print"')) return match;

            // Strip rel="stylesheet" and any crossorigin attr, add media trick.
            const href = tag.match(/href="([^"]+)"/)?.[1];
            if (!href) return match;

            return [
              // Non-blocking load: browser fetches it as print, then swaps to 'all'.
              `<link rel="stylesheet" href="${href}" media="print" onload="this.media='all'">`,
              // Fallback for browsers with JS disabled (no FOUC for real users).
              `<noscript><link rel="stylesheet" href="${href}"></noscript>`,
            ].join('\n    ');
          },
        );
      },
    },
  };
}
