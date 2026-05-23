// Shared helper for prerender-library.mjs / prerender-routes.mjs /
// prerender-chat.mjs (Task #395, Task #404).
//
// Each prerendered page (LibraryPage / ChatPage / SubjectLandingPage /
// ChapterPage) is now its own JS chunk — the entry chunk no longer
// statically imports them, so Vite no longer emits an automatic
// `<link rel="modulepreload">` for them in dist/index.html. Without a
// preload, the browser only discovers the page chunk after it parses +
// executes the entry chunk and resolves the dynamic `import()` in
// `index.jsx`'s `preloadPageForKind()` call, adding ~1 RTT to
// first-paint hydration.
//
// Task #404: resolve the per-page chunk by reading Vite's build
// manifest (`dist/.vite/manifest.json`, emitted via
// `build.manifest = true`) and looking up the page's source path,
// instead of scanning `dist/assets/` for a filename prefix. Filename
// heuristics broke silently when bundler defaults changed (chunk
// merging, rename, hash-only output), so we now drive everything off
// Rollup's authoritative source→chunk map.

import fs from "fs";
import path from "path";

// Map the public "page basename" callers use (LibraryPage / ChatPage /
// SubjectLandingPage / ChapterPage) to the source path Rollup keys the
// manifest by. Keep this list aligned with the `lazyPreload(() =>
// import(...))` calls in `src/App.jsx`.
const PAGE_SOURCE_BY_BASENAME = {
  LibraryPage: "src/pages/LibraryPage.jsx",
  ChatPage: "src/pages/ChatPage.jsx",
  SubjectLandingPage: "src/pages/SubjectLandingPage.jsx",
  ChapterPage: "src/pages/ChapterPage.jsx",
};

let _manifestCache = null;

function readManifest(distDir) {
  if (_manifestCache && _manifestCache.distDir === distDir) {
    return _manifestCache.manifest;
  }
  const manifestPath = path.join(distDir, ".vite", "manifest.json");
  if (!fs.existsSync(manifestPath)) {
    throw new Error(
      `[_page-chunk-preload] Vite build manifest missing at ${manifestPath}. ` +
        `Ensure build.manifest = true in vite.config.js (Task #404) so ` +
        `prerender scripts can resolve per-page chunks by source path.`,
    );
  }
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
  _manifestCache = { distDir, manifest };
  return manifest;
}

export function findPageChunk(distDir, pageBasename) {
  const manifest = readManifest(distDir);

  // Prefer the explicit mapping — it fails loudly if a page is renamed.
  let sourceKey = PAGE_SOURCE_BY_BASENAME[pageBasename];

  // Fallback: search for any manifest key ending with
  // `/<pageBasename>.(jsx|tsx|js|ts)` so a freshly-added page works
  // without editing this file. Only used when the explicit mapping is
  // absent (not when it's present-but-stale).
  if (!sourceKey) {
    const tailRe = new RegExp(`(?:^|/)${pageBasename}\\.(?:jsx|tsx|js|ts)$`);
    sourceKey = Object.keys(manifest).find((k) => tailRe.test(k));
  }

  if (!sourceKey) return null;
  const entry = manifest[sourceKey];
  if (!entry || typeof entry.file !== "string") return null;

  // entry.file is relative to outDir, e.g. "assets/LibraryPage-hash.js".
  // Callers construct the href as `/assets/${chunkFile}`, so return
  // just the basename — and assert the chunk sits under assets/ so a
  // future Vite default that dropped the `assets/` prefix is caught.
  const expectedDir = "assets/";
  if (!entry.file.startsWith(expectedDir)) {
    throw new Error(
      `[_page-chunk-preload] manifest entry for ${sourceKey} has unexpected ` +
        `location "${entry.file}" (expected under "${expectedDir}"). ` +
        `injectPageChunkPreload() assumes /assets/<file>; update callers ` +
        `before removing this guard.`,
    );
  }
  return entry.file.slice(expectedDir.length);
}

export function injectPageChunkPreload(html, chunkFile) {
  if (!chunkFile) return html;
  const href = `/assets/${chunkFile}`;
  if (html.includes(`href="${href}"`)) return html; // already present
  const link = `<link rel="modulepreload" crossorigin href="${href}">`;

  // Prefer to slot in next to the existing modulepreload block so the
  // browser sees all critical-path chunks together.
  const lastPreloadRe = /(<link rel="modulepreload"[^>]*>)(?![\s\S]*<link rel="modulepreload")/;
  if (lastPreloadRe.test(html)) {
    return html.replace(lastPreloadRe, `$1\n    ${link}`);
  }
  // Fall back to injecting immediately before the entry <script type="module">.
  return html.replace(
    /<script type="module"/,
    `${link}\n    <script type="module"`,
  );
}
