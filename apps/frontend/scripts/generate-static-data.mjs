// Build-time static data generator for Cloudflare Pages.
//
// Fetches content data from GCS (preferred) or the backend API and
// writes static JSON files + sitemaps into public/ so they can be
// served directly from the CDN without hitting the worker or backend.
//
// Env vars:
//   GCS_BUCKET          — GCS bucket name (for public URL construction)
//   BUILD_BACKEND_URL   — backend API base URL (build-time override)
//   VITE_BACKEND_URL    — backend API base URL (fallback)
//
// Safe to run multiple times (idempotent). Never breaks the build —
// if both GCS and API are unreachable, files are simply skipped.

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const publicDir = path.resolve(__dirname, "..", "public");
const staticDir = path.join(publicDir, "static");

// ── Configuration ───────────────────────────────────────────────────────────

const GCS_BUCKET = (process.env.GCS_BUCKET || "").trim();
const BACKEND_URL = (
  process.env.BUILD_BACKEND_URL ||
  process.env.VITE_BACKEND_URL ||
  ""
).replace(/\/$/, "");

const SITE_URL = "https://syrabit.ai";
const FETCH_TIMEOUT_MS = 10_000;

// ── Helpers ─────────────────────────────────────────────────────────────────

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function writeJSON(filePath, data) {
  ensureDir(path.dirname(filePath));
  fs.writeFileSync(filePath, JSON.stringify(data, null, 2));
  console.log(`[generate-static-data] wrote ${path.relative(publicDir, filePath)}`);
}

function writeFile(filePath, content) {
  ensureDir(path.dirname(filePath));
  fs.writeFileSync(filePath, content, "utf-8");
  console.log(`[generate-static-data] wrote ${path.relative(publicDir, filePath)}`);
}

async function fetchWithTimeout(url, timeoutMs = FETCH_TIMEOUT_MS) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status} from ${url}`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Try fetching from GCS public URL first, fall back to backend API.
 * Returns null if both sources fail.
 */
async function fetchFromGCSOrBackend(gcsPath, apiPath) {
  // Try GCS first
  if (GCS_BUCKET) {
    const gcsUrl = `https://storage.googleapis.com/${GCS_BUCKET}/${gcsPath}`;
    try {
      const data = await fetchWithTimeout(gcsUrl);
      console.log(`[generate-static-data] fetched from GCS: ${gcsPath}`);
      return data;
    } catch (err) {
      console.warn(`[generate-static-data] GCS fetch failed for ${gcsPath}: ${err.message}`);
    }
  }

  // Fall back to backend API
  if (BACKEND_URL) {
    const apiUrl = `${BACKEND_URL}${apiPath}`;
    try {
      const data = await fetchWithTimeout(apiUrl);
      console.log(`[generate-static-data] fetched from API: ${apiPath}`);
      return data;
    } catch (err) {
      console.warn(`[generate-static-data] API fetch failed for ${apiPath}: ${err.message}`);
    }
  }

  console.warn(`[generate-static-data] both GCS and API unavailable for ${gcsPath}`);
  return null;
}

// ── Data Generation ─────────────────────────────────────────────────────────

async function generateLibraryData() {
  // Full library bundle
  const bundle = await fetchFromGCSOrBackend(
    "static/library-bundle.json",
    "/api/content/library-bundle"
  );
  if (bundle) {
    writeJSON(path.join(staticDir, "library-bundle.json"), bundle);
  }

  // Slim library bundle
  const bundleSlim = await fetchFromGCSOrBackend(
    "static/library-bundle-slim.json",
    "/api/content/library-bundle?slim=1"
  );
  if (bundleSlim) {
    writeJSON(path.join(staticDir, "library-bundle-slim.json"), bundleSlim);
  }

  // Extract boards from the bundle (full or slim)
  const sourceBundle = bundle || bundleSlim;
  if (sourceBundle) {
    const boards = sourceBundle.boards || [];
    writeJSON(path.join(staticDir, "boards.json"), boards);

    // Flatten all subjects from the bundle
    const subjects = [];
    if (Array.isArray(boards)) {
      for (const board of boards) {
        const classes = board.classes || [];
        for (const cls of classes) {
          const classSubjects = cls.subjects || [];
          for (const subj of classSubjects) {
            subjects.push(subj);
          }
        }
      }
    }
    writeJSON(path.join(staticDir, "subjects.json"), subjects);
    return { boards, subjects, bundle: sourceBundle };
  }

  return { boards: [], subjects: [], bundle: null };
}

