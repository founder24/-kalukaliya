/**
 * Task #897 — Bot HTML cache rolling-hour hit-rate panel.
 *
 * The edge worker (Task #885) records hit/miss/304/fallback counters
 * per 5-minute bucket over a rolling hour and exposes the totals +
 * per-bucket history under `bot_cache:` in the
 * `/api/edge/kv-usage` payload (proxied to the dashboard via
 * `/admin/kv-health`).
 *
 * This panel surfaces that block prominently in the admin
 * notification-prefs collapsible so a deploy that drifts the
 * BOT_HTML_CACHE key — silently dropping crawler hit-rate from ~95%
 * to 0% — is visible inside one bucket window without anyone having
 * to curl the edge endpoint. The 60% warning threshold mirrors the
 * docstring on workers/edge-proxy/src/bot-cache-stats.ts (a value
 * below ~0.6 indicates either a cache-key drift, a high churn rate
 * of freshly-published pages, or an aggressive crawler hitting cold
 * URLs).
 *
 * Props:
 *   - kvHealth: the same `kvHealth` state the parent already loads
 *     via /admin/kv-health. `null` while loading; `{ configured,
 *     reason?, snapshot? }` once the request settles. The panel
 *     reads `kvHealth.snapshot.bot_cache`; when that block is
 *     missing it shows an "unavailable" placeholder instead of
 *     crashing the dashboard's ErrorBoundary.
 */
import React from 'react';

const WARN_THRESHOLD = 0.6;

const SPARK_W = 160;
const SPARK_H = 32;
const PAD_X = 2;
const PAD_Y = 2;
const INNER_W = SPARK_W - PAD_X * 2;
const INNER_H = SPARK_H - PAD_Y * 2;

function fmtPct(v) {
  return `${(Math.max(0, Math.min(1, v)) * 100).toFixed(1)}%`;
}

