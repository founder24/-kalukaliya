import React from 'react';
import { RefreshCw } from 'lucide-react';
import { LatencyBadge, formatRelative } from './shared';

export default function ProviderLatencyBench({ benchLatest, benchLoading, loadBenchLatest }) {
  const [sortBy, setSortBy] = React.useState('ttft_warm_p50_ms');
  const [sortDir, setSortDir] = React.useState('asc');

  const latest = benchLatest?.latest;
  const hasResults = benchLatest?.has_results && latest?.suites;
  const generatedAt = latest?.generated_at;
  const generatedSec = generatedAt ? Math.floor(new Date(generatedAt).getTime() / 1000) : null;
  const suiteLabels = {
    english_chat: 'English chat',
    assamese_chat: 'Assamese chat',
    long_form: 'Long-form (~1500w)',
  };

  function sortRows(rows) {
    const dir = sortDir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      if (a[1].skipped && !b[1].skipped) return 1;
      if (b[1].skipped && !a[1].skipped) return -1;
      const av = a[1][sortBy];
      const bv = b[1][sortBy];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av - bv) * dir;
    });
  }
  function clickSort(col) {
    if (sortBy === col) setSortDir(sortDir === 'asc' ? 'desc' : 'asc');
    else { setSortBy(col); setSortDir(col === 'tokens_per_sec_p50' ? 'desc' : 'asc'); }
  }
  const SortHeader = ({ col, children }) => (
    <th
      className="text-right px-2 py-1 font-medium text-gray-500 cursor-pointer hover:text-gray-800 select-none"
      onClick={() => clickSort(col)}
      data-testid={`sort-${col}`}
    >
      {children}{sortBy === col ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''}
    </th>
  );

  const methodologyTip = (
    "Each provider runs warm-up calls (TTFT cold) followed by sampled runs (TTFT warm). " +
    "TTFT = time to first streamed token. Total = end-to-end including decode. " +
    "Same prompts/system messages used across providers for fair comparison. " +
    "Source: artifacts/syrabit-backend/scripts/bench_llm_providers.py"
  );

  return (
    <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
      <div className="flex items-start justify-between mb-4">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
            Provider Latency (TTFT)
            <span
              className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-gray-100 text-gray-500 text-[9px] font-bold cursor-help border border-gray-200"
              title={methodologyTip}
              data-testid="bench-methodology-tooltip"
            >
              ?
            </span>
            <a
              href="https://github.com/syrabit/syrabit/blob/main/artifacts/syrabit-backend/scripts/bench_llm_providers.py"
              target="_blank" rel="noreferrer"
              className="text-[10px] font-normal text-blue-500 hover:underline"
            >
              How was this measured?
            </a>
          </h3>
          <p className="text-xs text-gray-400 mt-0.5">
            Head-to-head LLM speed bench · Task #279
            {generatedSec ? ` · last benchmarked ${formatRelative(generatedSec)}` : ''}
            {hasResults ? ` · ${latest.runs_per_suite} run${latest.runs_per_suite === 1 ? '' : 's'}/suite, ${latest.warmups} warm-up${latest.warmups === 1 ? '' : 's'}` : ''}
          </p>
        </div>
        <button
          onClick={loadBenchLatest}
          disabled={benchLoading}
          className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100"
          data-testid="button-refresh-bench-latest"
        >
          <RefreshCw size={14} className={benchLoading ? 'animate-spin' : ''} />
        </button>
      </div>
      {benchLoading ? (
        <div className="flex justify-center p-6"><RefreshCw size={18} className="animate-spin text-gray-300" /></div>
      ) : !hasResults ? (
        <div className="text-center py-8 text-gray-400">
          <p className="text-sm">No benchmark runs yet.</p>
          <p className="text-xs mt-1">
            Run <code className="bg-gray-50 px-1 rounded border border-gray-200">python -m scripts.bench_llm_providers --runs 10 --warm 2</code> to populate this tile.
          </p>
        </div>
      ) : Object.keys(latest.suites).length === 0 ? (
        <div className="text-center py-8 text-gray-400 text-sm">
          Benchmark file present but contains no suites.
        </div>
      ) : (
        <div className="space-y-5">
          {Object.entries(latest.suites).map(([suiteId, suite]) => {
            const rows = sortRows(Object.entries(suite.providers || {}));
            return (
              <div key={suiteId} data-testid={`bench-suite-${suiteId}`}>
                <div className="flex items-baseline justify-between mb-1.5">
                  <p className="text-xs font-semibold text-gray-700">
                    {suiteLabels[suiteId] || suite.label || suiteId}
                  </p>
                  {suite.winner && (
                    <p className="text-[10px] text-emerald-600 font-medium">
                      ⚡ fastest: <span className="font-mono">{suite.winner.provider}</span> ({Math.round(suite.winner.value)}ms)
                    </p>
                  )}
                </div>
                <div className="overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="border-b border-gray-100">
                        <th className="text-left px-2 py-1 font-medium text-gray-500">Provider</th>
                        <SortHeader col="ttft_cold_p50_ms">TTFT cold p50</SortHeader>
                        <SortHeader col="ttft_warm_p50_ms">TTFT warm p50</SortHeader>
                        <SortHeader col="ttft_warm_p95_ms">TTFT warm p95</SortHeader>
                        <SortHeader col="total_p50_ms">Total p50</SortHeader>
                        <SortHeader col="tokens_per_sec_p50">tok/s</SortHeader>
                        <SortHeader col="success_rate">Succ.</SortHeader>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map(([pid, r]) => (
                        <tr key={pid} className="border-b border-gray-50 last:border-0" data-testid={`bench-row-${suiteId}-${pid}`}>
                          <td className="px-2 py-1.5 font-mono text-gray-700" title={r.model}>
                            {pid}
                            <div className="text-[9px] text-gray-300 font-mono truncate max-w-[180px]">{r.model}</div>
                          </td>
                          {r.skipped ? (
                            <td colSpan={6} className="px-2 py-1.5 text-[10px] text-gray-400 italic" title={r.reason}>
                              skipped — {String(r.reason || '').slice(0, 80)}
                            </td>
                          ) : r.samples > 0 ? (
                            <>
                              <td className="px-2 py-1.5 text-right"><LatencyBadge ms={r.ttft_cold_p50_ms != null ? Math.round(r.ttft_cold_p50_ms) : null} /></td>
                              <td className="px-2 py-1.5 text-right"><LatencyBadge ms={Math.round(r.ttft_warm_p50_ms ?? r.ttft_p50_ms)} /></td>
                              <td className="px-2 py-1.5 text-right"><LatencyBadge ms={Math.round(r.ttft_warm_p95_ms ?? r.ttft_p95_ms)} /></td>
                              <td className="px-2 py-1.5 text-right text-gray-500 font-mono">{Math.round(r.total_p50_ms)}ms</td>
                              <td className="px-2 py-1.5 text-right text-gray-500 font-mono">{r.tokens_per_sec_p50}</td>
                              <td className="px-2 py-1.5 text-right text-gray-500 font-mono">{Math.round((r.success_rate || 0) * 100)}%</td>
                            </>
                          ) : (
                            <td colSpan={6} className="px-2 py-1.5 text-[10px] text-gray-400 italic">no samples</td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
