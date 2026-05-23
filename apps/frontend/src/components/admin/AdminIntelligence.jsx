import { useState, useEffect, useCallback } from 'react';
import {
  Activity, Cpu, Database, Layers, RefreshCw, Loader2,
  AlertTriangle, CheckCircle, Zap, BarChart2, Server, Shield,
  TrendingUp, ArrowRight,
} from 'lucide-react';
import { adminIntelligenceOverview, adminContentAutoHeal } from '@/utils/api';
import { toast } from 'sonner';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';

function MetricCard({ label, value, sub, color = '#8b5cf6', alert }) {
  const alertBorders = {
    green: '#a7f3d0',
    red: '#fecaca',
    amber: '#fde68a',
  };
  return (
    <div className="rounded-xl p-4 bg-white shadow-sm" style={{
      border: `1px solid ${alertBorders[alert] || '#e5e7eb'}`,
    }}>
      <p className="text-xs text-gray-500 mb-1">{label}</p>
      <p className="text-2xl font-bold font-mono" style={{ color }}>{value}</p>
      {sub && <p className="text-[11px] text-gray-400 mt-1">{sub}</p>}
    </div>
  );
}

function ProviderRow({ name, stats }) {
  const statusColor = stats.success_rate >= 95 ? '#10b981' : stats.success_rate >= 80 ? '#f59e0b' : '#ef4444';
  return (
    <div className="flex items-center justify-between py-2.5 px-3 rounded-lg bg-gray-50">
      <div className="flex items-center gap-3">
        <div className="w-2 h-2 rounded-full" style={{ background: statusColor }} />
        <span className="text-sm text-gray-700 font-medium capitalize">{name}</span>
        <span className="text-[10px] text-gray-400">{stats.models?.join(', ')}</span>
      </div>
      <div className="flex items-center gap-4 text-xs">
        <span className="text-gray-400">{stats.calls} calls</span>
        <span style={{ color: statusColor }}>{stats.success_rate}%</span>
        <span className="text-gray-500 font-mono">{stats.avg_latency_ms}ms</span>
      </div>
    </div>
  );
}

