/**
 * EdgeMetricsPanel — Task #109 Phase 5.
 *
 * Displays Workers Analytics Engine metrics for the syrabit-edge worker:
 *   - Edge cache hit rate (last N hours)
 *   - Total request count + AI request count
 *   - Average response time
 *   - Top chapters by request volume
 *   - RAG query breakdown by AI provider
 *
 * Data source: GET /api/admin/edge-analytics?range=<range>  (Flask backend proxy)
 *   The backend route (routes/admin_edge_analytics.py) adds X-Edge-Admin-Secret
 *   (D1_SYNC_SECRET) and forwards to the edge worker at /api/edge/analytics.
 *   The edge worker queries the Analytics Engine GraphQL API using CF_ANALYTICS_TOKEN.
 *   Only populated after the worker has been redeployed with the
 *   [[analytics_engine_datasets]] ANALYTICS binding (wrangler.toml Phase 5).
 */
import { useState, useEffect, useCallback } from 'react';
import { Activity, Zap, BarChart2, RefreshCw, TrendingUp, Clock, AlertTriangle, ExternalLink } from 'lucide-react';
import axios from 'axios';
import { API_BASE } from '@/utils/api';

const RANGES = [
  { label: '1 h',  value: '1h'  },
  { label: '6 h',  value: '6h'  },
  { label: '24 h', value: '24h' },
  { label: '7 d',  value: '7d'  },
];

function StatCard({ icon: Icon, label, value, sub, color = 'blue' }) {
  const palette = {
    blue:   'bg-blue-50 text-blue-600 border-blue-100',
    green:  'bg-emerald-50 text-emerald-600 border-emerald-100',
    violet: 'bg-violet-50 text-violet-600 border-violet-100',
    amber:  'bg-amber-50 text-amber-600 border-amber-100',
  };
  return (
    <div className={`rounded-xl border px-4 py-3 flex items-start gap-3 ${palette[color]}`}>
      <Icon size={16} className="mt-0.5 flex-shrink-0 opacity-70" />
      <div className="min-w-0">
        <p className="text-[10px] uppercase tracking-wider opacity-60 mb-0.5">{label}</p>
        <p className="text-xl font-bold font-mono leading-tight">{value}</p>
        {sub && <p className="text-[11px] opacity-60 mt-0.5">{sub}</p>}
      </div>
    </div>
  );
}

function MiniBar({ label, value, max }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-500 w-32 truncate flex-shrink-0" title={label}>{label || '(unknown)'}</span>
      <div className="flex-1 h-2 rounded-full bg-gray-100">
        <div className="h-2 rounded-full bg-violet-400" style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs font-mono text-gray-600 w-10 text-right flex-shrink-0">{value.toLocaleString()}</span>
    </div>
  );
}

