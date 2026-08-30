// Task #535: orchestrate the four prerender scripts.
//
// Pre-warms the shared backend cache (one library-bundle fetch +
// one top-routes fetch) THEN spawns the four prerender scripts in
// parallel. Because they all read from the on-disk cache populated
// here, no script re-issues those fetches.
//
// Each child script is wrapped in its own per-step deadline
// (PRERENDER_STEP_BUDGET_MS, default 6 minutes) so a single hung
// step cannot stall the build.
//
// Development builds soft-fail when individual scripts return non-zero because
// the SPA-fallback Worker still serves real HTML. Release builds hard-fail when
// the required library or subject/chapter scripts fail.

import { spawn } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
import {
  clearPrerenderCache,
  warmCache,
} from "./_prerender-data.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const STRICT_CURRICULUM_BUILD =
  process.env.CLOUDFLARE_RELEASE_BUILD === "true" ||
  (process.env.NODE_ENV === "production" &&
    process.env.ALLOW_INCOMPLETE_CURRICULUM_BUILD !== "true");
const REQUIRED_CURRICULUM_SCRIPTS = new Set([
  "prerender-library.mjs",
  "prerender-routes.mjs",
]);

const STEP_BUDGET_MS = (() => {
  const raw = process.env.PRERENDER_STEP_BUDGET_MS;
  const n = raw ? Number.parseInt(raw, 10) : NaN;
  // Task #543: bumped default 6m → 8m to accommodate 429 retry-with-
  // backoff in _prerender-data.mjs without prematurely SIGTERMing
  // the child. build.mjs allots prerender 8m, which is the matching
  // outer budget; the inner deadline is ε shorter to surface clean
  // errors before the outer guard nukes the process.
  return Number.isFinite(n) && n >= 30_000 && n <= 30 * 60_000
    ? n
    : 1_200_000;
})();

// Task #544: concurrency restored to 4 (run all scripts in parallel).
// The earlier serialization (#543, cap=2) was hiding the real problem
// — too many routes, not too many concurrent fetches. Now that the
// route worklist is capped at ~80 (#544: SUBJECTS_LIMIT 50→20,
// CHAPTERS_PER_SUBJECT 5→3) and _prerender-data.mjs has 429 retry-
// with-backoff, full parallel fan-out is the fastest stable mode.
const CONCURRENCY = (() => {
  const raw = process.env.PRERENDER_CONCURRENCY;
  const n = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(n) && n >= 1 && n <= 8 ? n : 4;
})();

// Single batch — all four scripts run in parallel up to CONCURRENCY.
const SCRIPT_BATCHES = [
  [
    "prerender-library.mjs",
    "prerender-routes.mjs",
    "prerender-chat.mjs",
    "prerender-static-routes.mjs",
  ],
];

function runStep(scriptName) {
  const file = path.join(__dirname, scriptName);
  const startedAt = Date.now();
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [file], {
      stdio: "inherit",
      env: process.env,
    });
    let killed = false;
    const timer = setTimeout(() => {
      killed = true;
      console.warn(
        `[prerender-all] ${scriptName} exceeded ${STEP_BUDGET_MS}ms — sending SIGTERM`,
      );
      try {
        child.kill("SIGTERM");
      } catch {}
      setTimeout(() => {
        try {
          child.kill("SIGKILL");
        } catch {}
      }, 5_000).unref();
    }, STEP_BUDGET_MS);
    timer.unref();
    child.on("exit", (code, signal) => {
      clearTimeout(timer);
      const elapsed = Math.round((Date.now() - startedAt) / 1000);
      const ok = code === 0 && !killed;
      const status = killed
        ? "TIMEOUT"
        : code === 0
          ? "ok"
          : `FAIL (code=${code}${signal ? `, signal=${signal}` : ""})`;
      console.log(`[prerender-all] ${scriptName}: ${status} in ${elapsed}s`);
      resolve({ scriptName, ok, elapsed, killed });
    });
  });
}

