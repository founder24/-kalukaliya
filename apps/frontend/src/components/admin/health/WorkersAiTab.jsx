import { RefreshCw, Activity, AlertTriangle, Zap, Database, TrendingUp, BarChart2, Clock, Users, MessageSquare } from 'lucide-react';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, BarChart, Bar, LineChart, Line } from 'recharts';
import axios from 'axios';
import { API_BASE } from '@/utils/api';
import { adminHeaders } from './shared';
import { toast } from 'sonner';
import { computeHeavyFreshness, computeThrottleFreshness } from '@/utils/metricsFreshness';

export default function WorkersAiTab({ adminToken, waiStatus, waiToggling, toggleWorkersAi, loadWorkersAi, waiThrottle, groqThrottle, geminiThrottle, azureOpenaiThrottle, deepgramThrottle, assameseUnavailable, assameseRecentExpanded, setAssameseRecentExpanded, routingConfig, setRoutingConfig, embedBurst, embedCooldownDisplay, metricsMeta, chatPipelineProbe, chatPipelineLoading, loadChatPipelineProbe }) {
  return (
          <SectionErrorBoundary name="Workers AI Fallback">
            <div className="space-y-4">

              {/* Task #214 — Chat pipeline probe card.
                  Surfaces streaming_assamese_probe.first_chunk_latency_ms
                  from /admin/health/chat-pipeline-probe so on-call staff can
                  spot a Gemini TTFB regression on the dashboard without
                  manually curling the probe endpoint. */}
              <div className="rounded-2xl p-4 border border-violet-200 bg-violet-50/40 shadow-sm" data-testid="chat-pipeline-probe-card">
                <div className="flex items-center justify-between mb-3">
                  <div>
                    <p className="text-xs font-semibold text-violet-800">Chat Pipeline Probe</p>
                    <p className="text-[10px] text-violet-600/70 mt-0.5">
                      End-to-end AI probe: Sarvam → Gemini fallback · Assamese quality gates · streaming TTFB
                    </p>
                  </div>
                  <button
                    onClick={loadChatPipelineProbe}
                    disabled={chatPipelineLoading}
                    className="px-2.5 py-1 rounded-md text-[11px] border border-violet-300 text-violet-700 hover:bg-violet-100 disabled:opacity-50"
                    data-testid="btn-refresh-chat-pipeline-probe"
                  >
                    {chatPipelineLoading ? '…' : '↻ Refresh'}
                  </button>
                </div>

                {!chatPipelineProbe ? (
                  <p className="text-xs text-gray-400 py-2">Loading…</p>
                ) : chatPipelineProbe._error ? (
                  <p className="text-xs text-red-500 py-2">Failed to load probe data — check admin session.</p>
                ) : (
                  <>
                    {/* Metric tiles */}
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">

                      {/* Provider */}
                      <div className="rounded-lg p-2.5 bg-white border border-violet-100">
                        <p className="text-[10px] uppercase font-semibold text-violet-400 mb-0.5">Provider</p>
                        <p className={`text-sm font-bold ${
                          chatPipelineProbe.provider === 'sarvam' ? 'text-emerald-600' :
                          chatPipelineProbe.provider === 'gemini-2.5-flash' ? 'text-amber-600' :
                          'text-gray-400'
                        }`}>
                          {chatPipelineProbe.provider === 'sarvam' ? 'Sarvam' :
                           chatPipelineProbe.provider === 'gemini-2.5-flash' ? 'Gemini (fallback)' :
                           chatPipelineProbe.step === 'ai_pipeline' ? '✗ Unavailable' : '—'}
                        </p>
                        {chatPipelineProbe.latency_ms != null && (
                          <p className="text-[10px] text-gray-400 tabular-nums">{chatPipelineProbe.latency_ms} ms</p>
                        )}
                      </div>

                      {/* Non-streaming Assamese probe */}
                      {(() => {
                        const ap = chatPipelineProbe.assamese_probe;
                        if (!ap) return null;
                        const skipped = ap.status === 'skipped';
                        const degraded = ap.status === 'degraded';
                        const ok = ap.has_assamese_script === true;
                        return (
                          <div className="rounded-lg p-2.5 bg-white border border-violet-100">
                            <p className="text-[10px] uppercase font-semibold text-violet-400 mb-0.5">Assamese probe</p>
                            <p className={`text-sm font-bold ${
                              skipped ? 'text-gray-400' :
                              degraded ? 'text-amber-600' :
                              ok ? 'text-emerald-600' : 'text-red-600'
                            }`}>
                              {skipped ? '— Skipped' :
                               degraded ? '⚠ Quota' :
                               ok ? '✓ Assamese' : '✗ No script'}
                            </p>
                            {ap.latency_ms != null && (
                              <p className="text-[10px] text-gray-400 tabular-nums">{ap.latency_ms} ms</p>
                            )}
                          </div>
                        );
                      })()}

                      {/* Streaming TTFB — the key metric for this task */}
                      {(() => {
                        const sp = chatPipelineProbe.streaming_assamese_probe;
                        if (!sp) return null;
                        const skipped = sp.status === 'skipped';
                        const degraded = sp.status === 'degraded';
                        const ttfb = sp.first_chunk_latency_ms;
                        const hasTtfbWarn = sp.ttfb_warning || (ttfb != null && ttfb > 10000);
                        const ok = sp.has_assamese_script === true;
                        return (
                          <div className={`rounded-lg p-2.5 border ${
                            hasTtfbWarn ? 'bg-amber-50 border-amber-200' :
                            'bg-white border-violet-100'
                          }`} data-testid="streaming-assamese-probe-tile">
                            <div className="flex items-center gap-1 mb-0.5">
                              <p className="text-[10px] uppercase font-semibold text-violet-400">Streaming TTFB</p>
                              {hasTtfbWarn && !skipped && (
                                <span
                                  className="px-1.5 py-0.5 rounded-full text-[9px] font-bold bg-amber-100 text-amber-700"
                                  data-testid="ttfb-slow-badge"
                                  title={sp.ttfb_warning || `${ttfb?.toLocaleString()} ms > 10 000 ms threshold`}
                                >
                                  SLOW
                                </span>
                              )}
                            </div>
                            <p className={`text-sm font-bold tabular-nums ${
                              skipped ? 'text-gray-400' :
                              degraded ? 'text-amber-600' :
                              hasTtfbWarn ? 'text-amber-700' :
                              ok ? 'text-emerald-600' : 'text-red-600'
                            }`}>
                              {skipped ? '— Skipped' :
                               degraded ? '⚠ Quota' :
                               ttfb != null ? `${ttfb.toLocaleString()} ms` : '—'}
                            </p>
                            {!skipped && !degraded && (
                              <p className={`text-[10px] ${ok ? 'text-emerald-600' : 'text-red-500'}`}>
                                {ok ? '✓ Assamese script' : '✗ No script'}
                              </p>
                            )}
                          </div>
                        );
                      })()}

                      {/* RAG */}
                      <div className="rounded-lg p-2.5 bg-white border border-violet-100">
                        <p className="text-[10px] uppercase font-semibold text-violet-400 mb-0.5">RAG</p>
                        <p className={`text-sm font-bold ${
                          chatPipelineProbe.rag_status === 'healthy' ? 'text-emerald-600' :
                          chatPipelineProbe.rag_status === 'degraded' ? 'text-amber-600' :
                          chatPipelineProbe.rag_status === 'unavailable' ? 'text-red-500' :
                          'text-gray-400'
                        }`}>
                          {chatPipelineProbe.rag_status || '—'}
                        </p>
                        {chatPipelineProbe.rag_topics_cached != null && (
                          <p className="text-[10px] text-gray-400">{chatPipelineProbe.rag_topics_cached} topics</p>
                        )}
                      </div>
                    </div>

                    {/* Overall status pill + failure step */}
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-semibold ${
                        chatPipelineProbe.status === 'healthy' ? 'bg-emerald-100 text-emerald-700' :
                        chatPipelineProbe.status === 'degraded' ? 'bg-amber-100 text-amber-700' :
                        chatPipelineProbe.status === 'unhealthy' ? 'bg-red-100 text-red-700' :
                        'bg-gray-100 text-gray-500'
                      }`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${
                          chatPipelineProbe.status === 'healthy' ? 'bg-emerald-500' :
                          chatPipelineProbe.status === 'degraded' ? 'bg-amber-400' :
                          chatPipelineProbe.status === 'unhealthy' ? 'bg-red-500' :
                          'bg-gray-400'
                        }`} />
                        {chatPipelineProbe.status || 'unknown'}
                      </span>
                      {chatPipelineProbe.step && (
                        <span className="text-[11px] text-red-600 font-medium">
                          Failed at: <code className="font-mono">{chatPipelineProbe.step}</code>
                        </span>
                      )}
                      {chatPipelineProbe.error && (
                        <span className="text-[11px] text-gray-500 truncate max-w-xs" title={chatPipelineProbe.error}>
                          {chatPipelineProbe.error.slice(0, 80)}
                        </span>
                      )}
                    </div>
                  </>
                )}
              </div>

              {/* Task #297 — locked provider chain surfacing.
                  Reads /admin/routing-config (pools is an ARRAY of
                  {feature, providers, strict_primary_lock}) and renders
                  one card per Task #297 surfaced provider with every pool
                  membership + role/share + credential presence. */}
              <div className="rounded-2xl p-4 border border-emerald-200 bg-emerald-50/40 shadow-sm" data-testid="locked-provider-chain">
                <div className="flex items-center justify-between mb-2">
                  <div>
                    <p className="text-xs font-semibold text-emerald-800">Locked Provider Chain (Task #297)</p>
                    <p className="text-[10px] text-emerald-700/70">Deepgram (speech), workers_ai_indic (IndicTrans2) and MongoDB Atlas (vector_search fallback) — sourced from <code className="font-mono">GET /admin/routing-config</code>.</p>
                  </div>
                  <button
                    onClick={() => {
                      axios.get(`${API_BASE}/admin/routing-config`, {
                        headers: adminHeaders(adminToken), withCredentials: true,
                      })
                        .then((r) => setRoutingConfig(r.data))
                        .catch(() => setRoutingConfig({ _error: true }));
                    }}
                    className="px-2.5 py-1 rounded-md text-[11px] border border-emerald-300 text-emerald-700 hover:bg-emerald-100"
                    data-testid="btn-refresh-routing-config"
                  >↻ Load</button>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-3">
                  {[
                    { key: 'deepgram',         label: 'Deepgram',         note: 'Speech-to-text + voice (primary)' },
                    { key: 'workers_ai_indic', label: 'workers_ai_indic', note: 'IndicTrans2 — Indic translation primary' },
                    { key: 'mongodb_atlas',    label: 'MongoDB Atlas',    note: 'vector_search fallback (weight-0)' },
                  ].map((p) => {
                    const poolsArr = Array.isArray(routingConfig?.pools) ? routingConfig.pools : [];
                    // Every pool this provider participates in (real
                    // feature names from PROVIDER_PRIORITY, not guesses).
                    const memberships = poolsArr
                      .map((pool) => {
                        const slot = pool.providers?.find?.((x) => x.name === p.key);
                        return slot ? { feature: pool.feature, ...slot } : null;
                      })
                      .filter(Boolean);
                    const present = memberships.length > 0;
                    const keyStatus = routingConfig?.key_status?.[p.key];
                    const credConfigured = keyStatus?.configured;
                    const dotColor = routingConfig === null
                      ? 'bg-gray-300'
                      : routingConfig?._error
                        ? 'bg-red-500'
                        : present && credConfigured
                          ? 'bg-emerald-500'
                          : present
                            ? 'bg-amber-400'
                            : 'bg-gray-300';
                    return (
                      <div key={p.key} className="rounded-lg p-3 bg-white border border-emerald-100" data-testid={`provider-card-${p.key}`}>
                        <div className="flex items-center gap-2 mb-1">
                          <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />
                          <span className="text-xs font-semibold text-gray-700">{p.label}</span>
                          {keyStatus && (
                            <span
                              className={`ml-auto text-[9px] font-semibold px-1.5 py-0.5 rounded ${credConfigured ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'}`}
                              title={`source: ${keyStatus.source}`}
                              data-testid={`provider-cred-${p.key}`}
                            >
                              {credConfigured ? 'Configured' : 'Missing'}
                            </span>
                          )}
                        </div>
                        <div className="text-[10px] text-gray-500 mb-1.5">{p.note}</div>
                        {present ? (
                          <div className="space-y-0.5">
                            {memberships.map((m) => (
                              <div key={m.feature} className="text-[11px] font-mono text-gray-600 flex justify-between gap-2">
                                <span>
                                  <span className="text-emerald-700">{m.feature}</span>
                                  <span className="text-gray-400"> · {m.role}</span>
                                </span>
                                <span className="text-gray-500 tabular-nums">{m.share_pct}%</span>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-[11px] text-gray-400 italic">
                            {routingConfig === null ? 'Click ↻ Load to fetch' : 'Not present in any pool'}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
                {routingConfig?._error && (
                  <p className="mt-2 text-[11px] text-red-600">Failed to load routing config — check admin token / api workflow.</p>
                )}
              </div>

              {/* Task #396 — freshness indicator. Backend (cms_sarvam_health.py
                  admin_dashboard_metrics) piggybacks `_meta` on every response
                  so admins can tell which numbers below are seconds-fresh
                  (throttle tiles, recomputed every poll per Task #388) vs
                  cached (heavy users / revenue / SEO / deps block, cached for
                  ~5s per Task #395). Without this strip, recovery from a
                  burst feels indistinguishable from a stale "still throttled"
                  view, and a stale heavy-block could mislead an admin during
                  a deploy. Sits directly above the burst tiles below so the
                  "live" label is visually adjacent to the data it describes. */}
              {metricsMeta && (() => {
                // Shared formatter + thresholds — see
                // src/utils/metricsFreshness.js. Centralised so the
                // AdminDashboard badge (Task #398) renders the exact
                // same wording for the same `heavy_cached_at` value;
                // a tuning change here updates both panels in lockstep.
                const heavyAt = Number(metricsMeta.heavy_cached_at);
                const throttleAt = Number(metricsMeta.throttle_fresh_at);
                const { label: heavyLabel } = computeHeavyFreshness(heavyAt);
                const { label: throttleLabel } = computeThrottleFreshness(throttleAt);
                // The four heavy sections (users, revenue, SEO, deps) all
                // share a single `heavy_cached_at` because they're computed
                // and cached together as one block (see admin_dashboard_metrics
                // cache-miss path in cms_sarvam_health.py). We still list
                // them individually here — matching the task spec example
                // "Throttle: live • Users: 14s ago • Deps: 14s ago" — so
                // each tile on AdminDashboard / AdminHealth has a clearly
                // attributed freshness label, and a future split into
                // per-collection TTLs can wire each label to its own
                // timestamp without changing the visual layout.
                return (
                  <div
                    className="text-[10px] text-gray-400 flex flex-wrap items-center gap-x-2 gap-y-0.5 px-1"
                    data-testid="metrics-freshness"
                    title={`heavy_cached_at=${heavyAt} · throttle_fresh_at=${throttleAt}`}
                  >
                    <span>
                      Throttle:{' '}
                      <span className="text-gray-600 font-medium" data-testid="metrics-freshness-throttle">
                        {throttleLabel}
                      </span>
                    </span>
                    {[
                      { label: 'Users',   testid: 'metrics-freshness-users' },
                      { label: 'Revenue', testid: 'metrics-freshness-revenue' },
                      { label: 'SEO',     testid: 'metrics-freshness-seo' },
                      { label: 'Deps',    testid: 'metrics-freshness-deps' },
                    ].map(({ label, testid }) => (
                      <React.Fragment key={label}>
                        <span aria-hidden="true">•</span>
                        <span>
                          {label}:{' '}
                          <span className="text-gray-600 font-medium" data-testid={testid}>
                            {heavyLabel}
                          </span>
                        </span>
                      </React.Fragment>
                    ))}
                  </div>
                );
              })()}

              {/* Tasks #85/#90/#374/#378 — reusable burst gauge for any provider.
                  ``assamese-chat`` reuses the same shape but counts
                  ``assamese_unavailable`` events (both rails red) instead of
                  429s — see backend ``record_assamese_unavailable``.
                  Azure OpenAI and Deepgram added per Task #378 so admins
                  can spot a building burst before on-call is paged. */}
              {[
                { key: 'workers-ai',    label: 'Workers AI',                thr: waiThrottle,         unit: '429s' },
                { key: 'gemini',        label: 'Gemini',                    thr: geminiThrottle,      unit: '429s' },
                { key: 'azure-openai',  label: 'Azure OpenAI',              thr: azureOpenaiThrottle, unit: '429s' },
                { key: 'deepgram',      label: 'Deepgram',                  thr: deepgramThrottle,    unit: '429s' },
                { key: 'assamese-chat', label: 'Assamese Chat (both rails)', thr: assameseUnavailable, unit: 'outage events' },
              ].map(({ key, label, thr: _thr, unit }) => (
                <div key={key}>
                  {(() => {
                    const thr = _thr;
                    const isLoading = thr === null;
                    const burst60 = thr?.burst_60s ?? 0;
                    const burst180 = thr?.burst_180s ?? 0;
                    const limit = thr?.alert_threshold ?? 5;
                    const throttled = !isLoading && (thr?.throttled ?? false);
                    const approaching = !isLoading && !throttled && burst60 >= Math.ceil(limit * 0.6);
                    const dotColor = isLoading ? 'bg-gray-300' : throttled ? 'bg-red-500' : approaching ? 'bg-amber-400' : 'bg-emerald-500';
                    const statusLabel = isLoading ? 'Loading\u2026' : throttled ? 'Throttled' : approaching ? 'Approaching' : 'OK';
                    const statusText = isLoading ? 'text-gray-400' : throttled ? 'text-red-600' : approaching ? 'text-amber-600' : 'text-emerald-600';
                    return (
                      <div className={`rounded-2xl p-4 border shadow-sm ${throttled ? 'bg-red-50 border-red-200' : approaching ? 'bg-amber-50 border-amber-200' : 'bg-white border-gray-200'}`}>
                        {throttled && (
                          <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-red-100 border border-red-200">
                            <AlertTriangle size={14} className="text-red-600 shrink-0" />
                            <span className="text-xs font-semibold text-red-700">
                              {key === 'assamese-chat'
                                ? `${label} — both rails red: ${burst180} ${unit} in the last 180 s (threshold: ${limit})`
                                : `${label} is throttled — ${burst60} ${unit} in the last 60 s (threshold: ${limit})`}
                            </span>
                          </div>
                        )}
                        <div className="flex items-center gap-2">
                          <Zap size={14} className={isLoading ? 'text-gray-300' : throttled ? 'text-red-500' : approaching ? 'text-amber-500' : 'text-gray-400'} />
                          <span className="text-xs font-semibold text-gray-700">
                            {key === 'assamese-chat'
                              ? `${label} — Outage Burst Pressure`
                              : `${label} — 429 Burst Pressure`}
                          </span>
                          <span className={`flex items-center gap-1 text-[11px] font-semibold ${statusText}`}>
                            <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />
                            {statusLabel}
                          </span>
                        </div>
                        {!isLoading && (
                          <div className="mt-3 grid grid-cols-3 gap-3">
                            <div className="rounded-lg p-2.5 bg-white/70 border border-gray-100">
                              <div className="text-[10px] uppercase text-gray-400 font-semibold mb-0.5">60 s (this worker)</div>
                              <div className={`text-base font-bold tabular-nums ${burst60 >= limit ? 'text-red-600' : burst60 >= Math.ceil(limit * 0.6) ? 'text-amber-600' : 'text-emerald-600'}`}>
                                {burst60}<span className="text-xs font-normal text-gray-400"> / {limit}</span>
                              </div>
                            </div>
                            <div className="rounded-lg p-2.5 bg-white/70 border border-gray-100">
                              <div className="text-[10px] uppercase text-gray-400 font-semibold mb-0.5">180 s (all workers)</div>
                              <div className={`text-base font-bold tabular-nums ${key === 'assamese-chat' && burst180 >= limit ? 'text-red-600' : 'text-gray-700'}`}>{burst180}</div>
                            </div>
                            <div className="rounded-lg p-2.5 bg-white/70 border border-gray-100">
                              <div className="text-[10px] uppercase text-gray-400 font-semibold mb-0.5">Alert threshold</div>
                              <div className="text-base font-bold tabular-nums text-gray-700">{limit}</div>
                            </div>
                          </div>
                        )}
                        {/* Task #379 — recent outage events for the Assamese
                            tile only. The other burst tiles aggregate
                            generic 429s where per-event detail isn't
                            useful; the Assamese rail is a P0 outage where
                            knowing *which leg* failed first speeds triage. */}
                        {key === 'assamese-chat' && !isLoading && (
                          <div className="mt-3 pt-3 border-t border-gray-100">
                            <button
                              type="button"
                              onClick={() => setAssameseRecentExpanded(v => !v)}
                              className="flex items-center justify-between w-full text-[11px] font-semibold text-gray-600 hover:text-gray-900"
                              aria-expanded={assameseRecentExpanded}
                              data-testid="assamese-recent-toggle"
                            >
                              <span>
                                Recent outage events
                                <span className="ml-1 text-gray-400 font-normal">
                                  ({(thr?.recent ?? []).length})
                                </span>
                              </span>
                              <span className="text-gray-400">{assameseRecentExpanded ? '▾' : '▸'}</span>
                            </button>
                            {assameseRecentExpanded && (
                              <div className="mt-2" data-testid="assamese-recent-list">
                                {(thr?.recent ?? []).length === 0 ? (
                                  <p className="text-[11px] text-gray-400 italic py-2">
                                    No recent outage events recorded — the rail has been calm for the last 180 s.
                                  </p>
                                ) : (
                                  <ul className="space-y-1.5">
                                    {(thr?.recent ?? []).slice(0, 5).map((ev, idx) => {
                                      const ts = typeof ev?.ts === 'number'
                                        ? new Date(ev.ts * 1000)
                                        : null;
                                      const tsStr = ts && !isNaN(ts.getTime())
                                        ? ts.toLocaleTimeString([], { hour12: false })
                                        : '—';
                                      const legLabels = {
                                        sarvam_workers_indic_chain: 'Sarvam → Vertex/Gemini',
                                        workers_ai_unavailable: 'Workers-AI Phase-2 unavailable',
                                        workers_ai_phase2: 'Workers-AI Phase-2 errored',
                                      };
                                      const legLabel = legLabels[ev?.failing_leg] || (ev?.failing_leg || 'unknown');
                                      const errSummary = (ev?.error_summary || '').trim();
                                      const convHash = (ev?.conversation_id_hash || '').trim();
                                      return (
                                        <li
                                          key={`${ev?.ts ?? 'na'}-${idx}`}
                                          className="rounded-md px-2.5 py-1.5 bg-white/80 border border-gray-100 text-[11px]"
                                          data-testid="assamese-recent-event"
                                        >
                                          <div className="flex items-baseline gap-2">
                                            <span className="font-mono text-gray-500 tabular-nums shrink-0">{tsStr}</span>
                                            <span className="font-semibold text-gray-700">{legLabel}</span>
                                            {convHash && (
                                              <span className="ml-auto text-[10px] text-gray-400 font-mono">conv {convHash}</span>
                                            )}
                                          </div>
                                          {errSummary && (
                                            <div className="mt-0.5 text-[10.5px] text-gray-500 break-words" title={errSummary}>
                                              {errSummary.length > 140 ? `${errSummary.slice(0, 139)}…` : errSummary}
                                            </div>
                                          )}
                                        </li>
                                      );
                                    })}
                                  </ul>
                                )}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                </div>
              ))}

              <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
                <div className="flex items-start justify-between mb-4">
                  <div>
                    <h3 className="text-sm font-semibold text-gray-900">Workers AI Fallback</h3>
                    <p className="text-xs text-gray-500 mt-1">
                      Cloudflare Workers AI auto-fallback for chat / embed / TTS / STT.
                      Activates only after the primary provider fails with a retryable
                      error (timeout / 5xx / 429 / quota). 4xx bad-input failures
                      always surface to the caller.
                    </p>
                  </div>
                  <button onClick={loadWorkersAi}
                    className="px-3 py-1.5 rounded-lg text-xs border border-gray-200 text-gray-500 hover:text-gray-700">
                    ↻ Refresh
                  </button>
                </div>

                {!waiStatus ? (
                  <div className="text-xs text-gray-400 py-4">Loading…</div>
                ) : !waiStatus.ok ? (
                  <div className="text-xs text-red-500 py-4">
                    Status unavailable: {waiStatus.error || 'unknown'}
                  </div>
                ) : (
                  <>
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
                      <div className="rounded-lg p-3 bg-gray-50 border border-gray-100">
                        <div className="text-[10px] uppercase text-gray-400 font-semibold">Master switch</div>
                        <div className={`text-sm font-semibold ${waiStatus.enabled_globally ? 'text-emerald-600' : 'text-gray-400'}`}>
                          {waiStatus.enabled_globally ? 'Enabled' : 'Disabled'}
                        </div>
                      </div>
                      <div className="rounded-lg p-3 bg-gray-50 border border-gray-100">
                        <div className="text-[10px] uppercase text-gray-400 font-semibold">Shared secret</div>
                        <div className={`text-sm font-semibold ${waiStatus.secret_configured ? 'text-emerald-600' : 'text-red-500'}`}>
                          {waiStatus.secret_configured ? 'Configured' : 'Missing'}
                        </div>
                      </div>
                      <div className="rounded-lg p-3 bg-gray-50 border border-gray-100 col-span-2 sm:col-span-1">
                        <div className="text-[10px] uppercase text-gray-400 font-semibold">Edge URL</div>
                        <div className="text-xs font-mono text-gray-600 truncate" title={waiStatus.edge_url}>
                          {waiStatus.edge_url || '—'}
                        </div>
                      </div>
                    </div>

                    {/* Task #96 — Workers AI embed 429 cooldown indicator */}
                    {(() => {
                      const eb = embedBurst;
                      const isLoading     = eb === null;
                      const isUnavailable = eb === false;
                      if (isUnavailable) return (
                        <div className="rounded-2xl p-4 border border-gray-200 bg-white shadow-sm mb-4">
                          <div className="flex items-center gap-2">
                            <Zap size={14} className="text-gray-300" />
                            <span className="text-xs font-semibold text-gray-700">Workers AI — Embed 429 Cooldown</span>
                            <span className="flex items-center gap-1 text-[11px] font-semibold text-gray-400">
                              <span className="inline-block w-2 h-2 rounded-full bg-gray-300" />
                              Unavailable
                            </span>
                          </div>
                          <p className="mt-2 text-[11px] text-gray-400">Pool stats endpoint unreachable — retry will happen in 30 s.</p>
                        </div>
                      );
                      const burst      = eb?.burst ?? 0;
                      const cooldown   = eb?.cooldown ?? false;
                      const remainingS = eb?.remainingS ?? 0;
                      const threshold  = eb?.threshold ?? 3;
                      const durationS  = eb?.durationS ?? 60;
                      const approaching = !isLoading && !cooldown && burst >= Math.ceil(threshold * 0.6);
                      const dotColor = isLoading
                        ? 'bg-gray-300'
                        : cooldown
                        ? 'bg-red-500'
                        : approaching
                        ? 'bg-amber-400'
                        : 'bg-emerald-500';
                      const statusLabel = isLoading
                        ? 'Loading\u2026'
                        : cooldown ? 'Cooldown Active' : approaching ? 'Approaching' : 'OK';
                      const statusText = isLoading
                        ? 'text-gray-400'
                        : cooldown
                        ? 'text-red-600'
                        : approaching
                        ? 'text-amber-600'
                        : 'text-emerald-600';
                      return (
                        <div className={`rounded-2xl p-4 border shadow-sm mb-4 ${
                          cooldown
                            ? 'bg-red-50 border-red-200'
                            : approaching
                            ? 'bg-amber-50 border-amber-200'
                            : 'bg-white border-gray-200'
                        }`}>
                          {cooldown && (
                            <div className={`flex items-center gap-2 mb-3 px-3 py-2 rounded-lg border transition-colors ${
                              embedCooldownDisplay <= 5
                                ? 'bg-amber-100 border-amber-300'
                                : 'bg-red-100 border-red-200'
                            }`}>
                              <AlertTriangle size={14} className={`shrink-0 transition-colors ${embedCooldownDisplay <= 5 ? 'text-amber-600' : 'text-red-600'}`} />
                              <span className={`text-xs font-semibold transition-colors ${embedCooldownDisplay <= 5 ? 'text-amber-700' : 'text-red-700'}`}>
                                Embed cooldown active — Workers AI embed skipped for {embedCooldownDisplay}s
                                ({burst} of {threshold} hits in last {durationS}s)
                              </span>
                            </div>
                          )}
                          {approaching && !cooldown && (
                            <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-amber-100 border border-amber-200">
                              <AlertTriangle size={14} className="text-amber-600 shrink-0" />
                              <span className="text-xs font-semibold text-amber-700">
                                Approaching cooldown — {burst}/{threshold} embed 429s recorded
                              </span>
                            </div>
                          )}
                          <div className="flex items-center gap-2">
                            <Zap size={14} className={isLoading ? 'text-gray-300' : cooldown ? 'text-red-500' : approaching ? 'text-amber-500' : 'text-gray-400'} />
                            <span className="text-xs font-semibold text-gray-700">Workers AI — Embed 429 Cooldown</span>
                            <span className={`flex items-center gap-1 text-[11px] font-semibold ${statusText}`}>
                              <span className={`inline-block w-2 h-2 rounded-full ${dotColor}`} />
                              {statusLabel}
                            </span>
                          </div>
                          {!isLoading && (
                            <div className="mt-3 grid grid-cols-3 gap-3">
                              <div className="rounded-lg p-2.5 bg-white/70 border border-gray-100">
                                <div className="text-[10px] uppercase text-gray-400 font-semibold mb-0.5">429 hits (60 s)</div>
                                <div className={`text-base font-bold tabular-nums ${burst >= threshold ? 'text-red-600' : burst >= Math.ceil(threshold * 0.6) ? 'text-amber-600' : 'text-emerald-600'}`}>
                                  {burst}
                                  <span className="text-xs font-normal text-gray-400"> / {threshold}</span>
                                </div>
                              </div>
                              {/* Two-stage urgency: red+pulse (6-10s), amber bg + orange text (0-5s) */}
                              <div className={`rounded-lg p-2.5 border transition-colors ${
                                cooldown && embedCooldownDisplay <= 5
                                  ? 'bg-amber-50 border-amber-300'
                                  : 'bg-white/70 border-gray-100'
                              }`}>
                                <div className={`text-[10px] uppercase font-semibold mb-0.5 ${
                                  cooldown && embedCooldownDisplay <= 5 ? 'text-amber-600' : 'text-gray-400'
                                }`}>Cooldown clears in</div>
                                <div className={`text-base font-bold tabular-nums ${
                                  cooldown && embedCooldownDisplay <= 5
                                    ? 'text-orange-500 animate-pulse'
                                    : cooldown && embedCooldownDisplay <= 10
                                    ? 'text-red-600 animate-pulse'
                                    : cooldown
                                    ? 'text-red-600'
                                    : 'text-gray-400'
                                }`}>
                                  {cooldown ? `${embedCooldownDisplay} s` : '—'}
                                </div>
                              </div>
                              <div className="rounded-lg p-2.5 bg-white/70 border border-gray-100">
                                <div className="text-[10px] uppercase text-gray-400 font-semibold mb-0.5">Skip window</div>
                                <div className="text-base font-bold tabular-nums text-gray-700">{durationS} s</div>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })()}

                    <div className="overflow-hidden rounded-xl border border-gray-100">
                      <table className="w-full text-xs">
                        <thead className="bg-gray-50 text-gray-500">
                          <tr>
                            <th className="text-left px-3 py-2 font-semibold">Capability</th>
                            <th className="text-left px-3 py-2 font-semibold">Last fallback</th>
                            <th className="text-left px-3 py-2 font-semibold">24h ok / fail</th>
                            <th className="text-left px-3 py-2 font-semibold">Last reason</th>
                            <th className="text-right px-3 py-2 font-semibold">Action</th>
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(waiStatus.capabilities || {}).map(([cap, c]) => (
                            <tr key={cap} className="border-t border-gray-100">
                              <td className="px-3 py-2 font-mono text-gray-700">{cap}</td>
                              <td className="px-3 py-2 text-gray-500">
                                {c.last_fallback_at
                                  ? new Date(c.last_fallback_at * 1000).toLocaleString()
                                  : <span className="text-gray-300">never</span>}
                              </td>
                              <td className="px-3 py-2">
                                <span className="text-emerald-600 font-semibold">{c.successes_24h ?? 0}</span>
                                <span className="text-gray-300"> / </span>
                                <span className="text-red-500 font-semibold">{c.failures_24h ?? 0}</span>
                              </td>
                              <td className="px-3 py-2 text-gray-500 font-mono">
                                {c.last_primary_error || <span className="text-gray-300">—</span>}
                              </td>
                              <td className="px-3 py-2 text-right">
                                <button
                                  onClick={() => toggleWorkersAi(cap, !c.enabled)}
                                  disabled={waiToggling === cap}
                                  className={`px-3 py-1 rounded-md text-[11px] font-semibold ${
                                    c.enabled
                                      ? 'bg-emerald-50 text-emerald-700 border border-emerald-200 hover:bg-emerald-100'
                                      : 'bg-gray-100 text-gray-500 border border-gray-200 hover:bg-gray-200'
                                  } disabled:opacity-50`}
                                >
                                  {waiToggling === cap ? '…' : (c.enabled ? 'Enabled' : 'Disabled')}
                                </button>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                )}
              </div>
            </div>
          </SectionErrorBoundary>
  );
}
