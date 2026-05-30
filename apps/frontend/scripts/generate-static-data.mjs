// Static data generation — fetches public content from the backend at
// build time and writes JSON / XML files into public/ so the CDN can
// serve them directly without hitting the API on every page load.
//
// Non-fatal: if any fetch fails (backend unreachable, endpoint 5xx,
// network timeout) we log a warning and continue. The frontend has a
// runtime fallback that hits the live API when static files are missing.

import { writeFile, mkdir } from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.resolve(__dirname, "..", "public");
const staticDir = path.join(publicDir, "static");

const backendUrl = (
  process.env.BUILD_BACKEND_URL ||
  process.env.VITE_BACKEND_URL ||
  "http://localhost:4000"
).replace(/\/+$/, "");

const API_BASE = `${backendUrl}/api/v1`;

// ── JSON endpoints → public/static/*.json ───────────────────────────────────
const JSON_ENDPOINTS = [
  { endpoint: "/content/library-bundle", file: "library-bundle.json" },
  { endpoint: "/content/library-bundle?slim=1", file: "library-bundle-slim.json" },
  { endpoint: "/content/boards", file: "boards.json" },
  { endpoint: "/content/subjects", file: "subjects.json" },
  { endpoint: "/content/classes", file: "classes.json" },
  { endpoint: "/content/streams", file: "streams.json" },
  { endpoint: "/subscription/plans", file: "plans.json" },
];

// ── Sitemap XML endpoints → public/*.xml ────────────────────────────────────
const SITEMAP_ENDPOINTS = [
  { endpoint: "/seo/sitemap.xml", file: "sitemap-index.xml", rewrite: true },
  { endpoint: "/seo/sitemap-static.xml", file: "sitemap-static.xml" },
  { endpoint: "/seo/sitemap-subjects.xml", file: "sitemap-subjects.xml" },
  { endpoint: "/seo/sitemap-chapters.xml", file: "sitemap-chapters.xml" },
];

async function fetchAndWrite(url, destPath, { transform } = {}) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(30_000) });
    if (!res.ok) {
      console.warn(`[static-data] WARN: ${url} responded ${res.status} — skipped`);
      return;
    }
    let body = await res.text();
    if (transform) body = transform(body);
    await writeFile(destPath, body, "utf-8");
    console.log(`[static-data] ✓ ${path.relative(publicDir, destPath)}`);
  } catch (err) {
    console.warn(`[static-data] WARN: ${url} — ${err.message || err}`);
  }
}

/**
 * Rewrite <loc> URLs in the sitemap index so that references like
 *   https://…/api/v1/seo/sitemap-static.xml
 * become
 *   https://…/sitemap-static.xml
 */
function rewriteSitemapLocs(xml) {
  return xml.replace(
    /(<loc>[^<]*?)\/api\/v1\/seo\/(sitemap-[^<]+<\/loc>)/g,
    "$1/$2",
  );
}

async function main() {
  console.log(`[static-data] Backend: ${API_BASE}`);

  // Ensure output directory exists.
  await mkdir(staticDir, { recursive: true });

  // Fetch JSON endpoints in parallel.
  await Promise.all(
    JSON_ENDPOINTS.map(({ endpoint, file }) =>
      fetchAndWrite(`${API_BASE}${endpoint}`, path.join(staticDir, file)),
    ),
  );

  // Fetch sitemap XML endpoints in parallel.
  await Promise.all(
    SITEMAP_ENDPOINTS.map(({ endpoint, file, rewrite }) =>
      fetchAndWrite(`${API_BASE}${endpoint}`, path.join(publicDir, file), {
        transform: rewrite ? rewriteSitemapLocs : undefined,
      }),
    ),
  );

  console.log("[static-data] Done.");
}

main().catch((err) => {
  // Should never reach here since individual fetches are guarded, but
  // ensure we never fail the build.
  console.warn(`[static-data] Unexpected error: ${err.message || err}`);
  process.exit(0);
});