function generatePlans() {
  const plans = [
    { id: "pro", name: "Pro Monthly", price: 499, currency: "INR" }
  ];
  writeJSON(path.join(staticDir, "plans.json"), plans);
}

// ── Sitemap Generation ──────────────────────────────────────────────────────

function generateSitemapIndex() {
  const now = new Date().toISOString().split("T")[0];
  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>${SITE_URL}/sitemap-static.xml</loc>
    <lastmod>${now}</lastmod>
  </sitemap>
  <sitemap>
    <loc>${SITE_URL}/sitemap-subjects.xml</loc>
    <lastmod>${now}</lastmod>
  </sitemap>
  <sitemap>
    <loc>${SITE_URL}/sitemap-chapters.xml</loc>
    <lastmod>${now}</lastmod>
  </sitemap>
</sitemapindex>`;
  writeFile(path.join(publicDir, "sitemap-index.xml"), xml);
}

function generateStaticSitemap() {
  const now = new Date().toISOString().split("T")[0];
  const staticPages = ["/", "/library", "/pricing", "/login"];
  const urls = staticPages
    .map(
      (p) => `  <url>
    <loc>${SITE_URL}${p}</loc>
    <lastmod>${now}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>${p === "/" ? "1.0" : "0.8"}</priority>
  </url>`
    )
    .join("\n");

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls}
</urlset>`;
  writeFile(path.join(publicDir, "sitemap-static.xml"), xml);
}

function generateSubjectsSitemap(subjects, boards) {
  const now = new Date().toISOString().split("T")[0];

  // Build a lookup for board/class slugs from the boards array
  const urls = [];
  if (Array.isArray(boards)) {
    for (const board of boards) {
      const classes = board.classes || [];
      for (const cls of classes) {
        const classSubjects = cls.subjects || [];
        for (const subj of classSubjects) {
          const boardSlug = board.slug || board.name;
          const classSlug = cls.slug || cls.name;
          const subjectSlug = subj.slug || subj.name;
          if (boardSlug && classSlug && subjectSlug) {
            urls.push(`  <url>
    <loc>${SITE_URL}/${boardSlug}/${classSlug}/${subjectSlug}</loc>
    <lastmod>${now}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.7</priority>
  </url>`);
          }
        }
      }
    }
  }

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.join("\n")}
</urlset>`;
  writeFile(path.join(publicDir, "sitemap-subjects.xml"), xml);
}

function generateChaptersSitemap(bundle) {
  const now = new Date().toISOString().split("T")[0];
  const urls = [];

  if (bundle && Array.isArray(bundle.boards)) {
    for (const board of bundle.boards) {
      const classes = board.classes || [];
      for (const cls of classes) {
        const classSubjects = cls.subjects || [];
        for (const subj of classSubjects) {
          const chapters = subj.chapters || [];
          for (const ch of chapters) {
            const chapterSlug = ch.slug;
            if (chapterSlug) {
              urls.push(`  <url>
    <loc>${SITE_URL}/chapter/${chapterSlug}</loc>
    <lastmod>${now}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.6</priority>
  </url>`);
            }
          }
        }
      }
    }
  }

  const xml = `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${urls.join("\n")}
</urlset>`;
  writeFile(path.join(publicDir, "sitemap-chapters.xml"), xml);
}

// ── Main ────────────────────────────────────────────────────────────────────

async function main() {
  console.log("[generate-static-data] starting...");
  console.log(`[generate-static-data] GCS_BUCKET=${GCS_BUCKET || "(not set)"}`);
  console.log(`[generate-static-data] BACKEND_URL=${BACKEND_URL || "(not set)"}`);

  ensureDir(staticDir);

  try {
    // Generate library data (bundles, boards, subjects)
    const { boards, subjects, bundle } = await generateLibraryData();

    // Generate plans stub
    generatePlans();

    // Generate sitemaps
    generateSitemapIndex();
    generateStaticSitemap();
    generateSubjectsSitemap(subjects, boards);
    generateChaptersSitemap(bundle);

    console.log("[generate-static-data] done.");
  } catch (err) {
    // Never break the build - log and continue
    console.error(`[generate-static-data] unexpected error: ${err.message}`);
    console.error(err.stack);
  }
}

main();
