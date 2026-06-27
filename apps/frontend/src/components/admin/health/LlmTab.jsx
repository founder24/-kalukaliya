import { RefreshCw, DollarSign } from 'lucide-react';
import { SectionErrorBoundary } from '@/components/ErrorBoundary';
import { PeakBadge, CustomTooltip } from './shared';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { llmCosts } from '@/utils/api';

export default function LlmTab({ adminToken, llmData, llmLoading, llmDays, setLlmDays, setLlmLoading, setLlmData, loadLlmCosts }) {
  return (
          <SectionErrorBoundary name="LLM Cost Tracker" resetKeys={['llm']}>
          <div className="space-y-4">
            <div className="flex items-center gap-2 mb-2">
              {[7, 14, 30].map(d => (
                <button key={d} onClick={() => { setLlmDays(d); setLlmLoading(true); llmCosts(adminToken, d).then(r => setLlmData(r.data)).catch(() => {}).finally(() => setLlmLoading(false)); }}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all border ${
                    llmDays === d ? 'bg-violet-50 border-violet-200 text-violet-600' : 'border-gray-200 text-gray-400 hover:text-gray-600'
                  }`}>
                  {d}d
                </button>
              ))}
              <button onClick={loadLlmCosts} disabled={llmLoading}
                className="ml-2 px-3 py-1.5 rounded-lg text-xs border border-gray-200 text-gray-400 hover:text-gray-600">
                {llmLoading ? 'Loading…' : '↻ Refresh'}
              </button>
            </div>
            {llmLoading ? (
              <div className="flex justify-center p-10"><RefreshCw size={20} className="animate-spin text-gray-300" /></div>
            ) : llmData ? (
              <>
                <SectionErrorBoundary name="LLM Cost Stats">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  {[
                    { label: `Total Cost (${llmDays}d)`, value: `$${llmData?.total_cost_usd || '0.000000'}`, color: 'amber' },
                    { label: 'Total Cost (INR)', value: `₹${llmData?.total_cost_inr || '0.0000'}`, color: 'emerald' },
                    { label: 'Total Tokens', value: Number(llmData?.total_tokens || 0).toLocaleString(), color: 'violet' },
                    { label: 'Cost/Page', value: `$${llmData?.cost_per_published_page_usd || '0.000000'}`, color: 'blue' },
                  ].map(s => <PeakBadge key={s.label} label={s.label} value={s.value} color={s.color} />)}
                </div>
                </SectionErrorBoundary>

                {(llmData?.by_model?.length > 0) && (
                  <SectionErrorBoundary name="Cost by Model">
                  <div className="rounded-xl p-5 bg-white border border-gray-200 shadow-sm">
                    <h3 className="text-sm font-semibold text-gray-900 mb-4">Cost by Model</h3>
                    <div className="space-y-3">
                      {llmData.by_model.map(m => {
                        const pct = llmData.total_cost_usd > 0 ? Math.round(m.cost_usd / llmData.total_cost_usd * 100) : 0;
                        return (
                          <div key={m.model}>
                            <div className="flex justify-between mb-1">
                              <span className="text-xs text-gray-600 font-mono">{m.model}</span>
                              <span className="text-xs text-violet-600">${m.cost_usd} ({m.calls} calls)</span>
                            </div>
                            <div className="h-1 rounded-full overflow-hidden bg-gray-100">
                              <div style={{ width: `${pct}%`, height: '100%', background: 'linear-gradient(90deg,#7c3aed,#a78bfa)', borderRadius: 2 }} />
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                  </SectionErrorBoundary>
                )}

                {(llmData?.daily?.length > 0) && (
                  <SectionErrorBoundary name="Daily LLM Spend">
                  <div className="rounded-xl p-5 bg-white border border-gray-200 shadow-sm">
                    <h3 className="text-sm font-semibold text-gray-900 mb-4">Daily LLM Spend</h3>
                    <ResponsiveContainer width="100%" height={160}>
                      <BarChart data={llmData.daily}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f3f4f6" />
                        <XAxis dataKey="date" tick={{ fill: '#9ca3af', fontSize: 10 }} tickFormatter={d => d?.slice(5)} axisLine={false} tickLine={false} />
                        <YAxis tick={{ fill: '#9ca3af', fontSize: 10 }} axisLine={false} tickLine={false} />
                        <Tooltip content={<CustomTooltip />} />
                        <Bar dataKey="cost_usd" name="Cost (USD)" fill="#7c3aed" radius={[3, 3, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  </SectionErrorBoundary>
                )}

                {llmData?.total_calls === 0 && (
                  <div className="text-center py-12 text-gray-400">
                    <DollarSign size={32} className="mx-auto mb-3 opacity-30" />
                    <p className="text-sm">No LLM calls recorded yet — costs will appear here as content is generated</p>
                  </div>
                )}
              </>
            ) : null}
          </div>
          </SectionErrorBoundary>
  );
}
