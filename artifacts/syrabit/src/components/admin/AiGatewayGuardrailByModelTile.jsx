/**
 * Task #448 — "top models by guardrail block ratio" tile for the admin
 * CF Health panel. Fed from /api/admin/cf-health → ai_gateway.guardrail_by_model
 * (per-model aggregation built on top of recent_samples in
 * artifacts/syrabit-backend/ai_gateway_observability.py).
 *
 * Sibling of AiGatewayCacheByModelTile — Task #419 added the cache-hit
 * breakdown but the admin panel still only showed aggregate guardrail
 * counters. On-call would have to slice ai_gateway.recent_samples by
 * hand to answer "is one model disproportionately tripping the
 * Llama-Guard / AI Content Safety layer?". This tile renders that
 * breakdown inline.
 *
 * "—" rule: a model whose samples carried no cf-aig-guardrail-action
 * at all (e.g. only cache events) reports `block_ratio: null` from the
 * backend. We render that as "—" rather than 0% so the row is not
 * mistaken for a "0% blocked" outlier (Task #448 done-criteria).
 */

import React from 'react';
import { ShieldAlert, RefreshCw } from 'lucide-react';

function ratioColor(ratio) {
  if (ratio == null) return 'text-gray-400';
  // For block ratio higher == worse — invert the cache-tile palette so
  // red flags the worst offender at a glance.
  if (ratio >= 0.2) return 'text-red-600';
  if (ratio >= 0.05) return 'text-amber-600';
  return 'text-emerald-600';
}

function formatRatio(ratio) {
  if (ratio == null) return '—';
  return `${Math.round(ratio * 100)}%`;
}

export default function AiGatewayGuardrailByModelTile({ data, loading, onRefresh }) {
  const enabled = !!data?.enabled;
  const rows = Array.isArray(data?.guardrail_by_model) ? data.guardrail_by_model : [];
  const topRows = rows.slice(0, 5);

  return (
    <div className="rounded-2xl border bg-white shadow-sm overflow-hidden" data-testid="aig-guardrail-by-model-tile">
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-gray-100">
        <div className="flex items-center gap-2 min-w-0">
          <ShieldAlert size={16} className="shrink-0 text-rose-600" />
          <span className="text-sm font-semibold text-gray-700 truncate">
            AI Gateway · models by guardrail block ratio
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span
            className={`inline-flex items-center text-[10px] font-semibold px-2 py-0.5 rounded-full border ${
              enabled
                ? 'bg-emerald-50 text-emerald-700 border-emerald-200'
                : 'bg-gray-100 text-gray-500 border-gray-200'
            }`}
            data-testid="aig-guardrail-by-model-flag"
          >
            {enabled ? 'CF_AIGW_OBS_ON' : 'OBS OFF'}
          </span>
          {onRefresh && (
            <button
              onClick={onRefresh}
              disabled={loading}
              title="Refresh"
              className="text-gray-400 hover:text-gray-600 transition-colors disabled:opacity-40"
              data-testid="aig-guardrail-by-model-refresh"
            >
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            </button>
          )}
        </div>
      </div>

      <div className="px-4 py-3">
        {topRows.length === 0 ? (
          <p className="text-[11px] text-gray-400 italic" data-testid="aig-guardrail-by-model-empty">
            No AI Gateway samples in the current window yet.
          </p>
        ) : (
          <table className="w-full text-xs" data-testid="aig-guardrail-by-model-table">
            <thead>
              <tr className="text-left text-[10px] uppercase tracking-wider text-gray-400 border-b border-gray-100">
                <th className="py-1 pr-2 font-medium">Model</th>
                <th className="py-1 px-2 font-medium text-right">Block ratio</th>
                <th className="py-1 px-2 font-medium text-right">Blocks</th>
                <th className="py-1 px-2 font-medium text-right">Rewrites</th>
                <th className="py-1 pl-2 font-medium text-right">Samples</th>
              </tr>
            </thead>
            <tbody>
              {topRows.map((row, idx) => {
                const key = `${row.provider || 'unknown'}::${row.model || 'unknown'}::${idx}`;
                return (
                  <tr key={key} className="border-b border-gray-50 last:border-0" data-testid={`aig-guardrail-row-${row.model || 'unknown'}`}>
                    <td className="py-1.5 pr-2">
                      <div className="font-mono text-gray-700 truncate max-w-[14rem]" title={row.model || ''}>
                        {row.model || 'unknown'}
                      </div>
                      <div className="text-[10px] text-gray-400">{row.provider || 'unknown'}</div>
                    </td>
                    <td
                      className={`py-1.5 px-2 text-right font-mono font-semibold ${ratioColor(row.block_ratio)}`}
                      data-testid={`aig-guardrail-ratio-${row.model || 'unknown'}`}
                    >
                      {formatRatio(row.block_ratio)}
                    </td>
                    <td className="py-1.5 px-2 text-right font-mono text-gray-600">{row.blocks ?? 0}</td>
                    <td className="py-1.5 px-2 text-right font-mono text-gray-600">{row.rewrites ?? 0}</td>
                    <td className="py-1.5 pl-2 text-right font-mono text-gray-500">{row.samples ?? 0}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
