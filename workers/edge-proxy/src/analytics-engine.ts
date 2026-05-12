/**
 * analytics-engine.ts — Task #109 Phase 5: Workers Analytics Engine query utility.
 *
 * Queries Cloudflare Workers Analytics Engine via the **GraphQL Analytics API**
 * (https://api.cloudflare.com/client/v4/graphql) to return edge metrics for the
 * syrabit-edge worker (dataset: syrabit-edge-metrics).
 *
 * The dataset is written to by the edge worker on every request (see index.ts
 * ANALYTICS.writeDataPoint calls). Fields written per request:
 *
 *   blob1  — cacheStatus:      "hit" | "miss" | "bypass" | "dynamic" | "pass"
 *   blob2  — chapterId:        slug of the chapter being viewed, or "" if not a chapter
 *   blob3  — aiProvider:       "workers-ai" | "backend" | "none"
 *   blob4  — pathname:         request pathname (first 64 chars)
 *   blob5  — rateLimitResult:  "ok" | "ai_limited" | "ip_limited"
 *
 *   double1 — responseTimeMs: end-to-end response time in milliseconds
 *   double2 — isAiRequest:    1 if the request hit an AI route, else 0
 *   double3 — httpStatus:     HTTP response status code
 *
 * GraphQL type name for dataset "syrabit-edge-metrics":
 *   syrabitEdgeMetricsAdaptiveGroups
 *
 * Required env:
 *   CF_ANALYTICS_TOKEN  — Cloudflare API token with Analytics: Read scope.
 *                         Set via: wrangler secret put CF_ANALYTICS_TOKEN
 */

const ACCOUNT_ID = 'd66e40eac539fff1db270fddf384a5ec';
const GQL_URL    = 'https://api.cloudflare.com/client/v4/graphql';

/** GraphQL type name: dataset name → camelCase + AdaptiveGroups suffix. */
const GQL_TYPE = 'syrabitEdgeMetricsAdaptiveGroups';

/** Number of seconds in each range option. */
const RANGE_SECONDS: Record<string, number> = {
  '1h':  3_600,
  '6h':  21_600,
  '24h': 86_400,
  '7d':  604_800,
};

export interface EdgeMetrics {
  rangeLabel: string;
  totalRequests: number;
  cacheHitRate: number;          // 0–1 fraction
  cacheHits: number;
  cacheMisses: number;
  aiRequests: number;
  avgResponseMs: number;
  topChapters: { chapterId: string; requests: number }[];
  ragByProvider: { provider: string; requests: number }[];
  rateLimitEvents: number;
}

/** ISO-8601 string for `secsAgo` seconds before now. */
function isoSecsAgo(secsAgo: number): string {
  return new Date(Date.now() - secsAgo * 1000).toISOString().replace(/\.\d+Z$/, 'Z');
}

interface GqlCacheRow {
  count: number;
  dimensions: { blob1: string };
  avg: { double1: number };
}
interface GqlChapterRow {
  count: number;
  dimensions: { blob2: string };
}
interface GqlProviderRow {
  count: number;
  dimensions: { blob3: string };
}
interface GqlRateLimitRow {
  count: number;
  dimensions: { blob5: string };
}

async function runGql<T>(token: string, query: string, variables: Record<string, unknown>): Promise<T> {
  const res = await fetch(GQL_URL, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type':  'application/json',
    },
    body: JSON.stringify({ query, variables }),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`CF GraphQL HTTP ${res.status}: ${text.slice(0, 300)}`);
  }

  const json = await res.json() as { data?: T; errors?: { message: string }[] };

  if (json.errors?.length) {
    const msgs = json.errors.map((e) => e.message).join('; ');
    throw new Error(`CF GraphQL error: ${msgs.slice(0, 400)}`);
  }
  if (!json.data) {
    throw new Error('CF GraphQL: empty response (no data field)');
  }
  return json.data;
}

