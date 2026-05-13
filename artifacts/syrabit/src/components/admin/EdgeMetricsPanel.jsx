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
import { Activity, Zap, BarChart2, RefreshCw, TrendingUp, Clock, AlertTriangle, ExternalLink, Settings, Check, X } from 'lucide-react';
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

/** Task #33 / Task #62 — Alert settings sub-panel rendered inside TitleInjectionGaps. */
export function AlertSettings({ token, onSaved }) {
  const [settings, setSettings]     = useState(null);
  const [loading, setLoading]       = useState(false);
  const [saving, setSaving]         = useState(false);
  const [error, setError]           = useState(null);
  const [saveError, setSaveError]   = useState(null);
  const [savedOk, setSavedOk]       = useState(false);
  const [pendingConfirm, setPendingConfirm] = useState(false);

  // Draft values edited by the admin.
  const [draftThreshold, setDraftThreshold] = useState('');
  const [draftDisabled, setDraftDisabled]   = useState(false);

  // Task #62 — inline validation derived from current draft value.
  // Empty string is treated as "not yet provided" and validated only on save;
  // any non-empty value that is not a whole integer ≥ 1 shows the error inline
  // next to the input so the admin sees the constraint as they type.
  // Note: Number() is used instead of parseInt() so that "1.5" is correctly
  // identified as non-integer (parseInt("1.5") would silently truncate to 1
  // and accept a value the backend Pydantic int field would reject with 422).
  const _thrNum = Number(draftThreshold.trim());
  const thresholdInputError =
    draftThreshold.trim() !== '' &&
    (!Number.isInteger(_thrNum) || _thrNum < 1)
      ? 'Must be a whole number ≥ 1'
      : null;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      const res = await axios.get(`${API_BASE}/admin/edge/spa-title-miss-settings`, {
        headers,
        withCredentials: true,
      });
      const body = res.data;
      if (!body.configured) {
        setError(body.reason || 'Edge not configured');
        return;
      }
      setSettings(body);
      setDraftThreshold(String(body.threshold ?? 50));
      setDraftDisabled(!!body.disabled);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || 'Request failed';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { load(); }, [load]);

  const commitSave = async () => {
    setPendingConfirm(false);
    setSaving(true);
    setSaveError(null);
    setSavedOk(false);
    try {
      // Use the same Number.isInteger check as thresholdInputError so "1.5"
      // is rejected here too (parseInt would silently truncate it to 1).
      const thr = Number(draftThreshold.trim());
      if (!Number.isInteger(thr) || thr < 1) {
        setSaveError('Threshold must be a whole number ≥ 1');
        return;
      }
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      await axios.patch(
        `${API_BASE}/admin/edge/spa-title-miss-settings`,
        { threshold: thr, disabled: draftDisabled },
        { headers, withCredentials: true },
      );
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 3000);
      // Reload to reflect the new effective values from KV.
      await load();
      if (onSaved) onSaved();
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || 'Save failed';
      setSaveError(msg);
    } finally {
      setSaving(false);
    }
  };

  const save = () => {
    setSaveError(null);
    // Use the same Number.isInteger check as thresholdInputError so "1.5"
    // is caught here too (parseInt would silently truncate to 1).
    const thr = Number(draftThreshold.trim());
    if (!Number.isInteger(thr) || thr < 1) {
      // Task #62 — inline error next to the input already communicates the
      // constraint; no network request is made and no duplicate saveError is
      // set here so the bottom status area stays uncluttered.
      return;
    }
    if (draftDisabled) {
      setPendingConfirm(true);
      return;
    }
    commitSave();
  };

  if (loading && !settings) {
    return (
      <div className="flex items-center gap-1.5 py-1">
        <RefreshCw size={10} className="animate-spin text-gray-300" />
        <span className="text-[10px] text-gray-400">Loading settings…</span>
      </div>
    );
  }

  if (error) {
    return (
      <p className="text-[11px] text-amber-600 bg-amber-50 border border-amber-100 rounded px-2 py-1.5 mt-1">
        Settings unavailable: {error}
      </p>
    );
  }

  if (!settings) return null;

  const isOverride = settings.kv_override_set;

  return (
    <div className="mt-2 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2.5 space-y-2">
      <p className="text-[10px] uppercase tracking-wider text-gray-400 font-semibold mb-1">
        Alert Settings
        {isOverride && (
          <span className="ml-1.5 normal-case bg-violet-100 text-violet-600 px-1.5 py-0.5 rounded-full text-[9px] font-bold">
            KV override active
          </span>
        )}
        {!isOverride && (
          <span className="ml-1.5 normal-case bg-gray-200 text-gray-500 px-1.5 py-0.5 rounded-full text-[9px]">
            env-var defaults
          </span>
        )}
      </p>

      {/* Threshold field — Task #62: inline error shown as the admin types */}
      <div className="flex items-center gap-2">
        <label className="text-[11px] text-gray-600 w-32 flex-shrink-0">
          Hit threshold
        </label>
        <div className="flex flex-col gap-0.5">
          <input
            type="number"
            min={1}
            value={draftThreshold}
            onChange={(e) => setDraftThreshold(e.target.value)}
            data-testid="threshold-input"
            aria-describedby={thresholdInputError ? 'threshold-error' : undefined}
            aria-invalid={!!thresholdInputError}
            className={`w-20 rounded border px-2 py-0.5 text-xs font-mono text-gray-800 focus:outline-none focus:ring-1 ${
              thresholdInputError
                ? 'border-red-400 bg-red-50 focus:ring-red-400'
                : 'border-gray-200 bg-white focus:ring-amber-400'
            }`}
          />
          {thresholdInputError && (
            <span
              id="threshold-error"
              data-testid="threshold-error"
              className="text-[10px] text-red-500 leading-tight"
            >
              {thresholdInputError}
            </span>
          )}
        </div>
        <span className="text-[10px] text-gray-400">bot hits / 24 h to alert</span>
      </div>

      {/* Disabled toggle */}
      <div className="flex items-center gap-2">
        <label className="text-[11px] text-gray-600 w-32 flex-shrink-0">
          Alert disabled
        </label>
        <button
          onClick={() => {
            setDraftDisabled((v) => !v);
            setPendingConfirm(false);
          }}
          className={`relative inline-flex h-4 w-8 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none ${
            draftDisabled ? 'bg-red-400' : 'bg-emerald-400'
          }`}
          role="switch"
          aria-checked={draftDisabled}
        >
          <span
            className={`inline-block h-3 w-3 transform rounded-full bg-white shadow transition-transform ${
              draftDisabled ? 'translate-x-4' : 'translate-x-0'
            }`}
          />
        </button>
        <span className={`text-[10px] font-semibold ${draftDisabled ? 'text-red-500' : 'text-emerald-600'}`}>
          {draftDisabled ? 'Alert paused' : 'Alert active'}
        </span>
      </div>

      {/* Save / status */}
      <div className="flex items-center gap-2 pt-0.5 flex-wrap">
        {pendingConfirm ? (
          <div
            data-testid="alert-settings-confirm-dialog"
            className="flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-2.5 py-1.5"
          >
            <AlertTriangle size={10} className="text-red-500 flex-shrink-0" />
            <span className="text-[11px] text-red-700 font-semibold">Confirm pause?</span>
            <button
              data-testid="alert-settings-confirm-btn"
              onClick={commitSave}
              disabled={saving}
              className="flex items-center gap-1 px-2 py-0.5 rounded bg-red-500 text-white text-[11px] font-semibold hover:bg-red-600 disabled:opacity-50 transition-colors"
            >
              {saving ? <RefreshCw size={9} className="animate-spin" /> : <Check size={9} />}
              Confirm
            </button>
            <button
              data-testid="alert-settings-cancel-btn"
              onClick={() => setPendingConfirm(false)}
              className="flex items-center gap-1 px-2 py-0.5 rounded bg-gray-200 text-gray-700 text-[11px] font-semibold hover:bg-gray-300 transition-colors"
            >
              <X size={9} /> Cancel
            </button>
          </div>
        ) : (
          <button
            data-testid="alert-settings-save-btn"
            onClick={save}
            disabled={saving || !!thresholdInputError}
            data-testid="save-button"
            className="flex items-center gap-1 px-2.5 py-1 rounded bg-amber-500 text-white text-[11px] font-semibold hover:bg-amber-600 disabled:opacity-50 transition-colors"
          >
            {saving ? <RefreshCw size={9} className="animate-spin" /> : <Check size={9} />}
            Save
          </button>
        )}
        {savedOk && (
          <span className="text-[11px] text-emerald-600 flex items-center gap-1">
            <Check size={10} /> Saved to KV
          </span>
        )}
        {saveError && (
          <span className="text-[11px] text-red-500 flex items-center gap-1">
            <X size={10} /> {saveError}
          </span>
        )}
      </div>

      {isOverride && settings.env_threshold !== undefined && (
        <p className="text-[10px] text-gray-400 pt-0.5">
          Env-var defaults: threshold={settings.env_threshold}, disabled={String(settings.env_disabled ?? false)}.
          KV values override them at runtime.
        </p>
      )}
    </div>
  );
}

