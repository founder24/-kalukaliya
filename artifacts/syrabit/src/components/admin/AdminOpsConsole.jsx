import React, { useEffect, useState } from "react";
import { API_BASE } from "@/utils/api";

/**
 * Task #2 — 2026 blueprint Ops Console.
 *
 * Single-pane-of-glass dashboard rendered as the `ops` section inside
 * AdminPage. Hits `GET /api/admin/ops/console` once per mount and once
 * every 30 seconds and renders three tiles:
 *
 *   1. SLA ledger      — rolling 24h + 7d success rate, p50/p95
 *                        latency, target ms, and breach count per
 *                        canonical-specialist chain.
 *   2. Outage map      — per-provider 1h success/failure/latency from
 *                        `llm.get_llm_provider_stats(3600)` plus the
 *                        circuit-breaker state, surfaced as a single
 *                        status pill (healthy / degraded / down /
 *                        open / unknown).
 *   3. Toggles viewer  — read-only listing of operator env knobs,
 *                        founder-locked degradation thresholds, and
 *                        the per-feature routing-pool snapshot
 *                        (provider · weight · share% · role) sourced
 *                        from `routes/admin_routing_config._build_pool`.
 *
 * The component is intentionally read-only: the corresponding writes
 * still flow through the existing `/api/admin/settings` panel.
 */
const STATUS_PILL_CLASS = {
  healthy:  "bg-green-100 text-green-800",
  degraded: "bg-yellow-100 text-yellow-800",
  down:     "bg-red-100 text-red-800",
  open:     "bg-red-200 text-red-900 font-semibold",
  unknown:  "bg-gray-100 text-gray-600",
};