export async function queryEdgeMetrics(
  token: string,
  range: string = '24h',
): Promise<EdgeMetrics> {
  const windowSecs = RANGE_SECONDS[range] ?? RANGE_SECONDS['24h'];
  const rangeLabel = range;
  const datetimeGeq = isoSecsAgo(windowSecs);
  const datetimeLeq = new Date().toISOString().replace(/\.\d+Z$/, 'Z');

  // ── 1. Cache status breakdown + avg response time ─────────────────────
  const cacheQuery = `
    query CacheBreakdown($accountTag: String!, $datetimeGeq: String!, $datetimeLeq: String!) {
      viewer {
        accounts(filter: { accountTag: $accountTag }) {
          ${GQL_TYPE}(
            filter: { datetime_geq: $datetimeGeq, datetime_leq: $datetimeLeq }
            limit: 20
            orderBy: [count_DESC]
          ) {
            count
            dimensions { blob1 }
            avg { double1 }
          }
        }
      }
    }
  `;

  // ── 2. Top chapters ────────────────────────────────────────────────────
  const chaptersQuery = `
    query TopChapters($accountTag: String!, $datetimeGeq: String!, $datetimeLeq: String!) {
      viewer {
        accounts(filter: { accountTag: $accountTag }) {
          ${GQL_TYPE}(
            filter: {
              datetime_geq: $datetimeGeq
              datetime_leq: $datetimeLeq
              blob2_neq: ""
            }
            limit: 5
            orderBy: [count_DESC]
          ) {
            count
            dimensions { blob2 }
          }
        }
      }
    }
  `;

  // ── 3. RAG requests by AI provider ────────────────────────────────────
  const ragQuery = `
    query RagByProvider($accountTag: String!, $datetimeGeq: String!, $datetimeLeq: String!) {
      viewer {
        accounts(filter: { accountTag: $accountTag }) {
          ${GQL_TYPE}(
            filter: {
              datetime_geq: $datetimeGeq
              datetime_leq: $datetimeLeq
              double2_gt: 0
            }
            limit: 10
            orderBy: [count_DESC]
          ) {
            count
            dimensions { blob3 }
          }
        }
      }
    }
  `;

  // ── 4. Rate limit events ───────────────────────────────────────────────
  const rateLimitQuery = `
    query RateLimitEvents($accountTag: String!, $datetimeGeq: String!, $datetimeLeq: String!) {
      viewer {
        accounts(filter: { accountTag: $accountTag }) {
          ${GQL_TYPE}(
            filter: {
              datetime_geq: $datetimeGeq
              datetime_leq: $datetimeLeq
              blob5_neq: "ok"
            }
            limit: 1
            orderBy: [count_DESC]
          ) {
            count
            dimensions { blob5 }
          }
        }
      }
    }
  `;

  const vars = { accountTag: ACCOUNT_ID, datetimeGeq, datetimeLeq };

  type AccountsWrapper<T> = { viewer: { accounts: { [key: string]: T[] }[] } };

  const [cacheData, chaptersData, ragData, rlData] = await Promise.all([
    runGql<AccountsWrapper<GqlCacheRow>>(token, cacheQuery, vars),
    runGql<AccountsWrapper<GqlChapterRow>>(token, chaptersQuery, vars),
    runGql<AccountsWrapper<GqlProviderRow>>(token, ragQuery, vars),
    runGql<AccountsWrapper<GqlRateLimitRow>>(token, rateLimitQuery, vars),
  ]);

  const cacheRows   = (cacheData.viewer.accounts[0]?.[GQL_TYPE]   ?? []) as GqlCacheRow[];
  const chapterRows = (chaptersData.viewer.accounts[0]?.[GQL_TYPE] ?? []) as GqlChapterRow[];
  const ragRows     = (ragData.viewer.accounts[0]?.[GQL_TYPE]     ?? []) as GqlProviderRow[];
  const rlRows      = (rlData.viewer.accounts[0]?.[GQL_TYPE]      ?? []) as GqlRateLimitRow[];

  // ── Aggregate cache metrics ──────────────────────────────────────────
  let totalRequests = 0;
  let cacheHits     = 0;
  let cacheMisses   = 0;
  let sumResponseMs = 0;
  let countForAvg   = 0;

  for (const row of cacheRows) {
    const status   = row.dimensions?.blob1 ?? '';
    const requests = row.count ?? 0;
    const avgMs    = row.avg?.double1 ?? 0;
    totalRequests += requests;
    if (status === 'hit')                          cacheHits   += requests;
    if (status === 'miss' || status === 'pass')    cacheMisses += requests;
    sumResponseMs += avgMs * requests;
    countForAvg   += requests;
  }

  const cacheHitRate  = totalRequests > 0 ? cacheHits / totalRequests : 0;
  const avgResponseMs = countForAvg > 0 ? sumResponseMs / countForAvg : 0;

  // ── Top chapters ─────────────────────────────────────────────────────
  const topChapters = chapterRows.map((r) => ({
    chapterId: r.dimensions?.blob2 ?? '',
    requests:  r.count ?? 0,
  }));

  // ── RAG by provider ──────────────────────────────────────────────────
  const aiRequests = ragRows.reduce((s, r) => s + (r.count ?? 0), 0);
  const ragByProvider = ragRows
    .filter((r) => (r.dimensions?.blob3 ?? '') !== 'none')
    .map((r) => ({
      provider: r.dimensions?.blob3 ?? '',
      requests: r.count ?? 0,
    }));

  // ── Rate limit events ────────────────────────────────────────────────
  const rateLimitEvents = rlRows.reduce((s, r) => s + (r.count ?? 0), 0);

  return {
    rangeLabel,
    totalRequests,
    cacheHitRate,
    cacheHits,
    cacheMisses,
    aiRequests,
    avgResponseMs: Math.round(avgResponseMs),
    topChapters,
    ragByProvider,
    rateLimitEvents,
  };
}