export default function AdminIntelligence({ adminToken, onNavigate }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [healing, setHealing] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const res = await adminIntelligenceOverview(adminToken);
      setData(res.data);
    } catch (e) {
      toast.error('Failed to load intelligence data');
    } finally {
      setLoading(false);
    }
  }, [adminToken]);

  useEffect(() => { loadData(); }, [loadData]);

  const handleAutoHeal = async () => {
    setHealing(true);
    try {
      const res = await adminContentAutoHeal(adminToken);
      const d = res.data;
      toast.success(`Auto-heal: ${d.healed} chapters regenerated, ${d.still_thin} still thin, ${d.errors} errors`);
      loadData();
    } catch (e) {
      toast.error('Auto-heal failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setHealing(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <Loader2 className="w-8 h-8 animate-spin text-violet-500" />
      </div>
    );
  }

  if (!data) return null;

  const { llm_health, vector_search, pipeline, content, content_health } = data;

  return (
    <SectionErrorBoundary name="Intelligence">
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-gray-900 flex items-center gap-2">
              <Activity size={20} className="text-violet-500" /> Intelligence Panel
            </h2>
            <p className="text-sm text-gray-500 mt-1">Real-time system health, content quality, and pipeline metrics</p>
          </div>
          <button onClick={loadData} className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs text-gray-500 hover:text-gray-700 transition-colors border border-gray-200 bg-white shadow-sm">
            <RefreshCw size={14} /> Refresh
          </button>
        </div>

        <SectionErrorBoundary name="LLM Provider Health">
          <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
            <h3 className="text-sm font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <Server size={16} className="text-blue-500" /> LLM Provider Health
              <span className="text-[10px] text-gray-400 ml-auto">Last 1h</span>
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
              <MetricCard label="Total Calls" value={llm_health.total_calls} color="#60a5fa" />
              <MetricCard
                label="Success Rate"
                value={`${llm_health.overall_success_rate}%`}
                color={llm_health.overall_success_rate >= 95 ? '#10b981' : '#ef4444'}
                alert={llm_health.overall_success_rate >= 95 ? 'green' : 'red'}
              />
              <MetricCard label="Fallback Rate" value={`${llm_health.fallback_rate}%`} color={llm_health.fallback_rate <= 5 ? '#10b981' : '#f59e0b'} />
              <MetricCard label="Providers" value={Object.keys(llm_health.providers || {}).length} color="#a78bfa" />
            </div>
            <div className="space-y-1">
              {Object.entries(llm_health.providers || {}).map(([name, stats]) => (
                <ProviderRow key={name} name={name} stats={stats} />
              ))}
              {Object.keys(llm_health.providers || {}).length === 0 && (
                <p className="text-xs text-gray-400 text-center py-3">No LLM calls recorded yet</p>
              )}
            </div>
          </div>
        </SectionErrorBoundary>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          <SectionErrorBoundary name="Vector Search">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-sm font-semibold text-gray-900 mb-4 flex items-center gap-2">
                <Database size={16} className="text-emerald-500" /> RAG Vector Search
              </h3>
              {vector_search.has_data ? (
                <div className="grid grid-cols-2 gap-3">
                  <MetricCard label="Searches" value={vector_search.total_searches} color="#60a5fa" />
                  <MetricCard label="Avg Best Score" value={vector_search.avg_best_score?.toFixed(3)} color="#10b981" />
                  <MetricCard label="% Below Threshold" value={`${vector_search.pct_below_threshold}%`} color={vector_search.pct_below_threshold < 20 ? '#10b981' : '#ef4444'} />
                  <MetricCard label="Zero-Result Rate" value={`${vector_search.zero_result_pct}%`} color={vector_search.zero_result_pct < 10 ? '#10b981' : '#ef4444'} />
                </div>
              ) : (
                <p className="text-xs text-gray-400 text-center py-6">No vector searches recorded yet</p>
              )}
            </div>
          </SectionErrorBoundary>

          <SectionErrorBoundary name="Pipeline Runs">
            <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
              <h3 className="text-sm font-semibold text-gray-900 mb-4 flex items-center gap-2">
                <Zap size={16} className="text-amber-500" /> Pipeline Runs (24h)
              </h3>
              {pipeline.has_data ? (
                <div className="grid grid-cols-2 gap-3">
                  <MetricCard label="Total Runs" value={pipeline.total_runs} color="#f59e0b" />
                  <MetricCard label="Success Rate" value={`${pipeline.success_rate}%`} color={pipeline.success_rate >= 90 ? '#10b981' : '#ef4444'} />
                  <MetricCard label="Chapters Processed" value={pipeline.total_chapters} color="#a78bfa" />
                  <MetricCard label="Chunks Created" value={pipeline.total_chunks} color="#60a5fa" />
                </div>
              ) : (
                <p className="text-xs text-gray-400 text-center py-6">No pipeline runs recorded yet</p>
              )}
            </div>
          </SectionErrorBoundary>
        </div>

        <SectionErrorBoundary name="Content Health">
          <div className="rounded-2xl p-5 bg-white border border-gray-200 shadow-sm">
            <h3 className="text-sm font-semibold text-gray-900 mb-4 flex items-center gap-2">
              <Layers size={16} className="text-violet-500" /> Content Health
            </h3>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-4">
              <MetricCard label="Total Chapters" value={content.total_chapters} color="#a78bfa" />
              <MetricCard label="With Content" value={content.with_content} color="#60a5fa" />
              <MetricCard label="Embedded" value={content.embedded} color="#10b981" />
              <MetricCard label="Total Chunks" value={content.total_chunks} color="#f59e0b" />
              <MetricCard label="Chunks/Chapter" value={content.chunks_per_chapter} color="#e879f9" />
            </div>

            <div className="flex items-center justify-between mb-3">
              <div className="flex items-center gap-2">
                {content_health.thin_count > 0 ? (
                  <AlertTriangle size={14} className="text-amber-500" />
                ) : (
                  <CheckCircle size={14} className="text-emerald-500" />
                )}
                <span className="text-xs text-gray-500">
                  {content_health.thin_count} thin chapters (&lt;600 words), {content_health.no_embedding_count} missing embeddings
                </span>
              </div>
              {content_health.thin_count > 0 && (
                <button
                  onClick={handleAutoHeal}
                  disabled={healing}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-xl text-xs font-medium text-white transition-all hover:opacity-90 disabled:opacity-50 bg-violet-600 shadow-sm"
                >
                  {healing ? <Loader2 size={12} className="animate-spin" /> : <Zap size={12} />}
                  {healing ? 'Healing...' : 'Auto-Heal Thin Content'}
                </button>
              )}
            </div>

            {content_health.thin_chapters?.length > 0 && (
              <div className="rounded-xl overflow-hidden border border-gray-200">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="text-gray-500 border-b border-gray-200 bg-gray-50">
                      <th className="text-left px-3 py-2">Chapter</th>
                      <th className="text-right px-3 py-2">Words</th>
                      <th className="text-right px-3 py-2">Chunks</th>
                      <th className="text-right px-3 py-2">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {content_health.thin_chapters.slice(0, 15).map((ch) => (
                      <tr key={ch.id} className="hover:bg-gray-50 transition-colors border-b border-gray-100">
                        <td className="px-3 py-2 text-gray-600">{ch.title}</td>
                        <td className="px-3 py-2 text-right font-mono text-amber-600">{ch.word_count}</td>
                        <td className="px-3 py-2 text-right font-mono text-gray-400">{ch.chunk_count}</td>
                        <td className="px-3 py-2 text-right">
                          {ch.needs_review ? (
                            <span className="text-amber-600">Needs Review</span>
                          ) : (
                            <span className="text-gray-400">Thin</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </SectionErrorBoundary>
      </div>
    </SectionErrorBoundary>
  );
}
