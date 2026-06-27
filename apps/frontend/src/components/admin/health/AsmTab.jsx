import { RefreshCw, Activity, AlertTriangle, Star, Undo2, ExternalLink, Check, Clock } from 'lucide-react';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import SarvamHealthCard from '../SarvamHealthCard';
import AssameseCorpusCoveragePill from '../AssameseCorpusCoveragePill';
import AssameseBackfillPanel from '../AssameseBackfillPanel';
import { buildHighlightedSegments } from '@/utils/highlightSegments';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import axios from 'axios';
import { API_BASE } from '@/utils/api';
import { adminHeaders } from './shared';
import { toast } from 'sonner';

export default function AsmTab({ adminToken, asmStats, asmStatsLoading, asmStatsWindow, setAsmStatsWindow, loadAsmStats, asmLoading, asmDraft, setAsmDraft, asmCfg, asmSaving, asmRuns, asmRunsLoading, asmRunsActionFilter, setAsmRunsActionFilter, asmRunsExpanded, setAsmRunsExpanded, loadAsmRuns, asmAudit, asmAuditLoading, asmAuditFilters, setAsmAuditFilters, asmAuditOffset, setAsmAuditOffset, loadAsmAudit, asmRevertingId, asmRevertPreview, setAsmRevertPreview, revertAsmAuditRow, confirmAsmRevert, asmTesting, asmTestResult, setAsmTestResult, asmTestSample, setAsmTestSample, fireAsmTest, loadAsmCfg, saveAsmOverride, clearAsmOverride, ASM_AUDIT_PAGE }) {
  return (
          <SectionErrorBoundary name="Sarvam Purity" resetKeys={['asm']}>
          <div className="space-y-4" data-testid="asm-purity-tab">
            {/* Task #553 — Inference-providers tile for the Sarvam-m
                Assamese-chat primary. Polls /api/admin/health/sarvam
                every 30s; surfaces the rolling 1h success-rate that
                also drives the <95% Sentry alert. */}
            <SarvamHealthCard token={adminToken} />
            {/* Task #423 — sanitiser-run stats so admins can see whether the
                override they just set is actually changing live behaviour. */}
            <SectionErrorBoundary name="ASM Cleanup Activity">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm" data-testid="asm-stats-card">
              <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <Activity size={16} className="text-blue-500" />
                    Cleanup activity
                  </h3>
                  <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                    How often the sanitiser fired against real Sarvam Indic chat replies, what action it took, and how leaky those replies were.
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  {['24h', '7d'].map((w) => (
                    <button
                      key={w}
                      onClick={() => { setAsmStatsWindow(w); loadAsmStats(w); }}
                      className={`px-3 py-1 rounded-lg text-[11px] font-semibold transition-all ${
                        asmStatsWindow === w
                          ? 'bg-blue-600 text-white shadow-sm'
                          : 'text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                      }`}
                      data-testid={`button-asm-window-${w}`}
                    >
                      {w}
                    </button>
                  ))}
                  <button
                    onClick={() => loadAsmStats()}
                    disabled={asmStatsLoading}
                    className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100"
                    data-testid="button-refresh-asm-stats"
                    title="Refresh stats"
                  >
                    <RefreshCw size={14} className={asmStatsLoading ? 'animate-spin' : ''} />
                  </button>
                </div>
              </div>

              {asmStats && asmStats.ok === false && (
                <div className="mb-3 p-3 rounded-xl bg-red-50 border border-red-200 flex items-start gap-2" data-testid="asm-stats-error">
                  <AlertTriangle size={14} className="text-red-500 mt-0.5 flex-shrink-0" />
                  <div className="text-[11px] text-red-700 leading-relaxed">
                    <span className="font-semibold">Stats backend unavailable.</span>{' '}
                    {asmStats.error || 'Aggregation failed — see api logs.'} Numbers below default to zero and are not authoritative.
                  </div>
                </div>
              )}
              {asmStatsLoading && !asmStats ? (
                <div className="flex justify-center py-10"><RefreshCw size={20} className="animate-spin text-gray-300" /></div>
              ) : asmStats ? (
                asmStats.total === 0 ? (
                  <p className="text-xs text-gray-400 py-6 text-center" data-testid="asm-stats-empty">
                    No sanitiser runs recorded in the last {asmStatsWindow}. Stats appear once Indic chat traffic flows through the sanitiser.
                  </p>
                ) : (
                  <>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                      <PeakBadge label="Total runs" value={asmStats.total.toLocaleString()} color="blue" />
                      <PeakBadge label="Cleanup fired" value={`${asmStats.active.toLocaleString()} (${asmStats.total ? Math.round(100 * asmStats.active / asmStats.total) : 0}%)`} color="amber" />
                      <PeakBadge label="Avg leakage" value={(asmStats.avg_ratio || 0).toFixed(4)} color="violet" />
                      <PeakBadge label="p95 leakage" value={(asmStats.p95_ratio || 0).toFixed(4)} color="emerald" />
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div>
                        <p className="text-[10px] uppercase tracking-wider text-gray-500 font-bold mb-2">Action breakdown</p>
                        <div className="space-y-1.5" data-testid="asm-stats-actions">
                          {Object.entries(asmStats.actions || {}).sort((a, b) => b[1] - a[1]).map(([action, count]) => {
                            const pct = asmStats.total ? Math.round(100 * count / asmStats.total) : 0;
                            return (
                              <div key={action} className="flex items-center gap-2">
                                <span className="text-xs font-mono text-gray-700 w-32 truncate" title={action}>{action}</span>
                                <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                                  <div className="h-full bg-blue-400" style={{ width: `${pct}%` }} />
                                </div>
                                <span className="text-[11px] text-gray-500 font-mono w-20 text-right">{count.toLocaleString()} · {pct}%</span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase tracking-wider text-gray-500 font-bold mb-2">Behaviour split</p>
                        <div className="space-y-1.5" data-testid="asm-stats-behaviours">
                          {Object.entries(asmStats.behaviours || {}).sort((a, b) => b[1] - a[1]).map(([beh, count]) => {
                            const pct = asmStats.total ? Math.round(100 * count / asmStats.total) : 0;
                            return (
                              <div key={beh} className="flex items-center gap-2">
                                <span className="text-xs font-mono text-gray-700 w-32 truncate" title={beh}>{beh}</span>
                                <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                                  <div className="h-full bg-violet-400" style={{ width: `${pct}%` }} />
                                </div>
                                <span className="text-[11px] text-gray-500 font-mono w-20 text-right">{count.toLocaleString()} · {pct}%</span>
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    </div>

                    {(asmStats.translated > 0 || asmStats.regenerated > 0) && (
                      <p className="text-[11px] text-gray-400 mt-3 leading-relaxed">
                        <span className="font-semibold text-gray-500">Translate-fix:</span> {asmStats.translated.toLocaleString()} runs ·{' '}
                        <span className="font-semibold text-gray-500">Regenerate:</span> {asmStats.regenerated.toLocaleString()} runs
                      </p>
                    )}
                  </>
                )
              ) : (
                <p className="text-xs text-gray-400 py-6 text-center">Stats unavailable.</p>
              )}
            </div>
            </SectionErrorBoundary>

            {/* Task #428 — drill into individual sanitiser runs so admins
                can see the exact replies that got translated/stripped/
                regenerated and tune the threshold from real evidence. */}
            <SectionErrorBoundary name="ASM Recent Runs">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm" data-testid="asm-runs-card">
              <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <MessageSquare size={16} className="text-amber-500" />
                    Recent cleanups
                  </h3>
                  <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                    Last 50 sanitiser runs (newest first) with the original vs cleaned text. Snippets are truncated to 600 chars and PII (emails, phone numbers, long digit IDs) is scrubbed before persisting. Noop runs are still recorded for traceability but omit the original/cleaned snippets.
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  <select
                    value={asmRunsActionFilter}
                    onChange={(e) => { setAsmRunsActionFilter(e.target.value); loadAsmRuns(e.target.value); }}
                    className="text-[11px] font-mono px-2 py-1 rounded-lg border border-gray-200 focus:border-amber-300 focus:ring-1 focus:ring-amber-200 outline-none"
                    data-testid="select-asm-runs-action"
                    title="Filter by action"
                  >
                    <option value="">All actions</option>
                    <option value="stripped">stripped</option>
                    <option value="translated">translated</option>
                    <option value="translated+stripped">translated+stripped</option>
                    <option value="regenerated">regenerated</option>
                    <option value="regenerated+translated">regenerated+translated</option>
                    <option value="regenerated+stripped">regenerated+stripped</option>
                    <option value="regenerated+translated+stripped">regenerated+translated+stripped</option>
                    <option value="noop">noop</option>
                  </select>
                  <button
                    onClick={() => loadAsmRuns()}
                    disabled={asmRunsLoading}
                    className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100"
                    data-testid="button-refresh-asm-runs"
                    title="Refresh recent cleanups"
                  >
                    <RefreshCw size={14} className={asmRunsLoading ? 'animate-spin' : ''} />
                  </button>
                </div>
              </div>

              {asmRuns && asmRuns.ok === false && (
                <div className="mb-3 p-3 rounded-xl bg-red-50 border border-red-200 flex items-start gap-2" data-testid="asm-runs-error">
                  <AlertTriangle size={14} className="text-red-500 mt-0.5 flex-shrink-0" />
                  <div className="text-[11px] text-red-700 leading-relaxed">
                    <span className="font-semibold">Recent cleanups unavailable.</span>{' '}
                    {asmRuns.error || 'Mongo read failed — see api logs.'}
                  </div>
                </div>
              )}

              {asmRunsLoading && !asmRuns ? (
                <div className="flex justify-center py-10"><RefreshCw size={20} className="animate-spin text-gray-300" /></div>
              ) : asmRuns?.entries?.length ? (
                <ul className="space-y-2" data-testid="asm-runs-list">
                  {asmRuns.entries.map((row, idx) => {
                    const expanded = !!asmRunsExpanded[idx];
                    const ratioLabel = `${(row.ratio || 0).toFixed(4)} → ${(row.post_ratio || 0).toFixed(4)}`;
                    const actionColor = row.action === 'noop'
                      ? 'bg-gray-50 text-gray-500 border-gray-200'
                      : row.action?.includes('regenerated')
                        ? 'bg-blue-50 text-blue-600 border-blue-200'
                        : row.action?.includes('translated')
                          ? 'bg-violet-50 text-violet-600 border-violet-200'
                          : 'bg-amber-50 text-amber-600 border-amber-200';
                    return (
                      <li key={idx} className="rounded-xl border border-gray-200 bg-white" data-testid={`asm-run-row-${idx}`}>
                        <button
                          type="button"
                          onClick={() => setAsmRunsExpanded(prev => ({ ...prev, [idx]: !prev[idx] }))}
                          className="w-full flex items-center gap-2 p-3 text-left hover:bg-gray-50 rounded-xl"
                        >
                          <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold border ${actionColor}`}>
                            {row.action || '—'}
                          </span>
                          <span className="text-[11px] font-mono text-gray-500 truncate">
                            {row.behaviour || '—'} · {ratioLabel}
                          </span>
                          <span className="text-[11px] text-gray-400 ml-auto font-mono whitespace-nowrap">
                            {row.ts ? new Date(row.ts).toLocaleString() : '—'}
                          </span>
                        </button>
                        {expanded && (
                          <div className="px-3 pb-3 space-y-2" data-testid={`asm-run-detail-${idx}`}>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                              <div className="rounded-lg border border-gray-200 p-2 bg-gray-50">
                                <div className="flex items-center justify-between mb-1">
                                  <p className="text-[10px] uppercase tracking-wider text-gray-500 font-bold">Original</p>
                                  {Array.isArray(row.suspicious_tokens) && row.suspicious_tokens.length > 0 && (
                                    <span
                                      className="text-[9px] uppercase tracking-wider text-amber-700 font-semibold"
                                      data-testid={`asm-run-token-count-${idx}`}
                                    >
                                      {row.suspicious_tokens.length} flagged
                                    </span>
                                  )}
                                </div>
                                <p
                                  className="text-xs text-gray-800 font-mono whitespace-pre-wrap break-words"
                                  data-testid={`asm-run-original-${idx}`}
                                >
                                  {row.raw_snippet ? (
                                    buildHighlightedSegments(row.raw_snippet, row.suspicious_tokens).map((seg, i) =>
                                      seg.highlight ? (
                                        <mark
                                          key={i}
                                          className="bg-amber-200 text-amber-900 rounded px-0.5"
                                          data-testid={`asm-run-token-${idx}-${i}`}
                                        >
                                          {seg.text}
                                        </mark>
                                      ) : (
                                        <span key={i}>{seg.text}</span>
                                      ),
                                    )
                                  ) : (
                                    <span className="text-gray-400">(not persisted)</span>
                                  )}
                                </p>
                              </div>
                              <div className="rounded-lg border border-emerald-200 p-2 bg-emerald-50">
                                <p className="text-[10px] uppercase tracking-wider text-emerald-700 font-bold mb-1">Cleaned</p>
                                <p className="text-xs text-gray-800 font-mono whitespace-pre-wrap break-words">
                                  {row.cleaned_snippet || <span className="text-gray-400">(not persisted)</span>}
                                </p>
                              </div>
                            </div>
                            <div className="text-[10px] font-mono text-gray-500 flex flex-wrap gap-x-3 gap-y-1">
                              <span>threshold: {(row.threshold || 0).toFixed(3)}</span>
                              <span>translated: {String(!!row.translated)}</span>
                              <span>regenerated: {String(!!row.regenerated)}</span>
                              <span>has_assamese: {String(row.has_assamese !== false)}</span>
                            </div>
                            {(row.conversation_id || row.user_id) && (
                              <div
                                className="text-[10px] font-mono text-gray-600 flex flex-wrap gap-x-3 gap-y-1 pt-1 border-t border-gray-100"
                                data-testid={`asm-run-trace-${idx}`}
                              >
                                {row.conversation_id && (
                                  <span data-testid={`asm-run-conv-${idx}`}>
                                    conversation: <span className="text-gray-800">{row.conversation_id}</span>
                                  </span>
                                )}
                                {row.user_id && (
                                  <span data-testid={`asm-run-user-${idx}`}>
                                    user: <span className="text-gray-800">{row.user_id}</span>
                                  </span>
                                )}
                              </div>
                            )}
                          </div>
                        )}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-xs text-gray-400 py-6 text-center" data-testid="asm-runs-empty">
                  {asmRunsActionFilter
                    ? `No recent cleanups match action="${asmRunsActionFilter}".`
                    : 'No sanitiser runs recorded yet. Entries appear once Indic chat traffic flows through cleanup.'}
                </p>
              )}
            </div>
            </SectionErrorBoundary>

            <SectionErrorBoundary name="ASM Configuration">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <Zap size={16} className="text-violet-500" />
                    Assamese Purity Override
                  </h3>
                  <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                    Live behaviour and threshold for Sarvam Assamese leakage cleanup. Changes apply immediately and survive restarts (persisted in <code className="font-mono text-[11px] text-gray-600">db.api_config</code>).
                  </p>
                </div>
                <button
                  onClick={loadAsmCfg}
                  disabled={asmLoading}
                  className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100"
                  data-testid="button-refresh-asm"
                  title="Refresh"
                >
                  <RefreshCw size={14} className={asmLoading ? 'animate-spin' : ''} />
                </button>
              </div>

              {asmLoading && !asmCfg ? (
                <div className="flex justify-center py-10"><RefreshCw size={20} className="animate-spin text-gray-300" /></div>
              ) : asmCfg ? (
                <>
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-5">
                    <PeakBadge label="Active behaviour" value={asmCfg.config?.behaviour || '—'} color="violet" />
                    <PeakBadge label="Active threshold" value={asmCfg.config?.threshold != null ? Number(asmCfg.config.threshold).toFixed(3) : '—'} color="emerald" />
                    <PeakBadge label="Indic provider" value={asmCfg.config?.indic_provider || '—'} color={asmCfg.config?.indic_provider === 'vertex' ? 'amber' : 'blue'} />
                    <PeakBadge label="Behaviour source" value={asmCfg.config?.behaviour_source || '—'} color={asmCfg.config?.behaviour_source === 'override' ? 'amber' : 'blue'} />
                    <PeakBadge label="Threshold source" value={asmCfg.config?.threshold_source || '—'} color={asmCfg.config?.threshold_source === 'override' ? 'amber' : 'blue'} />
                    <PeakBadge label="Provider source" value={asmCfg.config?.indic_provider_source || '—'} color={asmCfg.config?.indic_provider_source === 'override' ? 'amber' : 'blue'} />
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Behaviour</label>
                      <select
                        value={asmDraft.behaviour}
                        onChange={(e) => setAsmDraft(d => ({ ...d, behaviour: e.target.value }))}
                        className="w-full text-sm font-mono px-3 py-2 rounded-lg border border-gray-200 focus:border-violet-300 focus:ring-1 focus:ring-violet-200 outline-none"
                        data-testid="select-asm-behaviour"
                      >
                        {(asmCfg.config?.valid_behaviours || []).map(b => (
                          <option key={b} value={b}>{b}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Threshold (0–1)</label>
                      <input
                        type="number"
                        min="0.001"
                        max="0.999"
                        step="0.005"
                        value={asmDraft.threshold}
                        onChange={(e) => setAsmDraft(d => ({ ...d, threshold: e.target.value }))}
                        className="w-full text-sm font-mono px-3 py-2 rounded-lg border border-gray-200 focus:border-violet-300 focus:ring-1 focus:ring-violet-200 outline-none"
                        data-testid="input-asm-threshold"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">
                        Indic provider
                        <span className="ml-2 text-[10px] font-normal text-emerald-700 normal-case tracking-normal">LOCKED · V4 §15</span>
                      </label>
                      <div
                        className="w-full text-sm font-mono px-3 py-2 rounded-lg border border-emerald-200 bg-emerald-50 text-emerald-900 select-none"
                        data-testid="select-asm-indic-provider"
                        title="V4 §15 / Task #492 locks the Indic chat provider to sarvam-m chat (assamese_rag_chat) → Workers-AI IndicTrans2 fallback. Vertex was removed by Task #490."
                      >
                        sarvam <span className="text-emerald-600 text-xs">(assamese_rag_chat → Workers-AI IndicTrans2)</span>
                      </div>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      onClick={saveAsmOverride}
                      disabled={asmSaving}
                      className="px-4 py-2 rounded-lg bg-violet-600 text-white text-xs font-semibold shadow-sm hover:bg-violet-700 disabled:opacity-50"
                      data-testid="button-save-asm"
                    >
                      {asmSaving ? 'Saving…' : 'Save override'}
                    </button>
                    <button
                      onClick={clearAsmOverride}
                      disabled={asmSaving || !asmCfg.persisted}
                      className="px-4 py-2 rounded-lg border border-gray-200 text-gray-500 text-xs font-semibold hover:bg-gray-50 disabled:opacity-40"
                      data-testid="button-clear-asm"
                      title={asmCfg.persisted ? 'Drop the override and revert to env vars' : 'No override to clear'}
                    >
                      Clear override
                    </button>
                    {asmCfg.persisted?.updated_at && (
                      <span className="text-[11px] text-gray-400 ml-auto font-mono">
                        Last edit by {asmCfg.persisted.updated_by || 'admin'} · {new Date(asmCfg.persisted.updated_at).toLocaleString()}
                      </span>
                    )}
                  </div>

                  <p className="text-[11px] text-gray-400 leading-relaxed mt-4">
                    Defaults: behaviour <code className="font-mono">{asmCfg.config?.default_behaviour}</code> · threshold <code className="font-mono">{asmCfg.config?.default_threshold}</code>. <span className="text-amber-600">Override</span> beats env vars; env vars beat defaults. Source columns above tell you what's currently winning.
                  </p>
                </>
              ) : null}
            </div>
            </SectionErrorBoundary>

            <SectionErrorBoundary name="ASM Trial Sentence">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-sm font-semibold text-gray-900 mb-1 flex items-center gap-2">
                <ShieldCheck size={16} className="text-emerald-500" />
                Test fire
              </h3>
              <p className="text-xs text-gray-500 mb-3 leading-relaxed">
                Sends the sample below through the LIVE sanitiser using the currently active behaviour. Use this to validate a new override before letting real users hit it.
              </p>
              {asmCfg?.config?.behaviour && (asmCfg.config.behaviour === 'regenerate' || asmCfg.config.behaviour === 'translate+regenerate') && (
                <div className="mb-3 px-3 py-2 rounded-lg bg-amber-50 border border-amber-200 text-[11px] text-amber-800 leading-relaxed" data-testid="asm-regenerate-warning">
                  <strong>Heads up:</strong> the active behaviour includes <code className="font-mono">regenerate</code>, but the test-fire route does not have a real chat context, so the regenerate step will be skipped here (you'll see <code className="font-mono">regenerated: false</code> in the diagnostic). Translate / strip behaviour IS exercised. Use a real chat query in Assamese to fully validate regenerate end-to-end.
                </div>
              )}

              <label className="block text-xs font-bold text-gray-500 uppercase tracking-wider mb-2">Sample (Assamese with English leakage)</label>
              <textarea
                value={asmTestSample}
                onChange={(e) => setAsmTestSample(e.target.value)}
                rows={3}
                className="w-full text-sm font-mono px-3 py-2 rounded-lg border border-gray-200 focus:border-violet-300 focus:ring-1 focus:ring-violet-200 outline-none mb-3"
                data-testid="input-asm-sample"
              />

              <button
                onClick={fireAsmTest}
                disabled={asmTesting || !asmTestSample.trim()}
                className="px-4 py-2 rounded-lg bg-emerald-600 text-white text-xs font-semibold shadow-sm hover:bg-emerald-700 disabled:opacity-50"
                data-testid="button-fire-asm"
              >
                {asmTesting ? 'Running…' : 'Fire test'}
              </button>

              {asmTestResult && (
                <div className="mt-4 space-y-3" data-testid="asm-test-result">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div className="rounded-xl border border-gray-200 p-3 bg-gray-50">
                      <p className="text-[10px] uppercase tracking-wider text-gray-500 font-bold mb-2">Raw input</p>
                      <p className="text-xs text-gray-800 font-mono whitespace-pre-wrap break-words">{asmTestResult.raw}</p>
                    </div>
                    <div className="rounded-xl border border-emerald-200 p-3 bg-emerald-50">
                      <p className="text-[10px] uppercase tracking-wider text-emerald-700 font-bold mb-2">Cleaned output</p>
                      <p className="text-xs text-gray-800 font-mono whitespace-pre-wrap break-words">{asmTestResult.cleaned}</p>
                    </div>
                  </div>
                  <div className="rounded-xl border border-gray-200 p-3 bg-white">
                    <p className="text-[10px] uppercase tracking-wider text-gray-500 font-bold mb-2">Diagnostic</p>
                    <pre className="text-[11px] font-mono text-gray-700 overflow-x-auto">{JSON.stringify(asmTestResult.diag, null, 2)}</pre>
                  </div>
                </div>
              )}
            </div>
            </SectionErrorBoundary>

            {/* Task #424 — append-only audit trail of override edits.
                Read-only here; writes happen via PATCH/DELETE handlers.
                Task #430 — filter by admin email + date range, paginate. */}
            <SectionErrorBoundary name="ASM Audit Log">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm" data-testid="asm-audit-card">
              <div className="flex items-start justify-between gap-3 mb-4">
                <div>
                  <h3 className="text-sm font-semibold text-gray-900 flex items-center gap-2">
                    <Clock size={16} className="text-gray-500" />
                    Recent override changes
                  </h3>
                  <p className="text-xs text-gray-500 mt-1 leading-relaxed">
                    Append-only log of who edited the Sarvam purity override and what changed.
                    {' '}{(() => {
                      const total = asmAudit?.total ?? 0;
                      const off = asmAudit?.offset ?? 0;
                      const shown = asmAudit?.entries?.length ?? 0;
                      if (!shown) return `0 entries match the current filters.`;
                      return `Showing ${off + 1}–${off + shown} of ${total} (newest first).`;
                    })()}
                  </p>
                </div>
                <button
                  onClick={() => loadAsmAudit({
                    offset: asmAudit?.offset ?? asmAuditOffset,
                    filters: asmAuditFilters,
                  })}
                  disabled={asmAuditLoading}
                  className="p-2 rounded-xl text-gray-400 hover:text-gray-600 hover:bg-gray-100"
                  data-testid="button-refresh-asm-audit"
                  title="Refresh audit log"
                >
                  <RefreshCw size={14} className={asmAuditLoading ? 'animate-spin' : ''} />
                </button>
              </div>

              {/* Task #430 — filter row. Apply on submit so each keystroke
                  doesn't fire a request; Reset clears every filter and the
                  paging cursor in one click. */}
              <form
                className="grid grid-cols-1 sm:grid-cols-4 gap-2 mb-4"
                onSubmit={(e) => {
                  e.preventDefault();
                  setAsmAuditOffset(0);
                  loadAsmAudit({ offset: 0, filters: asmAuditFilters });
                }}
                data-testid="asm-audit-filters"
              >
                <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-gray-500">
                  Admin email
                  <input
                    type="text"
                    value={asmAuditFilters.admin_email}
                    onChange={(e) => setAsmAuditFilters((f) => ({ ...f, admin_email: e.target.value }))}
                    placeholder="ops@syrabit.ai"
                    className="px-3 py-1.5 rounded-lg border border-gray-200 text-xs font-mono text-gray-700 focus:outline-none focus:border-violet-300"
                    data-testid="input-asm-audit-email"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-gray-500">
                  From
                  <input
                    type="datetime-local"
                    value={asmAuditFilters.since}
                    onChange={(e) => setAsmAuditFilters((f) => ({ ...f, since: e.target.value }))}
                    className="px-3 py-1.5 rounded-lg border border-gray-200 text-xs font-mono text-gray-700 focus:outline-none focus:border-violet-300"
                    data-testid="input-asm-audit-since"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wider text-gray-500">
                  To
                  <input
                    type="datetime-local"
                    value={asmAuditFilters.until}
                    onChange={(e) => setAsmAuditFilters((f) => ({ ...f, until: e.target.value }))}
                    className="px-3 py-1.5 rounded-lg border border-gray-200 text-xs font-mono text-gray-700 focus:outline-none focus:border-violet-300"
                    data-testid="input-asm-audit-until"
                  />
                </label>
                <div className="flex items-end gap-2">
                  <button
                    type="submit"
                    disabled={asmAuditLoading}
                    className="flex-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-violet-600 text-white hover:bg-violet-700 disabled:opacity-50"
                    data-testid="button-apply-asm-audit"
                  >
                    Apply
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const cleared = { admin_email: '', since: '', until: '' };
                      setAsmAuditFilters(cleared);
                      setAsmAuditOffset(0);
                      loadAsmAudit({ offset: 0, filters: cleared });
                    }}
                    disabled={asmAuditLoading}
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-gray-200 text-gray-500 hover:text-gray-700 hover:bg-gray-50"
                    data-testid="button-reset-asm-audit"
                  >
                    Reset
                  </button>
                </div>
              </form>

              {asmAudit && asmAudit.ok === false && (
                <div className="mb-3 p-3 rounded-xl bg-red-50 border border-red-200 flex items-start gap-2" data-testid="asm-audit-error">
                  <AlertTriangle size={14} className="text-red-500 mt-0.5 flex-shrink-0" />
                  <div className="text-[11px] text-red-700 leading-relaxed">
                    <span className="font-semibold">Audit log unavailable.</span>{' '}
                    {asmAudit.error || 'Mongo read failed — see api logs.'}
                  </div>
                </div>
              )}

              {asmAuditLoading && !asmAudit ? (
                <div className="flex justify-center py-10"><RefreshCw size={20} className="animate-spin text-gray-300" /></div>
              ) : asmAudit?.entries?.length ? (
                <div className="overflow-x-auto" data-testid="asm-audit-table">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-[10px] uppercase tracking-wider text-gray-500 border-b border-gray-100">
                        <th className="py-2 pr-3 font-bold">When</th>
                        <th className="py-2 pr-3 font-bold">Action</th>
                        <th className="py-2 pr-3 font-bold">Admin</th>
                        <th className="py-2 pr-3 font-bold">Before</th>
                        <th className="py-2 pr-3 font-bold">After</th>
                        <th className="py-2 font-bold text-right">Revert</th>
                      </tr>
                    </thead>
                    <tbody>
                      {asmAudit.entries.map((row, idx) => {
                        const fmtSide = (side) => {
                          if (!side) return <span className="text-gray-400">—</span>;
                          const beh = side.behaviour;
                          const thr = side.threshold;
                          return (
                            <span className="font-mono text-[11px] text-gray-700">
                              {beh != null ? beh : '·'} / {thr != null ? Number(thr).toFixed(3) : '·'}
                            </span>
                          );
                        };
                        return (
                          <tr key={idx} className="border-b border-gray-50 hover:bg-gray-50" data-testid={`asm-audit-row-${idx}`}>
                            <td className="py-2 pr-3 text-gray-500 font-mono text-[11px] whitespace-nowrap">
                              {row.ts ? new Date(row.ts).toLocaleString() : '—'}
                            </td>
                            <td className="py-2 pr-3">
                              <span className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                                row.action === 'delete'
                                  ? 'bg-red-50 text-red-600 border border-red-200'
                                  : row.action === 'revert'
                                  ? 'bg-amber-50 text-amber-700 border border-amber-200'
                                  : 'bg-violet-50 text-violet-600 border border-violet-200'
                              }`}>
                                {row.action || '—'}
                              </span>
                            </td>
                            <td className="py-2 pr-3 text-gray-700 truncate max-w-[180px]" title={row.admin_email || row.admin_id || ''}>
                              {row.admin_email || row.admin_id || <span className="text-gray-400">unknown</span>}
                            </td>
                            <td className="py-2 pr-3">{fmtSide(row.before)}</td>
                            <td className="py-2 pr-3">{fmtSide(row.after)}</td>
                            <td className="py-2 text-right">
                              {row.action === 'revert' ? (
                                <span
                                  className="text-[10px] text-gray-400"
                                  title={row.source_audit_id ? `Reverted from ${row.source_audit_id}` : ''}
                                >
                                  ↩ revert
                                </span>
                              ) : (
                                <button
                                  type="button"
                                  onClick={() => revertAsmAuditRow(row)}
                                  disabled={!row.id || asmRevertingId === row.id}
                                  title={row.id ? 'Re-apply this row\'s before-state' : 'No id — predates revert support'}
                                  className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-amber-200 bg-amber-50 text-amber-700 text-[11px] font-semibold hover:bg-amber-100 disabled:opacity-40 disabled:cursor-not-allowed"
                                  data-testid={`button-revert-asm-audit-${idx}`}
                                >
                                  <Undo2 size={11} className={asmRevertingId === row.id ? 'animate-spin' : ''} />
                                  {asmRevertingId === row.id ? 'Reverting…' : 'Revert'}
                                </button>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                  <p className="text-[10px] text-gray-400 mt-3 leading-relaxed">
                    Format is <code className="font-mono">behaviour / threshold</code>. Dash means the field was unset. Audit rows are append-only and persist across mongo restarts.
                  </p>
                </div>
              ) : (
                <p className="text-xs text-gray-400 py-6 text-center" data-testid="asm-audit-empty">
                  {(asmAudit?.total ?? 0) > 0
                    ? 'No entries on this page — try Prev or relax the filters.'
                    : 'No override edits match. The first PATCH or DELETE on this tab will appear here.'}
                </p>
              )}

              {/* Task #430 — pagination. Prev/Next operate on the current
                  offset; the backend reports total so we can disable Next
                  when we've shown the last page. */}
              {(asmAudit?.total ?? 0) > ASM_AUDIT_PAGE && (
                <div className="flex items-center justify-between gap-3 mt-4 pt-3 border-t border-gray-100" data-testid="asm-audit-pager">
                  <p className="text-[11px] text-gray-400 font-mono">
                    Page {Math.floor((asmAudit?.offset ?? 0) / ASM_AUDIT_PAGE) + 1}
                    {' / '}
                    {Math.max(1, Math.ceil((asmAudit?.total ?? 0) / ASM_AUDIT_PAGE))}
                  </p>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        const next = Math.max(0, (asmAudit?.offset ?? 0) - ASM_AUDIT_PAGE);
                        setAsmAuditOffset(next);
                        loadAsmAudit({ offset: next, filters: asmAuditFilters });
                      }}
                      disabled={asmAuditLoading || (asmAudit?.offset ?? 0) <= 0}
                      className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                      data-testid="button-asm-audit-prev"
                    >
                      ← Prev
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        const next = (asmAudit?.offset ?? 0) + ASM_AUDIT_PAGE;
                        setAsmAuditOffset(next);
                        loadAsmAudit({ offset: next, filters: asmAuditFilters });
                      }}
                      disabled={
                        asmAuditLoading ||
                        ((asmAudit?.offset ?? 0) + (asmAudit?.entries?.length ?? 0)) >= (asmAudit?.total ?? 0)
                      }
                      className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-gray-200 text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                      data-testid="button-asm-audit-next"
                    >
                      Next →
                    </button>
                  </div>
                </div>
              )}
            </div>
            </SectionErrorBoundary>

            {/* Task #441 — side-by-side revert preview. Renders the
                currently persisted override against the source row's
                `before` snapshot so an admin can confirm provenance
                before re-applying an old value. */}
            {asmRevertPreview && (
              <SectionErrorBoundary name="ASM Revert Preview">
                {(() => {
              const row = asmRevertPreview;
              const current = asmCfg?.persisted || null;
              const target = row.before || null;
              const reverting = asmRevertingId === row.id;
              const fmtVal = (v, digits = 3) =>
                v == null || v === ''
                  ? <span className="text-gray-400">·</span>
                  : <span className="font-mono text-gray-800">{typeof v === 'number' ? v.toFixed(digits) : v}</span>;
              const Side = ({ heading, accent, snapshot, footer }) => (
                <div className={`flex-1 rounded-xl border ${accent} p-4 min-w-0`}>
                  <p className="text-[10px] uppercase tracking-wider font-bold text-gray-500 mb-3">{heading}</p>
                  <dl className="space-y-2 text-xs">
                    <div className="flex justify-between gap-3">
                      <dt className="text-gray-500">Behaviour</dt>
                      <dd>{fmtVal(snapshot?.behaviour, 0)}</dd>
                    </div>
                    <div className="flex justify-between gap-3">
                      <dt className="text-gray-500">Threshold</dt>
                      <dd>{fmtVal(snapshot?.threshold)}</dd>
                    </div>
                  </dl>
                  {footer && <div className="mt-3 pt-3 border-t border-gray-100 text-[10px] text-gray-500 leading-relaxed">{footer}</div>}
                </div>
              );
              return (
                <div
                  className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
                  role="dialog"
                  aria-modal="true"
                  aria-labelledby="asm-revert-modal-title"
                  onClick={() => { if (!reverting) setAsmRevertPreview(null); }}
                  data-testid="asm-revert-modal"
                >
                  <div
                    className="bg-white rounded-2xl shadow-2xl border border-gray-200 max-w-2xl w-full p-6"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div className="flex items-start gap-3 mb-4">
                      <div className="w-9 h-9 rounded-full bg-amber-50 border border-amber-200 flex items-center justify-center flex-shrink-0">
                        <Undo2 size={16} className="text-amber-600" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <h3 id="asm-revert-modal-title" className="text-base font-semibold text-gray-900">Revert Sarvam purity?</h3>
                        <p className="text-xs text-gray-500 mt-0.5 leading-relaxed">
                          Compare the live override with the snapshot you're about to re-apply. This action is logged as a new <code className="font-mono">revert</code> row.
                        </p>
                      </div>
                    </div>

                    <div className="flex flex-col sm:flex-row gap-3 mb-4">
                      <Side
                        heading="Current (live)"
                        accent="bg-gray-50 border-gray-200"
                        snapshot={current}
                        footer={
                          current?.updated_at
                            ? <>Last edit by <span className="font-mono text-gray-700">{current.updated_by || 'admin'}</span> · {new Date(current.updated_at).toLocaleString()}</>
                            : <span className="text-gray-400">No persisted override (env vars in effect).</span>
                        }
                      />
                      <div className="hidden sm:flex items-center text-gray-300 text-xl px-1" aria-hidden="true">→</div>
                      <Side
                        heading="Target (revert to)"
                        accent="bg-amber-50 border-amber-200"
                        snapshot={target}
                        footer={
                          <>
                            Source row by <span className="font-mono text-gray-700">{row.admin_email || row.admin_id || 'unknown'}</span>
                            {row.ts && <> · {new Date(row.ts).toLocaleString()}</>}
                            {!target && <div className="mt-1 text-amber-700">Snapshot is empty — this will clear the override.</div>}
                          </>
                        }
                      />
                    </div>

                    <div className="flex items-center justify-end gap-2 pt-3 border-t border-gray-100">
                      <button
                        type="button"
                        onClick={() => setAsmRevertPreview(null)}
                        disabled={reverting}
                        className="px-4 py-2 rounded-lg border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50 disabled:opacity-40"
                        data-testid="button-asm-revert-cancel"
                      >
                        Cancel
                      </button>
                      <button
                        type="button"
                        onClick={confirmAsmRevert}
                        disabled={reverting}
                        className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-amber-500 text-white text-xs font-semibold hover:bg-amber-600 disabled:opacity-40"
                        data-testid="button-asm-revert-confirm"
                      >
                        <Undo2 size={12} className={reverting ? 'animate-spin' : ''} />
                        {reverting ? 'Reverting…' : 'Confirm revert'}
                      </button>
                    </div>
                  </div>
                </div>
              );
                })()}
              </SectionErrorBoundary>
            )}
          </div>
          </SectionErrorBoundary>
  );
}