/** Title Injection Gaps sub-section — Task #12. */
function TitleInjectionGaps({ token, range }) {
  const [misses, setMisses]       = useState(null);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState(null);
  const [missRange, setMissRange] = useState('7d');

  const loadMisses = useCallback(async (r) => {
    setLoading(true);
    setError(null);
    try {
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const res = await axios.get(`${API_BASE}/admin/edge/spa-title-misses`, {
        params: { range: r },
        headers,
        withCredentials: true,
      });
      const body = res.data;
      if (!body.configured) {
        setError(body.reason || 'Edge analytics not configured');
        return;
      }
      if (body.misses === null) {
        setError(body.reason || 'Could not fetch title-miss data');
        return;
      }
      setMisses(body.misses);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || 'Request failed';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { loadMisses(missRange); }, [loadMisses, missRange]);

  const MISS_RANGES = [
    { label: '24 h', value: '24h' },
    { label: '7 d',  value: '7d'  },
  ];

  // Derive the preview origin from the current window so it works in
  // staging and local dev without a hardcoded production URL.
  const previewOrigin =
    typeof window !== 'undefined' && window.location.hostname !== 'localhost'
      ? window.location.origin.replace(/^https?:\/\/[^.]+\./, 'https://syrabit.')
      : 'https://syrabit.ai';

  return (
    <div className="border-t border-gray-100 pt-3 mt-1">
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-1.5">
          <AlertTriangle size={12} className="text-amber-500" />
          <p className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
            Title Injection Gaps
          </p>
          {misses !== null && misses.length > 0 && (
            <span className="text-[10px] bg-amber-100 text-amber-700 rounded-full px-1.5 py-0.5 font-bold">
              {misses.length}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {MISS_RANGES.map((r) => (
            <button
              key={r.value}
              onClick={() => setMissRange(r.value)}
              className={`px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors ${
                missRange === r.value
                  ? 'bg-amber-100 text-amber-700'
                  : 'text-gray-400 hover:bg-gray-100'
              }`}
            >
              {r.label}
            </button>
          ))}
          <button
            onClick={() => loadMisses(missRange)}
            disabled={loading}
            className="ml-0.5 p-0.5 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 disabled:opacity-40"
            title="Refresh"
          >
            <RefreshCw size={10} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {error && (
        <p className="text-[11px] text-amber-600 bg-amber-50 border border-amber-100 rounded px-2 py-1.5">
          {error}
        </p>
      )}

      {!error && loading && misses === null && (
        <div className="flex justify-center py-3">
          <RefreshCw size={14} className="animate-spin text-gray-300" />
        </div>
      )}

      {!error && misses !== null && misses.length === 0 && (
        <p className="text-[11px] text-gray-400 text-center py-2">
          No uncovered paths in this window.
        </p>
      )}

      {!error && misses !== null && misses.length > 0 && (
        <div className="space-y-0.5">
          <p className="text-[10px] text-gray-400 mb-1">
            Bot-crawled paths without a custom title — add these to{' '}
            <code className="font-mono bg-gray-100 px-0.5 rounded text-[9px]">_resolveSpaRouteMeta</code>
            {' '}to fix SEO.
          </p>
          {misses.map((m) => (
            <div
              key={m.pathname}
              className="flex items-center justify-between gap-2 rounded px-2 py-1 hover:bg-amber-50 group transition-colors"
            >
              <a
                href={`${previewOrigin}${m.pathname}`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-[11px] font-mono text-gray-700 hover:text-amber-700 truncate min-w-0"
                title={m.pathname}
              >
                <span className="truncate">{m.pathname}</span>
                <ExternalLink size={9} className="flex-shrink-0 opacity-0 group-hover:opacity-60 transition-opacity" />
              </a>
              <span className="text-[11px] font-mono text-amber-700 font-semibold flex-shrink-0">
                {m.count.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function EdgeMetricsPanel({ token }) {
  const [range, setRange]     = useState('24h');
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);

  const load = useCallback(async (r) => {
    setLoading(true);
    setError(null);
    try {
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const res = await axios.get(`${API_BASE}/admin/edge-analytics`, {
        params: { range: r },
        headers,
        withCredentials: true,
      });
      const body = res.data;
      if (!body.configured) {
        setError(body.reason || 'Edge analytics not configured');
        return;
      }
      if (!body.metrics) {
        setError(body.reason || 'No metrics returned');
        return;
      }
      setData(body.metrics);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || 'Request failed';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    load(range);
    const timer = setInterval(() => load(range), 60_000);
    return () => clearInterval(timer);
  }, [load, range]);

  const hitRatePct = data ? Math.round(data.cacheHitRate * 100) : null;
  const maxChapter = data?.topChapters?.[0]?.requests ?? 1;
  const maxProvider = data?.ragByProvider?.[0]?.requests ?? 1;

  return (
    <div className="rounded-xl border border-gray-200 bg-white shadow-sm p-4 space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <BarChart2 size={14} className="text-violet-500" />
          <p className="text-xs font-bold text-gray-700 uppercase tracking-wider">Edge Metrics (Analytics Engine)</p>
        </div>
        <div className="flex items-center gap-1">
          {RANGES.map((r) => (
            <button
              key={r.value}
              onClick={() => { setRange(r.value); load(r.value); }}
              className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors ${
                range === r.value
                  ? 'bg-violet-100 text-violet-700'
                  : 'text-gray-500 hover:bg-gray-100'
              }`}
            >
              {r.label}
            </button>
          ))}
          <button
            onClick={() => load(range)}
            disabled={loading}
            className="ml-1 p-1 rounded text-gray-400 hover:text-gray-600 hover:bg-gray-100 disabled:opacity-40"
            title="Refresh"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg bg-amber-50 border border-amber-200 p-3 text-xs text-amber-700">
          <strong>Analytics unavailable:</strong> {error}
          {error.includes('CF_ANALYTICS_TOKEN') && (
            <p className="mt-1 opacity-75">
              Set the secret: <code className="font-mono bg-amber-100 px-1 rounded">wrangler secret put CF_ANALYTICS_TOKEN</code>
            </p>
          )}
        </div>
      )}

      {!error && data && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <StatCard
              icon={TrendingUp}
              label="Cache Hit Rate"
              value={`${hitRatePct}%`}
              sub={`${data.cacheHits.toLocaleString()} hits`}
              color={hitRatePct >= 80 ? 'green' : hitRatePct >= 50 ? 'amber' : 'blue'}
            />
            <StatCard
              icon={Activity}
              label="Total Requests"
              value={data.totalRequests.toLocaleString()}
              sub={`last ${data.rangeLabel}`}
              color="blue"
            />
            <StatCard
              icon={Zap}
              label="AI Requests"
              value={data.aiRequests.toLocaleString()}
              sub="RAG + chat + quiz"
              color="violet"
            />
            <StatCard
              icon={Clock}
              label="Avg Response"
              value={`${data.avgResponseMs} ms`}
              sub="edge to client"
              color={data.avgResponseMs < 200 ? 'green' : data.avgResponseMs < 600 ? 'amber' : 'blue'}
            />
          </div>

          {data.topChapters?.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-2">Top Chapters</p>
              <div className="space-y-1.5">
                {data.topChapters.map((c) => (
                  <MiniBar key={c.chapterId} label={c.chapterId} value={c.requests} max={maxChapter} />
                ))}
              </div>
            </div>
          )}

          {data.ragByProvider?.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-gray-400 mb-2">RAG Volume by Provider</p>
              <div className="space-y-1.5">
                {data.ragByProvider.map((p) => (
                  <MiniBar key={p.provider} label={p.provider} value={p.requests} max={maxProvider} />
                ))}
              </div>
            </div>
          )}

          {data.topChapters?.length === 0 && data.ragByProvider?.length === 0 && (
            <p className="text-xs text-gray-400 text-center py-2">
              No data yet — metrics populate after the worker is redeployed with the ANALYTICS binding.
            </p>
          )}
        </>
      )}

      {!error && !data && !loading && (
        <p className="text-xs text-gray-400 text-center py-4">No data loaded.</p>
      )}

      {loading && !data && (
        <div className="flex items-center justify-center py-6">
          <RefreshCw size={18} className="animate-spin text-gray-300" />
        </div>
      )}

      {/* Task #12 — Title Injection Gaps: always shown below other metrics */}
      <TitleInjectionGaps token={token} />

      <p className="text-[10px] text-gray-300 text-right">
        Dataset: syrabit-edge-metrics · Phase 5 · Task #109
      </p>
    </div>
  );
}
