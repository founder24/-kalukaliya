// Post-build CI assertion for Task #389.
//
// verify-all.mjs only inspects the prerendered HTML structurally —
// it cannot detect a React hydration *mismatch*, where the server-rendered
// DOM and the first client render disagree. React swallows those mismatches
// at runtime by falling back to a full client render (logging a warning to
// the console), so the page appears to "work" while silently shipping a
// broken SSR/CSR contract.
//
// This script closes that gap by:
//   1. Picking one representative public static route, one prerendered
//      subject route, and one prerendered chapter route from `dist/`
//      (using the same data-hydrate marker scan as verify-all.mjs).
//   2. Serving `dist/` over a local static HTTP server.
//   3. Loading each route in a real headless Chromium via Playwright.
//   4. Failing the build if any console message matches the well-known
//      hydration mismatch signatures (React's plain-text warnings as well as
//      minified production error codes #418/#423/#425), or if a public static
//      route raises an uncaught page error.
//
// Soft-fails (warns, exit 0) when there are no routes to inspect — matches
// the soft-fail philosophy of scripts/prerender-routes.mjs and
// scripts/verify-all.mjs so a transient backend outage on the build host
// doesn't break deploys.

import fs from "fs";
import http from "http";
import path from "path";
import { fileURLToPath } from "url";
import {
  browserIssuesForTarget,
} from "./verify-hydration-policy.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// The fixture test points this at an isolated dist tree; release validation
// leaves it unset and always checks the built application.
const distDir = path.resolve(
  process.env.VERIFY_HYDRATION_DIST_DIR || path.join(__dirname, "..", "dist"),
);
const manifestPath = path.join(distDir, "prerender-manifest.json");

function warn(msg) {
  console.warn(`[verify-hydration] ${msg}`);
}
function fail(msg) {
  console.error(`[verify-hydration] FAIL: ${msg}`);
  process.exit(1);
}

let subjectsWritten = 0;
let chaptersWritten = 0;
if (!fs.existsSync(manifestPath)) {
  warn("no prerender-manifest.json — prerender step likely soft-failed");
} else {
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
  subjectsWritten = manifest?.counts?.subjects_written ?? 0;
  chaptersWritten = manifest?.counts?.chapters_written ?? 0;
}

// Walk dist/ and bucket prerendered routes by data-hydrate kind so we can
// pick one representative subject + chapter URL to load in the browser.
const PUBLIC_STATIC_ROUTES = [
  { kind: "static", route: "/home", file: "home/index.html" },
  { kind: "static", route: "/login", file: "login/index.html" },
  { kind: "static", route: "/terms", file: "terms/index.html" },
];

