// Task #535: fold scripts/inject-modulepreload.mjs into the Vite
// build as a post-bundle plugin. Adds <link rel="modulepreload"> hints
// for the critical-path runtime chunks (react-dom, vendor) to the
// emitted index.html so the browser starts fetching them in parallel
// with the entry chunk.
//
// Runs in the `writeBundle` hook so it sees the final hashed
// filenames Rollup produced. Idempotent — skips chunks whose preload
// link is already present.

import fs from "fs";
import path from "path";

// Task #639: targets are the chunks every prerendered route needs on
// the critical path. The legacy `vendor` chunk was split into router
// (react-router) + query (@tanstack) + radix (@radix-ui + floating-ui
// + react-remove-scroll). Radix is intentionally NOT preloaded — it
// only loads on chat/dialog/form routes that statically import a
// Radix component, and adding it to the base preload set was costing
// ~150 kB of speculative downloads on /library mobile first paint.
const TARGETS = ["react-dom", "router", "query"];

export default function modulepreloadInjectPlugin() {
  return {
    name: "syrabit-modulepreload-inject",
    apply: "build",
    enforce: "post",
    writeBundle(options, bundle) {
      // SSR builds emit JS only — no index.html to patch.
      if (options?.ssr) return;
      const outDir = options?.dir
        ? path.resolve(options.dir)
        : path.resolve(process.cwd(), "dist");
      const htmlPath = path.join(outDir, "index.html");
      if (!fs.existsSync(htmlPath)) return;

      const fileNames = Object.keys(bundle || {});
      const links = [];
      for (const name of TARGETS) {
        const re = new RegExp(`(?:^|/)${name}-[A-Za-z0-9_-]+\\.js$`);
        const match = fileNames.find((f) => re.test(f));
        if (match) {
          links.push(
            `<link rel="modulepreload" crossorigin href="/${match}">`,
          );
        }
      }
      if (links.length === 0) return;

      let html = fs.readFileSync(htmlPath, "utf-8");
      const existingHrefs = new Set(
        (html.match(/<link rel="modulepreload"[^>]*>/g) || [])
          .map((tag) => tag.match(/href="([^"]+)"/)?.[1])
          .filter(Boolean),
      );
      const newLinks = links.filter((tag) => {
        const href = tag.match(/href="([^"]+)"/)?.[1];
        return href && !existingHrefs.has(href);
      });
      if (newLinks.length === 0) return;

      const insertPoint = html.indexOf('<link rel="modulepreload"');
      if (insertPoint === -1) {
        const headEnd = html.indexOf("</head>");
        html =
          html.slice(0, headEnd) +
          "    " +
          newLinks.join("\n    ") +
          "\n  " +
          html.slice(headEnd);
      } else {
        html =
          html.slice(0, insertPoint) +
          newLinks.join("\n    ") +
          "\n    " +
          html.slice(insertPoint);
      }

      fs.writeFileSync(htmlPath, html);
      this.warn(
        `[modulepreload-inject] added ${newLinks.length} preload hint(s): ` +
          newLinks
            .map((l) => l.match(/href="([^"]+)"/)?.[1])
            .filter(Boolean)
            .join(", "),
      );
    },
  };
}
