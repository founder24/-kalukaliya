// Static data generation — fetches public content from GCS (source of truth)
// at build time and writes JSON / XML files into public/ so the CDN can
// serve them directly without hitting the API on every page load.
//
// Priority: GCS bucket (if GCS_BUCKET env set) → Backend API (fallback)
//
// Release builds fail when any required fetch or payload validation fails.
// Local production/offline builds can explicitly opt out of production-style
// validation with ALLOW_INCOMPLETE_CURRICULUM_BUILD=true. A Cloudflare release
// remains strict even if that opt-out is accidentally present.

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
const IS_RELEASE_BUILD = process.env.CLOUDFLARE_RELEASE_BUILD === "true";
const ALLOW_INCOMPLETE_CURRICULUM_BUILD =
  process.env.ALLOW_INCOMPLETE_CURRICULUM_BUILD === "true";
const STRICT_CURRICULUM_BUILD =
  IS_RELEASE_BUILD ||
  (process.env.NODE_ENV === "production" &&
    !ALLOW_INCOMPLETE_CURRICULUM_BUILD);

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

const REQUIRED_JSON_FILES = new Set(JSON_ENDPOINTS.map(({ file }) => file));
const REQUIRED_XML_FILES = new Set(SITEMAP_ENDPOINTS.map(({ file }) => file));

function validateJsonPayload(file, body) {
  let parsed;
  try {
    parsed = JSON.parse(body);
  } catch (err) {
    throw new Error(`invalid JSON (${err.message})`);
  }

  if (!parsed || typeof parsed !== "object") {
    throw new Error("payload must be a JSON object or array");
  }

  // These are the curriculum sources used by prerendering and by the
  // library/browser snapshots. An empty response is a successful HTTP
  // response but still an invalid production build.
  if (file === "subjects.json") {
    if (!Array.isArray(parsed) || parsed.length === 0) {
      throw new Error("payload contains zero subjects");
    }
  } else if (
    (file === "library-bundle.json" ||
      file === "library-bundle-slim.json") &&
    (!Array.isArray(parsed.subjects) || parsed.subjects.length === 0)
  ) {
    throw new Error("payload contains zero subjects");
  }
}

function validateXmlPayload(file, body) {
  if (!body.trim()) throw new Error("payload is empty");
  if (!/<(?:sitemapindex|urlset)\b/i.test(body)) {
    throw new Error("payload is not a sitemap document");
  }
  if (!REQUIRED_XML_FILES.has(file)) {
    throw new Error(`unknown required XML file ${file}`);
  }
}

function validatePayload(file, body) {
  if (REQUIRED_JSON_FILES.has(file)) {
    validateJsonPayload(file, body);
  } else if (REQUIRED_XML_FILES.has(file)) {
    validateXmlPayload(file, body);
  }
}

async function fetchWithFallback(
  gcsPath,
  apiPath,
  { transform, validate } = {},
) {
  // Try GCS first (source of truth)
  if (GCS_BASE) {
    try {
      const url = `${GCS_BASE}/${gcsPath}`;
      const res = await fetch(url, { signal: AbortSignal.timeout(15_000) });
      if (res.ok) {
        let body = await res.text();
        if (transform) body = transform(body);
        if (validate) validate(body);
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
  if (validate) validate(body);
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
  console.log(
    `[static-data] Curriculum validation: ${
      STRICT_CURRICULUM_BUILD
        ? "strict"
        : ALLOW_INCOMPLETE_CURRICULUM_BUILD
          ? "offline opt-out"
          : "development fallback"
    }`,
  );

  // Ensure output directory exists.
  await mkdir(staticDir, { recursive: true });

  const failures = [];

  // Fetch JSON endpoints in parallel.
  await Promise.all(
    JSON_ENDPOINTS.map(async ({ gcsPath, apiPath, file }) => {
      try {
        const body = await fetchWithFallback(gcsPath, apiPath, {
          validate: (payload) => validatePayload(file, payload),
        });
        await writeFile(path.join(staticDir, file), body, "utf-8");
      } catch (err) {
        const message = `${file} — ${err.message}`;
        failures.push(message);
        console.warn(`[static-data] ${STRICT_CURRICULUM_BUILD ? "FAIL" : "WARN"}: ${message}`);
      }
    }),
  );

  // Fetch sitemap XML endpoints in parallel.
  await Promise.all(
    SITEMAP_ENDPOINTS.map(async ({ gcsPath, apiPath, file, rewrite }) => {
      try {
        const body = await fetchWithFallback(gcsPath, apiPath, {
          transform: rewrite ? rewriteSitemapLocs : undefined,
          validate: (payload) => validatePayload(file, payload),
        });
        await writeFile(path.join(publicDir, file), body, "utf-8");
      } catch (err) {
        const message = `${file} — ${err.message}`;
        failures.push(message);
        console.warn(`[static-data] ${STRICT_CURRICULUM_BUILD ? "FAIL" : "WARN"}: ${message}`);
      }
    }),
  );

  if (failures.length && STRICT_CURRICULUM_BUILD) {
    throw new Error(
      `required static data unavailable or invalid (${failures.length} file(s)):\n` +
        failures.map((failure) => `  - ${failure}`).join("\n"),
    );
  }

  console.log("[static-data] Done.");
}

main().catch((err) => {
  const prefix = STRICT_CURRICULUM_BUILD ? "FAIL" : "WARN";
  console[STRICT_CURRICULUM_BUILD ? "error" : "warn"](
    `[static-data] ${prefix}: ${err.message || err}`,
  );
  process.exit(STRICT_CURRICULUM_BUILD ? 1 : 0);
});