// ── Task #9 — SPA title-injection gap query ───────────────────────────────────
//
// Surfaces the top bot-crawled paths that did NOT match any pattern in
// _resolveSpaRouteMeta and therefore received the generic SPA <title>.
// Each such miss is recorded as an Analytics Engine datapoint with
//   blob1 = "spa_title_miss"
//   blob4 = pathname (first 64 chars)
// by the onMiss callback wired in _handleEdgeFetch (see index.ts Task #9).
//
// Callers: admin /api/admin/edge/spa-title-misses route (to be added in
// the backend) or the admin dashboard tile querying the CF AE directly
// via this helper.
// ─────────────────────────────────────────────────────────────────────────────

export interface SpaTitleMiss {
  /** Request pathname that reached the bot injector with no matching pattern. */
  pathname: string;
  /** Number of bot hits on that path within the requested time window. */
  count: number;
}

/**
 * Returns the top 20 bot-crawled SPA paths that did not match any title-
 * injection pattern, ordered by hit count descending. Use this to discover
 * which route families should be added to _resolveSpaRouteMeta.
 *
 * @param token  Cloudflare API token with Analytics: Read scope
 *               (CF_ANALYTICS_TOKEN secret).
 * @param range  One of "1h" | "6h" | "24h" | "7d". Defaults to "7d".
 */
export async function querySpaTitleMisses(
  token: string,
  range: string = '7d',
): Promise<SpaTitleMiss[]> {
  const windowSecs  = RANGE_SECONDS[range] ?? RANGE_SECONDS['7d'];
  const datetimeGeq = isoSecsAgo(windowSecs);
  const datetimeLeq = new Date().toISOString().replace(/\.\d+Z$/, 'Z');

  const query = `
    query SpaTitleMisses($accountTag: String!, $datetimeGeq: String!, $datetimeLeq: String!) {
      viewer {
        accounts(filter: { accountTag: $accountTag }) {
          ${GQL_TYPE}(
            filter: {
              datetime_geq: $datetimeGeq
              datetime_leq: $datetimeLeq
              blob1_eq: "spa_title_miss"
            }
            limit: 20
            orderBy: [count_DESC]
          ) {
            count
            dimensions { blob4 }
          }
        }
      }
    }
  `;

  type AccountsWrapper<T> = { viewer: { accounts: { [key: string]: T[] }[] } };
  interface MissRow { count: number; dimensions: { blob4: string } }

  const data = await runGql<AccountsWrapper<MissRow>>(
    token,
    query,
    { accountTag: ACCOUNT_ID, datetimeGeq, datetimeLeq },
  );

  const rows = (data.viewer.accounts[0]?.[GQL_TYPE] ?? []) as MissRow[];
  return rows
    .filter((r) => (r.dimensions?.blob4 ?? '') !== '')
    .map((r) => ({
      pathname: r.dimensions?.blob4 ?? '',
      count:    r.count ?? 0,
    }));
}
