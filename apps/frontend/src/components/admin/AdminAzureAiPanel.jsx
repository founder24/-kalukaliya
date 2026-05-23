import React, { useCallback, useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import {
  Activity,
  AlertTriangle,
  Cloud,
  CloudOff,
  DollarSign,
  RefreshCw,
  ShieldCheck,
  Zap,
} from 'lucide-react';
import { API_BASE } from '@/utils/api';

/*
  AdminAzureAiPanel
  =================

  Phase 5b — Azure-native advanced features (Task #338).

  Single pane of glass for the ten Azure AI services lit up by
  `infra/azure/ai-services.tf`. Each row shows:

    - Per-feature toggle (POST /admin/azure/ai/toggle)
    - Throttle indicator (rolling 429 count over the last 15 min)
    - p50 / p95 latency on the data plane
    - Spend MTD against the per-feature budget
    - Failure mode (text drawn from docs/features/azure-native.md)

  The panel never holds Azure credentials directly — every call goes
  through the backend route `routes/admin_azure_ai.py` which uses
  the cron-tier managed identity to query Cognitive Services usage
  metrics + Application Insights customMetrics. The bundle only
  sees the pre-aggregated payload below.

  Backend contract — GET /admin/azure/ai/health:
    {
      asOf: "2026-05-04T12:34:56Z",
      compositeAlert: "ok" | "degraded" | "throttled" | "down",
      features: [
        {
          key:               "openai" | "speech" | "translator" | ...,
          displayName:       "Azure OpenAI",
          purpose:           "Additional LLM target wired into AI Gateway",
          enabled:           true,
          throttle15m:       0,            // 429 count last 15 min
          latencyP50Ms:      120,
          latencyP95Ms:      540,
          spendMtdUsd:       8.42,
          spendBudgetUsd:    50.00,
          lastErrorAt:       "2026-05-04T12:20:00Z" | null,
          lastErrorMessage:  "..." | null,
          failureMode:       "Falls back to direct OpenAI then Bedrock-Cohere",
          adminToggleKey:    "azure.openai.enabled"
        }, ...
      ],
      anomalies: [
        { ts: "2026-05-04T12:30:00Z", series: "credit_burn.openai", severity: 0.62 }
      ]
    }
*/

const FEATURE_ORDER = [
  'openai',
  'speech',
  'translator',
  'document_intel',
  'vision',
  'content_safety',
  'language',
  'search',
  'anomaly_detector',
  'personalizer',
];

const COMPOSITE_BADGE = {
  ok:        { wrap: 'bg-emerald-50 border-emerald-200 text-emerald-700', label: 'All Azure AI services green' },
  degraded:  { wrap: 'bg-amber-50 border-amber-200 text-amber-700',       label: 'Some services degraded' },
  throttled: { wrap: 'bg-amber-50 border-amber-200 text-amber-700',       label: 'Throttling detected' },
  down:      { wrap: 'bg-red-50 border-red-200 text-red-700',             label: 'One or more services down' },
};

function LatencyCell({ p50, p95 }) {
  if (p50 == null && p95 == null) return <span className="text-xs text-gray-400">—</span>;
  const color = (p95 ?? 0) > 1500 ? 'text-red-600' : (p95 ?? 0) > 600 ? 'text-amber-600' : 'text-emerald-600';
  return (
    <span className={`font-mono text-xs ${color}`}>
      {p50 ?? '—'} / <span className="font-bold">{p95 ?? '—'}</span> ms
    </span>
  );
}

function ThrottleCell({ count }) {
  if (count == null) return <span className="text-xs text-gray-400">—</span>;
  if (count === 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
        <ShieldCheck size={12} /> 0
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs text-amber-600 font-mono">
      <AlertTriangle size={12} /> {count}
    </span>
  );
}

function SpendCell({ spend, budget }) {
  if (spend == null) return <span className="text-xs text-gray-400">—</span>;
  const pct = budget ? Math.round((spend / budget) * 100) : 0;
  const color = pct > 90 ? 'text-red-600' : pct > 70 ? 'text-amber-600' : 'text-gray-600';
  return (
    <span className={`text-xs font-mono ${color}`}>
      ${spend.toFixed(2)} <span className="text-gray-400">/ ${budget?.toFixed(0) ?? '—'}</span>
    </span>
  );
}

export default function AdminAzureAiPanel({ token }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [savingKey, setSavingKey] = useState(null);

  const headers = useMemo(
    () => (token && token.split('.').length === 3 ? { Authorization: `Bearer ${token}` } : {}),
    [token],
  );

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await axios.get(`${API_BASE}/admin/azure/ai/health`, { headers, timeout: 15000 });
      setData(res.data);
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Failed to load Azure AI health');
    } finally {
      setLoading(false);
    }
  }, [headers]);

  useEffect(() => {
    fetchHealth();
    const t = setInterval(fetchHealth, 60_000);
    return () => clearInterval(t);
  }, [fetchHealth]);

  const toggleFeature = useCallback(
    async (feature) => {
      setSavingKey(feature.key);
      try {
        await axios.post(
          `${API_BASE}/admin/azure/ai/toggle`,
          { feature: feature.key, enabled: !feature.enabled },
          { headers, timeout: 10000 },
        );
        await fetchHealth();
      } catch (e) {
        setError(`Toggle failed for ${feature.displayName}: ${e?.response?.data?.detail || e.message}`);
      } finally {
        setSavingKey(null);
      }
    },
    [headers, fetchHealth],
  );

  const sortedFeatures = useMemo(() => {
    const features = data?.features ?? [];
    return [...features].sort(
      (a, b) => FEATURE_ORDER.indexOf(a.key) - FEATURE_ORDER.indexOf(b.key),
    );
  }, [data]);

  const composite = COMPOSITE_BADGE[data?.compositeAlert] || COMPOSITE_BADGE.ok;

  return (
    <section
      data-testid="admin-azure-ai-panel"
      className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm"
    >
      <header className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cloud size={18} className="text-blue-500" />
          <h2 className="text-base font-semibold text-gray-900">Azure-native AI features</h2>
          <span className={`ml-2 rounded-full border px-2 py-0.5 text-xs ${composite.wrap}`}>
            {composite.label}
          </span>
        </div>
        <button
          type="button"
          onClick={fetchHealth}
          disabled={loading}
          className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-1.5 text-xs text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          data-testid="azure-ai-refresh"
        >
          <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
          Refresh
        </button>
      </header>

      {error && (
        <div className="mb-3 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-700">
          {error}
        </div>
      )}

      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-gray-100 text-xs uppercase tracking-wider text-gray-400">
            <tr>
              <th className="py-2 pr-4">Feature</th>
              <th className="py-2 pr-4">Enabled</th>
              <th className="py-2 pr-4">Throttle (15 m)</th>
              <th className="py-2 pr-4">Latency p50/p95</th>
              <th className="py-2 pr-4">Spend MTD</th>
              <th className="py-2">Failure mode</th>
            </tr>
          </thead>
          <tbody>
            {sortedFeatures.length === 0 && !loading && (
              <tr>
                <td colSpan={6} className="py-6 text-center text-xs text-gray-400">
                  No data — backend route /admin/azure/ai/health returned an empty payload.
                </td>
              </tr>
            )}
            {sortedFeatures.map((f) => (
              <tr key={f.key} className="border-b border-gray-50">
                <td className="py-3 pr-4 align-top">
                  <div className="flex flex-col">
                    <span className="font-medium text-gray-800">{f.displayName}</span>
                    <span className="text-[11px] text-gray-400">{f.purpose}</span>
                  </div>
                </td>
                <td className="py-3 pr-4 align-top">
                  <button
                    type="button"
                    onClick={() => toggleFeature(f)}
                    disabled={savingKey === f.key}
                    data-testid={`azure-ai-toggle-${f.key}`}
                    className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition ${
                      f.enabled
                        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                        : 'border-gray-200 bg-gray-50 text-gray-500'
                    } ${savingKey === f.key ? 'opacity-60' : ''}`}
                  >
                    {f.enabled ? <Zap size={10} /> : <CloudOff size={10} />}
                    {f.enabled ? 'On' : 'Off'}
                  </button>
                </td>
                <td className="py-3 pr-4 align-top">
                  <ThrottleCell count={f.throttle15m} />
                </td>
                <td className="py-3 pr-4 align-top">
                  <LatencyCell p50={f.latencyP50Ms} p95={f.latencyP95Ms} />
                </td>
                <td className="py-3 pr-4 align-top">
                  <SpendCell spend={f.spendMtdUsd} budget={f.spendBudgetUsd} />
                </td>
                <td className="py-3 align-top text-xs text-gray-500">{f.failureMode}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data?.anomalies?.length > 0 && (
        <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-amber-700">
            <Activity size={12} /> Recent anomalies (Anomaly Detector)
          </div>
          <ul className="space-y-1 text-xs text-amber-700">
            {data.anomalies.map((a, i) => (
              <li key={i} className="font-mono">
                {a.ts} — <span className="font-bold">{a.series}</span> severity{' '}
                {a.severity?.toFixed?.(2) ?? a.severity}
              </li>
            ))}
          </ul>
        </div>
      )}

      <footer className="mt-3 flex items-center justify-between text-[11px] text-gray-400">
        <span>
          Source: <span className="font-mono">/admin/azure/ai/health</span>
        </span>
        {data?.asOf && <span>Last refresh: {data.asOf}</span>}
      </footer>
    </section>
  );
}