async function main() {
  const overallStart = Date.now();
  const trafficDays = Number.parseInt(
    process.env.PRERENDER_TRAFFIC_DAYS || "30",
    10,
  );
  if (STRICT_CURRICULUM_BUILD) {
    // The CI/build cache can restore a valid-looking curriculum snapshot from
    // an earlier release. Invalidate it before warmCache() so the cache shared
    // by all child prerender processes is populated only by this build.
    clearPrerenderCache();
    console.log(
      "[prerender-all] release cache policy: invalidated restored prerender cache; fetching a fresh curriculum snapshot",
    );
  } else {
    console.log(
      "[prerender-all] development cache policy: reusing fresh prerender cache entries when available",
    );
  }
  console.log("[prerender-all] warming shared backend cache…");
  const cacheStart = Date.now();
  const { bundle, traffic } = await warmCache({ days: trafficDays });
  const cacheElapsed = Math.round((Date.now() - cacheStart) / 1000);
  console.log(
    `[prerender-all] cache warmed in ${cacheElapsed}s — bundle=${bundle ? "ok" : "MISS"}, traffic=${traffic ? "ok" : "MISS"}`,
  );
  if (
    STRICT_CURRICULUM_BUILD &&
    (!bundle || !Array.isArray(bundle.subjects) || bundle.subjects.length === 0)
  ) {
    throw new Error(
      "[prerender-all] release build requires a non-empty library bundle",
    );
  }

  // Honour PRERENDER_SUBJECTS_LIMIT=0 as a kill-switch for skipping
  // the heavy subject + chapter pass. Useful when the backend is
  // known to be slow and we just want a fast SPA-shell deploy.
  if (process.env.PRERENDER_SUBJECTS_LIMIT === "0") {
    console.warn(
      "[prerender-all] PRERENDER_SUBJECTS_LIMIT=0 — skipping subject/chapter prerender",
    );
  }

  // Task #543: run scripts in capped-concurrency batches instead of
  // one big Promise.all so we don't burst-hit the backend rate limit.
  // Flatten SCRIPT_BATCHES, then walk it CONCURRENCY-at-a-time.
  const ordered = SCRIPT_BATCHES.flat();
  const results = [];
  for (let i = 0; i < ordered.length; i += CONCURRENCY) {
    const slice = ordered.slice(i, i + CONCURRENCY);
    console.log(
      `[prerender-all] batch ${Math.floor(i / CONCURRENCY) + 1}: ${slice.join(", ")}`,
    );
    const batchResults = await Promise.all(slice.map(runStep));
    results.push(...batchResults);
  }

  const totalElapsed = Math.round((Date.now() - overallStart) / 1000);
  const failed = results.filter((r) => !r.ok);
  console.log(
    `[prerender-all] done in ${totalElapsed}s — ${results.length - failed.length}/${results.length} steps ok (concurrency=${CONCURRENCY})` +
      (failed.length
        ? `, failures: ${failed.map((f) => f.scriptName).join(", ")}`
        : ""),
  );

  const requiredFailures = failed.filter((result) =>
    REQUIRED_CURRICULUM_SCRIPTS.has(result.scriptName),
  );
  if (STRICT_CURRICULUM_BUILD && requiredFailures.length > 0) {
    throw new Error(
      `[prerender-all] required release prerender step(s) failed: ` +
        requiredFailures.map(({ scriptName }) => scriptName).join(", "),
    );
  }

  // Development/offline builds keep the SPA fallback. Production release
  // builds propagate failures from the library and curriculum route scripts.
}

main().catch((err) => {
  // Code-review feedback: only soft-fail expected backend / data-fetch
  // problems (those are already handled inside warmCache + each
  // prerender child returning null). An exception that reaches here
  // is an internal orchestrator bug — surface it so it gets fixed.
  console.error("[prerender-all] unexpected failure:", err?.stack || err);
  process.exit(1);
});