const MISS_RANGES = [
  { label: '1 h',  value: '1h'  },
  { label: '6 h',  value: '6h'  },
  { label: '24 h', value: '24h' },
  { label: '7 d',  value: '7d'  },
];

/** Task #39 — compact tag-rewrite status row rendered inside TitleInjectionGaps. */
function TagRewriteStatus({ tagHandlers }) {
  if (!tagHandlers || Object.keys(tagHandlers).length === 0) return null;

  const tags = [
    { key: 'og_image',          label: 'og:image' },
    { key: 'og_image_alt',      label: 'og:image:alt' },
    { key: 'twitter_image',     label: 'twitter:image' },
    { key: 'twitter_image_alt', label: 'twitter:image:alt' },
  ];

  return (
    <div className="mt-2 rounded-lg border border-emerald-100 bg-emerald-50 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-emerald-600 font-semibold mb-1.5">
        Tag Rewrite Coverage
      </p>
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        {tags.map(({ key, label }) => {
          const active = tagHandlers[key] === true;
          return (
            <span
              key={key}
              className={`inline-flex items-center gap-1 text-[11px] font-mono ${
                active ? 'text-emerald-700' : 'text-amber-600'
              }`}
            >
              {active
                ? <Check size={10} className="flex-shrink-0" />
                : <X size={10} className="flex-shrink-0" />
              }
              {label}
            </span>
          );
        })}
      </div>
      <p className="text-[10px] text-emerald-500 mt-1.5 leading-snug">
        All tags above are rewritten on every bot-crawled route matched by{' '}
        <code className="font-mono bg-emerald-100 px-0.5 rounded text-[9px]">_resolveSpaRouteMeta</code>.
        Uncovered routes below still serve the generic fallback.
      </p>
    </div>
  );
}