export default function AdminOpsConsole({ adminToken }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const fetchOps = async () => {
      try {
        const resp = await fetch(`${API_BASE}/admin/ops/console`, {
          credentials: "include",
          headers: adminToken
            ? { Authorization: `Bearer ${adminToken}` }
            : {},
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const json = await resp.json();
        if (!cancelled) {
          setData(json);
          setError(null);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    fetchOps();
    const t = setInterval(fetchOps, 30_000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [adminToken]);

  if (loading) return <div className="p-4">Loading Ops Console…</div>;
  if (error)
    return (
      <div className="p-4 text-red-600">Ops Console error: {error}</div>
    );
  if (!data) return null;

  const sla = data.sla_ledger || { rows: [] };
  const outage = data.outage_map || { rows: [] };
  const toggles = data.toggles || {
    env_knobs: [],
    founder_locked_thresholds: {},
    routing_pools: [],
  };

  const renderWindow = (w) =>
    !w || w.calls === 0 ? (
      <span className="text-gray-400">—</span>
    ) : (
      <span>
        {(w.success_rate * 100).toFixed(2)}% · p50 {w.p50_ms ?? "—"}ms · p95{" "}
        {w.p95_ms ?? "—"}ms ·{" "}
        <span className={w.breaches > 0 ? "text-red-600 font-semibold" : ""}>
          {w.breaches} breach{w.breaches === 1 ? "" : "es"}
        </span>{" "}
        / {w.calls}
      </span>
    );

  return (
    <div className="p-4 space-y-6">
      <h1 className="text-2xl font-semibold">Ops Console</h1>
      <p className="text-sm text-gray-500">
        Generated{" "}
        {new Date((data.generated_at || 0) * 1000).toLocaleString()}
      </p>

      <section>
        <h2 className="text-lg font-medium mb-2">
          SLA Ledger (rolling 24h + 7d windows)
        </h2>
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-gray-100">
              <th className="text-left p-2">Feature</th>
              <th className="text-right p-2">Target</th>
              <th className="text-left p-2">24h</th>
              <th className="text-left p-2">7d</th>
            </tr>
          </thead>
          <tbody>
            {sla.rows.map((r) => (
              <tr key={r.feature} className="border-t">
                <td className="p-2 font-mono">{r.feature}</td>
                <td className="text-right p-2 text-xs text-gray-500">
                  {r.target_ms}ms
                </td>
                <td className="p-2 text-xs">{renderWindow(r.h24)}</td>
                <td className="p-2 text-xs">{renderWindow(r.d7)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2 className="text-lg font-medium mb-2">
          Outage Map (1h provider health · breaker state)
        </h2>
        {outage.rows.length === 0 ? (
          <p className="text-sm text-gray-500">
            No provider activity in the last hour.
          </p>
        ) : (
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="bg-gray-100">
                <th className="text-left p-2">Provider</th>
                <th className="text-left p-2">Status</th>
                <th className="text-right p-2">Calls (1h)</th>
                <th className="text-right p-2">Success %</th>
                <th className="text-right p-2">Failure %</th>
                <th className="text-right p-2">Avg latency</th>
                <th className="text-right p-2">Breaker fails</th>
                <th className="text-left p-2">Last error</th>
              </tr>
            </thead>
            <tbody>
              {outage.rows.map((r) => (
                <tr key={r.provider} className="border-t">
                  <td className="p-2 font-mono">{r.provider}</td>
                  <td className="p-2">
                    <span
                      className={`px-2 py-0.5 rounded text-xs uppercase ${
                        STATUS_PILL_CLASS[r.status] ||
                        STATUS_PILL_CLASS.unknown
                      }`}
                    >
                      {r.status || "unknown"}
                    </span>
                  </td>
                  <td className="text-right p-2">{r.calls_1h ?? 0}</td>
                  <td className="text-right p-2">
                    {r.success_rate_pct_1h ?? "—"}
                  </td>
                  <td
                    className={`text-right p-2 ${
                      (r.failure_rate_pct_1h || 0) >= 5
                        ? "text-red-600 font-semibold"
                        : ""
                    }`}
                  >
                    {r.failure_rate_pct_1h ?? "—"}
                  </td>
                  <td className="text-right p-2">
                    {r.avg_latency_ms_1h
                      ? `${r.avg_latency_ms_1h}ms`
                      : "—"}
                  </td>
                  <td className="text-right p-2">
                    {r.consecutive_failures}
                    {r.open ? (
                      <span className="ml-1 text-red-600">⚠</span>
                    ) : null}
                  </td>
                  <td className="p-2 text-xs text-gray-600 truncate max-w-xs">
                    {r.last_error || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2 className="text-lg font-medium mb-2">
          Toggles (read-only)
        </h2>

        <h3 className="text-sm font-medium mb-1">Operator env knobs</h3>
        <table className="w-full border-collapse text-sm mb-4">
          <thead>
            <tr className="bg-gray-100">
              <th className="text-left p-2">Env knob</th>
              <th className="text-left p-2">Value</th>
            </tr>
          </thead>
          <tbody>
            {toggles.env_knobs.map((k) => (
              <tr key={k.name} className="border-t">
                <td className="p-2 font-mono">{k.name}</td>
                <td className="p-2 font-mono text-xs">
                  {k.set ? k.value : <em className="text-gray-400">unset</em>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <h3 className="text-sm font-medium mb-1">
          Founder-locked thresholds
        </h3>
        <table className="w-full border-collapse text-sm mb-4">
          <tbody>
            {Object.entries(toggles.founder_locked_thresholds || {}).map(
              ([k, v]) => (
                <tr key={k} className="border-t">
                  <td className="p-2 font-mono">{k}</td>
                  <td className="p-2 font-mono">{String(v)}</td>
                </tr>
              ),
            )}
          </tbody>
        </table>

        <h3 className="text-sm font-medium mb-1">
          Routing pools (canonical specialists)
        </h3>
        {(toggles.routing_pools || []).length === 0 ? (
          <p className="text-xs text-gray-500">No routing pools available.</p>
        ) : (
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="bg-gray-100">
                <th className="text-left p-2">Feature</th>
                <th className="text-left p-2">Provider</th>
                <th className="text-right p-2">Weight</th>
                <th className="text-right p-2">Share %</th>
                <th className="text-left p-2">Role</th>
                <th className="text-left p-2">Lock</th>
              </tr>
            </thead>
            <tbody>
              {(toggles.routing_pools || []).flatMap((pool) =>
                (pool.providers || []).map((p, idx) => (
                  <tr
                    key={`${pool.feature}-${p.name}`}
                    className="border-t"
                  >
                    <td className="p-2 font-mono">
                      {idx === 0 ? pool.feature : ""}
                    </td>
                    <td className="p-2 font-mono">{p.name}</td>
                    <td className="text-right p-2">{p.weight}</td>
                    <td className="text-right p-2">{p.share_pct}</td>
                    <td className="p-2 text-xs">{p.role}</td>
                    <td className="p-2 text-xs">
                      {idx === 0 && pool.strict_primary_lock
                        ? "strict-primary"
                        : ""}
                    </td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