function* walk(dir, prefix = "") {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    const rel = prefix ? `${prefix}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      if (rel === "assets" || rel === "icons" || rel === "fonts") continue;
      yield* walk(full, rel);
    } else if (entry.name === "index.html") {
      yield { full, rel };
    }
  }
}

const subjectRoutes = [];
const chapterRoutes = [];
for (const { full, rel } of walk(distDir)) {
  if (rel === "index.html") continue;
  const route = "/" + rel.replace(/\/index\.html$/, "");
  if (route === "/library") continue;
  const html = fs.readFileSync(full, "utf-8");
  const m = html.match(/<div id="root" data-hydrate="([a-z]+)"/);
  if (!m) continue;
  if (m[1] === "subject") subjectRoutes.push(route);
  else if (m[1] === "chapter") chapterRoutes.push(route);
}

const targets = [];
if (subjectRoutes.length > 0) targets.push({ kind: "subject", route: subjectRoutes[0] });
else if (subjectsWritten > 0) {
  fail(`manifest claimed ${subjectsWritten} subjects written but none found on disk`);
}
if (chapterRoutes.length > 0) targets.push({ kind: "chapter", route: chapterRoutes[0] });
else if (chaptersWritten > 0) {
  fail(`manifest claimed ${chaptersWritten} chapters written but none found on disk`);
}

for (const target of PUBLIC_STATIC_ROUTES) {
  if (fs.existsSync(path.join(distDir, target.file))) {
    targets.push(target);
  }
}

if (targets.length === 0) {
  warn("no public static, subject, or chapter routes found on disk; nothing to verify");
  process.exit(0);
}

// --- Static server over dist/ -----------------------------------------------

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".mjs": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
  ".xml": "application/xml; charset=utf-8",
  ".map": "application/json; charset=utf-8",
};

function serveDist(rootDir) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      try {
        let urlPath = decodeURIComponent((req.url || "/").split("?")[0]);
        if (urlPath.includes("..")) {
          res.writeHead(400);
          return res.end("bad request");
        }
        let filePath = path.join(rootDir, urlPath);
        if (filePath.endsWith(path.sep) || filePath.endsWith("/")) {
          filePath = path.join(filePath, "index.html");
        }
        let stat;
        try {
          stat = fs.statSync(filePath);
        } catch {
          stat = null;
        }
        if (stat && stat.isDirectory()) {
          filePath = path.join(filePath, "index.html");
          try {
            stat = fs.statSync(filePath);
          } catch {
            stat = null;
          }
        }
        if (!stat || !stat.isFile()) {
          // SPA fallback — return root index.html with 200 so the bootstrap
          // can take over. Mirrors Cloudflare Pages behaviour.
          filePath = path.join(rootDir, "index.html");
        }
        const ext = path.extname(filePath).toLowerCase();
        res.writeHead(200, {
          "Content-Type": MIME[ext] || "application/octet-stream",
          "Cache-Control": "no-store",
        });
        fs.createReadStream(filePath).pipe(res);
      } catch (err) {
        res.writeHead(500);
        res.end(String(err?.message || err));
      }
    });
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      resolve({ server, port: typeof addr === "object" ? addr.port : 0 });
    });
  });
}

// --- Browser check ----------------------------------------------------------

// Detect Playwright environment problems (missing npm package, browser binary,
// or native browser runtime) separately from application hydration failures.
// Local builds retain the best-effort soft-skip. Release workflows run the
// native-runtime preflight before this script and set
// REQUIRE_HYDRATION_BROWSER=true so an unavailable browser is a clear
// environment failure rather than an application failure.
function isPlaywrightEnvProblem(err) {
  const msg = String(err?.message || err || "");
  return (
    /Executable doesn[''']t exist/i.test(msg) ||
    /please run the following command to download new browsers/i.test(msg) ||
    /browserType\.launch.*Failed to launch/i.test(msg) ||
    /Failed to launch the browser process/i.test(msg) ||
    /libgbm\.so/i.test(msg) ||
    /libnss3\.so/i.test(msg) ||
    /error while loading shared libraries: lib[^:]+\.so/i.test(msg) ||
    /Host system is missing dependencies/i.test(msg) ||
    /ENOENT.*chrome/i.test(msg) ||
    /MODULE_NOT_FOUND/i.test(msg) ||
    /Cannot find package ['"]playwright['"]?/i.test(msg)
  );
}
function softSkip(reason) {
  warn(`ENVIRONMENT SKIP: ${reason}`);
  warn(
    "skipping headless hydration verification — structural checks in verify-all.mjs still ran",
  );
  process.exit(0);
}

function environmentFail(reason) {
  console.error(`[verify-hydration] ENVIRONMENT ERROR: ${reason}`);
  console.error(
    "[verify-hydration] Chromium was required for this release; " +
      "check the Playwright browser install and runner dependencies.",
  );
  process.exit(1);
}

function handlePlaywrightEnvProblem(reason) {
  if (process.env.REQUIRE_HYDRATION_BROWSER === "true") {
    environmentFail(reason);
  }
  softSkip(reason);
}

const CHAPTER_SEO_CHECKS = [
  { label: "canonical", selector: 'link[rel="canonical"]', attribute: "href" },
  { label: "title", selector: "title", attribute: null },
  {
    label: "description",
    selector: 'meta[name="description"]',
    attribute: "content",
  },
  { label: "og:url", selector: 'meta[property="og:url"]', attribute: "content" },
  {
    label: "og:title",
    selector: 'meta[property="og:title"]',
    attribute: "content",
  },
  {
    label: "og:description",
    selector: 'meta[property="og:description"]',
    attribute: "content",
  },
  {
    label: "og:image:alt",
    selector: 'meta[property="og:image:alt"]',
    attribute: "content",
  },
  {
    label: "twitter:card",
    selector: 'meta[name="twitter:card"]',
    attribute: "content",
  },
  {
    label: "twitter:title",
    selector: 'meta[name="twitter:title"]',
    attribute: "content",
  },
  {
    label: "twitter:description",
    selector: 'meta[name="twitter:description"]',
    attribute: "content",
  },
  {
    label: "twitter:image",
    selector: 'meta[name="twitter:image"]',
    attribute: "content",
  },
  {
    label: "twitter:image:alt",
    selector: 'meta[name="twitter:image:alt"]',
    attribute: "content",
  },
];

async function chapterSeoIssues(page, route) {
  const issues = [];
  const expectedCanonical = `https://syrabit.ai${route}`;

  for (const check of CHAPTER_SEO_CHECKS) {
    const locator = page.locator(check.selector);
    const count = await locator.count();
    if (count !== 1) {
      issues.push({
        type: "seo",
        text:
          `SEO ${check.label} on ${route}: expected exactly 1 matching tag, ` +
          `observed ${count}`,
      });
      continue;
    }

    const value =
      check.attribute === null
        ? await locator.textContent()
        : await locator.getAttribute(check.attribute);
    if (!value || !value.trim()) {
      issues.push({
        type: "seo",
        text: `SEO ${check.label} on ${route}: tag value is empty`,
      });
      continue;
    }

    if (check.label === "canonical" && value !== expectedCanonical) {
      issues.push({
        type: "seo",
        text:
          `SEO canonical on ${route}: expected ${expectedCanonical}, ` +
          `observed ${value}`,
      });
    }
  }

  return issues;
}

