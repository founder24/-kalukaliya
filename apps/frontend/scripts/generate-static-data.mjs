// Static data generation — fetches public content from GCS (source of truth)
// at build time and writes JSON / XML files into public/ so the CDN can
// serve them directly without hitting the API on every page load.
//
// Priority: GCS bucket (if GCS_BUCKET env set) → Backend API (fallback)
//
// Non-fatal: if any fetch fails we log a warning and continue. The frontend
// has a runtime fallback that hits the live API when static files are missing.

import { writeFile, mkdir } from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.resolve(__dirname, "..", "public");
const staticDir = path.join(publicDir, "static");

const GCS_BUCKET = (process.env.GCS_BUCKET || "").replace(/\/+$/, "");
const backendUrl = (
  process.env.BUILD_BACKEND_URL ||
  process.env.VITE_BACKEND_URL ||
  "http://localhost:4000"
).replace(/\/+$/, "");

const API_BASE = `${backendUrl}/api/v1`;
const GCS_BASE = GCS_BUCKET ? `https://storage.googleapis.com/${GCS_BUCKET}` : "";

// ── JSON endpoints ──────────────────────────────────────────────────────────
const JSON_ENDPOINTS = [
  { gcsPath: "derived/library-bundle.json", apiPath: "/content/library-bundle", file: "library-bundle.json" },
  { gcsPath: "derived/library-bundle-slim.json", apiPath: "/content/library-bundle?slim=1", file: "library-bundle-slim.json" },
  { gcsPath: "hierarchy/boards.json", apiPath: "/content/boards", file: "boards.json" },
  { gcsPath: "hierarchy/subjects.json", apiPath: "/content/subjects", file: "subjects.json" },
  { gcsPath: "hierarchy/classes.json", apiPath: "/content/classes", file: "classes.json" },
  { gcsPath: "hierarchy/streams.json", apiPath: "/content/streams", file: "streams.json" },
  { gcsPath: "derived/plans.json", apiPath: "/subscription/plans", file: "plans.json" },
];

// ── Sitemap XML endpoints ───────────────────────────────────────────────────
const SITEMAP_ENDPOINTS = [
  { gcsPath: "sitemaps/sitemap-index.xml", apiPath: "/seo/sitemap.xml", file: "sitemap-index.xml", rewrite: true },
  { gcsPath: "sitemaps/sitemap-static.xml", apiPath: "/seo/sitemap-static.xml", file: "sitemap-static.xml" },
  { gcsPath: "sitemaps/sitemap-subjects.xml", apiPath: "/seo/sitemap-subjects.xml", file: "sitemap-subjects.xml" },
  { gcsPath: "sitemaps/sitemap-chapters.xml", apiPath: "/seo/sitemap-chapters.xml", file: "sitemap-chapters.xml" },
];

async function fetchWithFallback(gcsPath, apiPath, { transform } = {}) {
  // Try GCS first (source of truth)
  if (GCS_BASE) {
    try {
      const url = `${GCS_BASE}/${gcsPath}`;
      const res = await fetch(url, { signal: AbortSignal.timeout(15_000) });
      if (res.ok) {
        let body = await res.text();
        if (transform) body = transform(body);
        console.log(`[static-data] \u2713 ${gcsPath} (from GCS)`);
        return body;
      }
      console.warn(`[static-data] GCS ${gcsPath}: ${res.status}, trying API fallback`);
    } catch (err) {
      console.warn(`[static-data] GCS ${gcsPath}: ${err.message}, trying API fallback`);
    }
  }

  // Fallback to backend API
  const url = `${API_BASE}${apiPath}`;
  const res = await fetch(url, { signal: AbortSignal.timeout(30_000) });
  if (!res.ok) throw new Error(`API ${apiPath}: ${res.status}`);
  let body = await res.text();
  if (transform) body = transform(body);
  console.log(`[static-data] \u2713 ${apiPath} (from API)`);
  return body;
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
  console.log(`[static-data] GCS Bucket: ${GCS_BUCKET || "(not set, API-only mode)"}`);
  console.log(`[static-data] Backend API: ${API_BASE}`);

  // Ensure output directory exists.
  await mkdir(staticDir, { recursive: true });

  // Fetch JSON endpoints in parallel.
  await Promise.all(
    JSON_ENDPOINTS.map(async ({ gcsPath, apiPath, file }) => {
      try {
        const body = await fetchWithFallback(gcsPath, apiPath);
        await writeFile(path.join(staticDir, file), body, "utf-8");
      } catch (err) {
        console.warn(`[static-data] WARN: ${file} — ${err.message}`);
      }
    }),
  );

  // Fetch sitemap XML endpoints in parallel.
  await Promise.all(
    SITEMAP_ENDPOINTS.map(async ({ gcsPath, apiPath, file, rewrite }) => {
      try {
        const body = await fetchWithFallback(gcsPath, apiPath, {
          transform: rewrite ? rewriteSitemapLocs : undefined,
        });
        await writeFile(path.join(publicDir, file), body, "utf-8");
      } catch (err) {
        console.warn(`[static-data] WARN: ${file} — ${err.message}`);
      }
    }),
  );

  console.log("[static-data] Done.");
}

main().catch((err) => {
  // Should never reach here since individual fetches are guarded, but
  // ensure we never fail the build.
  console.warn(`[static-data] Unexpected error: ${err.message || err}`);
  process.exit(0);
});