/** Title Injection Gaps sub-section — Task #12 / Task #44. */
function TitleInjectionGaps({ token }) {
  // scanResult holds the full enriched response from the backend proxy:
  // { range, threshold, alert_disabled, gaps_found, gaps_above_threshold, gaps[] }
  const [scanResult, setScanResult]     = useState(null);
  const [tagHandlers, setTagHandlers]   = useState(null);
  const [loading, setLoading]           = useState(false);
  const [error, setError]               = useState(null);
  const [missRange, setMissRange]       = useState('24h');
  const [showSettings, setShowSettings] = useState(false);

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
      // Task #44: backend now unpacks the enriched edge response.
      // `body.gaps` is null when the edge is unreachable.
      if (body.gaps === null || body.gaps === undefined) {
        setError(body.reason || 'Could not fetch title-miss data');
        return;
      }
      setScanResult(body);
      // Task #39 — persist tag_handlers so the coverage section can render.
      setTagHandlers(body.tag_handlers ?? null);
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.response?.data?.error || e?.message || 'Request failed';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { loadMisses(missRange); }, [loadMisses, missRange]);

  // Derive the preview origin from the current window so it works in
  // staging and local dev without a hardcoded production URL.
  const previewOrigin =
    typeof window !== 'undefined' && window.location.hostname !== 'localhost'
      ? window.location.origin.replace(/^https?:\/\/[^.]+\./, 'https://syrabit.')
      : 'https://syrabit.ai';

  const gaps = scanResult?.gaps ?? [];

  return (
    <div className="border-t border-gray-100 pt-3 mt-1">
      {/* Header row */}
      <div className="flex items-center justify-between gap-2 mb-2">
        <div className="flex items-center gap-1.5">
          <AlertTriangle size={12} className="text-amber-500" />
          <p className="text-[10px] uppercase tracking-wider text-gray-500 font-semibold">
            Title Injection Gaps
          </p>
          {scanResult !== null && scanResult.gaps_above_threshold > 0 && (
            <span className="text-[10px] bg-amber-100 text-amber-700 rounded-full px-1.5 py-0.5 font-bold">
              {scanResult.gaps_above_threshold}
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
          {/* Scan Now / Refresh button */}
          <button
            onClick={() => loadMisses(missRange)}
            disabled={loading}
            className="ml-1 flex items-center gap-1 px-2 py-0.5 rounded bg-amber-50 border border-amber-200 text-amber-700 text-[10px] font-semibold hover:bg-amber-100 disabled:opacity-40 transition-colors"
            title="Scan Now — re-fetch title gaps from the edge worker"
          >
            <RefreshCw size={9} className={loading ? 'animate-spin' : ''} />
            {loading ? 'Scanning…' : 'Scan Now'}
          </button>
          {/* Task #33 — toggle alert settings panel */}
          <button
            onClick={() => setShowSettings((v) => !v)}
            className={`ml-0.5 p-0.5 rounded transition-colors ${
              showSettings
                ? 'text-amber-600 bg-amber-50'
                : 'text-gray-400 hover:text-gray-600 hover:bg-gray-100'
            }`}
            title="Alert settings (threshold & on/off)"
          >
            <Settings size={10} />
          </button>
        </div>
      </div>

      {/* Task #44 — counters + threshold summary row */}
      {scanResult !== null && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
          <span className="text-[10px] text-gray-500">
            <span className="font-semibold text-gray-700">{scanResult.gaps_found}</span> paths uncovered
          </span>
          <span className="text-[10px] text-gray-400">·</span>
          <span className="text-[10px] text-gray-500">
            <span className="font-semibold text-amber-700">{scanResult.gaps_above_threshold}</span> above threshold
          </span>
          {scanResult.threshold != null && (
            <>
              <span className="text-[10px] text-gray-400">·</span>
              <span className="text-[10px] text-gray-500">
                threshold: <span className="font-mono font-semibold text-gray-700">{scanResult.threshold}</span> bot hits
              </span>
            </>
          )}
          {scanResult.alert_disabled && (
            <>
              <span className="text-[10px] text-gray-400">·</span>
              <span className="text-[10px] bg-red-100 text-red-600 px-1.5 py-0.5 rounded-full font-semibold">
                alert paused
              </span>
            </>
          )}
        </div>
      )}

      {/* Task #33 — collapsible alert settings panel */}
      {showSettings && (
        <AlertSettings
          token={token}
          onSaved={() => loadMisses(missRange)}
        />
      )}

      {/* Task #39 — tag rewrite coverage: shown once data loads, above errors */}
      {tagHandlers && !error && <TagRewriteStatus tagHandlers={tagHandlers} />}

      {error && (
        <p className="text-[11px] text-amber-600 bg-amber-50 border border-amber-100 rounded px-2 py-1.5">
          {error}
        </p>
      )}

      {!error && loading && scanResult === null && (
        <div className="flex justify-center py-3">
          <RefreshCw size={14} className="animate-spin text-gray-300" />
        </div>
      )}

      {!error && scanResult !== null && gaps.length === 0 && (
        <p className="text-[11px] text-gray-400 text-center py-2">
          No paths above threshold in this window.
        </p>
      )}

      {!error && scanResult !== null && gaps.length > 0 && (
        <div className="space-y-0.5">
          <div className="grid grid-cols-[1fr_auto_auto] gap-x-2 px-2 mb-1">
            <span className="text-[10px] text-gray-400 uppercase tracking-wider">Path</span>
            <span className="text-[10px] text-gray-400 uppercase tracking-wider text-right">Bot hits</span>
            <span className="text-[10px] text-gray-400 uppercase tracking-wider text-right">Suggested title</span>
          </div>
          {gaps.map((m) => (
            <div
              key={m.pathname}
              className="grid grid-cols-[1fr_auto_auto] gap-x-2 items-center rounded px-2 py-1 hover:bg-amber-50 group transition-colors"
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
              <span className="text-[11px] font-mono text-amber-700 font-semibold text-right flex-shrink-0">
                {m.count.toLocaleString()}
              </span>
              {m.suggested_title ? (
                <span
                  className="text-[10px] text-gray-500 italic text-right flex-shrink-0 max-w-[140px] truncate"
                  title={m.suggested_title}
                >
                  {m.suggested_title}
                </span>
              ) : (
                <span />
              )}
            </div>
          ))}
          <p className="text-[10px] text-gray-400 mt-1.5 px-2">
            Add these paths to{' '}
            <code className="font-mono bg-gray-100 px-0.5 rounded text-[9px]">_resolveSpaRouteMeta</code>
            {' '}to fix SEO gaps.
          </p>
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