async function chapterStructuredDataIssues(page, route) {
  const issues = [];
  const scripts = page.locator('script[type="application/ld+json"]');
  const count = await scripts.count();
  const schemas = [];
  const expectedPath = route.replace(/\/+$/, "") || "/";
  const sameOriginUrlFields = new Set(["@id", "url", "item"]);

  function isAllowedSameOriginUrl(rawUrl, field) {
    let parsed;
    try {
      parsed = new URL(rawUrl, "https://syrabit.ai");
    } catch {
      return true;
    }
    if (parsed.origin !== "https://syrabit.ai") return true;

    const pathname = parsed.pathname.replace(/\/+$/, "") || "/";
    if (pathname === "/" || pathname === "/library") return true;
    if (pathname === expectedPath) return true;
    if (field === "item" && expectedPath.startsWith(`${pathname}/`)) {
      return true;
    }
    return false;
  }

  function collectSameOriginUrls(value, path = "") {
    const urls = [];
    if (Array.isArray(value)) {
      value.forEach((entry, index) => {
        urls.push(...collectSameOriginUrls(entry, `${path}[${index}]`));
      });
      return urls;
    }
    if (value === null || typeof value !== "object") return urls;

    for (const [key, entry] of Object.entries(value)) {
      const fieldPath = path ? `${path}.${key}` : key;
      if (sameOriginUrlFields.has(key) && typeof entry === "string") {
        urls.push({ field: key, fieldPath, value: entry });
      }
      if (entry && typeof entry === "object") {
        urls.push(...collectSameOriginUrls(entry, fieldPath));
      }
    }
    return urls;
  }

  function hasType(value, expectedType) {
    return (
      value === expectedType ||
      (Array.isArray(value) && value.includes(expectedType))
    );
  }

  function countFaqPageObjects(schema) {
    let faqPageCount = hasType(schema["@type"], "FAQPage") ? 1 : 0;
    if (Array.isArray(schema["@graph"])) {
      faqPageCount += schema["@graph"].filter((node) =>
        node && typeof node === "object" && hasType(node["@type"], "FAQPage"),
      ).length;
    }
    return faqPageCount;
  }

  if (count === 0) {
    issues.push({
      type: "structured-data",
      text:
        `Structured data on ${route}: expected at least 1 chapter ` +
        `application/ld+json script, observed 0`,
    });
  }

  for (let index = 0; index < count; index += 1) {
    const scriptNumber = index + 1;
    const raw = (await scripts.nth(index).textContent())?.trim() || "";
    if (!raw) {
      issues.push({
        type: "structured-data",
        text:
          `Structured data on ${route}: script ${scriptNumber} has empty JSON`,
      });
      continue;
    }

    let schema;
    try {
      schema = JSON.parse(raw);
    } catch (err) {
      issues.push({
        type: "structured-data",
        text:
          `Structured data on ${route}: script ${scriptNumber} is not valid JSON ` +
          `(${err.message})`,
      });
      continue;
    }

    if (
      schema === null ||
      typeof schema !== "object" ||
      Array.isArray(schema) ||
      typeof schema["@context"] !== "string" ||
      !schema["@context"].trim()
    ) {
      issues.push({
        type: "structured-data",
        text:
          `Structured data on ${route}: script ${scriptNumber} must contain ` +
          `a JSON-LD object with a non-empty @context`,
      });
      continue;
    }

    const graphIsValid =
      Array.isArray(schema["@graph"]) && schema["@graph"].length > 0;
    const typedObject = typeof schema["@type"] === "string" && schema["@type"].trim();
    if (!graphIsValid && !typedObject) {
      issues.push({
        type: "structured-data",
        text:
          `Structured data on ${route}: script ${scriptNumber} must contain ` +
          `a non-empty @graph or @type`,
      });
      continue;
    }
    schemas.push(schema);

    for (const urlField of collectSameOriginUrls(schema)) {
      if (isAllowedSameOriginUrl(urlField.value, urlField.field)) continue;
      issues.push({
        type: "structured-data",
        text:
          `Structured data on ${route}: script ${scriptNumber} has a same-origin ` +
          `URL at ${urlField.fieldPath} that does not belong to the route; ` +
          `observed ${urlField.value}`,
      });
    }
  }

  const faqExpected = await page.evaluate(() => {
    const entries = window.__CHAPTER_PRELOAD__?.data?.faq_entries;
    if (!Array.isArray(entries)) return false;
    return entries.filter(
      (entry) =>
        entry &&
        String(entry.question || "").trim() &&
        String(entry.answer || "").trim(),
    ).length >= 2;
  });
  if (faqExpected) {
    const faqPageCount = schemas.reduce(
      (count, schema) => count + countFaqPageObjects(schema),
      0,
    );
    if (faqPageCount === 0) {
      issues.push({
        type: "structured-data",
        text:
          `Structured data on ${route}: chapter preload contains FAQ entries ` +
          `but no FAQPage JSON-LD object was found`,
      });
    } else if (faqPageCount !== 1) {
      issues.push({
        type: "structured-data",
        text:
          `Structured data on ${route}: expected exactly 1 FAQPage JSON-LD ` +
          `object when chapter FAQ entries are present, observed ${faqPageCount}`,
      });
    }
  }

  return issues;
}

