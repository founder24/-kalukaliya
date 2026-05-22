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
// Soft-fails when individual scripts return non-zero — prerender is
// already designed to be advisory (the SPA-fallback Worker still
// serves real HTML). The orchestrator only hard-fails when the
// dist/ directory is missing or the env is wrong.

import { spawn } from "child_process";
import path from "path";
import { fileURLToPath } from "url";
import { warmCache } from "./_prerender-data.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

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
    : 8 * 60_000 - 5_000;
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
  console.log("[prerender-all] warming shared backend cache…");
  const cacheStart = Date.now();
  const { bundle, traffic } = await warmCache({ days: trafficDays });
  const cacheElapsed = Math.round((Date.now() - cacheStart) / 1000);
  console.log(
    `[prerender-all] cache warmed in ${cacheElapsed}s — bundle=${bundle ? "ok" : "MISS"}, traffic=${traffic ? "ok" : "MISS"}`,
  );

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

  // We do NOT propagate per-step failures — each prerender script is
  // already designed to soft-fail (SPA shell remains the safety net).
  // verify-all enforces structural correctness for the routes that DID
  // emit; that's the right place to hard-fail.
}

main().catch((err) => {
  // Code-review feedback: only soft-fail expected backend / data-fetch
  // problems (those are already handled inside warmCache + each
  // prerender child returning null). An exception that reaches here
  // is an internal orchestrator bug — surface it so it gets fixed.
  console.error("[prerender-all] unexpected failure:", err?.stack || err);
  process.exit(1);
});
