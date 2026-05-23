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
//   1. Picking one prerendered subject route and one prerendered chapter
//      route from `dist/` (using the same data-hydrate marker scan as
//      verify-all.mjs).
//   2. Serving `dist/` over a local static HTTP server.
//   3. Loading each route in a real headless Chromium via Playwright.
//   4. Failing the build if any console message or page error matches the
//      well-known hydration mismatch signatures (React's plain-text
//      warnings as well as minified production error codes #418/#423/#425).
//
// Soft-fails (warns, exit 0) when there are no prerendered subject or
// chapter routes to inspect — matches the soft-fail philosophy of
// scripts/prerender-routes.mjs and scripts/verify-all.mjs so a
// transient backend outage on the build host doesn't break deploys.

import fs from "fs";
import http from "http";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(__dirname, "..", "dist");
const manifestPath = path.join(distDir, "prerender-manifest.json");

function warn(msg) {
  console.warn(`[verify-hydration] ${msg}`);
}
function fail(msg) {
  console.error(`[verify-hydration] FAIL: ${msg}`);
  process.exit(1);
}

if (!fs.existsSync(manifestPath)) {
  warn("no prerender-manifest.json — prerender step likely soft-failed; skipping verification");
  process.exit(0);
}

const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
const subjectsWritten = manifest?.counts?.subjects_written ?? 0;
const chaptersWritten = manifest?.counts?.chapters_written ?? 0;
if (subjectsWritten === 0 && chaptersWritten === 0) {
  warn("manifest reports zero prerendered routes; nothing to verify");
  process.exit(0);
}

// Walk dist/ and bucket prerendered routes by data-hydrate kind so we can
// pick one representative subject + chapter URL to load in the browser.
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

if (targets.length === 0) {
  warn("no prerendered subject or chapter routes found on disk; nothing to verify");
  process.exit(0);
}

// React hydration mismatch signatures. React 18/19 emits the plain-text
// warnings in dev, and the production minified error codes in prod (which
// is what we ship). Match all of them.
const HYDRATION_PATTERNS = [
  /Hydration failed/i,
  /hydrating but the server rendered/i,
  /did not match/i,
  /Text content does not match/i,
  /Text content did not match/i,
  /Hydration completed but contains mismatches/i,
  /There was an error while hydrating/i,
  /server rendered HTML didn't match the client/i,
  /Minified React error #418/i,
  /Minified React error #421/i,
  /Minified React error #422/i,
  /Minified React error #423/i,
  /Minified React error #425/i,
  /reactjs\.org\/docs\/error-decoder\.html\?invariant=(?:418|421|422|423|425)/i,
  /react\.dev\/errors\/(?:418|421|422|423|425)/i,
];

function looksLikeHydrationProblem(text) {
  if (!text) return false;
  return HYDRATION_PATTERNS.some((re) => re.test(text));
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

// Task #543: detect Playwright environment problems (missing npm package
// OR missing browser binary) and soft-skip rather than hard-fail the
// build. The hydration check is a quality gate, not a structural
// correctness gate — when the build host can't run a headless browser
// (no Playwright cache, missing system libs, etc.), the deploy should
// still go out. The structural assertions in verify-all.mjs already
// guarantee the prerendered HTML itself is well-formed.
function isPlaywrightEnvProblem(err) {
  const msg = String(err?.message || err || "");
  return (
    /Executable doesn[''']t exist/i.test(msg) ||
    /please run the following command to download new browsers/i.test(msg) ||
    /browserType\.launch.*Failed to launch/i.test(msg) ||
    /Failed to launch the browser process/i.test(msg) ||
    /libgbm\.so/i.test(msg) ||
    /libnss3\.so/i.test(msg) ||
    /Host system is missing dependencies/i.test(msg) ||
    /ENOENT.*chrome/i.test(msg) ||
    /MODULE_NOT_FOUND/i.test(msg) ||
    /Cannot find package ['"]playwright['"]?/i.test(msg)
  );
}
function softSkip(reason) {
  warn(reason);
  warn(
    "skipping headless hydration verification — structural checks in verify-all.mjs still ran",
  );
  process.exit(0);
}

async function main() {
  let chromium;
  try {
    ({ chromium } = await import("playwright"));
  } catch (err) {
    if (isPlaywrightEnvProblem(err)) {
      softSkip(
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
    // Playwright's bundled chrome-headless-shell on Replit/NixOS sometimes
    // can't find libgbm.so.1 on the default loader path. Inject the Nix
    // mesa lib directory into LD_LIBRARY_PATH so it can resolve.
    const env = { ...process.env };
    try {
      const mesaLibs = fs
        .readdirSync("/nix/store")
        .filter((n) => /^[a-z0-9]+-mesa-\d/.test(n))
        .map((n) => `/nix/store/${n}/lib`)
        .filter((p) => {
          try {
            return fs.existsSync(`${p}/libgbm.so.1`);
          } catch {
            return false;
          }
        });
      if (mesaLibs.length > 0) {
        env.LD_LIBRARY_PATH = [env.LD_LIBRARY_PATH, ...mesaLibs]
          .filter(Boolean)
          .join(":");
      }
    } catch {
      // /nix/store not present (non-Replit env) — skip the patch.
    }

    try {
      browser = await chromium.launch({
        args: ["--no-sandbox", "--disable-dev-shm-usage"],
        env,
      });
    } catch (launchErr) {
      // Tear down the static server before the soft-skip so we don't
      // leak a listening port to the rest of the build.
      try { server.close(); } catch {}
      if (isPlaywrightEnvProblem(launchErr)) {
        softSkip(
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
      // Final settle so any deferred warnings React logs after commit
      // (e.g. "Hydration completed but contains mismatches") land in our
      // console buffer before we tear the page down.
      await page.waitForTimeout(750);

      const offenders = messages.filter((m) => looksLikeHydrationProblem(m.text));
      for (const o of offenders) {
        findings.push({ route: target.route, kind: target.kind, ...o });
      }

      await context.close();
      console.log(
        `[verify-hydration] ${target.route}: ${messages.length} console msgs, ${offenders.length} hydration issues`,
      );
    }
  } finally {
    if (browser) await browser.close().catch(() => {});
    server.close();
  }

  if (findings.length > 0) {
    console.error("[verify-hydration] hydration mismatches detected:");
    for (const f of findings) {
      console.error(`  - [${f.kind} ${f.route}] (${f.type}) ${f.text}`);
    }
    fail(`${findings.length} hydration warning(s) across ${targets.length} prerendered route(s)`);
  }

  console.log(
    `[verify-hydration] OK — ${targets.length} prerendered route(s) hydrated cleanly in headless Chromium`,
  );
}

main().catch((err) => {
  console.error("[verify-hydration] unexpected failure:", err?.stack || err);
  process.exit(1);
});
