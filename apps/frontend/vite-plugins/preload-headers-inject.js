// Task #79 — preload-headers-inject.js
//
// Post-build Vite plugin that writes `Link: rel=preload` HTTP headers into
// `dist/_headers` using the hashed filenames produced by the current build.
//
// WHY THIS IS NEEDED
// Cloudflare Pages supports Early Hints (103): when the edge sees a
// `Link: rel=preload` response header on an HTML response it forwards a 103
// status to the browser BEFORE the full 200 arrives, so the browser can start
// fetching critical JS/CSS while the HTML is still in-flight. Task #66
// confirmed Early Hints is ON for the syrabit.ai zone, but the `_headers`
// file contained no Link headers, so the feature was delivering zero benefit.
//
// WHY NOT HARD-CODE THE FILENAMES
// Vite uses content-hash suffixes (e.g. `index-CtEvINMO.js`). Writing a
// static `_headers` entry would break on every deploy the moment a file
// changes. This plugin runs in `writeBundle` (same hook as
// `modulepreload-inject.js`) so it sees the real filenames, reads the Vite
// manifest, and writes a fresh `/*` block for each build.
//
// WHAT IT EMITS (example):
//   /*
//     Link: </assets/index-CtEvINMO.js>; rel=preload; as=script; crossorigin
//     Link: </assets/index-DPLO5fn0.css>; rel=preload; as=style
//     Link: </assets/react-dom-B5Sv2qnU.js>; rel=preload; as=script; crossorigin
//     Link: </assets/router-Ce6RPQkI.js>; rel=preload; as=script; crossorigin
//     Link: </assets/query-DCJWmNgi.js>; rel=preload; as=script; crossorigin
//
// The `/*` rule is added ONCE at the BOTTOM of `dist/_headers` (more-specific
// path rules already present override the catch-all for their own headers).

import fs from "fs";
import path from "path";

// Chunk name patterns to preload (matched against bundle keys, no leading `/`).
// These are the critical-path chunks that the entry point statically imports.
//
// DRIFT WARNING — keep in sync with TARGETS in modulepreload-inject.js.
// Both lists must contain the same chunk names so the <link rel="modulepreload">
// tags injected into index.html by modulepreload-inject.js match the
// Link: response headers emitted by this plugin. If a chunk is renamed or
// split in vite.config.js → rollupOptions.output.manualChunks, update both
// files together. Missing from SCRIPT_TARGETS = no Early Hints for that chunk.
const SCRIPT_TARGETS = ["index", "react-dom", "router", "query"];

export default function preloadHeadersInjectPlugin() {
  return {
    name: "syrabit-preload-headers-inject",
    apply: "build",
    enforce: "post",

    writeBundle(options, bundle) {
      if (options?.ssr) return;

      const outDir = options?.dir
        ? path.resolve(options.dir)
        : path.resolve(process.cwd(), "dist");

      const headersPath = path.join(outDir, "_headers");
      if (!fs.existsSync(headersPath)) {
        this.warn("[preload-headers] dist/_headers not found — skipping.");
        return;
      }

      const fileNames = Object.keys(bundle || {});
      const linkLines = [];

      // ── JS chunks ──────────────────────────────────────────────────────────
      for (const name of SCRIPT_TARGETS) {
        const re = new RegExp(`(?:^|/)${name}-[A-Za-z0-9_-]+\\.js$`);
        const match = fileNames.find((f) => re.test(f));
        if (match) {
          linkLines.push(
            `  Link: </${match}>; rel=preload; as=script; crossorigin`,
          );
        }
      }

      // ── CSS for the entry point ────────────────────────────────────────────
      // Read the Vite manifest to find the CSS file(s) for the entry chunk.
      const manifestPath = path.join(outDir, ".vite", "manifest.json");
      if (fs.existsSync(manifestPath)) {
        try {
          const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
          const entry = manifest["index.html"];
          for (const cssFile of entry?.css || []) {
            linkLines.push(`  Link: </${cssFile}>; rel=preload; as=style`);
          }
        } catch {
          // Non-fatal — proceed without CSS preload.
        }
      }

      if (linkLines.length === 0) {
        this.warn("[preload-headers] No matching chunks found — skipping.");
        return;
      }

      // ── Patch _headers ─────────────────────────────────────────────────────
      // If a `/*` block already exists (e.g. from a previous run), replace
      // only the Link: lines inside it and leave other headers intact.
      // Otherwise append a new `/*` block.
      let headers = fs.readFileSync(headersPath, "utf-8");

      const BLOCK_RE = /^\/\*\s*\n([ \t][^\n]*\n)*/m;
      const newBlock =
        `/*\n` +
        linkLines.join("\n") +
        "\n";

      if (BLOCK_RE.test(headers)) {
        headers = headers.replace(BLOCK_RE, (existing) => {
          // Keep non-Link header lines, replace Link: rel=preload lines.
          const otherLines = existing
            .split("\n")
            .filter(
              (l) =>
                !l.trim().toLowerCase().startsWith("link:") &&
                l !== "/*",
            )
            .join("\n");
          const otherTrimmed = otherLines.replace(/^\s+|\s+$/g, "");
          return (
            `/*\n` +
            linkLines.join("\n") +
            (otherTrimmed ? "\n" + otherTrimmed : "") +
            "\n"
          );
        });
      } else {
        headers = headers.trimEnd() + "\n\n" + newBlock;
      }

      fs.writeFileSync(headersPath, headers);

      // ── Build-time assertion ────────────────────────────────────────────────
      // Read back what was written and verify at least one Link: rel=preload
      // line exists. Emitting a hard error here catches cases where the `/*`
      // block replacement logic silently produced no output (e.g. regex
      // mismatch on an unexpected _headers format).
      const written = fs.readFileSync(headersPath, "utf-8");
      const preloadLineCount = (
        written.match(/^\s+Link:.*rel=preload/gim) || []
      ).length;
      if (preloadLineCount === 0) {
        this.error(
          "[preload-headers] ASSERTION FAILED: dist/_headers was written but " +
            "contains no Link: rel=preload lines. Check the `/*` block logic.",
        );
        return;
      }

      this.warn(
        `[preload-headers] Wrote ${linkLines.length} Link preload header(s) ` +
          `to dist/_headers (/*). Assertion: ${preloadLineCount} line(s) confirmed.`,
      );
    },
  };
}
