#!/usr/bin/env node
/**
 * Task #749 — Off-host Trustpilot aggregate refresh.
 *
 * Why this exists:
 *   The production backend container (Azure Container Apps) and the
 *   Cloudflare Pages build container both have egress that is fully
 *   WAF-blocked from `api.trustpilot.com`, `www.trustpilot.com`, and
 *   `widget.trustpilot.com` (CloudFront 403 on every endpoint shape —
 *   see Task #747). Without an outside refresher, the committed cache
 *   file (`artifacts/syrabit/scripts/.trustpilot-aggregate-cache.json`)
 *   drifts, and the backend's in-process cache stays empty, which
 *   keeps the >24h staleness alert firing forever.
 *
 * What this does:
 *   1. Calls Trustpilot's Business API from wherever this script is
 *      invoked (intended: a daily GitHub Actions cron — GH runners
 *      are not WAF-blocked) using TRUSTPILOT_API_KEY +
 *      TRUSTPILOT_BUSINESS_UNIT_ID.
 *   2. On success, writes the canonical aggregate JSON to
 *      `artifacts/syrabit/scripts/.trustpilot-aggregate-cache.json`
 *      (so the next Cloudflare Pages build bakes in the latest
 *      values).
 *   3. POSTs the same payload to
 *      `${BACKEND_URL}/api/config/trustpilot/aggregate/refresh`
 *      with the `X-Trustpilot-Refresh-Secret` header — refreshes
 *      the backend's in-process cache so the >24h staleness alert
 *      clears immediately, without waiting for a redeploy.
 *
 * Required env:
 *   TRUSTPILOT_API_KEY            Trustpilot Business API key
 *   TRUSTPILOT_BUSINESS_UNIT_ID   Trustpilot business unit GUID
 *
 * Optional env:
 *   BACKEND_URL                   default https://api.syrabit.ai
 *   TRUSTPILOT_REFRESH_SECRET     when set, POST to backend webhook
 *                                 (must match the backend's env var
 *                                 of the same name). When unset, only
 *                                 the cache file is updated.
 *   SKIP_CACHE_FILE_WRITE=1       skip writing the committed cache
 *                                 file (e.g. when only a backend
 *                                 push is desired).
 *   SKIP_BACKEND_PUSH=1           skip the backend POST.
 *
 * Exit codes:
 *   0  success (at least one of: cache written, backend pushed)
 *   1  Trustpilot fetch failed or returned unparseable response
 *   2  backend push failed (and was not skipped)
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, "..");
const cacheFile = path.resolve(
  repoRoot,
  "artifacts/syrabit/scripts/.trustpilot-aggregate-cache.json",
);

const FETCH_TIMEOUT_MS = 15000;

function log(msg) {
  console.log(`[trustpilot-refresh] ${msg}`);
}

function err(msg) {
  console.error(`[trustpilot-refresh] ${msg}`);
}

async function fetchWithTimeout(url, opts = {}, timeoutMs = FETCH_TIMEOUT_MS) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    return await fetch(url, { ...opts, signal: ctrl.signal });
  } finally {
    clearTimeout(t);
  }
}

function isValidAggregate(obj) {
  return (
    obj &&
    typeof obj === "object" &&
    typeof obj.ratingValue === "number" &&
    Number.isFinite(obj.ratingValue) &&
    obj.ratingValue > 0 &&
    typeof obj.ratingCount === "number" &&
    Number.isInteger(obj.ratingCount) &&
    obj.ratingCount > 0
  );
}

async function fetchFromTrustpilot() {
  const apiKey = (process.env.TRUSTPILOT_API_KEY || "").trim();
  const buId = (process.env.TRUSTPILOT_BUSINESS_UNIT_ID || "").trim();
  if (!apiKey || !buId) {
    err("missing TRUSTPILOT_API_KEY or TRUSTPILOT_BUSINESS_UNIT_ID");
    process.exit(1);
  }
  const url = `https://api.trustpilot.com/v1/business-units/${encodeURIComponent(buId)}?apikey=${encodeURIComponent(apiKey)}`;
  let resp;
  try {
    resp = await fetchWithTimeout(url, {
      headers: {
        apikey: apiKey,
        Accept: "application/json",
        "User-Agent": "Syrabit.ai-Refresh/1.0 (+https://syrabit.ai)",
      },
    });
  } catch (e) {
    err(`trustpilot fetch network error: ${e.message || e}`);
    process.exit(1);
  }
  if (!resp.ok) {
    const body = await resp.text().catch(() => "");
    err(`trustpilot HTTP ${resp.status}: ${body.slice(0, 300)}`);
    process.exit(1);
  }
  const data = await resp.json();
  const score = (data && typeof data.score === "object") ? data.score : {};
  const ratingValue =
    (typeof data.trustScore === "number" && data.trustScore) ||
    (typeof score.trustScore === "number" && score.trustScore) ||
    (typeof data.stars === "number" && data.stars) ||
    (typeof score.stars === "number" && score.stars) ||
    null;
  const nr = data.numberOfReviews;
  const ratingCount = (typeof nr === "object" && nr !== null) ? nr.total : nr;
  const candidate = {
    ratingValue: Number(ratingValue),
    ratingCount: Number(ratingCount),
    bestRating: 5,
    worstRating: 1,
  };
  if (!isValidAggregate(candidate)) {
    err(`unparseable trustpilot response: rating=${ratingValue} count=${ratingCount}`);
    process.exit(1);
  }
  // Round to 2dp to match backend canonical form.
  candidate.ratingValue = Math.round(candidate.ratingValue * 100) / 100;
  return candidate;
}

function writeCacheFile(agg) {
  const body = {
    _comment:
      "Build-time cache for Trustpilot aggregate-rating JSON-LD (Tasks #729 / #747 / #749). " +
      "Refreshed automatically by .github/workflows/trustpilot-aggregate-refresh.yml " +
      "(scripts/refresh-trustpilot-aggregate.mjs) from a host Trustpilot does not WAF-block. " +
      "If you need to update by hand, set ratingValue + ratingCount and commit; " +
      "set them to null to disable injection until the next successful refresh.",
    ratingValue: agg.ratingValue,
    ratingCount: agg.ratingCount,
    bestRating: agg.bestRating,
    worstRating: agg.worstRating,
    fetched_at: new Date().toISOString(),
    source: "github_actions_refresh",
  };
  fs.writeFileSync(cacheFile, JSON.stringify(body, null, 2) + "\n");
  log(`wrote ${path.relative(repoRoot, cacheFile)}`);
}

function readPreviousCache() {
  try {
    return JSON.parse(fs.readFileSync(cacheFile, "utf8"));
  } catch {
    return null;
  }
}

async function pushToBackend(agg) {
  const secret = (process.env.TRUSTPILOT_REFRESH_SECRET || "").trim();
  if (!secret) {
    log("TRUSTPILOT_REFRESH_SECRET not set — skipping backend push");
    return { skipped: true };
  }
  const base = (process.env.BACKEND_URL || "https://api.syrabit.ai").replace(/\/+$/, "");
  const url = `${base}/api/config/trustpilot/aggregate/refresh`;
  let resp;
  try {
    resp = await fetchWithTimeout(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Trustpilot-Refresh-Secret": secret,
        "User-Agent": "Syrabit.ai-Refresh/1.0 (+https://syrabit.ai)",
      },
      body: JSON.stringify({
        ratingValue: agg.ratingValue,
        ratingCount: agg.ratingCount,
        bestRating: agg.bestRating,
        worstRating: agg.worstRating,
        source: "github_actions_refresh",
      }),
    });
  } catch (e) {
    err(`backend push network error: ${e.message || e}`);
    return { ok: false, error: String(e) };
  }
  const text = await resp.text().catch(() => "");
  if (!resp.ok) {
    err(`backend push HTTP ${resp.status}: ${text.slice(0, 300)}`);
    return { ok: false, status: resp.status, body: text };
  }
  log(`backend push OK (${url}): ${text.slice(0, 200)}`);
  return { ok: true };
}

async function main() {
  const agg = await fetchFromTrustpilot();
  log(`fetched ${agg.ratingValue}★ (${agg.ratingCount} reviews) from trustpilot`);

  let cacheChanged = false;
  if (process.env.SKIP_CACHE_FILE_WRITE === "1") {
    log("SKIP_CACHE_FILE_WRITE=1 — not writing cache file");
  } else {
    const prev = readPreviousCache();
    if (
      !prev ||
      prev.ratingValue !== agg.ratingValue ||
      prev.ratingCount !== agg.ratingCount
    ) {
      writeCacheFile(agg);
      cacheChanged = true;
    } else {
      // Even when values match, refresh fetched_at so the next build
      // can prove the data is fresh. Idempotent for git purposes
      // because the diff still shows up — that's fine, it's exactly
      // one tiny diff per day at most.
      writeCacheFile(agg);
      cacheChanged = true;
    }
  }

  let pushResult = { skipped: true };
  if (process.env.SKIP_BACKEND_PUSH === "1") {
    log("SKIP_BACKEND_PUSH=1 — not pushing to backend");
  } else {
    pushResult = await pushToBackend(agg);
    if (pushResult.ok === false) {
      // Cache file may still have been written — exit 2 so CI flags
      // the partial success without losing the committed update.
      process.exit(2);
    }
  }

  log(
    `done — cacheChanged=${cacheChanged} backendPush=${pushResult.ok ? "ok" : (pushResult.skipped ? "skipped" : "failed")}`,
  );
}

main().catch((e) => {
  err(`FATAL ${e?.stack || e}`);
  process.exit(1);
});