export default function BotCachePanel({ kvHealth }) {
  const botCache = kvHealth?.snapshot?.bot_cache;

  if (kvHealth === null) {
    return (
      <div
        className="mb-3 pb-3 border-b border-gray-200"
        data-testid="notif-prefs-bot-cache"
      >
        <Header />
        <div className="text-[10px] text-gray-400">Loading…</div>
      </div>
    );
  }

  if (!botCache) {
    return (
      <div
        className="mb-3 pb-3 border-b border-gray-200"
        data-testid="notif-prefs-bot-cache"
      >
        <Header />
        <div
          className="text-[10px] text-gray-400"
          data-testid="notif-prefs-bot-cache-unavailable"
        >
          Bot cache telemetry not available
          {kvHealth?.reason ? ` — ${kvHealth.reason}` : ''}
          . Once the edge worker has served at least one crawler
          request through BOT_HTML_CACHE the rolling hit-rate will
          appear here.
        </div>
      </div>
    );
  }

  const hit = botCache.hit ?? 0;
  const miss = botCache.miss ?? 0;
  const cond = botCache.conditional_304 ?? 0;
  const fallback = botCache.fallback ?? 0;
  const denom = hit + miss + fallback;
  // Trust the worker's hit_rate when present — it already excludes
  // 304s from the denominator (a successful freshness revalidation
  // shouldn't be charged as a miss). Fall back to the local
  // computation only when the worker omitted the field for some
  // reason (older deploy, partial response).
  const rate =
    typeof botCache.hit_rate === 'number'
      ? botCache.hit_rate
      : denom > 0
        ? hit / denom
        : 0;
  const noTraffic = denom === 0 && cond === 0;
  const warn = !noTraffic && rate < WARN_THRESHOLD;

  const rateTone = noTraffic
    ? 'text-gray-400'
    : warn
      ? 'text-red-600'
      : 'text-emerald-600';
  const badgeCls = noTraffic
    ? 'bg-gray-100 text-gray-500 ring-gray-200'
    : warn
      ? 'bg-red-100 text-red-700 ring-red-200'
      : 'bg-emerald-100 text-emerald-700 ring-emerald-200';
  const badgeLabel = noTraffic ? 'NO TRAFFIC' : warn ? 'WARNING' : 'HEALTHY';
  const containerCls = warn
    ? 'bg-red-50 ring-red-200'
    : noTraffic
      ? 'bg-gray-50 ring-gray-200'
      : 'bg-emerald-50 ring-emerald-200';

  const buckets = Array.isArray(botCache.buckets) ? botCache.buckets : [];
  const perBucketRates = buckets.map((b) => {
    const h = b.hit ?? 0;
    const m = b.miss ?? 0;
    const f = b.fallback ?? 0;
    const d = h + m + f;
    return d > 0 ? h / d : null;
  });
  const N = perBucketRates.length;
  const stepX = N > 1 ? INNER_W / (N - 1) : 0;
  const yFor = (r) => {
    if (r == null) return null;
    const clamped = Math.max(0, Math.min(1, r));
    return PAD_Y + (1 - clamped) * INNER_H;
  };

  // Build a polyline path that breaks across no-traffic buckets via
  // a fresh "M" instead of an interpolated "L" (otherwise the gap
  // would render as a misleadingly straight line through 0).
  const pathSegments = [];
  let currentSeg = '';
  perBucketRates.forEach((r, i) => {
    const y = yFor(r);
    if (y == null) {
      if (currentSeg) {
        pathSegments.push(currentSeg);
        currentSeg = '';
      }
      return;
    }
    const x = PAD_X + i * stepX;
    if (!currentSeg) {
      currentSeg = `M ${x.toFixed(2)} ${y.toFixed(2)}`;
    } else {
      currentSeg += ` L ${x.toFixed(2)} ${y.toFixed(2)}`;
    }
  });
  if (currentSeg) pathSegments.push(currentSeg);
  const pathD = pathSegments.join(' ');

  const strokeColor = warn ? '#dc2626' : '#059669';
  const warnY = PAD_Y + (1 - WARN_THRESHOLD) * INNER_H;

  return (
    <div
      className="mb-3 pb-3 border-b border-gray-200"
      data-testid="notif-prefs-bot-cache"
    >
      <Header />
      <div
        className={`rounded-md ring-1 px-2 py-2 ${containerCls}`}
        data-testid="notif-prefs-bot-cache-panel"
      >
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-baseline gap-2">
            <span
              className={`text-2xl font-semibold tabular-nums ${rateTone}`}
              data-testid="notif-prefs-bot-cache-rate"
            >
              {noTraffic ? '—' : fmtPct(rate)}
            </span>
            <span className="text-[10px] text-gray-500">hit rate</span>
          </div>
          <span
            className={`text-[9px] uppercase tracking-wide font-semibold px-1.5 py-0.5 rounded ring-1 ${badgeCls}`}
            data-testid="notif-prefs-bot-cache-badge"
          >
            {badgeLabel}
          </span>
        </div>

        <div className="mt-1.5">
          <svg
            width={SPARK_W}
            height={SPARK_H}
            viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
            role="img"
            aria-label={`Bot cache hit rate sparkline, ${N} buckets`}
            data-testid="notif-prefs-bot-cache-sparkline"
            className="block"
          >
            {/* 60% warning threshold reference line */}
            <line
              x1={PAD_X}
              x2={SPARK_W - PAD_X}
              y1={warnY}
              y2={warnY}
              stroke="#9ca3af"
              strokeWidth="0.5"
              strokeDasharray="2 2"
            />
            {pathD && (
              <path
                d={pathD}
                fill="none"
                stroke={strokeColor}
                strokeWidth="1.5"
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            )}
            {perBucketRates.map((r, i) => {
              const y = yFor(r);
              if (y == null) return null;
              const x = PAD_X + i * stepX;
              return (
                <circle key={i} cx={x} cy={y} r={1.5} fill={strokeColor}>
                  <title>
                    {`${new Date(buckets[i].ts).toLocaleTimeString()} · ${fmtPct(r)}`}
                  </title>
                </circle>
              );
            })}
          </svg>
        </div>

        <div className="grid grid-cols-4 gap-1 mt-2 text-[10px] text-gray-500">
          <Stat label="hit" value={hit} testId="notif-prefs-bot-cache-hit" tone="text-emerald-700 font-medium" />
          <Stat label="miss" value={miss} testId="notif-prefs-bot-cache-miss" />
          <Stat label="304" value={cond} testId="notif-prefs-bot-cache-cond" />
          <Stat
            label="fallback"
            value={fallback}
            testId="notif-prefs-bot-cache-fallback"
            tone={fallback > 0 ? 'text-amber-700 font-medium' : undefined}
          />
        </div>

        {warn && (
          <div
            className="text-[10px] text-red-700 mt-1.5"
            data-testid="notif-prefs-bot-cache-warning"
          >
            Hit rate dropped below 60% — check whether a recent deploy
            drifted the BOT_HTML_CACHE key or a crawler is hammering
            cold URLs.
          </div>
        )}
      </div>
    </div>
  );
}

function Header() {
  return (
    <div className="flex items-center justify-between mb-1.5">
      <label className="text-[10px] text-gray-500 font-medium">
        Bot HTML cache — rolling-hour hit rate
      </label>
      <span className="text-[10px] text-gray-400">last 60 min · 5-min buckets</span>
    </div>
  );
}

function Stat({ label, value, testId, tone }) {
  return (
    <div className="flex flex-col" data-testid={testId}>
      <span className="uppercase text-[9px]">{label}</span>
      <span className={`tabular-nums ${tone || 'text-gray-700'}`}>
        {value.toLocaleString()}
      </span>
    </div>
  );
}