async function main() {
  let chromium;
  try {
    ({ chromium } = await import("playwright"));
  } catch (err) {
    if (isPlaywrightEnvProblem(err)) {
      handlePlaywrightEnvProblem(
        `playwright npm package not importable: ${err?.message || err}`,
      );
    }
    fail(
      "playwright import failed for an unexpected reason. " +
        `Underlying error: ${err?.message || err}`,
    );
  }

  const { server, port } = await serveDist(distDir);
  const baseUrl = `http://127.0.0.1:${port}`;
  console.log(`[verify-hydration] serving dist/ at ${baseUrl}`);

  let browser;
  const findings = [];
  try {
    try {
      browser = await chromium.launch({
        args: ["--no-sandbox", "--disable-dev-shm-usage"],
      });
    } catch (launchErr) {
      // Tear down the static server before handling the environment failure
      // so we don't leak a listening port to the rest of the build.
      try { server.close(); } catch {}
      if (isPlaywrightEnvProblem(launchErr)) {
        handlePlaywrightEnvProblem(
          `chromium.launch() failed in this environment: ${launchErr?.message || launchErr}`,
        );
      }
      throw launchErr;
    }

    for (const target of targets) {
      const url = `${baseUrl}${target.route}`;
      const messages = [];
      const context = await browser.newContext();
      const page = await context.newPage();

      page.on("console", (msg) => {
        const text = msg.text();
        messages.push({ type: msg.type(), text });
      });
      page.on("pageerror", (err) => {
        messages.push({ type: "pageerror", text: String(err?.message || err) });
      });

      console.log(`[verify-hydration] loading ${target.kind} route ${target.route}`);
      await page.goto(url, { waitUntil: "load", timeout: 30000 });
      if (target.kind === "static") {
        // Static public routes are intentionally SPA shell stubs and use
        // createRoot rather than the prerendered hydrateRoot path. Give the
        // client mount and its effects a short settle window before checking
        // for uncaught page errors.
        await page.waitForTimeout(2000);
      } else {
        // Wait for the bootstrap to mark hydration complete
        // (window.__SYRABIT_HYDRATED__, set by src/index.jsx right after the
        // hydrateRoot call). Fall back to a fixed window if the flag never
        // appears so we still capture console warnings on routes that may
        // have fallen back to client rendering.
        try {
          await page.waitForFunction(() => window.__SYRABIT_HYDRATED__ === true, {
            timeout: 8000,
          });
        } catch {
          await page.waitForTimeout(2000);
        }
      }
      // Final settle so any deferred warnings React logs after commit
      // (e.g. "Hydration completed but contains mismatches") land in our
      // console buffer before we tear the page down.
      await page.waitForTimeout(750);

      const offenders = browserIssuesForTarget(target, messages);
      for (const o of offenders) {
        findings.push({ route: target.route, kind: target.kind, ...o });
      }
      if (target.kind === "chapter") {
        const seoIssues = await chapterSeoIssues(page, target.route);
        for (const issue of seoIssues) {
          findings.push({ route: target.route, kind: target.kind, ...issue });
        }
        const structuredDataIssues = await chapterStructuredDataIssues(
          page,
          target.route,
        );
        for (const issue of structuredDataIssues) {
          findings.push({ route: target.route, kind: target.kind, ...issue });
        }
      }

      await context.close();
      console.log(
        `[verify-hydration] ${target.route}: ${messages.length} console msgs, ${offenders.length} browser issues`,
      );
    }
  } finally {
    if (browser) await browser.close().catch(() => {});
    server.close();
  }

  if (findings.length > 0) {
    console.error("[verify-hydration] browser/hydration issues detected:");
    for (const f of findings) {
      console.error(`  - [${f.kind} ${f.route}] (${f.type}) ${f.text}`);
    }
    fail(`${findings.length} browser issue(s) across ${targets.length} checked route(s)`);
  }

  console.log(
    `[verify-hydration] OK — ${targets.length} route(s) checked in headless Chromium`,
  );
}

main().catch((err) => {
  console.error("[verify-hydration] unexpected failure:", err?.stack || err);
  process.exit(1);
});
